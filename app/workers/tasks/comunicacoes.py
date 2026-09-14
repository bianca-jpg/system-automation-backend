"""Worker e reconciliação periódica do delivery outbox de e-mail."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.config.settings import get_settings
from app.shared.database.options import asyncpg_connect_args
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_TASK_NAME = "app.workers.tasks.comunicacoes.processar_entregas"


@celery_app.task(name=_TASK_NAME)
def processar_entregas() -> dict[str, int | str]:
    return asyncio.run(_processar())


async def _processar() -> dict[str, int | str]:
    from app.modules.comunicacoes import service

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
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            try:
                result = await service.processar_entregas(db)
            except Exception as exc:  # noqa: BLE001 - não registrar params/PII SQL
                await db.rollback()
                logger.error(
                    "Falha no processamento do delivery outbox: %s",
                    type(exc).__name__,
                )
                return {"status": "error"}
        summary = result.as_dict()
        logger.info("Delivery outbox processado: %s", summary)
        return {"status": "success", **summary}
    finally:
        await engine.dispose()
