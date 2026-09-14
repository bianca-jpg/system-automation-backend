"""estoque isolado por canal (coluna canal + chave única com canal)

Revision ID: 007
Revises: 006
Create Date: 2026-07-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # estoque é full-refresh; as linhas atuais não têm canal e serão repovoadas
    # na próxima sincronização. Limpa antes de adicionar a coluna NOT NULL.
    op.execute("DELETE FROM estoque")
    op.drop_constraint("uq_estoque_chave", "estoque", type_="unique")
    op.add_column("estoque", sa.Column("canal", sa.String(length=32), nullable=False))
    op.create_unique_constraint(
        "uq_estoque_chave", "estoque", ["cd_prod_cor", "sg_tamanho", "canal"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_estoque_chave", "estoque", type_="unique")
    op.drop_column("estoque", "canal")
    op.create_unique_constraint(
        "uq_estoque_chave", "estoque", ["cd_prod_cor", "sg_tamanho"]
    )
