from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestao.infrastructure.models import Estoque
from app.modules.pedidos.application import casos_uso
from app.modules.pedidos.application.commands import AlterarGradesProdutoCommand
from app.modules.pedidos.application.ports import ResultadoAprovacaoOrdem
from app.modules.pedidos.application.schemas import AlterarGradesProdutoRequest
from app.modules.pedidos.domain.consultas import ConsultaClientesProduto
from app.modules.pedidos.domain.edicao_grade import montar_grade_atualizada
from app.modules.pedidos.domain.orcamento_edicao import OrcamentoPedidoExcedidoError
from app.modules.pedidos.domain.versao_ordem import calcular_versao_ordem
from app.modules.pedidos.infrastructure.models import (
    EstoqueVirtual,
    OrdemReserva,
    PedidoModificacao,
)
from app.modules.pedidos.infrastructure.repositorio_consultas import (
    SqlPedidosReadRepository,
)
from app.modules.pedidos.infrastructure.repositorio_estoque_virtual import (
    carregar_estoque_disponivel_alvos,
    recalcular_estoque_virtual_alvos,
)
from app.modules.pedidos.infrastructure.repositorio_produto_writes import (
    OrdemProdutoState,
    ResultadoAprovacaoProduto,
    aprovar_produto_canal,
    atualizar_grades_em_lote,
    carregar_ordens_produto_para_update,
    upsert_modificacoes_em_lote,
)
from app.modules.pedidos.infrastructure.write_adapter import (
    SqlAlchemyPedidosWriteUnitOfWork,
)
from app.shared.database.advisory_locks import ORDER_STATE_MUTATION_LOCK
from app.shared.database.session import async_session_factory


def _item(
    nr: int,
    *,
    code: str = "BATCH_GRADE",
    channel: str | None = "Franquia",
    size: str = "M",
    qty: int = 5,
    value: float = 100,
) -> dict:
    return {
        "nr_pedido": nr,
        "cd_prod_cor": code,
        "sg_tamanho": size,
        "ds_grupo": "CAMISA",
        "qt_liquida": qty,
        "vl_liquido": value,
        "client": f"Loja {nr}",
        "canal": channel,
        "status_credito": "Com Credito",
        "ds_produto": "Camisa Batch",
        "data": "2026-08-08",
        "status_item": "Gerar OR",
        "qt_solicitada": qty,
        "diff_valor": 0,
    }


def _state(
    nr: int, *, items: list[dict] | None = None, tipo: str = "com"
) -> OrdemProdutoState:
    return OrdemProdutoState(
        nr_pedido=nr,
        cd_prod_cor="BATCH_GRADE",
        tipo=tipo,
        itens=items or [_item(nr)],
        created_at=datetime.now(UTC) - timedelta(minutes=5),
        aprovado_em=None,
    )


def _version(state: OrdemProdutoState) -> str:
    return calcular_versao_ordem(
        tipo=state.tipo,
        created_at=state.created_at,
        aprovado_em=state.aprovado_em,
        itens=state.itens,
    )


def test_grade_schema_rejeita_order_id_duplicado_antes_do_caso_de_uso():
    with pytest.raises(ValidationError, match="cada pedido pode aparecer"):
        AlterarGradesProdutoRequest.model_validate(
            {
                "changes": [
                    {"orderId": 10, "expectedVersion": "a" * 64, "sizes": {"M": 5}},
                    {"orderId": 10, "expectedVersion": "b" * 64, "sizes": {"G": 5}},
                ]
            }
        )


async def test_write_disputa_mesmo_lock_do_full_sync_em_duas_sessoes():
    async with async_session_factory() as sync_db, async_session_factory() as write_db:
        acquired = await sync_db.scalar(
            select(func.pg_try_advisory_xact_lock(ORDER_STATE_MUTATION_LOCK))
        )
        assert acquired is True

        with pytest.raises(casos_uso.ProcessamentoEmAndamentoError):
            await casos_uso._adquirir_lock_processamento(
                SqlAlchemyPedidosWriteUnitOfWork(write_db)
            )

        await sync_db.rollback()
        await casos_uso._adquirir_lock_processamento(
            SqlAlchemyPedidosWriteUnitOfWork(write_db)
        )
        await write_db.rollback()


def test_grade_schema_aceita_26_mas_rejeita_mais_de_100_clientes():
    def change(order_id: int) -> dict:
        return {
            "orderId": order_id,
            "expectedVersion": "a" * 64,
            "sizes": {"M": 1},
        }

    assert (
        len(
            AlterarGradesProdutoRequest.model_validate(
                {"changes": [change(index) for index in range(1, 27)]}
            ).changes
        )
        == 26
    )
    with pytest.raises(ValidationError):
        AlterarGradesProdutoRequest.model_validate(
            {"changes": [change(index) for index in range(1, 102)]}
        )


def test_grade_schema_rejeita_lote_com_mais_de_100_tamanhos_distintos():
    with pytest.raises(ValidationError, match="no máximo 100 tamanhos"):
        AlterarGradesProdutoRequest.model_validate(
            {
                "changes": [
                    {
                        "orderId": 1,
                        "expectedVersion": "a" * 64,
                        "sizes": {f"T{index}": 1 for index in range(100)},
                    },
                    {
                        "orderId": 2,
                        "expectedVersion": "b" * 64,
                        "sizes": {"EXTRA": 1},
                    },
                ]
            }
        )


def test_grade_schema_rejeita_id_fora_do_integer_postgres_e_versao_invalida():
    with pytest.raises(ValidationError):
        AlterarGradesProdutoRequest.model_validate(
            {
                "changes": [
                    {
                        "orderId": 2_147_483_648,
                        "expectedVersion": "a" * 64,
                        "sizes": {"M": 1},
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        AlterarGradesProdutoRequest.model_validate(
            {
                "changes": [
                    {
                        "orderId": 1,
                        "expectedVersion": "não-é-sha256",
                        "sizes": {"M": 1},
                    }
                ]
            }
        )


def test_grade_dinamica_preserva_total_financeiro_e_tamanhos_numericos():
    original = [
        _item(10, size="36", qty=2, value=40),
        _item(10, size="37", qty=3, value=60),
    ]

    items, total = montar_grade_atualizada(
        nr_pedido=10,
        cd_prod_cor="BATCH_GRADE",
        itens_originais=original,
        sizes={"36": 1, "37": 4},
    )

    assert [(item["sg_tamanho"], item["qt_liquida"]) for item in items] == [
        ("36", 1),
        ("37", 4),
    ]
    assert sum(item["vl_liquido"] for item in items) == 100
    assert total == 100
    with pytest.raises(ValueError, match="preservar a quantidade total"):
        montar_grade_atualizada(
            nr_pedido=10,
            cd_prod_cor="BATCH_GRADE",
            itens_originais=original,
            sizes={"36": 3, "37": 3},
        )


async def test_batch_stale_valida_tudo_antes_de_qualquer_write():
    first = _state(101)
    second = _state(102)
    payload = AlterarGradesProdutoRequest.model_validate(
        {
            "changes": [
                {
                    "orderId": 102,
                    "expectedVersion": "0" * 64,
                    "sizes": {"M": 5},
                },
                {
                    "orderId": 101,
                    "expectedVersion": _version(first),
                    "sizes": {"M": 5},
                },
            ]
        }
    )
    db = AsyncMock()
    write = AsyncMock()
    with (
        patch.object(casos_uso, "_adquirir_lock_processamento", new=AsyncMock()),
        patch.object(
            casos_uso,
            "carregar_ordens_produto_para_update",
            new=AsyncMock(return_value=[first, second]),
        ),
        patch.object(casos_uso, "atualizar_grades_em_lote", new=write),
        pytest.raises(casos_uso.ProdutoBatchConflitoError, match="recarregue"),
    ):
        await casos_uso.executar_alteracao_grades_produto(
            db,
            cd_prod_cor="BATCH_GRADE",
            channel="Franquia",
            payload=cast(AlterarGradesProdutoCommand, payload),
        )
    write.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_batch_rejeita_redistribuicao_para_tamanho_sem_estoque_sem_write():
    state = _state(201)
    payload = AlterarGradesProdutoRequest.model_validate(
        {
            "changes": [
                {
                    "orderId": 201,
                    "expectedVersion": _version(state),
                    "sizes": {"G": 5},
                }
            ]
        }
    )
    db = AsyncMock()
    write = AsyncMock()
    with (
        patch.object(casos_uso, "_adquirir_lock_processamento", new=AsyncMock()),
        patch.object(
            casos_uso,
            "carregar_ordens_produto_para_update",
            new=AsyncMock(return_value=[state]),
        ),
        patch.object(
            casos_uso,
            "carregar_estoque_disponivel_alvos",
            new=AsyncMock(return_value={}),
        ),
        patch.object(casos_uso, "atualizar_grades_em_lote", new=write),
        pytest.raises(
            casos_uso.ProdutoBatchConflitoError, match="Estoque insuficiente"
        ),
    ):
        await casos_uso.executar_alteracao_grades_produto(
            db,
            cd_prod_cor="BATCH_GRADE",
            channel="Franquia",
            payload=cast(AlterarGradesProdutoCommand, payload),
        )
    write.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_batch_redistribui_dentro_da_capacidade_em_uma_transacao():
    state = _state(301)
    payload = AlterarGradesProdutoRequest.model_validate(
        {
            "changes": [
                {
                    "orderId": 301,
                    "expectedVersion": _version(state),
                    "sizes": {"M": 2, "G": 3},
                }
            ]
        }
    )
    db = AsyncMock()
    expected_pairs = ((301, "BATCH_GRADE"),)
    with (
        patch.object(casos_uso, "_adquirir_lock_processamento", new=AsyncMock()),
        patch.object(
            casos_uso,
            "carregar_ordens_produto_para_update",
            new=AsyncMock(return_value=[state]),
        ),
        patch.object(
            casos_uso,
            "carregar_estoque_disponivel_alvos",
            new=AsyncMock(
                return_value={
                    ("BATCH_GRADE", "M", "Franquia"): 0,
                    ("BATCH_GRADE", "G", "Franquia"): 3,
                }
            ),
        ),
        patch.object(
            casos_uso,
            "carregar_referencia_posicoes",
            new=AsyncMock(return_value={"BATCH_GRADE": {"M": 1, "G": 2}}),
        ),
        patch.object(
            casos_uso,
            "atualizar_grades_em_lote",
            new=AsyncMock(return_value=expected_pairs),
        ) as update_batch,
        patch.object(casos_uso, "upsert_modificacoes_em_lote", new=AsyncMock()),
        patch.object(casos_uso, "recalcular_estoque_virtual_alvos", new=AsyncMock()),
        patch.object(casos_uso, "salvar_linhas_linx", new=AsyncMock()),
        patch.object(
            casos_uso,
            "totais_produto_em_edicao",
            new=AsyncMock(return_value=(5, 100)),
        ),
        patch.object(casos_uso, "_snapshot_alertas", new=AsyncMock(return_value=set())),
        patch.object(casos_uso, "_registrar_novos_alertas", new=AsyncMock()),
        patch.object(casos_uso, "record_event", new=AsyncMock()),
    ):
        result = await casos_uso.executar_alteracao_grades_produto(
            db,
            cd_prod_cor="BATCH_GRADE",
            channel="Franquia",
            payload=cast(AlterarGradesProdutoCommand, payload),
        )

    assert result == {
        "status": "success",
        "updated_count": 1,
        "total_qty": 5,
        "total_value": 100.0,
        "approved_count": 0,
    }
    update_batch.assert_awaited_once()
    assert update_batch.await_args is not None
    sent_items = update_batch.await_args.args[1][0]["itens"]
    assert {item["sg_tamanho"]: item["qt_liquida"] for item in sent_items} == {
        "G": 3,
        "M": 2,
    }
    db.commit.assert_awaited_once()


async def test_batch_retry_sem_mudanca_retorna_zero_sem_write_evento_ou_commit():
    state = _state(302)
    payload = AlterarGradesProdutoRequest.model_validate(
        {
            "changes": [
                {
                    "orderId": 302,
                    "expectedVersion": _version(state),
                    "sizes": {"M": 5},
                }
            ]
        }
    )
    db = AsyncMock()
    update = AsyncMock()
    upsert = AsyncMock()
    recalculate = AsyncMock()
    save_linx = AsyncMock()
    event = AsyncMock()
    snapshot = AsyncMock()
    with (
        patch.object(casos_uso, "_adquirir_lock_processamento", new=AsyncMock()),
        patch.object(
            casos_uso,
            "carregar_ordens_produto_para_update",
            new=AsyncMock(return_value=[state]),
        ),
        patch.object(
            casos_uso,
            "carregar_estoque_disponivel_alvos",
            new=AsyncMock(
                return_value={
                    ("BATCH_GRADE", "M", "Franquia"): 0,
                }
            ),
        ),
        patch.object(
            casos_uso,
            "carregar_referencia_posicoes",
            new=AsyncMock(return_value={}),
        ),
        patch.object(casos_uso, "atualizar_grades_em_lote", new=update),
        patch.object(casos_uso, "upsert_modificacoes_em_lote", new=upsert),
        patch.object(casos_uso, "recalcular_estoque_virtual_alvos", new=recalculate),
        patch.object(casos_uso, "salvar_linhas_linx", new=save_linx),
        patch.object(
            casos_uso,
            "totais_produto_em_edicao",
            new=AsyncMock(return_value=(5, 100)),
        ),
        patch.object(casos_uso, "_snapshot_alertas", new=snapshot),
        patch.object(casos_uso, "record_event", new=event),
    ):
        result = await casos_uso.executar_alteracao_grades_produto(
            db,
            cd_prod_cor="BATCH_GRADE",
            channel="Franquia",
            payload=cast(AlterarGradesProdutoCommand, payload),
        )

    assert result == {
        "status": "success",
        "updated_count": 0,
        "total_qty": 5,
        "total_value": 100.0,
    }
    update.assert_not_awaited()
    upsert.assert_not_awaited()
    recalculate.assert_not_awaited()
    save_linx.assert_not_awaited()
    snapshot.assert_not_awaited()
    event.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_grade_reconcilia_alertas_completos_somente_dos_pedidos_afetados():
    uow = AsyncMock()
    uow.observe_active_entity_keys_scoped.return_value = ["303:warning"]
    before = {(301, "warning"), (302, "error")}
    product_after = {(301, "error"), (303, "warning")}
    full_after = {
        (301, "error"),
        (302, "warning"),  # outro produto do mesmo pedido ainda o bloqueia
        (303, "warning"),
    }
    snapshots = AsyncMock(side_effect=[product_after, full_after])

    with patch.object(casos_uso, "_snapshot_alertas", new=snapshots):
        await casos_uso._registrar_novos_alertas(
            uow,
            before,
            reason="grade_changed",
            channel="Franquia",
            cd_prod_cor="BATCH_GRADE",
            affected_order_ids={304},
        )

    assert snapshots.await_args_list[0].kwargs == {
        "cd_prod_cor": "BATCH_GRADE",
        "channel": "Franquia",
    }
    assert snapshots.await_args_list[1].kwargs == {
        "order_ids": {301, 302, 303, 304},
    }
    uow.observe_active_entity_keys_scoped.assert_awaited_once()
    call = uow.observe_active_entity_keys_scoped.await_args
    assert call.args[0] == "alerts"
    assert set(call.kwargs["scope_prefixes"]) == {301, 302, 303, 304}
    assert set(call.args[1]) == {
        "301:error",
        "302:warning",
        "303:warning",
    }
    uow.record_event.assert_awaited_once_with(
        topic="alerts",
        event_type="alerts.new.v1",
        payload={"reason": "grade_changed", "count": 1, "channel": "Franquia"},
    )


async def test_estoque_targeted_faz_um_upsert_e_preserva_chaves_fora_do_lote():
    code = "TARGETED_STOCK"
    other_code = "TARGETED_OTHER"
    nr = 9_901_777
    today = datetime.now(UTC).date()

    class CaptureDb:
        def __init__(self, db) -> None:
            self.db = db
            self.statements: list[str] = []

        async def execute(self, statement, params=None):
            self.statements.append(str(statement))
            return await self.db.execute(statement, params or {})

    async with async_session_factory() as db:
        db.add_all(
            [
                Estoque(
                    cd_prod_cor=code,
                    sg_tamanho="M",
                    canal="Franquia",
                    qt_disponivel=10,
                    dt_estoque=today,
                ),
                Estoque(
                    cd_prod_cor=other_code,
                    sg_tamanho="M",
                    canal="Franquia",
                    qt_disponivel=999,
                    dt_estoque=today,
                ),
                EstoqueVirtual(
                    cd_prod_cor=code,
                    sg_tamanho="M",
                    canal="Franquia",
                    qt_disponivel=10,
                    dt_estoque=today,
                ),
                EstoqueVirtual(
                    cd_prod_cor=other_code,
                    sg_tamanho="M",
                    canal="Franquia",
                    qt_disponivel=777,
                    dt_estoque=today,
                ),
                OrdemReserva(
                    nr_pedido=nr,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=[_item(nr, code=code, size="M", qty=2, value=20)],
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        await db.commit()
        try:
            targets = {(code, "M", "Franquia")}
            assert await carregar_estoque_disponivel_alvos(db, targets) == {
                (code, "M", "Franquia"): 8,
            }

            capture = CaptureDb(db)
            assert await recalcular_estoque_virtual_alvos(
                cast(AsyncSession, capture), targets
            ) == {
                (code, "M", "Franquia"): 8,
            }
            assert len(capture.statements) == 1
            assert "DELETE FROM estoque_virtual" not in capture.statements[0]
            assert (
                await db.scalar(
                    select(EstoqueVirtual.qt_disponivel).where(
                        EstoqueVirtual.cd_prod_cor == other_code,
                        EstoqueVirtual.sg_tamanho == "M",
                        EstoqueVirtual.canal == "Franquia",
                    )
                )
                == 777
            )
            await db.rollback()
        finally:
            await db.execute(delete(OrdemReserva).where(OrdemReserva.nr_pedido == nr))
            await db.execute(
                delete(EstoqueVirtual).where(
                    EstoqueVirtual.cd_prod_cor.in_([code, other_code])
                )
            )
            await db.execute(
                delete(Estoque).where(Estoque.cd_prod_cor.in_([code, other_code]))
            )
            await db.commit()


async def test_batch_falha_linx_e_rollback_preserva_or_original():
    code = "BATCH_ROLLBACK"
    nr = 9_901_401
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=5, value=100)]
    state_version = calcular_versao_ordem(
        tipo="com",
        created_at=created_at,
        aprovado_em=None,
        itens=original,
    )
    payload = AlterarGradesProdutoRequest.model_validate(
        {
            "changes": [
                {
                    "orderId": nr,
                    "expectedVersion": state_version,
                    "sizes": {"M": 2, "G": 3},
                }
            ]
        }
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="com",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            with (
                patch.object(
                    casos_uso, "_adquirir_lock_processamento", new=AsyncMock()
                ),
                patch.object(
                    casos_uso,
                    "carregar_estoque_disponivel_alvos",
                    new=AsyncMock(
                        return_value={
                            (code, "M", "Franquia"): 0,
                            (code, "G", "Franquia"): 3,
                        }
                    ),
                ),
                patch.object(
                    casos_uso,
                    "carregar_referencia_posicoes",
                    new=AsyncMock(return_value={code: {"M": 1, "G": 2}}),
                ),
                patch.object(
                    casos_uso, "recalcular_estoque_virtual_alvos", new=AsyncMock()
                ),
                patch.object(
                    casos_uso,
                    "salvar_linhas_linx",
                    new=AsyncMock(side_effect=RuntimeError("Linx indisponível")),
                ),
                patch.object(
                    casos_uso,
                    "_snapshot_alertas",
                    new=AsyncMock(return_value=set()),
                ),
                pytest.raises(RuntimeError, match="Linx indisponível"),
            ):
                await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload),
                )
            await db.rollback()

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr,
                    OrdemReserva.cd_prod_cor == code,
                )
            )
            modification = await db.scalar(
                select(PedidoModificacao).where(
                    PedidoModificacao.nr_pedido == nr,
                    PedidoModificacao.cd_prod_cor == code,
                )
            )
            assert persisted is not None
            assert cast(bool, persisted.itens == original)
            assert modification is None
        finally:
            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr,
                    OrdemReserva.cd_prod_cor == code,
                )
            )
            if persisted is not None:
                await db.delete(persisted)
            await db.commit()


async def test_aprovacao_use_case_retry_e_expirado_nao_reemitem_eventos():
    db = AsyncMock()
    first = ResultadoAprovacaoProduto(
        matched_count=2,
        approved_pairs=((501, "BATCH_APPROVE"),),
        already_approved_count=0,
        expired_count=1,
    )
    retry = ResultadoAprovacaoProduto(
        matched_count=2,
        approved_pairs=(),
        already_approved_count=1,
        expired_count=1,
    )
    approve = AsyncMock(side_effect=[first, retry])
    observe = AsyncMock()
    event = AsyncMock()
    with (
        patch.object(casos_uso, "_adquirir_lock_processamento", new=AsyncMock()),
        patch.object(casos_uso, "aprovar_produto_canal", new=approve),
        patch.object(casos_uso, "observe_entity_keys", new=observe),
        patch.object(casos_uso, "record_event", new=event),
    ):
        first_result = await casos_uso.executar_aprovacao_produto(
            db,
            cd_prod_cor="BATCH_APPROVE",
            channel="Franquia",
        )
        retry_result = await casos_uso.executar_aprovacao_produto(
            db,
            cd_prod_cor="BATCH_APPROVE",
            channel="Franquia",
        )

    assert first_result["approved_count"] == 1
    assert retry_result["approved_count"] == 0
    observe.assert_awaited_once()
    assert event.await_count == 2
    db.commit.assert_awaited_once()


async def test_write_channel_lookup_falha_fechado_para_canal_ausente():
    now = datetime.now(UTC)
    code = "CHANNEL_WRITE_GUARD"
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=9_902_001,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=[_item(9_902_001, code=code, channel="Franquia")],
                    created_at=now,
                ),
                OrdemReserva(
                    nr_pedido=9_902_002,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=[_item(9_902_002, code=code, channel="Multimarca")],
                    created_at=now,
                ),
                OrdemReserva(
                    nr_pedido=9_902_003,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=[_item(9_902_003, code=code, channel=None)],
                    created_at=now,
                ),
                OrdemReserva(
                    nr_pedido=9_902_004,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=[_item(9_902_004, code=code, channel="Outro")],
                    created_at=now,
                ),
            ]
        )
        await db.flush()

        franquia = await carregar_ordens_produto_para_update(
            db, cd_prod_cor=code, channel="Franquia"
        )
        multimarca = await carregar_ordens_produto_para_update(
            db, cd_prod_cor=code, channel="Multimarca"
        )

        assert [state.nr_pedido for state in franquia] == [9_902_001]
        assert [state.nr_pedido for state in multimarca] == [9_902_002]
        await db.rollback()


async def test_bulk_repository_atualiza_grade_e_modificacao_com_readback():
    now = datetime.now(UTC)
    code = "BATCH_REPO_READBACK"
    nr = 9_902_101
    original = [_item(nr, code=code, size="36", qty=2, value=40)]
    changed = [_item(nr, code=code, size="37", qty=2, value=40)]
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="com",
                itens=original,
                created_at=now,
            )
        )
        await db.flush()

        pairs = await atualizar_grades_em_lote(
            db,
            [
                {
                    "nr_pedido": nr,
                    "cd_prod_cor": code,
                    "itens": changed,
                }
            ],
        )
        await upsert_modificacoes_em_lote(
            db,
            [
                {
                    "nr_pedido": nr,
                    "cd_prod_cor": code,
                    "items": changed,
                    "original_items": original,
                }
            ],
        )
        order = await db.scalar(
            select(OrdemReserva).where(
                OrdemReserva.nr_pedido == nr,
                OrdemReserva.cd_prod_cor == code,
            )
        )
        modification = await db.scalar(
            select(PedidoModificacao).where(
                PedidoModificacao.nr_pedido == nr,
                PedidoModificacao.cd_prod_cor == code,
            )
        )

        assert pairs == ((nr, code),)
        assert order is not None
        assert cast(bool, order.itens == changed)
        assert modification is not None
        assert cast(bool, modification.items == changed)
        assert cast(bool, modification.original_items == original)
        await db.rollback()


async def test_detail_edicao_expoe_mesma_versao_usada_pelo_write():
    now = datetime.now(UTC) - timedelta(minutes=10)
    code = "VERSION_DETAIL"
    nr = 9_902_201
    items = [
        _item(nr, code=code, size="36", qty=2, value=40),
        _item(nr, code=code, size="37", qty=3, value=60),
    ]
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="com",
                itens=items,
                created_at=now,
            )
        )
        await db.flush()
        result = await SqlPedidosReadRepository(db).listar_clientes_produto(
            ConsultaClientesProduto(
                code,
                "edicao",
                "Franquia",
                "Todos",
                25,
            )
        )

        assert result["total"] == 1
        assert result["rows"][0]["item"]["sizes"] == {"36": 2, "37": 3}
        assert result["rows"][0]["version"] == calcular_versao_ordem(
            tipo="com",
            created_at=now,
            aprovado_em=None,
            itens=items,
        )
        await db.rollback()


async def test_detail_edicao_expoe_grade_original_da_modificacao():
    now = datetime.now(UTC) - timedelta(minutes=10)
    code = "ORIGINAL_DETAIL"
    nr = 9_902_202
    original = [_item(nr, code=code, size="36", qty=5, value=100)]
    current = [
        _item(nr, code=code, size="36", qty=2, value=40),
        _item(nr, code=code, size="37", qty=3, value=60),
    ]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=current,
                    created_at=now,
                ),
                PedidoModificacao(
                    nr_pedido=nr,
                    cd_prod_cor=code,
                    items=current,
                    original_items=original,
                ),
            ]
        )
        await db.flush()

        result = await SqlPedidosReadRepository(db).listar_clientes_produto(
            ConsultaClientesProduto(code, "edicao", "Franquia", "Todos", 25)
        )

        order = result["rows"][0]["order"]
        assert order["pedido_alterado"] is True
        assert order["items"][0]["sizes"] == {"36": 2, "37": 3}
        assert order["original_items"][0]["sizes"] == {"36": 5}
        await db.rollback()


async def test_aprovacao_produto_e_idempotente_e_nao_cruza_canal():
    now = datetime.now(UTC)
    code = "BATCH_APPROVAL"
    rows = [
        OrdemReserva(
            nr_pedido=9_903_001,
            cd_prod_cor=code,
            tipo="com",
            itens=[_item(9_903_001, code=code, channel="Franquia")],
            created_at=now - timedelta(hours=1),
        ),
        OrdemReserva(
            nr_pedido=9_903_002,
            cd_prod_cor=code,
            tipo="com",
            itens=[_item(9_903_002, code=code, channel="Franquia")],
            created_at=now - timedelta(hours=25),
        ),
        OrdemReserva(
            nr_pedido=9_903_003,
            cd_prod_cor=code,
            tipo="com",
            itens=[_item(9_903_003, code=code, channel="Franquia")],
            created_at=now - timedelta(hours=2),
            aprovado_em=now - timedelta(minutes=10),
        ),
        OrdemReserva(
            nr_pedido=9_903_004,
            cd_prod_cor=code,
            tipo="com",
            itens=[_item(9_903_004, code=code, channel="Multimarca")],
            created_at=now - timedelta(hours=1),
        ),
        OrdemReserva(
            nr_pedido=9_903_005,
            cd_prod_cor=code,
            tipo="com",
            itens=[
                _item(9_903_005, code=code, channel="Franquia", size="M"),
                _item(9_903_005, code=code, channel="Multimarca", size="G"),
            ],
            created_at=now - timedelta(hours=1),
        ),
    ]
    async with async_session_factory() as db:
        db.add_all(rows)
        await db.flush()
        limit = now - timedelta(hours=24)

        first = await aprovar_produto_canal(
            db,
            cd_prod_cor=code,
            channel="Franquia",
            limite_criacao=limit,
        )
        second = await aprovar_produto_canal(
            db,
            cd_prod_cor=code,
            channel="Franquia",
            limite_criacao=limit,
        )
        mm_approved = await db.scalar(
            select(OrdemReserva.aprovado_em).where(
                OrdemReserva.nr_pedido == 9_903_004,
                OrdemReserva.cd_prod_cor == code,
            )
        )
        mixed_approved = await db.scalar(
            select(OrdemReserva.aprovado_em).where(
                OrdemReserva.nr_pedido == 9_903_005,
                OrdemReserva.cd_prod_cor == code,
            )
        )

        assert first.matched_count == 3
        assert first.approved_count == 1
        assert first.expired_count == 1
        assert first.already_approved_count == 1
        assert second.approved_count == 0
        assert second.expired_count == 1
        assert second.already_approved_count == 2
        assert mm_approved is None
        assert mixed_approved is None
        await db.rollback()


# ---------------------------------------------------------------------------
# Phase 20 Plan 03 — trava de orçamento ±5% na edição manual de "sem
# adequação" (GRADE-03). Os testes abaixo seedam `OrdemReserva` real (sem
# linha em `pedidos`): o par vira a única fonte de `total_original` via o
# ramo `missing_from_pedidos` de `PEDIDO_BUDGET_SQL` — o próprio
# `qt_solicitada` do par é a linha de base, então ela permanece estável ao
# longo de edições sucessivas (D-06/plano 20-02), o que é exatamente a
# propriedade que estes testes precisam para provar acúmulo sem reintroduzir
# consulta a `pedidos`.
# ---------------------------------------------------------------------------


@contextmanager
def _patches_edicao_grade(
    *, estoque: dict[tuple[str, str, str], int], referencia: dict[str, dict[str, int]]
):
    """Patches comuns aos testes de orçamento: só a orquestração de
    estoque/Linx/alertas é mockada — leitura de ORs, orçamento, tolerância,
    a fusão salvar+aprovar (plano 20-05) e a escrita da grade em si
    (`atualizar_grades_em_lote`, `upsert_modificacoes_em_lote`) rodam contra
    o banco de teste de verdade. Cede o mock de `record_event` para quem
    precisar afirmar sobre os eventos emitidos (ex.: aprovação fundida)."""
    record_event = AsyncMock()
    with (
        patch.object(casos_uso, "_adquirir_lock_processamento", new=AsyncMock()),
        patch.object(
            casos_uso,
            "carregar_estoque_disponivel_alvos",
            new=AsyncMock(return_value=estoque),
        ),
        patch.object(
            casos_uso,
            "carregar_referencia_posicoes",
            new=AsyncMock(return_value=referencia),
        ),
        patch.object(casos_uso, "recalcular_estoque_virtual_alvos", new=AsyncMock()),
        patch.object(casos_uso, "salvar_linhas_linx", new=AsyncMock()),
        patch.object(casos_uso, "_snapshot_alertas", new=AsyncMock(return_value=set())),
        patch.object(casos_uso, "_registrar_novos_alertas", new=AsyncMock()),
        patch.object(casos_uso, "record_event", new=record_event),
    ):
        yield record_event


async def _run_edicao_grade(
    db,
    *,
    code: str,
    changes: list[dict],
    estoque: dict[tuple[str, str, str], int],
    referencia: dict[str, dict[str, int]],
) -> dict:
    payload = AlterarGradesProdutoRequest.model_validate({"changes": changes})
    with _patches_edicao_grade(estoque=estoque, referencia=referencia):
        return await casos_uso.executar_alteracao_grades_produto(
            SqlAlchemyPedidosWriteUnitOfWork(db),
            cd_prod_cor=code,
            channel="Franquia",
            payload=cast(AlterarGradesProdutoCommand, payload),
        )


@contextmanager
def _sem_fusao():
    """Neutraliza a fusão salvar+aprovar (Phase 20 Plan 05) sem mockar o
    resto do caminho de escrita: usado pelos testes de orçamento do plano
    20-03 que precisam reeditar a MESMA OR "sem adequação" ainda aberta
    várias vezes em sequência — algo que a fusão passaria a impedir (a
    primeira edição bem-sucedida dentro do orçamento finalizaria a OR). A
    cobertura da fusão em si vive nos testes `salvar_e_aprovar` abaixo."""
    with (
        patch.object(
            casos_uso,
            "carregar_pares_aprovaveis_pedido_para_update",
            new=AsyncMock(return_value=()),
        ),
        patch.object(
            casos_uso,
            "aprovar_ordem_reserva",
            new=AsyncMock(
                return_value=ResultadoAprovacaoOrdem(encontrados=0, alterados=0)
            ),
        ),
    ):
        yield


async def _limpar_or(db, *, nr_pedido: int, cd_prod_cor: str) -> None:
    persisted = await db.scalar(
        select(OrdemReserva).where(
            OrdemReserva.nr_pedido == nr_pedido,
            OrdemReserva.cd_prod_cor == cd_prod_cor,
        )
    )
    if persisted is not None:
        await db.delete(persisted)
    modification = await db.scalar(
        select(PedidoModificacao).where(
            PedidoModificacao.nr_pedido == nr_pedido,
            PedidoModificacao.cd_prod_cor == cd_prod_cor,
        )
    )
    if modification is not None:
        await db.delete(modification)


async def test_orcamento_sucesso_dentro_do_limite():
    code = "BATCH_ORC_OK"
    nr = 9_902_001
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            # Teto exato de adição: floor(100 * 0.05) = 5 -> 100 + 5 = 105.
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[{"orderId": nr, "expectedVersion": version, "sizes": {"M": 105}}],
                estoque={(code, "M", "Franquia"): 1000},
                referencia={code: {"M": 1}},
            )
            assert result["status"] == "success"
            assert result["updated_count"] == 1

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert sum(item["qt_liquida"] for item in persisted.itens) == 105
            # Elo com o plano 20-02: a linha de base do par gravada continua
            # igual à original, não à quantidade nova.
            assert sum(item["qt_solicitada"] for item in persisted.itens) == 100
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_estouro_adicao_uma_peca():
    code = "BATCH_ORC_ADD"
    nr = 9_902_002
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            with pytest.raises(OrcamentoPedidoExcedidoError) as excinfo:
                await _run_edicao_grade(
                    db,
                    code=code,
                    changes=[
                        {"orderId": nr, "expectedVersion": version, "sizes": {"M": 106}}
                    ],
                    estoque={(code, "M", "Franquia"): 1000},
                    referencia={code: {"M": 1}},
                )
            await db.rollback()
            assert excinfo.value.nr_pedido == nr
            assert excinfo.value.orcamento == "adicao"

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert cast(bool, persisted.itens == original)
            modification = await db.scalar(
                select(PedidoModificacao).where(
                    PedidoModificacao.nr_pedido == nr,
                    PedidoModificacao.cd_prod_cor == code,
                )
            )
            assert modification is None
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_estouro_corte():
    """Simétrico ao teste de adição, com o orçamento de adição intocado —
    prova D-08 (os dois orçamentos não se compensam)."""
    code = "BATCH_ORC_CUT"
    nr = 9_902_003
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            with pytest.raises(OrcamentoPedidoExcedidoError) as excinfo:
                await _run_edicao_grade(
                    db,
                    code=code,
                    changes=[
                        {"orderId": nr, "expectedVersion": version, "sizes": {"M": 94}}
                    ],
                    estoque={(code, "M", "Franquia"): 1000},
                    referencia={code: {"M": 1}},
                )
            await db.rollback()
            assert excinfo.value.nr_pedido == nr
            assert excinfo.value.orcamento == "corte"

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert cast(bool, persisted.itens == original)
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_resave_par_legado_acima_do_limite_nao_bloqueia():
    """Regressão real-DB do CR-01 (20-REVIEW): OR "sem adequação" legada cuja
    `qt_liquida` JÁ desvia de `qt_solicitada` mais do que `floor(total ×
    tolerancia)` permite — o cenário exato deixado para trás pelo bug de SQL
    que este plano corrige (o par nunca tinha sido medido pelo orçamento do
    pedido antes). Um resave puro (mesma grade final) não pode ser
    bloqueado: não introduz nenhum desvio novo, só reafirma o que já
    estava lá."""
    code = "BATCH_ORC_LEGADO"
    nr = 9_902_004
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    # baseline (qt_solicitada) = 100, mas qt_liquida já está em 108 — 8
    # peças de adição, acima do limite de 5 (floor(100 * 0.05)), gravadas
    # antes desta fase existir e nunca antes cobradas contra o orçamento.
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    original[0]["qt_liquida"] = 108
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            # Resave sem mudança: pede exatamente os mesmos 108 já vivos. A
            # grade final é idêntica à persistida, então o próprio caminho de
            # escrita a trata como no-op (updated_count=0) — o que importa
            # para o CR-01 é que `validar_edicao_orcamento`, chamada ANTES
            # desse curto-circuito de no-op, não levanta.
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[{"orderId": nr, "expectedVersion": version, "sizes": {"M": 108}}],
                estoque={(code, "M", "Franquia"): 1000},
                referencia={code: {"M": 1}},
            )
            assert result["status"] == "success"
            assert result["updated_count"] == 0

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert sum(item["qt_liquida"] for item in persisted.itens) == 108
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_sucessivo_edicoes_acumulam():
    """O teste crítico da fase (D-06/PD-02): expõe o double-spend que só
    aparece na SEGUNDA edição de um mesmo pedido. Se a primeira edição
    "esquecer" o consumo (não rebobinar a contribuição atual antes de
    recobrar), o passo 2 abaixo passaria em vez de recusar. NÃO afrouxe essa
    asserção "corrigindo-a" para aceitar — é exatamente o falso positivo que
    este teste existe para pegar."""
    code = "BATCH_ORC_SUC"
    nr = 9_902_004
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    estoque = {(code, "M", "Franquia"): 1000}
    referencia = {code: {"M": 1}}
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            # Passo 1: consome 3 de um limite de adição de 5 -> sucesso.
            version1 = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original
            )
            payload1 = AlterarGradesProdutoRequest.model_validate(
                {
                    "changes": [
                        {"orderId": nr, "expectedVersion": version1, "sizes": {"M": 103}}
                    ]
                }
            )
            with (
                _patches_edicao_grade(estoque=estoque, referencia=referencia),
                _sem_fusao(),
            ):
                result1 = await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload1),
                )
            assert result1["status"] == "success"

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert sum(item["qt_solicitada"] for item in persisted.itens) == 100

            # Passo 2: tenta consumir além do acumulado (100 -> 106, 3 já
            # gastos + 6 novos = 9 > limite 5) -> recusa.
            version2 = calcular_versao_ordem(
                tipo="sem",
                created_at=persisted.created_at,
                aprovado_em=None,
                itens=persisted.itens,
            )
            payload2 = AlterarGradesProdutoRequest.model_validate(
                {
                    "changes": [
                        {"orderId": nr, "expectedVersion": version2, "sizes": {"M": 106}}
                    ]
                }
            )
            with (
                _patches_edicao_grade(estoque=estoque, referencia=referencia),
                _sem_fusao(),
                pytest.raises(OrcamentoPedidoExcedidoError),
            ):
                await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload2),
                )
            await db.rollback()

            # Passo 3: fica dentro do acumulado (100 -> 105, exatamente o
            # limite de 5) -> sucesso.
            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            version3 = calcular_versao_ordem(
                tipo="sem",
                created_at=persisted.created_at,
                aprovado_em=None,
                itens=persisted.itens,
            )
            payload3 = AlterarGradesProdutoRequest.model_validate(
                {
                    "changes": [
                        {"orderId": nr, "expectedVersion": version3, "sizes": {"M": 105}}
                    ]
                }
            )
            with (
                _patches_edicao_grade(estoque=estoque, referencia=referencia),
                _sem_fusao(),
            ):
                result3 = await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload3),
                )
            assert result3["status"] == "success"
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_lote_dois_pedidos_um_estourando_recusa_tudo():
    """PD-05: o lote é tudo-ou-nada — um `nr_pedido` estourado recusa a
    chamada inteira, inclusive o outro pedido do mesmo lote que caberia."""
    code = "BATCH_ORC_LOTE"
    nr_ok, nr_estoura = 9_902_005, 9_902_006
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_ok = [_item(nr_ok, code=code, size="M", qty=100, value=1000)]
    original_estoura = [_item(nr_estoura, code=code, size="M", qty=100, value=1000)]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr_ok,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_ok,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr_estoura,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_estoura,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version_ok = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_ok
            )
            version_estoura = calcular_versao_ordem(
                tipo="sem",
                created_at=created_at,
                aprovado_em=None,
                itens=original_estoura,
            )
            with pytest.raises(OrcamentoPedidoExcedidoError) as excinfo:
                await _run_edicao_grade(
                    db,
                    code=code,
                    changes=[
                        {
                            "orderId": nr_ok,
                            "expectedVersion": version_ok,
                            "sizes": {"M": 103},
                        },
                        {
                            "orderId": nr_estoura,
                            "expectedVersion": version_estoura,
                            "sizes": {"M": 108},
                        },
                    ],
                    estoque={(code, "M", "Franquia"): 10_000},
                    referencia={code: {"M": 1}},
                )
            await db.rollback()
            assert excinfo.value.nr_pedido == nr_estoura

            for nr, original in ((nr_ok, original_ok), (nr_estoura, original_estoura)):
                persisted = await db.scalar(
                    select(OrdemReserva).where(
                        OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                    )
                )
                assert persisted is not None
                assert cast(bool, persisted.itens == original)
        finally:
            await _limpar_or(db, nr_pedido=nr_ok, cd_prod_cor=code)
            await _limpar_or(db, nr_pedido=nr_estoura, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_pedidos_independentes_por_lote():
    """PD-06: cada `nr_pedido` do lote tem seu próprio orçamento. Se a
    implementação agregasse o lote num único ledger, este caso (dois
    pedidos, cada um no teto do seu próprio orçamento de 5) seria recusado
    — aqui os dois cabem porque não são somados entre si."""
    code = "BATCH_ORC_INDEP"
    nr_a, nr_b = 9_902_007, 9_902_008
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_a = [_item(nr_a, code=code, size="M", qty=100, value=1000)]
    original_b = [_item(nr_b, code=code, size="M", qty=100, value=1000)]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr_a,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_a,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr_b,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_b,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version_a = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_a
            )
            version_b = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_b
            )
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {"orderId": nr_a, "expectedVersion": version_a, "sizes": {"M": 105}},
                    {"orderId": nr_b, "expectedVersion": version_b, "sizes": {"M": 105}},
                ],
                estoque={(code, "M", "Franquia"): 10_000},
                referencia={code: {"M": 1}},
            )
            assert result["status"] == "success"
            assert result["updated_count"] == 2
        finally:
            await _limpar_or(db, nr_pedido=nr_a, cd_prod_cor=code)
            await _limpar_or(db, nr_pedido=nr_b, cd_prod_cor=code)
            await db.commit()


async def test_orcamento_corrida_entre_sessoes_bloqueia_no_lock():
    """T-20-10/D-07: a leitura do orçamento só pode acontecer depois do lock
    global e dentro da mesma transação. Seguindo o molde de
    `test_write_disputa_mesmo_lock_do_full_sync_em_duas_sessoes`: enquanto
    uma sessão detém o advisory lock, uma segunda sessão tentando editar
    (produto potencialmente diferente do mesmo pedido — o lock é global, não
    por produto) precisa falhar no lock, nunca chegar a ler o orçamento em
    paralelo com uma escrita não commitada."""
    code = "BATCH_ORC_LOCK"
    nr = 9_902_009
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as sync_db, async_session_factory() as write_db:
        write_db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await write_db.commit()
        try:
            acquired = await sync_db.scalar(
                select(func.pg_try_advisory_xact_lock(ORDER_STATE_MUTATION_LOCK))
            )
            assert acquired is True

            payload = AlterarGradesProdutoRequest.model_validate(
                {
                    "changes": [
                        {"orderId": nr, "expectedVersion": version, "sizes": {"M": 103}}
                    ]
                }
            )
            with pytest.raises(casos_uso.ProcessamentoEmAndamentoError):
                await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(write_db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload),
                )
            await sync_db.rollback()
            await write_db.rollback()
        finally:
            async with async_session_factory() as cleanup:
                await _limpar_or(cleanup, nr_pedido=nr, cd_prod_cor=code)
                await cleanup.commit()


async def test_tipo_com_redistribuicao_e_divergencia_sem_mudanca_de_comportamento():
    """Regressão D-01/D-11: a trava de orçamento não pode mudar em nada o
    caminho "com adequação" — nem no resultado, nem no número de queries
    (a leitura de orçamento é pulada porque não há par 'sem' no lote)."""
    code = "BATCH_ORC_COM"
    nr_divergente, nr_redistribui = 9_902_010, 9_902_011
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_divergente = [_item(nr_divergente, code=code, size="M", qty=10, value=100)]
    original_redistribui = [
        _item(nr_redistribui, code=code, size="M", qty=6, value=60),
        _item(nr_redistribui, code=code, size="G", qty=4, value=40),
    ]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr_divergente,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=original_divergente,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr_redistribui,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=original_redistribui,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version_divergente = calcular_versao_ordem(
                tipo="com",
                created_at=created_at,
                aprovado_em=None,
                itens=original_divergente,
            )
            with pytest.raises(
                casos_uso.ProdutoBatchConflitoError, match="redistribui"
            ):
                await _run_edicao_grade(
                    db,
                    code=code,
                    changes=[
                        {
                            "orderId": nr_divergente,
                            "expectedVersion": version_divergente,
                            "sizes": {"M": 12},
                        }
                    ],
                    estoque={(code, "M", "Franquia"): 1000},
                    referencia={code: {"M": 1}},
                )
            await db.rollback()

            version_redistribui = calcular_versao_ordem(
                tipo="com",
                created_at=created_at,
                aprovado_em=None,
                itens=original_redistribui,
            )
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {
                        "orderId": nr_redistribui,
                        "expectedVersion": version_redistribui,
                        "sizes": {"M": 8, "G": 2},
                    }
                ],
                estoque={
                    (code, "M", "Franquia"): 1000,
                    (code, "G", "Franquia"): 1000,
                },
                referencia={code: {"M": 1, "G": 2}},
            )
            assert result["status"] == "success"
        finally:
            await _limpar_or(db, nr_pedido=nr_divergente, cd_prod_cor=code)
            await _limpar_or(db, nr_pedido=nr_redistribui, cd_prod_cor=code)
            await db.commit()


# ---------------------------------------------------------------------------
# Phase 20 Plan 05 — fusão salvar+aprovar para OR "sem adequação" dentro do
# orçamento (D-09/PD-10/PD-11/PD-12). Reaproveita `_patches_edicao_grade`/
# `_run_edicao_grade`/`_limpar_or` do plano 20-03 (seção acima); nenhum
# desses testes usa `_sem_fusao()` — o ponto aqui é justamente exercitar a
# aprovação fundida de verdade contra o banco de teste.
# ---------------------------------------------------------------------------


async def test_salvar_e_aprovar_caminho_feliz_aprova_e_finaliza_no_mesmo_commit():
    code = "BATCH_FUSAO_OK"
    nr = 9_905_001
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            payload = AlterarGradesProdutoRequest.model_validate(
                {
                    "changes": [
                        {"orderId": nr, "expectedVersion": version, "sizes": {"M": 103}}
                    ]
                }
            )
            with _patches_edicao_grade(
                estoque={(code, "M", "Franquia"): 10_000},
                referencia={code: {"M": 1}},
            ) as record_event:
                result = await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload),
                )

            assert result["status"] == "success"
            assert result["approved_count"] == 1

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert sum(item["qt_liquida"] for item in persisted.itens) == 103
            assert persisted.aprovado_em is not None

            event_types = [
                call.kwargs.get("event_type") for call in record_event.call_args_list
            ]
            assert "orders.approved.v1" in event_types
            assert "history.changed.v1" in event_types
            approved_call = next(
                call
                for call in record_event.call_args_list
                if call.kwargs.get("event_type") == "orders.approved.v1"
            )
            assert approved_call.kwargs["payload"]["source"] == "grade_save"
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_escopo_nao_vaza_para_pedido_nao_editado():
    """PD-10: aprovar um lote não pode aprovar outra OR do mesmo
    produto/canal que não estava no lote editado (Pitfall 4 do 20-RESEARCH)."""
    code = "BATCH_FUSAO_ESCOPO"
    nr_editado, nr_intocado = 9_905_002, 9_905_003
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_editado = [_item(nr_editado, code=code, size="M", qty=100, value=1000)]
    original_intocado = [_item(nr_intocado, code=code, size="M", qty=100, value=1000)]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr_editado,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_editado,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr_intocado,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_intocado,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_editado
            )
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {"orderId": nr_editado, "expectedVersion": version, "sizes": {"M": 103}}
                ],
                estoque={(code, "M", "Franquia"): 10_000},
                referencia={code: {"M": 1}},
            )
            assert result["approved_count"] == 1

            editado = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr_editado, OrdemReserva.cd_prod_cor == code
                )
            )
            intocado = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr_intocado, OrdemReserva.cd_prod_cor == code
                )
            )
            assert editado is not None and editado.aprovado_em is not None
            assert intocado is not None
            assert intocado.aprovado_em is None
            assert cast(bool, intocado.itens == original_intocado)
        finally:
            await _limpar_or(db, nr_pedido=nr_editado, cd_prod_cor=code)
            await _limpar_or(db, nr_pedido=nr_intocado, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_escopo_nao_vaza_para_outro_produto_do_pedido():
    """PD-10: a restrição por `cd_prod_cor` na carga de pares aprováveis
    impede que outro produto do MESMO pedido seja finalizado de carona."""
    nr = 9_905_004
    code_a, code_b = "BATCH_FUSAO_PA", "BATCH_FUSAO_PB"
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_a = [_item(nr, code=code_a, size="M", qty=100, value=1000)]
    original_b = [_item(nr, code=code_b, size="M", qty=100, value=1000)]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr,
                    cd_prod_cor=code_a,
                    tipo="sem",
                    itens=original_a,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr,
                    cd_prod_cor=code_b,
                    tipo="sem",
                    itens=original_b,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version_a = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_a
            )
            result = await _run_edicao_grade(
                db,
                code=code_a,
                changes=[
                    {"orderId": nr, "expectedVersion": version_a, "sizes": {"M": 103}}
                ],
                estoque={(code_a, "M", "Franquia"): 10_000},
                referencia={code_a: {"M": 1}},
            )
            assert result["approved_count"] == 1

            produto_a = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code_a
                )
            )
            produto_b = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code_b
                )
            )
            assert produto_a is not None and produto_a.aprovado_em is not None
            assert produto_b is not None
            assert produto_b.aprovado_em is None
            assert cast(bool, produto_b.itens == original_b)
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code_a)
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code_b)
            await db.commit()


async def test_salvar_e_aprovar_com_adequacao_nao_e_aprovada():
    """D-11: redistribuição "com adequação" válida grava a grade mas nunca
    finaliza a OR — os dois botões continuam separados."""
    code = "BATCH_FUSAO_COM"
    nr = 9_905_005
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [
        _item(nr, code=code, size="M", qty=6, value=60),
        _item(nr, code=code, size="G", qty=4, value=40),
    ]
    version = calcular_versao_ordem(
        tipo="com", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="com",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {
                        "orderId": nr,
                        "expectedVersion": version,
                        "sizes": {"M": 8, "G": 2},
                    }
                ],
                estoque={
                    (code, "M", "Franquia"): 1000,
                    (code, "G", "Franquia"): 1000,
                },
                referencia={code: {"M": 1, "G": 2}},
            )
            assert result["status"] == "success"
            assert result["approved_count"] == 0

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert persisted.aprovado_em is None
            assert {item["sg_tamanho"]: item["qt_liquida"] for item in persisted.itens} == {
                "M": 8,
                "G": 2,
            }
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_lote_misto_nao_aprova_nada():
    """PD-11: um lote com um par 'sem' e um par 'com', ambos válidos, grava
    os dois e não aprova nenhum — casa com o rótulo condicional do
    20-UI-SPEC.md (só troca quando TODAS as linhas alteradas são 'sem')."""
    code = "BATCH_FUSAO_MISTO"
    nr_sem, nr_com = 9_905_006, 9_905_007
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_sem = [_item(nr_sem, code=code, size="M", qty=100, value=1000)]
    original_com = [
        _item(nr_com, code=code, size="M", qty=6, value=60),
        _item(nr_com, code=code, size="G", qty=4, value=40),
    ]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr_sem,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_sem,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr_com,
                    cd_prod_cor=code,
                    tipo="com",
                    itens=original_com,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version_sem = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_sem
            )
            version_com = calcular_versao_ordem(
                tipo="com", created_at=created_at, aprovado_em=None, itens=original_com
            )
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {
                        "orderId": nr_sem,
                        "expectedVersion": version_sem,
                        "sizes": {"M": 103},
                    },
                    {
                        "orderId": nr_com,
                        "expectedVersion": version_com,
                        "sizes": {"M": 8, "G": 2},
                    },
                ],
                estoque={
                    (code, "M", "Franquia"): 10_000,
                    (code, "G", "Franquia"): 10_000,
                },
                referencia={code: {"M": 1, "G": 2}},
            )
            assert result["status"] == "success"
            assert result["updated_count"] == 2
            assert result["approved_count"] == 0

            for nr in (nr_sem, nr_com):
                persisted = await db.scalar(
                    select(OrdemReserva).where(
                        OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                    )
                )
                assert persisted is not None
                assert persisted.aprovado_em is None
        finally:
            await _limpar_or(db, nr_pedido=nr_sem, cd_prod_cor=code)
            await _limpar_or(db, nr_pedido=nr_com, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_dois_pares_sem_aprovam_os_dois():
    code = "BATCH_FUSAO_DOIS"
    nr_1, nr_2 = 9_905_008, 9_905_009
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original_1 = [_item(nr_1, code=code, size="M", qty=100, value=1000)]
    original_2 = [_item(nr_2, code=code, size="M", qty=100, value=1000)]
    async with async_session_factory() as db:
        db.add_all(
            [
                OrdemReserva(
                    nr_pedido=nr_1,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_1,
                    created_at=created_at,
                ),
                OrdemReserva(
                    nr_pedido=nr_2,
                    cd_prod_cor=code,
                    tipo="sem",
                    itens=original_2,
                    created_at=created_at,
                ),
            ]
        )
        await db.commit()
        try:
            version_1 = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_1
            )
            version_2 = calcular_versao_ordem(
                tipo="sem", created_at=created_at, aprovado_em=None, itens=original_2
            )
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {"orderId": nr_1, "expectedVersion": version_1, "sizes": {"M": 103}},
                    {"orderId": nr_2, "expectedVersion": version_2, "sizes": {"M": 103}},
                ],
                estoque={(code, "M", "Franquia"): 10_000},
                referencia={code: {"M": 1}},
            )
            assert result["approved_count"] == 2

            for nr in (nr_1, nr_2):
                persisted = await db.scalar(
                    select(OrdemReserva).where(
                        OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                    )
                )
                assert persisted is not None
                assert persisted.aprovado_em is not None
        finally:
            await _limpar_or(db, nr_pedido=nr_1, cd_prod_cor=code)
            await _limpar_or(db, nr_pedido=nr_2, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_falha_na_aprovacao_desfaz_a_gravacao_da_grade():
    """T-20-24/PD-12: se a etapa de aprovação falhar depois da grade já
    gravada em memória de transação, o `db.rollback()` do teste (que
    substitui o commit único que nunca é alcançado) prova que nada
    sobrevive — grade e aprovação vivem ou morrem juntas."""
    code = "BATCH_FUSAO_ATOMIC"
    nr = 9_905_010
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            payload = AlterarGradesProdutoRequest.model_validate(
                {
                    "changes": [
                        {"orderId": nr, "expectedVersion": version, "sizes": {"M": 103}}
                    ]
                }
            )
            with (
                _patches_edicao_grade(
                    estoque={(code, "M", "Franquia"): 10_000},
                    referencia={code: {"M": 1}},
                ),
                patch.object(
                    casos_uso,
                    "aprovar_ordem_reserva",
                    new=AsyncMock(side_effect=RuntimeError("aprovacao indisponivel")),
                ),
                pytest.raises(RuntimeError, match="aprovacao indisponivel"),
            ):
                await casos_uso.executar_alteracao_grades_produto(
                    SqlAlchemyPedidosWriteUnitOfWork(db),
                    cd_prod_cor=code,
                    channel="Franquia",
                    payload=cast(AlterarGradesProdutoCommand, payload),
                )
            await db.rollback()

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert cast(bool, persisted.itens == original)
            assert persisted.aprovado_em is None
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_edicao_sem_mudanca_nao_aprova():
    """Retorno antecipado de zero alterações (comportamento de hoje):
    nenhuma aprovação, nenhum evento, nenhum commit."""
    code = "BATCH_FUSAO_NOOP"
    nr = 9_905_011
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            result = await _run_edicao_grade(
                db,
                code=code,
                changes=[
                    {"orderId": nr, "expectedVersion": version, "sizes": {"M": 100}}
                ],
                estoque={(code, "M", "Franquia"): 10_000},
                referencia={code: {"M": 1}},
            )
            assert result["updated_count"] == 0
            assert result.get("approved_count", 0) == 0

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert persisted.aprovado_em is None
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()


async def test_salvar_e_aprovar_estouro_de_orcamento_nao_aprova():
    code = "BATCH_FUSAO_ESTOURO"
    nr = 9_905_012
    created_at = datetime.now(UTC) - timedelta(minutes=5)
    original = [_item(nr, code=code, size="M", qty=100, value=1000)]
    version = calcular_versao_ordem(
        tipo="sem", created_at=created_at, aprovado_em=None, itens=original
    )
    async with async_session_factory() as db:
        db.add(
            OrdemReserva(
                nr_pedido=nr,
                cd_prod_cor=code,
                tipo="sem",
                itens=original,
                created_at=created_at,
            )
        )
        await db.commit()
        try:
            with pytest.raises(OrcamentoPedidoExcedidoError):
                await _run_edicao_grade(
                    db,
                    code=code,
                    changes=[
                        {"orderId": nr, "expectedVersion": version, "sizes": {"M": 106}}
                    ],
                    estoque={(code, "M", "Franquia"): 10_000},
                    referencia={code: {"M": 1}},
                )
            await db.rollback()

            persisted = await db.scalar(
                select(OrdemReserva).where(
                    OrdemReserva.nr_pedido == nr, OrdemReserva.cd_prod_cor == code
                )
            )
            assert persisted is not None
            assert cast(bool, persisted.itens == original)
            assert persisted.aprovado_em is None
        finally:
            await _limpar_or(db, nr_pedido=nr, cd_prod_cor=code)
            await db.commit()
