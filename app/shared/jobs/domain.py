"""Pure domain types for durable, at-least-once background jobs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

MAX_RESULT_BYTES = 64 * 1024
MAX_CLEANUP_BATCH = 500
DEFAULT_RETENTION_DAYS = 30

_SAFE_KIND = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_SAFE_WORKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


DISPATCHABLE_JOB_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RETRYING})


class JobReservationOutcome(StrEnum):
    CREATED = "created"
    REPLAYED = "replayed"
    COALESCED = "coalesced"


class DurableJobError(RuntimeError):
    """Base error whose text is safe to expose without request data."""


class InvalidJobData(DurableJobError, ValueError):
    pass


class JobIdempotencyConflict(DurableJobError):
    def __init__(self, existing_job_id: UUID) -> None:
        super().__init__("idempotency_key_reused_with_different_payload")
        self.existing_job_id = existing_job_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def digest_idempotency_key(value: str) -> str:
    """Return the only representation of an idempotency key that may persist.

    Consumers must pass a namespaced value such as
    ``f"ingestion.sync:{raw_header_value}"`` so the same client key can be used
    independently by different operations.
    """

    if not value or len(value.encode("utf-8")) > 512:
        raise InvalidJobData("invalid_idempotency_key")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint_payload(payload: Mapping[str, Any]) -> str:
    """Create a deterministic digest without retaining the request payload."""

    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidJobData("job_payload_is_not_json_serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def validate_result(result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if not isinstance(result, Mapping):
        raise InvalidJobData("job_result_must_be_an_object")
    materialized = dict(result)
    try:
        encoded = json.dumps(
            materialized,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidJobData("job_result_is_not_json_serializable") from exc
    if len(encoded) > MAX_RESULT_BYTES:
        raise InvalidJobData("job_result_too_large")
    return materialized


def validate_error_code(value: str) -> str:
    if not _SAFE_ERROR_CODE.fullmatch(value):
        raise InvalidJobData("invalid_job_error_code")
    return value


def validate_worker_id(value: str) -> str:
    if not _SAFE_WORKER.fullmatch(value):
        raise InvalidJobData("invalid_job_worker_id")
    return value


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidJobData(f"{field}_must_be_timezone_aware")


@dataclass(frozen=True, slots=True)
class NewJob:
    kind: str
    fingerprint: str
    scope_key: str | None
    owner_id: int | None = None
    idempotency_digest: str | None = None
    max_attempts: int = 3
    requested_at: datetime = field(default_factory=utc_now)
    deadline_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not _SAFE_KIND.fullmatch(self.kind):
            raise InvalidJobData("invalid_job_kind")
        if self.scope_key is not None and not _SAFE_SCOPE.fullmatch(self.scope_key):
            raise InvalidJobData("invalid_job_scope_key")
        if self.owner_id is not None and self.owner_id <= 0:
            raise InvalidJobData("invalid_job_owner")
        if not _SHA256_HEX.fullmatch(self.fingerprint):
            raise InvalidJobData("invalid_job_fingerprint")
        if self.idempotency_digest is not None and not _SHA256_HEX.fullmatch(
            self.idempotency_digest
        ):
            raise InvalidJobData("invalid_job_idempotency_digest")
        if not 1 <= self.max_attempts <= 20:
            raise InvalidJobData("invalid_job_max_attempts")
        _require_aware(self.requested_at, "requested_at")
        if self.deadline_at is not None:
            _require_aware(self.deadline_at, "deadline_at")
            if self.deadline_at <= self.requested_at:
                raise InvalidJobData("job_deadline_must_follow_request")


@dataclass(frozen=True, slots=True)
class DurableJob:
    id: UUID
    kind: str
    owner_id: int | None
    scope_key: str | None
    idempotency_digest: str | None
    fingerprint: str
    status: JobStatus
    attempts: int
    max_attempts: int
    progress_current: int
    progress_total: int | None
    result: Mapping[str, Any] | None
    error_code: str | None
    retryable: bool
    requested_at: datetime
    available_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    deadline_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.result is not None and not isinstance(self.result, MappingProxyType):
            object.__setattr__(self, "result", MappingProxyType(dict(self.result)))


@dataclass(frozen=True, slots=True)
class JobReservation:
    job: DurableJob
    outcome: JobReservationOutcome


@dataclass(frozen=True, slots=True)
class JobSubmission:
    reservation: JobReservation
    broker_enqueued: bool


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    retrying: tuple[UUID, ...] = ()
    failed: tuple[UUID, ...] = ()
    skipped: tuple[UUID, ...] = ()
