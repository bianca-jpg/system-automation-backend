"""indica_blacklist em pedidos/pedido_produto_read + motivo 'blacklist' (quick task 260825-jhv)

Revision ID: 033
Revises: 032
Create Date: 2026-08-25

Regra de negócio dura da mantenedora: cliente com `indica_blacklist="SIM"` na
origem (view Databricks `system_automation_pedidos_em_aberto`) nunca pode receber
peça de tamanho fora do que pediu, mesmo em `modo=ADEQUAR`. O motor passa a
forçar esses pedidos para o caminho tudo-ou-nada por produto (idêntico ao
`modo=SEM_ADEQUAR`), independente do modo escolhido — ver
`app/modules/pedidos/domain/motor_adequacao.py::blacklist_nrs`.

Duas colunas booleanas novas, ambas com `server_default=false` (aditivas,
sem backfill necessário — linhas existentes assumem "não blacklist", o lado
conservador): `pedidos.indica_blacklist` (snapshot cru do full refresh) e
`pedido_produto_read.indica_blacklist` (projeção por par, escrita via SQL
puro em `repositorio_snapshot.py`, por isso também precisa de
`server_default`, no mesmo padrão de `credito_bloqueado`).

`ck_psm_motivo` (`pedido_standby_motivo`, migration 032) ganha o 4º valor
`'blacklist'` — precisa de DROP + CREATE porque Postgres não altera CHECK
constraint in-place.

`downgrade()` é simétrico: primeiro devolve o CHECK aos 3 valores originais
(rejeitando 'blacklist' de novo), depois remove as 2 colunas — ordem inversa
do upgrade, para nunca deixar o CHECK aceitando um valor cuja origem (coluna)
já não existe mais.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pedidos",
        sa.Column(
            "indica_blacklist",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "pedido_produto_read",
        sa.Column(
            "indica_blacklist",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.drop_constraint("ck_psm_motivo", "pedido_standby_motivo", type_="check")
    op.create_check_constraint(
        "ck_psm_motivo",
        "pedido_standby_motivo",
        "motivo IN ('sem_credito', 'sem_estoque', 'furo_grade', 'blacklist')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_psm_motivo", "pedido_standby_motivo", type_="check")
    op.create_check_constraint(
        "ck_psm_motivo",
        "pedido_standby_motivo",
        "motivo IN ('sem_credito', 'sem_estoque', 'furo_grade')",
    )
    op.drop_column("pedido_produto_read", "indica_blacklist")
    op.drop_column("pedidos", "indica_blacklist")
