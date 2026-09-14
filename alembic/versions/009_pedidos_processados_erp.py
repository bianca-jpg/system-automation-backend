"""tabela pedidos_processados_erp (flags de processamento do ERP)

Revision ID: 009
Revises: 008
Create Date: 2026-07-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedidos_processados_erp",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("sg_tamanho", sa.String(length=16), nullable=False),
        sa.Column("ds_grupo", sa.String(length=128), nullable=True),
        sa.Column("client", sa.String(length=255), nullable=True),
        sa.Column("canal", sa.String(length=32), nullable=True),
        sa.Column("ds_produto", sa.String(length=255), nullable=True),
        sa.Column("qt", sa.Integer(), nullable=False),
        sa.Column("vl_liquido", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("indica_reserva", sa.Boolean(), nullable=False),
        sa.Column("indica_embalado", sa.Boolean(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nr_pedido", "cd_prod_cor", "sg_tamanho", name="uq_proc_erp_item"
        ),
    )
    op.create_index(
        op.f("ix_pedidos_processados_erp_nr_pedido"),
        "pedidos_processados_erp",
        ["nr_pedido"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_pedidos_processados_erp_nr_pedido"),
        table_name="pedidos_processados_erp",
    )
    op.drop_table("pedidos_processados_erp")
