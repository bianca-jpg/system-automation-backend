"""Bloco SQL de erp_orders + agregação erp_count/erp_billing."""

from __future__ import annotations

from sqlalchemy import text


async def _erp_orders_stats(repo) -> tuple[dict[str, int], dict[str, float]]:
    erp_rows = (
        (
            await repo.db.execute(
                text(
                    """
                WITH erp_orders AS (
                    SELECT
                        pe.nr_pedido,
                        CASE WHEN bool_and(
                            upper(coalesce(pe.canal, '')) LIKE 'MULTIMARCA%'
                            OR upper(coalesce(pe.canal, '')) LIKE 'MM%'
                        ) THEN 'Multimarca'
                        WHEN bool_and(
                            upper(coalesce(pe.canal, '')) LIKE 'FRANQUIA%'
                            OR upper(coalesce(pe.canal, '')) LIKE 'FRQ%'
                        ) THEN 'Franquia'
                        ELSE NULL END AS canal,
                        sum(pe.vl_liquido)::numeric AS value
                    FROM pedidos_processados_erp pe
                    WHERE NOT EXISTS (
                        SELECT 1 FROM ordens_reserva o
                        WHERE o.nr_pedido = pe.nr_pedido
                          AND o.cd_prod_cor = pe.cd_prod_cor
                    )
                    GROUP BY pe.nr_pedido
                )
                SELECT canal, count(*)::bigint AS count, coalesce(sum(value), 0) AS billing
                FROM erp_orders
                WHERE canal IS NOT NULL
                GROUP BY canal
                """
                )
            )
        )
        .mappings()
        .all()
    )
    erp_count = {"Franquia": 0, "Multimarca": 0}
    erp_billing = {"Franquia": 0.0, "Multimarca": 0.0}
    for row in erp_rows:
        erp_count[str(row["canal"])] = int(row["count"])
        erp_billing[str(row["canal"])] = round(float(row["billing"]), 2)
    erp_count["Todos"] = erp_count["Franquia"] + erp_count["Multimarca"]
    erp_billing["Todos"] = round(erp_billing["Franquia"] + erp_billing["Multimarca"], 2)
    return erp_count, erp_billing
