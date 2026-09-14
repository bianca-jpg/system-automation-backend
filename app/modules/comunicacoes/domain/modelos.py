"""Tipos e regras puras do bounded context de comunicações."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class StatusComunicacao(StrEnum):
    PENDENTE = "Pendente"
    ENVIADO = "Enviado"
    FALHOU = "Falhou"
    INCERTO = "Incerto"


@dataclass(frozen=True, slots=True)
class Comunicacao:
    id: str
    type: str
    status: str
    time: str
    content: str
    recipient: str
    created_at: datetime
    requested_by_user_id: int | None = None
    idempotency_key: str | None = None
    idempotency_fingerprint: str | None = None
    attempt_count: int | None = None
    max_attempts: int | None = None
    updated_at: datetime | None = None
    sent_at: datetime | None = None
    next_attempt_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EntregaEmail:
    id: str
    recipient: str
    subject: str
    content: str
    message_id: str
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class PoliticaEntrega:
    max_attempts: int = 5
    lease_seconds: int = 120
    retry_base_seconds: int = 30
    retry_max_seconds: int = 1_800
    batch_size: int = 20

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts deve estar entre 1 e 20")
        if not 30 <= self.lease_seconds <= 3_600:
            raise ValueError("lease_seconds deve estar entre 30 e 3600")
        if not 1 <= self.retry_base_seconds <= self.retry_max_seconds:
            raise ValueError("retry_base_seconds inválido")
        if not self.retry_max_seconds <= 86_400:
            raise ValueError("retry_max_seconds deve ser no máximo 86400")
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size deve estar entre 1 e 100")

    def proxima_tentativa(
        self,
        *,
        delivery_id: str,
        attempt_count: int,
        now: datetime,
    ) -> datetime:
        """Backoff exponencial limitado com jitter determinístico de ±20%."""

        exponent = max(attempt_count - 1, 0)
        base_delay = min(
            self.retry_max_seconds,
            self.retry_base_seconds * (2**exponent),
        )
        digest = hashlib.sha256(f"{delivery_id}:{attempt_count}".encode()).digest()
        jitter = 0.8 + (int.from_bytes(digest[:2], "big") / 65_535) * 0.4
        delay_seconds = min(
            self.retry_max_seconds,
            max(1, round(base_delay * jitter)),
        )
        return now.astimezone(UTC) + timedelta(seconds=delay_seconds)


def message_id_deterministico(communication_id: str) -> str:
    """Identificador RFC 5322 estável entre todas as tentativas SMTP."""

    digest = hashlib.sha256(
        f"system-automation:communication:{communication_id}".encode()
    ).hexdigest()
    return f"<{digest}@system-automation.project.com.br>"
