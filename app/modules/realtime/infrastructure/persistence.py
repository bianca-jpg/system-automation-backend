"""Adapter SQLAlchemy do port de persistência da aplicação Realtime."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.realtime.domain import RealtimeEventEnvelope

from . import repository


class SqlRealtimeRepository:
    """Traduz o port estável para as funções SQLAlchemy já existentes."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def persist_event(
        self,
        *,
        event_id: UUID,
        topic: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> RealtimeEventEnvelope:
        return await repository.persist_event(
            self._db,
            event_id=event_id,
            topic=topic,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )

    async def observe_keys(
        self,
        *,
        topic: str,
        keys: Sequence[str],
        baseline_if_empty: bool,
    ) -> list[str]:
        return await repository.observe_keys(
            self._db,
            topic=topic,
            keys=keys,
            baseline_if_empty=baseline_if_empty,
        )

    async def observe_active_keys(
        self,
        *,
        topic: str,
        keys: Sequence[str],
        baseline_if_empty: bool,
        allow_empty_snapshot: bool,
    ) -> list[str]:
        return await repository.observe_active_keys(
            self._db,
            topic=topic,
            keys=keys,
            baseline_if_empty=baseline_if_empty,
            allow_empty_snapshot=allow_empty_snapshot,
        )

    async def observe_active_keys_scoped(
        self,
        *,
        topic: str,
        keys: Sequence[str],
        scope_prefixes: Sequence[str],
    ) -> list[str]:
        return await repository.observe_active_keys_scoped(
            self._db,
            topic=topic,
            keys=keys,
            scope_prefixes=scope_prefixes,
        )

    async def initialize_read_cursors(
        self,
        *,
        user_id: int,
        topics: Sequence[str],
    ) -> None:
        await repository.initialize_read_cursors(
            self._db,
            user_id=user_id,
            topics=topics,
        )

    async def get_topic_statuses(
        self,
        *,
        user_id: int,
        topics: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        return await repository.get_topic_statuses(
            self._db,
            user_id=user_id,
            topics=topics,
        )

    async def get_global_latest_sequence(self) -> int:
        return await repository.get_global_latest_sequence(self._db)

    async def acknowledge_through(
        self,
        *,
        user_id: int,
        topic: str,
        through_sequence: int,
    ) -> int:
        cursor = await repository.acknowledge_through(
            self._db,
            user_id=user_id,
            topic=topic,
            through_sequence=through_sequence,
        )
        return cursor.last_read_sequence

    async def replay_events(
        self,
        *,
        topics: Sequence[str],
        after_sequence: int,
        limit: int,
        through_sequence: int | None = None,
    ) -> list[RealtimeEventEnvelope]:
        return await repository.replay_events(
            self._db,
            topics=topics,
            after_sequence=after_sequence,
            limit=limit,
            through_sequence=through_sequence,
        )


__all__ = ["SqlRealtimeRepository"]
