"""Helpers de CTE de estoque disponível e de pares elegíveis, compartilhados pelas projeções de produto."""

from __future__ import annotations

_STATUS_SQL = {
    "Todos": None,
    "Com Adequação": "OR com adequação",
    "Sem Adequação": "OR sem adequação",
    "Bloqueado Estoque": "Bloqueado Estoque",
    "Bloqueado Crédito": "Bloqueado sem crédito",
}

_AVAILABLE_STOCK_CTE = """
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
        sum(greatest(coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0), 0)) AS qty
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
          ELSE NULL END
    WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
      AND NOT EXISTS (
          SELECT 1 FROM pedido_produto_read erp
          WHERE erp.source = 'erp'
            AND erp.nr_pedido = o.nr_pedido
            AND erp.cd_prod_cor = o.cd_prod_cor
      )
      AND (
          s.dt_estoque IS NULL
          OR (o.created_at AT TIME ZONE 'America/Sao_Paulo')::date >= s.dt_estoque
      )
    GROUP BY 1, 2, 3
),
available_product AS (
    SELECT s.cd_prod_cor, s.canal,
           sum(greatest(s.qt_disponivel - coalesce(r.qty, 0), 0))::bigint AS qty
    FROM stock s
    LEFT JOIN reservas r
      ON r.cd_prod_cor = s.cd_prod_cor
     AND r.sg_tamanho = s.sg_tamanho
     AND r.canal = s.canal
    GROUP BY s.cd_prod_cor, s.canal
)
"""


def _available_stock_cte(*, scoped_product: bool) -> str:
    if not scoped_product:
        return _AVAILABLE_STOCK_CTE
    return _AVAILABLE_STOCK_CTE.replace(
        "    FROM estoque e\n),",
        "    FROM estoque e\n"
        "    WHERE e.cd_prod_cor = :code AND e.canal = :channel\n),",
        1,
    )


def _or_pairs_sql(where_sql: str) -> str:
    return f"""
    SELECT
        o.nr_pedido,
        o.cd_prod_cor AS code,
        d.client,
        d.channel,
        d.order_date,
        d.order_date_raw,
        d.name,
        d.qty,
        d.value,
        d.original_value,
        d.sizes,
        o.created_at AS processed_at,
        coalesce(o.aprovado_em, o.created_at + interval '24 hours') AS approved_at,
        o.tipo = 'com' AS adequacao,
        o.aprovado_em IS NOT NULL OR o.created_at < now() - interval '24 hours'
            AS aprovado,
        CASE WHEN o.tipo = 'com' THEN 'OR com adequação' ELSE 'OR sem adequação' END
            AS status,
        CASE WHEN o.tipo = 'com' THEN 'Faturado or com adequação'
             ELSE 'Faturado or sem adequação' END AS motivo,
        'or'::text AS source,
        CASE WHEN erp.nr_pedido IS NULL THEN 'app' ELSE 'app_erp' END AS origem,
        erp.nr_pedido IS NOT NULL AS confirmado_erp,
        o.tipo AS version_tipo,
        o.created_at AS version_created_at,
        o.aprovado_em AS version_approved_at,
        o.itens AS version_items
    FROM ordens_reserva o
    JOIN LATERAL (
        SELECT
            coalesce(max(nullif(item ->> 'client', '')), 'Cliente ' || o.nr_pedido::text)
                AS client,
            CASE WHEN bool_and(
                upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
            ) THEN 'Multimarca'
            WHEN bool_and(
                upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
            ) THEN 'Franquia'
            ELSE NULL END AS channel,
            max(
                CASE
                  WHEN pg_input_is_valid(substring(item ->> 'data', 1, 10), 'date')
                  THEN substring(item ->> 'data', 1, 10)::date
                  ELSE NULL
                END
            ) AS order_date,
            max(nullif(item ->> 'data', '')) AS order_date_raw,
            coalesce(
                max(nullif(item ->> 'ds_produto', '')),
                trim(max(coalesce(item ->> 'ds_grupo', '')) || ' ' || o.cd_prod_cor)
            ) AS name,
            sum(greatest(coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0), 0))::bigint
                AS qty,
            sum(coalesce(nullif(item ->> 'vl_liquido', '')::numeric, 0))::numeric
                AS value,
            sum(
                coalesce(nullif(item ->> 'vl_liquido', '')::numeric, 0)
                - coalesce(nullif(item ->> 'diff_valor', '')::numeric, 0)
            )::numeric AS original_value,
            jsonb_object_agg(size_key, size_qty ORDER BY size_key) AS sizes
        FROM (
            SELECT
                item,
                upper(trim(item ->> 'sg_tamanho')) AS size_key,
                sum(
                    greatest(coalesce(nullif(item ->> 'qt_liquida', '')::integer, 0), 0)
                ) OVER (PARTITION BY upper(trim(item ->> 'sg_tamanho'))) AS size_qty
            FROM jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
            WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
              AND length(trim(item ->> 'sg_tamanho')) BETWEEN 1 AND 16
        ) items
        HAVING count(*) > 0
    ) d ON d.channel IS NOT NULL
    LEFT JOIN pedido_produto_read erp
      ON erp.source = 'erp'
     AND erp.nr_pedido = o.nr_pedido
     AND erp.cd_prod_cor = o.cd_prod_cor
    WHERE {where_sql}
    """


def _pairs_cte(estagio: str, *, status: str, scoped_product: bool = False) -> str:
    ppr_scope = (
        " AND p.cd_prod_cor = :code AND p.canal = :channel" if scoped_product else ""
    )
    or_scope = (
        " AND o.cd_prod_cor = :code AND d.channel = :channel" if scoped_product else ""
    )
    if estagio == "aguardando":
        return f"""
        pairs AS (
            SELECT
                p.nr_pedido, p.cd_prod_cor AS code, p.client, p.canal AS channel,
                p.order_date, p.order_date_raw,
                coalesce(p.product_name, p.cd_prod_cor) AS name,
                p.qty, p.value, p.value AS original_value, p.sizes,
                NULL::timestamptz AS processed_at,
                NULL::timestamptz AS approved_at,
                false AS adequacao, false AS aprovado,
                'Liberados para faturamento'::text AS status,
                ''::text AS motivo, 'pending'::text AS source,
                'app'::text AS origem, false AS confirmado_erp,
                p.source AS version_tipo,
                p.synced_at AS version_created_at,
                NULL::timestamptz AS version_approved_at,
                p.sizes AS version_items
            FROM pedido_produto_read p
            LEFT JOIN pedidos_processados pp
              ON pp.nr_pedido = p.nr_pedido
             AND pp.cd_prod_cor = p.cd_prod_cor
            WHERE p.source = 'pending' AND pp.nr_pedido IS NULL{ppr_scope}
        )
        """
    if estagio == "edicao":
        return f"""
        pairs AS (
            {_or_pairs_sql("o.aprovado_em IS NULL AND o.created_at >= now() - interval '24 hours'" + or_scope)}
        )
        """

    status_target = _STATUS_SQL[status]
    status_filter = "true" if status_target is None else "status = :status_target"
    historical_or = _or_pairs_sql(
        "(o.aprovado_em IS NOT NULL OR o.created_at < now() - interval '24 hours') "
        "AND NOT EXISTS (SELECT 1 FROM pending_active_keys pa WHERE pa.nr_pedido = o.nr_pedido "
        "AND pa.cd_prod_cor = o.cd_prod_cor)" + or_scope
    )
    return f"""
    {_available_stock_cte(scoped_product=scoped_product)},
    pending_active_keys AS MATERIALIZED (
        SELECT p.nr_pedido, p.cd_prod_cor
        FROM pedido_produto_read p
        LEFT JOIN pedidos_processados pp
          ON pp.nr_pedido = p.nr_pedido
         AND pp.cd_prod_cor = p.cd_prod_cor
        WHERE p.source = 'pending' AND pp.nr_pedido IS NULL{ppr_scope}
    ),
    all_history_pairs AS (
        SELECT
            p.nr_pedido, p.cd_prod_cor AS code, p.client, p.canal AS channel,
            p.order_date, p.order_date_raw,
            coalesce(p.product_name, p.cd_prod_cor) AS name,
            p.qty, p.value, p.value AS original_value, p.sizes,
            NULL::timestamptz AS processed_at,
            NULL::timestamptz AS approved_at,
            false AS adequacao, true AS aprovado,
            CASE WHEN p.credito_bloqueado THEN 'Bloqueado sem crédito'
                 ELSE 'Bloqueado Estoque' END AS status,
            CASE WHEN p.credito_bloqueado THEN 'Aguardando liberação de crédito'
                 ELSE 'Estoque insuficiente para o produto' END AS motivo,
            'pending'::text AS source, 'app'::text AS origem,
            false AS confirmado_erp,
            p.source AS version_tipo,
            p.synced_at AS version_created_at,
            NULL::timestamptz AS version_approved_at,
            p.sizes AS version_items
        FROM pedido_produto_read p
        LEFT JOIN pedidos_processados pp
          ON pp.nr_pedido = p.nr_pedido
         AND pp.cd_prod_cor = p.cd_prod_cor
        LEFT JOIN available_product ap
          ON ap.cd_prod_cor = p.cd_prod_cor AND ap.canal = p.canal
        WHERE p.source = 'pending'
          AND pp.nr_pedido IS NULL
          AND (p.credito_bloqueado OR p.qty > coalesce(ap.qty, 0))
          {ppr_scope}

        UNION ALL
        {historical_or}

        UNION ALL
        SELECT
            p.nr_pedido, p.cd_prod_cor, p.client, p.canal,
            p.order_date, p.order_date_raw,
            coalesce(p.product_name, p.cd_prod_cor),
            p.qty, p.value, p.value, p.sizes,
            NULL::timestamptz, NULL::timestamptz,
            false, true, 'Processado no ERP',
            CASE WHEN p.indica_reserva AND p.indica_embalado
                 THEN 'Reserva e embalado no ERP'
                 WHEN p.indica_embalado THEN 'Embalado no ERP'
                 ELSE 'Reserva efetuada no ERP' END,
            'erp', 'erp', true,
            p.source, p.synced_at, NULL::timestamptz, p.sizes
        FROM pedido_produto_read p
        WHERE p.source = 'erp'
          {ppr_scope}
          AND NOT EXISTS (
              SELECT 1 FROM ordens_reserva o
              WHERE o.nr_pedido = p.nr_pedido
                AND o.cd_prod_cor = p.cd_prod_cor
          )
          AND NOT EXISTS (
              SELECT 1 FROM pending_active_keys pa
              WHERE pa.nr_pedido = p.nr_pedido
                AND pa.cd_prod_cor = p.cd_prod_cor
          )
    ),
    pairs AS (
        SELECT * FROM all_history_pairs WHERE {status_filter}
    )
    """
