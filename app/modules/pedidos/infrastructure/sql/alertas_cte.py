"""CTE de alertas e suas duas variantes com escopo (por produto e por pedidos)."""

from __future__ import annotations

from app.modules.pedidos.infrastructure.sql.credito import _credito_bloqueado_sql

# Fonte única da classificação de alertas. O endpoint paginado e o snapshot
# usado pelo realtime projetam esta mesma relação para não haver duas regras.
_ALERTS_CTE = f"""
pending_items AS (
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
stock AS (
    SELECT e.cd_prod_cor, upper(e.sg_tamanho) AS sg_tamanho,
           e.canal, e.qt_disponivel, e.dt_estoque
    FROM estoque e
),
reservas AS (
    SELECT o.cd_prod_cor,
           upper(trim(item ->> 'sg_tamanho')) AS sg_tamanho,
           CASE
             WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
               OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
             THEN 'Multimarca'
             WHEN upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
               OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
             THEN 'Franquia' ELSE NULL
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
         THEN 'Franquia' ELSE NULL END
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
products AS (
    SELECT
        p.nr_pedido, p.cd_prod_cor,
        coalesce(
            (array_agg(nullif(p.client, '') ORDER BY p.id)
                FILTER (WHERE nullif(p.client, '') IS NOT NULL))[1],
            'Cliente ' || p.nr_pedido::text
        ) AS client,
        CASE WHEN bool_and(
            upper(coalesce(p.canal, '')) LIKE 'MULTIMARCA%'
            OR upper(coalesce(p.canal, '')) LIKE 'MM%'
        ) THEN 'Multimarca'
        WHEN bool_and(
            upper(coalesce(p.canal, '')) LIKE 'FRANQUIA%'
            OR upper(coalesce(p.canal, '')) LIKE 'FRQ%'
        ) THEN 'Franquia' ELSE NULL END AS canal,
        coalesce(max(nullif(p.data, '')), '') AS order_key,
        sum(p.qt_entregar)::bigint AS demand,
        bool_or({_credito_bloqueado_sql("p.status_credito")}) AS sem_credito,
        bool_or(
            lower(coalesce(p.cd_prod_cor, '')) LIKE :search ESCAPE '\\'
            OR lower(coalesce(p.ds_produto, '')) LIKE :search ESCAPE '\\'
        ) AS item_matches
    FROM pending_items p
    GROUP BY p.nr_pedido, p.cd_prod_cor
),
orders AS (
    SELECT
        p.nr_pedido, max(p.client) AS client,
        CASE WHEN bool_and(p.canal = 'Multimarca') THEN 'Multimarca'
             WHEN bool_and(p.canal = 'Franquia') THEN 'Franquia'
             ELSE NULL END AS canal,
        max(p.order_key) AS order_key,
        bool_or(p.sem_credito) AS sem_credito,
        bool_or(p.demand > coalesce(ap.qt, 0)) AS sem_estoque,
        bool_or(p.item_matches) AS item_matches
    FROM products p
    LEFT JOIN available_product ap
      ON ap.cd_prod_cor = p.cd_prod_cor
     AND ap.canal = p.canal
    WHERE p.canal IS NOT NULL
    GROUP BY p.nr_pedido
),
alerts AS (
    SELECT
        nr_pedido, client, canal, order_key,
        CASE WHEN sem_credito THEN 'error' ELSE 'warning' END AS alert_type
    FROM orders
    WHERE canal IS NOT NULL
      AND (sem_credito OR sem_estoque)
      AND (:channel = 'Todos' OR canal = :channel)
      AND (
        :search_empty
        OR nr_pedido::text LIKE :search ESCAPE '\\'
        OR lower(client) LIKE :search ESCAPE '\\'
        OR item_matches
      )
)
"""


def _limitar_estoque_alertas_ao_escopo(scoped: str) -> str:
    return scoped.replace(
        "    FROM estoque e\n),",
        "    FROM estoque e\n"
        "    WHERE EXISTS (\n"
        "        SELECT 1 FROM pending_items p\n"
        "        WHERE p.cd_prod_cor = e.cd_prod_cor\n"
        "          AND CASE\n"
        "              WHEN upper(coalesce(p.canal, '')) LIKE 'MULTIMARCA%'\n"
        "                OR upper(coalesce(p.canal, '')) LIKE 'MM%'\n"
        "              THEN 'Multimarca'\n"
        "              WHEN upper(coalesce(p.canal, '')) LIKE 'FRANQUIA%'\n"
        "                OR upper(coalesce(p.canal, '')) LIKE 'FRQ%'\n"
        "              THEN 'Franquia' ELSE NULL\n"
        "          END = e.canal\n"
        "    )\n),",
        1,
    )


def _alerts_cte_produto() -> str:
    """Restringe o snapshot aos pedidos afetados por um produto/canal.

    Os demais produtos desses pedidos continuam na classificação para preservar
    a precedência do alerta de crédito sobre estoque. Foto e reservas ficam
    limitadas a esse conjunto, sem varrer o catálogo inteiro em um PUT de grade.
    """
    affected = """
affected_orders AS (
    SELECT DISTINCT p.nr_pedido
    FROM pedidos p
    WHERE p.cd_prod_cor = :scope_product
      AND CASE
          WHEN upper(coalesce(p.canal, '')) LIKE 'MULTIMARCA%'
            OR upper(coalesce(p.canal, '')) LIKE 'MM%'
          THEN 'Multimarca'
          WHEN upper(coalesce(p.canal, '')) LIKE 'FRANQUIA%'
            OR upper(coalesce(p.canal, '')) LIKE 'FRQ%'
          THEN 'Franquia' ELSE NULL
      END = :scope_channel
      AND NOT EXISTS (
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
"""
    scoped = _ALERTS_CTE.replace(
        "pending_items AS (", affected + "pending_items AS (", 1
    )
    scoped = scoped.replace(
        "    WHERE NOT EXISTS (",
        "    WHERE p.nr_pedido IN (SELECT nr_pedido FROM affected_orders)\n"
        "      AND NOT EXISTS (",
        1,
    )
    return _limitar_estoque_alertas_ao_escopo(scoped)


def _alerts_cte_pedidos() -> str:
    """Restringe a foto aos IDs fornecidos, mantendo todos os seus produtos."""

    affected = """
affected_orders AS (
    SELECT unnest(CAST(:scope_orders AS integer[])) AS nr_pedido
),
"""
    scoped = _ALERTS_CTE.replace(
        "pending_items AS (", affected + "pending_items AS (", 1
    )
    scoped = scoped.replace(
        "    WHERE NOT EXISTS (",
        "    WHERE p.nr_pedido IN (SELECT nr_pedido FROM affected_orders)\n"
        "      AND NOT EXISTS (",
        1,
    )
    return _limitar_estoque_alertas_ao_escopo(scoped)
