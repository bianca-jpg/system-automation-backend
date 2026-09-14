"""tabela pedidos_processados

Revision ID: 003
Revises: 002
Create Date: 2026-06-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedidos_processados",
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column(
            "processado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nr_pedido"),
    )


def downgrade() -> None:
    op.drop_table("pedidos_processados")
