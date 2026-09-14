"""tabelas pedidos e estoque (ingestao Databricks)

Revision ID: 005
Revises: 004
Create Date: 2026-06-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedidos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("sg_tamanho", sa.String(length=16), nullable=False),
        sa.Column("ds_grupo", sa.String(length=128), nullable=False),
        sa.Column("cd_colecao_ped", sa.Integer(), nullable=False),
        sa.Column("qt_entregar", sa.Integer(), nullable=False),
        sa.Column("vl_liquido", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("client", sa.String(length=255), nullable=True),
        sa.Column("canal", sa.String(length=32), nullable=True),
        sa.Column("status_credito", sa.String(length=64), nullable=True),
        sa.Column("ds_produto", sa.String(length=255), nullable=True),
        sa.Column("data", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nr_pedido", "cd_prod_cor", "sg_tamanho", name="uq_pedido_item"
        ),
    )
    op.create_index(
        op.f("ix_pedidos_nr_pedido"), "pedidos", ["nr_pedido"], unique=False
    )

    op.create_table(
        "estoque",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("sg_tamanho", sa.String(length=16), nullable=False),
        sa.Column("qt_disponivel", sa.Integer(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cd_prod_cor", "sg_tamanho", name="uq_estoque_chave"),
    )


def downgrade() -> None:
    op.drop_table("estoque")
    op.drop_index(op.f("ix_pedidos_nr_pedido"), table_name="pedidos")
    op.drop_table("pedidos")
