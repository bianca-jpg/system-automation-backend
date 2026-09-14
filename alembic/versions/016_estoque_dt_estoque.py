"""coluna dt_estoque em estoque (data da foto diária vinda do Databricks)

Revision ID: 016
Revises: 015
Create Date: 2026-08-05

A view `system_automation_estoque_filtrado` passou a filtrar `dt_estoque =
current_date()`: cada dia é uma foto do estoque. Guardar a data da foto permite
(a) avisar quando o job da origem atrasou e a foto não é de hoje e (b) usar a
virada da data como âncora de reset do estoque virtual.

Nullable de propósito: dispensa backfill — as linhas existentes são de uma foto
cuja data não se sabe, e o próximo sync substitui a tabela inteira (full refresh)
já com a data preenchida. Fora do `uq_estoque_chave`: o full refresh mantém uma
foto por vez, então a data não faz parte da identidade da linha.

O `downgrade()` só descarta a data da foto; as quantidades permanecem.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("estoque", sa.Column("dt_estoque", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("estoque", "dt_estoque")
