"""Escritas transacionais e bounded no grão produto/canal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class OrdemProdutoState:
    nr_pedido: int
    cd_prod_cor: str
    tipo: str
    itens: list[dict]
    created_at: datetime
    aprovado_em: datetime | None


@dataclass(frozen=True, slots=True)
class ResultadoAprovacaoProduto:
    matched_count: int
    approved_pairs: tuple[tuple[int, str], ...]
    already_approved_count: int
    expired_count: int

    @property
    def approved_count(self) -> int:
        return len(self.approved_pairs)


_CHANNEL_LATERAL_SQL = """
JOIN LATERAL (
    SELECT CASE WHEN bool_and(
        upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
        OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
    ) THEN 'Multimarca'
    WHEN bool_and(
        upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
        OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
    ) THEN 'Franquia'
    ELSE NULL END AS channel
    FROM jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
    WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
      AND length(trim(item ->> 'sg_tamanho')) BETWEEN 1 AND 16
    HAVING count(*) > 0
) channel_state ON true
"""


async def carregar_ordens_produto_para_update(
    db: AsyncSession,
    *,
    cd_prod_cor: str,
    channel: str,
    order_ids: list[int] | None = None,
) -> list[OrdemProdutoState]:
    """Bloqueia ORs em ordem canônica; nunca atravessa o canal solicitado."""
    order_filter = ""
    params: dict[str, object] = {
        "cd_prod_cor": cd_prod_cor,
        "channel": channel,
    }
    if order_ids is not None:
        order_filter = "AND o.nr_pedido = ANY(CAST(:order_ids AS integer[]))"
        params["order_ids"] = order_ids
    rows = (
        (
            await db.execute(
                text(
                    f"""
                SELECT o.nr_pedido, o.cd_prod_cor, o.tipo, o.itens,
                       o.created_at, o.aprovado_em
                FROM ordens_reserva o
                {_CHANNEL_LATERAL_SQL}
                WHERE o.cd_prod_cor = :cd_prod_cor
                  AND channel_state.channel = :channel
                  {order_filter}
                ORDER BY o.nr_pedido ASC, o.cd_prod_cor ASC
                FOR UPDATE OF o
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return [
        OrdemProdutoState(
            nr_pedido=int(row["nr_pedido"]),
            cd_prod_cor=str(row["cd_prod_cor"]),
            tipo=str(row["tipo"]),
            itens=[dict(item) for item in row["itens"]],
            created_at=row["created_at"],
            aprovado_em=row["aprovado_em"],
        )
        for row in rows
    ]


async def aprovar_produto_canal(
    db: AsyncSession,
    *,
    cd_prod_cor: str,
    channel: str,
    limite_criacao: datetime,
) -> ResultadoAprovacaoProduto:
    """Aprova em lote só as ORs abertas do produto/canal já bloqueadas."""
    from app.modules.pedidos.infrastructure.models import OrdemReserva

    states = await carregar_ordens_produto_para_update(
        db,
        cd_prod_cor=cd_prod_cor,
        channel=channel,
    )
    active = [
        state
        for state in states
        if state.aprovado_em is None and state.created_at >= limite_criacao
    ]
    expired_count = sum(
        state.aprovado_em is None and state.created_at < limite_criacao
        for state in states
    )
    already_count = sum(state.aprovado_em is not None for state in states)
    pairs = tuple((state.nr_pedido, state.cd_prod_cor) for state in active)
    if pairs:
        order_ids = [nr for nr, _cd in pairs]
        changed = (
            await db.execute(
                update(OrdemReserva)
                .where(
                    OrdemReserva.cd_prod_cor == cd_prod_cor,
                    OrdemReserva.nr_pedido.in_(order_ids),
                    OrdemReserva.aprovado_em.is_(None),
                    OrdemReserva.created_at >= limite_criacao,
                )
                .values(aprovado_em=func.now())
                .returning(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor)
            )
        ).all()
        pairs = tuple(sorted((int(nr), str(cd)) for nr, cd in changed))
    return ResultadoAprovacaoProduto(
        matched_count=len(states),
        approved_pairs=pairs,
        already_approved_count=already_count,
        expired_count=expired_count,
    )


async def carregar_pares_aprovaveis_pedido_para_update(
    db: AsyncSession,
    *,
    nr_pedido: int,
    cd_prod_cor: str | None,
    limite_criacao: datetime,
) -> tuple[tuple[int, str], ...]:
    """Premarca exatamente os pares que a aprovação legacy pode alterar."""
    from app.modules.pedidos.infrastructure.models import OrdemReserva

    filters = [
        OrdemReserva.nr_pedido == nr_pedido,
        OrdemReserva.aprovado_em.is_(None),
        OrdemReserva.created_at >= limite_criacao,
    ]
    if cd_prod_cor is not None:
        filters.append(OrdemReserva.cd_prod_cor == cd_prod_cor)
    rows = (
        await db.execute(
            select(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor)
            .where(*filters)
            .order_by(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor)
            .with_for_update()
        )
    ).all()
    return tuple((int(nr), str(cd)) for nr, cd in rows)


async def atualizar_grades_em_lote(
    db: AsyncSession,
    updates: list[dict],
) -> tuple[tuple[int, str], ...]:
    """Atualiza até 100 grades em um único UPDATE FROM JSONB."""
    if not updates:
        return ()
    rows = (
        await db.execute(
            text(
                """
                UPDATE ordens_reserva target
                SET itens = source.itens
                FROM jsonb_to_recordset(CAST(:updates AS jsonb))
                    AS source(nr_pedido integer, cd_prod_cor text, itens jsonb)
                WHERE target.nr_pedido = source.nr_pedido
                  AND target.cd_prod_cor = source.cd_prod_cor
                RETURNING target.nr_pedido, target.cd_prod_cor
                """
            ),
            {"updates": json.dumps(updates, ensure_ascii=False)},
        )
    ).all()
    return tuple(sorted((int(nr), str(cd)) for nr, cd in rows))


async def upsert_modificacoes_em_lote(
    db: AsyncSession,
    values: list[dict],
) -> None:
    """Upsert set-based; preserva a primeira grade como original."""
    if not values:
        return
    from app.modules.pedidos.infrastructure.models import PedidoModificacao

    stmt = insert(PedidoModificacao).values(values)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[
                PedidoModificacao.nr_pedido,
                PedidoModificacao.cd_prod_cor,
            ],
            set_={
                "items": stmt.excluded["items"],
                "updated_at": func.now(),
            },
        )
    )


async def totais_produto_em_edicao(
    db: AsyncSession,
    *,
    cd_prod_cor: str,
    channel: str,
    limite_criacao: datetime,
) -> tuple[int, Decimal]:
    """Totais autoritativos de todas as ORs ainda editáveis do produto/canal."""
    row = (
        await db.execute(
            text(
                f"""
                SELECT
                    coalesce(sum(items.qty), 0)::bigint AS qty,
                    coalesce(sum(items.value), 0)::numeric AS value
                FROM ordens_reserva o
                {_CHANNEL_LATERAL_SQL}
                JOIN LATERAL (
                    SELECT
                        sum(greatest(coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0), 0))::bigint AS qty,
                        sum(coalesce(nullif(item ->> 'vl_liquido', '')::numeric, 0))::numeric AS value
                    FROM jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
                    WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
                ) items ON true
                WHERE o.cd_prod_cor = :cd_prod_cor
                  AND channel_state.channel = :channel
                  AND o.aprovado_em IS NULL
                  AND o.created_at >= :limite_criacao
                """
            ),
            {
                "cd_prod_cor": cd_prod_cor,
                "channel": channel,
                "limite_criacao": limite_criacao,
            },
        )
    ).one()
    return int(row.qty or 0), Decimal(row.value or 0)


__all__ = [
    "OrdemProdutoState",
    "ResultadoAprovacaoProduto",
    "aprovar_produto_canal",
    "atualizar_grades_em_lote",
    "carregar_ordens_produto_para_update",
    "carregar_pares_aprovaveis_pedido_para_update",
    "totais_produto_em_edicao",
    "upsert_modificacoes_em_lote",
]
