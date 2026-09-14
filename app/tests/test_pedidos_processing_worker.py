"""Composição Celery do processamento durável de pedidos."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.pedidos import service as pedidos_service
from app.modules.pedidos.processing.application.ports import (
    ProcessingRepository,
    ProcessingUnitOfWork,
)
from app.modules.pedidos.processing.application.service import submit_processing_job
from app.modules.pedidos.processing.domain import (
    InvalidProcessingRequest,
    ProcessingChannel,
    ProcessingLeaseLost,
    ProcessingLockUnavailable,
    ProcessingMode,
    ProcessingPlanConflict,
    ProcessingPlanTooLarge,
    ProcessingRequest,
)
from app.shared.jobs.application.ports import DurableJobDispatcher, DurableJobRepository
from app.shared.jobs.domain import (
    DurableJob,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
)
from app.workers.celery_app import celery_app
from app.workers.tasks import pedidos as worker

_JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174000")


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return None


def _runtime():
    engine = SimpleNamespace(dispose=AsyncMock())
    session = object()
    factory = Mock(side_effect=lambda: _SessionContext(session))
    return engine, factory, session


def _asyncio_run_raising(error: BaseException):
    def raise_after_closing(coroutine):
        coroutine.close()
        raise error

    return raise_after_closing


class _HeldLock:
    def __init__(self, session: object, events: list[str]) -> None:
        self.session = session
        self.events = events

    async def __aenter__(self):
        self.events.append("lock")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.events.append("unlock")

    async def ensure_held(self):
        return None


def test_task_esta_registrada_com_deadlines_e_retries_bounded():
    task = celery_app.tasks["app.workers.tasks.pedidos.processar_pedidos"]

    assert task.name == worker.processar_pedidos.name
    assert task.max_retries == 2
    assert task.soft_time_limit == 15 * 60
    assert task.time_limit == 15 * 60 + 30


def test_wrapper_celery_reagenda_com_countdown_e_excecao_sanitizada():
    retry_signal = RuntimeError("celery-retry-signal")
    with (
        patch.object(
            worker.asyncio,
            "run",
            side_effect=_asyncio_run_raising(
                worker._RetryableProcessingTaskError("customer@example.invalid")
            ),
        ),
        patch.object(
            worker.processar_pedidos,
            "retry",
            side_effect=retry_signal,
        ) as retry,
        pytest.raises(RuntimeError, match="celery-retry-signal"),
    ):
        worker.processar_pedidos.run(str(_JOB_ID))

    retry.assert_called_once()
    assert retry.call_args.kwargs["countdown"] == 15
    assert str(retry.call_args.kwargs["exc"]) == "orders_processing_retry_scheduled"
    assert "customer@example.invalid" not in str(retry.call_args.kwargs["exc"])


@pytest.mark.parametrize(
    ("error", "safe_message"),
    [
        (
            worker._TerminalProcessingTaskError("customer@example.invalid"),
            "orders_processing_failed",
        ),
        (SoftTimeLimitExceeded(), "orders_processing_time_limit"),
    ],
)
def test_wrapper_celery_terminal_e_soft_limit_expoem_somente_erro_seguro(
    error,
    safe_message,
):
    with (
        patch.object(worker.asyncio, "run", side_effect=_asyncio_run_raising(error)),
        pytest.raises(RuntimeError, match=safe_message) as raised,
    ):
        worker.processar_pedidos.run(str(_JOB_ID))

    assert "customer@example.invalid" not in str(raised.value)


@pytest.mark.asyncio
async def test_composition_adquire_lock_antes_do_claim_e_usa_a_mesma_sessao():
    events: list[str] = []
    engine = object()
    factory = Mock()
    locked_session = object()
    lock = _HeldLock(locked_session, events)
    claimed = SimpleNamespace(id=_JOB_ID)
    completed = SimpleNamespace(id=_JOB_ID, status=JobStatus.SUCCEEDED)

    async def claim(**kwargs):
        events.append("claim")
        assert kwargs["jobs"]._db is locked_session
        assert kwargs["lock_guard"] is lock
        assert kwargs["unit_of_work"]._db is locked_session
        return claimed

    async def run(**kwargs):
        events.append("run")
        assert kwargs["claimed"] is claimed
        assert kwargs["jobs"]._db is locked_session
        assert kwargs["processing"]._db is locked_session
        assert kwargs["planner"]._db is locked_session
        assert kwargs["writer"]._db is locked_session
        assert kwargs["realtime"]._db is locked_session
        assert kwargs["unit_of_work"]._db is locked_session
        assert kwargs["lock_guard"] is lock
        return completed

    with (
        patch.object(
            pedidos_service,
            "PostgresOrderMutationSessionLock",
            return_value=lock,
        ),
        patch.object(pedidos_service, "claim_processing_job", side_effect=claim),
        patch.object(pedidos_service, "run_claimed_processing_job", side_effect=run),
    ):
        result = await pedidos_service.executar_job_processamento(
            engine=cast(AsyncEngine, engine),
            session_factory=factory,
            job_id=_JOB_ID,
            worker_id="orders:test",
        )

    assert result is completed
    assert events == ["lock", "claim", "run", "unlock"]


@pytest.mark.asyncio
async def test_composition_lock_busy_deixa_ledger_intocado():
    claim = AsyncMock()

    class _BusyLock:
        async def __aenter__(self):
            raise ProcessingLockUnavailable("order_state_mutation_lock_busy")

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    with (
        patch.object(
            pedidos_service,
            "PostgresOrderMutationSessionLock",
            return_value=_BusyLock(),
        ),
        patch.object(pedidos_service, "claim_processing_job", new=claim),
        pytest.raises(ProcessingLockUnavailable),
    ):
        await pedidos_service.executar_job_processamento(
            engine=cast(AsyncEngine, object()),
            session_factory=Mock(),
            job_id=_JOB_ID,
            worker_id="orders:test",
        )

    claim.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_preflight_de_replay_e_read_only_e_actor_scoped(found):
    session = object()
    factory = Mock(side_effect=lambda: _SessionContext(session))
    reservation = JobReservation(
        job=cast(DurableJob, SimpleNamespace(id=_JOB_ID)),
        outcome=JobReservationOutcome.REPLAYED,
    )
    repository = SimpleNamespace(
        find_idempotent=AsyncMock(return_value=reservation if found else None)
    )
    with (
        patch.object(pedidos_service, "async_session_factory", factory),
        patch.object(
            pedidos_service,
            "SqlAlchemyDurableJobRepository",
            return_value=repository,
        ) as repository_type,
    ):
        result = await pedidos_service.buscar_replay_job_processamento(
            owner_id=77,
            idempotency_key="pedido-job-0001",
            mode="adequar",
            channel="Todos",
        )

    repository_type.assert_called_once_with(session)
    proposed = repository.find_idempotent.await_args.args[0]
    assert proposed.owner_id == 77
    assert proposed.kind == "orders.processing.v1"
    assert proposed.scope_key == "adequar:Todos"
    assert proposed.idempotency_digest is not None
    if found:
        assert result is not None
        assert result.reservation is reservation
        assert result.broker_enqueued is False
    else:
        assert result is None


@pytest.mark.asyncio
async def test_submit_confirma_job_antes_do_broker_e_aceita_publish_falho(caplog):
    events: list[str] = []
    reservation = JobReservation(
        job=cast(DurableJob, SimpleNamespace(id=_JOB_ID)),
        outcome=JobReservationOutcome.CREATED,
    )

    async def reserve(_proposed):
        events.append("reserve")
        return reservation

    async def create_spec(**_kwargs):
        events.append("spec")

    async def commit():
        events.append("commit")

    async def rollback():
        events.append("rollback")

    async def enqueue(**_kwargs):
        events.append("broker")
        raise ConnectionError("redis://secret@example.invalid")

    submission = await submit_processing_job(
        jobs=cast(DurableJobRepository, SimpleNamespace(reserve=reserve)),
        processing=cast(ProcessingRepository, SimpleNamespace(create_spec=create_spec)),
        unit_of_work=cast(
            ProcessingUnitOfWork, SimpleNamespace(commit=commit, rollback=rollback)
        ),
        dispatcher=cast(DurableJobDispatcher, SimpleNamespace(enqueue=enqueue)),
        owner_id=77,
        idempotency_key="pedido-job-0001",
        request=ProcessingRequest(
            mode=ProcessingMode.ADEQUAR,
            channel=ProcessingChannel.TODOS,
        ),
    )

    assert submission.reservation is reservation
    assert submission.broker_enqueued is False
    assert events == ["reserve", "spec", "commit", "broker"]
    assert "secret@example.invalid" not in caplog.text


@pytest.mark.asyncio
async def test_job_id_invalido_e_rejeitado_sem_abrir_engine():
    with patch.object(worker, "_engine_and_factory") as runtime:
        result = await worker._processar("../segredo")

    assert result == {"status": "skipped", "reason": "invalid_job_id"}
    runtime.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completed", "expected"),
    [
        (
            SimpleNamespace(status=JobStatus.SUCCEEDED, id=_JOB_ID),
            {"status": "succeeded", "jobId": str(_JOB_ID)},
        ),
        (None, {"status": "skipped", "reason": "not_claimed"}),
    ],
)
async def test_worker_compoe_engine_factory_e_descarta_runtime(completed, expected):
    engine, factory, _session = _runtime()
    execute = AsyncMock(return_value=completed)
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.pedidos.service.executar_job_processamento",
            new=execute,
        ),
        patch.object(worker, "_worker_id", return_value="orders:test"),
    ):
        result = await worker._processar(str(_JOB_ID))

    assert result == expected
    execute.assert_awaited_once_with(
        engine=engine,
        session_factory=factory,
        job_id=_JOB_ID,
        worker_id="orders:test",
    )
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_code", "retryable", "transition_error"),
    [
        (
            SoftTimeLimitExceeded(),
            "processing_time_limit",
            True,
            worker._RetryableProcessingTaskError("retry"),
        ),
        (
            ProcessingPlanTooLarge("payload-proibido"),
            "processing_plan_too_large",
            False,
            worker._TerminalProcessingTaskError("terminal"),
        ),
        (
            InvalidProcessingRequest("payload-proibido"),
            "processing_source_invalid",
            False,
            worker._TerminalProcessingTaskError("terminal"),
        ),
        (
            ProcessingPlanConflict("payload-proibido"),
            "processing_plan_conflict",
            False,
            worker._TerminalProcessingTaskError("terminal"),
        ),
        (
            OperationalError("customer@example.invalid", {}, RuntimeError()),
            "processing_dependency_unavailable",
            True,
            worker._RetryableProcessingTaskError("retry"),
        ),
        (
            RuntimeError("customer@example.invalid"),
            "internal_error",
            False,
            worker._TerminalProcessingTaskError("terminal"),
        ),
    ],
)
async def test_worker_mapeia_falhas_para_codigos_seguros(
    error,
    error_code,
    retryable,
    transition_error,
    caplog,
):
    engine, factory, _session = _runtime()
    transition = AsyncMock(side_effect=transition_error)
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.pedidos.service.executar_job_processamento",
            new=AsyncMock(side_effect=error),
        ),
        patch.object(worker, "_transition_failure", new=transition),
        patch.object(worker, "_worker_id", return_value="orders:test"),
        pytest.raises(type(transition_error)),
    ):
        await worker._processar(str(_JOB_ID))

    transition.assert_awaited_once_with(
        factory,
        job_id=_JOB_ID,
        worker_id="orders:test",
        error_code=error_code,
        retryable=retryable,
    )
    engine.dispose.assert_awaited_once()
    assert "customer@example.invalid" not in caplog.text
    assert "payload-proibido" not in caplog.text


@pytest.mark.asyncio
async def test_lock_ocupado_reagenda_sem_claim_nem_transicao_do_ledger():
    engine, factory, _session = _runtime()
    transition = AsyncMock()
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.pedidos.service.executar_job_processamento",
            new=AsyncMock(
                side_effect=ProcessingLockUnavailable("order_state_mutation_lock_busy")
            ),
        ),
        patch.object(worker, "_transition_failure", new=transition),
        pytest.raises(worker._RetryableProcessingTaskError),
    ):
        await worker._processar(str(_JOB_ID))

    transition.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lease_perdido_nao_tenta_cas_com_owner_obsoleto():
    engine, factory, _session = _runtime()
    transition = AsyncMock()
    with (
        patch.object(worker, "_engine_and_factory", return_value=(engine, factory)),
        patch(
            "app.modules.pedidos.service.executar_job_processamento",
            new=AsyncMock(side_effect=ProcessingLeaseLost("segredo")),
        ),
        patch.object(worker, "_transition_failure", new=transition),
        pytest.raises(worker._RetryableProcessingTaskError),
    ):
        await worker._processar(str(_JOB_ID))

    transition.assert_not_awaited()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transitioned", "expected_error"),
    [
        (
            SimpleNamespace(status=JobStatus.RETRYING),
            worker._RetryableProcessingTaskError,
        ),
        (None, worker._RetryableProcessingTaskError),
        (
            SimpleNamespace(status=JobStatus.FAILED),
            worker._TerminalProcessingTaskError,
        ),
    ],
)
async def test_transicao_persistida_define_retry_celery_ou_terminal(
    transitioned,
    expected_error,
):
    _engine, factory, session = _runtime()
    mark = AsyncMock(return_value=transitioned)
    with (
        patch(
            "app.modules.pedidos.service.marcar_falha_job_processamento",
            new=mark,
        ),
        pytest.raises(expected_error),
    ):
        await worker._transition_failure(
            factory,
            job_id=_JOB_ID,
            worker_id="orders:test",
            error_code="order_state_lock_busy",
            retryable=True,
        )

    mark.assert_awaited_once_with(
        session,
        job_id=_JOB_ID,
        worker_id="orders:test",
        error_code="order_state_lock_busy",
        retryable=True,
    )


@pytest.mark.asyncio
async def test_falha_ao_persistir_transicao_e_retryable_sem_vazar_excecao(caplog):
    _engine, factory, _session = _runtime()
    with (
        patch(
            "app.modules.pedidos.service.marcar_falha_job_processamento",
            new=AsyncMock(side_effect=RuntimeError("customer@example.invalid")),
        ),
        pytest.raises(worker._RetryableProcessingTaskError),
    ):
        await worker._transition_failure(
            factory,
            job_id=_JOB_ID,
            worker_id="orders:test",
            error_code="internal_error",
            retryable=False,
        )

    assert "customer@example.invalid" not in caplog.text
