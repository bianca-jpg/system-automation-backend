"""Escrita da bandeja Linx em lotes idempotentes, sem controlar transação."""

from collections import defaultdict
from collections.abc import Iterable, Iterator

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

# Uma linha Linx pode carregar mais de 70 colunas. 250 linhas por statement
# permanecem com folga abaixo do limite de parâmetros do PostgreSQL.
_WRITE_CHUNK_SIZE = 250
_NATURAL_KEY = ("nr_pedido", "cd_prod_cor")
_IMMUTABLE_FIELDS = {"id", "nr_pedido", "cd_prod_cor", "created_at"}


def _chunks[T](values: Iterable[T], size: int | None = None) -> Iterator[list[T]]:
    size = size or _WRITE_CHUNK_SIZE
    chunk: list[T] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


async def salvar_linhas_linx(db: AsyncSession, linhas: list[dict]) -> None:
    """Grava/atualiza linhas por chave natural com comandos em lote.

    Cada `linha` é um dict de colunas do model `OrdemReservaLinx`, contendo ao
    menos `nr_pedido` e `cd_prod_cor`. Linhas com conjuntos de campos diferentes
    são agrupadas antes do INSERT; assim, um campo omitido continua preservado
    no UPDATE em vez de ser convertido implicitamente em NULL.
    """
    from app.modules.pedidos.infrastructure.models import OrdemReservaLinx

    grouped: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for linha in linhas:
        missing = [field for field in _NATURAL_KEY if field not in linha]
        if missing:
            raise ValueError(f"linha Linx sem chave natural: {', '.join(missing)}")
        grouped[tuple(sorted(linha))].append(linha)

    for field_names, values in grouped.items():
        update_fields = [
            field for field in field_names if field not in _IMMUTABLE_FIELDS
        ]
        for chunk in _chunks(values):
            stmt = insert(OrdemReservaLinx).values(chunk)
            if update_fields:
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_ordens_reserva_linx_chave",
                    set_={field: stmt.excluded[field] for field in update_fields},
                )
            else:
                stmt = stmt.on_conflict_do_nothing(
                    constraint="uq_ordens_reserva_linx_chave"
                )
            await db.execute(stmt)
