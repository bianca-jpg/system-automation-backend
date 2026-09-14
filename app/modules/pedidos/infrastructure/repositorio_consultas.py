"""Read projection SQL do contexto Pedidos.

Este módulo concentra o repositório de leitura e os helpers SQL compartilhados
pelas projeções de resumo, alertas, produtos e lookup. Cada
projeção seleciona primeiro uma página de chaves e só depois hidrata os dados
das chaves selecionadas: nenhum GET monta ou retém o snapshot inteiro em
memória.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos.domain.consultas import (
    ConsultaAlertas,
    ConsultaClientesProduto,
    ConsultaLookupPedidos,
    ConsultaProdutos,
    PaginaCursor,
)


class SqlPedidosReadRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _estoque_disponivel(
        self, targets: set[tuple[str, str, str]] | None = None
    ) -> dict[tuple[str, str, str], int]:
        """Disponível canônico: foto física menos ORs locais ainda não refletidas.

        A consulta é direcionada aos produtos da página quando ``targets`` é
        informado. Ela não depende da ordem dos cards nem de ``first-seen``.
        """
        product_filter = ""
        params: dict = {}
        if targets is not None:
            products = sorted({product for product, _size, _channel in targets})
            if not products:
                return {}
            product_filter = "WHERE e.cd_prod_cor = ANY(CAST(:products AS varchar[]))"
            params["products"] = products

        stmt = text(
            f"""
            WITH stock AS (
                SELECT e.cd_prod_cor, upper(e.sg_tamanho) AS sg_tamanho,
                       e.canal, e.qt_disponivel, e.dt_estoque
                FROM estoque e
                {product_filter}
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
                WHERE NOT EXISTS (
                    SELECT 1 FROM pedidos_processados_erp pe
                    WHERE pe.nr_pedido = o.nr_pedido
                      AND pe.cd_prod_cor = o.cd_prod_cor
                )
                  AND coalesce(item ->> 'status_item', '') <> 'Pedido em Stand By'
                  AND (
                    s.dt_estoque IS NULL
                    OR (o.created_at AT TIME ZONE 'America/Sao_Paulo')::date >= s.dt_estoque
                  )
                GROUP BY 1, 2, 3
            )
            SELECT
                s.cd_prod_cor, s.sg_tamanho, s.canal,
                greatest(s.qt_disponivel - coalesce(r.qt, 0), 0)::integer AS disponivel
            FROM stock s
            LEFT JOIN reservas r
              ON r.cd_prod_cor = s.cd_prod_cor
             AND r.sg_tamanho = s.sg_tamanho
             AND r.canal = s.canal
            """
        )
        rows = (await self.db.execute(stmt, params)).all()
        available = {(cd, size, channel): int(qty) for cd, size, channel, qty in rows}
        if targets is None:
            return available
        return {target: available.get(target, 0) for target in targets}

    async def obter_resumo(self, *, include_stock: bool = False) -> dict:
        from app.modules.pedidos.infrastructure.resumo.obter_resumo import (
            obter_resumo_sql,
        )

        return await obter_resumo_sql(self, include_stock=include_stock)

    async def listar_alertas(self, consulta: ConsultaAlertas) -> PaginaCursor:
        from app.modules.pedidos.infrastructure.resumo.listar_alertas import (
            listar_alertas_sql,
        )

        return await listar_alertas_sql(self, consulta)

    async def listar_produtos(self, consulta: ConsultaProdutos) -> PaginaCursor:
        from app.modules.pedidos.infrastructure.produtos.listar_produtos import (
            listar_produtos_sql,
        )

        return await listar_produtos_sql(self, consulta)

    async def listar_clientes_produto(self, consulta: ConsultaClientesProduto) -> dict:
        from app.modules.pedidos.infrastructure.produtos.listar_clientes_produto import (
            listar_clientes_produto_sql,
        )

        return await listar_clientes_produto_sql(self, consulta)

    async def listar_lookup(self, consulta: ConsultaLookupPedidos) -> PaginaCursor:
        from app.modules.pedidos.infrastructure.produtos.listar_lookup import (
            listar_lookup_sql,
        )

        return await listar_lookup_sql(self, consulta)
