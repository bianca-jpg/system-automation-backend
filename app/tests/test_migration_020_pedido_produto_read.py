"""Integration coverage for revision 020's persisted pair projection.

The tests reset ``public`` and therefore only run against an explicit loopback
database whose name starts with ``automation_migration_test``. An inherited DEV or
PROD URL can never opt in accidentally.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.ingestao.infrastructure import repositorio_snapshot
from app.modules.ingestao.infrastructure.models import PedidoProdutoRead

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_URL_ENV = "MIGRATION_TEST_DATABASE_URL"
_DATABASE_PREFIX = "automation_migration_test"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_MODEL_CONTRACT_SCHEMA = "migration_020_model_contract"
_MIGRATION_ADVISORY_LOCK = 7_541_240_193_001

_COLUMN_CONTRACT_SQL = """
SELECT
    attribute.attname,
    pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
    attribute.attnotnull,
    coalesce(pg_get_expr(default_value.adbin, default_value.adrelid), ''),
    attribute.attidentity,
    attribute.attgenerated
FROM pg_attribute attribute
JOIN pg_class relation ON relation.oid = attribute.attrelid
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
LEFT JOIN pg_attrdef default_value
  ON default_value.adrelid = relation.oid
 AND default_value.adnum = attribute.attnum
WHERE namespace.nspname = :schema_name
  AND relation.relname = 'pedido_produto_read'
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
ORDER BY attribute.attnum
"""

_CONSTRAINT_CONTRACT_SQL = """
SELECT
    constraint_value.conname,
    constraint_value.contype,
    pg_get_constraintdef(constraint_value.oid, true)
FROM pg_constraint constraint_value
JOIN pg_class relation ON relation.oid = constraint_value.conrelid
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = :schema_name
  AND relation.relname = 'pedido_produto_read'
ORDER BY constraint_value.conname
"""

_INDEX_CONTRACT_SQL = """
SELECT
    index_relation.relname,
    index_value.indisunique,
    index_value.indisprimary,
    access_method.amname,
    ARRAY(
        SELECT pg_get_indexdef(index_value.indexrelid, position, true)
        FROM generate_series(1, index_value.indnatts) AS position
    ),
    coalesce(pg_get_expr(index_value.indpred, index_value.indrelid), '')
FROM pg_index index_value
JOIN pg_class relation ON relation.oid = index_value.indrelid
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
JOIN pg_class index_relation ON index_relation.oid = index_value.indexrelid
JOIN pg_am access_method ON access_method.oid = index_relation.relam
WHERE namespace.nspname = :schema_name
  AND relation.relname = 'pedido_produto_read'
ORDER BY index_relation.relname
"""


def _repository_head() -> str:
    config = Config()
    config.set_main_option(
        "script_location",
        str(_BACKEND_ROOT / "alembic"),
    )
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"expected one Alembic head, found {heads!r}"
    return heads[0]


def _canonical_sql(statement: str) -> str:
    return " ".join(statement.split())


async def _assert_model_matches_migrated_schema_async(database_url: str) -> None:
    engine = create_async_engine(database_url)
    shadow_metadata = MetaData()
    cast(Table, PedidoProdutoRead.__table__).to_metadata(
        shadow_metadata,
        schema=_MODEL_CONTRACT_SCHEMA,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{_MODEL_CONTRACT_SCHEMA}" CASCADE')
            )
            await connection.execute(text(f'CREATE SCHEMA "{_MODEL_CONTRACT_SCHEMA}"'))
            await connection.run_sync(shadow_metadata.create_all)

            for contract_sql in (
                _COLUMN_CONTRACT_SQL,
                _CONSTRAINT_CONTRACT_SQL,
                _INDEX_CONTRACT_SQL,
            ):
                migrated = (
                    await connection.execute(
                        text(contract_sql),
                        {"schema_name": "public"},
                    )
                ).all()
                declared = (
                    await connection.execute(
                        text(contract_sql),
                        {"schema_name": _MODEL_CONTRACT_SCHEMA},
                    )
                ).all()
                assert migrated == declared

            await connection.execute(
                text(f'DROP SCHEMA "{_MODEL_CONTRACT_SCHEMA}" CASCADE')
            )
    finally:
        await engine.dispose()


def _assert_model_matches_migrated_schema(database_url: str) -> None:
    asyncio.run(_assert_model_matches_migrated_schema_async(database_url))


def test_runtime_and_migration_backfill_sql_do_not_drift() -> None:
    migration_path = (
        _BACKEND_ROOT / "alembic" / "versions" / "020_pedido_produto_read.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_020_pedido_produto_read_contract",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    # 260825-jhv: `indica_blacklist` é a PRIMEIRA coluna de `pedido_produto_read`
    # cuja fonte (`pedidos.indica_blacklist`) só existe a partir da migration 033
    # — revisões DEPOIS de 020. Diferente de credito_bloqueado (calculado inline
    # a partir de status_credito, já presente em 020) e indica_reserva/
    # indica_embalado (colunas de pedidos_processados_erp desde a migration 009),
    # não é possível reescrever o backfill ÚNICO de 020 para já selecionar esta
    # coluna: um fresh `alembic upgrade head` replay do zero executaria o
    # backfill de 020 ANTES de 033 criar a coluna, falhando com
    # UndefinedColumnError (confirmado manualmente contra um banco vazio).
    # Por isso o backfill histórico de 020 permanece sem indica_blacklist (a
    # próxima sincronização/full-refresh, já pós-033, populariza a coluna
    # corretamente) e este teste declara explicitamente essa ÚNICA divergência
    # esperada, em vez de exigir igualdade byte-a-byte para sempre.
    pending_com_blacklist = (
        migration._PENDING_BACKFILL_SQL.replace(
            ") AS credito_bloqueado\n    FROM pedidos p",
            ") AS credito_bloqueado,\n        bool_or(p.indica_blacklist) AS indica_blacklist\n    FROM pedidos p",
        )
        .replace(
            "qty, value, sizes, credito_bloqueado,\n    indica_reserva",
            "qty, value, sizes, credito_bloqueado, indica_blacklist,\n    indica_reserva",
        )
        .replace(
            "g.qty, g.value, g.sizes, p.credito_bloqueado,\n    false, false, statement_timestamp()",
            "g.qty, g.value, g.sizes, p.credito_bloqueado, p.indica_blacklist,\n"
            "    false, false, statement_timestamp()",
        )
    )
    assert pending_com_blacklist != migration._PENDING_BACKFILL_SQL, (
        "as substituições acima não bateram com o texto de 020 — revisar o "
        "diff manualmente antes de confiar nesta comparação"
    )

    assert _canonical_sql(pending_com_blacklist) == _canonical_sql(
        repositorio_snapshot._PENDING_READ_INSERT_SQL
    )
    assert _canonical_sql(migration._ERP_BACKFILL_SQL) == _canonical_sql(
        repositorio_snapshot._ERP_READ_INSERT_SQL
    )


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


async def _execute_async(database_url: str, statement: str) -> list[tuple]:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(statement))
            if not result.returns_rows:
                return []
            return [tuple(row) for row in result]
    finally:
        await engine.dispose()


def _execute(database_url: str, statement: str) -> list[tuple]:
    return asyncio.run(_execute_async(database_url, statement))


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


async def _run_concurrent_head_upgrades_async(
    database_url: str,
) -> list[tuple[int, str, str]]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    engine = create_async_engine(database_url)
    processes: list[asyncio.subprocess.Process] = []
    observed_both_contending = False
    revision_while_blocked: str | None = None
    try:
        async with engine.connect() as blocker:
            await blocker.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": _MIGRATION_ADVISORY_LOCK},
            )
            await blocker.commit()
            processes = list(
                await asyncio.gather(
                    *[
                        asyncio.create_subprocess_exec(
                            sys.executable,
                            "-m",
                            "alembic",
                            "upgrade",
                            "head",
                            cwd=_BACKEND_ROOT,
                            env=environment,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        for _ in range(2)
                    ]
                )
            )
            try:
                deadline = asyncio.get_running_loop().time() + 30
                while asyncio.get_running_loop().time() < deadline:
                    contenders = int(
                        (
                            await blocker.execute(
                                text(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND usename = current_user "
                                    "AND backend_type = 'client backend' "
                                    "AND pid <> pg_backend_pid()"
                                )
                            )
                        ).scalar_one()
                    )
                    await blocker.commit()
                    if contenders >= 2 and all(
                        process.returncode is None for process in processes
                    ):
                        revision_while_blocked = str(
                            (
                                await blocker.execute(
                                    text("SELECT version_num FROM alembic_version")
                                )
                            ).scalar_one()
                        )
                        await blocker.commit()
                        observed_both_contending = True
                        break
                    await asyncio.sleep(0.05)
            finally:
                await blocker.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": _MIGRATION_ADVISORY_LOCK},
                )
                await blocker.commit()

        results = await asyncio.wait_for(
            asyncio.gather(*[process.communicate() for process in processes]),
            timeout=180,
        )
        assert observed_both_contending, (
            "both Alembic processes must stay alive while polling the session lock"
        )
        assert revision_while_blocked == "017"
        normalized_results: list[tuple[int, str, str]] = []
        for process, (stdout, stderr) in zip(processes, results, strict=True):
            assert process.returncode is not None
            normalized_results.append(
                (
                    process.returncode,
                    stdout.decode(errors="replace"),
                    stderr.decode(errors="replace"),
                )
            )
        return normalized_results
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=30)
        await engine.dispose()


def _run_concurrent_head_upgrades(database_url: str) -> list[tuple[int, str, str]]:
    return asyncio.run(_run_concurrent_head_upgrades_async(database_url))


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


def _seed_revision_019(database_url: str) -> None:
    _execute(
        database_url,
        """
        INSERT INTO pedidos (
            nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo,
            cd_colecao_ped, qt_entregar, vl_liquido, client, canal,
            status_credito, ds_produto, data
        ) VALUES
            (1001, 'PENDING', ' m ', 'CAMISAS', 1, 3, 60.00,
             ' Cliente Primeiro ', ' MM ', 'Com Crédito',
             'Camisa Pending', '2026-08-07T10:30:00'),
            (1001, 'PENDING', 'P', 'CAMISAS', 1, 2, 40.00,
             'Cliente Depois', 'MM', 'Sem Crédito',
             'Camisa Pending', '2026-08-07T10:30:00'),
            (1002, 'BOTH', 'M', 'CALCAS', 1, 1, 10.00,
             'Cliente Pending Perdedor', 'Franquia', 'Com Crédito',
             'Calça Pending', '2026-08-05'),
            (1004, 'INVALID_DATE', 'U', 'GRUPO', 1, 1, 12.34,
             NULL, 'Franquia', 'Com Crédito', NULL,
             '2026-02-30T12:00:00'),
            (1005, 'BOUNDED', ' ', 'GRUPO', 1, 9, 90.00,
             'Cliente Bounded', 'Franquia', 'Com Crédito',
             'Produto Bounded', '2026-08-04'),
            (1005, 'BOUNDED', 'G', 'GRUPO', 1, 1, 10.00,
             'Cliente Bounded', 'Franquia', 'Com Crédito',
             'Produto Bounded', '2026-08-04'),
            (1006, 'ONLY_INVALID', '  ', 'GRUPO', 1, 4, 20.00,
             'Cliente Inválido', 'Franquia', 'Com Crédito',
             'Produto Inválido', '2026-08-04'),
            (1007, 'NEGATIVE_QTY', 'P', 'GRUPO', 1, -5, 5.00,
             'Cliente Negativo', 'Franquia', 'Com Crédito',
             'Produto Negativo', '2026-08-04'),
            (1009, 'SHARED_PRODUCT', 'P', 'GRUPO', 1, 1, 20.00,
             'Cliente Franquia', 'Franquia', 'Com Crédito',
             'Produto Compartilhado', '2026-08-03'),
            (1010, 'SHARED_PRODUCT', 'M', 'GRUPO', 1, 2, 30.00,
             'Cliente Multimarca', 'Multimarca', 'Com Crédito',
             'Produto Compartilhado', '2026-08-02')
        """,
    )
    _execute(
        database_url,
        """
        INSERT INTO pedidos_processados_erp (
            nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo, client, canal,
            ds_produto, data, qt, vl_liquido,
            indica_reserva, indica_embalado
        ) VALUES
            (1002, 'BOTH', '36', 'CALCAS', 'Cliente ERP', 'Franquia',
             'Calça ERP', '2026-08-06', 2, 40.00, true, false),
            (1002, 'BOTH', '37', 'CALCAS', 'Cliente ERP', 'Franquia',
             'Calça ERP', '2026-08-06', 3, 60.00, false, true),
            (1003, 'ERP_ZERO', 'UN', 'ACESSORIOS', NULL, 'Multimarca',
             NULL, 'data-invalida', 0, 0.00, false, true),
            (1008, 'ERP_NEGATIVE', '36', 'DEVOLUCAO', 'Cliente Devolução',
             'Franquia', 'Ajuste ERP', '2026-08-01', 6, -3.64, false, false),
            (1008, 'ERP_NEGATIVE', '37', 'DEVOLUCAO', 'Cliente Devolução',
             'Franquia', 'Ajuste ERP', '2026-08-01', 2, -1.21, false, false),
            (1008, 'ERP_NEGATIVE', '38', 'DEVOLUCAO', 'Cliente Devolução',
             'Franquia', 'Ajuste ERP', '2026-08-01', 7, -4.25, false, false),
            (1008, 'ERP_NEGATIVE', '39', 'DEVOLUCAO', 'Cliente Devolução',
             'Franquia', 'Ajuste ERP', '2026-08-01', 3, -1.82, false, false)
        """,
    )


def _projection(database_url: str) -> dict[tuple[str, int, str], tuple]:
    rows = _execute(
        database_url,
        """
        SELECT
            source, nr_pedido, cd_prod_cor, client, canal,
            order_date, order_date_raw, product_name, group_name,
            qty, value, sizes, credito_bloqueado,
            indica_reserva, indica_embalado
        FROM pedido_produto_read
        ORDER BY source, nr_pedido, cd_prod_cor
        """,
    )
    return {
        (str(source), int(order_number), str(product)): tuple(values)
        for source, order_number, product, *values in rows
    }


def test_fresh_upgrade_head_contains_projection_schema(
    migration_database_url: str,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "head")

    assert _current_revision(migration_database_url) == _repository_head()
    assert _execute(
        migration_database_url,
        "SELECT count(*) FROM pedido_produto_read",
    ) == [(0,)]
    checks = {
        str(name)
        for (name,) in _execute(
            migration_database_url,
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE n.nspname = 'public'
              AND rel.relname = 'pedido_produto_read'
              AND con.contype = 'c'
            """,
        )
    }
    assert checks == {
        "ck_ppr_canal",
        "ck_ppr_cd",
        "ck_ppr_nr_pedido_positivo",
        "ck_ppr_qty_nao_negativa",
        "ck_ppr_sizes_bounded",
        "ck_ppr_source",
        "ck_ppr_value_nao_negativo",
    }
    _assert_model_matches_migrated_schema(migration_database_url)

    # O contrato financeiro é source-aware: ajustes ERP signed são válidos,
    # mas pending negativo falha fechado.
    _execute(
        migration_database_url,
        """
        INSERT INTO pedido_produto_read (
            source, nr_pedido, cd_prod_cor, canal, qty, value, sizes
        ) VALUES (
            'erp', 1, 'SIGNED_OK', 'Franquia', 1, -0.01, '{"U": 1}'::jsonb
        )
        """,
    )
    with pytest.raises(IntegrityError):
        _execute(
            migration_database_url,
            """
            INSERT INTO pedido_produto_read (
                source, nr_pedido, cd_prod_cor, canal, qty, value, sizes
            ) VALUES (
                'pending', 2, 'SIGNED_BLOCKED', 'Franquia', 1, -0.01,
                '{"U": 1}'::jsonb
            )
            """,
        )


def test_concurrent_017_to_head_upgrades_serialize_without_ddl_race(
    migration_database_url: str,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "017")

    results = _run_concurrent_head_upgrades(migration_database_url)

    failures = [
        {"returncode": returncode, "stdout": stdout, "stderr": stderr}
        for returncode, stdout, stderr in results
        if returncode != 0
    ]
    assert failures == [], "\n\n".join(
        f"returncode={failure['returncode']}\n"
        f"stdout:\n{failure['stdout']}\n"
        f"stderr:\n{failure['stderr']}"
        for failure in failures
    )
    assert _current_revision(migration_database_url) == _repository_head()
    assert _execute(
        migration_database_url,
        """
        SELECT count(*)
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = 'pedido_produto_read'
          AND relation.relkind = 'r'
        """,
    ) == [(1,)]
    assert _execute(
        migration_database_url,
        """
        SELECT count(*)
        FROM pg_locks
        WHERE locktype = 'advisory'
          AND database = (
              SELECT oid FROM pg_database WHERE datname = current_database()
          )
        """,
    ) == [(0,)]


def test_019_to_020_backfills_pairs_and_roundtrips_derived_schema(
    migration_database_url: str,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "019")
    _seed_revision_019(migration_database_url)

    source_before = _execute(
        migration_database_url,
        """
        SELECT
            (SELECT count(*) FROM pedidos),
            (SELECT sum(qt_entregar) FROM pedidos),
            (SELECT sum(vl_liquido) FROM pedidos),
            (SELECT count(*) FROM pedidos_processados_erp),
            (SELECT sum(qt) FROM pedidos_processados_erp),
            (SELECT sum(vl_liquido) FROM pedidos_processados_erp)
        """,
    )
    _run_alembic(migration_database_url, "upgrade", "020")

    assert _current_revision(migration_database_url) == "020"
    projection = _projection(migration_database_url)
    assert set(projection) == {
        ("pending", 1001, "PENDING"),
        ("pending", 1004, "INVALID_DATE"),
        ("pending", 1005, "BOUNDED"),
        ("pending", 1007, "NEGATIVE_QTY"),
        ("pending", 1009, "SHARED_PRODUCT"),
        ("pending", 1010, "SHARED_PRODUCT"),
        ("erp", 1002, "BOTH"),
        ("erp", 1003, "ERP_ZERO"),
        ("erp", 1008, "ERP_NEGATIVE"),
    }

    pending = projection[("pending", 1001, "PENDING")]
    assert pending == (
        "Cliente Primeiro",
        "Multimarca",
        date(2026, 8, 7),
        "2026-08-07T10:30:00",
        "Camisa Pending",
        "CAMISAS",
        5,
        Decimal("100.00"),
        {"M": 3, "P": 2},
        True,
        False,
        False,
    )

    # ERP ganha por par; não existe uma segunda linha pending para 1002/BOTH.
    erp = projection[("erp", 1002, "BOTH")]
    assert erp[0:9] == (
        "Cliente ERP",
        "Franquia",
        date(2026, 8, 6),
        "2026-08-06",
        "Calça ERP",
        "CALCAS",
        5,
        Decimal("100.00"),
        {"36": 2, "37": 3},
    )
    assert erp[9:] == (False, True, True)

    erp_zero = projection[("erp", 1003, "ERP_ZERO")]
    assert erp_zero[2] is None
    assert erp_zero[3] == "data-invalida"
    assert erp_zero[6:9] == (0, Decimal("0.00"), {"UN": 0})

    invalid_date = projection[("pending", 1004, "INVALID_DATE")]
    assert invalid_date[0] == "Cliente 1004"
    assert invalid_date[2] is None
    assert invalid_date[3] == "2026-02-30T12:00:00"
    assert invalid_date[4] == "GRUPO INVALID_DATE"

    bounded = projection[("pending", 1005, "BOUNDED")]
    assert bounded[6:9] == (1, Decimal("10.00"), {"G": 1})
    negative_qty = projection[("pending", 1007, "NEGATIVE_QTY")]
    assert negative_qty[6:9] == (0, Decimal("5.00"), {"P": 0})

    # O produto pode existir simultaneamente nos dois canais, mas identidade e
    # agregação continuam separadas pelo pedido; nenhum valor/tamanho cruza.
    shared_franchise = projection[("pending", 1009, "SHARED_PRODUCT")]
    shared_multibrand = projection[("pending", 1010, "SHARED_PRODUCT")]
    assert shared_franchise[1:2] == ("Franquia",)
    assert shared_franchise[6:9] == (1, Decimal("20.00"), {"P": 1})
    assert shared_multibrand[1:2] == ("Multimarca",)
    assert shared_multibrand[6:9] == (2, Decimal("30.00"), {"M": 2})

    erp_negative = projection[("erp", 1008, "ERP_NEGATIVE")]
    assert erp_negative[6:9] == (
        18,
        Decimal("-10.92"),
        {"36": 6, "37": 2, "38": 7, "39": 3},
    )

    # Para todo par válido, qty e value são exatamente a soma pós-019, e a
    # soma das quantidades do JSON coincide com qty.
    assert (
        _execute(
            migration_database_url,
            """
        WITH expected AS (
            SELECT
                'pending'::varchar AS source,
                p.nr_pedido,
                p.cd_prod_cor,
                sum(greatest(p.qt_entregar, 0))::bigint AS qty,
                sum(p.vl_liquido)::numeric(14, 2) AS value
            FROM pedidos p
            WHERE length(trim(p.sg_tamanho)) BETWEEN 1 AND 16
              AND NOT EXISTS (
                  SELECT 1
                  FROM pedidos_processados_erp pe
                  WHERE pe.nr_pedido = p.nr_pedido
                    AND pe.cd_prod_cor = p.cd_prod_cor
              )
            GROUP BY p.nr_pedido, p.cd_prod_cor
            UNION ALL
            SELECT
                'erp'::varchar,
                pe.nr_pedido,
                pe.cd_prod_cor,
                sum(greatest(pe.qt, 0))::bigint,
                sum(pe.vl_liquido)::numeric(14, 2)
            FROM pedidos_processados_erp pe
            WHERE length(trim(pe.sg_tamanho)) BETWEEN 1 AND 16
            GROUP BY pe.nr_pedido, pe.cd_prod_cor
        ),
        actual AS (
            SELECT
                p.source,
                p.nr_pedido,
                p.cd_prod_cor,
                p.qty,
                p.value,
                (
                    SELECT sum(entry.value::bigint)
                    FROM jsonb_each_text(p.sizes) AS entry
                )::bigint AS sizes_qty
            FROM pedido_produto_read p
        )
        SELECT
            coalesce(e.source, a.source),
            coalesce(e.nr_pedido, a.nr_pedido),
            coalesce(e.cd_prod_cor, a.cd_prod_cor)
        FROM expected e
        FULL OUTER JOIN actual a
          USING (source, nr_pedido, cd_prod_cor)
        WHERE e.nr_pedido IS NULL
           OR a.nr_pedido IS NULL
           OR a.qty IS DISTINCT FROM e.qty
           OR a.value IS DISTINCT FROM e.value
           OR a.sizes_qty IS DISTINCT FROM e.qty
        ORDER BY 1, 2, 3
        """,
        )
        == []
    )

    # A projeção é derivada: downgrade remove só ela e preserva integralmente as
    # duas fontes; re-upgrade reproduz o mesmo conteúdo (exceto synced_at).
    _run_alembic(migration_database_url, "downgrade", "019")
    assert _current_revision(migration_database_url) == "019"
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.pedido_produto_read')",
    ) == [(None,)]
    assert (
        _execute(
            migration_database_url,
            """
        SELECT
            (SELECT count(*) FROM pedidos),
            (SELECT sum(qt_entregar) FROM pedidos),
            (SELECT sum(vl_liquido) FROM pedidos),
            (SELECT count(*) FROM pedidos_processados_erp),
            (SELECT sum(qt) FROM pedidos_processados_erp),
            (SELECT sum(vl_liquido) FROM pedidos_processados_erp)
        """,
        )
        == source_before
    )

    _run_alembic(migration_database_url, "upgrade", "020")
    assert _current_revision(migration_database_url) == "020"
    assert _projection(migration_database_url) == projection


def test_more_than_100_sizes_aborts_020_atomically_with_pair_diagnostic(
    migration_database_url: str,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "019")
    _execute(
        migration_database_url,
        """
        INSERT INTO pedidos_processados_erp (
            nr_pedido, cd_prod_cor, sg_tamanho, canal, qt, vl_liquido,
            indica_reserva, indica_embalado
        )
        SELECT
            1100,
            'TOO_MANY_SIZES',
            'S' || lpad(size_number::text, 3, '0'),
            'Franquia',
            1,
            1.00,
            false,
            false
        FROM generate_series(0, 100) AS size_number
        """,
    )

    with pytest.raises(subprocess.CalledProcessError) as error:
        _run_alembic(migration_database_url, "upgrade", "020")

    assert "ck_ppr_sizes_bounded" in error.value.stderr
    assert "TOO_MANY_SIZES" in error.value.stderr
    assert _current_revision(migration_database_url) == "019"
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.pedido_produto_read')",
    ) == [(None,)]
    assert _execute(
        migration_database_url,
        """
        SELECT count(*), sum(qt), sum(vl_liquido)
        FROM pedidos_processados_erp
        WHERE nr_pedido = 1100 AND cd_prod_cor = 'TOO_MANY_SIZES'
        """,
    ) == [(101, 101, Decimal("101.00"))]


@pytest.mark.parametrize(
    (
        "insert_statement",
        "expected_diagnostic",
        "source_count_statement",
        "expected_source_count",
    ),
    [
        (
            """
            INSERT INTO pedidos (
                nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo,
                cd_colecao_ped, qt_entregar, vl_liquido, canal
            ) VALUES (
                1200, 'UNKNOWN_PENDING_CHANNEL', 'M', 'TESTE',
                1, 1, 10.00, 'automation DESCONHECIDO'
            )
            """,
            "source=pending nr_pedido=1200 cd_prod_cor=UNKNOWN_PENDING_CHANNEL",
            "SELECT count(*) FROM pedidos WHERE nr_pedido = 1200",
            1,
        ),
        (
            """
            INSERT INTO pedidos_processados_erp (
                nr_pedido, cd_prod_cor, sg_tamanho, qt, vl_liquido,
                canal, indica_reserva, indica_embalado
            ) VALUES (
                1201, 'UNKNOWN_ERP_CHANNEL', 'M', 0, 0.00,
                NULL, true, false
            )
            """,
            "source=erp nr_pedido=1201 cd_prod_cor=UNKNOWN_ERP_CHANNEL",
            "SELECT count(*) FROM pedidos_processados_erp WHERE nr_pedido = 1201",
            1,
        ),
        (
            """
            INSERT INTO pedidos (
                nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo,
                cd_colecao_ped, qt_entregar, vl_liquido, canal
            ) VALUES
                (1202, 'MIXED_PENDING_CHANNEL', 'P', 'TESTE',
                 1, 1, 10.00, 'Franquia'),
                (1202, 'MIXED_PENDING_CHANNEL', 'M', 'TESTE',
                 1, 1, 10.00, 'MM')
            """,
            (
                "mixed channels source=pending nr_pedido=1202 "
                "cd_prod_cor=MIXED_PENDING_CHANNEL"
            ),
            "SELECT count(*) FROM pedidos WHERE nr_pedido = 1202",
            2,
        ),
    ],
    ids=["pending-unknown", "erp-null", "pending-mixed"],
)
def test_invalid_channel_aborts_020_before_backfill_without_touching_source(
    migration_database_url: str,
    insert_statement: str,
    expected_diagnostic: str,
    source_count_statement: str,
    expected_source_count: int,
) -> None:
    _run_alembic(migration_database_url, "upgrade", "019")
    _execute(migration_database_url, insert_statement)

    with pytest.raises(subprocess.CalledProcessError) as error:
        _run_alembic(migration_database_url, "upgrade", "020")

    assert "ck_ppr_canal" in error.value.stderr
    assert expected_diagnostic in error.value.stderr
    assert _current_revision(migration_database_url) == "019"
    assert _execute(
        migration_database_url,
        "SELECT to_regclass('public.pedido_produto_read')",
    ) == [(None,)]
    assert _execute(migration_database_url, source_count_statement) == [
        (expected_source_count,)
    ]
