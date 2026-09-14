"""pedidos_processados_erp: vl_planejado e vl_distribuido

Revision ID: 010
Revises: 009
Create Date: 2026-07-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pedidos_processados_erp",
        sa.Column("vl_planejado", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column(
        "pedidos_processados_erp",
        sa.Column("vl_distribuido", sa.Numeric(precision=16, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pedidos_processados_erp", "vl_distribuido")
    op.drop_column("pedidos_processados_erp", "vl_planejado")
