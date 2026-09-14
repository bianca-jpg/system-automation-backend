"""Factories de composição do bounded context Realtime."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.realtime.application.ports import RealtimeRepositoryPort
from app.modules.realtime.infrastructure.persistence import (
    SqlRealtimeRepository,
)


def build_realtime_repository(db: AsyncSession) -> RealtimeRepositoryPort:
    """Liga o adapter à mesma transação recebida pela fachada pública."""

    return SqlRealtimeRepository(db)


__all__ = ["build_realtime_repository"]
