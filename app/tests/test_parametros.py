import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.domain.roles import automationRole
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import create_access_token
from app.modules.parametros.domain.coercao import _coerce
from app.modules.parametros.service import get_param_value
from app.shared.config.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers de setup — mesmo padrão de test_auth_flows.py: engine descartável
# (NullPool) para não prender conexão a um event loop diferente do TestClient.
# ---------------------------------------------------------------------------


def _chave_unica(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex[:8]}"


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


def criar_usuario(prefixo: str, roles):
    email = f"{prefixo}-{uuid.uuid4().hex[:8]}@teste.project.com"
    user_id = asyncio.run(_criar_usuario_async(email=email, roles=roles))
    return user_id, email


def token_de(user_id: int, email: str, roles: list) -> str:
    # Emissão direta do token, substituindo o POST /api/auth/sign-in que
    # deixou de existir (login por senha removido) — mesmo padrão adotado em
    # test_auth_flows.py.
    return create_access_token(user_id=user_id, email=email, roles=list(roles))[0]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tokens(client):
    """Um usuário de cada nível relevante, com token emitido direto."""
    tokens_por_papel = {}
    for papel in (automationRole.BASICO, automationRole.GESTOR, automationRole.ADMINISTRADOR):
        uid, email = criar_usuario(f"parametros-{papel.value}", [papel])
        tokens_por_papel[papel] = token_de(uid, email, [papel])
    return tokens_por_papel


# ---------------------------------------------------------------------------
# CRUD de parâmetros
# ---------------------------------------------------------------------------


def test_criar_parametro_exige_admin(client, tokens):
    chave = _chave_unica("rbac-criar")
    resp_viewer = client.post(
        "/api/v1/parametros",
        json={"chave": chave, "valor": "1", "tipo": "int"},
        headers=auth_header(tokens[automationRole.BASICO]),
    )
    assert resp_viewer.status_code == 403

    resp_admin = client.post(
        "/api/v1/parametros",
        json={"chave": chave, "valor": "1", "tipo": "int"},
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )
    assert resp_admin.status_code == 201
    assert resp_admin.json()["chave"] == chave


def test_criar_parametro_chave_duplicada_retorna_409(client, tokens):
    chave = _chave_unica("duplicada")
    headers = auth_header(tokens[automationRole.ADMINISTRADOR])
    client.post(
        "/api/v1/parametros", json={"chave": chave, "valor": "1"}, headers=headers
    )

    resp = client.post(
        "/api/v1/parametros", json={"chave": chave, "valor": "2"}, headers=headers
    )

    assert resp.status_code == 409
    assert chave in resp.json()["detail"]


def test_atualizar_parametro_inexistente_retorna_404(client, tokens):
    resp = client.put(
        f"/api/v1/parametros/{_chave_unica('inexistente')}",
        json={"valor": "novo"},
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Parâmetro não encontrado."


def test_atualizar_parametro_sucesso(client, tokens):
    chave = _chave_unica("atualizar")
    headers = auth_header(tokens[automationRole.ADMINISTRADOR])
    client.post(
        "/api/v1/parametros",
        json={"chave": chave, "valor": "1", "tipo": "int"},
        headers=headers,
    )

    resp = client.put(
        f"/api/v1/parametros/{chave}", json={"valor": "99"}, headers=headers
    )

    assert resp.status_code == 200
    assert resp.json()["valor"] == "99"


def test_excluir_parametro_inexistente_retorna_404(client, tokens):
    resp = client.delete(
        f"/api/v1/parametros/{_chave_unica('inexistente')}",
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )
    assert resp.status_code == 404


def test_excluir_parametro_sucesso(client, tokens):
    chave = _chave_unica("excluir")
    headers = auth_header(tokens[automationRole.ADMINISTRADOR])
    client.post(
        "/api/v1/parametros", json={"chave": chave, "valor": "1"}, headers=headers
    )

    resp = client.delete(f"/api/v1/parametros/{chave}", headers=headers)
    assert resp.status_code == 204

    listagem = client.get(
        "/api/v1/parametros", params={"search": chave}, headers=headers
    ).json()
    assert not any(p["chave"] == chave for p in listagem["rows"])


def test_listar_parametros_contem_criado(client, tokens):
    # Busca pela chave em vez de varrer a primeira página: a listagem ordena por
    # `chave` ascendente com página de 25, então assumir que o registro recém-criado
    # cai na página 1 só funciona enquanto a tabela é pequena. O banco de dev é
    # persistente e acumula um parâmetro por execução da suíte — o teste passava por
    # sorte e quebrou ao cruzar ~25 linhas acumuladas, sem nenhuma mudança de código.
    chave = _chave_unica("listar")
    headers = auth_header(tokens[automationRole.ADMINISTRADOR])
    client.post(
        "/api/v1/parametros", json={"chave": chave, "valor": "1"}, headers=headers
    )

    resp = client.get(
        f"/api/v1/parametros?search={chave}",
        headers=auth_header(tokens[automationRole.BASICO]),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["pageSize"] == 25
    assert any(p["chave"] == chave for p in body["rows"])


def test_listar_parametros_paginas_1_2_3_com_busca_e_ordem(client, tokens):
    marker = _chave_unica("pagina-parametros")
    headers = auth_header(tokens[automationRole.ADMINISTRADOR])
    chaves = [f"{marker}-{suffix}" for suffix in ("c", "a", "b")]
    for chave in chaves:
        response = client.post(
            "/api/v1/parametros",
            json={"chave": chave, "valor": "1", "tipo": "int"},
            headers=headers,
        )
        assert response.status_code == 201

    pages = [
        client.get(
            "/api/v1/parametros",
            params={
                "page": page,
                "pageSize": 1,
                "search": marker,
                "sort": "name",
                "order": "asc",
            },
            headers=headers,
        ).json()
        for page in (1, 2, 3)
    ]

    assert all(page["total"] == 3 for page in pages)
    assert all(page["totalPages"] == 3 for page in pages)
    assert [page["rows"][0]["chave"] for page in pages] == sorted(chaves)


# ---------------------------------------------------------------------------
# Change requests
# ---------------------------------------------------------------------------


def test_criar_solicitacao_exige_gestor(client, tokens):
    resp_basico = client.post(
        "/api/v1/parametros/change-requests",
        json={"change_type": "update", "target_chave": "x", "proposed_payload": {}},
        headers=auth_header(tokens[automationRole.BASICO]),
    )
    assert resp_basico.status_code == 403

    resp_gestor = client.post(
        "/api/v1/parametros/change-requests",
        json={"change_type": "update", "target_chave": "x", "proposed_payload": {}},
        headers=auth_header(tokens[automationRole.GESTOR]),
    )
    assert resp_gestor.status_code == 201
    assert resp_gestor.json()["status"] == "pending"


def test_listar_solicitacoes_gestor_ve_so_as_suas_admin_ve_todas(client):
    uid_a, email_a = criar_usuario("scope-gestor-a", [automationRole.GESTOR])
    uid_b, email_b = criar_usuario("scope-gestor-b", [automationRole.GESTOR])
    uid_admin, email_admin = criar_usuario("scope-admin", [automationRole.ADMINISTRADOR])
    token_a = token_de(uid_a, email_a, [automationRole.GESTOR])
    token_b = token_de(uid_b, email_b, [automationRole.GESTOR])
    token_admin = token_de(uid_admin, email_admin, [automationRole.ADMINISTRADOR])

    marca = _chave_unica("scope")
    client.post(
        "/api/v1/parametros/change-requests",
        json={"change_type": "update", "target_chave": marca, "proposed_payload": {}},
        headers=auth_header(token_a),
    )
    client.post(
        "/api/v1/parametros/change-requests",
        json={"change_type": "update", "target_chave": marca, "proposed_payload": {}},
        headers=auth_header(token_b),
    )

    vistas_por_a = client.get(
        "/api/v1/parametros/change-requests",
        params={"search": marca},
        headers=auth_header(token_a),
    ).json()["rows"]
    vistas_por_a_desta_marca = [r for r in vistas_por_a if r["target_chave"] == marca]
    assert len(vistas_por_a_desta_marca) == 1

    vistas_por_admin = client.get(
        "/api/v1/parametros/change-requests",
        params={"search": marca},
        headers=auth_header(token_admin),
    ).json()["rows"]
    vistas_por_admin_desta_marca = [
        r for r in vistas_por_admin if r["target_chave"] == marca
    ]
    assert len(vistas_por_admin_desta_marca) == 2


def test_listar_solicitacoes_paginas_1_2_3_por_status(client):
    uid_admin, email_admin = criar_usuario(
        "pagina-change-admin", [automationRole.ADMINISTRADOR]
    )
    uid_gestor, email_gestor = criar_usuario(
        "pagina-change-gestor", [automationRole.GESTOR]
    )
    token_admin = token_de(uid_admin, email_admin, [automationRole.ADMINISTRADOR])
    token_gestor = token_de(uid_gestor, email_gestor, [automationRole.GESTOR])
    marker = _chave_unica("pagina-change")
    for suffix in ("c", "a", "b"):
        response = client.post(
            "/api/v1/parametros/change-requests",
            json={
                "change_type": "update",
                "target_chave": f"{marker}-{suffix}",
                "proposed_payload": {},
            },
            headers=auth_header(token_gestor),
        )
        assert response.status_code == 201

    pages = [
        client.get(
            "/api/v1/parametros/change-requests",
            params={
                "page": page,
                "pageSize": 1,
                "search": marker,
                "statusFilter": "pending",
                "sort": "parameter",
                "order": "asc",
            },
            headers=auth_header(token_admin),
        ).json()
        for page in (1, 2, 3)
    ]

    assert all(page["total"] == 3 for page in pages)
    assert all(page["totalPages"] == 3 for page in pages)
    assert [page["rows"][0]["target_chave"] for page in pages] == sorted(
        f"{marker}-{suffix}" for suffix in ("c", "a", "b")
    )


def test_aprovar_solicitacao_exige_admin_e_e_idempotente_contra_dupla_revisao(
    client, tokens
):
    resp_criacao = client.post(
        "/api/v1/parametros/change-requests",
        json={
            "change_type": "update",
            "target_chave": _chave_unica("aprovar"),
            "proposed_payload": {},
        },
        headers=auth_header(tokens[automationRole.GESTOR]),
    )
    request_id = resp_criacao.json()["id"]

    resp_gestor = client.post(
        f"/api/v1/parametros/change-requests/{request_id}/approve",
        headers=auth_header(tokens[automationRole.GESTOR]),
    )
    assert resp_gestor.status_code == 403

    resp_admin = client.post(
        f"/api/v1/parametros/change-requests/{request_id}/approve",
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )
    assert resp_admin.status_code == 200
    assert resp_admin.json()["status"] == "approved"
    assert resp_admin.json()["reviewed_by"] is not None

    resp_segunda_revisao = client.post(
        f"/api/v1/parametros/change-requests/{request_id}/reject",
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )
    assert resp_segunda_revisao.status_code == 409


def test_rejeitar_solicitacao_sucesso(client, tokens):
    resp_criacao = client.post(
        "/api/v1/parametros/change-requests",
        json={
            "change_type": "update",
            "target_chave": _chave_unica("rejeitar"),
            "proposed_payload": {},
        },
        headers=auth_header(tokens[automationRole.GESTOR]),
    )
    request_id = resp_criacao.json()["id"]

    resp = client.post(
        f"/api/v1/parametros/change-requests/{request_id}/reject",
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_revisar_solicitacao_inexistente_retorna_404(client, tokens):
    resp = client.post(
        "/api/v1/parametros/change-requests/999999999/approve",
        headers=auth_header(tokens[automationRole.ADMINISTRADOR]),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Unit tests: _coerce e get_param_value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor,tipo,esperado",
    [
        ("3.14", "float", 3.14),
        ("42", "int", 42),
        ("true", "bool", True),
        ("sim", "bool", True),
        ("0", "bool", False),
        ("nao", "bool", False),
        ('{"a": 1}', "json", {"a": 1}),
        ("texto livre", "string", "texto livre"),
    ],
)
def test_coerce_por_tipo(valor, tipo, esperado):
    assert _coerce(valor, tipo) == esperado


async def test_get_param_value_usa_default_quando_parametro_nao_existe():
    from app.shared.database.session import async_session_factory

    async with async_session_factory() as session:
        resultado = await get_param_value(
            session, _chave_unica("nao-existe"), default=0.05, cast=float
        )
    assert resultado == 0.05


async def test_get_param_value_usa_default_quando_cast_falha():
    from unittest.mock import AsyncMock, patch

    from app.modules.parametros.infrastructure.models import Parametro

    param_invalido = Parametro(chave="x", valor="nao-e-um-numero", tipo="float")
    with patch(
        "app.modules.parametros.service.get_parametro",
        new=AsyncMock(return_value=param_invalido),
    ):
        from app.shared.database.session import async_session_factory

        async with async_session_factory() as session:
            resultado = await get_param_value(session, "x", default=0.05, cast=float)

    assert resultado == 0.05


# ---------------------------------------------------------------------------
# Bounds de tamanho nos schemas (nivel de schema puro, sem client/banco): o
# 422 do FastAPI e automatico a partir do ValidationError do pydantic.
# ---------------------------------------------------------------------------


def test_parametro_create_recusa_valor_acima_de_4000_caracteres():
    from pydantic import ValidationError

    from app.modules.parametros.application.schemas import ParametroCreate

    with pytest.raises(ValidationError):
        ParametroCreate(chave="x", valor="a" * 4_001)


def test_parametro_create_recusa_descricao_acima_de_4000_caracteres():
    from pydantic import ValidationError

    from app.modules.parametros.application.schemas import ParametroCreate

    with pytest.raises(ValidationError):
        ParametroCreate(chave="x", valor="ok", descricao="a" * 4_001)


def test_parametro_update_recusa_valor_e_descricao_acima_de_4000_caracteres():
    from pydantic import ValidationError

    from app.modules.parametros.application.schemas import ParametroUpdate

    with pytest.raises(ValidationError):
        ParametroUpdate(valor="a" * 4_001)
    with pytest.raises(ValidationError):
        ParametroUpdate(descricao="a" * 4_001)


def test_change_request_create_recusa_justification_acima_de_2000_caracteres():
    from pydantic import ValidationError

    from app.modules.parametros.application.schemas import ChangeRequestCreate
    from app.modules.parametros.infrastructure.models import ChangeRequestType

    with pytest.raises(ValidationError):
        ChangeRequestCreate(
            change_type=ChangeRequestType.UPDATE,
            justification="a" * 2_001,
        )


def test_change_request_create_recusa_proposed_payload_acima_de_16kib():
    from pydantic import ValidationError

    from app.modules.parametros.application.schemas import ChangeRequestCreate
    from app.modules.parametros.infrastructure.models import ChangeRequestType

    with pytest.raises(ValidationError):
        ChangeRequestCreate(
            change_type=ChangeRequestType.UPDATE,
            proposed_payload={"k": "x" * 20_000},
        )


def test_change_request_create_aceita_payload_pequeno():
    from app.modules.parametros.application.schemas import ChangeRequestCreate
    from app.modules.parametros.infrastructure.models import ChangeRequestType

    change_request = ChangeRequestCreate(
        change_type=ChangeRequestType.UPDATE,
        proposed_payload={"valor": "0.05"},
        justification="ajuste de tolerancia",
    )
    assert change_request.proposed_payload == {"valor": "0.05"}
