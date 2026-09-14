"""Ciclo destrutivo isolado da migration 032 (tabela pedido_standby_motivo)."""

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


def test_upgrade_cria_tabela_com_check_de_tres_motivos_e_downgrade_reverte(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "032")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("032",)]

    colunas_esperadas = {
        "nr_pedido",
        "cd_prod_cor",
        "canal",
        "motivo",
        "execucoes_consecutivas",
        "job_id",
        "atualizado_em",
    }
    colunas_no_banco = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'pedido_standby_motivo'",
        )
    }
    assert colunas_no_banco == colunas_esperadas

    _execute(
        migration_database_url,
        "INSERT INTO pedido_standby_motivo "
        "(nr_pedido, cd_prod_cor, canal, motivo, job_id, atualizado_em) "
        "VALUES (:nr_pedido, :cd_prod_cor, 'Franquia', 'furo_grade', "
        ":job_id, now())",
        {
            "nr_pedido": 1001,
            "cd_prod_cor": "PROD001COR01",
            "job_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert _execute(
        migration_database_url,
        "SELECT motivo FROM pedido_standby_motivo "
        "WHERE nr_pedido = 1001 AND cd_prod_cor = 'PROD001COR01'",
    ) == [("furo_grade",)]

    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO pedido_standby_motivo "
            "(nr_pedido, cd_prod_cor, canal, motivo, job_id, atualizado_em) "
            "VALUES (:nr_pedido, :cd_prod_cor, 'Franquia', 'invalido', "
            ":job_id, now())",
            {
                "nr_pedido": 1002,
                "cd_prod_cor": "PROD002COR01",
                "job_id": "22222222-2222-2222-2222-222222222222",
            },
        )

    _alembic(migration_database_url, "downgrade", "031")
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.pedido_standby_motivo')",
    ) == [(None,)]

    _alembic(migration_database_url, "upgrade", "032")
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.pedido_standby_motivo')",
    ) == [("pedido_standby_motivo",)]
    assert _execute(
        migration_database_url,
        "SELECT count(*) FROM pedido_standby_motivo",
    ) == [(0,)]
