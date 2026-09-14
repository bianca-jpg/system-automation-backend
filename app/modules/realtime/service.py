"""Fachada pública que compõe aplicação e persistência transacional.

Este módulo preserva as assinaturas históricas ``(db, ...)`` do Open Host
Service. Cada chamada cria um adapter leve ligado à ``AsyncSession`` recebida;
commit e rollback continuam sob responsabilidade exclusiva do chamador.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.realtime.application import facade, use_cases
from app.modules.realtime.domain import RealtimeEventEnvelope
from app.modules.realtime.infrastructure.http.dependencies import (
    build_realtime_repository,
)


async def enqueue_realtime_event(
    db: AsyncSession,
    *,
    topic: str,
    event_type: str,
    payload: Mapping[str, Any],
    occurred_at: datetime | None = None,
    event_id: UUID | None = None,
) -> RealtimeEventEnvelope:
    return await facade.enqueue_realtime_event(
        build_realtime_repository(db),
        topic=topic,
        event_type=event_type,
        payload=payload,
        occurred_at=occurred_at,
        event_id=event_id,
    )


async def observe_entity_keys(
    db: AsyncSession,
    topic: str,
    keys: Iterable[str | int],
    baseline_if_empty: bool = True,
) -> list[str]:
    return await facade.observe_entity_keys(
        build_realtime_repository(db),
        topic,
        keys,
        baseline_if_empty,
    )


async def observe_active_entity_keys(
    db: AsyncSession,
    topic: str,
    keys: Iterable[str | int],
    baseline_if_empty: bool = True,
    *,
    allow_empty_snapshot: bool = False,
) -> list[str]:
    return await facade.observe_active_entity_keys(
        build_realtime_repository(db),
        topic,
        keys,
        baseline_if_empty,
        allow_empty_snapshot=allow_empty_snapshot,
    )


async def observe_active_entity_keys_scoped(
    db: AsyncSession,
    topic: str,
    keys: Iterable[str | int],
    *,
    scope_prefixes: Iterable[str | int],
) -> list[str]:
    return await facade.observe_active_entity_keys_scoped(
        build_realtime_repository(db),
        topic,
        keys,
        scope_prefixes=scope_prefixes,
    )


async def initialize_user_baseline(
    db: AsyncSession, *, user_id: int, topics: Sequence[str]
) -> None:
    await use_cases.initialize_user_baseline(
        build_realtime_repository(db),
        user_id=user_id,
        topics=topics,
    )


async def realtime_status(
    db: AsyncSession, *, user_id: int, topics: Sequence[str]
) -> dict[str, Any]:
    return await use_cases.realtime_status(
        build_realtime_repository(db),
        user_id=user_id,
        topics=topics,
    )


async def mark_read(
    db: AsyncSession,
    *,
    user_id: int,
    topic: str,
    through_sequence: int,
) -> dict[str, Any]:
    return await use_cases.mark_read(
        build_realtime_repository(db),
        user_id=user_id,
        topic=topic,
        through_sequence=through_sequence,
    )


async def replay_events(
    db: AsyncSession,
    *,
    topics: Sequence[str],
    after_sequence: int,
    limit: int,
    through_sequence: int | None = None,
) -> list[RealtimeEventEnvelope]:
    return await use_cases.replay_events(
        build_realtime_repository(db),
        topics=topics,
        after_sequence=after_sequence,
        limit=limit,
        through_sequence=through_sequence,
    )


# Nome curto mantido para os bounded contexts consumidores.
record_event = enqueue_realtime_event

__all__ = [
    "enqueue_realtime_event",
    "initialize_user_baseline",
    "mark_read",
    "observe_active_entity_keys",
    "observe_active_entity_keys_scoped",
    "observe_entity_keys",
    "realtime_status",
    "record_event",
    "replay_events",
]
