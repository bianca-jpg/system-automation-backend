"""Chunking genérico compartilhado pelos adapters de leitura e escrita do processing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def _chunks[T](values: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]
