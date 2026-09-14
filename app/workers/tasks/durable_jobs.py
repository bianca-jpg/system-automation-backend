"""Generic Celery composition for the authoritative durable-job ledger."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.shared.config.settings import get_settings
from app.shared.database.options import asyncpg_connect_args
from app.shared.jobs.application.service import (
    cleanup_retained_jobs,
    reconcile_and_dispatch,
)
from app.shared.jobs.domain import utc_now
from app.shared.jobs.infrastructure.repository import (
    SqlAlchemyDurableJobRepository,
    SqlAlchemyDurableJobUnitOfWork,
)
from app.workers.celery_app import celery_app
from app.workers.durable_job_dispatcher import CeleryDurableJobDispatcher

# The dispatcher has a 2 s deadline per row. Twenty publishes leave headroom
# for database work and cleanup inside the task's 55 s soft time limit.
_RECONCILE_LIMIT = 20
_CLEANUP_LIMIT = 500
_RETRY_DELAY = timedelta(seconds=15)


def _engine_and_factory() -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
]:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        connect_args=asyncpg_connect_args(
            ssl_mode=settings.postgres_ssl_mode,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
            command_timeout_seconds=settings.postgres_command_timeout_seconds,
        ),
    )
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@celery_app.task(
    name="app.workers.tasks.durable_jobs.reconciliar_jobs",
    soft_time_limit=55,
    time_limit=60,
)
def reconciliar_jobs() -> dict[str, int]:
    """Recover all registered job kinds and apply bounded retention."""

    return asyncio.run(_reconciliar_jobs())


async def _reconciliar_jobs() -> dict[str, int]:
    engine, factory = _engine_and_factory()
    try:
        async with factory() as db:
            repository = SqlAlchemyDurableJobRepository(db)
            unit_of_work = SqlAlchemyDurableJobUnitOfWork(db)
            now = utc_now()
            reconciled = await reconcile_and_dispatch(
                repository,
                unit_of_work,
                CeleryDurableJobDispatcher(),
                now=now,
                retry_delay=_RETRY_DELAY,
                limit=_RECONCILE_LIMIT,
            )
            removed = await cleanup_retained_jobs(
                repository,
                unit_of_work,
                limit=_CLEANUP_LIMIT,
            )
        return {
            "retrying": len(reconciled.retrying),
            "failed": len(reconciled.failed),
            "skipped": len(reconciled.skipped),
            "removed": removed,
        }
    finally:
        await engine.dispose()


__all__ = ["reconciliar_jobs"]
