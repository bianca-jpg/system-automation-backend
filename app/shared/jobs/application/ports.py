"""Technology-agnostic ports for durable background jobs.

``owner_id`` scopes idempotency and audit history; it is not, by itself, the
authorization policy. A globally coalesced job may have been created by another
actor. Consumer GET endpoints must authorize with their operation capability
and keep payload/result free of PII.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.shared.jobs.domain import (
    DurableJob,
    JobReservation,
    NewJob,
    ReconcileResult,
)


class DurableJobRepository(Protocol):
    async def reserve(self, proposed: NewJob) -> JobReservation:
        """Persist job/key atomically, including keys coalesced onto an active job."""
        ...

    async def find_idempotent(self, proposed: NewJob) -> JobReservation | None:
        """Read a compatible binding as REPLAYED, or raise on fingerprint conflict."""
        ...

    async def get(self, job_id: UUID) -> DurableJob | None: ...

    async def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        job_id: UUID | None = None,
        kinds: tuple[str, ...] = (),
    ) -> DurableJob | None: ...

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        progress_current: int,
        progress_total: int | None = None,
    ) -> DurableJob | None: ...

    async def succeed(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        result: Mapping[str, object] | None = None,
    ) -> DurableJob | None: ...

    async def fail(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        error_code: str,
        retryable: bool,
    ) -> DurableJob | None: ...

    async def retry(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> DurableJob | None: ...

    async def skip(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        now: datetime,
        error_code: str,
    ) -> DurableJob | None: ...

    async def list_dispatchable(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[DurableJob, ...]: ...

    async def reconcile_expired(
        self,
        *,
        now: datetime,
        retry_at: datetime,
        limit: int,
    ) -> ReconcileResult: ...

    async def cleanup_terminal(
        self,
        *,
        retention: timedelta,
        limit: int,
    ) -> int: ...


class DurableJobUnitOfWork(Protocol):
    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class DurableJobDispatcher(Protocol):
    async def enqueue(self, *, job_id: UUID, kind: str) -> None: ...
