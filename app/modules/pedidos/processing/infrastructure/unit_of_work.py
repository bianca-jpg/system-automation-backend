"""Unit of work transacional do processing (commit/rollback da sessão)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyProcessingUnitOfWork:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def commit(self) -> None:
        await self._db.commit()

    async def rollback(self) -> None:
        await self._db.rollback()


__all__ = ["SqlAlchemyProcessingUnitOfWork"]
