from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.shared.config.settings import get_settings
from app.shared.jobs.domain import (
    JobIdempotencyConflict,
    JobReservationOutcome,
    JobStatus,
    NewJob,
    digest_idempotency_key,
    fingerprint_payload,
)
from app.shared.jobs.infrastructure.models import (
    DurableJobIdempotencyBindingModel,
    DurableJobModel,
)
from app.shared.jobs.infrastructure.repository import SqlAlchemyDurableJobRepository


@pytest.fixture
async def job_database():
    database_url = get_settings().database_url
    assert database_url.rsplit("/", 1)[-1].endswith("_test")
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(DurableJobModel))
        await session.commit()
    try:
        yield factory
    finally:
        async with factory() as session:
            await session.execute(delete(DurableJobModel))
            await session.commit()
        await engine.dispose()


def _new_job(
    *,
    kind: str = "ingestion.sync",
    scope: str | None = "global",
    raw_key: str | None = None,
    fingerprint: str | None = None,
    max_attempts: int = 3,
    requested_at: datetime | None = None,
    deadline_at: datetime | None = None,
    owner_id: int | None = 42,
) -> NewJob:
    return NewJob(
        kind=kind,
        owner_id=owner_id,
        scope_key=scope,
        idempotency_digest=(
            digest_idempotency_key(f"{kind}:{raw_key}") if raw_key is not None else None
        ),
        fingerprint=fingerprint or fingerprint_payload({"kind": kind, "v": 1}),
        max_attempts=max_attempts,
        requested_at=requested_at or datetime.now(UTC),
        deadline_at=deadline_at,
    )


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(value, datetime)
    return value


async def test_replay_conflict_and_same_raw_key_across_kinds(job_database) -> None:
    raw_key = f"same-{uuid.uuid4()}"
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        first = await repository.reserve(_new_job(raw_key=raw_key))
        await session.commit()
    assert first.outcome is JobReservationOutcome.CREATED

    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        replay = await repository.reserve(_new_job(raw_key=raw_key))
        different_kind = await repository.reserve(
            _new_job(
                kind="orders.processing",
                scope="channel:Franquia",
                raw_key=raw_key,
            )
        )
        await session.commit()
    assert replay.outcome is JobReservationOutcome.REPLAYED
    assert replay.job.id == first.job.id
    assert different_kind.outcome is JobReservationOutcome.CREATED

    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        with pytest.raises(JobIdempotencyConflict):
            await repository.reserve(
                _new_job(
                    raw_key=raw_key,
                    fingerprint=fingerprint_payload({"kind": "ingestion.sync", "v": 2}),
                )
            )


async def test_find_idempotent_is_read_only_and_validates_fingerprint(
    job_database,
) -> None:
    raw_key = f"pre-rate-limit-{uuid.uuid4()}"
    async with job_database() as session:
        created = await SqlAlchemyDurableJobRepository(session).reserve(
            _new_job(scope=None, raw_key=raw_key)
        )
        await session.commit()

    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        replay = await repository.find_idempotent(_new_job(scope=None, raw_key=raw_key))
        missing = await repository.find_idempotent(
            _new_job(scope=None, raw_key=f"missing-{uuid.uuid4()}")
        )
        with pytest.raises(JobIdempotencyConflict):
            await repository.find_idempotent(
                _new_job(
                    scope=None,
                    raw_key=raw_key,
                    fingerprint=fingerprint_payload(
                        {"kind": "ingestion.sync", "v": 999}
                    ),
                )
            )
        job_count = int(
            await session.scalar(select(func.count()).select_from(DurableJobModel)) or 0
        )
        binding_count = int(
            await session.scalar(
                select(func.count()).select_from(DurableJobIdempotencyBindingModel)
            )
            or 0
        )

    assert replay is not None
    assert replay.outcome is JobReservationOutcome.REPLAYED
    assert replay.job.id == created.job.id
    assert missing is None
    assert job_count == binding_count == 1


async def test_active_scope_is_coalesced(job_database) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        first = await repository.reserve(_new_job(owner_id=41, raw_key="first"))
        second = await repository.reserve(_new_job(owner_id=42, raw_key="second"))
        now = datetime.now(UTC)
        claimed = await repository.claim(
            job_id=first.job.id,
            worker_id="worker-coalesced",
            now=now,
            lease_until=now + timedelta(minutes=1),
        )
        assert claimed is not None
        completed = await repository.succeed(
            job_id=first.job.id,
            worker_id="worker-coalesced",
            now=now + timedelta(seconds=1),
            result={"processed": 1},
        )
        assert completed is not None
        await session.commit()

    assert first.outcome is JobReservationOutcome.CREATED
    assert second.outcome is JobReservationOutcome.COALESCED
    assert second.job.id == first.job.id

    # The second actor's binding survives completion, so a lost 202 response
    # cannot turn the same Idempotency-Key into another operation.
    async with job_database() as session:
        replay = await SqlAlchemyDurableJobRepository(session).reserve(
            _new_job(owner_id=42, raw_key="second")
        )
    assert replay.outcome is JobReservationOutcome.REPLAYED
    assert replay.job.id == first.job.id
    assert replay.job.status is JobStatus.SUCCEEDED


async def test_active_scope_with_different_fingerprint_is_not_coalesced(
    job_database,
) -> None:
    incompatible = fingerprint_payload({"kind": "ingestion.sync", "v": 2})
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        existing = await repository.reserve(_new_job(owner_id=41, raw_key="first"))
        with pytest.raises(JobIdempotencyConflict) as raised:
            await repository.reserve(
                _new_job(
                    owner_id=42,
                    raw_key="incompatible",
                    fingerprint=incompatible,
                )
            )
        binding_count = int(
            await session.scalar(
                select(func.count())
                .select_from(DurableJobIdempotencyBindingModel)
                .where(DurableJobIdempotencyBindingModel.owner_id == 42)
            )
            or 0
        )

    assert raised.value.existing_job_id == existing.job.id
    assert binding_count == 0


async def test_concurrent_lost_response_replays_single_created_mapping(
    job_database,
) -> None:
    raw_key = f"concurrent-{uuid.uuid4()}"
    first_ready = asyncio.Event()
    release_first = asyncio.Event()

    async def reserve_first():
        async with job_database() as session:
            reservation = await SqlAlchemyDurableJobRepository(session).reserve(
                _new_job(scope=None, raw_key=raw_key)
            )
            first_ready.set()
            await release_first.wait()
            await session.commit()
            return reservation

    async def reserve_replay():
        await first_ready.wait()
        async with job_database() as session:
            reservation = await SqlAlchemyDurableJobRepository(session).reserve(
                _new_job(scope=None, raw_key=raw_key)
            )
            await session.commit()
            return reservation

    first_task = asyncio.create_task(reserve_first())
    await first_ready.wait()
    replay_task = asyncio.create_task(reserve_replay())
    await asyncio.sleep(0.05)
    assert not replay_task.done()
    release_first.set()
    first, replay = await asyncio.gather(first_task, replay_task)

    assert first.outcome is JobReservationOutcome.CREATED
    assert replay.outcome is JobReservationOutcome.REPLAYED
    assert replay.job.id == first.job.id
    async with job_database() as session:
        job_count = int(
            await session.scalar(select(func.count()).select_from(DurableJobModel)) or 0
        )
        binding_count = int(
            await session.scalar(
                select(func.count()).select_from(DurableJobIdempotencyBindingModel)
            )
            or 0
        )
    assert job_count == binding_count == 1


async def test_concurrent_different_keys_coalesce_and_both_remain_bound(
    job_database,
) -> None:
    first_ready = asyncio.Event()
    release_first = asyncio.Event()

    async def reserve_creator():
        async with job_database() as session:
            reservation = await SqlAlchemyDurableJobRepository(session).reserve(
                _new_job(owner_id=41, raw_key="creator")
            )
            first_ready.set()
            await release_first.wait()
            await session.commit()
            return reservation

    async def reserve_coalesced():
        await first_ready.wait()
        async with job_database() as session:
            reservation = await SqlAlchemyDurableJobRepository(session).reserve(
                _new_job(owner_id=42, raw_key="coalesced")
            )
            await session.commit()
            return reservation

    creator_task = asyncio.create_task(reserve_creator())
    await first_ready.wait()
    coalesced_task = asyncio.create_task(reserve_coalesced())
    await asyncio.sleep(0.05)
    assert not coalesced_task.done()
    release_first.set()
    creator, coalesced = await asyncio.gather(creator_task, coalesced_task)

    assert creator.outcome is JobReservationOutcome.CREATED
    assert coalesced.outcome is JobReservationOutcome.COALESCED
    assert coalesced.job.id == creator.job.id
    async with job_database() as session:
        bindings = list(
            (
                await session.scalars(
                    select(DurableJobIdempotencyBindingModel).order_by(
                        DurableJobIdempotencyBindingModel.owner_id
                    )
                )
            ).all()
        )
    assert [binding.owner_id for binding in bindings] == [41, 42]
    assert {binding.job_id for binding in bindings} == {creator.job.id}


async def test_created_job_and_binding_roll_back_atomically(job_database) -> None:
    proposed = _new_job(scope=None, raw_key=f"rollback-{uuid.uuid4()}")
    async with job_database() as session:
        created = await SqlAlchemyDurableJobRepository(session).reserve(proposed)
        assert created.outcome is JobReservationOutcome.CREATED
        binding = await session.scalar(
            select(DurableJobIdempotencyBindingModel).where(
                DurableJobIdempotencyBindingModel.job_id == created.job.id
            )
        )
        assert binding is not None
        await session.rollback()

    async with job_database() as session:
        assert await session.get(DurableJobModel, created.job.id) is None
        binding = await session.scalar(
            select(DurableJobIdempotencyBindingModel).where(
                DurableJobIdempotencyBindingModel.idempotency_key_hash
                == proposed.idempotency_digest
            )
        )
    assert binding is None


async def test_system_job_idempotency_is_scoped_by_periodic_window(
    job_database,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        first = await repository.reserve(
            _new_job(owner_id=None, scope=None, raw_key="window:2026-08-08T07:00Z")
        )
        replay = await repository.reserve(
            _new_job(owner_id=None, scope=None, raw_key="window:2026-08-08T07:00Z")
        )
        next_window = await repository.reserve(
            _new_job(owner_id=None, scope=None, raw_key="window:2026-08-08T08:00Z")
        )
        await session.commit()

    assert first.outcome is JobReservationOutcome.CREATED
    assert replay.outcome is JobReservationOutcome.REPLAYED
    assert replay.job.id == first.job.id
    assert next_window.outcome is JobReservationOutcome.CREATED


async def test_skip_locked_and_lease_owner_prevent_duplicate_completion(
    job_database,
) -> None:
    async with job_database() as session:
        created = await SqlAlchemyDurableJobRepository(session).reserve(
            _new_job(scope=None)
        )
        await session.commit()

    first = job_database()
    second = job_database()
    now = datetime.now(UTC)
    try:
        claimed = await SqlAlchemyDurableJobRepository(first).claim(
            job_id=created.job.id,
            worker_id="worker-one",
            now=now,
            lease_until=now + timedelta(minutes=1),
        )
        duplicate = await SqlAlchemyDurableJobRepository(second).claim(
            job_id=created.job.id,
            worker_id="worker-two",
            now=now,
            lease_until=now + timedelta(minutes=1),
        )
        assert claimed is not None and claimed.attempts == 1
        assert duplicate is None
        stale = await SqlAlchemyDurableJobRepository(first).succeed(
            job_id=created.job.id,
            worker_id="worker-two",
            now=now + timedelta(seconds=1),
            result={"processed": 1},
        )
        assert stale is None
    finally:
        await first.rollback()
        await second.rollback()
        await first.close()
        await second.close()


async def test_retry_promotes_to_failed_when_attempts_are_exhausted(
    job_database,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        created = await repository.reserve(_new_job(scope=None, max_attempts=1))
        now = datetime.now(UTC)
        claimed = await repository.claim(
            job_id=created.job.id,
            worker_id="worker-one",
            now=now,
            lease_until=now + timedelta(minutes=1),
        )
        assert claimed is not None and claimed.attempts == 1
        retried = await repository.retry(
            job_id=created.job.id,
            worker_id="worker-one",
            now=now + timedelta(seconds=1),
            retry_at=now + timedelta(seconds=5),
            error_code="source_unavailable",
        )
        await session.commit()

    assert retried is not None
    assert retried.status is JobStatus.FAILED
    assert retried.error_code == "job_attempts_exhausted"
    assert retried.retryable is False


async def test_heartbeat_is_monotonic_and_completion_is_lease_guarded(
    job_database,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        created = await repository.reserve(_new_job(scope=None))
        now = datetime.now(UTC)
        claimed = await repository.claim(
            job_id=created.job.id,
            worker_id="worker-progress",
            now=now,
            lease_until=now + timedelta(seconds=30),
        )
        assert claimed is not None
        heartbeat = await repository.heartbeat(
            job_id=created.job.id,
            worker_id="worker-progress",
            now=now + timedelta(seconds=1),
            lease_until=now + timedelta(seconds=60),
            progress_current=1,
            progress_total=2,
        )
        regressed = await repository.heartbeat(
            job_id=created.job.id,
            worker_id="worker-progress",
            now=now + timedelta(seconds=2),
            lease_until=now + timedelta(seconds=60),
            progress_current=0,
            progress_total=2,
        )
        succeeded = await repository.succeed(
            job_id=created.job.id,
            worker_id="worker-progress",
            now=now + timedelta(seconds=3),
            result={"processed": 2},
        )
        stale = await repository.succeed(
            job_id=created.job.id,
            worker_id="worker-progress",
            now=now + timedelta(seconds=4),
            result={"processed": 2},
        )
        await session.commit()

    assert heartbeat is not None
    assert heartbeat.progress_current == 1 and heartbeat.progress_total == 2
    assert regressed is None
    assert succeeded is not None and succeeded.status is JobStatus.SUCCEEDED
    assert succeeded.progress_current == succeeded.progress_total == 2
    assert stale is None


@pytest.mark.parametrize("app_skew", [timedelta(days=-1), timedelta(days=1)])
async def test_reserve_anchors_request_and_deadline_to_database_clock(
    job_database,
    app_skew: timedelta,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        before_reserve = await _database_now(session)
        app_requested_at = before_reserve + app_skew
        reservation = await repository.reserve(
            _new_job(
                scope=None,
                requested_at=app_requested_at,
                deadline_at=app_requested_at + timedelta(minutes=15),
            )
        )
        after_reserve = await _database_now(session)
        dispatchable = await repository.list_dispatchable(
            now=app_requested_at,
            limit=10,
        )
        await session.rollback()

    job = reservation.job
    assert before_reserve <= job.requested_at <= after_reserve
    assert job.available_at == job.updated_at == job.requested_at
    assert job.deadline_at is not None
    assert before_reserve + timedelta(minutes=15) <= job.deadline_at
    assert job.deadline_at <= after_reserve + timedelta(minutes=15)
    assert [candidate.id for candidate in dispatchable] == [job.id]


async def test_claim_list_and_heartbeat_use_database_clock_under_app_skew(
    job_database,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        ready = await repository.reserve(
            _new_job(
                scope="clock-ready",
                requested_at=database_now - timedelta(minutes=1),
                deadline_at=database_now + timedelta(minutes=2),
            )
        )
        future = await repository.reserve(
            _new_job(
                scope="clock-future",
                requested_at=database_now + timedelta(minutes=1),
            )
        )
        expired = await repository.reserve(
            _new_job(
                scope="clock-expired",
                requested_at=database_now - timedelta(minutes=2),
                deadline_at=database_now - timedelta(minutes=1),
            )
        )
        future_at = database_now + timedelta(minutes=1)
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == future.job.id)
            .values(
                requested_at=future_at,
                available_at=future_at,
                updated_at=future_at,
            )
        )
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == expired.job.id)
            .values(
                requested_at=database_now - timedelta(minutes=2),
                available_at=database_now - timedelta(minutes=2),
                deadline_at=database_now - timedelta(minutes=1),
                updated_at=database_now - timedelta(minutes=2),
            )
        )

        listed_with_future_app = await repository.list_dispatchable(
            now=database_now + timedelta(days=1),
            limit=10,
        )
        listed_with_stale_app = await repository.list_dispatchable(
            now=database_now - timedelta(days=1),
            limit=10,
        )
        assert {job.id for job in listed_with_future_app} == {ready.job.id}
        assert {job.id for job in listed_with_stale_app} == {ready.job.id}

        not_yet_available = await repository.claim(
            job_id=future.job.id,
            worker_id="worker-future-clock",
            now=database_now + timedelta(days=1),
            lease_until=database_now + timedelta(days=1, minutes=1),
        )
        deadline_passed = await repository.claim(
            job_id=expired.job.id,
            worker_id="worker-expired-clock",
            now=database_now - timedelta(days=1),
            lease_until=database_now - timedelta(days=1) + timedelta(minutes=1),
        )
        assert not_yet_available is None
        assert deadline_passed is None

        before_claim = await _database_now(session)
        stale_app_now = database_now - timedelta(days=1)
        claimed = await repository.claim(
            job_id=ready.job.id,
            worker_id="worker-ready-clock",
            now=stale_app_now,
            lease_until=stale_app_now + timedelta(seconds=60),
        )
        after_claim = await _database_now(session)
        assert claimed is not None
        assert claimed.started_at is not None and claimed.lease_expires_at is not None
        assert before_claim <= claimed.started_at <= after_claim
        assert before_claim + timedelta(seconds=59) <= claimed.lease_expires_at
        assert claimed.lease_expires_at <= after_claim + timedelta(seconds=61)

        before_heartbeat = await _database_now(session)
        future_app_now = database_now + timedelta(days=1)
        heartbeat = await repository.heartbeat(
            job_id=ready.job.id,
            worker_id="worker-ready-clock",
            now=future_app_now,
            lease_until=future_app_now + timedelta(seconds=90),
            progress_current=1,
            progress_total=1,
        )
        after_heartbeat = await _database_now(session)
        await session.rollback()

    assert heartbeat is not None
    assert heartbeat.heartbeat_at is not None and heartbeat.lease_expires_at is not None
    assert before_heartbeat <= heartbeat.heartbeat_at <= after_heartbeat
    assert before_heartbeat + timedelta(seconds=89) <= heartbeat.lease_expires_at
    assert heartbeat.lease_expires_at <= after_heartbeat + timedelta(seconds=91)


@pytest.mark.parametrize("app_skew", [timedelta(days=-1), timedelta(days=1)])
async def test_retry_backoff_is_anchored_to_database_clock(
    job_database,
    app_skew: timedelta,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        created = await repository.reserve(
            _new_job(
                scope=None,
                requested_at=database_now - timedelta(minutes=1),
                deadline_at=database_now + timedelta(minutes=5),
            )
        )
        app_now = database_now + app_skew
        claimed = await repository.claim(
            job_id=created.job.id,
            worker_id="worker-retry-clock",
            now=app_now,
            lease_until=app_now + timedelta(minutes=2),
        )
        assert claimed is not None

        before_retry = await _database_now(session)
        retried = await repository.retry(
            job_id=created.job.id,
            worker_id="worker-retry-clock",
            now=app_now,
            retry_at=app_now + timedelta(seconds=15),
            error_code="source_unavailable",
        )
        after_retry = await _database_now(session)
        await session.rollback()

    assert retried is not None and retried.status is JobStatus.RETRYING
    assert before_retry + timedelta(seconds=14) <= retried.available_at
    assert retried.available_at <= after_retry + timedelta(seconds=16)


@pytest.mark.parametrize("expired_guard", ["lease", "deadline"])
@pytest.mark.parametrize("transition", ["succeed", "fail", "retry", "skip"])
async def test_worker_transitions_use_database_clock_against_stale_app_time(
    job_database,
    transition: str,
    expired_guard: str,
) -> None:
    worker_id = f"worker-{expired_guard}-{transition}"
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        requested_at = database_now - timedelta(minutes=2)
        deadline_at = (
            database_now + timedelta(minutes=2) if expired_guard == "deadline" else None
        )
        created = await repository.reserve(
            _new_job(
                scope=None,
                requested_at=requested_at,
                deadline_at=deadline_at,
            )
        )
        claimed = await repository.claim(
            job_id=created.job.id,
            worker_id=worker_id,
            now=database_now,
            lease_until=database_now + timedelta(minutes=1),
        )
        assert claimed is not None

        expired_values = {
            "requested_at": requested_at,
            "available_at": database_now,
            **(
                {
                    "started_at": requested_at + timedelta(seconds=1),
                    "heartbeat_at": requested_at + timedelta(seconds=2),
                    "lease_expires_at": requested_at + timedelta(seconds=30),
                }
                if expired_guard == "lease"
                else {
                    "deadline_at": database_now - timedelta(minutes=1),
                    "lease_expires_at": database_now + timedelta(minutes=1),
                }
            ),
        }
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == created.job.id)
            .values(**expired_values)
        )
        stale_now = requested_at + timedelta(seconds=2)
        common = {
            "job_id": created.job.id,
            "worker_id": worker_id,
            "now": stale_now,
        }
        if transition == "succeed":
            result = await repository.succeed(**common, result={"processed": 1})
        elif transition == "fail":
            result = await repository.fail(
                **common,
                error_code="processing_failed",
                retryable=False,
            )
        elif transition == "retry":
            result = await repository.retry(
                **common,
                retry_at=stale_now + timedelta(seconds=1),
                error_code="processing_retry",
            )
        else:
            result = await repository.skip(
                **common,
                error_code="processing_skipped",
            )
        current = await repository.get(created.job.id)
        await session.rollback()

    assert result is None
    assert current is not None
    assert current.status is JobStatus.RUNNING


@pytest.mark.parametrize("expired_guard", ["lease", "deadline"])
async def test_heartbeat_cannot_revive_expired_guard_with_stale_app_time(
    job_database,
    expired_guard: str,
) -> None:
    worker_id = f"worker-heartbeat-{expired_guard}"
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        requested_at = database_now - timedelta(minutes=2)
        deadline_at = (
            database_now + timedelta(minutes=2) if expired_guard == "deadline" else None
        )
        created = await repository.reserve(
            _new_job(
                scope=None,
                requested_at=requested_at,
                deadline_at=deadline_at,
            )
        )
        claimed = await repository.claim(
            job_id=created.job.id,
            worker_id=worker_id,
            now=database_now,
            lease_until=database_now + timedelta(minutes=1),
        )
        assert claimed is not None

        expired_values = {
            "requested_at": requested_at,
            "available_at": database_now,
            **(
                {
                    "started_at": requested_at + timedelta(seconds=1),
                    "heartbeat_at": requested_at + timedelta(seconds=2),
                    "lease_expires_at": requested_at + timedelta(seconds=30),
                }
                if expired_guard == "lease"
                else {
                    "deadline_at": database_now - timedelta(minutes=1),
                    "lease_expires_at": database_now + timedelta(minutes=1),
                }
            ),
        }
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == created.job.id)
            .values(**expired_values)
        )
        stale_now = requested_at + timedelta(seconds=2)
        heartbeat = await repository.heartbeat(
            job_id=created.job.id,
            worker_id=worker_id,
            now=stale_now,
            lease_until=stale_now + timedelta(seconds=10),
            progress_current=1,
            progress_total=1,
        )
        current = await repository.get(created.job.id)
        await session.rollback()

    assert heartbeat is None
    assert current is not None
    assert current.status is JobStatus.RUNNING
    assert current.progress_current == 0


async def test_out_of_order_heartbeats_never_regress_timestamps_or_lease(
    job_database,
) -> None:
    requested_at = datetime.now(UTC)
    deadline_at = requested_at + timedelta(seconds=60)
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        created = await repository.reserve(
            _new_job(
                scope=None,
                requested_at=requested_at,
                deadline_at=deadline_at,
            )
        )
        claimed_at = requested_at + timedelta(seconds=1)
        claimed = await repository.claim(
            job_id=created.job.id,
            worker_id="worker-out-of-order",
            now=claimed_at,
            lease_until=claimed_at + timedelta(seconds=30),
        )
        assert claimed is not None
        newest = await repository.heartbeat(
            job_id=created.job.id,
            worker_id="worker-out-of-order",
            now=claimed_at + timedelta(seconds=20),
            lease_until=claimed_at + timedelta(seconds=80),
            progress_current=1,
            progress_total=3,
        )
        older_committed_later = await repository.heartbeat(
            job_id=created.job.id,
            worker_id="worker-out-of-order",
            now=claimed_at + timedelta(seconds=10),
            lease_until=claimed_at + timedelta(seconds=40),
            progress_current=2,
            progress_total=3,
        )
        await session.commit()

    assert newest is not None and older_committed_later is not None
    assert newest.heartbeat_at is not None
    assert older_committed_later.heartbeat_at is not None
    assert older_committed_later.heartbeat_at > newest.heartbeat_at
    assert older_committed_later.lease_expires_at == newest.lease_expires_at
    assert older_committed_later.lease_expires_at == created.job.deadline_at
    assert older_committed_later.updated_at > newest.updated_at
    assert older_committed_later.progress_current == 2


async def test_sweeper_recovers_expired_lease_and_skips_expired_pending(
    job_database,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        base = database_now - timedelta(minutes=5)
        running = await repository.reserve(_new_job(scope="running", requested_at=base))
        pending = await repository.reserve(
            _new_job(
                scope="expired",
                requested_at=base,
                deadline_at=base + timedelta(minutes=1),
            )
        )
        claimed = await repository.claim(
            job_id=running.job.id,
            worker_id="worker-crashed",
            now=database_now,
            lease_until=database_now + timedelta(minutes=1),
        )
        assert claimed is not None
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == running.job.id)
            .values(
                requested_at=base,
                available_at=database_now,
                started_at=base + timedelta(seconds=1),
                heartbeat_at=base + timedelta(seconds=2),
                lease_expires_at=base + timedelta(seconds=10),
            )
        )
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == pending.job.id)
            .values(
                requested_at=base,
                available_at=base,
                deadline_at=base + timedelta(minutes=1),
                updated_at=base,
            )
        )
        await session.commit()

    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        now = datetime.now(UTC)
        result = await repository.reconcile_expired(
            now=now,
            retry_at=now + timedelta(seconds=5),
            limit=10,
        )
        await session.commit()
        running_after = await repository.get(running.job.id)
        pending_after = await repository.get(pending.job.id)

    assert result.retrying == (running.job.id,)
    assert result.skipped == (pending.job.id,)
    assert running_after is not None and running_after.status is JobStatus.RETRYING
    assert pending_after is not None and pending_after.status is JobStatus.SKIPPED


@pytest.mark.parametrize("app_skew", [timedelta(days=-1), timedelta(days=1)])
async def test_sweeper_uses_database_clock_for_expiry_deadline_and_backoff(
    job_database,
    app_skew: timedelta,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        requested_at = database_now - timedelta(minutes=5)

        async def running_job(
            scope: str,
            *,
            deadline_at: datetime | None = None,
            expire_lease: bool,
        ):
            reservation = await repository.reserve(
                _new_job(
                    scope=scope,
                    requested_at=requested_at,
                    deadline_at=deadline_at,
                )
            )
            claimed = await repository.claim(
                job_id=reservation.job.id,
                worker_id=f"worker-{scope}",
                now=database_now,
                lease_until=database_now + timedelta(minutes=2),
            )
            assert claimed is not None
            if expire_lease:
                expired_values = {
                    "requested_at": requested_at,
                    "available_at": database_now,
                    "started_at": requested_at + timedelta(seconds=1),
                    "heartbeat_at": requested_at + timedelta(seconds=2),
                    "lease_expires_at": requested_at + timedelta(seconds=30),
                }
                if deadline_at is not None:
                    expired_values["deadline_at"] = deadline_at
                await session.execute(
                    update(DurableJobModel)
                    .where(DurableJobModel.id == reservation.job.id)
                    .values(**expired_values)
                )
            return reservation

        expired_running = await running_job(
            "sweep-expired-running",
            expire_lease=True,
        )
        misses_next_retry = await running_job(
            "sweep-misses-deadline",
            deadline_at=database_now + timedelta(seconds=30),
            expire_lease=True,
        )
        deadline_survives = await running_job(
            "sweep-live-deadline",
            deadline_at=database_now + timedelta(minutes=5),
            expire_lease=True,
        )
        future_running = await running_job(
            "sweep-future-running",
            expire_lease=False,
        )
        expired_pending = await repository.reserve(
            _new_job(
                scope="sweep-expired-pending",
                requested_at=requested_at,
                deadline_at=requested_at + timedelta(minutes=1),
            )
        )
        future_pending = await repository.reserve(
            _new_job(
                scope="sweep-future-pending",
                requested_at=requested_at,
                deadline_at=database_now + timedelta(minutes=5),
            )
        )
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == expired_pending.job.id)
            .values(
                requested_at=requested_at,
                available_at=requested_at,
                deadline_at=requested_at + timedelta(minutes=1),
                updated_at=requested_at,
            )
        )
        await session.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == future_pending.job.id)
            .values(
                requested_at=requested_at,
                available_at=requested_at,
                deadline_at=database_now + timedelta(minutes=5),
                updated_at=requested_at,
            )
        )

        app_now = database_now + app_skew
        before_sweep = await _database_now(session)
        result = await repository.reconcile_expired(
            now=app_now,
            retry_at=app_now + timedelta(seconds=60),
            limit=20,
        )
        after_sweep = await _database_now(session)
        jobs = {
            job_id: await repository.get(job_id)
            for job_id in (
                expired_running.job.id,
                misses_next_retry.job.id,
                deadline_survives.job.id,
                future_running.job.id,
                expired_pending.job.id,
                future_pending.job.id,
            )
        }
        immediately_dispatchable = await repository.list_dispatchable(
            now=app_now + timedelta(days=1),
            limit=20,
        )
        await session.rollback()

    assert set(result.retrying) == {
        expired_running.job.id,
        deadline_survives.job.id,
    }
    assert result.failed == (misses_next_retry.job.id,)
    assert result.skipped == (expired_pending.job.id,)
    expired_running_job = jobs[expired_running.job.id]
    deadline_survives_job = jobs[deadline_survives.job.id]
    misses_next_retry_job = jobs[misses_next_retry.job.id]
    future_running_job = jobs[future_running.job.id]
    expired_pending_job = jobs[expired_pending.job.id]
    future_pending_job = jobs[future_pending.job.id]
    assert expired_running_job is not None
    assert deadline_survives_job is not None
    assert misses_next_retry_job is not None
    assert future_running_job is not None
    assert expired_pending_job is not None
    assert future_pending_job is not None
    assert expired_running_job.status is JobStatus.RETRYING
    assert deadline_survives_job.status is JobStatus.RETRYING
    assert misses_next_retry_job.status is JobStatus.FAILED
    assert future_running_job.status is JobStatus.RUNNING
    assert expired_pending_job.status is JobStatus.SKIPPED
    assert future_pending_job.status is JobStatus.QUEUED

    retry_at = expired_running_job.available_at
    assert before_sweep + timedelta(seconds=59) <= retry_at
    assert retry_at <= after_sweep + timedelta(seconds=61)
    dispatchable_ids = {job.id for job in immediately_dispatchable}
    assert expired_running.job.id not in dispatchable_ids
    assert deadline_survives.job.id not in dispatchable_ids
    assert future_pending.job.id in dispatchable_ids


@pytest.mark.parametrize("app_skew", [timedelta(days=-1), timedelta(days=1)])
async def test_cleanup_uses_database_retention_boundary_under_app_skew(
    job_database,
    app_skew: timedelta,
) -> None:
    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        database_now = await _database_now(session)
        app_requested_at = database_now + app_skew
        terminal = await repository.reserve(
            _new_job(scope=None, raw_key="terminal", requested_at=app_requested_at)
        )
        recent_terminal = await repository.reserve(
            _new_job(
                scope=None,
                raw_key="recent-terminal",
                requested_at=app_requested_at,
            )
        )
        active = await repository.reserve(
            _new_job(scope=None, raw_key="active", requested_at=app_requested_at)
        )

        async def finish_at(job_id, worker_id: str, finished_at: datetime) -> None:
            claimed = await repository.claim(
                job_id=job_id,
                worker_id=worker_id,
                now=app_requested_at,
                lease_until=app_requested_at + timedelta(minutes=1),
            )
            assert claimed is not None
            succeeded = await repository.succeed(
                job_id=job_id,
                worker_id=worker_id,
                now=app_requested_at + timedelta(seconds=1),
                result={"processed": 1},
            )
            assert succeeded is not None
            await session.execute(
                update(DurableJobModel)
                .where(DurableJobModel.id == job_id)
                .values(
                    requested_at=finished_at - timedelta(seconds=2),
                    available_at=finished_at - timedelta(seconds=2),
                    started_at=finished_at - timedelta(seconds=1),
                    heartbeat_at=finished_at - timedelta(seconds=1),
                    finished_at=finished_at,
                    updated_at=finished_at,
                )
            )

        await finish_at(
            terminal.job.id,
            "cleanup-old-worker",
            database_now - timedelta(days=31),
        )
        await finish_at(
            recent_terminal.job.id,
            "cleanup-recent-worker",
            database_now - timedelta(days=29),
        )
        await session.commit()

    async with job_database() as session:
        repository = SqlAlchemyDurableJobRepository(session)
        removed = await repository.cleanup_terminal(
            retention=timedelta(days=30),
            limit=10,
        )
        await session.commit()
        terminal_after = await repository.get(terminal.job.id)
        recent_terminal_after = await repository.get(recent_terminal.job.id)
        active_after = await repository.get(active.job.id)
        terminal_binding = await session.scalar(
            select(DurableJobIdempotencyBindingModel).where(
                DurableJobIdempotencyBindingModel.job_id == terminal.job.id
            )
        )
        active_binding = await session.scalar(
            select(DurableJobIdempotencyBindingModel).where(
                DurableJobIdempotencyBindingModel.job_id == active.job.id
            )
        )

    assert removed == 1
    assert terminal_after is None
    assert terminal_binding is None
    assert recent_terminal_after is not None
    assert recent_terminal_after.status is JobStatus.SUCCEEDED
    assert active_after is not None and active_after.status is JobStatus.QUEUED
    assert active_binding is not None
