from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.modules.ingestao.application.casos_uso import (
    _registrar_novidades_realtime,
    sincronizar_pedidos,
)


def _realtime(
    *,
    keys: tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]],
    observed: list[list[str]] | None = None,
    active: list[str] | None = None,
) -> MagicMock:
    port = MagicMock()
    port.load_keys = AsyncMock(return_value=keys)
    port.observe_entities = AsyncMock(side_effect=observed or [[], []])
    port.observe_active_entities = AsyncMock(return_value=active or [])
    port.enqueue = AsyncMock()
    return port


@pytest.mark.asyncio
async def test_snapshot_inicial_vira_baseline_sem_emitir_historico() -> None:
    realtime = _realtime(keys=([(101, "A"), (102, "B")], [(101, "warning")], []))

    await _registrar_novidades_realtime(realtime)

    assert realtime.observe_entities.await_args_list == [
        call("orders", {"101:A": 101, "102:B": 102}),
        call("history", set()),
    ]
    realtime.observe_active_entities.assert_awaited_once_with(
        "alerts",
        {"101:warning": (101, "warning")},
        allow_empty_snapshot=True,
    )
    realtime.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_novidades_entram_no_outbox_sem_commit_proprio() -> None:
    realtime = _realtime(
        keys=(
            [(101, "A"), (102, "B")],
            [(102, "error"), (101, "warning")],
            [(900, "ERP-A")],
        ),
        observed=[["102:B"], ["900:ERP-A"]],
        active=["102:error"],
    )

    await _registrar_novidades_realtime(realtime)

    assert realtime.enqueue.await_args_list == [
        call(
            topic="orders",
            event_type="orders.changed.v1",
            payload={
                "newOrderCount": 1,
                "newProductCount": 1,
                "reason": "databricks_sync",
            },
        ),
        call(
            topic="alerts",
            event_type="alerts.changed.v1",
            payload={"newAlertCount": 1, "reason": "databricks_sync"},
        ),
        call(
            topic="history",
            event_type="history.changed",
            payload={"count": 1, "reason": "erp_sync"},
        ),
    ]


@pytest.mark.asyncio
async def test_snapshot_de_pedidos_vazio_nao_desativa_todos_os_alertas() -> None:
    realtime = _realtime(keys=([], [], []))

    await _registrar_novidades_realtime(
        realtime,
        orders_snapshot_complete=False,
    )

    realtime.observe_entities.assert_awaited_once_with("history", set())
    realtime.observe_active_entities.assert_not_awaited()


@pytest.mark.asyncio
async def test_dois_produtos_novos_no_mesmo_pedido_geram_um_evento() -> None:
    realtime = _realtime(
        keys=([(101, "A"), (101, "B"), (101, "C")], [], []),
        observed=[["101:B", "101:C"], []],
    )

    await _registrar_novidades_realtime(realtime)

    realtime.enqueue.assert_awaited_once_with(
        topic="orders",
        event_type="orders.changed.v1",
        payload={
            "newOrderCount": 1,
            "newProductCount": 2,
            "reason": "databricks_sync",
        },
    )


@pytest.mark.asyncio
async def test_origem_de_pedidos_vazia_preserva_snapshot_anterior() -> None:
    source = MagicMock()
    source.pedidos_em_aberto = AsyncMock(return_value=[])
    repo = MagicMock()
    repo.count_pedidos = AsyncMock(return_value=45_000)
    repo.replace_pedidos = AsyncMock()

    count = await sincronizar_pedidos(source, repo)

    assert count == 45_000
    repo.replace_pedidos.assert_not_awaited()
