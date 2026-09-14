"""Ciclo destrutivo isolado da migration 024 de processamento de Pedidos."""

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_URL_ENV = "MIGRATION_TEST_DATABASE_URL"
_DATABASE_PREFIX = "automation_migration_test"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "dev_db", "postgres"}


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


def _insert_job(database_url: str) -> str:
    job_id = str(uuid4())
    _execute(
        database_url,
        "INSERT INTO durable_jobs "
        "(id, kind, owner_id, scope_key, fingerprint, deadline_at) "
        "VALUES (:id, 'orders.processing.v1', 42, 'adequar:Franquia', "
        "repeat('a', 64), now() + interval '15 minutes')",
        {"id": job_id},
    )
    return job_id


def test_fresh_upgrade_catalog_constraints_and_023_cycle(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "024")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("024",)]
    assert {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'pedido_processamento%'",
        )
    } == {"pedido_processamentos", "pedido_processamento_plan"}

    checks = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid IN ('pedido_processamentos'::regclass, "
            "'pedido_processamento_plan'::regclass) AND contype='c'",
        )
    }
    assert {
        "ck_pedido_processamentos_counts",
        "ck_pedido_processamentos_checkpoint",
        "ck_pedido_processamentos_plan_state",
        "ck_pedido_processamento_plan_payload",
        "ck_pedido_processamento_plan_applied_state",
    }.issubset(checks)
    indexes = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='pedido_processamento_plan'",
        )
    }
    assert "ix_pedido_processamento_plan_pending" in indexes

    job_id = _insert_job(migration_database_url)
    _execute(
        migration_database_url,
        "INSERT INTO pedido_processamentos (job_id, mode, channel) "
        "VALUES (:id, 'adequar', 'Franquia')",
        {"id": job_id},
    )
    # jsonb::text de {"x": "..."} mede payload + 9 bytes. A fronteira
    # aceita pelo domínio é exatamente a mesma validada pelo CHECK.
    _execute(
        migration_database_url,
        "INSERT INTO pedido_processamento_plan "
        "(job_id, ordinal, nr_pedido, cd_prod_cor, payload, payload_hash) "
        "VALUES (:id, 1, 1, 'PROD', "
        "jsonb_build_object('x', repeat('x', 131063)), repeat('b', 64))",
        {"id": job_id},
    )
    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO pedido_processamento_plan "
            "(job_id, ordinal, nr_pedido, cd_prod_cor, payload, payload_hash) "
            "VALUES (:id, 2, 2, 'PROD2', jsonb_build_object('x', repeat('x', 131064)), "
            "repeat('b', 64))",
            {"id": job_id},
        )

    _alembic(migration_database_url, "downgrade", "023")
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.pedido_processamentos'), "
        "to_regclass('public.pedido_processamento_plan'), "
        "to_regclass('public.durable_jobs')",
    ) == [(None, None, "durable_jobs")]
    _alembic(migration_database_url, "upgrade", "024")
