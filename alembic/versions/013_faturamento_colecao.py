"""tabela faturamento_colecao (agregado pré-calculado do ERP por semestre × canal)

Revision ID: 013
Revises: 012
Create Date: 2026-08-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "faturamento_colecao",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("colecao", sa.Integer(), nullable=False),
        sa.Column("canal", sa.String(length=32), nullable=False),
        sa.Column("vl_planejado", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column("vl_distribuido", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("colecao", "canal", name="uq_faturamento_colecao_canal"),
    )


def downgrade() -> None:
    op.drop_table("faturamento_colecao")
