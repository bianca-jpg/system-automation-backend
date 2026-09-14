"""Celery orchestration for authoritative PostgreSQL ingestion jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.modules.ingestao.application.jobs import INGESTION_JOB_MAX_ATTEMPTS
from app.shared.config.settings import get_settings
from app.shared.database.options import asyncpg_connect_args
from app.shared.infrastructure.databricks_client import DatabricksError
from app.shared.jobs.domain import JobStatus, utc_now
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_MAX_TASK_RETRIES = INGESTION_JOB_MAX_ATTEMPTS - 1
_RETRY_BASE_SECONDS = 15
_RETRY_MAX_SECONDS = 60
_LEASE_SECONDS = 120
_HEARTBEAT_SECONDS = 30
_settings = get_settings()
_SOFT_TIME_LIMIT_SECONDS = int(_settings.ingestao_total_timeout_seconds) + 30
_HARD_TIME_LIMIT_SECONDS = _SOFT_TIME_LIMIT_SECONDS + 30
_DATABRICKS_ERROR_CODES = {
    "ambiguous_submission": "databricks_ambiguous_submission",
    "configuration": "databricks_configuration",
    "deadline": "databricks_deadline",
    "http": "databricks_http_rejected",
    "limit": "databricks_limit",
    "protocol": "databricks_protocol",
    "rate_limit": "databricks_rate_limit",
    "statement": "databricks_statement",
    "timeout": "databricks_timeout",
    "transport": "databricks_transport",
}


class _JobLeaseLostError(RuntimeError):
    pass


class _RetryableJobDatabricksError(DatabricksError):
    def __init__(self, *, kind: str, countdown: int) -> None:
        super().__init__(
            "Databricks temporariamente indisponível.",
            kind=kind,
            retryable=True,
        )
        self.countdown = countdown


def _databricks_error_code(exc: DatabricksError) -> str:
    return _DATABRICKS_ERROR_CODES.get(exc.kind, "databricks_unknown")


def _worker_id() -> str:
    raw = f"celery:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
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
    name="app.workers.tasks.ingestao.sincronizar_databricks",
    max_retries=_MAX_TASK_RETRIES,
    soft_time_limit=_SOFT_TIME_LIMIT_SECONDS,
    time_limit=_HARD_TIME_LIMIT_SECONDS,
)
def sincronizar_databricks(self, job_id: str | None = None) -> dict[str, object]:
    """Create a periodic intent or execute one claimed durable job."""

    celery_attempt = int(self.request.retries) + 1
    try:
        return asyncio.run(_sincronizar(job_id=job_id))
    except DatabricksError as exc:
        logger.warning(
            "Ingestão Databricks falhou; code=%s celery_attempt=%d retryable=%s",
            _databricks_error_code(exc),
            celery_attempt,
            exc.retryable,
        )
        if not exc.retryable:
            raise
        countdown = int(
            getattr(
                exc,
                "countdown",
                min(
                    _RETRY_MAX_SECONDS,
                    _RETRY_BASE_SECONDS * (2 ** (celery_attempt - 1)),
                ),
            )
        )
        safe_error = DatabricksError(
            "Databricks temporariamente indisponível.",
            kind=exc.kind,
            retryable=True,
        )
        raise self.retry(exc=safe_error, countdown=countdown) from None


async def _registrar_job_periodico(
    factory: async_sessionmaker[AsyncSession],
) -> dict[str, object]:
    from app.modules.ingestao import service

    settings = get_settings()
    requested_at = utc_now()
    window = int(
        requested_at.timestamp() // max(1, settings.ingestao_intervalo_segundos)
    )
    async with factory() as db:
        submission = await service.registrar_sincronizacao(
            db,
            owner_id=None,
            idempotency_key=f"periodic:{window}",
            requested_at=requested_at,
        )
    reservation = submission.reservation
    return {
        "status": reservation.job.status.value,
        "jobId": str(reservation.job.id),
        "reservation": reservation.outcome.value,
        "brokerEnqueued": submission.broker_enqueued,
    }


async def _heartbeat_loop(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
    worker_id: str,
) -> None:
    from app.modules.ingestao import service

    while True:
        await asyncio.sleep(_HEARTBEAT_SECONDS)
        now = utc_now()
        async with factory() as db:
            heartbeat = await service.heartbeat_job(
                db,
                job_id=job_id,
                worker_id=worker_id,
                now=now,
                lease_until=now + timedelta(seconds=_LEASE_SECONDS),
            )
        if heartbeat is None:
            raise _JobLeaseLostError("ingestion_job_lease_lost")


async def _executar_com_heartbeat(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
    worker_id: str,
    total_timeout_seconds: float,
) -> dict[str, int]:
    from app.modules.ingestao import service

    async with factory() as db:
        sync_task = asyncio.create_task(
            service.sincronizar_tudo(
                db,
                total_timeout_seconds=total_timeout_seconds,
            )
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(factory, job_id=job_id, worker_id=worker_id)
        )
        try:
            done, _pending = await asyncio.wait(
                {sync_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if sync_task in done:
                return await sync_task
            await heartbeat_task
            raise _JobLeaseLostError("ingestion_job_heartbeat_stopped")
        finally:
            for task in (sync_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sync_task, heartbeat_task, return_exceptions=True)


async def _falhar_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
    worker_id: str,
    error_code: str,
) -> None:
    from app.modules.ingestao import service

    try:
        async with factory() as db:
            await service.fail_job(
                db,
                job_id=job_id,
                worker_id=worker_id,
                now=utc_now(),
                error_code=error_code,
                retryable=False,
            )
    except Exception as transition_error:  # noqa: BLE001 - preserve root failure
        logger.error(
            "Falha ao registrar término do job de ingestão; error_type=%s",
            type(transition_error).__name__,
        )


async def _sincronizar(*, job_id: str | None = None) -> dict[str, object]:
    from app.modules.ingestao import service
    from app.modules.ingestao.service import (
        SincronizacaoEmAndamentoError,
        SincronizacaoTimeoutError,
    )

    engine, factory = _engine_and_factory()
    try:
        if job_id is None:
            return await _registrar_job_periodico(factory)

        if not isinstance(job_id, str) or len(job_id) != 36:
            logger.error("Job de ingestão rejeitado: identificador inválido")
            return {"status": "skipped", "reason": "invalid_job_id"}
        try:
            parsed_job_id = UUID(job_id)
        except (TypeError, ValueError):
            logger.error("Job de ingestão rejeitado: identificador inválido")
            return {"status": "skipped", "reason": "invalid_job_id"}

        worker_id = _worker_id()
        now = utc_now()
        async with factory() as db:
            claimed = await service.claim_job(
                db,
                job_id=parsed_job_id,
                worker_id=worker_id,
                now=now,
                lease_until=now + timedelta(seconds=_LEASE_SECONDS),
            )
        if claimed is None:
            return {"status": "skipped", "reason": "not_claimed"}

        total_timeout_seconds = float(get_settings().ingestao_total_timeout_seconds)
        if claimed.deadline_at is not None:
            total_timeout_seconds = max(
                0.001,
                (claimed.deadline_at - utc_now()).total_seconds(),
            )

        try:
            resultado = await _executar_com_heartbeat(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                total_timeout_seconds=total_timeout_seconds,
            )
        except SincronizacaoEmAndamentoError:
            logger.warning("Ingestão ignorada: outra sincronização está em andamento")
            async with factory() as db:
                skipped = await service.skip_job(
                    db,
                    job_id=parsed_job_id,
                    worker_id=worker_id,
                    now=utc_now(),
                    error_code="sync_already_running",
                )
            if skipped is None:
                raise _JobLeaseLostError("ingestion_job_lease_lost") from None
            return {"status": "skipped", "reason": "sync_already_running"}
        except DatabricksError as exc:
            error_code = _databricks_error_code(exc)
            if not exc.retryable:
                await _falhar_job(
                    factory,
                    job_id=parsed_job_id,
                    worker_id=worker_id,
                    error_code=error_code,
                )
                raise

            countdown = min(
                _RETRY_MAX_SECONDS,
                _RETRY_BASE_SECONDS * (2 ** max(0, claimed.attempts - 1)),
            )
            transition_now = utc_now()
            async with factory() as db:
                transitioned = await service.retry_job(
                    db,
                    job_id=parsed_job_id,
                    worker_id=worker_id,
                    now=transition_now,
                    retry_at=transition_now + timedelta(seconds=countdown),
                    error_code=error_code,
                )
            if transitioned is None:
                raise _JobLeaseLostError("ingestion_job_lease_lost") from None
            if transitioned.status is JobStatus.RETRYING:
                raise _RetryableJobDatabricksError(
                    kind=exc.kind,
                    countdown=countdown,
                ) from None
            raise DatabricksError(
                "Tentativas do job Databricks esgotadas.",
                kind=exc.kind,
                retryable=False,
            ) from None
        except SincronizacaoTimeoutError:
            logger.error("Ingestão excedeu o orçamento total")
            await _falhar_job(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="ingestion_timeout",
            )
            raise
        except asyncio.CancelledError:
            raise
        except _JobLeaseLostError:
            logger.error("Execução da ingestão perdeu a posse do lease")
            raise
        except Exception:
            logger.exception("Ingestão falhou com erro interno")
            await _falhar_job(
                factory,
                job_id=parsed_job_id,
                worker_id=worker_id,
                error_code="internal_error",
            )
            raise

        job_result: dict[str, object] = dict(resultado)
        async with factory() as db:
            succeeded = await service.succeed_job(
                db,
                job_id=parsed_job_id,
                worker_id=worker_id,
                now=utc_now(),
                result=job_result,
            )
        if succeeded is None:
            raise _JobLeaseLostError("ingestion_job_lease_lost")
        logger.info("Ingestão periódica concluída: %s", resultado)
        return {"status": "succeeded", **resultado}
    finally:
        await engine.dispose()


__all__ = ["sincronizar_databricks"]
