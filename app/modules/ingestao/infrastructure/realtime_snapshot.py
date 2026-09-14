"""Adaptador de snapshot para o ledger realtime após o full refresh."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos.infrastructure.resumo.listar_chaves_alertas_ativos import (
    listar_chaves_alertas_ativos_sql,
)


async def carregar_chaves_realtime(
    db: AsyncSession,
) -> tuple[
    list[tuple[int, str]],
    list[tuple[int, str]],
    list[tuple[int, str]],
]:
    """Retorna pares de pedidos, alertas ativos e pares já processados no ERP."""

    pedidos = (
        await db.execute(
            text(
                """
                SELECT DISTINCT p.nr_pedido, p.cd_prod_cor
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
                ORDER BY p.nr_pedido, p.cd_prod_cor
                """
            )
        )
    ).all()
    alertas = await listar_chaves_alertas_ativos_sql(db)
    historico = (
        await db.execute(
            text(
                """
                SELECT DISTINCT nr_pedido, cd_prod_cor
                FROM pedidos_processados_erp
                ORDER BY nr_pedido, cd_prod_cor
                """
            )
        )
    ).all()
    return (
        [(int(nr_pedido), str(cd_prod_cor)) for nr_pedido, cd_prod_cor in pedidos],
        alertas,
        [(int(nr_pedido), str(cd_prod_cor)) for nr_pedido, cd_prod_cor in historico],
    )
