"""realtime transacional, cursores e índices das queries do dashboard

Revision ID: 018
Revises: 017
Create Date: 2026-08-08

Além do outbox, esta migration repara de forma idempotente o único drift
histórico conhecido: alguns bancos foram marcados com a antiga revisão ``012``
(OR por produto) antes de ``012_comunicacoes.py`` existir. Não reescrevemos a
revisão antiga; apenas garantimos aqui que ``comunicacoes`` exista antes de
adicionar a chave de idempotência.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(conn: sa.Connection, table_name: str) -> bool:
    return bool(
        conn.scalar(
            sa.text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
            {"qualified_name": f"public.{table_name}"},
        )
    )


def _column_exists(conn: sa.Connection, table_name: str, column_name: str) -> bool:
    return bool(
        conn.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = :table_name AND column_name = :column_name"
                ")"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    )


def _drop_invalid_index(conn: sa.Connection, index_name: str) -> None:
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
            {"index_name": index_name},
        )
    )
    if invalid:
        # Os nomes vêm somente das constantes abaixo, nunca de input externo.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')


def _ensure_comunicacoes(conn: sa.Connection) -> None:
    if not _table_exists(conn, "comunicacoes"):
        op.create_table(
            "comunicacoes",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("time", sa.String(length=8), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("recipient", sa.String(length=255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    elif not _column_exists(conn, "comunicacoes", "idempotency_key"):
        op.add_column(
            "comunicacoes",
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        )

    if not _column_exists(conn, "comunicacoes", "idempotency_fingerprint"):
        op.add_column(
            "comunicacoes",
            sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
        )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_comunicacoes_idempotency_key "
        "ON comunicacoes (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_comunicacoes_created_at_id_desc "
        "ON comunicacoes (created_at DESC, id DESC)"
    )


def upgrade() -> None:
    # Estas tabelas podem ter centenas de MB. CONCURRENTLY evita bloquear os
    # writes durante deploy. O bloco vem antes do DDL novo: se uma etapa
    # posterior falhar, o rerun é seguro por causa de IF NOT EXISTS.
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        _drop_invalid_index(conn, "ix_pedidos_cd_prod_cor_nr_pedido")
        _drop_invalid_index(
            conn,
            "ix_pedidos_processados_erp_cd_prod_cor_nr_pedido",
        )
        _drop_invalid_index(
            conn,
            "ix_ordens_reserva_created_at_nr_pedido_cd_prod_cor",
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pedidos_cd_prod_cor_nr_pedido "
            "ON pedidos (cd_prod_cor, nr_pedido)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_pedidos_processados_erp_cd_prod_cor_nr_pedido "
            "ON pedidos_processados_erp (cd_prod_cor, nr_pedido)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_ordens_reserva_created_at_nr_pedido_cd_prod_cor "
            "ON ordens_reserva (created_at DESC, nr_pedido, cd_prod_cor)"
        )

    conn = op.get_bind()
    _ensure_comunicacoes(conn)

    op.create_table(
        "realtime_topic_state",
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("observed_initialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "latest_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "latest_topic_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "replay_floor_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "latest_event", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("latest_sequence >= 0", name="ck_realtime_topic_latest_seq"),
        sa.CheckConstraint(
            "latest_topic_sequence >= 0", name="ck_realtime_topic_latest_topic_seq"
        ),
        sa.CheckConstraint(
            "replay_floor_sequence >= 0", name="ck_realtime_topic_replay_floor"
        ),
        sa.PrimaryKeyConstraint("topic"),
    )

    op.create_table(
        "realtime_outbox",
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("topic_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence > 0", name="ck_realtime_outbox_sequence"),
        sa.CheckConstraint("topic_sequence > 0", name="ck_realtime_outbox_topic_seq"),
        sa.CheckConstraint("attempts >= 0", name="ck_realtime_outbox_attempts"),
        sa.CheckConstraint("version = 1", name="ck_realtime_outbox_version"),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("event_id", name="uq_realtime_outbox_event_id"),
        sa.UniqueConstraint(
            "topic", "topic_sequence", name="uq_realtime_outbox_topic_sequence"
        ),
    )
    op.execute(
        "CREATE INDEX ix_realtime_outbox_pending "
        "ON realtime_outbox (next_attempt_at, sequence) "
        "WHERE published_at IS NULL"
    )
    # O relay precisa descobrir o menor sequence não publicado antes de testar
    # due/lease. Este índice mantém o head-of-line O(log n), inclusive quando o
    # head está em backoff e bloqueia corretamente todos os eventos seguintes.
    op.execute(
        "CREATE INDEX ix_realtime_outbox_unpublished_head "
        "ON realtime_outbox (sequence) WHERE published_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_realtime_outbox_published_cleanup "
        "ON realtime_outbox (published_at, sequence) "
        "WHERE published_at IS NOT NULL"
    )
    op.create_index(
        "ix_realtime_outbox_topic_global_sequence",
        "realtime_outbox",
        ["topic", "sequence"],
    )

    op.create_table(
        "realtime_read_cursors",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column(
            "last_read_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "last_read_topic_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("last_read_sequence >= 0", name="ck_realtime_cursor_seq"),
        sa.CheckConstraint(
            "last_read_topic_sequence >= 0", name="ck_realtime_cursor_topic_seq"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "topic"),
    )

    op.create_table(
        "realtime_observed_entities",
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("entity_key", sa.String(length=256), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["topic"], ["realtime_topic_state.topic"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("topic", "entity_key"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_ordens_reserva_created_at_nr_pedido_cd_prod_cor"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_pedidos_processados_erp_cd_prod_cor_nr_pedido"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_pedidos_cd_prod_cor_nr_pedido")

    op.execute("DROP TABLE IF EXISTS realtime_observed_entities")
    op.execute("DROP TABLE IF EXISTS realtime_read_cursors")
    # Compatível com bancos que aplicaram uma versão intermediária da 018
    # durante desenvolvimento antes destes índices existirem.
    op.execute("DROP INDEX IF EXISTS ix_realtime_outbox_topic_global_sequence")
    op.execute("DROP INDEX IF EXISTS ix_realtime_outbox_published_cleanup")
    op.execute("DROP INDEX IF EXISTS ix_realtime_outbox_unpublished_head")
    op.execute("DROP INDEX IF EXISTS ix_realtime_outbox_pending")
    op.execute("DROP TABLE IF EXISTS realtime_outbox")
    op.execute("DROP TABLE IF EXISTS realtime_topic_state")

    if _table_exists(conn, "comunicacoes"):
        op.execute("DROP INDEX IF EXISTS ix_comunicacoes_created_at_id_desc")
        op.execute("DROP INDEX IF EXISTS uq_comunicacoes_idempotency_key")
        if _column_exists(conn, "comunicacoes", "idempotency_key"):
            op.drop_column("comunicacoes", "idempotency_key")
        if _column_exists(conn, "comunicacoes", "idempotency_fingerprint"):
            op.drop_column("comunicacoes", "idempotency_fingerprint")
