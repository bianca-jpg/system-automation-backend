"""Persistência do plano e do checkpoint de leitura (spec, chunks, standby)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos.domain.standby_motivo import SEM_CREDITO
from app.modules.pedidos.infrastructure.models import PedidoStandbyMotivo
from app.modules.pedidos.processing.domain import (
    APPLY_CHUNK_SIZE,
    PlanDraft,
    PlannedPair,
    PlanningState,
    PlanRowState,
    ProcessingChannel,
    ProcessingMode,
    ProcessingPlanConflict,
    ProcessingSpec,
)
from app.modules.pedidos.processing.infrastructure.chunking import _chunks
from app.modules.pedidos.processing.infrastructure.models import (
    PedidoProcessamentoModel,
    PedidoProcessamentoPlanModel,
)
from app.modules.pedidos.processing.infrastructure.repository_mappers import (
    _to_pair,
    _to_spec,
)


class SqlAlchemyProcessingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_spec(
        self,
        *,
        job_id: UUID,
        mode: ProcessingMode,
        channel: ProcessingChannel,
    ) -> ProcessingSpec:
        await self._db.execute(
            pg_insert(PedidoProcessamentoModel)
            .values(job_id=job_id, mode=mode.value, channel=channel.value)
            .on_conflict_do_nothing(index_elements=[PedidoProcessamentoModel.job_id])
        )
        row = await self._db.get(PedidoProcessamentoModel, job_id)
        if row is None:
            raise RuntimeError("processing_job_header_not_visible")
        if row.mode != mode.value or row.channel != channel.value:
            raise ProcessingPlanConflict("processing_job_header_conflict")
        return _to_spec(row)

    async def get_spec(
        self, job_id: UUID, *, for_update: bool = False
    ) -> ProcessingSpec | None:
        stmt = select(PedidoProcessamentoModel).where(
            PedidoProcessamentoModel.job_id == job_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._db.scalar(stmt)
        return _to_spec(row) if row is not None else None

    async def store_plan(
        self,
        *,
        job_id: UUID,
        draft: PlanDraft,
        planned_at: datetime,
    ) -> ProcessingSpec:
        row = await self._db.scalar(
            select(PedidoProcessamentoModel)
            .where(PedidoProcessamentoModel.job_id == job_id)
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("processing_job_header_missing")
        if row.planning_state == PlanningState.PLANNED.value:
            if row.plan_hash != draft.plan_hash:
                raise ProcessingPlanConflict("processing_plan_already_frozen")
            return _to_spec(row)
        existing = await self._db.scalar(
            select(PedidoProcessamentoPlanModel.ordinal)
            .where(PedidoProcessamentoPlanModel.job_id == job_id)
            .limit(1)
        )
        if existing is not None:
            raise ProcessingPlanConflict("processing_plan_rows_without_header")

        for planned_chunk in _chunks(draft.rows, APPLY_CHUNK_SIZE):
            values = [
                {
                    "job_id": job_id,
                    "ordinal": planned.ordinal,
                    "nr_pedido": planned.nr_pedido,
                    "cd_prod_cor": planned.cd_prod_cor,
                    "payload": dict(planned.payload),
                    "payload_hash": planned.payload_hash,
                    "state": PlanRowState.PLANNED.value,
                }
                for planned in planned_chunk
            ]
            await self._db.execute(
                pg_insert(PedidoProcessamentoPlanModel).values(values)
            )
        row.planning_state = PlanningState.PLANNED.value
        row.next_ordinal = 1
        row.candidate_count = draft.candidate_count
        row.planned_count = draft.planned_count
        row.applied_count = 0
        row.deferred_count = draft.deferred_count
        row.blocked_credit_count = draft.blocked_credit_count
        row.plan_hash = draft.plan_hash
        row.planned_at = planned_at
        await self._db.flush()
        return _to_spec(row)

    async def record_standby_reasons(
        self,
        *,
        job_id: UUID,
        deferred_pairs: Sequence[tuple[int, str, str, str]],
        blocked_credit_pairs: Sequence[tuple[int, str, str]],
        recorded_at: datetime,
    ) -> None:
        # Upsert único (nunca delete-then-insert) na PK composta
        # (nr_pedido, cd_prod_cor): a linha pode mudar de motivo entre
        # rodadas sem duplicar, e `execucoes_consecutivas` é incrementado
        # por EXPRESSÃO de coluna dentro do próprio SET do
        # ON CONFLICT DO UPDATE (valor pré-update lido pelo Postgres, sem
        # SELECT prévio nem janela de corrida read-modify-write). Chunkado
        # por APPLY_CHUNK_SIZE, mesmo precedente de `store_plan`: um único
        # INSERT com todos os pares de um plano grande (até PLAN_PAIR_LIMIT)
        # passaria do teto de 65535 bind parameters do protocolo Postgres e
        # abortaria a transação de planejamento inteira. O contador é
        # agnóstico ao motivo (conta execuções seguidas em ALGUM stand-by,
        # não "execuções seguidas com o MESMO motivo") — [ASSUMED] A2 do
        # 16-RESEARCH.md. Uma tentativa que faz rollback não infla o
        # contador, porque o incremento só existe se o commit existir.
        values: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for nr_pedido, cd_prod_cor, canal, motivo in deferred_pairs:
            key = (nr_pedido, cd_prod_cor)
            if key in seen:
                raise ProcessingPlanConflict("standby_duplicate_pair")
            seen.add(key)
            values.append(
                {
                    "nr_pedido": nr_pedido,
                    "cd_prod_cor": cd_prod_cor,
                    "canal": canal,
                    "motivo": motivo,
                    "job_id": job_id,
                    "atualizado_em": recorded_at,
                }
            )
        for nr_pedido, cd_prod_cor, canal in blocked_credit_pairs:
            key = (nr_pedido, cd_prod_cor)
            if key in seen:
                raise ProcessingPlanConflict("standby_duplicate_pair")
            seen.add(key)
            values.append(
                {
                    "nr_pedido": nr_pedido,
                    "cd_prod_cor": cd_prod_cor,
                    "canal": canal,
                    "motivo": SEM_CREDITO,
                    "job_id": job_id,
                    "atualizado_em": recorded_at,
                }
            )
        if not values:
            return
        for chunk in _chunks(values, APPLY_CHUNK_SIZE):
            stmt = pg_insert(PedidoStandbyMotivo).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    PedidoStandbyMotivo.nr_pedido,
                    PedidoStandbyMotivo.cd_prod_cor,
                ],
                set_={
                    "canal": stmt.excluded.canal,
                    "motivo": stmt.excluded.motivo,
                    "job_id": stmt.excluded.job_id,
                    "atualizado_em": stmt.excluded.atualizado_em,
                    "execucoes_consecutivas": (
                        PedidoStandbyMotivo.execucoes_consecutivas + 1
                    ),
                },
            )
            await self._db.execute(stmt)
        await self._db.flush()

    async def load_next_chunk(
        self,
        *,
        job_id: UUID,
        limit: int,
    ) -> tuple[PlannedPair, ...]:
        if not 1 <= limit <= APPLY_CHUNK_SIZE:
            raise ValueError("processing_chunk_limit_out_of_range")
        header = await self._db.scalar(
            select(PedidoProcessamentoModel)
            .where(PedidoProcessamentoModel.job_id == job_id)
            .with_for_update()
        )
        if header is None:
            raise RuntimeError("processing_job_header_missing")
        if header.planning_state != PlanningState.PLANNED.value:
            raise ProcessingPlanConflict("processing_plan_not_frozen")
        rows = (
            await self._db.scalars(
                select(PedidoProcessamentoPlanModel)
                .where(
                    PedidoProcessamentoPlanModel.job_id == job_id,
                    PedidoProcessamentoPlanModel.state == PlanRowState.PLANNED.value,
                    PedidoProcessamentoPlanModel.ordinal >= header.next_ordinal,
                )
                .order_by(PedidoProcessamentoPlanModel.ordinal)
                .with_for_update()
                .limit(limit)
            )
        ).all()
        if not rows:
            if header.applied_count != header.planned_count:
                raise ProcessingPlanConflict("processing_plan_checkpoint_has_gap")
            return ()
        expected = tuple(range(header.next_ordinal, header.next_ordinal + len(rows)))
        if tuple(row.ordinal for row in rows) != expected:
            raise ProcessingPlanConflict("processing_plan_ordinals_have_gap")
        return tuple(_to_pair(row) for row in rows)

    async def checkpoint_chunk(
        self,
        *,
        job_id: UUID,
        rows: Sequence[PlannedPair],
        applied_at: datetime,
    ) -> ProcessingSpec:
        if not rows or len(rows) > APPLY_CHUNK_SIZE:
            raise ValueError("processing_checkpoint_chunk_out_of_range")
        ordinals = tuple(row.ordinal for row in rows)
        if ordinals != tuple(range(ordinals[0], ordinals[0] + len(ordinals))):
            raise ProcessingPlanConflict("processing_checkpoint_not_contiguous")
        header = await self._db.scalar(
            select(PedidoProcessamentoModel)
            .where(PedidoProcessamentoModel.job_id == job_id)
            .with_for_update()
        )
        if header is None:
            raise RuntimeError("processing_job_header_missing")
        if header.next_ordinal != ordinals[0]:
            raise ProcessingPlanConflict("processing_checkpoint_stale")
        persisted = (
            await self._db.scalars(
                select(PedidoProcessamentoPlanModel)
                .where(
                    PedidoProcessamentoPlanModel.job_id == job_id,
                    PedidoProcessamentoPlanModel.ordinal.in_(ordinals),
                )
                .order_by(PedidoProcessamentoPlanModel.ordinal)
                .with_for_update()
            )
        ).all()
        if len(persisted) != len(rows):
            raise ProcessingPlanConflict("processing_checkpoint_rows_missing")
        expected_hashes = tuple(row.payload_hash for row in rows)
        if (
            tuple(row.ordinal for row in persisted) != ordinals
            or tuple(row.payload_hash for row in persisted) != expected_hashes
            or any(row.state != PlanRowState.PLANNED.value for row in persisted)
        ):
            raise ProcessingPlanConflict("processing_checkpoint_rows_changed")
        await self._db.execute(
            update(PedidoProcessamentoPlanModel)
            .where(
                PedidoProcessamentoPlanModel.job_id == job_id,
                PedidoProcessamentoPlanModel.ordinal.in_(ordinals),
                PedidoProcessamentoPlanModel.state == PlanRowState.PLANNED.value,
            )
            .values(state=PlanRowState.APPLIED.value, applied_at=applied_at)
        )
        header.applied_count += len(rows)
        header.next_ordinal = ordinals[-1] + 1
        await self._db.flush()
        return _to_spec(header)


__all__ = ["SqlAlchemyProcessingRepository"]
