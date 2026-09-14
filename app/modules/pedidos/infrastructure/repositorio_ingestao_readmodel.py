"""Leitura cross-context do bounded context Integração de Dados (ingestão).

Consome a API de leitura pública de `ingestao` (app.modules.ingestao.infrastructure.http.api_leitura)
em vez de acessar os models de `ingestao` diretamente: os `import ... as` abaixo
são apenas o mapeamento de nomes entre os dois bounded contexts. A exceção é
`carregar_pedidos_itens`, implementada aqui porque compõe a foto da ingestão com
o estado local de processamento (anti-joins), que pertence a Pedidos.

`carregar_estoque_fisico` é a FOTO CRUA do Databricks. O estoque que o app usa
para reservar e exibir é o virtual (`repositorio_estoque_virtual.carregar_estoque`),
que desconta as ORs já geradas.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_estado_foto_estoque as carregar_estado_foto_estoque,
)
from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_estoque_por_canal as carregar_estoque_fisico,
)
from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_foto_estoque as carregar_foto_estoque,
)
from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_pares_processados_erp as _carregar_pares_processados_erp,
)
from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_pedidos_processados_erp as carregar_processados_erp,
)
from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_referencia_posicoes_por_produtos as carregar_referencia_posicoes,
)

__all__ = [
    "carregar_estado_foto_estoque",
    "carregar_estoque_fisico",
    "carregar_foto_estoque",
    "carregar_pares_processados_erp",
    "carregar_pedidos_itens",
    "carregar_processados_erp",
    "carregar_referencia_posicoes",
]


async def carregar_pedidos_itens(
    db: AsyncSession, *, canal: str = "Todos"
) -> list[dict]:
    """Pendências reais, com os anti-joins por par executados no PostgreSQL.

    Este adaptador pertence a Pedidos e pode compor a foto da ingestão com o
    estado local sem fazer o bounded context Ingestão depender das tabelas de
    processamento. Nenhum set de centenas de milhares de chaves é hidratado.
    """

    stmt = text(
        """
        SELECT
            p.nr_pedido, p.cd_prod_cor, p.sg_tamanho, p.ds_grupo,
            p.qt_entregar AS qt_liquida, p.vl_liquido,
            p.status_credito, p.client, p.canal, p.ds_produto, p.data
        FROM pedidos p
        WHERE (:canal = 'Todos' OR p.canal = :canal)
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
        ORDER BY p.nr_pedido DESC, p.cd_prod_cor, p.sg_tamanho, p.id
        """
    )
    rows = (await db.execute(stmt, {"canal": canal})).mappings().all()
    return [
        {
            "nr_pedido": int(row["nr_pedido"]),
            "cd_prod_cor": str(row["cd_prod_cor"]),
            "sg_tamanho": str(row["sg_tamanho"]),
            "ds_grupo": str(row["ds_grupo"]),
            "qt_liquida": int(row["qt_liquida"]),
            "vl_liquido": float(row["vl_liquido"]),
            "status_credito": row["status_credito"],
            "client": row["client"],
            "canal": row["canal"],
            "ds_produto": row["ds_produto"],
            "data": row["data"],
        }
        for row in rows
    ]


async def carregar_pares_processados_erp(
    db: AsyncSession, *, canal: str = "Todos"
) -> set[tuple[int, str]]:
    """Pares (pedido, produto) já processados no ERP.

    O ERP é nível produto; `carregar_processados_erp` achata por pedido para a
    montagem dos cards, então derivamos os pares a partir dos itens. Sem isto, um
    produto ainda aberto seria escondido só porque OUTRO produto do mesmo pedido
    foi processado no ERP.
    """
    return await _carregar_pares_processados_erp(db, canal=canal)
