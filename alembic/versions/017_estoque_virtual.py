"""tabela estoque_virtual (PLACEBO: disponível descontando as ORs do app)

Revision ID: 017
Revises: 016
Create Date: 2026-08-05

PLACEBO TEMPORÁRIO. A view de estoque entrega uma foto CONGELADA por dia e o app
ainda não tem conexão com o ERP, então uma OR gerada às 10h não reduz o
disponível às 12h. Esta tabela guarda a projeção
`foto(estoque) − ORs geradas desde a data da foto`. Ver o docstring de
`EstoqueVirtual` em app/modules/pedidos/models.py para os limites aceitos e a
condição de remoção.

A tabela é uma PROJEÇÃO recalculável: `downgrade()` não perde dado de negócio —
o próximo recálculo a reconstrói inteira a partir de `estoque` + `ordens_reserva`.
Nasce vazia; o primeiro recálculo (lazy na leitura ou ao gerar OR) a preenche.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "estoque_virtual",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("sg_tamanho", sa.String(length=16), nullable=False),
        sa.Column("canal", sa.String(length=32), nullable=False),
        sa.Column(
            "qt_disponivel", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("dt_estoque", sa.Date(), nullable=True),
        sa.Column(
            "recalculado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", "canal", name="uq_estoque_virtual_chave"
        ),
    )


def downgrade() -> None:
    op.drop_table("estoque_virtual")
