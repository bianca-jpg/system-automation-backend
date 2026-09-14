"""Persistência das 3 tabelas de snapshot (full refresh: delete + insert na
mesma transação, sem commit próprio — quem decide quando commitar é
sincronizar_tudo)."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import delete, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestao.infrastructure.models import (
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
    ProdutoTamanhoPosicao,
)


async def substituir_pedidos(db: AsyncSession, itens: Iterable[dict]) -> None:
    await db.execute(delete(Pedido))
    db.add_all(
        Pedido(**{**item, "vl_liquido": Decimal(str(item["vl_liquido"]))})
        for item in itens
    )


async def substituir_estoque(
    db: AsyncSession, agregado: dict[tuple, int], dt_estoque: date | None = None
) -> None:
    """Substitui a foto de estoque. `dt_estoque` é a data da foto no Databricks
    (a mesma para todas as linhas) e serve de âncora de reset para o estoque
    virtual.

    O piso em zero saiu daqui: quem garante `disponivel > 0` é `agregar_estoque`,
    onde a regra de negócio é explícita e testável. Incondicional e sem commit
    próprio — a decisão de chamar ou não (guard de snapshot vazio) pertence ao
    caso de uso.
    """
    await db.execute(delete(Estoque))
    db.add_all(
        Estoque(
            cd_prod_cor=cd,
            sg_tamanho=tam,
            canal=canal,
            qt_disponivel=total,
            dt_estoque=dt_estoque,
        )
        for (cd, tam, canal), total in agregado.items()
    )


async def substituir_pedidos_processados_erp(
    db: AsyncSession, itens: Iterable[dict]
) -> None:
    await db.execute(delete(PedidoProcessadoErp))
    db.add_all(
        PedidoProcessadoErp(**{**item, "vl_liquido": Decimal(str(item["vl_liquido"]))})
        for item in itens
    )


_PENDING_READ_INSERT_SQL = """
WITH size_rows AS (
    SELECT
        p.nr_pedido,
        p.cd_prod_cor,
        upper(trim(p.sg_tamanho)) AS size_key,
        sum(greatest(p.qt_entregar, 0))::bigint AS size_qty,
        sum(p.vl_liquido)::numeric(14, 2) AS size_value
    FROM pedidos p
    WHERE length(trim(p.sg_tamanho)) BETWEEN 1 AND 16
    GROUP BY p.nr_pedido, p.cd_prod_cor, upper(trim(p.sg_tamanho))
),
products AS (
    SELECT
        p.nr_pedido,
        p.cd_prod_cor,
        coalesce(
            (array_agg(nullif(trim(p.client), '') ORDER BY p.id)
                FILTER (WHERE nullif(trim(p.client), '') IS NOT NULL))[1],
            'Cliente ' || p.nr_pedido::text
        ) AS client,
        CASE
            WHEN bool_or(
                upper(trim(coalesce(p.canal, ''))) LIKE 'MULTIMARCA%'
                OR upper(trim(coalesce(p.canal, ''))) LIKE 'MM%'
            ) AND NOT bool_or(
                upper(trim(coalesce(p.canal, ''))) LIKE 'FRANQUIA%'
                OR upper(trim(coalesce(p.canal, ''))) LIKE 'FRQ%'
            ) THEN 'Multimarca'
            WHEN bool_or(
                upper(trim(coalesce(p.canal, ''))) LIKE 'FRANQUIA%'
                OR upper(trim(coalesce(p.canal, ''))) LIKE 'FRQ%'
            ) AND NOT bool_or(
                upper(trim(coalesce(p.canal, ''))) LIKE 'MULTIMARCA%'
                OR upper(trim(coalesce(p.canal, ''))) LIKE 'MM%'
            ) THEN 'Franquia'
            ELSE NULL
        END AS canal,
        max(nullif(trim(p.data), '')) AS order_date_raw,
        coalesce(
            max(nullif(trim(p.ds_produto), '')),
            trim(coalesce(max(p.ds_grupo), '') || ' ' || p.cd_prod_cor)
        ) AS product_name,
        max(nullif(trim(p.ds_grupo), '')) AS group_name,
        bool_or(
            translate(
                upper(coalesce(p.status_credito, '')),
                'ÁÀÂÃÉÊÍÓÔÕÚÇ',
                'AAAAEEIOOOUC'
            ) LIKE '%SEM%CRED%'
            OR translate(
                upper(coalesce(p.status_credito, '')),
                'ÁÀÂÃÉÊÍÓÔÕÚÇ',
                'AAAAEEIOOOUC'
            ) LIKE '%BLOQUE%'
            OR translate(
                upper(coalesce(p.status_credito, '')),
                'ÁÀÂÃÉÊÍÓÔÕÚÇ',
                'AAAAEEIOOOUC'
            ) LIKE '%REPROV%'
        ) AS credito_bloqueado,
        bool_or(p.indica_blacklist) AS indica_blacklist
    FROM pedidos p
    WHERE NOT EXISTS (
        SELECT 1
        FROM pedidos_processados_erp pe
        WHERE pe.nr_pedido = p.nr_pedido
          AND pe.cd_prod_cor = p.cd_prod_cor
    )
    GROUP BY p.nr_pedido, p.cd_prod_cor
),
grades AS (
    SELECT
        s.nr_pedido,
        s.cd_prod_cor,
        sum(s.size_qty)::bigint AS qty,
        sum(s.size_value)::numeric(14, 2) AS value,
        jsonb_object_agg(s.size_key, s.size_qty ORDER BY s.size_key) AS sizes
    FROM size_rows s
    GROUP BY s.nr_pedido, s.cd_prod_cor
)
INSERT INTO pedido_produto_read (
    source, nr_pedido, cd_prod_cor, client, canal,
    order_date, order_date_raw, product_name, group_name,
    qty, value, sizes, credito_bloqueado, indica_blacklist,
    indica_reserva, indica_embalado, synced_at
)
SELECT
    'pending', p.nr_pedido, p.cd_prod_cor, p.client, p.canal,
    CASE
        WHEN pg_input_is_valid(substring(p.order_date_raw, 1, 10), 'date')
        THEN substring(p.order_date_raw, 1, 10)::date
        ELSE NULL
    END,
    p.order_date_raw, p.product_name, p.group_name,
    g.qty, g.value, g.sizes, p.credito_bloqueado, p.indica_blacklist,
    false, false, statement_timestamp()
FROM products p
JOIN grades g USING (nr_pedido, cd_prod_cor)
"""


_ERP_READ_INSERT_SQL = """
WITH size_rows AS (
    SELECT
        pe.nr_pedido,
        pe.cd_prod_cor,
        upper(trim(pe.sg_tamanho)) AS size_key,
        sum(greatest(pe.qt, 0))::bigint AS size_qty,
        sum(pe.vl_liquido)::numeric(14, 2) AS size_value,
        bool_or(pe.indica_reserva) AS indica_reserva,
        bool_or(pe.indica_embalado) AS indica_embalado
    FROM pedidos_processados_erp pe
    WHERE length(trim(pe.sg_tamanho)) BETWEEN 1 AND 16
    GROUP BY pe.nr_pedido, pe.cd_prod_cor, upper(trim(pe.sg_tamanho))
),
products AS (
    SELECT
        pe.nr_pedido,
        pe.cd_prod_cor,
        coalesce(
            max(nullif(trim(pe.client), '')),
            'Cliente ' || pe.nr_pedido::text
        ) AS client,
        CASE
            WHEN bool_or(
                upper(trim(coalesce(pe.canal, ''))) LIKE 'MULTIMARCA%'
                OR upper(trim(coalesce(pe.canal, ''))) LIKE 'MM%'
            ) AND NOT bool_or(
                upper(trim(coalesce(pe.canal, ''))) LIKE 'FRANQUIA%'
                OR upper(trim(coalesce(pe.canal, ''))) LIKE 'FRQ%'
            ) THEN 'Multimarca'
            WHEN bool_or(
                upper(trim(coalesce(pe.canal, ''))) LIKE 'FRANQUIA%'
                OR upper(trim(coalesce(pe.canal, ''))) LIKE 'FRQ%'
            ) AND NOT bool_or(
                upper(trim(coalesce(pe.canal, ''))) LIKE 'MULTIMARCA%'
                OR upper(trim(coalesce(pe.canal, ''))) LIKE 'MM%'
            ) THEN 'Franquia'
            ELSE NULL
        END AS canal,
        max(nullif(trim(pe.data), '')) AS order_date_raw,
        coalesce(
            max(nullif(trim(pe.ds_produto), '')),
            trim(coalesce(max(pe.ds_grupo), '') || ' ' || pe.cd_prod_cor)
        ) AS product_name,
        max(nullif(trim(pe.ds_grupo), '')) AS group_name
    FROM pedidos_processados_erp pe
    GROUP BY pe.nr_pedido, pe.cd_prod_cor
),
grades AS (
    SELECT
        s.nr_pedido,
        s.cd_prod_cor,
        sum(s.size_qty)::bigint AS qty,
        sum(s.size_value)::numeric(14, 2) AS value,
        jsonb_object_agg(s.size_key, s.size_qty ORDER BY s.size_key) AS sizes,
        bool_or(s.indica_reserva) AS indica_reserva,
        bool_or(s.indica_embalado) AS indica_embalado
    FROM size_rows s
    GROUP BY s.nr_pedido, s.cd_prod_cor
)
INSERT INTO pedido_produto_read (
    source, nr_pedido, cd_prod_cor, client, canal,
    order_date, order_date_raw, product_name, group_name,
    qty, value, sizes, credito_bloqueado,
    indica_reserva, indica_embalado, synced_at
)
SELECT
    'erp', p.nr_pedido, p.cd_prod_cor, p.client, p.canal,
    CASE
        WHEN pg_input_is_valid(substring(p.order_date_raw, 1, 10), 'date')
        THEN substring(p.order_date_raw, 1, 10)::date
        ELSE NULL
    END,
    p.order_date_raw, p.product_name, p.group_name,
    g.qty, g.value, g.sizes, false,
    g.indica_reserva, g.indica_embalado, statement_timestamp()
FROM products p
JOIN grades g USING (nr_pedido, cd_prod_cor)
"""


_STANDBY_ORPHAN_CLEANUP_SQL = """
DELETE FROM pedido_standby_motivo psm
WHERE NOT EXISTS (
    SELECT 1
    FROM pedido_produto_read ppr
    WHERE ppr.source = 'pending'
      AND ppr.nr_pedido = psm.nr_pedido
      AND ppr.cd_prod_cor = psm.cd_prod_cor
)
"""


async def reconstruir_pedido_produto_read(db: AsyncSession) -> int:
    """Reconstrói a projeção por par a partir dos dois snapshots normalizados.

    Participa da mesma transação do full refresh. ERP tem precedência quando a
    origem ainda publica o mesmo par nas duas fotos; datas inválidas ficam NULL
    sem apagar o texto bruto usado em diagnóstico.

    Depois dos dois INSERTs, limpa órfãos de `pedido_standby_motivo` — pares
    que deixaram de estar pendentes (ex.: confirmados direto no ERP) sem que
    ninguém tenha apagado a linha de stand-by correspondente. Precisa rodar
    estritamente DEPOIS dos INSERTs pending/erp: entre o DELETE de
    `pedido_produto_read` e os INSERTs a tabela fica temporariamente vazia, e
    rodar a limpeza nessa janela apagaria toda linha de stand-by, mesmo pares
    que continuam pendentes de verdade.
    """
    await db.flush()
    await db.execute(text("DELETE FROM pedido_produto_read"))
    pending = await db.execute(text(_PENDING_READ_INSERT_SQL))
    erp = await db.execute(text(_ERP_READ_INSERT_SQL))
    await db.execute(text(_STANDBY_ORPHAN_CLEANUP_SQL))
    pending_dml = cast(CursorResult[Any], pending)
    erp_dml = cast(CursorResult[Any], erp)
    return int(pending_dml.rowcount or 0) + int(erp_dml.rowcount or 0)


async def substituir_faturamento_colecao(
    db: AsyncSession, linhas: Iterable[dict]
) -> None:
    """Substitui o agregado de faturamento por (colecao, canal).
    Linhas esperadas: {colecao, canal, vl_planejado, vl_distribuido}."""
    await db.execute(delete(FaturamentoColecao))
    db.add_all(
        FaturamentoColecao(
            **{
                **linha,
                "vl_planejado": Decimal(str(linha["vl_planejado"])),
                "vl_distribuido": Decimal(str(linha["vl_distribuido"])),
            }
        )
        for linha in linhas
    )


async def substituir_referencia_tamanhos(
    db: AsyncSession, itens: Iterable[dict]
) -> None:
    """Substitui a referência tamanho->posição (cd_prod_cor, sg_tamanho, nr_posicao).
    Incondicional, sem commit próprio — a decisão de chamar ou não esta função
    (D-02, referência vazia) pertence ao caso de uso, não a este repositório."""
    await db.execute(delete(ProdutoTamanhoPosicao))
    db.add_all(ProdutoTamanhoPosicao(**item) for item in itens)
