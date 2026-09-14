"""delivery outbox transacional para comunicações por e-mail

Revision ID: 022
Revises: 021
Create Date: 2026-08-08

Comunicações legadas permanecem ``Enviado`` e não são reenfileiradas. Apenas
novos POSTs criam uma entrega pendente. A idempotência passa a ser isolada por
ator para impedir replay/vazamento entre usuários.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "comunicacoes",
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        schema="public",
    )
    op.create_foreign_key(
        "fk_comunicacoes_requested_by_user",
        "comunicacoes",
        "auth_users",
        ["requested_by_user_id"],
        ["id"],
        source_schema="public",
        referent_schema="public",
        ondelete="SET NULL",
    )
    op.execute("DROP INDEX IF EXISTS public.uq_comunicacoes_idempotency_key")
    op.execute(
        "CREATE UNIQUE INDEX uq_comunicacoes_idempotency_key "
        "ON public.comunicacoes (requested_by_user_id, idempotency_key) "
        "WHERE requested_by_user_id IS NOT NULL AND idempotency_key IS NOT NULL"
    )

    op.create_table(
        "communication_email_deliveries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("communication_id", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.SmallInteger(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'unknown')",
            name="ck_communication_email_delivery_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_communication_email_delivery_attempts",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_communication_email_delivery_max_attempts",
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR attempt_count < max_attempts",
            name="ck_communication_email_delivery_pending_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND claimed_by IS NOT NULL "
            "AND claimed_until IS NOT NULL) OR "
            "(status <> 'processing' AND claimed_by IS NULL "
            "AND claimed_until IS NULL)",
            name="ck_communication_email_delivery_lease_state",
        ),
        sa.CheckConstraint(
            "(status = 'sent' AND sent_at IS NOT NULL AND failed_at IS NULL) OR "
            "(status IN ('failed', 'unknown') AND failed_at IS NOT NULL "
            "AND sent_at IS NULL) OR "
            "(status IN ('pending', 'processing') AND sent_at IS NULL "
            "AND failed_at IS NULL)",
            name="ck_communication_email_delivery_terminal_state",
        ),
        sa.ForeignKeyConstraint(
            ["communication_id"],
            ["public.comunicacoes.id"],
            name="fk_communication_email_delivery_communication",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "communication_id",
            name="uq_communication_email_delivery_communication",
        ),
        sa.UniqueConstraint(
            "message_id",
            name="uq_communication_email_delivery_message_id",
        ),
        schema="public",
    )
    op.execute(
        "CREATE INDEX ix_communication_email_delivery_pending "
        "ON public.communication_email_deliveries (next_attempt_at, id) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX ix_communication_email_delivery_expired "
        "ON public.communication_email_deliveries (claimed_until, id) "
        "WHERE status = 'processing'"
    )


def downgrade() -> None:
    op.drop_table("communication_email_deliveries", schema="public")
    op.execute("DROP INDEX IF EXISTS public.uq_comunicacoes_idempotency_key")

    conn = op.get_bind()
    duplicate_key = conn.scalar(
        sa.text(
            "SELECT idempotency_key FROM public.comunicacoes "
            "WHERE idempotency_key IS NOT NULL "
            "GROUP BY idempotency_key HAVING count(*) > 1 LIMIT 1"
        )
    )
    if duplicate_key is not None:
        raise RuntimeError(
            "downgrade 022 recusado: existem chaves idempotentes repetidas entre atores"
        )

    op.execute(
        "CREATE UNIQUE INDEX uq_comunicacoes_idempotency_key "
        "ON public.comunicacoes (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    op.drop_constraint(
        "fk_comunicacoes_requested_by_user",
        "comunicacoes",
        schema="public",
        type_="foreignkey",
    )
    op.drop_column("comunicacoes", "requested_by_user_id", schema="public")
