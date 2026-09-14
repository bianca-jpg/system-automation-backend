"""Helpers de paginação por cursor, epoch e ordenação de tamanhos."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text


def _cursor_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _cursor_parameter(value: object, postgres_type: str) -> object:
    """Restaura o tipo PostgreSQL perdido ao serializar o cursor em JSON."""
    if value is None:
        return None
    if postgres_type == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if postgres_type == "timestamptz" and isinstance(value, str):
        return datetime.fromisoformat(value)
    if postgres_type == "numeric" and not isinstance(value, Decimal):
        return Decimal(str(value))
    if postgres_type == "bigint" and not isinstance(value, int):
        assert isinstance(value, (str, float))
        return int(value)
    return value


def _epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _size_sort_key(size: str, position: int | None) -> tuple:
    if position is not None:
        return (0, position, size)
    try:
        return (1, int(size), size)
    except ValueError:
        return (2, size)


async def _size_positions(db, codes: list[str]) -> dict[tuple[str, str], int]:
    if not codes:
        return {}
    rows = (
        await db.execute(
            text(
                """
                SELECT cd_prod_cor, upper(trim(sg_tamanho)), nr_posicao
                FROM produto_tamanho_posicao
                WHERE cd_prod_cor = ANY(CAST(:codes AS varchar[]))
                """
            ),
            {"codes": codes},
        )
    ).all()
    return {(str(code), str(size)): int(position) for code, size, position in rows}
