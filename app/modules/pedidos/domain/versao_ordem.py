"""Versão opaca e determinística do estado editável de uma OR."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def calcular_versao_ordem(
    *,
    tipo: str,
    created_at: datetime,
    aprovado_em: datetime | None,
    itens: list | dict,
) -> str:
    """Hash SHA-256 usado em leitura e escrita para impedir lost update."""
    canonical = json.dumps(
        {
            "aprovadoEm": _iso_utc(aprovado_em),
            "createdAt": _iso_utc(created_at),
            "itens": itens,
            "tipo": tipo,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["calcular_versao_ordem"]
