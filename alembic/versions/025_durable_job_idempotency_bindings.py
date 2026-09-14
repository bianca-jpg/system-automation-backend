"""durable idempotency bindings for created and coalesced jobs

Revision ID: 025
Revises: 024
Create Date: 2026-08-08

Every persisted Idempotency-Key is a namespaced SHA-256 digest. Bindings carry
only bounded metadata and follow the job lifecycle through ``ON DELETE
CASCADE``; terminal-job cleanup therefore removes them after the shared 30-day
retention window. No raw key, request payload or error text is stored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "durable_job_idempotency_bindings",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "owner_id IS NULL OR owner_id > 0",
            name="ck_durable_job_binding_owner",
        ),
        sa.CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_durable_job_binding_kind_safe",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_durable_job_binding_key_hash",
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_durable_job_binding_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["public.durable_jobs.id"],
            name="fk_durable_job_binding_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="public",
    )

    # Backfill every creator key written by 023/024. Secondary keys coalesced
    # after this migration are inserted atomically by the repository adapter.
    op.execute(
        "INSERT INTO public.durable_job_idempotency_bindings "
        "(job_id, owner_id, idempotency_key_hash, kind, fingerprint, created_at) "
        "SELECT id, owner_id, idempotency_key_hash, kind, fingerprint, requested_at "
        "FROM public.durable_jobs "
        "WHERE idempotency_key_hash IS NOT NULL "
        "ORDER BY requested_at, id"
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_durable_job_binding_owner_key "
        "ON public.durable_job_idempotency_bindings "
        "(owner_id, idempotency_key_hash) WHERE owner_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_durable_job_binding_system_key "
        "ON public.durable_job_idempotency_bindings "
        "(idempotency_key_hash) WHERE owner_id IS NULL"
    )
    # PostgreSQL does not automatically index the referencing side of an FK;
    # this keeps retention cleanup/cascade proportional to the deleted batch.
    op.execute(
        "CREATE INDEX ix_durable_job_binding_job_id "
        "ON public.durable_job_idempotency_bindings (job_id)"
    )


def downgrade() -> None:
    op.drop_table("durable_job_idempotency_bindings", schema="public")
