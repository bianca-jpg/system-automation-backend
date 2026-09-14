"""Ports do bounded context de ingestão.

A aplicação conhece somente estes contratos. Databricks, SQLAlchemy, Redis e
as funções concretas de persistência são ligados no composition root
``app.modules.ingestao.service``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import date
from typing import Protocol


class IngestionSourceReader(Protocol):
    async def pedidos_em_aberto(self) -> list[dict]: ...

    async def estoque(self) -> list[dict]: ...

    async def pedidos_processados_erp(self) -> list[dict]: ...

    async def faturamento_colecao(self) -> list[dict]: ...

    async def referencia_tamanhos(self) -> list[dict]: ...


class SnapshotRepository(Protocol):
    async def acquire_mutation_lock(self) -> bool: ...

    async def count_pedidos(self) -> int: ...

    async def count_estoque(self) -> int: ...

    async def count_processados_erp(self) -> int: ...

    async def count_faturamento(self) -> int: ...

    async def count_referencia_tamanhos(self) -> int: ...

    async def replace_pedidos(self, itens: Iterable[dict]) -> None: ...

    async def replace_estoque(
        self,
        agregado: dict[tuple, int],
        *,
        dt_estoque: date | None,
    ) -> None: ...

    async def replace_processados_erp(self, itens: Iterable[dict]) -> None: ...

    async def replace_faturamento(self, itens: Iterable[dict]) -> None: ...

    async def replace_referencia_tamanhos(self, itens: Iterable[dict]) -> None: ...

    async def rebuild_read_model(self) -> int: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class RealtimeSnapshotPort(Protocol):
    async def load_keys(
        self,
    ) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]: ...

    async def observe_entities(
        self,
        topic: str,
        keys: Iterable[str] | Mapping[str, object],
    ) -> list[str]: ...

    async def observe_active_entities(
        self,
        topic: str,
        keys: Mapping[str, object],
        *,
        allow_empty_snapshot: bool,
    ) -> list[str]: ...

    async def enqueue(
        self,
        *,
        topic: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None: ...


class IngestionJobLock(Protocol):
    def hold(self) -> AbstractAsyncContextManager[bool]: ...


__all__ = [
    "IngestionJobLock",
    "IngestionSourceReader",
    "RealtimeSnapshotPort",
    "SnapshotRepository",
]
