"""Use cases that preserve database authority across broker failures."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.shared.jobs.application.ports import (
    DurableJobDispatcher,
    DurableJobRepository,
    DurableJobUnitOfWork,
)
from app.shared.jobs.domain import (
    DEFAULT_RETENTION_DAYS,
    DISPATCHABLE_JOB_STATUSES,
    MAX_CLEANUP_BATCH,
    JobReservationOutcome,
    JobSubmission,
    NewJob,
    ReconcileResult,
)

logger = logging.getLogger(__name__)


async def submit_job(
    repository: DurableJobRepository,
    unit_of_work: DurableJobUnitOfWork,
    dispatcher: DurableJobDispatcher,
    proposed: NewJob,
) -> JobSubmission:
    """Commit the authoritative job before attempting best-effort dispatch.

    A broker outage deliberately does not roll back or mark the job failed. The
    caller can still return ``202`` and a reconciler will dispatch the queued
    row later.
    """

    try:
        reservation = await repository.reserve(proposed)
        await unit_of_work.commit()
    except Exception:
        await unit_of_work.rollback()
        raise

    broker_enqueued = False
    if (
        reservation.outcome is JobReservationOutcome.CREATED
        and reservation.job.status in DISPATCHABLE_JOB_STATUSES
    ):
        try:
            await dispatcher.enqueue(
                job_id=reservation.job.id,
                kind=reservation.job.kind,
            )
            broker_enqueued = True
        except Exception as exc:  # noqa: BLE001 - any broker failure is deferred
            logger.warning(
                "durable job dispatch deferred",
                extra={
                    "job_id": str(reservation.job.id),
                    "job_kind": reservation.job.kind,
                    "dispatch_error_type": type(exc).__name__,
                },
            )
    return JobSubmission(
        reservation=reservation,
        broker_enqueued=broker_enqueued,
    )


async def reconcile_and_dispatch(
    repository: DurableJobRepository,
    unit_of_work: DurableJobUnitOfWork,
    dispatcher: DurableJobDispatcher,
    *,
    now: datetime,
    retry_delay: timedelta = timedelta(seconds=5),
    limit: int = 100,
) -> ReconcileResult:
    """Sweep expired leases/deadlines, commit, then dispatch eligible rows."""

    if not 1 <= limit <= MAX_CLEANUP_BATCH:
        raise ValueError("job_reconcile_limit_out_of_range")
    try:
        reconciled = await repository.reconcile_expired(
            now=now,
            retry_at=now + retry_delay,
            limit=limit,
        )
        await unit_of_work.commit()
    except Exception:
        await unit_of_work.rollback()
        raise

    dispatchable = await repository.list_dispatchable(now=now, limit=limit)
    for job in dispatchable:
        try:
            await dispatcher.enqueue(job_id=job.id, kind=job.kind)
        except Exception as exc:  # noqa: BLE001 - any broker failure is deferred
            logger.warning(
                "durable job reconciliation dispatch deferred",
                extra={
                    "job_id": str(job.id),
                    "job_kind": job.kind,
                    "dispatch_error_type": type(exc).__name__,
                },
            )
    return reconciled


async def cleanup_retained_jobs(
    repository: DurableJobRepository,
    unit_of_work: DurableJobUnitOfWork,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    limit: int = MAX_CLEANUP_BATCH,
) -> int:
    if retention_days < DEFAULT_RETENTION_DAYS:
        raise ValueError("job_retention_cannot_be_less_than_30_days")
    if not 1 <= limit <= MAX_CLEANUP_BATCH:
        raise ValueError("job_cleanup_limit_out_of_range")
    try:
        removed = await repository.cleanup_terminal(
            retention=timedelta(days=retention_days),
            limit=limit,
        )
        await unit_of_work.commit()
        return removed
    except Exception:
        await unit_of_work.rollback()
        raise
