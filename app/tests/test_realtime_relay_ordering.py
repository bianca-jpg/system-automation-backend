import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.modules.realtime.domain import RealtimeUnavailableError
from app.modules.realtime.infrastructure import runtime as runtime_module
from app.modules.realtime.infrastructure.redis_gateway import RedisRealtimeGateway
from app.modules.realtime.infrastructure.runtime import RealtimeRuntime
from app.shared.config.settings import Settings


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class _SessionFactory:
    def __init__(self, sessions):
        self._sessions = iter(sessions)

    def __call__(self):
        return _AsyncContext(next(self._sessions))


def _db(*, leader: bool = False):
    return SimpleNamespace(
        scalar=AsyncMock(return_value=leader),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )


def _settings() -> Settings:
    overrides: dict[str, object] = {
        "REALTIME_RELAY_INTERVAL_MS": 50,
        "REALTIME_RELAY_BATCH_SIZE": 10,
    }
    # `_env_file` é um kwarg especial de `BaseSettings.__init__` (não um campo
    # do modelo); o pyright sintetiza o __init__ a partir dos campos e não
    # reconhece esse parâmetro, mesmo sendo válido em runtime. Confinado a
    # esta única linha do helper.
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


def _row(sequence: int):
    return SimpleNamespace(
        event_id=UUID(int=sequence),
        sequence=sequence,
        event_type="orders.processed.v1",
        topic="orders",
        occurred_at=datetime(2026, 8, 8, 4, 2, sequence, tzinfo=UTC),
        payload={"orderId": sequence},
        version=1,
        attempts=1,
    )


@pytest.mark.asyncio
async def test_duas_instancias_disputam_lock_e_so_lider_publica():
    leader_db = _db(leader=True)
    follower_db = _db(leader=False)
    metrics_db = _db()
    sessions_by_task = {
        "realtime-test-leader": iter([leader_db, metrics_db]),
        "realtime-test-follower": iter([follower_db]),
    }

    def session_factory():
        task = asyncio.current_task()
        assert task is not None
        return _AsyncContext(next(sessions_by_task[task.get_name()]))

    first = RealtimeRuntime(_settings())
    second = RealtimeRuntime(_settings())
    first_gateway = SimpleNamespace(publish_envelope=AsyncMock())
    second_gateway = SimpleNamespace(publish_envelope=AsyncMock())
    first.gateway = cast(RedisRealtimeGateway, first_gateway)
    second.gateway = cast(RedisRealtimeGateway, second_gateway)
    claim = AsyncMock(side_effect=[[_row(1)], []])
    mark_published = AsyncMock(return_value=True)

    with (
        patch.object(
            runtime_module,
            "async_session_factory",
            side_effect=session_factory,
        ),
        patch.object(runtime_module.repository, "claim_outbox_batch", new=claim),
        patch.object(
            runtime_module.repository,
            "mark_published",
            new=mark_published,
        ),
        patch.object(
            runtime_module.repository,
            "pending_outbox_count",
            new=AsyncMock(return_value=0),
        ),
        patch.object(
            runtime_module.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ):
        results = await asyncio.gather(
            asyncio.create_task(
                first._relay_loop(),
                name="realtime-test-leader",
            ),
            asyncio.create_task(
                second._relay_loop(),
                name="realtime-test-follower",
            ),
            return_exceptions=True,
        )

    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert (
        first_gateway.publish_envelope.await_count
        + second_gateway.publish_envelope.await_count
        == 1
    )
    assert claim.await_count == 2
    leader_db.scalar.assert_awaited_once()
    follower_db.scalar.assert_awaited_once()
    assert leader_db.scalar.await_args.kwargs == {}
    assert leader_db.scalar.await_args.args[1] == {
        "key": runtime_module._RELAY_LEADER_LOCK
    }
    assert "pg_try_advisory_xact_lock" in str(leader_db.scalar.await_args.args[0])
    leader_db.commit.assert_awaited_once()
    follower_db.rollback.assert_awaited_once()
    mark_published.assert_awaited_once()


@pytest.mark.asyncio
async def test_falha_de_n_bloqueia_n_mais_1_ate_retry_e_sucesso():
    first_db = _db(leader=True)
    second_db = _db(leader=True)
    session_factory = _SessionFactory([first_db, _db(), second_db, _db()])
    relay = RealtimeRuntime(_settings())
    relay_gateway = SimpleNamespace(
        publish_envelope=AsyncMock(
            side_effect=[
                RealtimeUnavailableError("redis indisponível"),
                None,
                None,
            ]
        )
    )
    relay.gateway = cast(RedisRealtimeGateway, relay_gateway)
    row_n = _row(1)
    row_n_plus_1 = _row(2)
    claim = AsyncMock(side_effect=[[row_n], [row_n], [row_n_plus_1], []])
    mark_failed = AsyncMock()
    mark_published = AsyncMock(return_value=True)
    sleep_calls = 0

    async def finish_after_second_cycle(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            raise asyncio.CancelledError

    with (
        patch.object(
            runtime_module,
            "async_session_factory",
            side_effect=session_factory,
        ),
        patch.object(runtime_module.repository, "claim_outbox_batch", new=claim),
        patch.object(
            runtime_module.repository,
            "mark_publish_failed",
            new=mark_failed,
        ),
        patch.object(
            runtime_module.repository,
            "mark_published",
            new=mark_published,
        ),
        patch.object(
            runtime_module.repository,
            "pending_outbox_count",
            new=AsyncMock(return_value=0),
        ),
        patch.object(runtime_module.asyncio, "sleep", new=finish_after_second_cycle),
        pytest.raises(asyncio.CancelledError),
    ):
        await relay._relay_loop()

    published_sequences = [
        call.args[0]["sequence"]
        for call in relay_gateway.publish_envelope.await_args_list
    ]
    assert published_sequences == [1, 1, 2]
    mark_failed.assert_awaited_once()
    assert mark_failed.await_args is not None
    assert mark_failed.await_args.kwargs == {
        "sequence": 1,
        "instance_id": relay.instance_id,
        "attempts": 1,
        "error_name": "RealtimeUnavailableError",
    }
    assert [call.kwargs["sequence"] for call in mark_published.await_args_list] == [
        1,
        2,
    ]
    first_db.commit.assert_awaited_once()
    second_db.commit.assert_awaited_once()
