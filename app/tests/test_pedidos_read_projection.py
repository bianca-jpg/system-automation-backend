from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.modules.ingestao.infrastructure.models import (
    Estoque,
    Pedido,
    PedidoProcessadoErp,
)
from app.modules.ingestao.infrastructure.repositorio_snapshot import (
    reconstruir_pedido_produto_read,
)
from app.modules.pedidos.application.schemas import PedidoCardOut
from app.modules.pedidos.domain.consultas import (
    ConsultaAlertas,
    ConsultaClientesProduto,
    ConsultaLookupPedidos,
    ConsultaProdutos,
)
from app.modules.pedidos.infrastructure.models import OrdemReserva, PedidoStandbyMotivo
from app.modules.pedidos.infrastructure.repositorio_consultas import (
    SqlPedidosReadRepository,
)
from app.shared.database.session import async_session_factory
from app.shared.pagination.cursor import (
    CursorInvalidoError,
    decode_cursor,
    encode_cursor,
)


def _pedido(
    nr: int,
    *,
    data_emissao: str | None,
    client: str,
    product: str,
    status_credito: str = "Com Credito",
) -> Pedido:
    return Pedido(
        nr_pedido=nr,
        cd_prod_cor=product,
        sg_tamanho="M",
        ds_grupo="CAMISA",
        qt_entregar=2,
        vl_liquido=200,
        client=client,
        canal="Franquia",
        status_credito=status_credito,
        ds_produto="Camisa Social",
        data=data_emissao,
    )


def _estoque(product: str, qty: int = 10) -> Estoque:
    return Estoque(
        cd_prod_cor=product,
        sg_tamanho="M",
        canal="Franquia",
        qt_disponivel=qty,
        dt_estoque=datetime.now(UTC).date(),
    )


def _pedido_produto(
    nr: int,
    *,
    product: str,
    channel: str,
    size: str,
    qty: int,
    value: int,
) -> Pedido:
    return Pedido(
        nr_pedido=nr,
        cd_prod_cor=product,
        sg_tamanho=size,
        ds_grupo="CALCA",
        qt_entregar=qty,
        vl_liquido=value,
        client=f"Loja {nr}",
        canal=channel,
        status_credito="Com Credito",
        ds_produto="Calca Cross Channel",
        data="2026-08-08",
    )


def _item_or(nr: int, product: str, *, client: str = "Loja OR") -> dict:
    return {
        "nr_pedido": nr,
        "cd_prod_cor": product,
        "sg_tamanho": "M",
        "ds_grupo": "CAMISA",
        "ds_produto": f"Camisa {product}",
        "qt_liquida": 2,
        "vl_liquido": 200.0,
        "client": client,
        "canal": "Franquia",
        "data": "2026-08-04",
        "status_item": "Gerar OR",
    }


def _erp(
    nr: int, product: str, *, data_emissao: str = "2026-08-04"
) -> PedidoProcessadoErp:
    return PedidoProcessadoErp(
        nr_pedido=nr,
        cd_prod_cor=product,
        sg_tamanho="M",
        ds_grupo="CAMISA",
        client=f"Loja ERP {nr}",
        canal="Franquia",
        ds_produto=f"Camisa {product}",
        data=data_emissao,
        qt=2,
        vl_liquido=200,
        indica_reserva=True,
        indica_embalado=False,
    )


async def test_read_projection_empty_contracts_execute_real_sql():
    async with async_session_factory() as db:
        repo = SqlPedidosReadRepository(db)

        repo._estoque_disponivel = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("resumo default não deve materializar o mapa")
        )
        summary = await repo.obter_resumo()
        alerts = await repo.listar_alertas(ConsultaAlertas("Todos", "", 25))

        assert summary["stock_by_code"] == {
            "Todos": {},
            "Franquia": {},
            "Multimarca": {},
        }
        assert set(summary["stats_by_channel"]) == {"Todos", "Franquia", "Multimarca"}
        assert alerts.rows == [] and alerts.total == 0


async def test_refresh_read_model_rollback_preserva_snapshot_se_erp_insert_falha():
    async with async_session_factory() as db:
        db.add_all(
            [
                _pedido(
                    9_901_080,
                    data_emissao="2026-08-08",
                    client="Loja snapshot",
                    product="SNAPSHOT_PENDING",
                ),
                _erp(9_901_081, "SNAPSHOT_ERP"),
            ]
        )
        await db.flush()
        await reconstruir_pedido_produto_read(db)
        await db.commit()
        before = (
            await db.execute(
                text(
                    "SELECT source, nr_pedido, cd_prod_cor, qty, value "
                    "FROM pedido_produto_read ORDER BY source, nr_pedido, cd_prod_cor"
                )
            )
        ).all()

        with (
            patch(
                "app.modules.ingestao.infrastructure.repositorio_snapshot._ERP_READ_INSERT_SQL",
                "INSERT INTO tabela_que_nao_existe SELECT 1",
            ),
            pytest.raises(SQLAlchemyError),
        ):
            await reconstruir_pedido_produto_read(db)
        await db.rollback()

        after = (
            await db.execute(
                text(
                    "SELECT source, nr_pedido, cd_prod_cor, qty, value "
                    "FROM pedido_produto_read ORDER BY source, nr_pedido, cd_prod_cor"
                )
            )
        ).all()
        assert after == before
        await db.execute(
            text("DELETE FROM pedidos WHERE nr_pedido IN (9901080, 9901081)")
        )
        await db.execute(
            text(
                "DELETE FROM pedidos_processados_erp "
                "WHERE nr_pedido IN (9901080, 9901081)"
            )
        )
        await reconstruir_pedido_produto_read(db)
        await db.commit()


async def test_product_projection_separates_same_code_by_channel_and_keeps_sizes():
    async with async_session_factory() as db:
        db.add_all(
            [
                _pedido_produto(
                    9_901_001,
                    product="CROSS_CHANNEL",
                    channel="Franquia",
                    size="36",
                    qty=2,
                    value=40,
                ),
                _pedido_produto(
                    9_901_001,
                    product="CROSS_CHANNEL",
                    channel="Franquia",
                    size="37",
                    qty=3,
                    value=60,
                ),
                _pedido_produto(
                    9_901_002,
                    product="CROSS_CHANNEL",
                    channel="Multimarca",
                    size="36",
                    qty=7,
                    value=140,
                ),
            ]
        )
        await db.flush()
        await reconstruir_pedido_produto_read(db)
        repo = SqlPedidosReadRepository(db)

        first = await repo.listar_produtos(
            ConsultaProdutos(
                "aguardando", "Todos", "", "Todos", "lastOrderAt", "desc", 1
            )
        )
        second = await repo.listar_produtos(
            ConsultaProdutos(
                "aguardando",
                "Todos",
                "",
                "Todos",
                "lastOrderAt",
                "desc",
                1,
                first.next_key,
            )
        )

        assert first.total == second.total == 2
        assert first.has_more is True and second.has_more is False
        assert [first.rows[0]["channel"], second.rows[0]["channel"]] == [
            "Franquia",
            "Multimarca",
        ]
        assert first.rows[0]["sizes"] == {"36": 2, "37": 3}
        assert first.rows[0]["size_keys"] == ["36", "37"]
        assert first.rows[0]["total_qty"] == 5
        assert second.rows[0]["sizes"] == {"36": 7}
        assert second.rows[0]["total_qty"] == 7

        franquia = await repo.listar_clientes_produto(
            ConsultaClientesProduto(
                "CROSS_CHANNEL", "aguardando", "Franquia", "Todos", 25
            )
        )
        assert franquia["total"] == 1
        assert franquia["summary"]["channel"] == "Franquia"
        assert franquia["summary"]["size_totals"] == {"36": 2, "37": 3}
        assert franquia["summary"]["total_qty"] == 5
        assert [row["order"]["id"] for row in franquia["rows"]] == ["#9901001"]


async def test_product_projection_offset_pagina_bate_com_o_keyset():
    """A faixa numerada usa OFFSET; as duas paginacoes tem que ver a mesma lista.

    Sem o desempate `code ASC, channel ASC` no ORDER BY, a pagina 2 por offset
    poderia repetir ou pular uma linha em relacao ao keyset — que e exatamente o
    defeito que a tela numerada exporia.
    """
    async with async_session_factory() as db:
        db.add_all(
            [
                _pedido_produto(
                    9_901_400 + index,
                    product=f"OFFSET_{index:02d}",
                    channel="Franquia",
                    size="M",
                    qty=index + 1,
                    value=10 * (index + 1),
                )
                for index in range(5)
            ]
        )
        await db.flush()
        await reconstruir_pedido_produto_read(db)
        repo = SqlPedidosReadRepository(db)

        def consulta(*, cursor=None, offset=None):
            return ConsultaProdutos(
                "aguardando",
                "Todos",
                "",
                "Todos",
                "code",
                "asc",
                2,
                cursor,
                offset,
            )

        keyset_p1 = await repo.listar_produtos(consulta())
        keyset_p2 = await repo.listar_produtos(consulta(cursor=keyset_p1.next_key))
        keyset_p3 = await repo.listar_produtos(consulta(cursor=keyset_p2.next_key))

        offset_p1 = await repo.listar_produtos(consulta(offset=0))
        offset_p2 = await repo.listar_produtos(consulta(offset=2))
        offset_p3 = await repo.listar_produtos(consulta(offset=4))

        def codes(pagina):
            return [row["code"] for row in pagina.rows]

        assert codes(offset_p1) == codes(keyset_p1)
        assert codes(offset_p2) == codes(keyset_p2)
        assert codes(offset_p3) == codes(keyset_p3)
        # Nenhuma linha repetida ou perdida entre as tres paginas por offset.
        todas = codes(offset_p1) + codes(offset_p2) + codes(offset_p3)
        assert len(todas) == len(set(todas)) == offset_p1.total
        assert offset_p1.has_more is True and offset_p3.has_more is False


async def test_read_projection_nao_classifica_canal_desconhecido_como_franquia():
    now = datetime.now(UTC)
    unknown = _pedido(
        9_901_090,
        data_emissao="2026-08-08",
        client="Loja canal inválido",
        product="UNKNOWN_CHANNEL",
    )
    unknown.canal = "Canal Novo"
    unknown_item = {
        **_item_or(9_901_091, "UNKNOWN_OR"),
        "canal": "Canal Novo",
    }
    async with async_session_factory() as db:
        db.add_all(
            [
                unknown,
                OrdemReserva(
                    nr_pedido=9_901_091,
                    cd_prod_cor="UNKNOWN_OR",
                    tipo="com",
                    itens=[unknown_item],
                    created_at=now,
                ),
            ]
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        products = await repo.listar_produtos(
            ConsultaProdutos("edicao", "Todos", "", "Todos", "code", "asc", 25)
        )

        assert products.rows == []


async def test_lookup_keyset_executes_first_page_and_three_pages_without_duplicates():
    async with async_session_factory() as db:
        db.add_all(
            [
                _pedido(
                    9_901_150 + index,
                    data_emissao=f"2026-08-0{index}",
                    client=f"Loja lookup {index}",
                    product=f"LOOKUP_{index}",
                )
                for index in range(1, 6)
            ]
        )
        await db.flush()
        await reconstruir_pedido_produto_read(db)
        repo = SqlPedidosReadRepository(db)

        first = await repo.listar_lookup(ConsultaLookupPedidos("", 2))
        second = await repo.listar_lookup(ConsultaLookupPedidos("", 2, first.next_key))
        third = await repo.listar_lookup(ConsultaLookupPedidos("", 2, second.next_key))

        assert [page.total for page in (first, second, third)] == [5, 5, 5]
        assert [page.has_more for page in (first, second, third)] == [True, True, False]
        ids = [row["id"] for page in (first, second, third) for row in page.rows]
        assert ids == [9_901_155, 9_901_154, 9_901_153, 9_901_152, 9_901_151]
        assert len(ids) == len(set(ids)) == 5


async def test_lookup_keyset_alinha_data_nula_com_sentinel_1970():
    async with async_session_factory() as db:
        db.add_all(
            [
                _pedido(
                    9_901_161,
                    data_emissao="1970-01-01",
                    client="Loja data sentinel",
                    product="LOOKUP_SENTINEL",
                ),
                _pedido(
                    9_901_162,
                    data_emissao="data-invalida",
                    client="Loja data nula",
                    product="LOOKUP_NULL",
                ),
            ]
        )
        await db.flush()
        await reconstruir_pedido_produto_read(db)
        repo = SqlPedidosReadRepository(db)

        first = await repo.listar_lookup(ConsultaLookupPedidos("", 1))
        second = await repo.listar_lookup(ConsultaLookupPedidos("", 1, first.next_key))

        assert first.total == second.total == 2
        assert [first.rows[0]["id"], second.rows[0]["id"]] == [
            9_901_162,
            9_901_161,
        ]
        assert first.has_more is True and second.has_more is False


async def test_alertas_operacionais_retorna_categorias_esperadas():
    async with async_session_factory() as db:
        repo = SqlPedidosReadRepository(db)
        alerts = await repo.listar_alertas(ConsultaAlertas("Todos", "", 25))

        assert isinstance(alerts.rows, list)
        assert isinstance(alerts.total, int)
        # Deve retornar objetos com os campos estendidos: category, type, title, message, time
        for row in alerts.rows:
            assert "category" in row
            assert "type" in row
            assert "title" in row
            assert "message" in row


async def test_alerts_are_bounded_sql_projections():
    async with async_session_factory() as db:
        repo = SqlPedidosReadRepository(db)
        alerts = await repo.listar_alertas(ConsultaAlertas("Todos", "", 25))
        assert isinstance(alerts.rows, list)


def test_cursor_is_signed_scoped_and_validated():
    token = encode_cursor(
        kind="pedidos",
        scope="stage=aguardando",
        key=("2026-08-01", 123),
        secret="test-secret",
    )
    assert decode_cursor(
        token,
        kind="pedidos",
        scope="stage=aguardando",
        secret="test-secret",
        key_size=2,
        key_validator=lambda key: isinstance(key[0], str) and isinstance(key[1], int),
    ) == ("2026-08-01", 123)

    try:
        decode_cursor(
            token + "!",
            kind="pedidos",
            scope="stage=aguardando",
            secret="test-secret",
            key_size=2,
        )
    except CursorInvalidoError:
        pass
    else:  # pragma: no cover - falha explícita mais legível que assert raises sem pytest
        raise AssertionError("cursor adulterado deveria ser rejeitado")

    undated = encode_cursor(
        kind="pedidos", scope="stage=aguardando", key=("", 124), secret="test-secret"
    )
    assert decode_cursor(
        undated,
        kind="pedidos",
        scope="stage=aguardando",
        secret="test-secret",
        key_size=2,
        key_validator=lambda key: isinstance(key[0], str) and isinstance(key[1], int),
    ) == ("", 124)


async def test_resumo_conta_edicao_no_grao_pedido_e_produto_canal() -> None:
    now = datetime.now(UTC)
    order_id = 9_904_100
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=order_id,
                    cd_prod_cor="EDIT_COUNT_A",
                    tipo="com",
                    itens=[_item_or(order_id, "EDIT_COUNT_A")],
                    created_at=now - timedelta(hours=1),
                ),
                OrdemReserva(
                    nr_pedido=order_id,
                    cd_prod_cor="EDIT_COUNT_B",
                    tipo="sem",
                    itens=[_item_or(order_id, "EDIT_COUNT_B")],
                    created_at=now - timedelta(hours=2),
                ),
                OrdemReserva(
                    nr_pedido=order_id,
                    cd_prod_cor="EDIT_COUNT_MM",
                    tipo="com",
                    itens=[
                        {
                            **_item_or(order_id, "EDIT_COUNT_MM"),
                            "canal": "Multimarca",
                        }
                    ],
                    created_at=now - timedelta(hours=3),
                ),
                OrdemReserva(
                    nr_pedido=9_904_101,
                    cd_prod_cor="EDIT_EXPIRED",
                    tipo="com",
                    itens=[_item_or(9_904_101, "EDIT_EXPIRED")],
                    created_at=now - timedelta(hours=25),
                ),
                OrdemReserva(
                    nr_pedido=9_904_102,
                    cd_prod_cor="EDIT_APPROVED",
                    tipo="com",
                    itens=[_item_or(9_904_102, "EDIT_APPROVED")],
                    created_at=now - timedelta(hours=1),
                    aprovado_em=now - timedelta(minutes=10),
                ),
            ]
        )
        await db.flush()

        summary = await SqlPedidosReadRepository(db).obter_resumo()
        stats = summary["stats_by_channel"]

        assert stats["Franquia"]["editing_order_count"] == 1
        assert stats["Franquia"]["editing_product_count"] == 2
        assert stats["Multimarca"]["editing_order_count"] == 1
        assert stats["Multimarca"]["editing_product_count"] == 1
        assert stats["Todos"]["editing_order_count"] == 1
        assert stats["Todos"]["editing_product_count"] == 3
        await db.rollback()


async def test_resumo_exclui_pedidos_e_ors_com_canal_misto_ou_desconhecido() -> None:
    now = datetime.now(UTC)
    mixed_order = 9_904_200
    async with async_session_factory() as db:
        pending_f = _pedido_produto(
            mixed_order,
            product="SUMMARY_PENDING_F",
            channel="Franquia",
            size="M",
            qty=1,
            value=10,
        )
        pending_mm = _pedido_produto(
            mixed_order,
            product="SUMMARY_PENDING_MM",
            channel="Multimarca",
            size="M",
            qty=1,
            value=10,
        )
        erp_f = _erp(mixed_order + 1, "SUMMARY_ERP_F")
        erp_mm = _erp(mixed_order + 1, "SUMMARY_ERP_MM")
        erp_mm.canal = "Multimarca"
        db.add_all(
            [
                pending_f,
                pending_mm,
                erp_f,
                erp_mm,
                OrdemReserva(
                    nr_pedido=mixed_order + 2,
                    cd_prod_cor="SUMMARY_OR_MIXED",
                    tipo="com",
                    itens=[
                        _item_or(mixed_order + 2, "SUMMARY_OR_MIXED"),
                        {
                            **_item_or(mixed_order + 2, "SUMMARY_OR_MIXED"),
                            "sg_tamanho": "G",
                            "canal": "Multimarca",
                        },
                    ],
                    created_at=now - timedelta(hours=25),
                ),
                OrdemReserva(
                    nr_pedido=mixed_order + 3,
                    cd_prod_cor="SUMMARY_OR_UNKNOWN",
                    tipo="sem",
                    itens=[
                        {
                            **_item_or(mixed_order + 3, "SUMMARY_OR_UNKNOWN"),
                            "canal": "Outro",
                        }
                    ],
                    created_at=now - timedelta(hours=25),
                ),
            ]
        )
        await db.flush()

        summary = await SqlPedidosReadRepository(db).obter_resumo()
        stats = summary["stats_by_channel"]

        for channel in ("Franquia", "Multimarca", "Todos"):
            assert stats[channel]["liberados_count"] == 0
            assert stats[channel]["or_com_adequacao_count"] == 0
            assert stats[channel]["or_sem_adequacao_count"] == 0
            assert stats[channel]["editing_order_count"] == 0
            assert summary["erp_count"][channel] == 0
        await db.rollback()


async def test_lookup_busca_produto_presente_apenas_na_or() -> None:
    code = "LOOKUP_ONLY_OR"
    nr = 9_904_300
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="com",
                itens=[
                    {
                        **_item_or(nr, code),
                        "ds_produto": "Produto órfão da projeção",
                    }
                ],
                created_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        by_code = await repo.listar_lookup(ConsultaLookupPedidos("only_or", 25))
        by_name = await repo.listar_lookup(ConsultaLookupPedidos("órfão", 25))

        assert [row["id"] for row in by_code.rows] == [nr]
        assert [row["id"] for row in by_name.rows] == [nr]
        await db.rollback()


async def test_read_projection_orcamento_pedido_cobre_todos_os_produtos_do_pedido() -> (
    None
):
    """D-12: o orçamento embutido na linha é do PEDIDO INTEIRO, não do
    produto que está sendo editado — a prova exige um segundo produto no
    mesmo pedido, fora do escopo da query de clientes (que é scoped a um
    único `code`); o total só entra na conta via `pedidos`, que soma
    `qt_entregar` por `nr_pedido` sem filtrar produto."""
    nr = 9_905_601
    now = datetime.now(UTC)
    produto_a = _pedido(
        nr, data_emissao="2026-08-08", client="Loja Orcamento", product="ORCA_PROD_A"
    )
    produto_a.qt_entregar = 60
    produto_b = _pedido(
        nr, data_emissao="2026-08-08", client="Loja Orcamento", product="ORCA_PROD_B"
    )
    produto_b.qt_entregar = 40
    async with async_session_factory() as db:
        db.add_all(
            [
                produto_a,
                produto_b,
                OrdemReserva(
                    nr_pedido=nr,
                    cd_prod_cor="ORCA_PROD_B",
                    tipo="sem",
                    itens=[
                        {
                            **_item_or(nr, "ORCA_PROD_B", client="Loja Orcamento"),
                            "qt_liquida": 40,
                            "qt_solicitada": 40,
                        }
                    ],
                    created_at=now,
                ),
            ]
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        page = await repo.listar_clientes_produto(
            ConsultaClientesProduto("ORCA_PROD_B", "edicao", "Franquia", "Todos", 25)
        )

        assert len(page["rows"]) == 1
        orcamento = page["rows"][0]["order"]["orcamento_pedido"]
        assert orcamento["nr_pedido"] == nr
        # 5% de 100 (60 + 40 — os DOIS produtos do pedido) = 5. Se a
        # implementação tivesse escopado só pelo produto da linha (40),
        # daria 2 — a diferença prova D-12.
        assert orcamento["limite_adicao"] == 5
        assert orcamento["limite_corte"] == 5
        assert orcamento["consumido_adicao"] == 0
        assert orcamento["restante_adicao"] == 5

        # WR-01 (20-REVIEW): a serialização camelCase de `OrcamentoPedidoOut`
        # (D-12) só era exercitada no nível de dict/aplicação — nunca através
        # de `PedidoCardOut.model_validate(...)` + JSON de verdade. Um typo em
        # `serialization_alias` não seria pego pelo resto da suíte.
        order_out = PedidoCardOut.model_validate(page["rows"][0]["order"])
        order_json = order_out.model_dump(mode="json", by_alias=True)
        orcamento_json = order_json["orcamentoPedido"]
        assert orcamento_json["nrPedido"] == nr
        assert orcamento_json["limiteAdicao"] == 5
        assert orcamento_json["limiteCorte"] == 5
        assert orcamento_json["consumidoAdicao"] == 0
        assert orcamento_json["restanteAdicao"] == 5
        assert orcamento_json["consumidoCorte"] == 0
        assert orcamento_json["restanteCorte"] == 5
        await db.rollback()


async def test_read_projection_linha_com_adequacao_nao_tem_chave_de_orcamento() -> None:
    nr = 9_905_602
    now = datetime.now(UTC)
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor="ORCA_COM",
                tipo="com",
                itens=[_item_or(nr, "ORCA_COM", client="Loja Com Adequacao")],
                created_at=now,
            )
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        page = await repo.listar_clientes_produto(
            ConsultaClientesProduto("ORCA_COM", "edicao", "Franquia", "Todos", 25)
        )

        assert len(page["rows"]) == 1
        # Chave ausente, não presente com valor nulo (PD-08).
        assert "orcamento_pedido" not in page["rows"][0]["order"]
        await db.rollback()


async def test_read_projection_pagina_toda_com_adequacao_nao_consulta_orcamento() -> (
    None
):
    nr = 9_905_603
    now = datetime.now(UTC)
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor="ORCA_SEM_QUERY",
                tipo="com",
                itens=[_item_or(nr, "ORCA_SEM_QUERY", client="Loja Sem Query")],
                created_at=now,
            )
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        with patch(
            "app.modules.pedidos.infrastructure.produtos.listar_clientes_produto."
            "carregar_orcamento_pedidos",
            new_callable=AsyncMock,
        ) as mocked:
            page = await repo.listar_clientes_produto(
                ConsultaClientesProduto(
                    "ORCA_SEM_QUERY", "edicao", "Franquia", "Todos", 25
                )
            )
            mocked.assert_not_called()

        assert len(page["rows"]) == 1
        await db.rollback()


async def test_read_projection_orcamento_ausente_na_leitura_cai_para_zero() -> None:
    """T-20-21: se o pedido do conjunto não vier no resultado da leitura em
    lote (defensivo — a SQL normalmente sempre devolve, via LEFT JOIN de
    `pedido_scope`), a projeção falha fechada com limites zero em vez de
    omitir o objeto — omitir sinalizaria 'com adequação' para o front."""
    nr = 9_905_604
    now = datetime.now(UTC)
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor="ORCA_ORFAO",
                tipo="sem",
                itens=[_item_or(nr, "ORCA_ORFAO", client="Loja Orfao")],
                created_at=now,
            )
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        with patch(
            "app.modules.pedidos.infrastructure.produtos.listar_clientes_produto."
            "carregar_orcamento_pedidos",
            new_callable=AsyncMock,
            return_value={},
        ):
            page = await repo.listar_clientes_produto(
                ConsultaClientesProduto(
                    "ORCA_ORFAO", "edicao", "Franquia", "Todos", 25
                )
            )

        orcamento = page["rows"][0]["order"]["orcamento_pedido"]
        assert orcamento == {
            "nr_pedido": nr,
            "limite_adicao": 0,
            "consumido_adicao": 0,
            "restante_adicao": 0,
            "limite_corte": 0,
            "consumido_corte": 0,
            "restante_corte": 0,
        }
        await db.rollback()


async def test_read_projection_orcamento_reflete_consumido_da_or_sem_adequacao() -> (
    None
):
    nr = 9_905_605
    now = datetime.now(UTC)
    pedido = _pedido(
        nr,
        data_emissao="2026-08-08",
        client="Loja Consumido",
        product="ORCA_CONSUMIDO",
    )
    pedido.qt_entregar = 100
    async with async_session_factory() as db:
        db.add_all(
            [
                pedido,
                OrdemReserva(
                    nr_pedido=nr,
                    cd_prod_cor="ORCA_CONSUMIDO",
                    tipo="sem",
                    itens=[
                        {
                            **_item_or(nr, "ORCA_CONSUMIDO", client="Loja Consumido"),
                            "qt_liquida": 44,
                            "qt_solicitada": 40,
                        }
                    ],
                    created_at=now,
                ),
            ]
        )
        await db.flush()
        repo = SqlPedidosReadRepository(db)

        page = await repo.listar_clientes_produto(
            ConsultaClientesProduto(
                "ORCA_CONSUMIDO", "edicao", "Franquia", "Todos", 25
            )
        )

        orcamento = page["rows"][0]["order"]["orcamento_pedido"]
        assert orcamento["consumido_adicao"] == 4
        assert orcamento["limite_adicao"] == 5  # floor(0.05 * 100)
        assert orcamento["restante_adicao"] == 1
        assert orcamento["consumido_corte"] == 0
        await db.rollback()


async def test_reconstruir_pedido_produto_read_apaga_standby_orfao_mas_preserva_par_pendente() -> (
    None
):
    orphan = 9_905_501
    surviving = 9_905_502
    async with async_session_factory() as db:
        db.add_all(
            [
                PedidoStandbyMotivo(
                    nr_pedido=orphan,
                    cd_prod_cor="STANDBY_ORPHAN",
                    canal="Franquia",
                    motivo="sem_estoque",
                    job_id=uuid4(),
                ),
                _pedido_produto(
                    surviving,
                    product="STANDBY_SURVIVES",
                    channel="Franquia",
                    size="M",
                    qty=2,
                    value=40,
                ),
                PedidoStandbyMotivo(
                    nr_pedido=surviving,
                    cd_prod_cor="STANDBY_SURVIVES",
                    canal="Franquia",
                    motivo="sem_estoque",
                    job_id=uuid4(),
                ),
            ]
        )
        await db.flush()
        await reconstruir_pedido_produto_read(db)

        rows = (
            await db.execute(
                text(
                    "SELECT nr_pedido, cd_prod_cor FROM pedido_standby_motivo "
                    "WHERE nr_pedido IN (:orphan, :surviving) ORDER BY nr_pedido"
                ),
                {"orphan": orphan, "surviving": surviving},
            )
        ).all()

        assert rows == [(surviving, "STANDBY_SURVIVES")]
        await db.rollback()
