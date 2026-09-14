"""Ciclo destrutivo isolado da migration 035 (remove password_hash/user_name/
cpf/phone de auth_users e a tabela auth_otp_challenges; backfill de
confirmed_at). Mesmos helpers de `test_migration_033_pedido_indica_blacklist.py`."""

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


def test_upgrade_dropa_colunas_e_tabela_otp_com_backfill_e_downgrade_reverte_estrutura(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "034")

    # Fase 1: duas linhas em auth_users (uma confirmed_at nulo, outra
    # preenchido) e uma linha em auth_otp_challenges, ainda no schema pós-034.
    _execute(
        migration_database_url,
        "INSERT INTO auth_users "
        "(email, password_hash, user_name, cpf, phone, roles, auth_provider, "
        "confirmed_at) VALUES "
        "(:email, 'hash-legado', 'Fulano', '11122233344', '11999990000', "
        "'[\"basico\"]'::jsonb, 'local', NULL)",
        {"email": "legado.nao-confirmado@example.com"},
    )
    _execute(
        migration_database_url,
        "INSERT INTO auth_users "
        "(email, password_hash, roles, auth_provider, confirmed_at) VALUES "
        "(:email, NULL, '[\"basico\"]'::jsonb, 'microsoft', now())",
        {"email": "sso.confirmado@example.com"},
    )
    _execute(
        migration_database_url,
        "INSERT INTO auth_otp_challenges "
        "(email, code, purpose, expires_at) VALUES "
        "(:email, 'digest-hmac-64-chars-fake', 'register', now() + interval '15 minutes')",
        {"email": "legado.nao-confirmado@example.com"},
    )

    _alembic(migration_database_url, "upgrade", "035")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("035",)]

    # Fase 2: as 4 colunas somem de auth_users; auth_otp_challenges some de
    # information_schema.tables; backfill prova o comportamento sem
    # falso-positivo (a linha que já tinha confirmed_at não foi tocada).
    colunas_auth_users = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'auth_users'",
        )
    }
    for coluna in ("password_hash", "user_name", "cpf", "phone"):
        assert coluna not in colunas_auth_users, f"auth_users ainda tem {coluna}"

    tabelas = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'",
        )
    }
    assert "auth_otp_challenges" not in tabelas

    linha_legada = _execute(
        migration_database_url,
        "SELECT confirmed_at FROM auth_users WHERE email = :email",
        {"email": "legado.nao-confirmado@example.com"},
    )
    assert linha_legada[0][0] is not None, "backfill nao preencheu confirmed_at"

    confirmado_antes = _execute(
        migration_database_url,
        "SELECT confirmed_at FROM auth_users WHERE email = :email",
        {"email": "sso.confirmado@example.com"},
    )
    assert confirmado_antes[0][0] is not None

    # Fase 3: downgrade para 034 restaura as 4 colunas (todas nulláveis),
    # auth_otp_challenges volta com code String(64) e o índice de email.
    _alembic(migration_database_url, "downgrade", "034")

    colunas_auth_users_depois = {
        row[0]: row[1]
        for row in _execute(
            migration_database_url,
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'auth_users'",
        )
    }
    for coluna in ("password_hash", "user_name", "cpf", "phone"):
        assert coluna in colunas_auth_users_depois, f"downgrade nao restaurou {coluna}"
        assert colunas_auth_users_depois[coluna] == "YES", (
            f"{coluna} deveria ser nullable apos downgrade"
        )

    code_col = _execute(
        migration_database_url,
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_name = 'auth_otp_challenges' AND column_name = 'code'",
    )
    assert code_col == [(64,)]

    indices = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT indexname FROM pg_indexes WHERE tablename = 'auth_otp_challenges'",
        )
    }
    assert "ix_auth_otp_challenges_email" in indices

    # Fase 4: re-upgrade para 035 é limpo (idempotência do ciclo).
    _alembic(migration_database_url, "upgrade", "035")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("035",)]
