"""Orquestração do delivery outbox, independente de banco e provedor SMTP."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter

from app.modules.comunicacoes.domain.modelos import PoliticaEntrega

from .ports import (
    EntregadorEmailPort,
    FalhaEntregaEmail,
    MetricasEntregaPort,
    PublicadorRealtimePort,
    RepositorioEntregasPort,
    ResultadoIncertoEntrega,
    UnidadeTrabalhoPort,
)


@dataclass(slots=True)
class ResultadoProcessamento:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    unknown: int = 0
    stale: int = 0
    reconciled_unknown: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "sent": self.sent,
            "retried": self.retried,
            "failed": self.failed,
            "unknown": self.unknown,
            "stale": self.stale,
            "reconciledUnknown": self.reconciled_unknown,
        }


async def processar_entregas_pendentes(
    repository: RepositorioEntregasPort,
    unit_of_work: UnidadeTrabalhoPort,
    sender: EntregadorEmailPort,
    realtime: PublicadorRealtimePort,
    metrics: MetricasEntregaPort,
    *,
    policy: PoliticaEntrega,
    worker_id: str | None = None,
) -> ResultadoProcessamento:
    """Drena um lote. SMTP sempre ocorre depois do commit que registra o claim.

    Um ``processing`` cujo lease expirou vira ``unknown``. Ele nunca volta
    automaticamente para ``pending`` porque o processo anterior pode ter caído
    depois que o servidor SMTP aceitou DATA.
    """

    resultado = ResultadoProcessamento()
    owner = (worker_id or f"mail-{uuid.uuid4().hex}")[:64]

    now = datetime.now(UTC)
    expiradas = await repository.reconciliar_processamentos_expirados(
        now=now,
        limit=policy.batch_size,
    )
    for communication_id in expiradas:
        await realtime.registrar(
            event_type="communication.unknown",
            communication_id=communication_id,
        )
    await unit_of_work.commit()
    resultado.reconciled_unknown = len(expiradas)
    for _ in expiradas:
        metrics.concluida("unknown", 0.0)

    for _ in range(policy.batch_size):
        claimed_at = datetime.now(UTC)
        entrega = await repository.reivindicar_proxima(
            claimed_by=owner,
            now=claimed_at,
            lease_until=claimed_at + timedelta(seconds=policy.lease_seconds),
        )
        # O claim precisa estar durável antes de qualquer chamada SMTP.
        await unit_of_work.commit()
        if entrega is None:
            break

        resultado.claimed += 1
        metrics.reivindicada()
        started = perf_counter()
        failure: FalhaEntregaEmail | None = None
        try:
            await sender.enviar(entrega)
        except FalhaEntregaEmail as exc:
            failure = exc
        except Exception:  # noqa: BLE001 - resultado do provider é desconhecido
            failure = ResultadoIncertoEntrega("smtp_outcome_unknown")

        finished_at = datetime.now(UTC)
        if failure is None:
            communication_id = await repository.marcar_enviada(
                delivery_id=entrega.id,
                claimed_by=owner,
                now=finished_at,
            )
            if communication_id is None:
                outcome = "stale"
                resultado.stale += 1
            else:
                await realtime.registrar(
                    event_type="communication.sent",
                    communication_id=communication_id,
                )
                outcome = "sent"
                resultado.sent += 1
        else:
            terminal_status = failure.terminal_status
            if (
                terminal_status is None
                and entrega.attempt_count >= entrega.max_attempts
            ):
                terminal_status = "failed"
            next_attempt_at = (
                policy.proxima_tentativa(
                    delivery_id=entrega.id,
                    attempt_count=entrega.attempt_count,
                    now=finished_at,
                )
                if terminal_status is None
                else None
            )
            transition = await repository.registrar_falha(
                delivery_id=entrega.id,
                claimed_by=owner,
                now=finished_at,
                error_code=failure.error_code,
                terminal_status=terminal_status,
                next_attempt_at=next_attempt_at,
            )
            if transition is None:
                outcome = "stale"
                resultado.stale += 1
            else:
                communication_id, outcome = transition
                if outcome == "retry":
                    resultado.retried += 1
                elif outcome == "failed":
                    resultado.failed += 1
                    await realtime.registrar(
                        event_type="communication.failed",
                        communication_id=communication_id,
                    )
                else:
                    resultado.unknown += 1
                    await realtime.registrar(
                        event_type="communication.unknown",
                        communication_id=communication_id,
                    )

        await unit_of_work.commit()
        metrics.concluida(outcome, perf_counter() - started)

    return resultado
