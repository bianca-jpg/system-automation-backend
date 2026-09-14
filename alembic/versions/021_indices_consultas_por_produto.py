"""índices direcionados para leitura e mutação por produto

Revision ID: 021
Revises: 020
Create Date: 2026-08-08

Os dois índices foram escolhidos após ``EXPLAIN (ANALYZE, BUFFERS)`` no grão
real das consultas, não por heurística:

* ``ordens_reserva`` com 50 mil linhas sintéticas: 8,585 ms/1.193 buffers para
  3,981 ms/1.004 buffers no lock por produto.
* ``pedido_produto_read`` no produto real mais frequente (983 pares):
  48,574 ms/7.194 buffers para 29,738 ms/1.509 buffers no detalhe lazy.

``CONCURRENTLY`` evita bloquear os fluxos de ingestão e edição durante deploy.
Um índice inválido deixado por interrupção é removido antes do rerun.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "ix_ordens_reserva_produto_pedido": (
        "ordens_reserva",
        "cd_prod_cor, nr_pedido",
    ),
    "ix_ppr_source_product_channel": (
        "pedido_produto_read",
        "source, cd_prod_cor, canal, nr_pedido",
    ),
}


def _drop_invalid_index(conn: sa.Connection, index_name: str) -> None:
    invalid = bool(
        conn.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND c.relname = :index_name AND NOT i.indisvalid"
                ")"
            ),
            {"index_name": index_name},
        )
    )
    if invalid:
        # ``index_name`` vem apenas do mapa constante acima.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')


def upgrade() -> None:
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for index_name, (table_name, columns) in _INDEXES.items():
            _drop_invalid_index(conn, index_name)
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ({columns})'
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name in reversed(_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')
