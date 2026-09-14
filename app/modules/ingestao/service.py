"""Composition root do bounded context de ingestão."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestao.application import casos_uso
from app.modules.ingestao.application.jobs import (
    INGESTION_JOB_KIND,
    new_sync_job,
    sanitized_result,
)
from app.modules.ingestao.infrastructure.adapters import (
    DatabricksIngestionSource,
    PostgresIngestionJobLock,
    SqlRealtimeSnapshotAdapter,
    SqlSnapshotRepository,
)
from app.shared.config.settings import get_settings
from app.shared.database.session import async_session_factory
from app.shared.jobs.application.service import submit_job
from app.shared.jobs.domain import (
    DurableJob,
    JobSubmission,
    utc_now,
)
from app.shared.jobs.infrastructure.repository import (
    SqlAlchemyDurableJobRepository,
    SqlAlchemyDurableJobUnitOfWork,
)
from app.workers.durable_job_dispatcher import CeleryDurableJobDispatcher

SincronizacaoEmAndamentoError = casos_uso.SincronizacaoEmAndamentoError
SincronizacaoTimeoutError = casos_uso.SincronizacaoTimeoutError


def _source() -> DatabricksIngestionSource:
    return DatabricksIngestionSource()


def _repository(db: AsyncSession) -> SqlSnapshotRepository:
    return SqlSnapshotRepository(db)


def _job_components(
    db: AsyncSession,
) -> tuple[SqlAlchemyDurableJobRepository, SqlAlchemyDurableJobUnitOfWork]:
    return SqlAlchemyDurableJobRepository(db), SqlAlchemyDurableJobUnitOfWork(db)


async def sincronizar_pedidos(db: AsyncSession) -> int:
    return await casos_uso.sincronizar_pedidos(_source(), _repository(db))


async def sincronizar_estoque(db: AsyncSession) -> int:
    return await casos_uso.sincronizar_estoque(_source(), _repository(db))


async def sincronizar_pedidos_processados_erp(db: AsyncSession) -> int:
    return await casos_uso.sincronizar_pedidos_processados_erp(
        _source(), _repository(db)
    )


async def sincronizar_faturamento_colecao(db: AsyncSession) -> int:
    return await casos_uso.sincronizar_faturamento_colecao(_source(), _repository(db))


async def sincronizar_referencia_tamanhos(db: AsyncSession) -> int:
    return await casos_uso.sincronizar_referencia_tamanhos(_source(), _repository(db))


async def sincronizar_tudo(
    db: AsyncSession,
    *,
    total_timeout_seconds: float | None = None,
) -> dict[str, int]:
    timeout_seconds = (
        total_timeout_seconds
        if total_timeout_seconds is not None
        else get_settings().ingestao_total_timeout_seconds
    )
    return await casos_uso.sincronizar_tudo(
        _source(),
        _repository(db),
        SqlRealtimeSnapshotAdapter(db),
        PostgresIngestionJobLock(db),
        total_timeout_seconds=timeout_seconds,
    )


async def registrar_sincronizacao(
    db: AsyncSession,
    *,
    owner_id: int | None,
    idempotency_key: str | None,
    requested_at: datetime | None = None,
) -> JobSubmission:
    settings = get_settings()
    proposed = new_sync_job(
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        requested_at=requested_at or utc_now(),
        total_timeout_seconds=settings.ingestao_total_timeout_seconds,
    )
    repository, unit_of_work = _job_components(db)
    return await submit_job(
        repository,
        unit_of_work,
        CeleryDurableJobDispatcher(),
        proposed,
    )


async def solicitar_sincronizacao(
    *,
    owner_id: int | None,
    idempotency_key: str | None,
) -> JobSubmission:
    async with async_session_factory() as db:
        return await registrar_sincronizacao(
            db,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
        )


async def obter_job_sincronizacao(job_id: UUID) -> DurableJob | None:
    async with async_session_factory() as db:
        return await SqlAlchemyDurableJobRepository(db).get(job_id)


async def claim_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    lease_until: datetime,
) -> DurableJob | None:
    repository, unit_of_work = _job_components(db)
    try:
        job = await repository.claim(
            job_id=job_id,
            kinds=(INGESTION_JOB_KIND,),
            worker_id=worker_id,
            now=now,
            lease_until=lease_until,
        )
        if job is not None:
            initialized = await repository.heartbeat(
                job_id=job_id,
                worker_id=worker_id,
                now=now,
                lease_until=lease_until,
                progress_current=0,
                progress_total=5,
            )
            if initialized is None:
                raise RuntimeError("ingestion_job_claim_initialization_failed")
            job = initialized
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


async def heartbeat_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    lease_until: datetime,
) -> DurableJob | None:
    repository, unit_of_work = _job_components(db)
    try:
        job = await repository.heartbeat(
            job_id=job_id,
            worker_id=worker_id,
            now=now,
            lease_until=lease_until,
            progress_current=0,
            progress_total=5,
        )
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


async def succeed_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    result: dict[str, object],
) -> DurableJob | None:
    repository, unit_of_work = _job_components(db)
    try:
        job = await repository.succeed(
            job_id=job_id,
            worker_id=worker_id,
            now=now,
            result=sanitized_result(result),
        )
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


async def fail_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    error_code: str,
    retryable: bool = False,
) -> DurableJob | None:
    repository, unit_of_work = _job_components(db)
    try:
        job = await repository.fail(
            job_id=job_id,
            worker_id=worker_id,
            now=now,
            error_code=error_code,
            retryable=retryable,
        )
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


async def retry_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    retry_at: datetime,
    error_code: str,
) -> DurableJob | None:
    repository, unit_of_work = _job_components(db)
    try:
        job = await repository.retry(
            job_id=job_id,
            worker_id=worker_id,
            now=now,
            retry_at=retry_at,
            error_code=error_code,
        )
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


async def skip_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    error_code: str,
) -> DurableJob | None:
    repository, unit_of_work = _job_components(db)
    try:
        job = await repository.skip(
            job_id=job_id,
            worker_id=worker_id,
            now=now,
            error_code=error_code,
        )
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


__all__ = [
    "SincronizacaoEmAndamentoError",
    "SincronizacaoTimeoutError",
    "claim_job",
    "fail_job",
    "heartbeat_job",
    "obter_job_sincronizacao",
    "registrar_sincronizacao",
    "retry_job",
    "sincronizar_estoque",
    "sincronizar_faturamento_colecao",
    "sincronizar_pedidos",
    "sincronizar_pedidos_processados_erp",
    "sincronizar_referencia_tamanhos",
    "sincronizar_tudo",
    "skip_job",
    "solicitar_sincronizacao",
    "succeed_job",
]
