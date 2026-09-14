from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.modules.realtime.domain import RealtimeEventEnvelope

from .ports import RealtimeRepositoryPort


async def initialize_user_baseline(
    repository: RealtimeRepositoryPort, *, user_id: int, topics: Sequence[str]
) -> None:
    await repository.initialize_read_cursors(user_id=user_id, topics=topics)


async def realtime_status(
    repository: RealtimeRepositoryPort, *, user_id: int, topics: Sequence[str]
) -> dict[str, Any]:
    topic_statuses = await repository.get_topic_statuses(user_id=user_id, topics=topics)
    return {
        # ``lastSequence`` é o watermark do protocolo global. Filtrar tópicos
        # altera somente o mapa abaixo; nunca faz o cursor global andar para
        # trás quando o cliente troca sua inscrição.
        "lastSequence": await repository.get_global_latest_sequence(),
        "topics": topic_statuses,
    }


async def mark_read(
    repository: RealtimeRepositoryPort,
    *,
    user_id: int,
    topic: str,
    through_sequence: int,
) -> dict[str, Any]:
    last_read_sequence = await repository.acknowledge_through(
        user_id=user_id,
        topic=topic,
        through_sequence=through_sequence,
    )
    status = (await repository.get_topic_statuses(user_id=user_id, topics=[topic]))[
        topic
    ]
    return {
        "topic": topic,
        "lastReadSequence": last_read_sequence,
        "unseen": status["unseen"],
    }


async def replay_events(
    repository: RealtimeRepositoryPort,
    *,
    topics: Sequence[str],
    after_sequence: int,
    limit: int,
    through_sequence: int | None = None,
) -> list[RealtimeEventEnvelope]:
    """Recupera uma janela consistente pelo port da aplicação."""

    return await repository.replay_events(
        topics=topics,
        after_sequence=after_sequence,
        limit=limit,
        through_sequence=through_sequence,
    )
