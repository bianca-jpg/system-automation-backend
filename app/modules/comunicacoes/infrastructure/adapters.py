"""Adapters pequenos compartilhados pela API e pelo worker."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.realtime import record_event


class UnidadeTrabalhoSqlAlchemy:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def commit(self) -> None:
        await self._db.commit()


class PublicadorComunicacoesRealtime:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def registrar(
        self,
        *,
        event_type: str,
        communication_id: str,
    ) -> None:
        await record_event(
            self._db,
            topic="communications",
            event_type=event_type,
            payload={"id": communication_id},
        )
