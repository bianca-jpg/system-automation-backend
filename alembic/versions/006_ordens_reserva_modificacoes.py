"""ordens_reserva e pedido_modificacoes (estado do fluxo movido de JSON p/ Postgres)

Revision ID: 006
Revises: 005
Create Date: 2026-07-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ordens_reserva",
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=8), nullable=False),
        sa.Column("itens", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nr_pedido"),
    )
    op.create_table(
        "pedido_modificacoes",
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "original_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nr_pedido"),
    )


def downgrade() -> None:
    op.drop_table("pedido_modificacoes")
    op.drop_table("ordens_reserva")
