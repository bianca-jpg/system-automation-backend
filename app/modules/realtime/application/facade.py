"""Fachada transacional consumida pelos demais bounded contexts.

Estas funções **nunca** fazem commit ou rollback. O chamador deve invocá-las na
mesma ``AsyncSession`` da mutação de negócio e é o único dono da transação. Isso
é o que torna o evento atômico com pedidos/comunicações/ingestão.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.modules.realtime.domain import (
    InvalidRealtimeEvent,
    RealtimeEventEnvelope,
    normalize_payload,
    validate_event_type,
    validate_topic,
)
from app.shared.config.settings import get_settings

from .ports import RealtimeRepositoryPort

_ENTITY_KEY_MAX_LENGTH = 256
# O snapshot ERP real possui ~133 mil pares. O repositório persiste em lotes de
# 5 mil, então este limite protege a normalização em memória sem impedir o full
# refresh autoritativo. A margem cobre crescimento sem transformar a API numa
# entrada sem limite.
_OBSERVATION_MAX_KEYS = 500_000
_SCOPED_OBSERVATION_MAX_KEYS = 10_000
_SCOPED_OBSERVATION_MAX_PREFIXES = 1_000
_SCOPE_PREFIX_MAX_LENGTH = _ENTITY_KEY_MAX_LENGTH


async def enqueue_realtime_event(
    repository: RealtimeRepositoryPort,
    *,
    topic: str,
    event_type: str,
    payload: Mapping[str, Any],
    occurred_at: datetime | None = None,
    event_id: UUID | None = None,
) -> RealtimeEventEnvelope:
    """Anexa um evento ao outbox da transação corrente e faz apenas ``flush``."""

    normalized_topic = validate_topic(topic)
    normalized_type = validate_event_type(event_type)
    normalized_payload = normalize_payload(
        payload,
        max_bytes=get_settings().realtime_payload_max_bytes,
    )
    timestamp = occurred_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise InvalidRealtimeEvent("occurred_at deve conter timezone")

    return await repository.persist_event(
        event_id=event_id or uuid4(),
        topic=normalized_topic,
        event_type=normalized_type,
        occurred_at=timestamp.astimezone(UTC),
        payload=normalized_payload,
    )


async def observe_entity_keys(
    repository: RealtimeRepositoryPort,
    topic: str,
    keys: Iterable[str | int],
    baseline_if_empty: bool = True,
) -> list[str]:
    """Persiste chaves vistas e devolve somente as novas após o baseline.

    O primeiro chamado inicializa o tópico mesmo com ``keys=[]``. Assim uma
    fonte inicialmente vazia não faz o primeiro pedido futuro virar baseline.
    O ledger sobrevive ao full refresh das tabelas de ingestão.
    """

    normalized_topic = validate_topic(topic)
    normalized_keys = sorted({str(key).strip() for key in keys})
    if len(normalized_keys) > _OBSERVATION_MAX_KEYS:
        raise InvalidRealtimeEvent(
            f"observação excede {_OBSERVATION_MAX_KEYS} chaves por chamada"
        )
    if any(not key for key in normalized_keys):
        raise InvalidRealtimeEvent("entity_key não pode ser vazia")
    if any(len(key) > _ENTITY_KEY_MAX_LENGTH for key in normalized_keys):
        raise InvalidRealtimeEvent(
            f"entity_key excede {_ENTITY_KEY_MAX_LENGTH} caracteres"
        )

    return await repository.observe_keys(
        topic=normalized_topic,
        keys=normalized_keys,
        baseline_if_empty=baseline_if_empty,
    )


async def observe_active_entity_keys(
    repository: RealtimeRepositoryPort,
    topic: str,
    keys: Iterable[str | int],
    baseline_if_empty: bool = True,
    *,
    allow_empty_snapshot: bool = False,
) -> list[str]:
    """Devolve chaves que entraram (ou reentraram) no conjunto ativo."""

    normalized_topic = validate_topic(topic)
    normalized_keys = sorted({str(key).strip() for key in keys})
    if len(normalized_keys) > _OBSERVATION_MAX_KEYS:
        raise InvalidRealtimeEvent(
            f"observação excede {_OBSERVATION_MAX_KEYS} chaves por chamada"
        )
    if any(not key for key in normalized_keys):
        raise InvalidRealtimeEvent("entity_key não pode ser vazia")
    if any(len(key) > _ENTITY_KEY_MAX_LENGTH for key in normalized_keys):
        raise InvalidRealtimeEvent(
            f"entity_key excede {_ENTITY_KEY_MAX_LENGTH} caracteres"
        )
    return await repository.observe_active_keys(
        topic=normalized_topic,
        keys=normalized_keys,
        baseline_if_empty=baseline_if_empty,
        allow_empty_snapshot=allow_empty_snapshot,
    )


async def observe_active_entity_keys_scoped(
    repository: RealtimeRepositoryPort,
    topic: str,
    keys: Iterable[str | int],
    *,
    scope_prefixes: Iterable[str | int],
) -> list[str]:
    """Reconcilia somente entidades cujo prefixo pertence ao escopo informado.

    O prefixo é a parte exata de ``entity_key`` anterior ao primeiro ``:``.
    Diferentemente de um snapshot completo, esta operação não inicializa o
    baseline global do tópico e nunca desativa entidades de outros escopos.
    """

    normalized_topic = validate_topic(topic)
    normalized_prefixes = sorted({str(prefix).strip() for prefix in scope_prefixes})
    if not normalized_prefixes:
        raise InvalidRealtimeEvent("scope_prefixes não pode ser vazio")
    if len(normalized_prefixes) > _SCOPED_OBSERVATION_MAX_PREFIXES:
        raise InvalidRealtimeEvent(
            "scope_prefixes excede "
            f"{_SCOPED_OBSERVATION_MAX_PREFIXES} itens por chamada"
        )
    if any(not prefix for prefix in normalized_prefixes):
        raise InvalidRealtimeEvent("scope_prefix não pode ser vazio")
    if any(":" in prefix for prefix in normalized_prefixes):
        raise InvalidRealtimeEvent("scope_prefix não pode conter ':'")
    if any(len(prefix) > _SCOPE_PREFIX_MAX_LENGTH for prefix in normalized_prefixes):
        raise InvalidRealtimeEvent(
            f"scope_prefix excede {_SCOPE_PREFIX_MAX_LENGTH} caracteres"
        )

    normalized_keys = sorted({str(key).strip() for key in keys})
    if len(normalized_keys) > _SCOPED_OBSERVATION_MAX_KEYS:
        raise InvalidRealtimeEvent(
            "observação scoped excede "
            f"{_SCOPED_OBSERVATION_MAX_KEYS} chaves por chamada"
        )
    if any(not key for key in normalized_keys):
        raise InvalidRealtimeEvent("entity_key não pode ser vazia")
    if any(len(key) > _ENTITY_KEY_MAX_LENGTH for key in normalized_keys):
        raise InvalidRealtimeEvent(
            f"entity_key excede {_ENTITY_KEY_MAX_LENGTH} caracteres"
        )

    allowed_prefixes = set(normalized_prefixes)
    for key in normalized_keys:
        prefix, separator, suffix = key.partition(":")
        if not separator or not prefix or not suffix:
            raise InvalidRealtimeEvent(
                "entity_key scoped deve seguir o formato '<scope>:<identificador>'"
            )
        if prefix not in allowed_prefixes:
            raise InvalidRealtimeEvent(
                f"entity_key {key!r} não pertence a scope_prefixes"
            )

    return await repository.observe_active_keys_scoped(
        topic=normalized_topic,
        keys=normalized_keys,
        scope_prefixes=normalized_prefixes,
    )


# Nome curto útil em casos de uso; preserva uma única implementação/contrato.
record_event = enqueue_realtime_event
