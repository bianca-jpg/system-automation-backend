import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, call, patch

import pytest

from app.modules.realtime.domain import RealtimeUnavailableError
from app.modules.realtime.infrastructure import repository
from app.modules.realtime.infrastructure.connection_manager import (
    LocalConnectionManager,
)
from app.modules.realtime.infrastructure.redis_gateway import (
    RedisRealtimeGateway,
    TicketRateLimitedError,
)
from app.modules.realtime.infrastructure.runtime import RealtimeRuntime
from app.shared.config.settings import Settings

_TICKET = "ticket_realtime_one_use_1234567890abcdef"
_ACCESS_EXPIRES_AT = datetime(2099, 1, 1, tzinfo=UTC)
_TOPICS = ["orders", "alerts", "communications", "history"]


def _settings(**overrides) -> Settings:
    values = {"_env_file": None, **overrides}
    return Settings(**values)


@pytest.mark.asyncio
async def test_ticket_usa_getdel_e_so_pode_ser_consumido_uma_vez():
    redis = AsyncMock()
    redis.eval.return_value = 1
    redis.set.return_value = True
    gateway = RedisRealtimeGateway(redis, _settings())

    with (
        patch(
            "app.modules.realtime.infrastructure.redis_gateway.secrets.token_urlsafe",
            return_value=_TICKET,
        ),
        patch(
            "app.modules.realtime.infrastructure.redis_gateway.secrets.token_hex",
            return_value="nonce1234567890",
        ),
    ):
        issued = await gateway.issue_ticket(
            user_id=42,
            client_ip="127.0.0.1",
            access_expires_at=_ACCESS_EXPIRES_AT,
            topics=_TOPICS,
        )

    assert issued == _TICKET
    ticket_key, stored_value = redis.set.await_args.args
    assert ticket_key.startswith("automation:realtime:ticket:")
    assert _TICKET not in ticket_key
    stored = json.loads(stored_value)
    assert stored["userId"] == 42
    assert stored["nonce"] == "nonce1234567890"
    assert datetime.fromisoformat(stored["issuedAt"]).tzinfo is not None
    assert datetime.fromisoformat(stored["accessExpiresAt"]) == _ACCESS_EXPIRES_AT
    assert stored["topics"] == _TOPICS
    assert redis.set.await_args.kwargs == {"ex": 30, "nx": True}

    redis.getdel.side_effect = [stored_value, None]
    first = await gateway.consume_ticket(_TICKET)
    second = await gateway.consume_ticket(_TICKET)

    assert first is not None
    assert first.user_id == 42
    assert first.access_expires_at == _ACCESS_EXPIRES_AT
    assert first.topics == frozenset(_TOPICS)
    assert second is None
    assert redis.getdel.await_count == 2
    redis.getdel.assert_awaited_with(ticket_key)


@pytest.mark.asyncio
async def test_ticket_rate_limit_interrompe_antes_de_persistir():
    redis = AsyncMock()
    settings = _settings(REALTIME_TICKET_USER_RATE_PER_MINUTE=1)
    redis.eval.return_value = 2
    gateway = RedisRealtimeGateway(redis, settings)

    with pytest.raises(TicketRateLimitedError, match="user"):
        await gateway.issue_ticket(
            user_id=42,
            client_ip="127.0.0.1",
            access_expires_at=_ACCESS_EXPIRES_AT,
            topics=_TOPICS,
        )

    redis.set.assert_not_awaited()
    assert redis.eval.await_count == 1


@pytest.mark.asyncio
async def test_publish_envelope_usa_xadd_com_maxlen_bounded():
    redis = AsyncMock()
    redis.xadd.return_value = "1700000000000-0"
    settings = _settings(REALTIME_STREAM_MAXLEN=12_345)
    gateway = RedisRealtimeGateway(redis, settings)
    envelope = {
        "version": 1,
        "eventId": "event-1",
        "sequence": 1,
        "type": "orders.processed.v1",
        "topic": "orders",
        "occurredAt": "2026-08-08T04:02:03Z",
        "payload": {"orderId": 123},
    }

    stream_id = await gateway.publish_envelope(envelope)

    assert stream_id == "1700000000000-0"
    redis.xadd.assert_awaited_once_with(
        "automation:realtime:v1",
        {"envelope": json.dumps(envelope, separators=(",", ":"))},
        maxlen=12_345,
        approximate=True,
    )


@pytest.mark.asyncio
async def test_dois_managers_recebem_o_mesmo_fanout_horizontal():
    settings = _settings()
    manager_a = LocalConnectionManager(settings)
    manager_b = LocalConnectionManager(settings)
    connection_a = await manager_a.register(
        AsyncMock(), user_id=1, topics=frozenset({"orders"})
    )
    connection_b = await manager_b.register(
        AsyncMock(), user_id=2, topics=frozenset({"orders"})
    )
    envelope = {"eventId": "event-horizontal", "topic": "orders"}

    try:
        # Cada listener de instância lê o mesmo Stream e chama seu hub local;
        # não há consumer group dividindo o evento entre instâncias.
        await manager_a.broadcast(envelope)
        await manager_b.broadcast(envelope)

        assert connection_a.queue.get_nowait() == envelope
        assert connection_b.queue.get_nowait() == envelope
    finally:
        await manager_a.unregister(connection_a)
        await manager_b.unregister(connection_b)


@pytest.mark.asyncio
async def test_manager_deduplica_event_id_por_conexao():
    manager = LocalConnectionManager(_settings())
    connection = await manager.register(
        AsyncMock(), user_id=1, topics=frozenset({"orders"})
    )
    envelope = {"eventId": "event-duplicado", "topic": "orders"}

    try:
        await manager.broadcast(envelope)
        await manager.broadcast(envelope)

        assert connection.queue.qsize() == 1
        assert connection.queue.get_nowait() == envelope
    finally:
        await manager.unregister(connection)


@pytest.mark.asyncio
async def test_queue_cheia_desregistra_e_fecha_com_1013():
    manager = LocalConnectionManager(_settings())
    websocket = AsyncMock()
    connection = await manager.register(
        websocket, user_id=1, topics=frozenset({"orders"})
    )
    for index in range(connection.queue.maxsize):
        connection.queue.put_nowait({"queued": index})

    safe_close = AsyncMock()
    with (
        patch.object(manager, "unregister", wraps=manager.unregister) as unregister,
        patch.object(manager, "_safe_close", new=safe_close),
    ):
        await manager.broadcast({"eventId": "event-backpressure", "topic": "orders"})
        await asyncio.sleep(0)

    unregister.assert_awaited_once_with(connection, reason="backpressure")
    safe_close.assert_awaited_once_with(
        websocket,
        code=1013,
        reason="backpressure",
    )
    assert connection.connection_id not in manager._connections


@pytest.mark.asyncio
async def test_ledger_ativo_ausente_e_reaparecido_retorna_a_chave():
    state = SimpleNamespace(observed_initialized_at=datetime(2026, 8, 8, tzinfo=UTC))
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=list)),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["order-123"])),
    ]

    with (
        patch(
            "app.modules.realtime.infrastructure.repository.lock_global_event_order",
            new=AsyncMock(),
        ),
        patch(
            "app.modules.realtime.infrastructure.repository._locked_topic_state",
            new=AsyncMock(return_value=state),
        ),
    ):
        absent = await repository.observe_active_keys(
            db,
            topic="orders",
            keys=[],
            baseline_if_empty=False,
            allow_empty_snapshot=True,
        )
        reappeared = await repository.observe_active_keys(
            db,
            topic="orders",
            keys=["order-123"],
            baseline_if_empty=False,
            allow_empty_snapshot=True,
        )

    assert absent == []
    assert reappeared == ["order-123"]
    # A função hidrata apenas o resultado set-based; nunca usa scalars() para
    # carregar o histórico ORM do tópico.
    db.scalars.assert_not_awaited()
    assert db.flush.await_count == 2


def _raw_envelope(sequence: int) -> dict[str, object]:
    return {
        "version": 1,
        "eventId": f"00000000-0000-0000-0000-{sequence:012d}",
        "sequence": sequence,
        "type": "order.changed",
        "topic": "orders",
        "occurredAt": "2026-08-08T04:02:03Z",
        "payload": {"orderId": sequence},
    }


@pytest.mark.asyncio
async def test_listener_detecta_gap_confirmado_e_fecha_sockets_com_1013():
    runtime = RealtimeRuntime(_settings())
    gateway = SimpleNamespace(
        read_stream=AsyncMock(
            side_effect=[
                ("1-0", [_raw_envelope(1)]),
                ("3-0", [_raw_envelope(3)]),
                asyncio.CancelledError,
            ]
        )
    )
    manager = SimpleNamespace(
        broadcast=AsyncMock(),
        disconnect_all=AsyncMock(),
    )
    runtime.gateway = cast(RedisRealtimeGateway, gateway)
    runtime.manager = cast(LocalConnectionManager, manager)

    with (
        patch.object(
            repository,
            "stream_gap_requires_resync",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await runtime._stream_loop()

    manager.disconnect_all.assert_awaited_once_with(
        code=1013,
        reason="stream_gap",
    )
    assert manager.broadcast.await_count == 2


@pytest.mark.asyncio
async def test_listener_fecha_janela_de_reconexao_apos_redis_recuperar():
    runtime = RealtimeRuntime(_settings())
    gateway = SimpleNamespace(
        read_stream=AsyncMock(
            side_effect=[
                RealtimeUnavailableError("down"),
                ("$", []),
                asyncio.CancelledError,
            ]
        )
    )
    manager = SimpleNamespace(
        broadcast=AsyncMock(),
        disconnect_all=AsyncMock(),
    )
    runtime.gateway = cast(RedisRealtimeGateway, gateway)
    runtime.manager = cast(LocalConnectionManager, manager)

    with (
        patch(
            "app.modules.realtime.infrastructure.runtime.asyncio.sleep",
            new=AsyncMock(),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await runtime._stream_loop()

    assert manager.disconnect_all.await_args_list == [
        call(code=1013, reason="transport_unavailable"),
        call(code=1013, reason="transport_recovered"),
    ]
