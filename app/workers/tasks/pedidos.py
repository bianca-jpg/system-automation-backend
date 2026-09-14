"""Celery composition for durable, resumable order processing jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from uuid import UUID, uuid4

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.shared.config.settings import get_settings
from app.shared.database.options import asyncpg_connect_args
from app.shared.jobs.domain import JobStatus
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_TASK_NAME = "app.workers.tasks.pedidos.processar_pedidos"
_MAX_CELERY_RETRIES = 2
_RETRY_SECONDS = 15
_SOFT_TIME_LIMIT_SECONDS = 15 * 60
_HARD_TIME_LIMIT_SECONDS = _SOFT_TIME_LIMIT_SECONDS + 30


class _RetryableProcessingTaskError(RuntimeError):
    pass


class _TerminalProcessingTaskError(RuntimeError):
    pass


def _worker_id() -> str:
    raw = f"orders:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    return re.sub(r"[^A-Za-z0-9_.:@-]", "-", raw)[:64]


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
    bind=True,
    name=_TASK_NAME,
    max_retries=_MAX_CELERY_RETRIES,
    soft_time_limit=_SOFT_TIME_LIMIT_SECONDS,
    time_limit=_HARD_TIME_LIMIT_SECONDS,
)
def processar_pedidos(self, job_id: str) -> dict[str, object]:
    """Execute one ledger job; retry state remains authoritative in PostgreSQL."""

    try:
        return asyncio.run(_processar(job_id))
    except SoftTimeLimitExceeded:
        # O signal pode interromper até a persistência da transição. O ledger
        # continua autoritativo e o sweeper recupera o lease após hard kill.
        logger.error("Processamento excedeu o limite total da task")
        raise RuntimeError("orders_processing_time_limit") from None
    except _RetryableProcessingTaskError:
        safe_error = RuntimeError("orders_processing_retry_scheduled")
        raise self.retry(exc=safe_error, countdown=_RETRY_SECONDS) from None
    except _TerminalProcessingTaskError:
        raise RuntimeError("orders_processing_failed") from None


async def _transition_failure(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
    worker_id: str,
    error_code: str,
    retryable: bool,
) -> None:
    from app.modules.pedidos import service

    try:
        async with factory() as db:
            transitioned = await service.marcar_falha_job_processamento(
                db,
                job_id=job_id,
                worker_id=worker_id,
                error_code=error_code,
                retryable=retryable,
            )
    except Exception as transition_error:  # noqa: BLE001 - falha de persistência é opaca
        logger.error(
            "Falha ao persistir transição do processamento; error_type=%s",
            type(transition_error).__name__,
        )
        raise _RetryableProcessingTaskError(
            "processing_transition_unavailable"
        ) from None

    if transitioned is None or transitioned.status is JobStatus.RETRYING:
        raise _RetryableProcessingTaskError("processing_retrying")
    raise _TerminalProcessingTaskError("processing_terminal")


async def _processar(job_id: str) -> dict[str, object]:
    from app.modules.pedidos import service
    from app.modules.pedidos.processing.domain import (
        InvalidProcessingRequest,
        ProcessingLeaseLost,
        ProcessingLockUnavailable,
        ProcessingPlanConflict,
        ProcessingPlanTooLarge,
    )

    try:
        parsed_job_id = UUID(job_id)
    except (TypeError, ValueError):
        logger.warning("Job de processamento rejeitado: identificador inválido")
        return {"status": "skipped", "reason": "invalid_job_id"}

    worker_id = _worker_id()
    engine, factory = _engine_and_factory()
    try:
        try:
            completed = await service.executar_job_processamento(
                engine=engine,
                session_factory=factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
            )
        except SoftTimeLimitExceeded:
            await _transition_failure(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="processing_time_limit",
                retryable=True,
            )
            raise AssertionError("unreachable") from None
        except ProcessingLeaseLost:
            logger.warning("Processamento perdeu o lease; job_id=%s", parsed_job_id)
            raise _RetryableProcessingTaskError("processing_lease_lost") from None
        except ProcessingLockUnavailable:
            # O try-lock ocorre antes do claim. Não consumir attempts/lease por
            # contenção normal; Celery e o reconciler redespacham o row queued.
            raise _RetryableProcessingTaskError("order_state_lock_busy") from None
        except ProcessingPlanTooLarge:
            await _transition_failure(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="processing_plan_too_large",
                retryable=False,
            )
            raise AssertionError("unreachable") from None
        except InvalidProcessingRequest:
            await _transition_failure(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="processing_source_invalid",
                retryable=False,
            )
            raise AssertionError("unreachable") from None
        except ProcessingPlanConflict:
            await _transition_failure(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="processing_plan_conflict",
                retryable=False,
            )
            raise AssertionError("unreachable") from None
        except (TimeoutError, SQLAlchemyError) as exc:
            logger.warning(
                "Falha transitória no processamento; error_type=%s",
                type(exc).__name__,
            )
            await _transition_failure(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="processing_dependency_unavailable",
                retryable=True,
            )
            raise AssertionError("unreachable") from None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fronteira Celery sanitiza o erro
            logger.error(
                "Falha interna no processamento; error_type=%s",
                type(exc).__name__,
            )
            await _transition_failure(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="internal_error",
                retryable=False,
            )
            raise AssertionError("unreachable") from None

        if completed is None:
            return {"status": "skipped", "reason": "not_claimed"}
        return {"status": completed.status.value, "jobId": str(completed.id)}
    finally:
        await engine.dispose()


__all__ = ["processar_pedidos"]
