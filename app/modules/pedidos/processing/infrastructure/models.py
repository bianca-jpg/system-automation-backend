"""Metadata SQLAlchemy da extensão e do plano de processamento de Pedidos."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class PedidoProcessamentoModel(Base):
    __tablename__ = "pedido_processamentos"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('adequar', 'sem_adequar')",
            name="ck_pedido_processamentos_mode",
        ),
        CheckConstraint(
            "channel IN ('Todos', 'Franquia', 'Multimarca')",
            name="ck_pedido_processamentos_channel",
        ),
        CheckConstraint(
            "planning_state IN ('pending', 'planned')",
            name="ck_pedido_processamentos_planning_state",
        ),
        CheckConstraint(
            "candidate_count BETWEEN 0 AND 100000 AND "
            "planned_count BETWEEN 0 AND candidate_count AND "
            "applied_count BETWEEN 0 AND planned_count AND "
            "deferred_count BETWEEN 0 AND candidate_count AND "
            "blocked_credit_count BETWEEN 0 AND candidate_count",
            name="ck_pedido_processamentos_counts",
        ),
        CheckConstraint(
            "next_ordinal = applied_count + 1 "
            "AND next_ordinal BETWEEN 1 AND planned_count + 1",
            name="ck_pedido_processamentos_checkpoint",
        ),
        CheckConstraint(
            "(planning_state = 'pending' AND plan_hash IS NULL "
            "AND planned_at IS NULL AND candidate_count = 0 "
            "AND planned_count = 0 AND applied_count = 0 "
            "AND deferred_count = 0 AND blocked_credit_count = 0 "
            "AND next_ordinal = 1) OR "
            "(planning_state = 'planned' "
            "AND plan_hash ~ '^[0-9a-f]{64}$' AND planned_at IS NOT NULL)",
            name="ck_pedido_processamentos_plan_state",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("durable_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    planning_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    next_ordinal: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    planned_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    applied_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    deferred_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    blocked_credit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    planned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PedidoProcessamentoPlanModel(Base):
    __tablename__ = "pedido_processamento_plan"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id"],
            ["pedido_processamentos.job_id"],
            ondelete="CASCADE",
            name="fk_pedido_processamento_plan_job",
        ),
        UniqueConstraint(
            "job_id",
            "nr_pedido",
            "cd_prod_cor",
            name="uq_pedido_processamento_plan_pair",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 100000",
            name="ck_pedido_processamento_plan_ordinal",
        ),
        CheckConstraint(
            "nr_pedido BETWEEN 1 AND 2147483647",
            name="ck_pedido_processamento_plan_order",
        ),
        CheckConstraint(
            "length(cd_prod_cor) BETWEEN 1 AND 64 AND cd_prod_cor = trim(cd_prod_cor)",
            name="ck_pedido_processamento_plan_product",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND octet_length(payload::text) <= 131072",
            name="ck_pedido_processamento_plan_payload",
        ),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pedido_processamento_plan_hash",
        ),
        CheckConstraint(
            "state IN ('planned', 'applied')",
            name="ck_pedido_processamento_plan_state",
        ),
        CheckConstraint(
            "(state = 'planned' AND applied_at IS NULL) OR "
            "(state = 'applied' AND applied_at IS NOT NULL)",
            name="ck_pedido_processamento_plan_applied_state",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    nr_pedido: Mapped[int] = mapped_column(Integer, nullable=False)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'planned'")
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "ix_pedido_processamento_plan_pending",
    PedidoProcessamentoPlanModel.job_id,
    PedidoProcessamentoPlanModel.ordinal,
    postgresql_where=text("state = 'planned'"),
)


__all__ = ["PedidoProcessamentoModel", "PedidoProcessamentoPlanModel"]
