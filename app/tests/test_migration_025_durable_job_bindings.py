"""Scratch PostgreSQL proof for durable idempotency bindings migration 025."""

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


def _insert_job(
    database_url: str,
    *,
    owner_id: int | None,
    digest: str,
    fingerprint: str,
) -> str:
    job_id = str(uuid4())
    _execute(
        database_url,
        "INSERT INTO durable_jobs "
        "(id, kind, owner_id, idempotency_key_hash, fingerprint) "
        "VALUES (:id, 'ingestion.sync', :owner, :digest, :fingerprint)",
        {
            "id": job_id,
            "owner": owner_id,
            "digest": digest,
            "fingerprint": fingerprint,
        },
    )
    return job_id


def test_backfill_uniques_cascade_and_024_025_cycle(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "024")
    owner_job_id = _insert_job(
        migration_database_url,
        owner_id=10,
        digest="a" * 64,
        fingerprint="b" * 64,
    )
    system_job_id = _insert_job(
        migration_database_url,
        owner_id=None,
        digest="c" * 64,
        fingerprint="d" * 64,
    )

    _alembic(migration_database_url, "upgrade", "025")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("025",)]
    columns = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'durable_job_idempotency_bindings'",
        )
    }
    assert columns == {
        "id",
        "job_id",
        "owner_id",
        "idempotency_key_hash",
        "kind",
        "fingerprint",
        "created_at",
    }
    assert _execute(
        migration_database_url,
        "SELECT job_id::text, owner_id, idempotency_key_hash, kind, fingerprint, "
        "created_at = (SELECT requested_at FROM durable_jobs j WHERE j.id = b.job_id) "
        "FROM durable_job_idempotency_bindings b ORDER BY owner_id NULLS LAST",
    ) == [
        (owner_job_id, 10, "a" * 64, "ingestion.sync", "b" * 64, True),
        (system_job_id, None, "c" * 64, "ingestion.sync", "d" * 64, True),
    ]

    index_names = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'durable_job_idempotency_bindings'",
        )
    }
    assert {
        "uq_durable_job_binding_owner_key",
        "uq_durable_job_binding_system_key",
        "ix_durable_job_binding_job_id",
    }.issubset(index_names)
    assert _execute(
        migration_database_url,
        "SELECT delete_rule FROM information_schema.referential_constraints "
        "WHERE constraint_name = 'fk_durable_job_binding_job'",
    ) == [("CASCADE",)]

    # A second authorized actor can bind its key to the same coalesced job.
    _execute(
        migration_database_url,
        "INSERT INTO durable_job_idempotency_bindings "
        "(job_id, owner_id, idempotency_key_hash, kind, fingerprint) "
        "VALUES (:job_id, 11, :digest, 'ingestion.sync', :fingerprint)",
        {
            "job_id": owner_job_id,
            "digest": "e" * 64,
            "fingerprint": "b" * 64,
        },
    )
    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO durable_job_idempotency_bindings "
            "(job_id, owner_id, idempotency_key_hash, kind, fingerprint) "
            "VALUES (:job_id, 10, :digest, 'ingestion.sync', :fingerprint)",
            {
                "job_id": owner_job_id,
                "digest": "a" * 64,
                "fingerprint": "b" * 64,
            },
        )
    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO durable_job_idempotency_bindings "
            "(job_id, owner_id, idempotency_key_hash, kind, fingerprint) "
            "VALUES (:job_id, NULL, :digest, 'ingestion.sync', :fingerprint)",
            {
                "job_id": system_job_id,
                "digest": "c" * 64,
                "fingerprint": "d" * 64,
            },
        )

    _execute(
        migration_database_url,
        "DELETE FROM durable_jobs WHERE id = :job_id",
        {"job_id": owner_job_id},
    )
    assert _execute(
        migration_database_url,
        "SELECT count(*) FROM durable_job_idempotency_bindings WHERE job_id = :job_id",
        {"job_id": owner_job_id},
    ) == [(0,)]

    _alembic(migration_database_url, "downgrade", "024")
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.durable_job_idempotency_bindings')",
    ) == [(None,)]
    _alembic(migration_database_url, "upgrade", "025")
    assert _execute(
        migration_database_url,
        "SELECT job_id::text FROM durable_job_idempotency_bindings",
    ) == [(system_job_id,)]
