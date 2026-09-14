"""Política do job durável de full refresh da ingestão."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.shared.jobs.domain import (
    DurableJob,
    NewJob,
    digest_idempotency_key,
    fingerprint_payload,
)

INGESTION_JOB_KIND = "ingestion.full_sync.v1"
INGESTION_IDEMPOTENCY_NAMESPACE = "ingestion.sync"
INGESTION_SCOPE_KEY = "full-refresh"
INGESTION_JOB_MAX_ATTEMPTS = 3

_RESULT_KEYS = (
    "pedidos_inseridos",
    "estoque_chaves",
    "processados_erp",
    "faturamento_colecoes",
    "referencia_tamanho_posicao",
)


def new_sync_job(
    *,
    owner_id: int | None,
    idempotency_key: str | None,
    requested_at: datetime,
    total_timeout_seconds: float,
) -> NewJob:
    namespaced_key = (
        f"{INGESTION_IDEMPOTENCY_NAMESPACE}:{idempotency_key}"
        if idempotency_key is not None
        else None
    )
    return NewJob(
        kind=INGESTION_JOB_KIND,
        owner_id=owner_id,
        scope_key=INGESTION_SCOPE_KEY,
        idempotency_digest=(
            digest_idempotency_key(namespaced_key)
            if namespaced_key is not None
            else None
        ),
        fingerprint=fingerprint_payload(
            {"kind": INGESTION_JOB_KIND, "schema_version": 1}
        ),
        max_attempts=INGESTION_JOB_MAX_ATTEMPTS,
        requested_at=requested_at,
        deadline_at=requested_at + timedelta(seconds=total_timeout_seconds),
    )


def sanitized_result(result: dict[str, object]) -> dict[str, int]:
    sanitized: dict[str, int] = {}
    for key in _RESULT_KEYS:
        value = result.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"resultado interno inválido para {key}")
        sanitized[key] = value
    return sanitized


def public_result(job: DurableJob) -> dict[str, int] | None:
    if job.result is None:
        return None
    return sanitized_result(dict(job.result))


__all__ = [
    "INGESTION_IDEMPOTENCY_NAMESPACE",
    "INGESTION_JOB_KIND",
    "INGESTION_JOB_MAX_ATTEMPTS",
    "INGESTION_SCOPE_KEY",
    "new_sync_job",
    "public_result",
    "sanitized_result",
]
