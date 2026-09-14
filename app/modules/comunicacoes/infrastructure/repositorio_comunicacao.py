"""Adapter SQLAlchemy dos repositórios de comunicações e entregas."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.comunicacoes.domain.modelos import (
    Comunicacao as ComunicacaoDominio,
)
from app.modules.comunicacoes.domain.modelos import (
    EntregaEmail,
    StatusComunicacao,
)
from app.modules.comunicacoes.infrastructure.models import (
    Comunicacao,
    ComunicacaoEmailDelivery,
)

_BRT = timezone(timedelta(hours=-3))


def _to_domain(
    row: Comunicacao,
    delivery: ComunicacaoEmailDelivery | None = None,
) -> ComunicacaoDominio:
    return ComunicacaoDominio(
        id=row.id,
        type=row.type,
        status=row.status,
        time=row.time,
        content=row.content,
        recipient=row.recipient,
        created_at=row.created_at,
        requested_by_user_id=row.requested_by_user_id,
        idempotency_key=row.idempotency_key,
        idempotency_fingerprint=row.idempotency_fingerprint,
        attempt_count=delivery.attempt_count if delivery is not None else None,
        max_attempts=delivery.max_attempts if delivery is not None else None,
        updated_at=delivery.updated_at if delivery is not None else row.created_at,
        sent_at=delivery.sent_at if delivery is not None else None,
        next_attempt_at=(
            delivery.next_attempt_at
            if delivery is not None and row.status == StatusComunicacao.PENDENTE.value
            else None
        ),
    )


class RepositorioComunicacoesSqlAlchemy:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def listar(
        self,
        *,
        page_size: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[list[ComunicacaoDominio], int, bool]:
        stmt = select(Comunicacao, ComunicacaoEmailDelivery).outerjoin(
            ComunicacaoEmailDelivery,
            ComunicacaoEmailDelivery.communication_id == Comunicacao.id,
        )
        if cursor is not None:
            created_at, communication_id = cursor
            stmt = stmt.where(
                or_(
                    Comunicacao.created_at < created_at,
                    and_(
                        Comunicacao.created_at == created_at,
                        Comunicacao.id < communication_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            Comunicacao.created_at.desc(), Comunicacao.id.desc()
        ).limit(page_size + 1)

        rows = list((await self._db.execute(stmt)).all())
        total = int(
            await self._db.scalar(select(func.count()).select_from(Comunicacao)) or 0
        )
        has_more = len(rows) > page_size
        return (
            [
                _to_domain(communication, delivery)
                for communication, delivery in rows[:page_size]
            ],
            total,
            has_more,
        )

    async def bloquear_idempotencia(self, actor_id: int, key: str) -> None:
        digest = hashlib.sha256(f"comunicacoes:{actor_id}:{key}".encode()).digest()[:8]
        lock_id = int.from_bytes(digest, byteorder="big", signed=True)
        await self._db.execute(select(func.pg_advisory_xact_lock(lock_id)))

    async def obter_por_idempotencia(
        self, actor_id: int, key: str
    ) -> ComunicacaoDominio | None:
        row = (
            await self._db.execute(
                select(Comunicacao, ComunicacaoEmailDelivery)
                .outerjoin(
                    ComunicacaoEmailDelivery,
                    ComunicacaoEmailDelivery.communication_id == Comunicacao.id,
                )
                .where(
                    Comunicacao.requested_by_user_id == actor_id,
                    Comunicacao.idempotency_key == key,
                )
            )
        ).first()
        return _to_domain(*row) if row is not None else None

    async def obter_por_id(self, communication_id: str) -> ComunicacaoDominio | None:
        row = (
            await self._db.execute(
                select(Comunicacao, ComunicacaoEmailDelivery)
                .outerjoin(
                    ComunicacaoEmailDelivery,
                    ComunicacaoEmailDelivery.communication_id == Comunicacao.id,
                )
                .where(Comunicacao.id == communication_id)
            )
        ).first()
        return _to_domain(*row) if row is not None else None

    async def criar_pendente(
        self,
        *,
        communication_id: str,
        delivery_id: str,
        content: str,
        recipient: str,
        subject: str,
        actor_id: int,
        idempotency_key: str,
        idempotency_fingerprint: str,
        message_id: str,
        max_attempts: int,
        created_at: datetime,
    ) -> ComunicacaoDominio:
        comunicacao = Comunicacao(
            id=communication_id,
            type="Email",
            status=StatusComunicacao.PENDENTE.value,
            time=created_at.astimezone(_BRT).strftime("%H:%M"),
            content=content,
            recipient=recipient,
            requested_by_user_id=actor_id,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=idempotency_fingerprint,
            created_at=created_at,
        )
        delivery = ComunicacaoEmailDelivery(
            id=delivery_id,
            communication_id=communication_id,
            status="pending",
            subject=subject,
            message_id=message_id,
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        self._db.add_all((comunicacao, delivery))
        await self._db.flush()
        return _to_domain(comunicacao, delivery)


class RepositorioEntregasSqlAlchemy:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def reconciliar_processamentos_expirados(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[str]:
        rows = list(
            (
                await self._db.execute(
                    select(ComunicacaoEmailDelivery)
                    .where(
                        ComunicacaoEmailDelivery.status == "processing",
                        ComunicacaoEmailDelivery.claimed_until < now,
                    )
                    .order_by(
                        ComunicacaoEmailDelivery.claimed_until,
                        ComunicacaoEmailDelivery.id,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        communication_ids = [row.communication_id for row in rows]
        if not rows:
            return []
        delivery_ids = [row.id for row in rows]
        await self._db.execute(
            update(ComunicacaoEmailDelivery)
            .where(ComunicacaoEmailDelivery.id.in_(delivery_ids))
            .values(
                status="unknown",
                claimed_by=None,
                claimed_until=None,
                last_error_code="worker_lease_expired",
                sent_at=None,
                failed_at=now,
                updated_at=now,
            )
        )
        await self._db.execute(
            update(Comunicacao)
            .where(Comunicacao.id.in_(communication_ids))
            .values(status=StatusComunicacao.INCERTO.value)
        )
        return communication_ids

    async def reivindicar_proxima(
        self,
        *,
        claimed_by: str,
        now: datetime,
        lease_until: datetime,
    ) -> EntregaEmail | None:
        result = await self._db.execute(
            select(ComunicacaoEmailDelivery, Comunicacao)
            .join(
                Comunicacao,
                Comunicacao.id == ComunicacaoEmailDelivery.communication_id,
            )
            .where(
                ComunicacaoEmailDelivery.status == "pending",
                ComunicacaoEmailDelivery.next_attempt_at <= now,
            )
            .order_by(
                ComunicacaoEmailDelivery.next_attempt_at,
                ComunicacaoEmailDelivery.id,
            )
            .with_for_update(of=ComunicacaoEmailDelivery, skip_locked=True)
            .limit(1)
        )
        claimed = result.first()
        if claimed is None:
            return None
        delivery, communication = claimed
        delivery.status = "processing"
        delivery.claimed_by = claimed_by
        delivery.claimed_until = lease_until
        delivery.attempt_count += 1
        delivery.updated_at = now
        await self._db.flush()
        return EntregaEmail(
            id=delivery.id,
            recipient=communication.recipient,
            subject=delivery.subject,
            content=communication.content,
            message_id=delivery.message_id,
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
        )

    async def marcar_enviada(
        self,
        *,
        delivery_id: str,
        claimed_by: str,
        now: datetime,
    ) -> str | None:
        communication_id = await self._db.scalar(
            update(ComunicacaoEmailDelivery)
            .where(
                ComunicacaoEmailDelivery.id == delivery_id,
                ComunicacaoEmailDelivery.status == "processing",
                ComunicacaoEmailDelivery.claimed_by == claimed_by,
            )
            .values(
                status="sent",
                claimed_by=None,
                claimed_until=None,
                last_error_code=None,
                sent_at=now,
                failed_at=None,
                updated_at=now,
            )
            .returning(ComunicacaoEmailDelivery.communication_id)
        )
        if communication_id is None:
            return None
        await self._db.execute(
            update(Comunicacao)
            .where(Comunicacao.id == communication_id)
            .values(status=StatusComunicacao.ENVIADO.value)
        )
        return communication_id

    async def registrar_falha(
        self,
        *,
        delivery_id: str,
        claimed_by: str,
        now: datetime,
        error_code: str,
        terminal_status: str | None,
        next_attempt_at: datetime | None,
    ) -> tuple[str, str] | None:
        if terminal_status not in {None, "failed", "unknown"}:
            raise ValueError("terminal_status inválido")
        delivery_status = terminal_status or "pending"
        values: dict[str, object] = {
            "status": delivery_status,
            "claimed_by": None,
            "claimed_until": None,
            "last_error_code": error_code[:64],
            "updated_at": now,
            "sent_at": None,
        }
        if terminal_status is None:
            if next_attempt_at is None:
                raise ValueError("retry exige next_attempt_at")
            values["next_attempt_at"] = next_attempt_at
            values["failed_at"] = None
        else:
            values["failed_at"] = now

        communication_id = await self._db.scalar(
            update(ComunicacaoEmailDelivery)
            .where(
                ComunicacaoEmailDelivery.id == delivery_id,
                ComunicacaoEmailDelivery.status == "processing",
                ComunicacaoEmailDelivery.claimed_by == claimed_by,
            )
            .values(**values)
            .returning(ComunicacaoEmailDelivery.communication_id)
        )
        if communication_id is None:
            return None
        if terminal_status is None:
            return communication_id, "retry"
        communication_status = (
            StatusComunicacao.FALHOU.value
            if terminal_status == "failed"
            else StatusComunicacao.INCERTO.value
        )
        await self._db.execute(
            update(Comunicacao)
            .where(Comunicacao.id == communication_id)
            .values(status=communication_status)
        )
        return communication_id, terminal_status
