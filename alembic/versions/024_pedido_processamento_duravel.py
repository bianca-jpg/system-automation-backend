"""plano durável e resumível do processamento de pedidos

Revision ID: 024
Revises: 023
Create Date: 2026-08-08

O ledger genérico continua autoridade de lease/status. Estas tabelas guardam
o cabeçalho e a seleção operacional protegida (que pode conter dados do pedido
necessários ao Linx), nunca exposta pelo status nem escrita em logs. Não há
Idempotency-Key crua ou texto de erro neste schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedido_processamentos",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column(
            "planning_state",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "next_ordinal", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "candidate_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "planned_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "applied_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "deferred_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "blocked_credit_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("plan_hash", sa.String(length=64), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('adequar', 'sem_adequar')",
            name="ck_pedido_processamentos_mode",
        ),
        sa.CheckConstraint(
            "channel IN ('Todos', 'Franquia', 'Multimarca')",
            name="ck_pedido_processamentos_channel",
        ),
        sa.CheckConstraint(
            "planning_state IN ('pending', 'planned')",
            name="ck_pedido_processamentos_planning_state",
        ),
        sa.CheckConstraint(
            "candidate_count BETWEEN 0 AND 100000 AND "
            "planned_count BETWEEN 0 AND candidate_count AND "
            "applied_count BETWEEN 0 AND planned_count AND "
            "deferred_count BETWEEN 0 AND candidate_count AND "
            "blocked_credit_count BETWEEN 0 AND candidate_count",
            name="ck_pedido_processamentos_counts",
        ),
        sa.CheckConstraint(
            "next_ordinal = applied_count + 1 "
            "AND next_ordinal BETWEEN 1 AND planned_count + 1",
            name="ck_pedido_processamentos_checkpoint",
        ),
        sa.CheckConstraint(
            "(planning_state = 'pending' AND plan_hash IS NULL "
            "AND planned_at IS NULL AND candidate_count = 0 "
            "AND planned_count = 0 AND applied_count = 0 "
            "AND deferred_count = 0 AND blocked_credit_count = 0 "
            "AND next_ordinal = 1) OR "
            "(planning_state = 'planned' "
            "AND plan_hash ~ '^[0-9a-f]{64}$' AND planned_at IS NOT NULL)",
            name="ck_pedido_processamentos_plan_state",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["durable_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_table(
        "pedido_processamento_plan",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default=sa.text("'planned'"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 100000",
            name="ck_pedido_processamento_plan_ordinal",
        ),
        sa.CheckConstraint(
            "nr_pedido BETWEEN 1 AND 2147483647",
            name="ck_pedido_processamento_plan_order",
        ),
        sa.CheckConstraint(
            "length(cd_prod_cor) BETWEEN 1 AND 64 AND cd_prod_cor = trim(cd_prod_cor)",
            name="ck_pedido_processamento_plan_product",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND octet_length(payload::text) <= 131072",
            name="ck_pedido_processamento_plan_payload",
        ),
        sa.CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pedido_processamento_plan_hash",
        ),
        sa.CheckConstraint(
            "state IN ('planned', 'applied')",
            name="ck_pedido_processamento_plan_state",
        ),
        sa.CheckConstraint(
            "(state = 'planned' AND applied_at IS NULL) OR "
            "(state = 'applied' AND applied_at IS NOT NULL)",
            name="ck_pedido_processamento_plan_applied_state",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["pedido_processamentos.job_id"],
            name="fk_pedido_processamento_plan_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "ordinal"),
        sa.UniqueConstraint(
            "job_id",
            "nr_pedido",
            "cd_prod_cor",
            name="uq_pedido_processamento_plan_pair",
        ),
    )
    op.create_index(
        "ix_pedido_processamento_plan_pending",
        "pedido_processamento_plan",
        ["job_id", "ordinal"],
        unique=False,
        postgresql_where=sa.text("state = 'planned'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pedido_processamento_plan_pending",
        table_name="pedido_processamento_plan",
    )
    op.drop_table("pedido_processamento_plan")
    op.drop_table("pedido_processamentos")
