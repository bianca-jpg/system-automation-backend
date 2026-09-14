"""normaliza vl_liquido por tamanho sem alterar o total do produto no pedido

Revision ID: 019
Revises: 018
Create Date: 2026-08-08

As views de origem repetiam em cada ``sg_tamanho`` o ``vl_liquido`` total do
par ``(nr_pedido, cd_prod_cor)``. A aplicação, porém, soma as linhas da grade;
por isso o snapshot histórico ficava multiplicado pela quantidade de tamanhos.

Esta migration reparte uma única vez o total que esteja repetido de forma
consistente em todas as linhas do par, em centavos, proporcionalmente à
quantidade positiva de cada tamanho. Em ``pedidos`` apenas totais positivos são
normalizados. O ERP também publica devoluções/ajustes negativos legítimos; em
``pedidos_processados_erp`` o rateio usa o valor absoluto e reaplica o sinal ao
final. O método dos maiores restos garante soma assinada exata: cada linha
recebe primeiro a parte inteira de seus centavos e os centavos restantes seguem
o resto fracionário decrescente, com desempate estável por
``sg_tamanho COLLATE \"C\"`` e ``id``.

Pares divergentes, com total zero ou sem quantidade positiva são preservados:
não há base segura para inventar qual era o total original. Totais negativos de
``pedidos`` também são preservados, pois essa fonte não tem contrato signed.
Dentro de um par válido, quantidade nula/negativa tem peso zero e, portanto,
recebe valor zero.

O downgrade é proibido. Depois do rateio não é possível reconstruir com
segurança qual valor repetido havia em cada linha; baixar a revisão e permitir
um re-upgrade também aplicaria o rateio outra vez sobre dados já normalizados.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalizar_rateio(
    table_name: str,
    quantity_column: str,
    *,
    allow_negative: bool,
) -> None:
    """Aplica o mesmo contrato financeiro aos dois snapshots ingeridos.

    Os identificadores vêm exclusivamente das duas chamadas constantes em
    ``upgrade``; o assert evita que esta função vire SQL dinâmico genérico.
    """

    allowed_targets = {
        ("pedidos", "qt_entregar", False),
        ("pedidos_processados_erp", "qt", True),
    }
    assert (table_name, quantity_column, allow_negative) in allowed_targets

    total_predicate = (
        "MIN(vl_liquido) <> 0" if allow_negative else "MIN(vl_liquido) > 0"
    )

    op.execute(
        f"""
        WITH pair_totals AS (
            SELECT
                nr_pedido,
                cd_prod_cor,
                CASE WHEN MIN(vl_liquido) < 0 THEN -1 ELSE 1 END AS value_sign,
                ROUND(ABS(MIN(vl_liquido)) * 100, 0)::bigint AS total_cents,
                SUM(GREATEST({quantity_column}, 0)::bigint) AS total_qty
            FROM {table_name}
            GROUP BY nr_pedido, cd_prod_cor
            HAVING
                COUNT(*) > 1
                AND {total_predicate}
                AND MIN(vl_liquido) = MAX(vl_liquido)
                AND SUM(GREATEST({quantity_column}, 0)::bigint) > 0
        ),
        shares AS (
            SELECT
                item.id,
                item.nr_pedido,
                item.cd_prod_cor,
                item.sg_tamanho,
                totals.value_sign,
                totals.total_cents,
                FLOOR(
                    totals.total_cents::numeric
                    * GREATEST(item.{quantity_column}, 0)::numeric
                    / totals.total_qty::numeric
                )::bigint AS base_cents,
                MOD(
                    totals.total_cents::numeric
                    * GREATEST(item.{quantity_column}, 0)::numeric,
                    totals.total_qty::numeric
                ) AS fractional_remainder
            FROM {table_name} AS item
            JOIN pair_totals AS totals
              USING (nr_pedido, cd_prod_cor)
        ),
        ranked AS (
            SELECT
                shares.*,
                shares.total_cents
                    - SUM(shares.base_cents) OVER (
                        PARTITION BY shares.nr_pedido, shares.cd_prod_cor
                    ) AS residual_cents,
                ROW_NUMBER() OVER (
                    PARTITION BY shares.nr_pedido, shares.cd_prod_cor
                    ORDER BY
                        shares.fractional_remainder DESC,
                        shares.sg_tamanho COLLATE "C" ASC,
                        shares.id ASC
                ) AS residual_rank
            FROM shares
        ),
        allocated AS (
            SELECT
                id,
                (
                    value_sign
                    * (
                        base_cents
                        + CASE
                            WHEN residual_rank <= residual_cents THEN 1
                            ELSE 0
                          END
                    )
                )::numeric / 100::numeric AS allocated_value
            FROM ranked
        )
        UPDATE {table_name} AS item
           SET vl_liquido = allocated.allocated_value
          FROM allocated
         WHERE item.id = allocated.id
           AND item.vl_liquido IS DISTINCT FROM allocated.allocated_value
        """
    )


def upgrade() -> None:
    # O full refresh usa DELETE + INSERT. Esta trava deixa leituras livres, mas
    # serializa a normalização contra a ingestão antiga durante rolling startup.
    op.execute(
        "LOCK TABLE pedidos, pedidos_processados_erp IN SHARE ROW EXCLUSIVE MODE"
    )
    _normalizar_rateio("pedidos", "qt_entregar", allow_negative=False)
    _normalizar_rateio(
        "pedidos_processados_erp",
        "qt",
        allow_negative=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 019 is irreversible: downgrading would allow its financial "
        "allocation to run twice and corrupt already-normalized values."
    )
