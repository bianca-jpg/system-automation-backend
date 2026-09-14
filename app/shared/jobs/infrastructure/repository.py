"""PostgreSQL durable-job adapter with leases and at-least-once claims."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.jobs.application.ports import DurableJobRepository
from app.shared.jobs.domain import (
    MAX_CLEANUP_BATCH,
    DurableJob,
    InvalidJobData,
    JobIdempotencyConflict,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
    NewJob,
    ReconcileResult,
    validate_error_code,
    validate_result,
    validate_worker_id,
)
from app.shared.jobs.infrastructure.models import (
    DurableJobIdempotencyBindingModel,
    DurableJobModel,
)

_DISPATCHABLE = (JobStatus.QUEUED.value, JobStatus.RETRYING.value)
_TERMINAL = (
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.SKIPPED.value,
)


def _ensure_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidJobData(f"{field}_must_be_timezone_aware")


def _bounded_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_CLEANUP_BATCH:
        raise InvalidJobData("job_batch_limit_out_of_range")
    return limit


def _database_clock_cte() -> Any:
    """Materializa um único instante autoritativo por statement PostgreSQL."""

    return select(func.clock_timestamp().label("value")).cte("durable_job_clock")


def _to_domain(row: DurableJobModel) -> DurableJob:
    return DurableJob(
        id=row.id,
        kind=row.kind,
        owner_id=row.owner_id,
        scope_key=row.scope_key,
        idempotency_digest=row.idempotency_key_hash,
        fingerprint=row.fingerprint,
        status=JobStatus(row.status),
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        progress_current=row.progress_current,
        progress_total=row.progress_total,
        result=row.result,
        error_code=row.error_code,
        retryable=row.retryable,
        requested_at=row.requested_at,
        available_at=row.available_at,
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        deadline_at=row.deadline_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyDurableJobRepository(DurableJobRepository):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def find_idempotent(self, proposed: NewJob) -> JobReservation | None:
        if proposed.idempotency_digest is None:
            return None
        owner_clause = (
            DurableJobIdempotencyBindingModel.owner_id.is_(None)
            if proposed.owner_id is None
            else DurableJobIdempotencyBindingModel.owner_id == proposed.owner_id
        )
        matched = (
            await self._db.execute(
                select(DurableJobIdempotencyBindingModel, DurableJobModel)
                .join(
                    DurableJobModel,
                    DurableJobModel.id == DurableJobIdempotencyBindingModel.job_id,
                )
                .where(
                    owner_clause,
                    DurableJobIdempotencyBindingModel.idempotency_key_hash
                    == proposed.idempotency_digest,
                )
            )
        ).one_or_none()
        if matched is None:
            return None
        binding, job = matched
        if (
            binding.kind != proposed.kind
            or binding.fingerprint != proposed.fingerprint
            or job.kind != proposed.kind
            or job.fingerprint != proposed.fingerprint
        ):
            raise JobIdempotencyConflict(job.id)
        return JobReservation(
            job=_to_domain(job),
            outcome=JobReservationOutcome.REPLAYED,
        )

    @staticmethod
    def _assert_compatible(existing: DurableJobModel, proposed: NewJob) -> None:
        if (
            existing.kind != proposed.kind
            or existing.fingerprint != proposed.fingerprint
        ):
            raise JobIdempotencyConflict(existing.id)

    async def _bind(
        self,
        *,
        proposed: NewJob,
        job: DurableJobModel,
        outcome: JobReservationOutcome,
    ) -> JobReservation:
        self._assert_compatible(job, proposed)
        if proposed.idempotency_digest is None:
            return JobReservation(job=_to_domain(job), outcome=outcome)

        inserted_id = await self._db.scalar(
            pg_insert(DurableJobIdempotencyBindingModel)
            .values(
                job_id=job.id,
                owner_id=proposed.owner_id,
                idempotency_key_hash=proposed.idempotency_digest,
                kind=proposed.kind,
                fingerprint=proposed.fingerprint,
            )
            .on_conflict_do_nothing()
            .returning(DurableJobIdempotencyBindingModel.id)
        )
        if inserted_id is not None:
            return JobReservation(job=_to_domain(job), outcome=outcome)

        replay = await self.find_idempotent(proposed)
        if replay is None:
            raise RuntimeError("durable_job_binding_conflict_not_visible")
        return replay

    async def _active_scope_match(self, proposed: NewJob) -> DurableJobModel | None:
        if proposed.scope_key is None:
            return None
        return await self._db.scalar(
            select(DurableJobModel)
            .where(
                DurableJobModel.kind == proposed.kind,
                DurableJobModel.scope_key == proposed.scope_key,
                DurableJobModel.status.in_(
                    (
                        JobStatus.QUEUED.value,
                        JobStatus.RUNNING.value,
                        JobStatus.RETRYING.value,
                    )
                ),
            )
            .order_by(DurableJobModel.requested_at, DurableJobModel.id)
        )

    async def reserve(self, proposed: NewJob) -> JobReservation:
        replay = await self.find_idempotent(proposed)
        if replay is not None:
            return replay
        active = await self._active_scope_match(proposed)
        if active is not None:
            return await self._bind(
                proposed=proposed,
                job=active,
                outcome=JobReservationOutcome.COALESCED,
            )

        database_now = await self._db.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise RuntimeError("durable_job_database_clock_missing")
        deadline_duration = (
            proposed.deadline_at - proposed.requested_at
            if proposed.deadline_at is not None
            else None
        )
        values: dict[str, object] = {
            "id": proposed.id,
            "kind": proposed.kind,
            "owner_id": proposed.owner_id,
            "scope_key": proposed.scope_key,
            "idempotency_key_hash": proposed.idempotency_digest,
            "fingerprint": proposed.fingerprint,
            "status": JobStatus.QUEUED.value,
            "attempts": 0,
            "max_attempts": proposed.max_attempts,
            "progress_current": 0,
            "retryable": False,
            "requested_at": database_now,
            "available_at": database_now,
            "deadline_at": (
                database_now + deadline_duration
                if deadline_duration is not None
                else None
            ),
            "updated_at": database_now,
        }
        # No conflict target is intentional: either an idempotency replay or an
        # active-scope coalescence may win the race.
        for _attempt in range(2):
            inserted_id = await self._db.scalar(
                pg_insert(DurableJobModel)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(DurableJobModel.id)
            )
            if inserted_id is not None:
                row = await self._db.get(DurableJobModel, inserted_id)
                if row is None:  # defensive: same transaction must see RETURNING row
                    raise RuntimeError("inserted_durable_job_not_visible")
                try:
                    reservation = await self._bind(
                        proposed=proposed,
                        job=row,
                        outcome=JobReservationOutcome.CREATED,
                    )
                except RuntimeError:
                    # Keep reserve atomic even when a caller catches the domain
                    # conflict and chooses to commit the surrounding session.
                    await self._db.execute(
                        delete(DurableJobModel).where(DurableJobModel.id == row.id)
                    )
                    raise
                if reservation.job.id != row.id:
                    # A concurrent request may have installed this key on an
                    # already-active job after our initial read. The losing job
                    # and any dependent rows must not escape this transaction.
                    await self._db.execute(
                        delete(DurableJobModel).where(DurableJobModel.id == row.id)
                    )
                return reservation

            replay = await self.find_idempotent(proposed)
            if replay is not None:
                return replay
            active = await self._active_scope_match(proposed)
            if active is not None:
                return await self._bind(
                    proposed=proposed,
                    job=active,
                    outcome=JobReservationOutcome.COALESCED,
                )
        raise RuntimeError("durable_job_reservation_conflict_not_visible")

    async def get(self, job_id: UUID) -> DurableJob | None:
        row = await self._db.get(DurableJobModel, job_id)
        return _to_domain(row) if row is not None else None

    async def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        job_id: UUID | None = None,
        kinds: tuple[str, ...] = (),
    ) -> DurableJob | None:
        validate_worker_id(worker_id)
        _ensure_aware(now, "now")
        _ensure_aware(lease_until, "lease_until")
        if lease_until <= now:
            raise InvalidJobData("job_lease_must_be_in_the_future")
        lease_duration = lease_until - now

        database_now = func.clock_timestamp()
        stmt = select(DurableJobModel).where(
            DurableJobModel.status.in_(_DISPATCHABLE),
            DurableJobModel.available_at <= database_now,
            DurableJobModel.attempts < DurableJobModel.max_attempts,
            or_(
                DurableJobModel.deadline_at.is_(None),
                DurableJobModel.deadline_at > database_now,
            ),
        )
        if job_id is not None:
            stmt = stmt.where(DurableJobModel.id == job_id)
        if kinds:
            stmt = stmt.where(DurableJobModel.kind.in_(kinds))
        row = await self._db.scalar(
            stmt.order_by(
                DurableJobModel.available_at,
                DurableJobModel.requested_at,
                DurableJobModel.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None

        clock = _database_clock_cte()
        authoritative_now = clock.c.value
        requested_lease = authoritative_now + lease_duration
        effective_lease = case(
            (
                DurableJobModel.deadline_at.is_not(None),
                func.least(requested_lease, DurableJobModel.deadline_at),
            ),
            else_=requested_lease,
        )
        updated_id = await self._db.scalar(
            update(DurableJobModel)
            .where(
                DurableJobModel.id == row.id,
                DurableJobModel.status.in_(_DISPATCHABLE),
                DurableJobModel.available_at <= authoritative_now,
                DurableJobModel.attempts < DurableJobModel.max_attempts,
                or_(
                    DurableJobModel.deadline_at.is_(None),
                    DurableJobModel.deadline_at > authoritative_now,
                ),
                effective_lease > authoritative_now,
            )
            .values(
                status=JobStatus.RUNNING.value,
                attempts=DurableJobModel.attempts + 1,
                started_at=func.coalesce(
                    DurableJobModel.started_at,
                    authoritative_now,
                ),
                heartbeat_at=authoritative_now,
                lease_owner=worker_id,
                lease_expires_at=effective_lease,
                error_code=None,
                retryable=False,
                updated_at=func.greatest(
                    DurableJobModel.updated_at,
                    authoritative_now,
                ),
            )
            .returning(DurableJobModel.id)
        )
        if updated_id is None:
            return None
        await self._db.refresh(row)
        return _to_domain(row)

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        progress_current: int,
        progress_total: int | None = None,
    ) -> DurableJob | None:
        validate_worker_id(worker_id)
        _ensure_aware(now, "now")
        _ensure_aware(lease_until, "lease_until")
        if lease_until <= now:
            raise InvalidJobData("job_lease_must_be_in_the_future")
        lease_duration = lease_until - now
        if progress_current < 0 or (
            progress_total is not None
            and (progress_total < 0 or progress_current > progress_total)
        ):
            raise InvalidJobData("invalid_job_progress")

        clock = _database_clock_cte()
        database_now = clock.c.value
        predicates = [
            DurableJobModel.id == job_id,
            DurableJobModel.status == JobStatus.RUNNING.value,
            DurableJobModel.lease_owner == worker_id,
            DurableJobModel.lease_expires_at > database_now,
            DurableJobModel.progress_current <= progress_current,
            or_(
                DurableJobModel.deadline_at.is_(None),
                DurableJobModel.deadline_at > database_now,
            ),
        ]
        requested_lease = database_now + lease_duration
        bounded_lease = case(
            (
                DurableJobModel.deadline_at.is_not(None),
                func.least(requested_lease, DurableJobModel.deadline_at),
            ),
            else_=requested_lease,
        )
        values: dict[str, object] = {
            "heartbeat_at": func.greatest(
                DurableJobModel.heartbeat_at,
                database_now,
            ),
            "lease_expires_at": func.greatest(
                DurableJobModel.lease_expires_at,
                bounded_lease,
            ),
            "progress_current": progress_current,
            "updated_at": func.greatest(
                DurableJobModel.updated_at,
                database_now,
            ),
        }
        if progress_total is not None:
            predicates.append(
                or_(
                    DurableJobModel.progress_total.is_(None),
                    DurableJobModel.progress_total == progress_total,
                )
            )
            values["progress_total"] = progress_total
        else:
            predicates.append(
                or_(
                    DurableJobModel.progress_total.is_(None),
                    DurableJobModel.progress_total >= progress_current,
                )
            )
        row = await self._db.scalar(
            update(DurableJobModel)
            .where(*predicates)
            .values(**values)
            .returning(DurableJobModel)
        )
        return _to_domain(row) if row is not None else None

    def _lease_cas(
        self,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        *,
        database_now: Any | None = None,
    ) -> tuple[Any, ...]:
        validate_worker_id(worker_id)
        _ensure_aware(now, "now")
        if database_now is None:
            database_now = func.clock_timestamp()
        return (
            DurableJobModel.id == job_id,
            DurableJobModel.status == JobStatus.RUNNING.value,
            DurableJobModel.lease_owner == worker_id,
            DurableJobModel.lease_expires_at > database_now,
            or_(
                DurableJobModel.deadline_at.is_(None),
                DurableJobModel.deadline_at > database_now,
            ),
        )

    async def succeed(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        result: Mapping[str, object] | None = None,
    ) -> DurableJob | None:
        safe_result = validate_result(result)
        clock = _database_clock_cte()
        database_now = clock.c.value
        row = await self._db.scalar(
            update(DurableJobModel)
            .where(
                *self._lease_cas(
                    job_id,
                    worker_id,
                    now,
                    database_now=database_now,
                )
            )
            .values(
                status=JobStatus.SUCCEEDED.value,
                progress_current=case(
                    (
                        DurableJobModel.progress_total.is_not(None),
                        DurableJobModel.progress_total,
                    ),
                    else_=DurableJobModel.progress_current,
                ),
                result=safe_result,
                error_code=None,
                retryable=False,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=database_now,
                updated_at=func.greatest(
                    DurableJobModel.updated_at,
                    database_now,
                ),
            )
            .returning(DurableJobModel)
        )
        return _to_domain(row) if row is not None else None

    async def fail(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        error_code: str,
        retryable: bool,
    ) -> DurableJob | None:
        safe_error = validate_error_code(error_code)
        clock = _database_clock_cte()
        database_now = clock.c.value
        row = await self._db.scalar(
            update(DurableJobModel)
            .where(
                *self._lease_cas(
                    job_id,
                    worker_id,
                    now,
                    database_now=database_now,
                )
            )
            .values(
                status=JobStatus.FAILED.value,
                result=None,
                error_code=safe_error,
                retryable=retryable,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=database_now,
                updated_at=func.greatest(
                    DurableJobModel.updated_at,
                    database_now,
                ),
            )
            .returning(DurableJobModel)
        )
        return _to_domain(row) if row is not None else None

    async def retry(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> DurableJob | None:
        safe_error = validate_error_code(error_code)
        _ensure_aware(retry_at, "retry_at")
        if retry_at < now:
            raise InvalidJobData("job_retry_cannot_be_in_the_past")

        retry_delay = retry_at - now
        clock = _database_clock_cte()
        database_now = clock.c.value
        effective_retry_at = database_now + retry_delay
        exhausted = DurableJobModel.attempts >= DurableJobModel.max_attempts
        misses_deadline = and_(
            DurableJobModel.deadline_at.is_not(None),
            DurableJobModel.deadline_at <= effective_retry_at,
        )
        terminal = or_(exhausted, misses_deadline)
        row = await self._db.scalar(
            update(DurableJobModel)
            .where(
                *self._lease_cas(
                    job_id,
                    worker_id,
                    now,
                    database_now=database_now,
                )
            )
            .values(
                status=case(
                    (terminal, JobStatus.FAILED.value),
                    else_=JobStatus.RETRYING.value,
                ),
                available_at=effective_retry_at,
                result=None,
                error_code=case(
                    (misses_deadline, "job_deadline_exceeded"),
                    (exhausted, "job_attempts_exhausted"),
                    else_=safe_error,
                ),
                retryable=case((terminal, False), else_=True),
                lease_owner=None,
                lease_expires_at=None,
                finished_at=case((terminal, database_now), else_=None),
                updated_at=func.greatest(
                    DurableJobModel.updated_at,
                    database_now,
                ),
            )
            .returning(DurableJobModel)
        )
        return _to_domain(row) if row is not None else None

    async def skip(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        error_code: str,
    ) -> DurableJob | None:
        safe_error = validate_error_code(error_code)
        clock = _database_clock_cte()
        database_now = clock.c.value
        row = await self._db.scalar(
            update(DurableJobModel)
            .where(
                *self._lease_cas(
                    job_id,
                    worker_id,
                    now,
                    database_now=database_now,
                )
            )
            .values(
                status=JobStatus.SKIPPED.value,
                result=None,
                error_code=safe_error,
                retryable=False,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=database_now,
                updated_at=func.greatest(
                    DurableJobModel.updated_at,
                    database_now,
                ),
            )
            .returning(DurableJobModel)
        )
        return _to_domain(row) if row is not None else None

    async def list_dispatchable(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[DurableJob, ...]:
        _ensure_aware(now, "now")
        database_now = func.clock_timestamp()
        rows = (
            await self._db.scalars(
                select(DurableJobModel)
                .where(
                    DurableJobModel.status.in_(_DISPATCHABLE),
                    DurableJobModel.available_at <= database_now,
                    DurableJobModel.attempts < DurableJobModel.max_attempts,
                    or_(
                        DurableJobModel.deadline_at.is_(None),
                        DurableJobModel.deadline_at > database_now,
                    ),
                )
                .order_by(
                    DurableJobModel.available_at,
                    DurableJobModel.requested_at,
                    DurableJobModel.id,
                )
                .limit(_bounded_limit(limit))
            )
        ).all()
        return tuple(_to_domain(row) for row in rows)

    async def reconcile_expired(
        self,
        *,
        now: datetime,
        retry_at: datetime,
        limit: int,
    ) -> ReconcileResult:
        _ensure_aware(now, "now")
        _ensure_aware(retry_at, "retry_at")
        if retry_at < now:
            raise InvalidJobData("job_retry_cannot_be_in_the_past")
        retry_delay = retry_at - now

        database_now = func.clock_timestamp()
        expired_running = and_(
            DurableJobModel.status == JobStatus.RUNNING.value,
            DurableJobModel.lease_expires_at <= database_now,
        )
        expired_pending = and_(
            DurableJobModel.status.in_(_DISPATCHABLE),
            DurableJobModel.deadline_at.is_not(None),
            DurableJobModel.deadline_at <= database_now,
        )
        rows = (
            await self._db.scalars(
                select(DurableJobModel)
                .where(or_(expired_running, expired_pending))
                .order_by(
                    case(
                        (
                            DurableJobModel.status == JobStatus.RUNNING.value,
                            DurableJobModel.lease_expires_at,
                        ),
                        else_=DurableJobModel.deadline_at,
                    ),
                    DurableJobModel.id,
                )
                .with_for_update(skip_locked=True)
                .limit(_bounded_limit(limit))
            )
        ).all()

        database_now_value = (
            await self._db.scalar(select(func.clock_timestamp())) if rows else None
        )
        effective_retry_at = (
            database_now_value + retry_delay
            if database_now_value is not None
            else retry_at
        )

        retrying: list[UUID] = []
        failed: list[UUID] = []
        skipped: list[UUID] = []
        for row in rows:
            was_running = row.status == JobStatus.RUNNING.value
            row.lease_owner = None
            row.lease_expires_at = None
            row.result = None
            if database_now_value is None:
                raise RuntimeError("durable_job_database_clock_missing")
            row.updated_at = database_now_value
            if not was_running:
                row.status = JobStatus.SKIPPED.value
                row.error_code = "job_deadline_exceeded"
                row.retryable = False
                row.finished_at = database_now_value
                skipped.append(row.id)
                continue

            deadline_missed = (
                row.deadline_at is not None and row.deadline_at <= effective_retry_at
            )
            if deadline_missed or row.attempts >= row.max_attempts:
                row.status = JobStatus.FAILED.value
                row.error_code = (
                    "job_deadline_exceeded"
                    if deadline_missed
                    else "job_attempts_exhausted"
                )
                row.retryable = False
                row.finished_at = database_now_value
                failed.append(row.id)
            else:
                row.status = JobStatus.RETRYING.value
                row.available_at = effective_retry_at
                row.error_code = "worker_lease_expired"
                row.retryable = True
                row.finished_at = None
                retrying.append(row.id)
        if rows:
            await self._db.flush()
        return ReconcileResult(
            retrying=tuple(retrying),
            failed=tuple(failed),
            skipped=tuple(skipped),
        )

    async def cleanup_terminal(
        self,
        *,
        retention: timedelta,
        limit: int,
    ) -> int:
        if retention <= timedelta(0):
            raise InvalidJobData("job_retention_must_be_positive")
        database_now = await self._db.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise RuntimeError("durable_job_database_clock_missing")
        database_before = database_now - retention
        candidate_ids = (
            select(DurableJobModel.id)
            .where(
                DurableJobModel.status.in_(_TERMINAL),
                DurableJobModel.finished_at < database_before,
            )
            .order_by(DurableJobModel.finished_at, DurableJobModel.id)
            .with_for_update(skip_locked=True)
            .limit(_bounded_limit(limit))
            .cte("terminal_jobs_to_delete")
        )
        deleted = list(
            (
                await self._db.scalars(
                    delete(DurableJobModel)
                    .where(DurableJobModel.id.in_(select(candidate_ids.c.id)))
                    .returning(DurableJobModel.id)
                )
            ).all()
        )
        return len(deleted)


class SqlAlchemyDurableJobUnitOfWork:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def commit(self) -> None:
        await self._db.commit()

    async def rollback(self) -> None:
        await self._db.rollback()
