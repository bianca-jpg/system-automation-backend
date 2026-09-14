"""Orquestração DDD do processamento; banco, broker e realtime são ports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from threading import Event
from uuid import UUID

from app.modules.pedidos.processing.application.ports import (
    ProcessingLeaseKeeper,
    ProcessingPlannerSource,
    ProcessingRealtime,
    ProcessingRepository,
    ProcessingSessionLockGuard,
    ProcessingUnitOfWork,
    ProcessingWriter,
)
from app.modules.pedidos.processing.domain import (
    APPLY_CHUNK_SIZE,
    PROCESSING_JOB_KIND,
    PROCESSING_LEASE,
    PROCESSING_RETRY_DELAY,
    PlanDraft,
    PlanningState,
    ProcessingLeaseLost,
    ProcessingRequest,
    ProcessingResult,
    ProcessingSpec,
    build_processing_plan,
    deterministic_alert_event_id,
    deterministic_completion_event_id,
    deterministic_progress_event_id,
    new_processing_job,
)
from app.shared.jobs.application.ports import (
    DurableJobDispatcher,
    DurableJobRepository,
)
from app.shared.jobs.domain import (
    DurableJob,
    JobReservationOutcome,
    JobStatus,
    JobSubmission,
    utc_now,
)

logger = logging.getLogger(__name__)
_CPU_MONITOR_INTERVAL_SECONDS = 0.5


async def _run_cpu_bound_monitored[T](
    operation: Callable[[Event], T],
    *,
    lease_keeper: ProcessingLeaseKeeper,
    lock_guard: ProcessingSessionLockGuard,
) -> T:
    """Cancela cooperativamente e espera a thread pura antes de liberar o lock."""

    cancel_token = Event()
    task = asyncio.create_task(asyncio.to_thread(operation, cancel_token))
    try:
        while not task.done():
            done, _pending = await asyncio.wait(
                {task},
                timeout=_CPU_MONITOR_INTERVAL_SECONDS,
            )
            if done:
                break
            await lock_guard.ensure_held()
            await lease_keeper.ensure_valid()
        return await task
    except BaseException:
        # O motor e o congelamento do payload checam o token em todos os loops
        # relevantes. Esperar aqui impede que uma thread órfã sobreviva ao
        # lease/deadline e concorra com uma retomada após a liberação do lock.
        cancel_token.set()
        # Sempre observa o resultado, inclusive se a task terminou entre o
        # check do monitor e este unwind. A falha do motor não substitui a
        # perda de lease/lock que iniciou o cancelamento.
        await asyncio.shield(asyncio.gather(task, return_exceptions=True))
        raise


async def submit_processing_job(
    *,
    jobs: DurableJobRepository,
    processing: ProcessingRepository,
    unit_of_work: ProcessingUnitOfWork,
    dispatcher: DurableJobDispatcher,
    owner_id: int,
    idempotency_key: str,
    request: ProcessingRequest,
    requested_at: datetime | None = None,
) -> JobSubmission:
    """Reserva ledger e header 1:1 atomicamente; broker é best effort pós-commit."""

    proposed = new_processing_job(
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request=request,
        requested_at=requested_at or utc_now(),
    )
    try:
        reservation = await jobs.reserve(proposed)
        if reservation.outcome is JobReservationOutcome.CREATED:
            await processing.create_spec(
                job_id=reservation.job.id,
                mode=request.mode,
                channel=request.channel,
            )
        await unit_of_work.commit()
    except BaseException:
        await unit_of_work.rollback()
        raise

    broker_enqueued = False
    if reservation.outcome is JobReservationOutcome.CREATED:
        try:
            await dispatcher.enqueue(
                job_id=reservation.job.id, kind=PROCESSING_JOB_KIND
            )
            broker_enqueued = True
        except Exception as exc:  # noqa: BLE001 - reconciler retries dispatch
            logger.warning(
                "orders processing dispatch deferred",
                extra={
                    "job_id": str(reservation.job.id),
                    "dispatch_error_type": type(exc).__name__,
                },
            )
    return JobSubmission(reservation=reservation, broker_enqueued=broker_enqueued)


async def get_processing_status(
    *,
    jobs: DurableJobRepository,
    processing: ProcessingRepository,
    job_id: UUID,
) -> tuple[DurableJob, ProcessingSpec] | None:
    job = await jobs.get(job_id)
    if job is None or job.kind != PROCESSING_JOB_KIND:
        return None
    spec = await processing.get_spec(job_id)
    if spec is None:
        raise RuntimeError("processing_job_header_missing")
    return job, spec


async def claim_processing_job(
    *,
    job_id: UUID,
    worker_id: str,
    jobs: DurableJobRepository,
    lock_guard: ProcessingSessionLockGuard,
    unit_of_work: ProcessingUnitOfWork,
    now: Callable[[], datetime] = utc_now,
) -> DurableJob | None:
    """Faz claim somente na sessão que já detém o lock global.

    Contenção esperada não toca o ledger nem consome ``attempts``: o worker
    deve entrar no session-lock antes de compor estes adapters.
    """

    claimed_at = now()
    try:
        await lock_guard.ensure_held()
        claimed = await jobs.claim(
            job_id=job_id,
            kinds=(PROCESSING_JOB_KIND,),
            worker_id=worker_id,
            now=claimed_at,
            lease_until=claimed_at + PROCESSING_LEASE,
        )
        await unit_of_work.commit()
        return claimed
    except BaseException:
        await unit_of_work.rollback()
        raise


async def _plan_once(
    *,
    job: DurableJob,
    worker_id: str,
    jobs: DurableJobRepository,
    processing: ProcessingRepository,
    planner: ProcessingPlannerSource,
    lease_keeper: ProcessingLeaseKeeper,
    lock_guard: ProcessingSessionLockGuard,
    unit_of_work: ProcessingUnitOfWork,
    now: Callable[[], datetime],
) -> ProcessingSpec:
    spec = await processing.get_spec(job.id, for_update=True)
    if spec is None:
        raise RuntimeError("processing_job_header_missing")
    if spec.planning_state is PlanningState.PLANNED:
        await unit_of_work.rollback()
        return spec

    await lock_guard.ensure_held()
    await lease_keeper.ensure_valid()
    stats = await planner.inspect_pending(spec.channel)

    pending_items = await planner.load_pending_items(spec.channel)
    hydrated_pairs = {
        (int(item["nr_pedido"]), str(item["cd_prod_cor"]).strip())
        for item in pending_items
    }
    if (
        len(pending_items) != stats.item_count
        or len(hydrated_pairs) != stats.pair_count
    ):
        raise RuntimeError("processing_snapshot_changed_during_planning")
    product_codes = {
        str(item.get("cd_prod_cor") or "").strip()
        for item in pending_items
        if item.get("cd_prod_cor") is not None
    }
    reference = await planner.load_size_reference(product_codes)
    # `stock`/`criterion`/`tolerance` passam a ser carregados nos dois modos:
    # SEM_ADEQUAR agora reserva estoque de verdade (tudo-ou-nada) pelo caminho
    # global, não só ADEQUAR.
    stock = await planner.load_stock(product_codes)
    criterion, tolerance = await planner.load_adequation_config()
    # Orçamento real (fecha ALOC-09): o escopo é o conjunto de pedidos da
    # foto já validada acima (pending_items), nunca um nr_pedido arbitrário.
    # Lido nos dois modos por decisão do usuário — ver comentário em
    # domain.py::build_processing_plan.
    order_ids = {int(item["nr_pedido"]) for item in pending_items}
    (
        total_original_por_pedido,
        consumido_previo_adicao_por_pedido,
        consumido_previo_corte_por_pedido,
    ) = await planner.load_pedido_budget(order_ids)
    processing_request = ProcessingRequest(mode=spec.mode, channel=spec.channel)
    # O motor é CPU-bound. Executá-lo em thread mantém o heartbeat independente
    # ativo mesmo no pior snapshot aceito pelo preflight.
    draft: PlanDraft = await _run_cpu_bound_monitored(
        lambda cancel_token: build_processing_plan(
            request=processing_request,
            pending_items=pending_items,
            reference=reference,
            stock=stock,
            criterion=criterion,
            tolerance=tolerance,
            cancel_token=cancel_token,
            total_original_por_pedido=total_original_por_pedido,
            consumido_previo_adicao_por_pedido=consumido_previo_adicao_por_pedido,
            consumido_previo_corte_por_pedido=consumido_previo_corte_por_pedido,
        ),
        lease_keeper=lease_keeper,
        lock_guard=lock_guard,
    )
    await lock_guard.ensure_held()
    await lease_keeper.ensure_valid()
    planned_at = now()
    spec = await processing.store_plan(
        job_id=job.id,
        draft=draft,
        planned_at=planned_at,
    )
    # A escrita do motivo tem de estar na MESMA transação de `store_plan` e
    # ANTES de `unit_of_work.commit()` (abaixo): `_plan_once` retorna cedo,
    # sem refazer nada, sempre que `planning_state` já é `PLANNED` (topo
    # desta função). Se o motivo fosse gravado depois do commit e o
    # processo caísse no meio, o plano ficaria congelado sem motivo nenhum
    # e nenhuma nova tentativa refaria a escrita perdida — perda silenciosa
    # e PERMANENTE (Pitfall 2 / risco 2 do 16-RESEARCH.md). `recorded_at`
    # reusa `planned_at`: motivo e plano são o mesmo instante lógico. Por
    # consequência, uma tentativa que faz rollback não infla
    # `execucoes_consecutivas` — o incremento só existe se o commit existir.
    await processing.record_standby_reasons(
        job_id=job.id,
        deferred_pairs=draft.deferred_pairs,
        blocked_credit_pairs=draft.blocked_credit_pairs,
        recorded_at=planned_at,
    )
    heartbeat_at = now()
    heartbeat = await jobs.heartbeat(
        job_id=job.id,
        worker_id=worker_id,
        now=heartbeat_at,
        lease_until=heartbeat_at + PROCESSING_LEASE,
        progress_current=0,
        progress_total=spec.planned_count,
    )
    if heartbeat is None:
        raise ProcessingLeaseLost("processing_job_lease_lost")
    await unit_of_work.commit()
    await lease_keeper.update_progress(
        progress_current=0,
        progress_total=spec.planned_count,
    )
    return spec


async def _apply_chunks(
    *,
    job: DurableJob,
    worker_id: str,
    jobs: DurableJobRepository,
    processing: ProcessingRepository,
    writer: ProcessingWriter,
    realtime: ProcessingRealtime,
    lease_keeper: ProcessingLeaseKeeper,
    lock_guard: ProcessingSessionLockGuard,
    unit_of_work: ProcessingUnitOfWork,
    now: Callable[[], datetime],
) -> ProcessingSpec:
    while True:
        await lock_guard.ensure_held()
        await lease_keeper.ensure_valid()
        rows = await processing.load_next_chunk(
            job_id=job.id,
            limit=APPLY_CHUNK_SIZE,
        )
        if not rows:
            spec = await processing.get_spec(job.id)
            if spec is None:
                raise RuntimeError("processing_job_header_missing")
            await unit_of_work.rollback()
            return spec

        try:
            await writer.apply_pairs(rows)
            checkpoint_at = now()
            spec = await processing.checkpoint_chunk(
                job_id=job.id,
                rows=rows,
                applied_at=checkpoint_at,
            )
            progress_event_at = now()
            await realtime.record_progress(
                event_id=deterministic_progress_event_id(
                    job.id,
                    spec.applied_count,
                ),
                occurred_at=progress_event_at,
                payload={
                    "jobId": str(job.id),
                    "mode": spec.mode.value,
                    "channel": spec.channel.value,
                    "progressCurrent": spec.applied_count,
                    "progressTotal": spec.planned_count,
                },
            )
            # O evento participa da mesma transação. O CAS temporal vem por
            # último para impedir que uma chamada realtime lenta faça um
            # checkpoint cruzar lease/deadline usando um relógio antigo.
            heartbeat_at = now()
            heartbeat = await jobs.heartbeat(
                job_id=job.id,
                worker_id=worker_id,
                now=heartbeat_at,
                lease_until=heartbeat_at + PROCESSING_LEASE,
                progress_current=spec.applied_count,
                progress_total=spec.planned_count,
            )
            if heartbeat is None:
                raise ProcessingLeaseLost("processing_job_lease_lost")
            await unit_of_work.commit()
            await lease_keeper.update_progress(
                progress_current=spec.applied_count,
                progress_total=spec.planned_count,
            )
        except BaseException:
            await unit_of_work.rollback()
            raise


async def run_claimed_processing_job(
    *,
    claimed: DurableJob,
    worker_id: str,
    jobs: DurableJobRepository,
    processing: ProcessingRepository,
    planner: ProcessingPlannerSource,
    writer: ProcessingWriter,
    realtime: ProcessingRealtime,
    lease_keeper: ProcessingLeaseKeeper,
    lock_guard: ProcessingSessionLockGuard,
    unit_of_work: ProcessingUnitOfWork,
    now: Callable[[], datetime] = utc_now,
) -> DurableJob | None:
    """Planeja/aplica um claim existente dentro de uma sessão globalmente locked."""

    await lock_guard.ensure_held()
    if (
        claimed.kind != PROCESSING_JOB_KIND
        or claimed.status is not JobStatus.RUNNING
        or claimed.lease_owner != worker_id
    ):
        raise ProcessingLeaseLost("processing_job_claim_invalid")
    job_id = claimed.id
    await lease_keeper.start(
        job_id=job_id,
        worker_id=worker_id,
        progress_current=claimed.progress_current,
        progress_total=claimed.progress_total,
    )
    try:
        spec = await _plan_once(
            job=claimed,
            worker_id=worker_id,
            jobs=jobs,
            processing=processing,
            planner=planner,
            lease_keeper=lease_keeper,
            lock_guard=lock_guard,
            unit_of_work=unit_of_work,
            now=now,
        )
        if not spec.complete:
            spec = await _apply_chunks(
                job=claimed,
                worker_id=worker_id,
                jobs=jobs,
                processing=processing,
                writer=writer,
                realtime=realtime,
                lease_keeper=lease_keeper,
                lock_guard=lock_guard,
                unit_of_work=unit_of_work,
                now=now,
            )
        if not spec.complete:
            raise RuntimeError("processing_checkpoint_incomplete")

        await lock_guard.ensure_held()
        await lease_keeper.ensure_valid()
        result = ProcessingResult(
            planned=spec.planned_count,
            applied=spec.applied_count,
            deferred=spec.deferred_count,
            blocked_credit=spec.blocked_credit_count,
        )
        alert_event_at = now()
        await realtime.reconcile_alerts(
            event_id=deterministic_alert_event_id(job_id),
            occurred_at=alert_event_at,
            reason="orders_processing_completed",
        )
        completion_event_at = now()
        await realtime.record_completion(
            event_id=deterministic_completion_event_id(job_id),
            occurred_at=completion_event_at,
            payload={
                "jobId": str(job_id),
                "mode": spec.mode.value,
                "channel": spec.channel.value,
                **result.as_dict(),
            },
        )
        await lock_guard.ensure_held()
        await lease_keeper.ensure_valid()
        # O timestamp local preserva o contrato do port; o adapter PostgreSQL
        # decide e grava a transição com clock_timestamp(). Falha remove os
        # eventos pela mesma transação.
        finished_at = now()
        completed = await jobs.succeed(
            job_id=job_id,
            worker_id=worker_id,
            now=finished_at,
            result=result.as_dict(),
        )
        if completed is None:
            await unit_of_work.rollback()
            raise ProcessingLeaseLost("processing_job_lease_lost")
        await unit_of_work.commit()
        return completed
    finally:
        await lease_keeper.stop()


async def mark_processing_failure(
    *,
    job_id: UUID,
    worker_id: str,
    jobs: DurableJobRepository,
    unit_of_work: ProcessingUnitOfWork,
    error_code: str,
    retryable: bool,
    now: Callable[[], datetime] = utc_now,
) -> DurableJob | None:
    """Persiste somente código seguro; detalhes da exceção nunca entram no ledger."""

    failed_at = now()
    try:
        if retryable:
            job = await jobs.retry(
                job_id=job_id,
                worker_id=worker_id,
                now=failed_at,
                retry_at=failed_at + PROCESSING_RETRY_DELAY,
                error_code=error_code,
            )
        else:
            job = await jobs.fail(
                job_id=job_id,
                worker_id=worker_id,
                now=failed_at,
                error_code=error_code,
                retryable=False,
            )
        await unit_of_work.commit()
        return job
    except BaseException:
        await unit_of_work.rollback()
        raise


def public_processing_result(job: DurableJob) -> dict[str, int] | None:
    if job.result is None:
        return None
    raw = dict(job.result)
    result = ProcessingResult(
        planned=int(raw.get("plannedCount", 0)),
        applied=int(raw.get("appliedCount", 0)),
        deferred=int(raw.get("deferredCount", 0)),
        blocked_credit=int(raw.get("blockedCreditCount", 0)),
    )
    return result.as_dict()


__all__ = [
    "claim_processing_job",
    "get_processing_status",
    "mark_processing_failure",
    "public_processing_result",
    "run_claimed_processing_job",
    "submit_processing_job",
]
