"""Integration tests for the one-way financial normalization in revision 019.

These tests are deliberately opt-in because they reset ``public`` and exercise
real Alembic transitions. They only accept a loopback database whose name starts
with ``automation_migration_test``; this prevents an inherited DEV/PROD URL from
turning a migration test into a destructive operation.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterable
from decimal import Decimal
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
        pytest.fail(f"{_URL_ENV} must use the postgresql+asyncpg driver")
    if parsed.host not in _LOOPBACK_HOSTS:
        pytest.fail(f"{_URL_ENV} must point to a loopback PostgreSQL host")
    if not (parsed.database or "").startswith(_DATABASE_PREFIX):
        pytest.fail(f"{_URL_ENV} database name must start with {_DATABASE_PREFIX!r}")
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
            if not result.returns_rows:
                return []
            return [tuple(row) for row in result]
    finally:
        await engine.dispose()


def _execute(
    database_url: str,
    statement: str,
    parameters: dict | None = None,
) -> list[tuple]:
    return asyncio.run(_execute_async(database_url, statement, parameters))


def _reset_public_schema(database_url: str) -> None:
    _execute(database_url, "DROP SCHEMA IF EXISTS public CASCADE")
    _execute(database_url, "CREATE SCHEMA public")


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture
def migration_database_url() -> Iterable[str]:
    database_url = _isolated_database_url()
    _reset_public_schema(database_url)
    try:
        yield database_url
    finally:
        _reset_public_schema(database_url)


def _current_revision(database_url: str) -> str:
    rows = _execute(database_url, "SELECT version_num FROM alembic_version")
    assert rows
    return str(rows[0][0])


def _seed_revision_018(database_url: str) -> None:
    _execute(
        database_url,
        """
        INSERT INTO pedidos (
            nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo,
            cd_colecao_ped, qt_entregar, vl_liquido
        ) VALUES
            (101, 'PROP', 'M', 'TESTE', 1, 3, 100.00),
            (101, 'PROP', 'P', 'TESTE', 1, 2, 100.00),
            (102, 'TIE', 'P', 'TESTE', 1, 1, 100.00),
            (102, 'TIE', 'G', 'TESTE', 1, 1, 100.00),
            (102, 'TIE', 'M', 'TESTE', 1, 1, 100.00),
            (103, 'REMAINDER', 'A', 'TESTE', 1, 1, 0.05),
            (103, 'REMAINDER', 'B', 'TESTE', 1, 1, 0.05),
            (103, 'REMAINDER', 'C', 'TESTE', 1, 2, 0.05),
            (104, 'DIVERGENT', 'A', 'TESTE', 1, 1, 90.00),
            (104, 'DIVERGENT', 'B', 'TESTE', 1, 3, 100.00),
            (105, 'ZERO_LINE', 'A', 'TESTE', 1, 0, 10.00),
            (105, 'ZERO_LINE', 'B', 'TESTE', 1, 2, 10.00),
            (106, 'NO_QTY', 'A', 'TESTE', 1, 0, 10.00),
            (106, 'NO_QTY', 'B', 'TESTE', 1, 0, 10.00),
            (107, 'NO_TOTAL', 'A', 'TESTE', 1, 1, 0.00),
            (107, 'NO_TOTAL', 'B', 'TESTE', 1, 2, 0.00),
            (108, 'SINGLE', 'U', 'TESTE', 1, 7, 12.34),
            (109, 'PENDING_NEG', 'A', 'TESTE', 1, 1, -4.00),
            (109, 'PENDING_NEG', 'B', 'TESTE', 1, 3, -4.00)
        """,
    )
    _execute(
        database_url,
        """
        INSERT INTO pedidos_processados_erp (
            nr_pedido, cd_prod_cor, sg_tamanho, qt, vl_liquido,
            indica_reserva, indica_embalado
        ) VALUES
            (201, 'ERP_PROP', 'A', 1, 10.01, true, false),
            (201, 'ERP_PROP', 'B', 2, 10.01, true, false),
            (202, 'ERP_ZERO_LINE', 'A', 0, 5.00, false, true),
            (202, 'ERP_ZERO_LINE', 'B', 4, 5.00, false, true),
            (203, 'ERP_NO_QTY', 'A', 0, 7.00, false, true),
            (203, 'ERP_NO_QTY', 'B', 0, 7.00, false, true),
            (204, 'ERP_NEG_REAL', 'A', 2, -10.92, false, true),
            (204, 'ERP_NEG_REAL', 'B', 3, -10.92, false, true),
            (204, 'ERP_NEG_REAL', 'C', 5, -10.92, false, true),
            (204, 'ERP_NEG_REAL', 'D', 8, -10.92, false, true),
            (205, 'ERP_NEG_EXACT', 'A', 3, -12.48, false, true),
            (205, 'ERP_NEG_EXACT', 'B', 5, -12.48, false, true),
            (206, 'ERP_NEG_DIVERGENT', 'A', 1, -10.00, false, true),
            (206, 'ERP_NEG_DIVERGENT', 'B', 1, -11.00, false, true)
        """,
    )


def _values_by_size(
    database_url: str,
    table_name: str,
) -> dict[tuple[int, str, str], Decimal]:
    assert table_name in {"pedidos", "pedidos_processados_erp"}
    rows = _execute(
        database_url,
        f"""
        SELECT nr_pedido, cd_prod_cor, sg_tamanho, vl_liquido
        FROM {table_name}
        ORDER BY nr_pedido, cd_prod_cor, sg_tamanho COLLATE "C"
        """,
    )
    return {
        (int(order_number), str(product), str(size)): Decimal(value)
        for order_number, product, size, value in rows
    }


def test_fresh_upgrade_reaches_019_with_financial_schema_intact(
    migration_database_url: str,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "019")

    assert _current_revision(migration_database_url) == "019"
    columns = _execute(
        migration_database_url,
        """
        SELECT table_name, data_type, numeric_precision, numeric_scale, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('pedidos', 'pedidos_processados_erp')
          AND column_name = 'vl_liquido'
        ORDER BY table_name
        """,
    )
    assert columns == [
        ("pedidos", "numeric", 14, 2, "NO"),
        ("pedidos_processados_erp", "numeric", 14, 2, "NO"),
    ]


def test_018_to_019_allocates_exact_cents_and_rejects_downgrade(
    migration_database_url: str,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "018")
    _seed_revision_018(migration_database_url)

    _run_alembic(migration_database_url, "upgrade", "019")
    assert _current_revision(migration_database_url) == "019"

    pedidos = _values_by_size(migration_database_url, "pedidos")
    assert pedidos[(101, "PROP", "M")] == Decimal("60.00")
    assert pedidos[(101, "PROP", "P")] == Decimal("40.00")
    assert pedidos[(102, "TIE", "G")] == Decimal("33.34")
    assert pedidos[(102, "TIE", "M")] == Decimal("33.33")
    assert pedidos[(102, "TIE", "P")] == Decimal("33.33")
    assert pedidos[(103, "REMAINDER", "A")] == Decimal("0.01")
    assert pedidos[(103, "REMAINDER", "B")] == Decimal("0.01")
    assert pedidos[(103, "REMAINDER", "C")] == Decimal("0.03")
    # Valores divergentes não confirmam qual era o total repetido original.
    assert pedidos[(104, "DIVERGENT", "A")] == Decimal("90.00")
    assert pedidos[(104, "DIVERGENT", "B")] == Decimal("100.00")
    assert pedidos[(105, "ZERO_LINE", "A")] == Decimal("0.00")
    assert pedidos[(105, "ZERO_LINE", "B")] == Decimal("10.00")
    assert pedidos[(106, "NO_QTY", "A")] == Decimal("10.00")
    assert pedidos[(106, "NO_QTY", "B")] == Decimal("10.00")
    assert pedidos[(107, "NO_TOTAL", "A")] == Decimal("0.00")
    assert pedidos[(107, "NO_TOTAL", "B")] == Decimal("0.00")
    assert pedidos[(108, "SINGLE", "U")] == Decimal("12.34")
    # Pending não possui contrato signed: um total negativo é preservado para
    # investigação em vez de ser reinterpretado silenciosamente.
    assert pedidos[(109, "PENDING_NEG", "A")] == Decimal("-4.00")
    assert pedidos[(109, "PENDING_NEG", "B")] == Decimal("-4.00")

    erp = _values_by_size(migration_database_url, "pedidos_processados_erp")
    assert erp[(201, "ERP_PROP", "A")] == Decimal("3.34")
    assert erp[(201, "ERP_PROP", "B")] == Decimal("6.67")
    assert erp[(202, "ERP_ZERO_LINE", "A")] == Decimal("0.00")
    assert erp[(202, "ERP_ZERO_LINE", "B")] == Decimal("5.00")
    assert erp[(203, "ERP_NO_QTY", "A")] == Decimal("7.00")
    assert erp[(203, "ERP_NO_QTY", "B")] == Decimal("7.00")
    # ERP signed usa Hamilton sobre o módulo e reaplica o sinal. O centavo
    # residual empata entre A/C/D e vai deterministicamente para A.
    assert erp[(204, "ERP_NEG_REAL", "A")] == Decimal("-1.22")
    assert erp[(204, "ERP_NEG_REAL", "B")] == Decimal("-1.82")
    assert erp[(204, "ERP_NEG_REAL", "C")] == Decimal("-3.03")
    assert erp[(204, "ERP_NEG_REAL", "D")] == Decimal("-4.85")
    assert erp[(205, "ERP_NEG_EXACT", "A")] == Decimal("-4.68")
    assert erp[(205, "ERP_NEG_EXACT", "B")] == Decimal("-7.80")
    # Divergência não confirma qual era o total original, mesmo quando signed.
    assert erp[(206, "ERP_NEG_DIVERGENT", "A")] == Decimal("-10.00")
    assert erp[(206, "ERP_NEG_DIVERGENT", "B")] == Decimal("-11.00")

    expected_pair_totals = {
        (101, "PROP"): Decimal("100.00"),
        (102, "TIE"): Decimal("100.00"),
        (103, "REMAINDER"): Decimal("0.05"),
        (105, "ZERO_LINE"): Decimal("10.00"),
        (108, "SINGLE"): Decimal("12.34"),
    }
    actual_pair_totals = {
        (int(order_number), str(product)): Decimal(total)
        for order_number, product, total in _execute(
            migration_database_url,
            """
            SELECT nr_pedido, cd_prod_cor, SUM(vl_liquido)
            FROM pedidos
            WHERE nr_pedido IN (101, 102, 103, 105, 108)
            GROUP BY nr_pedido, cd_prod_cor
            """,
        )
    }
    assert actual_pair_totals == expected_pair_totals

    erp_signed_pair_totals = {
        (int(order_number), str(product)): Decimal(total)
        for order_number, product, total in _execute(
            migration_database_url,
            """
            SELECT nr_pedido, cd_prod_cor, SUM(vl_liquido)
            FROM pedidos_processados_erp
            WHERE nr_pedido IN (204, 205)
            GROUP BY nr_pedido, cd_prod_cor
            """,
        )
    }
    assert erp_signed_pair_totals == {
        (204, "ERP_NEG_REAL"): Decimal("-10.92"),
        (205, "ERP_NEG_EXACT"): Decimal("-12.48"),
    }

    pedidos_before_downgrade = pedidos.copy()
    erp_before_downgrade = erp.copy()
    with pytest.raises(subprocess.CalledProcessError) as error:
        _run_alembic(migration_database_url, "downgrade", "018")

    assert "Revision 019 is irreversible" in error.value.stderr
    assert _current_revision(migration_database_url) == "019"
    assert _values_by_size(migration_database_url, "pedidos") == (
        pedidos_before_downgrade
    )
    assert (
        _values_by_size(migration_database_url, "pedidos_processados_erp")
        == erp_before_downgrade
    )
