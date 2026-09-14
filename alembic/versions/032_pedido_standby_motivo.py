"""tabela pedido_standby_motivo (STANDBY-01/06)

Revision ID: 032
Revises: 031
Create Date: 2026-08-21

Projeção recalculável, no mesmo padrão de `estoque_virtual`/`pedido_produto_read`:
grão par (nr_pedido, cd_prod_cor), fora do ciclo de vida do job durável
(`pedido_processamentos`/`pedido_processamento_plan` são efêmeros, com
ON DELETE CASCADE a partir de `durable_jobs` e sujeitos a expurgo — por isso
`job_id` aqui é informativo/auditoria, sem FK).

3 motivos desde o início (`sem_credito`, `sem_estoque`, `furo_grade`) — a
fonte deste DDL é `16-RESEARCH.md` § "Gap de desenho resolvido", NUNCA
`ARCHITECTURE.md` §3.2, que está desatualizado e lista só 2 valores. Mudar o
CHECK depois de dados em produção seria uma migration extra evitável.

`execucoes_consecutivas` (STANDBY-06): contador agnóstico ao motivo,
incrementado a cada upsert em que o par já existia (wiring real é 16-04);
reseta implicitamente quando a linha é apagada (par saiu de stand-by) e
reaparece depois (INSERT novo, DEFAULT 1).

`downgrade()` só remove esta projeção recalculável — nenhuma tabela fonte é
alterada, e um re-upgrade a reconstrói vazia.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedido_standby_motivo",
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("canal", sa.String(length=16), nullable=False),
        sa.Column("motivo", sa.String(length=16), nullable=False),
        sa.Column(
            "execucoes_consecutivas",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "atualizado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("nr_pedido", "cd_prod_cor"),
        sa.CheckConstraint(
            "canal IN ('Franquia', 'Multimarca')",
            name="ck_psm_canal",
        ),
        sa.CheckConstraint(
            "motivo IN ('sem_credito', 'sem_estoque', 'furo_grade')",
            name="ck_psm_motivo",
        ),
        sa.CheckConstraint(
            "execucoes_consecutivas >= 1",
            name="ck_psm_execucoes_consecutivas",
        ),
    )


def downgrade() -> None:
    op.drop_table("pedido_standby_motivo")
