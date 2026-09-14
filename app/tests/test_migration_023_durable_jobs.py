"""PostgreSQL scratch-cycle proof for durable jobs migration 023."""

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
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _isolated_database_url() -> str:
    raw_url = os.getenv(_URL_ENV)
    if not raw_url:
        pytest.skip(f"set {_URL_ENV} to run destructive migration integration tests")
    parsed = make_url(raw_url)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail(f"{_URL_ENV} must use postgresql+asyncpg")
    if parsed.host not in _LOOPBACK_HOSTS:
        pytest.fail(f"{_URL_ENV} must point to loopback PostgreSQL")
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


def _insert_queued(
    database_url: str,
    *,
    kind: str,
    owner_id: int | None,
    scope_key: str | None,
    digest: str | None,
) -> str:
    job_id = str(uuid4())
    _execute(
        database_url,
        "INSERT INTO durable_jobs "
        "(id, kind, owner_id, scope_key, idempotency_key_hash, fingerprint) "
        "VALUES (:id, :kind, :owner, :scope, :digest, repeat('a', 64))",
        {
            "id": job_id,
            "kind": kind,
            "owner": owner_id,
            "scope": scope_key,
            "digest": digest,
        },
    )
    return job_id


def test_upgrade_constraints_partial_indexes_and_downgrade(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "022")
    _alembic(migration_database_url, "upgrade", "023")

    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("023",)]
    index_names = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'durable_jobs'",
        )
    }
    assert {
        "uq_durable_jobs_owner_idempotency",
        "uq_durable_jobs_system_idempotency",
        "uq_durable_jobs_active_scope",
        "ix_durable_jobs_dispatchable",
        "ix_durable_jobs_expired_lease",
        "ix_durable_jobs_pending_deadline",
        "ix_durable_jobs_terminal_retention",
    }.issubset(index_names)
    constraint_names = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.durable_jobs'::regclass",
        )
    }
    assert {
        "ck_durable_jobs_status",
        "ck_durable_jobs_dispatchable_attempts",
        "ck_durable_jobs_result_bounded",
        "ck_durable_jobs_lease_owner_safe",
        "ck_durable_jobs_lease_state",
        "ck_durable_jobs_terminal_state",
    }.issubset(constraint_names)

    first_id = _insert_queued(
        migration_database_url,
        kind="ingestion.sync",
        owner_id=10,
        scope_key="global",
        digest="b" * 64,
    )
    with pytest.raises(IntegrityError):
        _insert_queued(
            migration_database_url,
            kind="ingestion.sync",
            owner_id=11,
            scope_key="global",
            digest="c" * 64,
        )
    with pytest.raises(IntegrityError):
        _insert_queued(
            migration_database_url,
            kind="orders.processing",
            owner_id=10,
            scope_key="channel:Franquia",
            digest="b" * 64,
        )

    _execute(
        migration_database_url,
        "UPDATE durable_jobs SET status = 'succeeded', finished_at = now() "
        "WHERE id = :id",
        {"id": first_id},
    )
    _insert_queued(
        migration_database_url,
        kind="ingestion.sync",
        owner_id=11,
        scope_key="global",
        digest="d" * 64,
    )
    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO durable_jobs (id, kind, fingerprint, result) "
            "VALUES (:id, 'test.large', repeat('e', 64), "
            "jsonb_build_object('value', repeat('x', 65536)))",
            {"id": str(uuid4())},
        )

    _alembic(migration_database_url, "downgrade", "022")
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.durable_jobs')",
    ) == [(None,)]
    _alembic(migration_database_url, "upgrade", "023")
