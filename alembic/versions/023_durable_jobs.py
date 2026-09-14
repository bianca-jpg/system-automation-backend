"""shared durable background-job ledger

Revision ID: 023
Revises: 022
Create Date: 2026-08-08

``owner_id`` is deliberately a nullable pseudonymous integer without a foreign
key: deleting an auth record must neither mutate job history nor collapse two
owner idempotency scopes into the owner-NULL system scope. Raw idempotency keys,
request payloads and provider error text are never persisted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "durable_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("scope_key", sa.String(length=96), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.SmallInteger(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "progress_current",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("progress_total", sa.BigInteger(), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "retryable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN "
            "('queued', 'running', 'retrying', 'succeeded', 'failed', 'skipped')",
            name="ck_durable_jobs_status",
        ),
        sa.CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_durable_jobs_kind_safe",
        ),
        sa.CheckConstraint(
            "scope_key IS NULL OR scope_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$'",
            name="ck_durable_jobs_scope_safe",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash IS NULL OR idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_durable_jobs_idempotency_hash",
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_durable_jobs_fingerprint",
        ),
        sa.CheckConstraint(
            "owner_id IS NULL OR owner_id > 0",
            name="ck_durable_jobs_owner",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts",
            name="ck_durable_jobs_attempts",
        ),
        sa.CheckConstraint(
            "status NOT IN ('queued', 'retrying') OR attempts < max_attempts",
            name="ck_durable_jobs_dispatchable_attempts",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_durable_jobs_max_attempts",
        ),
        sa.CheckConstraint(
            "progress_current >= 0 AND "
            "(progress_total IS NULL OR "
            "(progress_total >= 0 AND progress_current <= progress_total))",
            name="ck_durable_jobs_progress",
        ),
        sa.CheckConstraint(
            "result IS NULL OR "
            "(jsonb_typeof(result) = 'object' "
            "AND octet_length(result::text) <= 65536)",
            name="ck_durable_jobs_result_bounded",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_.:-]{0,63}$'",
            name="ck_durable_jobs_error_safe",
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR "
            "lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$'",
            name="ck_durable_jobs_lease_owner_safe",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_durable_jobs_lease_state",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'skipped') "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'retrying') "
            "AND finished_at IS NULL)",
            name="ck_durable_jobs_terminal_state",
        ),
        sa.CheckConstraint(
            "(status IN ('failed', 'retrying', 'skipped') "
            "AND error_code IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'succeeded') "
            "AND error_code IS NULL)",
            name="ck_durable_jobs_error_state",
        ),
        sa.CheckConstraint(
            "(status = 'retrying' AND retryable) OR "
            "(status IN ('queued', 'running', 'succeeded', 'skipped') "
            "AND NOT retryable) OR status = 'failed'",
            name="ck_durable_jobs_retryable_state",
        ),
        sa.CheckConstraint(
            "available_at >= requested_at",
            name="ck_durable_jobs_available_at",
        ),
        sa.CheckConstraint(
            "deadline_at IS NULL OR deadline_at > requested_at",
            name="ck_durable_jobs_deadline",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= requested_at",
            name="ck_durable_jobs_started_at",
        ),
        sa.CheckConstraint(
            "heartbeat_at IS NULL OR "
            "(started_at IS NOT NULL AND heartbeat_at >= started_at)",
            name="ck_durable_jobs_heartbeat_at",
        ),
        sa.CheckConstraint(
            "lease_expires_at IS NULL OR "
            "(heartbeat_at IS NOT NULL AND lease_expires_at > heartbeat_at)",
            name="ck_durable_jobs_lease_expires_at",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= requested_at",
            name="ck_durable_jobs_finished_at",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="public",
    )

    # Manual/API idempotency is global per owner after the caller namespaces the
    # raw key by endpoint/kind before hashing. A separate NULL-owner index is
    # required because PostgreSQL treats NULLs as distinct in unique indexes.
    op.execute(
        "CREATE UNIQUE INDEX uq_durable_jobs_owner_idempotency "
        "ON public.durable_jobs (owner_id, idempotency_key_hash) "
        "WHERE owner_id IS NOT NULL AND idempotency_key_hash IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_durable_jobs_system_idempotency "
        "ON public.durable_jobs (idempotency_key_hash) "
        "WHERE owner_id IS NULL AND idempotency_key_hash IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_durable_jobs_active_scope "
        "ON public.durable_jobs (kind, scope_key) "
        "WHERE scope_key IS NOT NULL "
        "AND status IN ('queued', 'running', 'retrying')"
    )
    op.execute(
        "CREATE INDEX ix_durable_jobs_dispatchable "
        "ON public.durable_jobs (available_at, requested_at, id) "
        "WHERE status IN ('queued', 'retrying')"
    )
    op.execute(
        "CREATE INDEX ix_durable_jobs_expired_lease "
        "ON public.durable_jobs (lease_expires_at, id) "
        "WHERE status = 'running'"
    )
    op.execute(
        "CREATE INDEX ix_durable_jobs_pending_deadline "
        "ON public.durable_jobs (deadline_at, id) "
        "WHERE deadline_at IS NOT NULL "
        "AND status IN ('queued', 'retrying')"
    )
    op.execute(
        "CREATE INDEX ix_durable_jobs_terminal_retention "
        "ON public.durable_jobs (finished_at, id) "
        "WHERE status IN ('succeeded', 'failed', 'skipped')"
    )


def downgrade() -> None:
    op.drop_table("durable_jobs", schema="public")
