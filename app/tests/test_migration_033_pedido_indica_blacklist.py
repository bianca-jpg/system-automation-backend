"""Ciclo destrutivo isolado da migration 033 (indica_blacklist + ck_psm_motivo
com 4 motivos). Mesmos helpers de `test_migration_032_pedido_standby_motivo.py`."""

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


def test_upgrade_adiciona_colunas_e_amplia_check_e_downgrade_reverte(
    migration_database_url: str,
) -> None:
    _alembic(migration_database_url, "upgrade", "032")
    _alembic(migration_database_url, "upgrade", "033")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("033",)]

    # (1) upgrade para 033 adiciona indica_blacklist em pedidos e
    # pedido_produto_read.
    for tabela in ("pedidos", "pedido_produto_read"):
        colunas = {
            row[0]
            for row in _execute(
                migration_database_url,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :tabela",
                {"tabela": tabela},
            )
        }
        assert "indica_blacklist" in colunas, f"{tabela} sem indica_blacklist"

    # (2) ck_psm_motivo aceita 'blacklist' e continua rejeitando motivo fora
    # dos 4 permitidos.
    _execute(
        migration_database_url,
        "INSERT INTO pedido_standby_motivo "
        "(nr_pedido, cd_prod_cor, canal, motivo, job_id, atualizado_em) "
        "VALUES (:nr_pedido, :cd_prod_cor, 'Franquia', 'blacklist', "
        ":job_id, now())",
        {
            "nr_pedido": 2001,
            "cd_prod_cor": "PROD001COR01",
            "job_id": "33333333-3333-3333-3333-333333333333",
        },
    )
    assert _execute(
        migration_database_url,
        "SELECT motivo FROM pedido_standby_motivo "
        "WHERE nr_pedido = 2001 AND cd_prod_cor = 'PROD001COR01'",
    ) == [("blacklist",)]

    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO pedido_standby_motivo "
            "(nr_pedido, cd_prod_cor, canal, motivo, job_id, atualizado_em) "
            "VALUES (:nr_pedido, :cd_prod_cor, 'Franquia', 'invalido', "
            ":job_id, now())",
            {
                "nr_pedido": 2002,
                "cd_prod_cor": "PROD002COR01",
                "job_id": "44444444-4444-4444-4444-444444444444",
            },
        )

    # (3) downgrade para 032 reverte as 2 colunas e o CHECK volta a rejeitar
    # 'blacklist'. A linha 'blacklist' inserida acima precisa ser removida
    # antes: o downgrade recria o CHECK restrito a 3 valores, e Postgres
    # valida dados existentes contra a constraint nova — corretamente
    # recusaria o downgrade se uma linha 'blacklist' ainda estivesse
    # presente (mesma trava de integridade de qualquer CHECK mais restritivo).
    _execute(
        migration_database_url,
        "DELETE FROM pedido_standby_motivo WHERE motivo = 'blacklist'",
    )
    _alembic(migration_database_url, "downgrade", "032")
    colunas_pedidos = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'pedidos'",
        )
    }
    colunas_ppr = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'pedido_produto_read'",
        )
    }
    assert "indica_blacklist" not in colunas_pedidos
    assert "indica_blacklist" not in colunas_ppr

    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            "INSERT INTO pedido_standby_motivo "
            "(nr_pedido, cd_prod_cor, canal, motivo, job_id, atualizado_em) "
            "VALUES (:nr_pedido, :cd_prod_cor, 'Franquia', 'blacklist', "
            ":job_id, now())",
            {
                "nr_pedido": 2003,
                "cd_prod_cor": "PROD003COR01",
                "job_id": "55555555-5555-5555-5555-555555555555",
            },
        )

    # (4) re-upgrade para 033 é limpo.
    _alembic(migration_database_url, "upgrade", "033")
    assert _execute(
        migration_database_url,
        "SELECT version_num FROM alembic_version",
    ) == [("033",)]
    colunas_pedidos = {
        row[0]
        for row in _execute(
            migration_database_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'pedidos'",
        )
    }
    assert "indica_blacklist" in colunas_pedidos
