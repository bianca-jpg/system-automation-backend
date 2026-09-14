"""SQLAlchemy model for the shared durable-job ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class DurableJobModel(Base):
    """Metadata only; request payloads and provider error text never live here."""

    __tablename__ = "durable_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('queued', 'running', 'retrying', 'succeeded', 'failed', 'skipped')",
            name="ck_durable_jobs_status",
        ),
        CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_durable_jobs_kind_safe",
        ),
        CheckConstraint(
            "scope_key IS NULL OR scope_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$'",
            name="ck_durable_jobs_scope_safe",
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_durable_jobs_idempotency_hash",
        ),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_durable_jobs_fingerprint",
        ),
        CheckConstraint(
            "owner_id IS NULL OR owner_id > 0",
            name="ck_durable_jobs_owner",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts",
            name="ck_durable_jobs_attempts",
        ),
        CheckConstraint(
            "status NOT IN ('queued', 'retrying') OR attempts < max_attempts",
            name="ck_durable_jobs_dispatchable_attempts",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_durable_jobs_max_attempts",
        ),
        CheckConstraint(
            "progress_current >= 0 AND "
            "(progress_total IS NULL OR "
            "(progress_total >= 0 AND progress_current <= progress_total))",
            name="ck_durable_jobs_progress",
        ),
        CheckConstraint(
            "result IS NULL OR "
            "(jsonb_typeof(result) = 'object' "
            "AND octet_length(result::text) <= 65536)",
            name="ck_durable_jobs_result_bounded",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_.:-]{0,63}$'",
            name="ck_durable_jobs_error_safe",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR "
            "lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$'",
            name="ck_durable_jobs_lease_owner_safe",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND started_at IS NOT NULL) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_durable_jobs_lease_state",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'skipped') "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'retrying') "
            "AND finished_at IS NULL)",
            name="ck_durable_jobs_terminal_state",
        ),
        CheckConstraint(
            "(status IN ('failed', 'retrying', 'skipped') "
            "AND error_code IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'succeeded') "
            "AND error_code IS NULL)",
            name="ck_durable_jobs_error_state",
        ),
        CheckConstraint(
            "(status = 'retrying' AND retryable) OR "
            "(status IN ('queued', 'running', 'succeeded', 'skipped') "
            "AND NOT retryable) OR status = 'failed'",
            name="ck_durable_jobs_retryable_state",
        ),
        CheckConstraint(
            "available_at >= requested_at",
            name="ck_durable_jobs_available_at",
        ),
        CheckConstraint(
            "deadline_at IS NULL OR deadline_at > requested_at",
            name="ck_durable_jobs_deadline",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= requested_at",
            name="ck_durable_jobs_started_at",
        ),
        CheckConstraint(
            "heartbeat_at IS NULL OR "
            "(started_at IS NOT NULL AND heartbeat_at >= started_at)",
            name="ck_durable_jobs_heartbeat_at",
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR "
            "(heartbeat_at IS NOT NULL AND lease_expires_at > heartbeat_at)",
            name="ck_durable_jobs_lease_expires_at",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= requested_at",
            name="ck_durable_jobs_finished_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'queued'")
    )
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )
    progress_current: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    progress_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "uq_durable_jobs_owner_idempotency",
    DurableJobModel.owner_id,
    DurableJobModel.idempotency_key_hash,
    unique=True,
    postgresql_where=text("owner_id IS NOT NULL AND idempotency_key_hash IS NOT NULL"),
)
Index(
    "uq_durable_jobs_system_idempotency",
    DurableJobModel.idempotency_key_hash,
    unique=True,
    postgresql_where=text("owner_id IS NULL AND idempotency_key_hash IS NOT NULL"),
)
Index(
    "uq_durable_jobs_active_scope",
    DurableJobModel.kind,
    DurableJobModel.scope_key,
    unique=True,
    postgresql_where=text(
        "scope_key IS NOT NULL AND status IN ('queued', 'running', 'retrying')"
    ),
)
Index(
    "ix_durable_jobs_dispatchable",
    DurableJobModel.available_at,
    DurableJobModel.requested_at,
    DurableJobModel.id,
    postgresql_where=text("status IN ('queued', 'retrying')"),
)
Index(
    "ix_durable_jobs_expired_lease",
    DurableJobModel.lease_expires_at,
    DurableJobModel.id,
    postgresql_where=text("status = 'running'"),
)
Index(
    "ix_durable_jobs_pending_deadline",
    DurableJobModel.deadline_at,
    DurableJobModel.id,
    postgresql_where=text(
        "deadline_at IS NOT NULL AND status IN ('queued', 'retrying')"
    ),
)
Index(
    "ix_durable_jobs_terminal_retention",
    DurableJobModel.finished_at,
    DurableJobModel.id,
    postgresql_where=text("status IN ('succeeded', 'failed', 'skipped')"),
)


class DurableJobIdempotencyBindingModel(Base):
    """Durable request-key binding, including requests coalesced onto another job."""

    __tablename__ = "durable_job_idempotency_bindings"
    __table_args__ = (
        CheckConstraint(
            "owner_id IS NULL OR owner_id > 0",
            name="ck_durable_job_binding_owner",
        ),
        CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="ck_durable_job_binding_kind_safe",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_durable_job_binding_key_hash",
        ),
        CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_durable_job_binding_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("durable_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "uq_durable_job_binding_owner_key",
    DurableJobIdempotencyBindingModel.owner_id,
    DurableJobIdempotencyBindingModel.idempotency_key_hash,
    unique=True,
    postgresql_where=text("owner_id IS NOT NULL"),
)
Index(
    "uq_durable_job_binding_system_key",
    DurableJobIdempotencyBindingModel.idempotency_key_hash,
    unique=True,
    postgresql_where=text("owner_id IS NULL"),
)
Index(
    "ix_durable_job_binding_job_id",
    DurableJobIdempotencyBindingModel.job_id,
)
