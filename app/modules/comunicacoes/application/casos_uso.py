"""Casos de uso de leitura e agendamento de comunicações."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from app.modules.comunicacoes.domain.assunto import montar_assunto
from app.modules.comunicacoes.domain.modelos import (
    Comunicacao,
    PoliticaEntrega,
    message_id_deterministico,
)

from .ports import PublicadorRealtimePort, RepositorioComunicacoesPort


class IdempotencyConflictError(RuntimeError):
    """A chave já foi vinculada a outro payload lógico."""


def request_fingerprint(
    *,
    actor_id: int,
    recipient: str,
    content: str,
    order_ref: str | None,
    subject: str | None,
) -> str:
    canonical = json.dumps(
        {
            "actor_id": actor_id,
            "content": content,
            "order_ref": order_ref,
            "recipient": recipient,
            "subject": subject,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def listar_comunicacoes(
    repository: RepositorioComunicacoesPort,
    *,
    page_size: int,
    cursor: tuple[datetime, str] | None,
) -> tuple[list[Comunicacao], int, bool]:
    return await repository.listar(page_size=page_size, cursor=cursor)


async def idempotency_key_persistida(
    repository: RepositorioComunicacoesPort,
    actor_id: int,
    idempotency_key: str,
) -> bool:
    """Replay persistido não é nova tentativa lógica nem consome quota."""

    return (
        await repository.obter_por_idempotencia(actor_id, idempotency_key) is not None
    )


async def obter_comunicacao(
    repository: RepositorioComunicacoesPort,
    communication_id: str,
) -> Comunicacao | None:
    return await repository.obter_por_id(communication_id)


async def agendar_comunicacao(
    repository: RepositorioComunicacoesPort,
    realtime: PublicadorRealtimePort,
    *,
    recipient: str,
    content: str,
    order_ref: str | None,
    subject: str | None,
    actor_id: int,
    idempotency_key: str,
    policy: PoliticaEntrega,
) -> tuple[Comunicacao, bool]:
    """Persiste comunicação+entrega+evento sem executar nenhum I/O SMTP."""

    fingerprint = request_fingerprint(
        actor_id=actor_id,
        recipient=recipient,
        content=content,
        order_ref=order_ref,
        subject=subject,
    )
    await repository.bloquear_idempotencia(actor_id, idempotency_key)
    existente = await repository.obter_por_idempotencia(actor_id, idempotency_key)
    if existente is not None:
        if existente.idempotency_fingerprint != fingerprint:
            raise IdempotencyConflictError(
                "Idempotency-Key já utilizada com outro payload."
            )
        return existente, False

    created_at = datetime.now(UTC)
    communication_id = f"comm-{uuid.uuid4().hex[:27]}"
    delivery_id = f"mail-{uuid.uuid4().hex[:27]}"
    comunicacao = await repository.criar_pendente(
        communication_id=communication_id,
        delivery_id=delivery_id,
        content=content,
        recipient=recipient,
        subject=montar_assunto(subject, order_ref),
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        idempotency_fingerprint=fingerprint,
        message_id=message_id_deterministico(communication_id),
        max_attempts=policy.max_attempts,
        created_at=created_at,
    )
    await realtime.registrar(
        event_type="communication.created",
        communication_id=communication_id,
    )
    return comunicacao, True
