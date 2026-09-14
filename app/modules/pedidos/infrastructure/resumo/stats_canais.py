"""Bloco SQL gigante de estatísticas por canal + parsing das linhas."""

from __future__ import annotations

from sqlalchemy import text

from app.modules.pedidos.infrastructure.sql.credito import _credito_bloqueado_sql


async def _channel_stats(
    repo,
) -> tuple[dict[str, dict[str, int]], dict[str, int], dict[str, int], dict[str, int]]:
    # O planner materializa `pending_items`/`pending_orders` como CTE Scan (são
    # referenciadas mais de uma vez) e não tem estatística real para elas —
    # por isso subestima drasticamente a cardinalidade e escolhe Nested Loop
    # para o JOIN da CTE `demand`. Com o volume real de pedidos em aberto
    # (~95 mil linhas de pending_items × ~2,5 mil de pending_orders), isso
    # detonou 246 milhões de comparações de linha e ~29s de execução
    # (medido em 2026-09-09, ver EXPLAIN ANALYZE no commit). Forçar Hash Join
    # aqui (mesmo resultado, plano diferente) derruba para menos de 1s.
    # `SET LOCAL` vale só até o fim da transação da requisição — nunca
    # vaza para outras queries nem para outras conexões do pool.
    #
    # `enable_nestloop = off` não proíbe Nested Loop de verdade: soma um
    # custo artificial de 10 bilhões a cada nó desse tipo, só para o planner
    # evitá-lo quando existe alternativa. Esse custo inflado empurra o custo
    # estimado da query inteira para muito acima de `jit_above_cost`
    # (100000), então o Postgres liga a compilação JIT para uma query que na
    # prática roda em milissegundos — a própria compilação (~3,6s) vira o
    # gargalo. Sem benefício aqui (a query não repete o suficiente para
    # amortizar o custo de compilar), então desligamos JIT junto.
    await repo.db.execute(text("SET LOCAL enable_nestloop = off"))
    await repo.db.execute(text("SET LOCAL jit = off"))

    stats_rows = (
        (
            await repo.db.execute(
                text(
                    f"""
                WITH pending_items AS (
                    SELECT p.*
                    FROM pedidos p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM pedidos_processados pp
                        WHERE pp.nr_pedido = p.nr_pedido
                          AND pp.cd_prod_cor = p.cd_prod_cor
                    )
                      AND NOT EXISTS (
                        SELECT 1 FROM pedidos_processados_erp pe
                        WHERE pe.nr_pedido = p.nr_pedido
                          AND pe.cd_prod_cor = p.cd_prod_cor
                    )
                ),
                pending_orders AS (
                    SELECT
                        nr_pedido,
                        CASE WHEN bool_and(
                            upper(coalesce(canal, '')) LIKE 'MULTIMARCA%'
                            OR upper(coalesce(canal, '')) LIKE 'MM%'
                        ) THEN 'Multimarca'
                        WHEN bool_and(
                            upper(coalesce(canal, '')) LIKE 'FRANQUIA%'
                            OR upper(coalesce(canal, '')) LIKE 'FRQ%'
                        ) THEN 'Franquia'
                        ELSE NULL END AS canal,
                        bool_or({_credito_bloqueado_sql("status_credito")})
                            AS blocked_credit
                    FROM pending_items
                    GROUP BY nr_pedido
                ),
                pending_counts AS (
                    SELECT canal,
                           count(*) FILTER (WHERE NOT blocked_credit)::bigint AS liberados,
                           count(*) FILTER (WHERE blocked_credit)::bigint AS blocked_credit
                    FROM pending_orders
                    GROUP BY canal
                ),
                stock AS (
                    SELECT e.cd_prod_cor, upper(e.sg_tamanho) AS sg_tamanho,
                           e.canal, e.qt_disponivel, e.dt_estoque
                    FROM estoque e
                ),
                reservas AS (
                    SELECT
                        o.cd_prod_cor,
                        upper(trim(item ->> 'sg_tamanho')) AS sg_tamanho,
                        CASE
                          WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
                          THEN 'Multimarca'
                          WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
                          THEN 'Franquia'
                          ELSE NULL
                        END AS canal,
                        sum(greatest(coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0), 0)) AS qt
                    FROM ordens_reserva o
                    CROSS JOIN LATERAL jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
                    JOIN stock s
                      ON s.cd_prod_cor = o.cd_prod_cor
                     AND s.sg_tamanho = upper(trim(item ->> 'sg_tamanho'))
                     AND s.canal = CASE
                          WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
                          THEN 'Multimarca'
                          WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
                          THEN 'Franquia'
                          ELSE NULL
                        END
                    WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
                      AND NOT EXISTS (
                        SELECT 1 FROM pedidos_processados_erp pe
                        WHERE pe.nr_pedido = o.nr_pedido
                          AND pe.cd_prod_cor = o.cd_prod_cor
                      )
                      AND (
                        s.dt_estoque IS NULL
                        OR (o.created_at AT TIME ZONE 'America/Sao_Paulo')::date >= s.dt_estoque
                      )
                    GROUP BY 1, 2, 3
                ),
                available_product AS (
                    SELECT s.cd_prod_cor, s.canal,
                           sum(greatest(s.qt_disponivel - coalesce(r.qt, 0), 0))::bigint AS qt
                    FROM stock s
                    LEFT JOIN reservas r
                      ON r.cd_prod_cor = s.cd_prod_cor
                     AND r.sg_tamanho = s.sg_tamanho
                     AND r.canal = s.canal
                    GROUP BY s.cd_prod_cor, s.canal
                ),
                demand AS (
                    SELECT po.canal, p.cd_prod_cor, sum(p.qt_entregar)::bigint AS qty
                    FROM pending_items p
                    JOIN pending_orders po USING (nr_pedido)
                    WHERE NOT po.blocked_credit
                    GROUP BY po.canal, p.cd_prod_cor
                ),
                demand_counts AS (
                    SELECT d.canal,
                           coalesce(sum(d.qty), 0)::bigint AS total_demand,
                           coalesce(sum(greatest(d.qty - coalesce(ap.qt, 0), 0)), 0)::bigint
                               AS blocked_pieces
                    FROM demand d
                    LEFT JOIN available_product ap
                      ON ap.cd_prod_cor = d.cd_prod_cor
                     AND ap.canal = d.canal
                    GROUP BY d.canal
                ),
                or_pairs AS (
                    SELECT
                        o.nr_pedido,
                        o.cd_prod_cor,
                        o.tipo,
                        channel_state.canal
                    FROM ordens_reserva o
                    JOIN LATERAL (
                        SELECT CASE
                          WHEN bool_and(
                            upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
                          ) THEN 'Multimarca'
                          WHEN bool_and(
                            upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
                          ) THEN 'Franquia'
                          ELSE NULL END AS canal
                        FROM jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
                        WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
                          AND length(trim(item ->> 'sg_tamanho')) BETWEEN 1 AND 16
                        HAVING count(*) > 0
                    ) channel_state ON channel_state.canal IS NOT NULL
                    WHERE NOT EXISTS (
                        SELECT 1 FROM pending_items p
                        WHERE p.nr_pedido = o.nr_pedido
                          AND p.cd_prod_cor = o.cd_prod_cor
                    )
                ),
                or_orders AS (
                    SELECT nr_pedido,
                           CASE WHEN bool_and(canal = 'Multimarca') THEN 'Multimarca'
                                WHEN bool_and(canal = 'Franquia') THEN 'Franquia'
                                ELSE NULL END AS canal,
                           bool_or(tipo = 'com') AS com_adequacao
                    FROM or_pairs
                    GROUP BY nr_pedido
                ),
                or_counts AS (
                    SELECT canal,
                           count(*) FILTER (WHERE com_adequacao)::bigint AS or_com,
                           count(*) FILTER (WHERE NOT com_adequacao)::bigint AS or_sem
                    FROM or_orders
                    GROUP BY canal
                ),
                editing_pairs AS (
                    SELECT o.nr_pedido, o.cd_prod_cor, channel_state.canal
                    FROM ordens_reserva o
                    JOIN LATERAL (
                        SELECT CASE
                          WHEN bool_and(
                            upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
                          ) THEN 'Multimarca'
                          WHEN bool_and(
                            upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                            OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
                          ) THEN 'Franquia'
                          ELSE NULL
                        END AS canal
                        FROM jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
                        WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
                          AND length(trim(item ->> 'sg_tamanho')) BETWEEN 1 AND 16
                        HAVING count(*) > 0
                    ) channel_state ON channel_state.canal IS NOT NULL
                    WHERE o.aprovado_em IS NULL
                      AND o.created_at >= now() - interval '24 hours'
                ),
                editing_counts AS (
                    SELECT canal,
                           count(DISTINCT nr_pedido)::bigint AS editing_orders,
                           count(DISTINCT cd_prod_cor)::bigint AS editing_products
                    FROM editing_pairs
                    GROUP BY canal
                ),
                editing_all AS (
                    SELECT count(DISTINCT nr_pedido)::bigint AS editing_orders,
                           count(DISTINCT (cd_prod_cor, canal))::bigint AS editing_products
                    FROM editing_pairs
                )
                SELECT channels.canal,
                       coalesce(pc.liberados, 0) AS liberados,
                       coalesce(pc.blocked_credit, 0) AS blocked_credit,
                       coalesce(oc.or_com, 0) AS or_com,
                       coalesce(oc.or_sem, 0) AS or_sem,
                       coalesce(ec.editing_orders, 0) AS editing_orders,
                       coalesce(ec.editing_products, 0) AS editing_products,
                       editing_all.editing_orders AS editing_orders_all,
                       editing_all.editing_products AS editing_products_all,
                       coalesce(dc.total_demand, 0) AS total_demand,
                       coalesce(dc.blocked_pieces, 0) AS blocked_pieces
                FROM (VALUES ('Franquia'), ('Multimarca')) AS channels(canal)
                LEFT JOIN pending_counts pc USING (canal)
                LEFT JOIN or_counts oc USING (canal)
                LEFT JOIN editing_counts ec USING (canal)
                LEFT JOIN demand_counts dc USING (canal)
                CROSS JOIN editing_all
                ORDER BY channels.canal
                """
                )
            )
        )
        .mappings()
        .all()
    )

    base_stats: dict[str, dict[str, int]] = {
        "Franquia": {},
        "Multimarca": {},
    }
    blocked_pieces = {"Franquia": 0, "Multimarca": 0}
    total_demand = {"Franquia": 0, "Multimarca": 0}
    editing_all = {"orders": 0, "products": 0}
    for row in stats_rows:
        channel = str(row["canal"])
        base_stats[channel] = {
            "liberados": int(row["liberados"]),
            "blocked_credit": int(row["blocked_credit"]),
            "or_com": int(row["or_com"]),
            "or_sem": int(row["or_sem"]),
            "editing_orders": int(row["editing_orders"]),
            "editing_products": int(row["editing_products"]),
        }
        editing_all = {
            "orders": int(row["editing_orders_all"]),
            "products": int(row["editing_products_all"]),
        }
        total_demand[channel] = int(row["total_demand"])
        blocked_pieces[channel] = int(row["blocked_pieces"])

    return base_stats, blocked_pieces, total_demand, editing_all
