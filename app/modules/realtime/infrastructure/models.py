"""Modelos persistentes do realtime.

``RealtimeOutbox`` pertence à mesma transação da mutação de negócio. O relay só
transporta linhas já commitadas; falha de Redis nunca desfaz uma operação REST.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class RealtimeOutbox(Base):
    __tablename__ = "realtime_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_realtime_outbox_event_id"),
        UniqueConstraint(
            "topic", "topic_sequence", name="uq_realtime_outbox_topic_sequence"
        ),
        CheckConstraint("sequence > 0", name="ck_realtime_outbox_sequence"),
        CheckConstraint(
            "topic_sequence > 0",
            name="ck_realtime_outbox_topic_seq",
        ),
        CheckConstraint("attempts >= 0", name="ck_realtime_outbox_attempts"),
        CheckConstraint("version = 1", name="ck_realtime_outbox_version"),
        Index(
            "ix_realtime_outbox_pending",
            "next_attempt_at",
            "sequence",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index(
            "ix_realtime_outbox_unpublished_head",
            "sequence",
            postgresql_where=text("published_at IS NULL"),
        ),
        Index(
            "ix_realtime_outbox_published_cleanup",
            "published_at",
            "sequence",
            postgresql_where=text("published_at IS NOT NULL"),
        ),
        Index("ix_realtime_outbox_topic_global_sequence", "topic", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True, nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    topic_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RealtimeTopicState(Base):
    __tablename__ = "realtime_topic_state"
    __table_args__ = (
        CheckConstraint(
            "latest_sequence >= 0",
            name="ck_realtime_topic_latest_seq",
        ),
        CheckConstraint(
            "latest_topic_sequence >= 0",
            name="ck_realtime_topic_latest_topic_seq",
        ),
        CheckConstraint(
            "replay_floor_sequence >= 0",
            name="ck_realtime_topic_replay_floor",
        ),
    )

    topic: Mapped[str] = mapped_column(String(64), primary_key=True)
    observed_initialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latest_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    latest_topic_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    replay_floor_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    latest_event: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RealtimeReadCursor(Base):
    __tablename__ = "realtime_read_cursors"
    __table_args__ = (
        CheckConstraint(
            "last_read_sequence >= 0",
            name="ck_realtime_cursor_seq",
        ),
        CheckConstraint(
            "last_read_topic_sequence >= 0",
            name="ck_realtime_cursor_topic_seq",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_read_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    last_read_topic_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RealtimeObservedEntity(Base):
    __tablename__ = "realtime_observed_entities"
    __table_args__ = (
        Index(
            "ix_realtime_observed_entities_active_topic",
            "topic",
            postgresql_where=text("active IS TRUE"),
        ),
    )

    topic: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("realtime_topic_state.topic", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
