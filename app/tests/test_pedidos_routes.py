"""Contratos HTTP e regressões transacionais do bounded context Pedidos.

Os antigos writes globais ``/adequar`` e ``/sem_adequar`` foram removidos em
2026-08-26; o fluxo durável é coberto separadamente em
``test_pedidos_processing_routes.py``.
Testes diretos de repositório usam sessões sem commit ou fixtures isoladas.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.domain.roles import automationRole
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import create_access_token
from app.modules.pedidos import service as pedidos_service
from app.modules.pedidos.infrastructure.repositorio_ordens import (
    ResultadoAprovacao,
)
from app.modules.pedidos.infrastructure.repositorio_ordens_linx import (
    salvar_linhas_linx,
)
from app.shared.config.settings import get_settings
from app.shared.database.session import async_session_factory

# ---------------------------------------------------------------------------
# Helpers de auth (mesmo padrão de test_auth_flows.py / test_parametros.py)
# ---------------------------------------------------------------------------


def _nova_sessao_isolada():
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return test_engine, async_sessionmaker(test_engine, expire_on_commit=False)


async def _criar_usuario_async(*, email, roles):
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            user = AuthUser(
                email=email,
                roles=list(roles),
                confirmed_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id
    finally:
        await test_engine.dispose()


def criar_usuario(prefixo, roles):
    email = f"{prefixo}-{uuid.uuid4().hex[:8]}@teste.project.com"
    user_id = asyncio.run(_criar_usuario_async(email=email, roles=roles))
    return user_id, email


def token_de(user_id: int, email: str, roles: list) -> str:
    # Emissão direta do token, substituindo o POST /api/auth/sign-in que
    # deixou de existir (login por senha removido).
    return create_access_token(user_id=user_id, email=email, roles=list(roles))[0]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_token(client):
    uid, email = criar_usuario("core-viewer", [automationRole.BASICO])
    return token_de(uid, email, [automationRole.BASICO])


@pytest.fixture
def actor_token(client):
    uid, email = criar_usuario("core-actor", [automationRole.OPERACIONAL])
    return token_de(uid, email, [automationRole.OPERACIONAL])


# ---------------------------------------------------------------------------
# Endpoints de leitura (seguros: só leem os dados reais, sem side effects)
# ---------------------------------------------------------------------------


def test_resumo_pedidos_retorna_agregados_com_shape_esperado(client, viewer_token):
    resp = client.get("/api/v1/pedidos/resumo", headers=auth_header(viewer_token))

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["stockByCode"].keys()) == {"Todos", "Franquia", "Multimarca"}
    assert set(body["erpBilling"].keys()) == {"Franquia", "Multimarca", "Todos"}
    assert set(body["erpCount"].keys()) == {"Franquia", "Multimarca", "Todos"}
    assert set(body["statsByChannel"].keys()) == {"Franquia", "Multimarca", "Todos"}
    assert body["stockByCode"] == {"Todos": {}, "Franquia": {}, "Multimarca": {}}


async def test_evolucao_faturamento_mockado(client, viewer_token):
    # A série é PRÉ-CALCULADA na ingestão e lida do Postgres (tabela
    # faturamento_colecao) — a rota não consulta mais o Databricks. O patch mira
    # `service.obter_faturamento_colecao`, que é o nome no namespace do service.
    serie_fake = [
        {
            "colecao": 117,
            "canal": "Franquia",
            "planejado": 1000.0,
            "distribuido": 900.0,
        },
    ]
    with patch(
        "app.modules.pedidos.service.obter_faturamento_colecao",
        new=AsyncMock(return_value=serie_fake),
    ) as mock_ler:
        resp = client.get(
            "/api/v1/pedidos/evolucao-faturamento", headers=auth_header(viewer_token)
        )

    assert resp.status_code == 200
    assert resp.json() == serie_fake
    mock_ler.assert_awaited_once()


# ---------------------------------------------------------------------------
# Endpoints de escrita: funções de I/O mockadas — nada toca o banco de verdade.
# ---------------------------------------------------------------------------


def test_batch_grade_http_camelcase_limites_e_channel_exato(client, actor_token):
    body = {
        "changes": [
            {
                "orderId": 42,
                "expectedVersion": "a" * 64,
                "sizes": {"36": 2, "37": 3},
            }
        ]
    }
    with patch(
        "app.modules.pedidos.service.executar_alteracao_grades_produto",
        new=AsyncMock(
            return_value={
                "status": "success",
                "updated_count": 1,
                "total_qty": 5,
                "total_value": 100,
            }
        ),
    ) as execute:
        response = client.put(
            "/api/v1/pedidos/produtos/grades?productCode=Z&channel=Franquia",
            json=body,
            headers=auth_header(actor_token),
        )
        invalid_channel = client.put(
            "/api/v1/pedidos/produtos/grades?productCode=Z&channel=Todos",
            json=body,
            headers=auth_header(actor_token),
        )
        too_many_sizes = client.put(
            "/api/v1/pedidos/produtos/grades?productCode=Z&channel=Franquia",
            json={
                "changes": [
                    {
                        "orderId": 42,
                        "expectedVersion": "a" * 64,
                        "sizes": {f"T{index}": 1 for index in range(100)},
                    },
                    {
                        "orderId": 43,
                        "expectedVersion": "b" * 64,
                        "sizes": {"EXTRA": 1},
                    },
                ]
            },
            headers=auth_header(actor_token),
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "updatedCount": 1,
        "totalQty": 5,
        "totalValue": 100.0,
        "approvedCount": 0,
    }
    assert invalid_channel.status_code == 422
    assert too_many_sizes.status_code == 422
    execute.assert_awaited_once()


def test_batch_grade_orcamento_excedido_retorna_409_camelcase(client, actor_token):
    """WR-01 (20-REVIEW): o except-branch que monta o 409 estruturado para
    `OrcamentoPedidoExcedidoError` (D-12) nunca era exercitado por nenhum
    teste — o único teste HTTP do endpoint sempre mockava um retorno de
    sucesso. Um typo de alias, um atributo renomeado na exceção, ou um
    status code errado passariam despercebidos."""
    body = {
        "changes": [
            {
                "orderId": 42,
                "expectedVersion": "a" * 64,
                "sizes": {"36": 2, "37": 3},
            }
        ]
    }
    erro = pedidos_service.OrcamentoPedidoExcedidoError(
        nr_pedido=42,
        orcamento="adicao",
        restante_adicao=3,
        restante_corte=5,
        adicao_solicitada=8,
        corte_solicitado=0,
    )
    with patch(
        "app.modules.pedidos.service.executar_alteracao_grades_produto",
        new=AsyncMock(side_effect=erro),
    ) as execute:
        response = client.put(
            "/api/v1/pedidos/produtos/grades?productCode=Z&channel=Franquia",
            json=body,
            headers=auth_header(actor_token),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "orcamento_pedido_excedido",
        "message": str(erro),
        "nrPedido": 42,
        "orcamento": "adicao",
        "restanteAdicao": 3,
        "restanteCorte": 5,
    }
    execute.assert_awaited_once()


def test_batch_aprovacao_http_e_bounded(client, actor_token):
    with patch(
        "app.modules.pedidos.service.executar_aprovacao_produto",
        new=AsyncMock(
            return_value={
                "status": "success",
                "matched_count": 5,
                "approved_count": 3,
                "already_approved_count": 1,
                "expired_count": 1,
            }
        ),
    ):
        response = client.post(
            "/api/v1/pedidos/produtos/aprovar?productCode=Z&channel=Multimarca",
            headers=auth_header(actor_token),
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "matchedCount": 5,
        "approvedCount": 3,
        "alreadyApprovedCount": 1,
        "expiredCount": 1,
    }


def test_product_code_em_branco_retorna_422_antes_do_caso_de_uso(
    client, actor_token, viewer_token
):
    body = {
        "changes": [
            {
                "orderId": 42,
                "expectedVersion": "a" * 64,
                "sizes": {"M": 1},
            }
        ]
    }
    with (
        patch(
            "app.modules.pedidos.service.listar_clientes_produto_page",
            new=AsyncMock(),
        ) as list_clients,
        patch(
            "app.modules.pedidos.service.executar_alteracao_grades_produto",
            new=AsyncMock(),
        ) as change_grades,
        patch(
            "app.modules.pedidos.service.executar_aprovacao_produto",
            new=AsyncMock(),
        ) as approve,
    ):
        detail = client.get(
            "/api/v1/pedidos/produtos/clientes?productCode=%20%20&stage=edicao&channel=Franquia",
            headers=auth_header(viewer_token),
        )
        grade = client.put(
            "/api/v1/pedidos/produtos/grades?productCode=%20%20&channel=Franquia",
            json=body,
            headers=auth_header(actor_token),
        )
        approval = client.post(
            "/api/v1/pedidos/produtos/aprovar?productCode=%20%20&channel=Franquia",
            headers=auth_header(actor_token),
        )

    assert detail.status_code == grade.status_code == approval.status_code == 422
    list_clients.assert_not_awaited()
    change_grades.assert_not_awaited()
    approve.assert_not_awaited()


async def test_produtos_rejeita_cursor_e_page_juntos(client, viewer_token):
    """As duas paginacoes sao exclusivas.

    Aceitar as duas silenciosamente faria o servidor escolher uma e ignorar a
    outra, escondendo um bug de chamador atras de uma lista plausivel.
    """
    with patch(
        "app.modules.pedidos.service.listar_produtos_page", new=AsyncMock()
    ) as listar:
        response = client.get(
            "/api/v1/pedidos/produtos?stage=aguardando&page=2&cursor=abc",
            headers=auth_header(viewer_token),
        )

    assert response.status_code == 422
    listar.assert_not_awaited()


async def test_produtos_page_repassa_pagina_numerada_ao_service(client, viewer_token):
    with patch(
        "app.modules.pedidos.service.listar_produtos_page",
        new=AsyncMock(
            return_value={
                "rows": [],
                "total": 0,
                "page_size": 25,
                "next_cursor": None,
                "has_more": False,
                "page": 3,
                "total_pages": 1,
            }
        ),
    ) as listar:
        response = client.get(
            "/api/v1/pedidos/produtos?stage=aguardando&page=3&pageSize=25",
            headers=auth_header(viewer_token),
        )

    assert response.status_code == 200
    assert listar.await_args is not None
    assert listar.await_args.kwargs["pagina_numero"] == 3
    assert listar.await_args.kwargs["cursor"] is None
    body = response.json()
    assert body["page"] == 3
    assert body["totalPages"] == 1


async def test_produtos_sem_page_mantem_contrato_cursor(client, viewer_token):
    with patch(
        "app.modules.pedidos.service.listar_produtos_page",
        new=AsyncMock(
            return_value={
                "rows": [],
                "total": 0,
                "page_size": 25,
                "next_cursor": None,
                "has_more": False,
                "page": None,
                "total_pages": None,
            }
        ),
    ) as listar:
        response = client.get(
            "/api/v1/pedidos/produtos?stage=aguardando",
            headers=auth_header(viewer_token),
        )

    assert response.status_code == 200
    assert listar.await_args is not None
    assert listar.await_args.kwargs["pagina_numero"] is None
    body = response.json()
    assert body["page"] is None and body["totalPages"] is None
    assert body["hasMore"] is False and body["nextCursor"] is None


async def test_aprovar_pedido_sem_or_retorna_404(client, actor_token):
    with (
        patch(
            "app.modules.pedidos.application.casos_uso.carregar_pares_aprovaveis_pedido_para_update",
            new=AsyncMock(return_value=()),
        ),
        patch(
            "app.modules.pedidos.application.casos_uso.aprovar_ordem_reserva",
            new=AsyncMock(return_value=ResultadoAprovacao(0, 0, 0, 0)),
        ),
    ):
        resp = client.post(
            "/api/v1/pedidos/123456/aprovar", headers=auth_header(actor_token)
        )

    assert resp.status_code == 404


def test_aprovar_pedido_valida_integer_e_converte_lock_em_409(client, actor_token):
    too_large = client.post(
        "/api/v1/pedidos/2147483648/aprovar",
        headers=auth_header(actor_token),
    )
    with patch(
        "app.modules.pedidos.service.executar_aprovacao",
        new=AsyncMock(
            side_effect=pedidos_service.ProcessamentoEmAndamentoError("lock ocupado")
        ),
    ):
        locked = client.post(
            "/api/v1/pedidos/123/aprovar",
            headers=auth_header(actor_token),
        )

    assert too_large.status_code == 422
    assert locked.status_code == 409
    assert locked.json()["detail"] == "lock ocupado"


async def test_aprovar_pedido_com_or_sucesso_mockado(client, actor_token):
    with (
        patch(
            "app.modules.pedidos.application.casos_uso.carregar_pares_aprovaveis_pedido_para_update",
            new=AsyncMock(return_value=((654321, "X"),)),
        ),
        patch(
            "app.modules.pedidos.application.casos_uso.aprovar_ordem_reserva",
            new=AsyncMock(return_value=ResultadoAprovacao(1, 1, 0, 0)),
        ) as mock_aprovar,
        patch(
            "app.modules.pedidos.application.casos_uso.observe_entity_keys",
            new=AsyncMock(),
        ) as observe,
        patch(
            "app.modules.pedidos.application.casos_uso.record_event", new=AsyncMock()
        ) as mock_event,
    ):
        resp = client.post(
            "/api/v1/pedidos/654321/aprovar", headers=auth_header(actor_token)
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_aprovar.assert_awaited_once()
    observe.assert_awaited_once()
    assert mock_event.await_count == 2


# ---------------------------------------------------------------------------
# salvar_ordens_reserva direto na camada de serviço (sem TestClient/HTTP), com
# uma sessão simples nunca commitada.
#
# Estes dois testes documentavam o schema drift: o banco tinha a chave composta
# (nr_pedido, cd_prod_cor) e o model declarava só nr_pedido, então TODO INSERT
# de OR nova levantava IntegrityError. A migration 014 alinhou os dois lados e
# eles foram invertidos: agora o INSERT tem de funcionar. A guarda permanente
# contra a recaída está em test_schema_guard.py.
# ---------------------------------------------------------------------------


async def test_salvar_ordens_reserva_par_novo_insere_sem_erro():
    par = (999999010, "PROD_NOVO")
    async with async_session_factory() as session:
        await pedidos_service.salvar_ordens_reserva(
            session, {par: [{"nr_pedido": par[0], "cd_prod_cor": par[1]}]}, tipo="com"
        )
        await session.flush()  # antes da 014 isto levantava IntegrityError


async def test_salvar_ordens_reserva_par_existente_atualiza_sem_erro():
    nr_existente = 999999011
    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO ordens_reserva (nr_pedido, cd_prod_cor, tipo, itens) "
                "VALUES (:nr, :cd, 'sem', '[]'::jsonb)"
            ),
            {"nr": nr_existente, "cd": "LEGADO"},
        )
        await session.flush()

        await pedidos_service.salvar_ordens_reserva(
            session,
            {(nr_existente, "LEGADO"): [{"nr_pedido": nr_existente}]},
            tipo="com",
        )
        await session.flush()  # UPDATE do par existente, não INSERT


async def test_salvar_ordens_reserva_dois_produtos_do_mesmo_pedido_coexistem():
    """Uma OR é um PRODUTO: o mesmo pedido tem uma linha por produto, e elas não
    se sobrescrevem. Era impossível no modelo por pedido (PK escalar)."""
    nr = 999999012
    async with async_session_factory() as session:
        await pedidos_service.salvar_ordens_reserva(
            session,
            {
                (nr, "PROD_A"): [{"nr_pedido": nr, "cd_prod_cor": "PROD_A"}],
                (nr, "PROD_B"): [{"nr_pedido": nr, "cd_prod_cor": "PROD_B"}],
            },
            tipo="com",
        )
        await session.flush()

        total = await session.scalar(
            text("SELECT count(*) FROM ordens_reserva WHERE nr_pedido = :nr"),
            {"nr": nr},
        )
        assert total == 2


async def test_salvar_linhas_linx_par_novo_insere_sem_erro():
    linha = {
        "nr_pedido": 999999020,
        "cd_prod_cor": "PROD_LINX_NOVO",
        "tipo": "com",
        "nome_clifor": "Cliente 999999020",
    }
    async with async_session_factory() as session:
        await salvar_linhas_linx(session, [linha])
        await session.flush()


async def test_salvar_linhas_linx_par_existente_atualiza_sem_duplicar():
    """Critério 4 do ROADMAP: duas gravações sucessivas do mesmo
    (nr_pedido, cd_prod_cor) resultam em 1 linha, não 2, e os valores refletem
    a última gravação."""
    nr = 999999021
    cd = "PROD_LINX_EXISTENTE"
    async with async_session_factory() as session:
        await salvar_linhas_linx(
            session,
            [{"nr_pedido": nr, "cd_prod_cor": cd, "tipo": "sem", "preco1": 10.0}],
        )
        await session.flush()

        await salvar_linhas_linx(
            session,
            [{"nr_pedido": nr, "cd_prod_cor": cd, "tipo": "com", "preco1": 20.0}],
        )
        await session.flush()

        total = await session.scalar(
            text(
                "SELECT count(*) FROM ordens_reserva_linx "
                "WHERE nr_pedido = :nr AND cd_prod_cor = :cd"
            ),
            {"nr": nr, "cd": cd},
        )
        assert total == 1

        row = (
            await session.execute(
                text(
                    "SELECT tipo, preco1 FROM ordens_reserva_linx "
                    "WHERE nr_pedido = :nr AND cd_prod_cor = :cd"
                ),
                {"nr": nr, "cd": cd},
            )
        ).one()
        assert row.tipo == "com"
        assert float(row.preco1) == 20.0
