"""Lookup bounded de pedidos, para busca/autocomplete."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.modules.pedidos.domain.consultas import ConsultaLookupPedidos, PaginaCursor


async def listar_lookup_sql(repo, consulta: ConsultaLookupPedidos) -> PaginaCursor:
    cursor_activity = (
        datetime.fromisoformat(consulta.cursor[0]).date() if consulta.cursor else None
    )
    cursor_nr = consulta.cursor[1] if consulta.cursor else None
    if consulta.busca:
        matched_orders_cte = """
        matched_orders AS MATERIALIZED (
            SELECT DISTINCT p.nr_pedido
            FROM pedido_produto_read p
            WHERE p.nr_pedido::text LIKE :search ESCAPE '\\'
               OR lower(coalesce(p.client, '')) LIKE :search ESCAPE '\\'
               OR lower(p.cd_prod_cor) LIKE :search ESCAPE '\\'
               OR lower(coalesce(p.product_name, '')) LIKE :search ESCAPE '\\'

            UNION

            SELECT DISTINCT o.nr_pedido
            FROM ordens_reserva o
            CROSS JOIN LATERAL jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
            WHERE o.nr_pedido::text LIKE :search ESCAPE '\\'
               OR lower(coalesce(item ->> 'client', '')) LIKE :search ESCAPE '\\'
               OR lower(o.cd_prod_cor) LIKE :search ESCAPE '\\'
               OR lower(coalesce(item ->> 'ds_produto', '')) LIKE :search ESCAPE '\\'
        ),
        """
        candidate_filter = "AND p.nr_pedido IN (SELECT nr_pedido FROM matched_orders)"
        order_filter = "AND o.nr_pedido IN (SELECT nr_pedido FROM matched_orders)"
    else:
        matched_orders_cte = ""
        candidate_filter = ""
        order_filter = ""
    stmt = text(
        f"""
        WITH {matched_orders_cte}
        candidates AS (
            SELECT
                p.nr_pedido,
                max(p.client) AS client,
                max(p.canal) AS canal,
                max(p.order_date) AS activity,
                sum(p.value)::numeric AS value,
                CASE WHEN p.source = 'pending' THEN 2 ELSE 0 END AS priority,
                CASE WHEN p.source = 'pending' THEN 'Aguardando'
                     ELSE 'Processado no ERP' END AS status,
                CASE WHEN p.source = 'pending' THEN NULL
                     ELSE 'Reserva ou embalagem confirmada no ERP' END AS motivo
            FROM pedido_produto_read p
            LEFT JOIN pedidos_processados pp
              ON pp.nr_pedido = p.nr_pedido
             AND pp.cd_prod_cor = p.cd_prod_cor
            WHERE (p.source = 'erp' OR pp.nr_pedido IS NULL)
              {candidate_filter}
            GROUP BY p.source, p.nr_pedido
            HAVING count(DISTINCT p.canal) = 1

            UNION ALL
            SELECT
                o.nr_pedido,
                coalesce(max(item ->> 'client'), 'Cliente ' || o.nr_pedido::text),
                CASE
                  WHEN bool_and(
                    upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                    OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
                  ) THEN 'Multimarca'
                  WHEN bool_and(
                    upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                    OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
                  ) THEN 'Franquia'
                  ELSE NULL
                END,
                max(o.created_at)::date,
                sum(coalesce(nullif(item ->> 'vl_liquido', '')::numeric, 0)),
                CASE WHEN bool_or(o.aprovado_em IS NULL AND o.created_at >= now() - interval '24 hours')
                     THEN 3 ELSE 1 END,
                CASE WHEN bool_or(o.aprovado_em IS NULL AND o.created_at >= now() - interval '24 hours')
                     THEN 'Em edição' ELSE 'Histórico' END,
                'Ordem de reserva gerada no aplicativo'
            FROM ordens_reserva o
            CROSS JOIN LATERAL jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) item
            WHERE coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
              {order_filter}
            GROUP BY o.nr_pedido
            HAVING bool_and(
                upper(coalesce(item ->> 'canal', '')) LIKE 'MULTIMARCA%'
                OR upper(coalesce(item ->> 'canal', '')) LIKE 'MM%'
            ) OR bool_and(
                upper(coalesce(item ->> 'canal', '')) LIKE 'FRANQUIA%'
                OR upper(coalesce(item ->> 'canal', '')) LIKE 'FRQ%'
            )
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY nr_pedido ORDER BY priority DESC, activity DESC NULLS LAST
            ) AS rank
            FROM candidates
        ),
        base AS MATERIALIZED (
            SELECT * FROM ranked r
            WHERE rank = 1
        ),
        totals AS (SELECT count(*)::bigint AS total FROM base),
        page AS (
            SELECT * FROM base
            WHERE CAST(:cursor_activity AS date) IS NULL
               OR coalesce(activity, date '1970-01-01') < CAST(:cursor_activity AS date)
               OR (coalesce(activity, date '1970-01-01') = CAST(:cursor_activity AS date)
                   AND nr_pedido < CAST(:cursor_nr AS integer))
            ORDER BY coalesce(activity, date '1970-01-01') DESC, nr_pedido DESC
            LIMIT :limit_plus_one
        )
        SELECT totals.total, page.*
        FROM totals LEFT JOIN page ON true
        ORDER BY coalesce(page.activity, date '1970-01-01') DESC, page.nr_pedido DESC
        """
    )
    search = (
        consulta.busca.lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    raw = (
        (
            await repo.db.execute(
                stmt,
                {
                    "search": f"%{search}%",
                    "cursor_activity": cursor_activity,
                    "cursor_nr": cursor_nr,
                    "limit_plus_one": consulta.limite + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    total = int(raw[0]["total"] or 0)
    page = [dict(row) for row in raw if row["nr_pedido"] is not None]
    has_more = len(page) > consulta.limite
    page = page[: consulta.limite]
    rows = [
        {
            "id": int(row["nr_pedido"]),
            "client": str(row["client"] or f"Cliente {row['nr_pedido']}"),
            "canal": str(row["canal"]),
            "status": str(row["status"]),
            "motivo": str(row["motivo"]) if row["motivo"] is not None else None,
            "value": round(float(row["value"] or 0), 2),
        }
        for row in page
    ]
    next_key = None
    if has_more and page:
        last = page[-1]
        activity = last["activity"]
        next_key = (
            activity.isoformat() if activity is not None else "1970-01-01",
            int(last["nr_pedido"]),
        )
    return PaginaCursor(rows=rows, total=total, has_more=has_more, next_key=next_key)
