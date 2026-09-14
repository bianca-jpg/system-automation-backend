"""índice parcial para reconciliação bounded do ledger realtime ativo

Revision ID: 026
Revises: 025
Create Date: 2026-08-08

O histórico observado é deliberadamente preservado para detectar recorrências.
A reconciliação de snapshots, porém, só precisa visitar as linhas atualmente
ativas de um tópico e as chaves do snapshot recebido. Este índice mantém a
primeira parcela bounded mesmo quando o tópico acumula milhões de inativos.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_realtime_observed_entities_active_topic"


def _drop_invalid_index(conn: sa.Connection) -> None:
    """Remove resto inválido de CREATE INDEX CONCURRENTLY interrompido."""

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
            {"index_name": _INDEX_NAME},
        )
    )
    if invalid:
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX_NAME}"')


def upgrade() -> None:
    # O ledger pode ser grande. CONCURRENTLY evita bloquear observações durante
    # deploy; IF NOT EXISTS + limpeza de índice inválido tornam o rerun seguro.
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        _drop_invalid_index(conn)
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            f'"{_INDEX_NAME}" '
            "ON realtime_observed_entities (topic) WHERE active IS TRUE"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_INDEX_NAME}"')
