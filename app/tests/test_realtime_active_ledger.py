"""Provas de boundedness e recorrência do ledger ativo do realtime."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.realtime.infrastructure import repository
from app.shared.database.session import async_session_factory

_ACTIVE_INDEX = "ix_realtime_observed_entities_active_topic"


@pytest.mark.asyncio
async def test_reconciliacao_ativa_hidrata_somente_novas_e_recorrentes():
    state = SimpleNamespace(observed_initialized_at=datetime.now(UTC))
    returned = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: ["new", "recurring"])
    )
    db = AsyncMock()
    db.execute.return_value = returned

    with (
        patch.object(repository, "lock_global_event_order", new=AsyncMock()) as lock,
        patch.object(
            repository,
            "_locked_topic_state",
            new=AsyncMock(return_value=state),
        ) as topic_lock,
    ):
        result = await repository.observe_active_keys(
            db,
            topic="alerts",
            keys=["current", "new", "recurring"],
            baseline_if_empty=False,
            allow_empty_snapshot=True,
        )

    assert result == ["new", "recurring"]
    lock.assert_awaited_once_with(db)
    topic_lock.assert_awaited_once_with(db, "alerts")
    db.scalars.assert_not_awaited()
    statement, parameters = db.execute.await_args.args
    sql = " ".join(statement.text.lower().split())
    assert "unnest(" in sql
    assert "on conflict (topic, entity_key) do update" in sql
    assert "observed.active is true" in sql
    assert "not exists" in sql
    assert "returning" not in sql
    assert parameters == {
        "topic": "alerts",
        "current_keys": ["current", "new", "recurring"],
        "observed_at": parameters["observed_at"],
        "deactivate_missing": True,
        "report_entered": True,
    }


@pytest.mark.asyncio
async def test_primeiro_baseline_nao_devolve_snapshot_para_python_descartar():
    state = SimpleNamespace(observed_initialized_at=None)
    returned = SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))
    db = AsyncMock()
    db.execute.return_value = returned

    with (
        patch.object(repository, "lock_global_event_order", new=AsyncMock()),
        patch.object(
            repository,
            "_locked_topic_state",
            new=AsyncMock(return_value=state),
        ),
    ):
        result = await repository.observe_active_keys(
            db,
            topic="alerts",
            keys=[f"key-{index}" for index in range(10_000)],
            baseline_if_empty=True,
            allow_empty_snapshot=True,
        )

    assert result == []
    assert state.observed_initialized_at is not None
    assert db.execute.await_args.args[1]["report_entered"] is False


@pytest.mark.asyncio
async def test_snapshot_vazio_seguro_e_recorrencia_preservam_primeiro_avistamento():
    topic = f"lifecycle-{uuid4().hex}"
    entity_key = "order-123"
    first_seen_at = datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)

    async with async_session_factory() as db:
        try:
            await db.execute(
                text(
                    "INSERT INTO realtime_topic_state "
                    "(topic, observed_initialized_at) VALUES (:topic, now())"
                ),
                {"topic": topic},
            )
            await db.execute(
                text(
                    "INSERT INTO realtime_observed_entities "
                    "(topic, entity_key, first_seen_at, last_seen_at, active) "
                    "VALUES (:topic, :entity_key, :seen_at, :seen_at, TRUE)"
                ),
                {
                    "topic": topic,
                    "entity_key": entity_key,
                    "seen_at": first_seen_at,
                },
            )

            safe_empty = await repository.observe_active_keys(
                db,
                topic=topic,
                keys=[],
                baseline_if_empty=False,
                allow_empty_snapshot=False,
            )
            assert safe_empty == []
            assert (
                await db.scalar(
                    text(
                        "SELECT active FROM realtime_observed_entities "
                        "WHERE topic = :topic AND entity_key = :entity_key"
                    ),
                    {"topic": topic, "entity_key": entity_key},
                )
                is True
            )

            await repository.observe_active_keys(
                db,
                topic=topic,
                keys=[],
                baseline_if_empty=False,
                allow_empty_snapshot=True,
            )
            recurrent = await repository.observe_active_keys(
                db,
                topic=topic,
                keys=[entity_key],
                baseline_if_empty=False,
                allow_empty_snapshot=True,
            )
            unchanged = await repository.observe_active_keys(
                db,
                topic=topic,
                keys=[entity_key],
                baseline_if_empty=False,
                allow_empty_snapshot=True,
            )

            assert recurrent == [entity_key]
            assert unchanged == []
            timestamps = (
                await db.execute(
                    text(
                        "SELECT first_seen_at, last_seen_at "
                        "FROM realtime_observed_entities "
                        "WHERE topic = :topic AND entity_key = :entity_key"
                    ),
                    {"topic": topic, "entity_key": entity_key},
                )
            ).one()
            assert timestamps.first_seen_at == first_seen_at
            assert timestamps.last_seen_at > first_seen_at
        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_milhares_de_inativos_nao_entram_no_resultado_ou_snapshot_python():
    topic = f"bounded-{uuid4().hex}"
    keep = "keep"
    resolve = "resolve"
    recurring = "recurring"
    new = "new"

    async with async_session_factory() as db:
        try:
            await db.execute(
                text(
                    "INSERT INTO realtime_topic_state "
                    "(topic, observed_initialized_at) VALUES (:topic, now())"
                ),
                {"topic": topic},
            )
            await db.execute(
                text(
                    "INSERT INTO realtime_observed_entities "
                    "(topic, entity_key, active) "
                    "SELECT :topic, 'inactive-' || lpad(series::text, 6, '0'), FALSE "
                    "FROM generate_series(1, 20000) AS series"
                ),
                {"topic": topic},
            )
            await db.execute(
                text(
                    "INSERT INTO realtime_observed_entities "
                    "(topic, entity_key, active) VALUES "
                    "(:topic, :keep, TRUE), "
                    "(:topic, :resolve, TRUE), "
                    "(:topic, :recurring, FALSE)"
                ),
                {
                    "topic": topic,
                    "keep": keep,
                    "resolve": resolve,
                    "recurring": recurring,
                },
            )
            await db.execute(text("ANALYZE realtime_observed_entities"))

            plan = "\n".join(
                (
                    await db.execute(
                        text(
                            "EXPLAIN (COSTS OFF) "
                            + repository._OBSERVE_ACTIVE_KEYS_SQL.text
                        ),
                        {
                            "topic": topic,
                            "current_keys": [keep, new, recurring],
                            "observed_at": datetime.now(UTC),
                            "deactivate_missing": True,
                            "report_entered": True,
                        },
                    )
                )
                .scalars()
                .all()
            )
            assert _ACTIVE_INDEX in plan

            entered = await repository.observe_active_keys(
                db,
                topic=topic,
                keys=sorted([keep, new, recurring]),
                baseline_if_empty=False,
                allow_empty_snapshot=True,
            )

            assert entered == sorted([new, recurring])
            # entity_key é VARCHAR(256) e active é BOOLEAN no modelo ORM
            # (RealtimeObservedEntity); o cast só declara ao pyright a forma
            # que o SELECT de duas colunas já garante em runtime, já que
            # `Row[Any]` genérico não é reconhecido como tupla estruturada.
            rows = cast(
                "list[tuple[str, bool]]",
                (
                    await db.execute(
                        text(
                            "SELECT entity_key, active "
                            "FROM realtime_observed_entities "
                            "WHERE topic = :topic AND entity_key = ANY("
                            "CAST(:keys AS VARCHAR(256)[]))"
                        ),
                        {
                            "topic": topic,
                            "keys": [keep, resolve, recurring, new],
                        },
                    )
                ).all(),
            )
            states = dict(rows)
            assert states == {
                keep: True,
                resolve: False,
                recurring: True,
                new: True,
            }
            historical_count = await db.scalar(
                text(
                    "SELECT count(*) FROM realtime_observed_entities "
                    "WHERE topic = :topic"
                ),
                {"topic": topic},
            )
            assert historical_count == 20_004
        finally:
            await db.rollback()
