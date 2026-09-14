"""Ciclo e invariantes da migration 022 em PostgreSQL local descartável."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
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


def _alembic(
    database_url: str,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=environment,
        check=check,
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


def _revision(database_url: str) -> str:
    return str(_execute(database_url, "SELECT version_num FROM alembic_version")[0][0])


def _insert_actor(database_url: str, actor_id: int, email: str) -> None:
    _execute(
        database_url,
        "INSERT INTO auth_users "
        "(id, email, password_hash, roles, confirmed_at) "
        "VALUES (:id, :email, 'test', '[\"operacional\"]'::jsonb, now())",
        {"id": actor_id, "email": email},
    )


def _insert_communication(
    database_url: str,
    *,
    communication_id: str,
    idempotency_key: str,
    actor_id: int | None = None,
) -> None:
    columns = (
        "id, type, status, time, content, recipient, idempotency_key, "
        "idempotency_fingerprint"
    )
    values = (
        ":id, 'Email', 'Enviado', '10:00', 'legacy', 'destino@project.com', "
        ":key, repeat('a', 64)"
    )
    parameters: dict[str, object] = {"id": communication_id, "key": idempotency_key}
    if actor_id is not None:
        columns += ", requested_by_user_id"
        values += ", :actor_id"
        parameters["actor_id"] = actor_id
    _execute(
        database_url,
        f"INSERT INTO comunicacoes ({columns}) VALUES ({values})",
        parameters,
    )


def test_legacy_enviado_nao_e_reenfileirado_e_ciclo_021_022_funciona(
    migration_database_url: str,
):
    _alembic(migration_database_url, "upgrade", "021")
    _insert_communication(
        migration_database_url,
        communication_id="comm-legacy",
        idempotency_key="legacy-key",
    )

    _alembic(migration_database_url, "upgrade", "022")
    assert _revision(migration_database_url) == "022"
    assert _execute(
        migration_database_url,
        "SELECT status FROM comunicacoes WHERE id = 'comm-legacy'",
    ) == [("Enviado",)]
    assert _execute(
        migration_database_url,
        "SELECT count(*) FROM communication_email_deliveries",
    ) == [(0,)]

    constraint_names = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.communication_email_deliveries'::regclass",
        )
    }
    assert {
        "ck_communication_email_delivery_status",
        "ck_communication_email_delivery_attempts",
        "ck_communication_email_delivery_pending_attempts",
        "ck_communication_email_delivery_lease_state",
        "ck_communication_email_delivery_terminal_state",
    }.issubset(constraint_names)

    _alembic(migration_database_url, "downgrade", "021")
    assert _revision(migration_database_url) == "021"
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.communication_email_deliveries')",
    ) == [(None,)]
    _alembic(migration_database_url, "upgrade", "022")
    assert _revision(migration_database_url) == "022"


def test_downgrade_com_chave_repetida_entre_atores_falha_atomicamente(
    migration_database_url: str,
):
    _alembic(migration_database_url, "upgrade", "022")
    _insert_actor(migration_database_url, 700001, "actor-one@example.test")
    _insert_actor(migration_database_url, 700002, "actor-two@example.test")
    _insert_communication(
        migration_database_url,
        communication_id="comm-actor-one",
        idempotency_key="same-key",
        actor_id=700001,
    )
    _insert_communication(
        migration_database_url,
        communication_id="comm-actor-two",
        idempotency_key="same-key",
        actor_id=700002,
    )

    failed = _alembic(
        migration_database_url,
        "downgrade",
        "021",
        check=False,
    )
    assert failed.returncode != 0
    assert _revision(migration_database_url) == "022"
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.communication_email_deliveries') IS NOT NULL",
    ) == [(True,)]
    assert _execute(
        migration_database_url,
        "SELECT count(*) FROM comunicacoes WHERE idempotency_key = 'same-key'",
    ) == [(2,)]
