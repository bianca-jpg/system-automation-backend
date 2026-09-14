"""Portas de persistência consumidas pelos casos de uso do Realtime.

A camada de aplicação conhece somente este contrato. A implementação SQLAlchemy
é conectada no composition root de ``app.modules.realtime.infrastructure.http.dependencies``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.realtime.domain import RealtimeEventEnvelope


class RealtimeRepositoryPort(Protocol):
    """Contrato transacional necessário pela aplicação Realtime.

    O adapter é ligado à unidade de trabalho no composition root. Por isso a
    aplicação não conhece SQLAlchemy e nenhum método pode assumir a posse de
    ``commit`` ou ``rollback``.
    """

    async def persist_event(
        self,
        *,
        event_id: UUID,
        topic: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> RealtimeEventEnvelope: ...

    async def observe_keys(
        self,
        *,
        topic: str,
        keys: Sequence[str],
        baseline_if_empty: bool,
    ) -> list[str]: ...

    async def observe_active_keys(
        self,
        *,
        topic: str,
        keys: Sequence[str],
        baseline_if_empty: bool,
        allow_empty_snapshot: bool,
    ) -> list[str]: ...

    async def observe_active_keys_scoped(
        self,
        *,
        topic: str,
        keys: Sequence[str],
        scope_prefixes: Sequence[str],
    ) -> list[str]: ...

    async def initialize_read_cursors(
        self,
        *,
        user_id: int,
        topics: Sequence[str],
    ) -> None: ...

    async def get_topic_statuses(
        self,
        *,
        user_id: int,
        topics: Sequence[str],
    ) -> dict[str, dict[str, Any]]: ...

    async def get_global_latest_sequence(self) -> int: ...

    async def acknowledge_through(
        self,
        *,
        user_id: int,
        topic: str,
        through_sequence: int,
    ) -> int:
        """Retorna a sequência efetivamente reconhecida pelo cursor."""

        ...

    async def replay_events(
        self,
        *,
        topics: Sequence[str],
        after_sequence: int,
        limit: int,
        through_sequence: int | None = None,
    ) -> list[RealtimeEventEnvelope]: ...


__all__ = ["RealtimeRepositoryPort"]
