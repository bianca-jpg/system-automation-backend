from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.shared.jobs.domain import ReconcileResult
from app.workers.celery_app import celery_app
from app.workers.tasks import durable_jobs as worker

_JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class _Factory:
    def __call__(self):
        return _SessionContext()


def _engine_factory():
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine, _Factory()


def test_worker_generico_nao_importa_bounded_context():
    source = Path(worker.__file__).read_text(encoding="utf-8")

    assert "app.modules." not in source


def test_celery_registra_worker_e_beat_genericos():
    assert "app.workers.tasks.durable_jobs" in celery_app.conf.include
    schedule = celery_app.conf.beat_schedule["reconciliar-jobs-duraveis"]
    assert schedule["task"] == "app.workers.tasks.durable_jobs.reconciliar_jobs"
    assert schedule["schedule"] == 60.0


@pytest.mark.asyncio
async def test_reconciliador_recupera_jobs_e_executa_retencao_bounded():
    engine, factory = _engine_factory()
    dispatcher = MagicMock()
    reconcile_result = ReconcileResult(
        retrying=(_JOB_ID,),
        failed=(),
        skipped=(),
    )
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch.object(
            worker,
            "CeleryDurableJobDispatcher",
            return_value=dispatcher,
        ),
        patch.object(
            worker,
            "reconcile_and_dispatch",
            new=AsyncMock(return_value=reconcile_result),
        ) as reconcile,
        patch.object(
            worker,
            "cleanup_retained_jobs",
            new=AsyncMock(return_value=3),
        ) as cleanup,
    ):
        result = await worker._reconciliar_jobs()

    assert result == {"retrying": 1, "failed": 0, "skipped": 0, "removed": 3}
    reconcile_await_args = reconcile.await_args
    assert reconcile_await_args is not None
    assert reconcile_await_args.kwargs["limit"] == 20
    assert reconcile_await_args.args[2] is dispatcher
    cleanup_await_args = cleanup.await_args
    assert cleanup_await_args is not None
    assert cleanup_await_args.kwargs["limit"] == 500
    assert "now" not in cleanup_await_args.kwargs
    engine.dispose.assert_awaited_once()
