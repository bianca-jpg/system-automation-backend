"""Ports tecnológicos do processamento durável de Pedidos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.pedidos.processing.domain import (
    PendingSnapshotStats,
    PlanDraft,
    PlannedPair,
    ProcessingChannel,
    ProcessingMode,
    ProcessingSpec,
)


class ProcessingPlannerSource(Protocol):
    async def inspect_pending(
        self, channel: ProcessingChannel
    ) -> PendingSnapshotStats: ...

    async def load_pending_items(self, channel: ProcessingChannel) -> list[dict]: ...

    async def load_stock(
        self, product_codes: set[str]
    ) -> dict[str, dict[str, int]]: ...

    async def load_adequation_config(self) -> tuple[str, float]: ...

    async def load_size_reference(
        self, product_codes: set[str]
    ) -> dict[str, dict[str, int]]: ...

    async def load_pedido_budget(
        self, nr_pedidos: set[int]
    ) -> tuple[dict[int, int], dict[int, int], dict[int, int]]: ...


class ProcessingRepository(Protocol):
    async def create_spec(
        self,
        *,
        job_id: UUID,
        mode: ProcessingMode,
        channel: ProcessingChannel,
    ) -> ProcessingSpec: ...

    async def get_spec(
        self, job_id: UUID, *, for_update: bool = False
    ) -> ProcessingSpec | None: ...

    async def store_plan(
        self,
        *,
        job_id: UUID,
        draft: PlanDraft,
        planned_at: datetime,
    ) -> ProcessingSpec: ...

    async def record_standby_reasons(
        self,
        *,
        job_id: UUID,
        deferred_pairs: Sequence[tuple[int, str, str, str]],
        blocked_credit_pairs: Sequence[tuple[int, str, str]],
        recorded_at: datetime,
    ) -> None: ...

    async def load_next_chunk(
        self,
        *,
        job_id: UUID,
        limit: int,
    ) -> tuple[PlannedPair, ...]: ...

    async def checkpoint_chunk(
        self,
        *,
        job_id: UUID,
        rows: Sequence[PlannedPair],
        applied_at: datetime,
    ) -> ProcessingSpec: ...


class ProcessingWriter(Protocol):
    async def apply_pairs(self, rows: Sequence[PlannedPair]) -> None:
        """Aplica OR/processado/Linx/estoque sem commit, idempotente por hash."""


class ProcessingRealtime(Protocol):
    async def record_progress(
        self,
        *,
        event_id: UUID,
        occurred_at: datetime,
        payload: Mapping[str, object],
    ) -> None: ...

    async def reconcile_alerts(
        self,
        *,
        event_id: UUID,
        occurred_at: datetime,
        reason: str,
    ) -> None: ...

    async def record_completion(
        self,
        *,
        event_id: UUID,
        occurred_at: datetime,
        payload: Mapping[str, object],
    ) -> None: ...


class ProcessingSessionLockGuard(Protocol):
    async def ensure_held(self) -> None: ...


class ProcessingLeaseKeeper(Protocol):
    async def start(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        progress_current: int,
        progress_total: int | None,
    ) -> None: ...

    async def update_progress(
        self, *, progress_current: int, progress_total: int | None
    ) -> None: ...

    async def ensure_valid(self) -> None: ...

    async def stop(self) -> None: ...


class ProcessingUnitOfWork(Protocol):
    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


__all__ = [
    "ProcessingLeaseKeeper",
    "ProcessingPlannerSource",
    "ProcessingRealtime",
    "ProcessingRepository",
    "ProcessingSessionLockGuard",
    "ProcessingUnitOfWork",
    "ProcessingWriter",
]
