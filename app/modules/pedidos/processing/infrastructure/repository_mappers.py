"""Conversão de rows ORM para os DTOs do domínio de processing (leitura)."""

from __future__ import annotations

from app.modules.pedidos.processing.domain import (
    PlannedPair,
    PlanningState,
    ProcessingChannel,
    ProcessingMode,
    ProcessingSpec,
)
from app.modules.pedidos.processing.infrastructure.models import (
    PedidoProcessamentoModel,
    PedidoProcessamentoPlanModel,
)


def _to_spec(row: PedidoProcessamentoModel) -> ProcessingSpec:
    return ProcessingSpec(
        job_id=row.job_id,
        mode=ProcessingMode(row.mode),
        channel=ProcessingChannel(row.channel),
        planning_state=PlanningState(row.planning_state),
        next_ordinal=row.next_ordinal,
        candidate_count=row.candidate_count,
        planned_count=row.planned_count,
        applied_count=row.applied_count,
        deferred_count=row.deferred_count,
        blocked_credit_count=row.blocked_credit_count,
        plan_hash=row.plan_hash,
        planned_at=row.planned_at,
    )


def _to_pair(row: PedidoProcessamentoPlanModel) -> PlannedPair:
    return PlannedPair(
        ordinal=row.ordinal,
        nr_pedido=row.nr_pedido,
        cd_prod_cor=row.cd_prod_cor,
        payload=row.payload,
        payload_hash=row.payload_hash,
    )
