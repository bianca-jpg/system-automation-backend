"""Comandos da aplicação independentes dos DTOs HTTP/FastAPI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlteracaoGradeProduto:
    order_id: int
    expected_version: str
    sizes: dict[str, int]


@dataclass(frozen=True, slots=True)
class AlterarGradesProdutoCommand:
    changes: tuple[AlteracaoGradeProduto, ...]


__all__ = ["AlteracaoGradeProduto", "AlterarGradesProdutoCommand"]
