"""Snapshot leve das chaves de alerta ativo, consumido pela ingestão e pelo processing."""

from __future__ import annotations

from sqlalchemy import text

from app.modules.pedidos.infrastructure.sql.alertas_cte import (
    _ALERTS_CTE,
    _alerts_cte_pedidos,
    _alerts_cte_produto,
)


async def listar_chaves_alertas_ativos_sql(
    db,
    *,
    cd_prod_cor: str | None = None,
    channel: str | None = None,
    order_ids: set[int] | None = None,
) -> list[tuple[int, str]]:
    """Snapshot completo e leve dos alertas ativos, no grão pedido+tipo.

    É intencionalmente sem paginação: a ingestão usa o conjunto para calcular
    diferenças depois de um full refresh. Somente as duas chaves são transferidas;
    cards, itens e mensagens continuam exclusivos do endpoint paginado.
    """

    if (cd_prod_cor is None) != (channel is None):
        raise ValueError("produto e canal devem ser informados juntos")
    if cd_prod_cor is not None and order_ids is not None:
        raise ValueError("os escopos por produto e por pedido são exclusivos")
    if order_ids is not None and not order_ids:
        return []
    if order_ids is not None:
        cte = _alerts_cte_pedidos()
    elif cd_prod_cor is not None:
        cte = _alerts_cte_produto()
    else:
        cte = _ALERTS_CTE
    stmt = text(
        f"""
        WITH {cte}
        SELECT nr_pedido, alert_type
        FROM alerts
        ORDER BY nr_pedido, alert_type
        """
    )
    rows = (
        await db.execute(
            stmt,
            {
                "channel": "Todos",
                "search": "%%",
                "search_empty": True,
                "scope_product": cd_prod_cor,
                "scope_channel": channel,
                "scope_orders": sorted(order_ids) if order_ids is not None else None,
            },
        )
    ).all()
    return [(int(nr_pedido), str(alert_type)) for nr_pedido, alert_type in rows]
