"""projeção persistida por pedido e produto para leituras bounded

Revision ID: 020
Revises: 019
Create Date: 2026-08-08

``pedidos`` e ``pedidos_processados_erp`` têm uma linha por tamanho. O histórico
precisa primeiro reduzi-las ao grão ``(source, nr_pedido, cd_prod_cor)``; fazer
isso em toda requisição varria mais de um milhão de linhas. Esta migration cria
uma projeção derivada já nesse grão e a preenche a partir dos valores financeiros
normalizados pela revisão 019.

ERP tem precedência por par: se a fonte ERP contém ``(nr_pedido, cd_prod_cor)``,
o mesmo par não entra como ``pending``. ``pedidos_processados`` continua fora da
projeção porque é estado local mutável entre full refreshes e permanece como
anti-join da consulta.

Datas de origem inválidas nunca viram ``now()``: ``order_date_raw`` é preservado
e ``order_date`` fica NULL. Tamanhos vazios são excluídos e quantidades negativas
têm peso zero, espelhando os limites da ingestão. O refresh futuro usa o mesmo
DELETE + INSERT pending + INSERT ERP dentro da transação única do full refresh.
Valores ``pending`` permanecem não negativos; o ERP preserva ajustes contábeis
signed já normalizados pela revisão 019, sem ``GREATEST`` ou coerção para zero.
Antes do backfill, um pré-check aborta com o par de origem se encontrar canal
fora do vocabulário Franquia/FRQ ou Multimarca/MM. Canal desconhecido nunca é
silenciosamente classificado como Franquia.

O downgrade só remove esta projeção recalculável. Nenhuma tabela fonte é
alterada, e um re-upgrade reconstrói o mesmo estado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHANNEL_PREFLIGHT_SQL = """
DO $$
DECLARE
    anomaly record;
BEGIN
    SELECT invalid_channel.*
      INTO anomaly
      FROM (
          SELECT
              'pending'::text AS source,
              p.nr_pedido,
              p.cd_prod_cor,
              p.sg_tamanho,
              p.canal AS raw_channel
          FROM pedidos p
          WHERE NOT (
              upper(trim(coalesce(p.canal, ''))) LIKE 'FRANQUIA%'
              OR upper(trim(coalesce(p.canal, ''))) LIKE 'FRQ%'
              OR upper(trim(coalesce(p.canal, ''))) LIKE 'MULTIMARCA%'
              OR upper(trim(coalesce(p.canal, ''))) LIKE 'MM%'
          )
          UNION ALL
          SELECT
              'erp'::text,
              pe.nr_pedido,
              pe.cd_prod_cor,
              pe.sg_tamanho,
              pe.canal
          FROM pedidos_processados_erp pe
          WHERE NOT (
              upper(trim(coalesce(pe.canal, ''))) LIKE 'FRANQUIA%'
              OR upper(trim(coalesce(pe.canal, ''))) LIKE 'FRQ%'
              OR upper(trim(coalesce(pe.canal, ''))) LIKE 'MULTIMARCA%'
              OR upper(trim(coalesce(pe.canal, ''))) LIKE 'MM%'
          )
      ) AS invalid_channel
      ORDER BY source, nr_pedido, cd_prod_cor, sg_tamanho COLLATE "C"
      LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'ck_ppr_canal',
            MESSAGE = format(
                'revision 020 cannot build pedido_produto_read: unknown channel '
                'source=%s nr_pedido=%s cd_prod_cor=%s sg_tamanho=%s canal=%s; '
                'expected Franquia/FRQ or Multimarca/MM',
                anomaly.source,
                anomaly.nr_pedido,
                anomaly.cd_prod_cor,
                anomaly.sg_tamanho,
                coalesce(anomaly.raw_channel, '<NULL>')
            );
    END IF;

    SELECT mixed_channel.*
      INTO anomaly
      FROM (
          SELECT
              canonical.source,
              canonical.nr_pedido,
              canonical.cd_prod_cor,
              string_agg(
                  DISTINCT canonical.canonical_channel,
                  ',' ORDER BY canonical.canonical_channel
              ) AS channels
          FROM (
              SELECT
                  'pending'::text AS source,
                  p.nr_pedido,
                  p.cd_prod_cor,
                  CASE
                      WHEN upper(trim(coalesce(p.canal, ''))) LIKE 'MULTIMARCA%'
                        OR upper(trim(coalesce(p.canal, ''))) LIKE 'MM%'
                      THEN 'Multimarca'
                      WHEN upper(trim(coalesce(p.canal, ''))) LIKE 'FRANQUIA%'
                        OR upper(trim(coalesce(p.canal, ''))) LIKE 'FRQ%'
                      THEN 'Franquia'
                      ELSE NULL
                  END AS canonical_channel
              FROM pedidos p
              UNION ALL
              SELECT
                  'erp'::text,
                  pe.nr_pedido,
                  pe.cd_prod_cor,
                  CASE
                      WHEN upper(trim(coalesce(pe.canal, ''))) LIKE 'MULTIMARCA%'
                        OR upper(trim(coalesce(pe.canal, ''))) LIKE 'MM%'
                      THEN 'Multimarca'
                      WHEN upper(trim(coalesce(pe.canal, ''))) LIKE 'FRANQUIA%'
                        OR upper(trim(coalesce(pe.canal, ''))) LIKE 'FRQ%'
                      THEN 'Franquia'
                      ELSE NULL
                  END
              FROM pedidos_processados_erp pe
          ) AS canonical
          WHERE canonical.canonical_channel IS NOT NULL
          GROUP BY canonical.source, canonical.nr_pedido, canonical.cd_prod_cor
          HAVING count(DISTINCT canonical.canonical_channel) > 1
      ) AS mixed_channel
      ORDER BY source, nr_pedido, cd_prod_cor
      LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            CONSTRAINT = 'ck_ppr_canal',
            MESSAGE = format(
                'revision 020 cannot build pedido_produto_read: mixed channels '
                'source=%s nr_pedido=%s cd_prod_cor=%s channels=%s; '
                'one pair must belong to exactly one canonical channel',
                anomaly.source,
                anomaly.nr_pedido,
                anomaly.cd_prod_cor,
                anomaly.channels
            );
    END IF;
END
$$
"""


_PENDING_BACKFILL_SQL = """
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
        ) AS credito_bloqueado
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
    qty, value, sizes, credito_bloqueado,
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
    g.qty, g.value, g.sizes, p.credito_bloqueado,
    false, false, statement_timestamp()
FROM products p
JOIN grades g USING (nr_pedido, cd_prod_cor)
"""


_ERP_BACKFILL_SQL = """
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


def upgrade() -> None:
    # Serializa o backfill com o DELETE + INSERT do full refresh sem bloquear
    # leitores. A trava é mantida até o commit da revisão.
    op.execute(
        "LOCK TABLE pedidos, pedidos_processados_erp IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(_CHANNEL_PREFLIGHT_SQL)

    op.create_table(
        "pedido_produto_read",
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("client", sa.String(length=255), nullable=True),
        sa.Column("canal", sa.String(length=32), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("order_date_raw", sa.String(length=64), nullable=True),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("group_name", sa.String(length=128), nullable=True),
        sa.Column("qty", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("sizes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "credito_bloqueado",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "indica_reserva",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "indica_embalado",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('pending', 'erp')",
            name="ck_ppr_source",
        ),
        sa.CheckConstraint("nr_pedido > 0", name="ck_ppr_nr_pedido_positivo"),
        sa.CheckConstraint(
            "length(trim(cd_prod_cor)) BETWEEN 1 AND 64",
            name="ck_ppr_cd",
        ),
        sa.CheckConstraint(
            "canal IN ('Franquia', 'Multimarca')",
            name="ck_ppr_canal",
        ),
        sa.CheckConstraint("qty >= 0", name="ck_ppr_qty_nao_negativa"),
        sa.CheckConstraint(
            "source = 'erp' OR value >= 0",
            name="ck_ppr_value_nao_negativo",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sizes) = 'object' "
            "AND jsonb_array_length("
            "jsonb_path_query_array(sizes, '$.keyvalue()')"
            ") BETWEEN 1 AND 100",
            name="ck_ppr_sizes_bounded",
        ),
        sa.PrimaryKeyConstraint(
            "source",
            "nr_pedido",
            "cd_prod_cor",
        ),
    )

    op.execute(_PENDING_BACKFILL_SQL)
    op.execute(_ERP_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_table("pedido_produto_read")
