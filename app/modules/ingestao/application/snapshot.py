"""DTOs preparados entre a coleta externa e o swap PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.modules.ingestao.domain.agregacao import EstoqueDiagnostico


@dataclass(frozen=True, slots=True)
class PedidosPreparados:
    itens: tuple[dict, ...]
    linhas_brutas: int
    descartadas: int
    substituir: bool


@dataclass(frozen=True, slots=True)
class EstoquePreparado:
    agregado: dict[tuple, int]
    linhas_brutas: int
    diagnostico: EstoqueDiagnostico
    dt_foto: date | None
    substituir: bool


@dataclass(frozen=True, slots=True)
class ProcessadosErpPreparados:
    itens: tuple[dict, ...]
    linhas_brutas: int
    descartadas: int
    substituir: bool


@dataclass(frozen=True, slots=True)
class FaturamentoPreparado:
    itens: tuple[dict, ...]
    linhas_brutas: int
    substituir: bool


@dataclass(frozen=True, slots=True)
class ReferenciaTamanhosPreparada:
    itens: tuple[dict, ...]
    linhas_brutas: int
    descartadas: int
    conflitos_por_produto: dict[str, list[str]]
    substituir: bool


@dataclass(frozen=True, slots=True)
class SnapshotPreparado:
    pedidos: PedidosPreparados
    estoque: EstoquePreparado
    processados_erp: ProcessadosErpPreparados
    faturamento: FaturamentoPreparado
    referencia: ReferenciaTamanhosPreparada


__all__ = [
    "EstoquePreparado",
    "FaturamentoPreparado",
    "PedidosPreparados",
    "ProcessadosErpPreparados",
    "ReferenciaTamanhosPreparada",
    "SnapshotPreparado",
]
