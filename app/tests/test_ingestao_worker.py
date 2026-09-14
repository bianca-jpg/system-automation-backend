import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shared.infrastructure.databricks_client import (
    DatabricksAmbiguousSubmissionError,
    DatabricksConfigurationError,
    DatabricksError,
)
from app.shared.jobs.domain import (
    DurableJob,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
    JobSubmission,
    digest_idempotency_key,
)
from app.workers.tasks import ingestao as worker
from app.workers.tasks.ingestao import sincronizar_databricks

_JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class _Factory:
    def __call__(self):
        return _SessionContext()


def _claimed_job(*, attempts: int = 1) -> DurableJob:
    now = datetime.now(UTC)
    return DurableJob(
        id=_JOB_ID,
        kind="ingestion.full_sync.v1",
        owner_id=1,
        scope_key="full-refresh",
        idempotency_digest=None,
        fingerprint="a" * 64,
        status=JobStatus.RUNNING,
        attempts=attempts,
        max_attempts=3,
        progress_current=0,
        progress_total=5,
        result=None,
        error_code=None,
        retryable=False,
        requested_at=now,
        available_at=now,
        started_at=now,
        heartbeat_at=now,
        lease_owner="celery:worker",
        lease_expires_at=now + timedelta(seconds=120),
        deadline_at=now + timedelta(minutes=30),
        finished_at=None,
        updated_at=now,
    )


def _engine_factory():
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine, _Factory()


def test_job_ingestao_namespaces_idempotencia_e_coalesce_full_refresh():
    from app.modules.ingestao.application.jobs import new_sync_job

    requested_at = datetime.now(UTC)
    job = new_sync_job(
        owner_id=7,
        idempotency_key="request-12345678",
        requested_at=requested_at,
        total_timeout_seconds=1800,
    )

    assert job.scope_key == "full-refresh"
    assert job.idempotency_digest == digest_idempotency_key(
        "ingestion.sync:request-12345678"
    )
    assert job.deadline_at == requested_at + timedelta(seconds=1800)


@pytest.mark.asyncio
async def test_claim_inicializa_progresso_total_no_mesmo_commit():
    from app.modules.ingestao import service

    claimed = replace(_claimed_job(), progress_total=None)
    initialized = replace(claimed, progress_total=5)
    repository = MagicMock()
    repository.claim = AsyncMock(return_value=claimed)
    repository.heartbeat = AsyncMock(return_value=initialized)
    unit_of_work = MagicMock()
    unit_of_work.commit = AsyncMock()
    unit_of_work.rollback = AsyncMock()
    now = datetime.now(UTC)

    with patch.object(
        service,
        "_job_components",
        return_value=(repository, unit_of_work),
    ):
        result = await service.claim_job(
            cast(AsyncSession, object()),
            job_id=_JOB_ID,
            worker_id="celery:worker",
            now=now,
            lease_until=now + timedelta(seconds=120),
        )

    assert result is initialized
    assert repository.heartbeat.await_args.kwargs["progress_total"] == 5
    unit_of_work.commit.assert_awaited_once()


def _run_raising(error: Exception):
    def run(coroutine):
        coroutine.close()
        raise error

    return run


def test_task_nao_reagenda_erro_permanente_databricks():
    erro = DatabricksConfigurationError("configuração ausente")
    with (
        patch(
            "app.workers.tasks.ingestao.asyncio.run",
            side_effect=_run_raising(erro),
        ),
        patch.object(sincronizar_databricks, "retry") as retry,
        pytest.raises(DatabricksConfigurationError),
    ):
        sincronizar_databricks.run()

    retry.assert_not_called()


def test_task_reagenda_submissao_ambigua_com_backoff_bounded():
    retry_signal = RuntimeError("celery retry")
    retry = MagicMock(side_effect=retry_signal)
    with (
        patch(
            "app.workers.tasks.ingestao.asyncio.run",
            side_effect=_run_raising(DatabricksAmbiguousSubmissionError()),
        ),
        patch.object(sincronizar_databricks, "retry", retry),
        pytest.raises(RuntimeError, match="celery retry"),
    ):
        sincronizar_databricks.run()

    retry.assert_called_once()
    assert retry.call_args.kwargs["countdown"] == 15
    safe_error = retry.call_args.kwargs["exc"]
    assert type(safe_error).__name__ == "DatabricksError"
    assert "http" not in str(safe_error).lower()


@pytest.mark.asyncio
async def test_worker_periodico_cria_intencao_autoritativa_mesmo_com_broker_down():
    engine, factory = _engine_factory()
    job = replace(_claimed_job(), status=JobStatus.QUEUED, attempts=0)
    submission = JobSubmission(
        reservation=JobReservation(
            job=job,
            outcome=JobReservationOutcome.CREATED,
        ),
        broker_enqueued=False,
    )
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.ingestao.service.registrar_sincronizacao",
            new=AsyncMock(return_value=submission),
        ) as register,
    ):
        result = await worker._sincronizar(job_id=None)

    assert result["status"] == "queued"
    assert result["brokerEnqueued"] is False
    register_await_args = register.await_args
    assert register_await_args is not None
    assert register_await_args.kwargs["owner_id"] is None
    assert register_await_args.kwargs["idempotency_key"].startswith("periodic:")
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_erro_permanente_falha_job_sem_retry():
    engine, factory = _engine_factory()
    claimed = _claimed_job()
    error = DatabricksConfigurationError("configuração ausente")
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.ingestao.service.claim_job",
            new=AsyncMock(return_value=claimed),
        ),
        patch.object(
            worker,
            "_executar_com_heartbeat",
            new=AsyncMock(side_effect=error),
        ),
        patch(
            "app.modules.ingestao.service.fail_job",
            new=AsyncMock(return_value=replace(claimed, status=JobStatus.FAILED)),
        ) as fail,
        patch("app.modules.ingestao.service.retry_job", new=AsyncMock()) as retry,
        pytest.raises(DatabricksConfigurationError),
    ):
        await worker._sincronizar(job_id=str(_JOB_ID))

    fail_await_args = fail.await_args
    assert fail_await_args is not None
    assert fail_await_args.kwargs["error_code"] == "databricks_configuration"
    assert fail_await_args.kwargs["retryable"] is False
    retry.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_erro_transitorio_registra_retry_antes_do_celery():
    engine, factory = _engine_factory()
    claimed = _claimed_job(attempts=2)
    transitioned = replace(
        claimed,
        status=JobStatus.RETRYING,
        retryable=True,
        lease_owner=None,
        lease_expires_at=None,
    )
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.ingestao.service.claim_job",
            new=AsyncMock(return_value=claimed),
        ),
        patch.object(
            worker,
            "_executar_com_heartbeat",
            new=AsyncMock(side_effect=DatabricksAmbiguousSubmissionError()),
        ),
        patch(
            "app.modules.ingestao.service.retry_job",
            new=AsyncMock(return_value=transitioned),
        ) as retry,
        patch("app.modules.ingestao.service.fail_job", new=AsyncMock()) as fail,
        pytest.raises(DatabricksError) as raised,
    ):
        await worker._sincronizar(job_id=str(_JOB_ID))

    assert isinstance(raised.value, worker._RetryableJobDatabricksError)
    assert raised.value.retryable is True
    assert raised.value.countdown == 30
    retry_await_args = retry.await_args
    assert retry_await_args is not None
    assert retry_await_args.kwargs["error_code"] == "databricks_ambiguous_submission"
    fail.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_conflito_de_lock_marca_skipped_sem_retry_storm():
    from app.modules.ingestao.service import SincronizacaoEmAndamentoError

    engine, factory = _engine_factory()
    claimed = _claimed_job()
    skipped = replace(
        claimed,
        status=JobStatus.SKIPPED,
        lease_owner=None,
        lease_expires_at=None,
    )
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.ingestao.service.claim_job",
            new=AsyncMock(return_value=claimed),
        ),
        patch.object(
            worker,
            "_executar_com_heartbeat",
            new=AsyncMock(side_effect=SincronizacaoEmAndamentoError()),
        ),
        patch(
            "app.modules.ingestao.service.skip_job",
            new=AsyncMock(return_value=skipped),
        ) as skip,
        patch("app.modules.ingestao.service.retry_job", new=AsyncMock()) as retry,
    ):
        result = await worker._sincronizar(job_id=str(_JOB_ID))

    assert result == {"status": "skipped", "reason": "sync_already_running"}
    skip_await_args = skip.await_args
    assert skip_await_args is not None
    assert skip_await_args.kwargs["error_code"] == "sync_already_running"
    retry.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_perda_de_heartbeat_cancela_coleta_em_andamento():
    coleta_iniciada = asyncio.Event()
    coleta_cancelada = asyncio.Event()

    async def coleta_longa(*_args, **_kwargs):
        coleta_iniciada.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            coleta_cancelada.set()
            raise

    async def heartbeat_falha(*_args, **_kwargs):
        await coleta_iniciada.wait()
        raise worker._JobLeaseLostError("lease_lost")

    with (
        patch(
            "app.modules.ingestao.service.sincronizar_tudo",
            side_effect=coleta_longa,
        ),
        patch.object(
            worker,
            "_heartbeat_loop",
            new=AsyncMock(side_effect=heartbeat_falha),
        ),
        pytest.raises(worker._JobLeaseLostError),
    ):
        await worker._executar_com_heartbeat(
            cast("async_sessionmaker[AsyncSession]", _Factory()),
            job_id=_JOB_ID,
            worker_id="celery:worker",
            total_timeout_seconds=1800,
        )

    assert coleta_cancelada.is_set()
