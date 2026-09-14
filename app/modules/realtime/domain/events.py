"""Contrato de domínio dos eventos realtime.

O envelope é deliberadamente pequeno, versionado e independente do transporte.
O mesmo formato é persistido no outbox, publicado no Redis Stream e entregue no
WebSocket. Consumidores devem deduplicar por ``eventId`` (entrega at-least-once).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

_TOPIC_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


class InvalidRealtimeEvent(ValueError):
    """Evento fora do contrato público do realtime."""


def validate_topic(topic: str) -> str:
    normalized = topic.strip()
    if not _TOPIC_RE.fullmatch(normalized):
        raise InvalidRealtimeEvent(
            "topic deve ter 1..64 caracteres minúsculos (a-z, 0-9, '.', '_' ou '-')"
        )
    return normalized


def validate_event_type(event_type: str) -> str:
    normalized = event_type.strip()
    if not _EVENT_TYPE_RE.fullmatch(normalized):
        raise InvalidRealtimeEvent(
            "event_type deve ter 1..128 caracteres minúsculos (a-z, 0-9, '.', '_' ou '-')"
        )
    return normalized


def normalize_payload(payload: Mapping[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Copia e valida um payload JSON bounded antes de tocar no banco."""

    try:
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidRealtimeEvent("payload deve ser um objeto JSON válido") from exc

    if len(encoded) > max_bytes:
        raise InvalidRealtimeEvent(f"payload excede o limite de {max_bytes} bytes")
    return json.loads(encoded)


@dataclass(frozen=True, slots=True)
class RealtimeEventEnvelope:
    event_id: UUID
    sequence: int
    event_type: str
    topic: str
    occurred_at: datetime
    payload: dict[str, Any]
    version: int = 1

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise InvalidRealtimeEvent("sequence deve ser positiva")
        if self.version != 1:
            raise InvalidRealtimeEvent("versão de envelope não suportada")
        validate_topic(self.topic)
        validate_event_type(self.event_type)
        if self.occurred_at.tzinfo is None:
            raise InvalidRealtimeEvent("occurred_at deve conter timezone")

    def as_dict(self) -> dict[str, Any]:
        occurred_at = self.occurred_at.astimezone(UTC)
        return {
            "version": self.version,
            "eventId": str(self.event_id),
            "sequence": self.sequence,
            "type": self.event_type,
            "topic": self.topic,
            "occurredAt": occurred_at.isoformat().replace("+00:00", "Z"),
            "payload": self.payload,
        }
