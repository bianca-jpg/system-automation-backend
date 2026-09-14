"""pedidos_processados_erp: coluna data (dt_emissao)

Revision ID: 011
Revises: 010
Create Date: 2026-07-17

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pedidos_processados_erp",
        sa.Column("data", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pedidos_processados_erp", "data")
