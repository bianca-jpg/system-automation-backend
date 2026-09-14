from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class Comunicacao(Base):
    __tablename__ = "comunicacoes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    time: Mapped[str] = mapped_column(String(8), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Índices declarados fora da classe para poder expressar tanto a ordenação DESC
# do keyset quanto a unicidade parcial (múltiplos NULL continuam permitidos).
Index(
    "uq_comunicacoes_idempotency_key",
    Comunicacao.requested_by_user_id,
    Comunicacao.idempotency_key,
    unique=True,
    postgresql_where=and_(
        Comunicacao.requested_by_user_id.is_not(None),
        Comunicacao.idempotency_key.is_not(None),
    ),
)
Index(
    "ix_comunicacoes_created_at_id_desc",
    Comunicacao.created_at.desc(),
    Comunicacao.id.desc(),
)


class ComunicacaoEmailDelivery(Base):
    """Delivery outbox SMTP; nunca contém credenciais nem erro do provider."""

    __tablename__ = "communication_email_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'unknown')",
            name="ck_communication_email_delivery_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_communication_email_delivery_attempts",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_communication_email_delivery_max_attempts",
        ),
        CheckConstraint(
            "status <> 'pending' OR attempt_count < max_attempts",
            name="ck_communication_email_delivery_pending_attempts",
        ),
        CheckConstraint(
            "(status = 'processing' AND claimed_by IS NOT NULL "
            "AND claimed_until IS NOT NULL) OR "
            "(status <> 'processing' AND claimed_by IS NULL "
            "AND claimed_until IS NULL)",
            name="ck_communication_email_delivery_lease_state",
        ),
        CheckConstraint(
            "(status = 'sent' AND sent_at IS NOT NULL AND failed_at IS NULL) OR "
            "(status IN ('failed', 'unknown') AND failed_at IS NOT NULL "
            "AND sent_at IS NULL) OR "
            "(status IN ('pending', 'processing') AND sent_at IS NULL "
            "AND failed_at IS NULL)",
            name="ck_communication_email_delivery_terminal_state",
        ),
        UniqueConstraint(
            "communication_id",
            name="uq_communication_email_delivery_communication",
        ),
        UniqueConstraint(
            "message_id",
            name="uq_communication_email_delivery_message_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    communication_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("comunicacoes.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'pending'"),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        server_default=text("0"),
        nullable=False,
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger,
        server_default=text("5"),
        nullable=False,
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Index(
    "ix_communication_email_delivery_pending",
    ComunicacaoEmailDelivery.next_attempt_at,
    ComunicacaoEmailDelivery.id,
    postgresql_where=ComunicacaoEmailDelivery.status == "pending",
)
Index(
    "ix_communication_email_delivery_expired",
    ComunicacaoEmailDelivery.claimed_until,
    ComunicacaoEmailDelivery.id,
    postgresql_where=ComunicacaoEmailDelivery.status == "processing",
)
