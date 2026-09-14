from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from app.shared.jobs.application.ports import DurableJobRepository
from app.shared.jobs.application.service import submit_job
from app.shared.jobs.domain import (
    DurableJob,
    InvalidJobData,
    JobReservation,
    JobReservationOutcome,
    JobStatus,
    NewJob,
    digest_idempotency_key,
    fingerprint_payload,
    validate_result,
)


def _job(status: JobStatus = JobStatus.QUEUED) -> DurableJob:
    now = datetime.now(UTC)
    return DurableJob(
        id=uuid4(),
        kind="ingestion.sync",
        owner_id=7,
        scope_key="global",
        idempotency_digest=digest_idempotency_key("ingestion.sync:raw-key"),
        fingerprint=fingerprint_payload({"kind": "ingestion.sync"}),
        status=status,
        attempts=0,
        max_attempts=3,
        progress_current=0,
        progress_total=None,
        result=None,
        error_code=None,
        retryable=False,
        requested_at=now,
        available_at=now,
        started_at=None,
        heartbeat_at=None,
        lease_owner=None,
        lease_expires_at=None,
        deadline_at=None,
        finished_at=None,
        updated_at=now,
    )


class _Repository:
    def __init__(self, reservation: JobReservation) -> None:
        self.reservation = reservation

    async def reserve(self, _proposed: NewJob) -> JobReservation:
        return self.reservation


class _UnitOfWork:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Dispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def enqueue(self, *, job_id, kind) -> None:
        assert job_id and kind == "ingestion.sync"
        self.calls += 1
        if self.fail:
            raise ConnectionError("broker unavailable")


def _proposal() -> NewJob:
    return NewJob(
        kind="ingestion.sync",
        owner_id=7,
        scope_key="global",
        idempotency_digest=digest_idempotency_key("ingestion.sync:raw-key"),
        fingerprint=fingerprint_payload({"kind": "ingestion.sync"}),
    )


async def test_broker_down_after_commit_keeps_created_job_accepted() -> None:
    reservation = JobReservation(_job(), JobReservationOutcome.CREATED)
    unit_of_work = _UnitOfWork()
    dispatcher = _Dispatcher(fail=True)

    submission = await submit_job(
        cast(DurableJobRepository, _Repository(reservation)),
        unit_of_work,
        dispatcher,
        _proposal(),
    )

    assert unit_of_work.commits == 1
    assert unit_of_work.rollbacks == 0
    assert dispatcher.calls == 1
    assert submission.reservation.job.status is JobStatus.QUEUED
    assert submission.broker_enqueued is False


@pytest.mark.parametrize(
    "outcome",
    [JobReservationOutcome.REPLAYED, JobReservationOutcome.COALESCED],
)
async def test_replay_and_coalescence_do_not_create_queue_storm(outcome) -> None:
    reservation = JobReservation(_job(), outcome)
    dispatcher = _Dispatcher()

    submission = await submit_job(
        cast(DurableJobRepository, _Repository(reservation)),
        _UnitOfWork(),
        dispatcher,
        _proposal(),
    )

    assert dispatcher.calls == 0
    assert submission.broker_enqueued is False


def test_idempotency_digest_is_namespaced_by_consumer_kind() -> None:
    raw_key = "same-client-key"
    ingestion = digest_idempotency_key(f"ingestion.sync:{raw_key}")
    orders = digest_idempotency_key(f"orders.processing:{raw_key}")

    assert ingestion != orders
    assert raw_key not in ingestion
    assert len(ingestion) == 64


def test_result_is_json_object_bounded_to_64_kib() -> None:
    assert validate_result({"processed": 10}) == {"processed": 10}
    with pytest.raises(InvalidJobData, match="job_result_must_be_an_object"):
        validate_result([1, 2])  # type: ignore[arg-type]
    with pytest.raises(InvalidJobData, match="job_result_too_large"):
        validate_result({"value": "x" * (64 * 1024)})


def test_domain_and_application_layers_do_not_import_sqlalchemy() -> None:
    jobs_root = Path(__file__).resolve().parents[1] / "shared" / "jobs"
    files = [jobs_root / "domain.py", *sorted((jobs_root / "application").glob("*.py"))]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        assert not any(name.startswith("sqlalchemy") for name in imported), path
