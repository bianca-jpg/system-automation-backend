"""Ciclo destrutivo isolado da migration 026 do ledger ativo realtime."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_URL_ENV = "MIGRATION_TEST_DATABASE_URL"
_DATABASE_PREFIX = "automation_migration_test"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "dev_db", "postgres"}
_INDEX_NAME = "ix_realtime_observed_entities_active_topic"


def _isolated_database_url() -> str:
    raw_url = os.getenv(_URL_ENV)
    if not raw_url:
        pytest.skip(f"set {_URL_ENV} to run destructive migration integration tests")
    parsed = make_url(raw_url)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail(f"{_URL_ENV} must use postgresql+asyncpg")
    if parsed.host not in _LOCAL_HOSTS:
        pytest.fail(f"{_URL_ENV} must point to local/Docker PostgreSQL")
    if not (parsed.database or "").startswith(_DATABASE_PREFIX):
        pytest.fail(f"{_URL_ENV} database must start with {_DATABASE_PREFIX!r}")
    return raw_url


async def _execute_async(
    database_url: str,
    statement: str,
    parameters: dict | None = None,
) -> list[tuple]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), parameters or {})
            return [tuple(row) for row in result] if result.returns_rows else []
    finally:
        await engine.dispose()


def _execute(
    database_url: str,
    statement: str,
    parameters: dict | None = None,
) -> list[tuple]:
    return asyncio.run(_execute_async(database_url, statement, parameters))


def _reset(database_url: str) -> None:
    _execute(database_url, "DROP SCHEMA IF EXISTS public CASCADE")
    _execute(database_url, "CREATE SCHEMA public")


def _alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )


@pytest.fixture
def migration_database_url() -> Iterable[str]:
    database_url = _isolated_database_url()
    _reset(database_url)
    try:
        yield database_url
    finally:
        _reset(database_url)


def test_fresh_upgrade_indice_parcial_plano_e_ciclo_025(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "026")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("026",)]

    catalog = _execute(
        migration_database_url,
        "SELECT i.indisvalid, i.indisready, "
        "pg_get_expr(i.indpred, i.indrelid), pg_get_indexdef(i.indexrelid) "
        "FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = :index_name",
        {"index_name": _INDEX_NAME},
    )
    assert len(catalog) == 1
    assert catalog[0][0] is True
    assert catalog[0][1] is True
    assert "active IS TRUE" in catalog[0][2]
    assert "(topic) WHERE (active IS TRUE)" in catalog[0][3]

    topic = f"migration-{uuid4().hex}"
    _execute(
        migration_database_url,
        "INSERT INTO realtime_topic_state (topic, observed_initialized_at) "
        "VALUES (:topic, now())",
        {"topic": topic},
    )
    _execute(
        migration_database_url,
        "INSERT INTO realtime_observed_entities (topic, entity_key, active) "
        "SELECT :topic, 'inactive-' || series::text, FALSE "
        "FROM generate_series(1, 20000) AS series",
        {"topic": topic},
    )
    _execute(
        migration_database_url,
        "INSERT INTO realtime_observed_entities (topic, entity_key, active) "
        "VALUES (:topic, 'active-a', TRUE), (:topic, 'active-b', TRUE)",
        {"topic": topic},
    )
    _execute(migration_database_url, "ANALYZE realtime_observed_entities")
    plan = "\n".join(
        row[0]
        for row in _execute(
            migration_database_url,
            "EXPLAIN (COSTS OFF) "
            "SELECT entity_key FROM realtime_observed_entities "
            "WHERE topic = :topic AND active IS TRUE",
            {"topic": topic},
        )
    )
    assert _INDEX_NAME in plan

    _alembic(migration_database_url, "downgrade", "025")
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.' || :index_name), "
        "to_regclass('public.realtime_observed_entities')",
        {"index_name": _INDEX_NAME},
    ) == [(None, "realtime_observed_entities")]
    _alembic(migration_database_url, "upgrade", "026")
