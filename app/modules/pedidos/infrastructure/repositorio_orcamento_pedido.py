"""Fonte única da agregação de orçamento ±5% por pedido (Phase 20, GRADE-03).

Corrige o Pitfall 1 da pesquisa: `_PEDIDO_BUDGET_SQL` (antes só em
`processing/infrastructure/adapters.py`) filtrava `op.tipo = 'com'` no CTE
`consumido`, então ORs "sem adequação" nunca apareciam no orçamento do
pedido — o restante voltaria sempre cheio e o operador poderia estourar o
limite de ±5% em edições sucessivas sem nunca ser barrado.

A correção NÃO é remover o filtro de tipo (ver PD-01 no plano 20-01): o
ramo `'com'` mede o desvio POR ITEM (por tamanho), que é a semântica certa
para o motor automático mas trataria redistribuição pura de "sem adequação"
como consumo. Este módulo mantém o ramo `'com'` byte-idêntico e adiciona um
ramo novo, `'sem'`, que mede o desvio pelo TOTAL do par
(`nr_pedido`, `cd_prod_cor`): agrega `qt_liquida`/`qt_solicitada` por par
primeiro, só então aplica `GREATEST(...)`. Um par que só moveu peças entre
tamanhos tem as duas somas iguais e contribui zero.

Este é o único módulo com a SQL de orçamento por pedido — o anti-padrão que
a pesquisa apontou é duas queries (motor vs. edição manual) divergindo em
silêncio. `PEDIDO_BUDGET_SQL` é consumida por dois formatos de saída:

- `carregar_orcamento_pedidos` — a interface nova, por `OrcamentoPedidoSnapshot`
  (D-05, consultável sob demanda).
- `carregar_pedido_budget_tuplas` — a forma legada de três dicionários
  paralelos, implementada em cima da primeira, só para
  `SqlAlchemyProcessingPlannerSource.load_pedido_budget` manter sua
  assinatura pública sem obrigar o motor a mudar.

Nenhuma coluna nem tabela nova: D-05 é explícito que o consumido é calculado
ao vivo a partir de `ordens_reserva.itens`.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos.domain.orcamento_edicao import OrcamentoPedidoSnapshot

__all__ = [
    "PEDIDO_BUDGET_SQL",
    "carregar_orcamento_pedidos",
    "carregar_pedido_budget_tuplas",
]

_CHUNK_SIZE = 5_000

PEDIDO_BUDGET_SQL = """
WITH pedido_scope AS (
    SELECT unnest(CAST(:nr_pedidos AS integer[])) AS nr_pedido
),
or_pairs AS (
    SELECT
        o.nr_pedido,
        o.cd_prod_cor,
        o.tipo,
        coalesce(o.itens, '[]'::jsonb) AS itens
    FROM ordens_reserva o
    JOIN pedido_scope USING (nr_pedido)
),
from_pedidos AS (
    SELECT p.nr_pedido, sum(p.qt_entregar)::bigint AS qty
    FROM pedidos p
    JOIN pedido_scope USING (nr_pedido)
    GROUP BY p.nr_pedido
),
missing_from_pedidos AS (
    SELECT
        op.nr_pedido,
        sum(
            coalesce(nullif(item ->> 'qt_solicitada', '')::integer, 0)
        )::bigint AS qty
    FROM or_pairs op
    CROSS JOIN LATERAL jsonb_array_elements(op.itens) AS item
    WHERE NOT EXISTS (
        SELECT 1 FROM pedidos p
        WHERE p.nr_pedido = op.nr_pedido
          AND p.cd_prod_cor = op.cd_prod_cor
    )
    GROUP BY op.nr_pedido
),
consumido_por_item AS (
    -- Ramo do motor automático ('com adequação') — medido POR ITEM (por
    -- tamanho), exatamente como antes desta fase (D-01). Byte-idêntico.
    SELECT
        op.nr_pedido,
        sum(
            GREATEST(
                coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0)
                - coalesce(nullif(item ->> 'qt_solicitada', '')::integer, 0),
                0
            )
        )::bigint AS adicao,
        sum(
            GREATEST(
                coalesce(nullif(item ->> 'qt_solicitada', '')::integer, 0)
                - coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0),
                0
            )
        )::bigint AS corte
    FROM or_pairs op
    CROSS JOIN LATERAL jsonb_array_elements(op.itens) AS item
    WHERE op.tipo = 'com'
    GROUP BY op.nr_pedido
),
par_sem_totais AS (
    -- Soma por PAR primeiro (nr_pedido, cd_prod_cor) — pré-requisito para
    -- medir pelo total do par em vez de por tamanho (PD-01).
    SELECT
        op.nr_pedido,
        op.cd_prod_cor,
        sum(
            coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0)
        )::bigint AS soma_liquida,
        sum(
            coalesce(nullif(item ->> 'qt_solicitada', '')::integer, 0)
        )::bigint AS soma_solicitada
    FROM or_pairs op
    CROSS JOIN LATERAL jsonb_array_elements(op.itens) AS item
    WHERE op.tipo = 'sem'
    GROUP BY op.nr_pedido, op.cd_prod_cor
),
consumido_por_total_par AS (
    -- Ramo novo, 'sem adequação' — medido PELO TOTAL do par. Redistribuir
    -- tamanhos sem mudar o total (permitido hoje) contribui zero: as duas
    -- somas do par ficam iguais e GREATEST(...) zera nos dois lados.
    SELECT
        nr_pedido,
        sum(GREATEST(soma_liquida - soma_solicitada, 0))::bigint AS adicao,
        sum(GREATEST(soma_solicitada - soma_liquida, 0))::bigint AS corte
    FROM par_sem_totais
    GROUP BY nr_pedido
),
consumido AS (
    -- União dos dois ramos por pedido: um pedido com par 'com' e par 'sem'
    -- soma as duas contribuições, e nenhum pedido duplica linha aqui porque
    -- o GROUP BY final é sobre a união, não sobre um segundo JOIN.
    SELECT
        nr_pedido,
        sum(adicao)::bigint AS adicao,
        sum(corte)::bigint AS corte
    FROM (
        SELECT nr_pedido, adicao, corte FROM consumido_por_item
        UNION ALL
        SELECT nr_pedido, adicao, corte FROM consumido_por_total_par
    ) uniao_com_e_sem
    GROUP BY nr_pedido
)
SELECT
    ps.nr_pedido,
    coalesce(from_pedidos.qty, 0) + coalesce(missing_from_pedidos.qty, 0)
        AS total_original,
    coalesce(consumido.adicao, 0) AS consumido_adicao,
    coalesce(consumido.corte, 0) AS consumido_corte
FROM pedido_scope ps
LEFT JOIN from_pedidos USING (nr_pedido)
LEFT JOIN missing_from_pedidos USING (nr_pedido)
LEFT JOIN consumido USING (nr_pedido)
"""


async def carregar_orcamento_pedidos(
    db: AsyncSession, nr_pedidos: set[int]
) -> dict[int, OrcamentoPedidoSnapshot]:
    """Orçamento ao vivo por pedido, indexado por `nr_pedido` (D-05:
    consultável sob demanda, sem coluna nem tabela nova). Consumido de
    adição/corte soma os dois ramos (`'com'` por item, `'sem'` por total do
    par); total_original inclui a correção do denominador que encolhe
    (invariante J5, `missing_from_pedidos`)."""

    snapshots: dict[int, OrcamentoPedidoSnapshot] = {}
    pedidos_ordenados = sorted(nr_pedidos)
    for offset in range(0, len(pedidos_ordenados), _CHUNK_SIZE):
        chunk = pedidos_ordenados[offset : offset + _CHUNK_SIZE]
        rows = (
            (
                await db.execute(
                    text(PEDIDO_BUDGET_SQL),
                    {"nr_pedidos": chunk},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            nr_pedido = int(row["nr_pedido"])
            snapshots[nr_pedido] = OrcamentoPedidoSnapshot(
                nr_pedido=nr_pedido,
                total_original=int(row["total_original"] or 0),
                consumido_adicao=int(row["consumido_adicao"] or 0),
                consumido_corte=int(row["consumido_corte"] or 0),
            )
    return snapshots


async def carregar_pedido_budget_tuplas(
    db: AsyncSession, nr_pedidos: set[int]
) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    """Forma legada de três dicionários paralelos (total_original,
    consumido_adicao, consumido_corte), existente só para
    `adapters.py::load_pedido_budget` manter sua assinatura pública
    inalterada. Implementada em termos de `carregar_orcamento_pedidos` —
    uma travessia só da SQL, não duas."""

    snapshots = await carregar_orcamento_pedidos(db, nr_pedidos)
    total_original = {
        nr_pedido: snapshot.total_original
        for nr_pedido, snapshot in snapshots.items()
    }
    consumido_adicao = {
        nr_pedido: snapshot.consumido_adicao
        for nr_pedido, snapshot in snapshots.items()
    }
    consumido_corte = {
        nr_pedido: snapshot.consumido_corte
        for nr_pedido, snapshot in snapshots.items()
    }
    return total_original, consumido_adicao, consumido_corte
