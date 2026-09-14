"""Repositório das tabelas próprias do módulo pedidos: PedidoProcessado
e OrdemReserva.

Todas operam no grão canônico `(nr_pedido, cd_prod_cor)` — ver models.py. Nenhuma
faz commit: quem decide quando commitar é o caso de uso.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

Par = tuple[int, str]

# Mantém cada comando bem abaixo do limite de parâmetros do PostgreSQL. O
# maior payload deste arquivo (`OrdemReserva.itens`) também fica pequeno o
# bastante para não monopolizar o event loop em uma única chamada.
_WRITE_CHUNK_SIZE = 250


class _OrdemReservaResult(Protocol):
    nr_pedido: int
    cd_prod_cor: str
    tipo: str
    itens: list[Any]
    created_at: datetime
    aprovado_em: datetime | None


@dataclass(frozen=True, slots=True)
class ResultadoAprovacao:
    encontrados: int
    alterados: int
    expirados: int
    ja_aprovados: int

    @property
    def exists(self) -> bool:
        return self.encontrados > 0

    @property
    def changed(self) -> bool:
        return self.alterados > 0

    @property
    def expired(self) -> bool:
        return self.expirados > 0


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


async def salvar_processados(db: AsyncSession, pares: set[Par]) -> None:
    """Marca pares como processados em lotes idempotentes, sem pré-leitura."""
    from app.modules.pedidos.infrastructure.models import PedidoProcessado

    values = ({"nr_pedido": nr, "cd_prod_cor": cd} for nr, cd in sorted(pares))
    for chunk in _chunks(values):
        stmt = (
            insert(PedidoProcessado)
            .values(chunk)
            .on_conflict_do_nothing(
                index_elements=[
                    PedidoProcessado.nr_pedido,
                    PedidoProcessado.cd_prod_cor,
                ]
            )
        )
        await db.execute(stmt)


async def carregar_ordens_reserva(
    db: AsyncSession,
) -> list[tuple[int, str, str, list, datetime, datetime | None]]:
    """Retorna (nr_pedido, cd_prod_cor, tipo, itens, created_at, aprovado_em) de
    cada OR, em ordem determinística — a ordem afeta a montagem a jusante.
    """
    from app.modules.pedidos.infrastructure.models import OrdemReserva

    rows = cast(
        Sequence[_OrdemReservaResult],
        (
            (
                await db.execute(
                    select(OrdemReserva).order_by(
                        OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor
                    )
                )
            )
            .scalars()
            .all()
        ),
    )
    return [
        (r.nr_pedido, r.cd_prod_cor, r.tipo, r.itens, r.created_at, r.aprovado_em)
        for r in rows
    ]


async def estado_ordens_reserva(db: AsyncSession) -> tuple[datetime | None, int]:
    """(created_at mais recente, nº de ORs) — barato, sem carregar as grades.

    Usado para detectar que uma projeção derivada das ORs (o estoque virtual)
    ficou velha porque alguma OR nasceu depois dela.
    """
    from app.modules.pedidos.infrastructure.models import OrdemReserva

    row = (
        await db.execute(
            select(func.max(OrdemReserva.created_at), func.count()).select_from(
                OrdemReserva
            )
        )
    ).one()
    return row[0], row[1] or 0


async def aprovar_ordem_reserva(
    db: AsyncSession,
    nr_pedido: int,
    cd_prod_cor: str | None = None,
    *,
    limite_criacao: datetime,
) -> ResultadoAprovacao:
    """Marca OR(s) como aprovadas manualmente (aprovado_em = now).

    `cd_prod_cor` informado aprova apenas aquele par; omitido aprova todos os
    produtos daquele pedido (é o que a tela faz ao aprovar a linha de um pedido).
    Só altera OR ainda aberta (`created_at >= limite_criacao`) e nunca
    sobrescreve a primeira aprovação. O resultado distingue ausência, mudança,
    expiração e replay para o caso de uso decidir HTTP/eventos sem ambiguidade.
    """
    from app.modules.pedidos.infrastructure.models import OrdemReserva

    filtro = [OrdemReserva.nr_pedido == nr_pedido]
    if cd_prod_cor is not None:
        filtro.append(OrdemReserva.cd_prod_cor == cd_prod_cor)

    rows = (
        await db.execute(
            select(OrdemReserva.created_at, OrdemReserva.aprovado_em).where(*filtro)
        )
    ).all()
    encontrados = len(rows)
    if not encontrados:
        return ResultadoAprovacao(0, 0, 0, 0)

    expirados = sum(
        aprovado_em is None and created_at < limite_criacao
        for created_at, aprovado_em in rows
    )
    ja_aprovados = sum(aprovado_em is not None for _, aprovado_em in rows)
    alterados = len(
        (
            await db.execute(
                update(OrdemReserva)
                .where(
                    *filtro,
                    OrdemReserva.aprovado_em.is_(None),
                    OrdemReserva.created_at >= limite_criacao,
                )
                .values(aprovado_em=func.now())
                .returning(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor)
            )
        ).all()
    )
    return ResultadoAprovacao(
        encontrados=encontrados,
        alterados=alterados,
        expirados=expirados,
        ja_aprovados=ja_aprovados,
    )


async def salvar_ordens_reserva(db: AsyncSession, resultados: dict, tipo: str) -> None:
    """Grava uma OR nova por par (nr_pedido, cd_prod_cor).

    `resultados` é { (nr_pedido, cd_prod_cor): [itens da grade] } — a saída do
    motor. `itens` é a grade de tamanhos daquele produto para aquele cliente.
    Uma OR existente é fato imutável neste fluxo e não é recalculada; edição de
    grade usa `repositorio_produto_writes.atualizar_grades_em_lote` após
    validação e lock do caso.
    """
    from app.modules.pedidos.infrastructure.models import OrdemReserva

    values = (
        {"nr_pedido": nr, "cd_prod_cor": cd, "tipo": tipo, "itens": itens}
        for (nr, cd), itens in sorted(resultados.items())
    )
    for chunk in _chunks(values):
        stmt = insert(OrdemReserva).values(chunk)
        await db.execute(
            stmt.on_conflict_do_nothing(
                index_elements=[OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor],
            )
        )
