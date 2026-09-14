"""Persistência do outbox, watermarks e ledger de entidades observadas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.realtime.domain import (
    CursorAheadError,
    InvalidRealtimeEvent,
    RealtimeEventEnvelope,
    ReplayRequiredError,
)

from .models import (
    RealtimeObservedEntity,
    RealtimeOutbox,
    RealtimeReadCursor,
    RealtimeTopicState,
)

# PostgreSQL aceita confortavelmente 10 mil bind params por statement neste
# INSERT de duas colunas. Lotes maiores reduzem round-trips no baseline ERP
# (~133 mil chaves) sem criar uma query/payload sem limite.
_OBSERVED_INSERT_BATCH = 5_000
# Serializa as transações que alocam a sequência global. Como o advisory xact
# lock só é liberado no commit/rollback do chamador, a ordem visível no banco é
# a mesma ordem de ``sequence`` (evita N+1 chegar antes de N no frontend).
_GLOBAL_EVENT_ORDER_LOCK = 7_401_801


# O snapshot atual viaja como um único array tipado. A reconciliação acontece
# no PostgreSQL: o único resultado hidratado em Python são as chaves novas ou
# recorrentes, nunca o histórico inteiro do tópico. O predicado ``active IS
# TRUE`` foi escrito de forma idêntica ao índice parcial criado na migration
# 026 para que a desativação permaneça limitada ao conjunto atualmente ativo.
_OBSERVE_ACTIVE_KEYS_SQL = text(
    """
    WITH current_keys AS MATERIALIZED (
        SELECT raw.entity_key, min(raw.ordinality) AS ordinal
        FROM unnest(CAST(:current_keys AS VARCHAR(256)[]))
             WITH ORDINALITY AS raw(entity_key, ordinality)
        GROUP BY raw.entity_key
    ),
    entered AS MATERIALIZED (
        SELECT current.entity_key, current.ordinal
        FROM current_keys AS current
        LEFT JOIN realtime_observed_entities AS observed
          ON observed.topic = :topic
         AND observed.entity_key = current.entity_key
        WHERE CAST(:report_entered AS BOOLEAN)
          AND (observed.entity_key IS NULL OR observed.active IS NOT TRUE)
    ),
    upserted AS (
        INSERT INTO realtime_observed_entities (
            topic,
            entity_key,
            first_seen_at,
            last_seen_at,
            active
        )
        SELECT :topic, current.entity_key, :observed_at, :observed_at, TRUE
        FROM current_keys AS current
        ON CONFLICT (topic, entity_key) DO UPDATE
        SET last_seen_at = EXCLUDED.last_seen_at,
            active = TRUE
    ),
    deactivated AS (
        UPDATE realtime_observed_entities AS observed
        SET active = FALSE
        WHERE CAST(:deactivate_missing AS BOOLEAN)
          AND observed.topic = :topic
          AND observed.active IS TRUE
          AND NOT EXISTS (
              SELECT 1
              FROM current_keys AS current
              WHERE current.entity_key = observed.entity_key
          )
    )
    SELECT entered.entity_key
    FROM entered
    ORDER BY entered.ordinal
    """
)


_OBSERVE_ACTIVE_KEYS_SCOPED_SQL = text(
    """
    WITH current_keys AS MATERIALIZED (
        SELECT raw.entity_key, min(raw.ordinality) AS ordinal
        FROM unnest(CAST(:current_keys AS VARCHAR(256)[]))
             WITH ORDINALITY AS raw(entity_key, ordinality)
        GROUP BY raw.entity_key
    ),
    scope_prefixes AS MATERIALIZED (
        SELECT DISTINCT unnest(
            CAST(:scope_prefixes AS VARCHAR(256)[])
        ) AS scope_prefix
    ),
    entered AS MATERIALIZED (
        SELECT current.entity_key, current.ordinal
        FROM current_keys AS current
        LEFT JOIN realtime_observed_entities AS observed
          ON observed.topic = :topic
         AND observed.entity_key = current.entity_key
        WHERE observed.entity_key IS NULL OR observed.active IS NOT TRUE
    ),
    upserted AS (
        INSERT INTO realtime_observed_entities (
            topic,
            entity_key,
            first_seen_at,
            last_seen_at,
            active
        )
        SELECT :topic, current.entity_key, :observed_at, :observed_at, TRUE
        FROM current_keys AS current
        ON CONFLICT (topic, entity_key) DO UPDATE
        SET last_seen_at = EXCLUDED.last_seen_at,
            active = TRUE
    ),
    deactivated AS (
        UPDATE realtime_observed_entities AS observed
        SET active = FALSE
        WHERE observed.topic = :topic
          AND observed.active IS TRUE
          AND EXISTS (
              SELECT 1
              FROM scope_prefixes AS scope
              WHERE scope.scope_prefix = split_part(observed.entity_key, ':', 1)
          )
          AND NOT EXISTS (
              SELECT 1
              FROM current_keys AS current
              WHERE current.entity_key = observed.entity_key
          )
    )
    SELECT entered.entity_key
    FROM entered
    ORDER BY entered.ordinal
    """
)


async def lock_global_event_order(db: AsyncSession) -> None:
    """Adquire primeiro o lock global usado por toda mutação do realtime.

    A ordem única global -> topic row evita deadlock entre uma transação que
    observa o ledger antes de emitir e outra que persiste um evento direto.
    O lock é transacional e será liberado no commit/rollback do chamador.
    """

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _GLOBAL_EVENT_ORDER_LOCK},
    )


async def _locked_topic_state(db: AsyncSession, topic: str) -> RealtimeTopicState:
    # O INSERT torna a inicialização segura entre múltiplas instâncias. O lock
    # serializa a sequência por tópico e o primeiro snapshot do ledger.
    await db.execute(
        pg_insert(RealtimeTopicState)
        .values(topic=topic)
        .on_conflict_do_nothing(index_elements=[RealtimeTopicState.topic])
    )
    state = await db.scalar(
        select(RealtimeTopicState)
        .where(RealtimeTopicState.topic == topic)
        .with_for_update()
    )
    if state is None:  # pragma: no cover - proteção contra schema corrompido
        raise RuntimeError(f"não foi possível inicializar o tópico {topic!r}")
    return state


async def persist_event(
    db: AsyncSession,
    *,
    event_id: UUID,
    topic: str,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> RealtimeEventEnvelope:
    await lock_global_event_order(db)
    # UUID fornecido pelo chamador é a chave de idempotência do evento. O
    # advisory lock cobre inclusive duas transações concorrentes em instâncias
    # diferentes, antes de qualquer INSERT/unique violation.
    advisory_key = event_id.int & ((1 << 63) - 1)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": advisory_key})
    existing = await db.scalar(
        select(RealtimeOutbox).where(RealtimeOutbox.event_id == event_id)
    )
    if existing is not None:
        same_contract = (
            existing.topic == topic
            and existing.event_type == event_type
            and existing.occurred_at == occurred_at
            and existing.payload == payload
            and existing.version == 1
        )
        if not same_contract:
            raise InvalidRealtimeEvent(
                "event_id já existe com tópico, tipo, instante ou payload diferente"
            )
        return RealtimeEventEnvelope(
            event_id=existing.event_id,
            sequence=existing.sequence,
            event_type=existing.event_type,
            topic=existing.topic,
            occurred_at=existing.occurred_at,
            payload=existing.payload,
            version=existing.version,
        )

    state = await _locked_topic_state(db, topic)
    topic_sequence = state.latest_topic_sequence + 1

    row = RealtimeOutbox(
        event_id=event_id,
        topic=topic,
        topic_sequence=topic_sequence,
        event_type=event_type,
        version=1,
        occurred_at=occurred_at,
        payload=payload,
    )
    db.add(row)
    await db.flush()

    envelope = RealtimeEventEnvelope(
        event_id=row.event_id,
        sequence=row.sequence,
        event_type=row.event_type,
        topic=row.topic,
        occurred_at=row.occurred_at,
        payload=row.payload,
        version=row.version,
    )
    state.latest_sequence = row.sequence
    state.latest_topic_sequence = topic_sequence
    state.latest_event = envelope.as_dict()
    state.updated_at = datetime.now(UTC)
    await db.flush()
    return envelope


async def observe_keys(
    db: AsyncSession,
    *,
    topic: str,
    keys: Sequence[str],
    baseline_if_empty: bool,
) -> list[str]:
    await lock_global_event_order(db)
    state = await _locked_topic_state(db, topic)
    is_first_observation = state.observed_initialized_at is None
    if is_first_observation:
        state.observed_initialized_at = datetime.now(UTC)

    inserted: list[str] = []
    for offset in range(0, len(keys), _OBSERVED_INSERT_BATCH):
        batch = keys[offset : offset + _OBSERVED_INSERT_BATCH]
        if not batch:
            continue
        result = await db.scalars(
            pg_insert(RealtimeObservedEntity)
            .values([{"topic": topic, "entity_key": key} for key in batch])
            .on_conflict_do_nothing(
                index_elements=[
                    RealtimeObservedEntity.topic,
                    RealtimeObservedEntity.entity_key,
                ]
            )
            .returning(RealtimeObservedEntity.entity_key)
        )
        inserted.extend(result.all())

    await db.flush()
    if is_first_observation and baseline_if_empty:
        return []
    return inserted


async def observe_active_keys(
    db: AsyncSession,
    *,
    topic: str,
    keys: Sequence[str],
    baseline_if_empty: bool,
    allow_empty_snapshot: bool,
) -> list[str]:
    """Detecta entradas em um conjunto ativo, incluindo recorrências.

    Uma chave que some vira inativa; se reaparecer, volta na lista de novidades.
    Snapshot vazio só desativa tudo quando o chamador confirma explicitamente
    ``allow_empty_snapshot=True``.
    """

    await lock_global_event_order(db)
    state = await _locked_topic_state(db, topic)
    is_first_observation = state.observed_initialized_at is None
    now = datetime.now(UTC)
    if is_first_observation:
        state.observed_initialized_at = now

    result = await db.execute(
        _OBSERVE_ACTIVE_KEYS_SQL,
        {
            "topic": topic,
            "current_keys": list(keys),
            "observed_at": now,
            "deactivate_missing": bool(keys) or allow_empty_snapshot,
            # O primeiro baseline pode conter 500 mil chaves. Se o contrato
            # manda silenciá-las, o banco não deve devolvê-las só para Python
            # descartá-las em seguida.
            "report_entered": not (is_first_observation and baseline_if_empty),
        },
    )
    entered = list(result.scalars().all())

    await db.flush()
    if is_first_observation and baseline_if_empty:
        return []
    return entered


async def observe_active_keys_scoped(
    db: AsyncSession,
    *,
    topic: str,
    keys: Sequence[str],
    scope_prefixes: Sequence[str],
) -> list[str]:
    """Reconcilia um subconjunto ativo sem afetar outros escopos do tópico.

    A aplicação garante listas normalizadas e que cada chave pertence ao
    escopo. Os arrays tipados mantêm a quantidade de bind parameters constante;
    ``split_part`` compara o prefixo exato, sem interpolação ou ``LIKE``.
    """

    await lock_global_event_order(db)
    await _locked_topic_state(db, topic)
    now = datetime.now(UTC)

    result = await db.execute(
        _OBSERVE_ACTIVE_KEYS_SCOPED_SQL,
        {
            "topic": topic,
            "current_keys": list(keys),
            "scope_prefixes": list(scope_prefixes),
            "observed_at": now,
        },
    )
    entered = list(result.scalars().all())

    await db.flush()
    return entered


def _envelope_from_row(row: RealtimeOutbox) -> RealtimeEventEnvelope:
    return RealtimeEventEnvelope(
        event_id=row.event_id,
        sequence=row.sequence,
        event_type=row.event_type,
        topic=row.topic,
        occurred_at=row.occurred_at,
        payload=row.payload,
        version=row.version,
    )


async def initialize_read_cursors(
    db: AsyncSession, *, user_id: int, topics: Sequence[str]
) -> None:
    """Cria somente cursores ausentes no latest atual (baseline server-side)."""

    states = {
        state.topic: state
        for state in (
            await db.scalars(
                select(RealtimeTopicState).where(RealtimeTopicState.topic.in_(topics))
            )
        ).all()
    }
    values = []
    for topic in topics:
        state = states.get(topic)
        values.append(
            {
                "user_id": user_id,
                "topic": topic,
                "last_read_sequence": state.latest_sequence if state else 0,
                "last_read_topic_sequence": (
                    state.latest_topic_sequence if state else 0
                ),
            }
        )
    if values:
        await db.execute(
            pg_insert(RealtimeReadCursor)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[RealtimeReadCursor.user_id, RealtimeReadCursor.topic]
            )
        )
        await db.flush()


async def get_topic_statuses(
    db: AsyncSession, *, user_id: int, topics: Sequence[str]
) -> dict[str, dict[str, Any]]:
    states = {
        state.topic: state
        for state in (
            await db.scalars(
                select(RealtimeTopicState).where(RealtimeTopicState.topic.in_(topics))
            )
        ).all()
    }
    cursors = {
        cursor.topic: cursor
        for cursor in (
            await db.scalars(
                select(RealtimeReadCursor).where(
                    RealtimeReadCursor.user_id == user_id,
                    RealtimeReadCursor.topic.in_(topics),
                )
            )
        ).all()
    }

    result: dict[str, dict[str, Any]] = {}
    for topic in topics:
        state = states.get(topic)
        cursor = cursors.get(topic)
        latest_sequence = state.latest_sequence if state else 0
        latest_topic_sequence = state.latest_topic_sequence if state else 0
        read_sequence = cursor.last_read_sequence if cursor else 0
        read_topic_sequence = cursor.last_read_topic_sequence if cursor else 0
        result[topic] = {
            "latestSequence": latest_sequence,
            "lastReadSequence": read_sequence,
            "unseen": max(0, latest_topic_sequence - read_topic_sequence),
            "latest": state.latest_event if state else None,
        }
    return result


async def get_global_latest_sequence(db: AsyncSession) -> int:
    """Watermark global do protocolo, independente do filtro de tópicos."""

    return int(
        await db.scalar(select(func.max(RealtimeTopicState.latest_sequence))) or 0
    )


async def acknowledge_through(
    db: AsyncSession,
    *,
    user_id: int,
    topic: str,
    through_sequence: int,
) -> RealtimeReadCursor:
    state = await db.scalar(
        select(RealtimeTopicState)
        .where(RealtimeTopicState.topic == topic)
        .with_for_update()
    )
    latest_sequence = state.latest_sequence if state else 0
    if through_sequence > latest_sequence:
        raise CursorAheadError(
            f"throughSequence {through_sequence} excede latestSequence {latest_sequence}"
        )

    if through_sequence == 0:
        through_topic_sequence = 0
    elif state is not None and through_sequence == state.latest_sequence:
        through_topic_sequence = state.latest_topic_sequence
    else:
        through_topic_sequence = await db.scalar(
            select(func.max(RealtimeOutbox.topic_sequence)).where(
                RealtimeOutbox.topic == topic,
                RealtimeOutbox.sequence <= through_sequence,
            )
        )
        if through_topic_sequence is None:
            cursor = await db.get(RealtimeReadCursor, (user_id, topic))
            if cursor is not None and through_sequence <= cursor.last_read_sequence:
                return cursor
            raise ReplayRequiredError(
                "throughSequence ficou anterior à janela retida; consulte /status"
            )

    insert_stmt = pg_insert(RealtimeReadCursor).values(
        user_id=user_id,
        topic=topic,
        last_read_sequence=through_sequence,
        last_read_topic_sequence=through_topic_sequence,
        updated_at=datetime.now(UTC),
    )
    await db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=[RealtimeReadCursor.user_id, RealtimeReadCursor.topic],
            set_={
                "last_read_sequence": func.greatest(
                    RealtimeReadCursor.last_read_sequence,
                    insert_stmt.excluded.last_read_sequence,
                ),
                "last_read_topic_sequence": func.greatest(
                    RealtimeReadCursor.last_read_topic_sequence,
                    insert_stmt.excluded.last_read_topic_sequence,
                ),
                "updated_at": datetime.now(UTC),
            },
        )
    )
    await db.flush()
    cursor = await db.get(
        RealtimeReadCursor,
        (user_id, topic),
        populate_existing=True,
    )
    if cursor is None:  # pragma: no cover - INSERT ... RETURNING guard
        raise RuntimeError("cursor realtime não foi persistido")
    return cursor


async def replay_events(
    db: AsyncSession,
    *,
    topics: Sequence[str],
    after_sequence: int,
    limit: int,
    through_sequence: int | None = None,
) -> list[RealtimeEventEnvelope]:
    global_latest = await get_global_latest_sequence(db)
    if after_sequence > global_latest:
        raise ReplayRequiredError(
            f"cursor {after_sequence} está além do watermark {global_latest}"
        )
    floors = (
        await db.execute(
            select(
                RealtimeTopicState.topic,
                RealtimeTopicState.replay_floor_sequence,
                RealtimeTopicState.latest_sequence,
            )
            .where(RealtimeTopicState.topic.in_(topics))
            # Cleanup toma FOR UPDATE nestas mesmas linhas antes de apagar.
            # O share lock mantém floor + leitura do outbox como um snapshot
            # consistente mesmo sob READ COMMITTED.
            .with_for_update(read=True)
        )
    ).all()
    if any(
        after_sequence < replay_floor and latest_sequence > after_sequence
        for _, replay_floor, latest_sequence in floors
    ):
        raise ReplayRequiredError("cursor anterior à janela de replay retida")

    replay_filters = [
        RealtimeOutbox.topic.in_(topics),
        RealtimeOutbox.sequence > after_sequence,
    ]
    if through_sequence is not None:
        replay_filters.append(RealtimeOutbox.sequence <= through_sequence)
    rows = (
        await db.scalars(
            select(RealtimeOutbox)
            .where(*replay_filters)
            .order_by(RealtimeOutbox.sequence)
            .limit(limit + 1)
        )
    ).all()
    if len(rows) > limit:
        raise ReplayRequiredError("janela de replay excede o limite por conexão")
    return [_envelope_from_row(row) for row in rows]


async def claim_outbox_batch(
    db: AsyncSession,
    *,
    instance_id: str,
    batch_size: int,
    lease_seconds: int = 30,
) -> list[RealtimeOutbox]:
    del batch_size  # Estritamente um head por vez; o runtime itera até seu batch.
    now = datetime.now(UTC)
    head_sequence = (
        select(func.min(RealtimeOutbox.sequence))
        .where(RealtimeOutbox.published_at.is_(None))
        .scalar_subquery()
    )
    candidates = (
        select(RealtimeOutbox.sequence)
        .where(
            RealtimeOutbox.sequence == head_sequence,
            RealtimeOutbox.published_at.is_(None),
            RealtimeOutbox.next_attempt_at <= now,
            (
                RealtimeOutbox.claimed_until.is_(None)
                | (RealtimeOutbox.claimed_until < now)
            ),
        )
        .order_by(RealtimeOutbox.sequence)
        .limit(1)
        .with_for_update(skip_locked=True)
        .cte("realtime_claim_candidates")
    )
    rows = (
        await db.scalars(
            update(RealtimeOutbox)
            .where(RealtimeOutbox.sequence.in_(select(candidates.c.sequence)))
            .values(
                claimed_by=instance_id,
                claimed_until=now + timedelta(seconds=lease_seconds),
                attempts=RealtimeOutbox.attempts + 1,
            )
            .returning(RealtimeOutbox)
        )
    ).all()
    return sorted(rows, key=lambda row: row.sequence)


async def mark_published(db: AsyncSession, *, sequence: int, instance_id: str) -> bool:
    result = await db.execute(
        update(RealtimeOutbox)
        .where(
            RealtimeOutbox.sequence == sequence,
            RealtimeOutbox.claimed_by == instance_id,
        )
        .values(
            published_at=datetime.now(UTC),
            claimed_by=None,
            claimed_until=None,
            last_error=None,
        )
    )
    return (cast(CursorResult[Any], result).rowcount or 0) == 1


async def mark_publish_failed(
    db: AsyncSession,
    *,
    sequence: int,
    instance_id: str,
    attempts: int,
    error_name: str,
) -> None:
    delay_seconds = min(300, 2 ** min(max(attempts, 1), 8))
    await db.execute(
        update(RealtimeOutbox)
        .where(
            RealtimeOutbox.sequence == sequence,
            RealtimeOutbox.claimed_by == instance_id,
        )
        .values(
            claimed_by=None,
            claimed_until=None,
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
            # Somente o nome da exceção, nunca payload/ticket/dados de usuário.
            last_error=error_name[:128],
        )
    )


async def cleanup_published(db: AsyncSession, *, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    states = (await db.scalars(select(RealtimeTopicState).with_for_update())).all()
    deleted_total = 0
    for state in states:
        max_deleted = await db.scalar(
            select(func.max(RealtimeOutbox.sequence)).where(
                RealtimeOutbox.topic == state.topic,
                RealtimeOutbox.published_at.is_not(None),
                RealtimeOutbox.published_at < cutoff,
                RealtimeOutbox.sequence < state.latest_sequence,
            )
        )
        if max_deleted is None:
            continue
        result = await db.execute(
            delete(RealtimeOutbox).where(
                RealtimeOutbox.topic == state.topic,
                RealtimeOutbox.published_at.is_not(None),
                RealtimeOutbox.published_at < cutoff,
                RealtimeOutbox.sequence <= max_deleted,
                RealtimeOutbox.sequence < state.latest_sequence,
            )
        )
        state.replay_floor_sequence = max(state.replay_floor_sequence, max_deleted)
        deleted_total += cast(CursorResult[Any], result).rowcount or 0
    return deleted_total


async def pending_outbox_count(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(RealtimeOutbox)
            .where(RealtimeOutbox.published_at.is_(None))
        )
        or 0
    )


async def stream_gap_requires_resync(
    db: AsyncSession,
    *,
    after_sequence: int,
    before_sequence: int,
) -> bool:
    """Distingue evento perdido de buraco legítimo da sequence PostgreSQL."""

    missing_committed = (
        await db.scalar(
            select(RealtimeOutbox.sequence)
            .where(
                RealtimeOutbox.sequence > after_sequence,
                RealtimeOutbox.sequence < before_sequence,
            )
            .limit(1)
        )
        is not None
    )
    if missing_committed:
        return True
    replay_floor = int(
        await db.scalar(select(func.max(RealtimeTopicState.replay_floor_sequence))) or 0
    )
    return replay_floor > after_sequence
