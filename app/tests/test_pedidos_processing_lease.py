from __future__ import annotations

import asyncio
from datetime import datetime
from time import monotonic
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.pedidos.processing.domain import ProcessingLeaseLost
from app.modules.pedidos.processing.infrastructure import session_lock as lock_module
from app.modules.pedidos.processing.infrastructure.session_lock import (
    SqlAlchemyProcessingLeaseKeeper,
)


async def test_lease_keeper_fails_closed_when_heartbeat_loses_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # session_factory nunca é usado: `_heartbeat` é monkeypatched em todos os
    # testes deste arquivo, então None é seguro em runtime.
    keeper = SqlAlchemyProcessingLeaseKeeper(
        cast("async_sessionmaker[AsyncSession]", None),
        interval_seconds=0.001,
    )

    async def lost_heartbeat(_now: datetime) -> bool:
        return False

    monkeypatch.setattr(keeper, "_heartbeat", lost_heartbeat)
    await keeper.start(
        job_id=uuid4(),
        worker_id="worker-lease",
        progress_current=0,
        progress_total=None,
    )
    await asyncio.sleep(0.02)
    with pytest.raises(ProcessingLeaseLost, match="processing_job_lease_lost"):
        await keeper.ensure_valid()
    await keeper.stop()


async def test_lease_keeper_stop_is_bounded_when_heartbeat_resists_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lock_module, "_HEARTBEAT_STOP_TIMEOUT_SECONDS", 0.01)
    # session_factory nunca é usado: `_heartbeat` é monkeypatched em todos os
    # testes deste arquivo, então None é seguro em runtime.
    keeper = SqlAlchemyProcessingLeaseKeeper(
        cast("async_sessionmaker[AsyncSession]", None),
        interval_seconds=0.001,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_heartbeat(_now: datetime) -> bool:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Simula driver que só observa cancelamento depois do command timeout.
            await release.wait()
        return True

    monkeypatch.setattr(keeper, "_heartbeat", blocked_heartbeat)
    await keeper.start(
        job_id=uuid4(),
        worker_id="worker-blocked",
        progress_current=0,
        progress_total=None,
    )
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    task = keeper._task
    started = monotonic()
    await keeper.stop()
    elapsed = monotonic() - started

    assert elapsed < 0.1
    assert task is not None and not task.done()
    release.set()
    await asyncio.wait_for(task, timeout=0.2)
