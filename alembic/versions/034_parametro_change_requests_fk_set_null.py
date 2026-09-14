"""parametro_change_requests.requested_by/reviewed_by viram ON DELETE SET NULL

Revision ID: 034
Revises: 033
Create Date: 2026-08-25 00:00:00.000000

Excluir um AuthUser (hard delete, quick task 260825-f21 — "Excluir acesso de
usuário") não pode quebrar o histórico de solicitações de parâmetro já
existente. Antes desta migration, `requested_by`/`reviewed_by` apontavam para
`auth_users.id` sem `ondelete`, então o Postgres recusaria (`IntegrityError`)
qualquer DELETE de um usuário que já tivesse solicitado ou revisado uma
mudança de parâmetro — exatamente o incidente de FK já visto na limpeza de
usuários de teste (`parametro_change_requests_requested_by_fkey`). Depois
desta migration, as duas FKs passam a `ON DELETE SET NULL`: a solicitação em
si (chave, status, payload, datas) sobrevive intacta, só o vínculo com o
usuário excluído se perde. `requested_by` também vira nullable — antes só
`reviewed_by` era.

Renumerada de "032" para "034" ao sincronizar com origin/develop: o número
"032" original colidia com `032_pedido_standby_motivo.py` (feature STANDBY,
tabela nova, sem relação com esta), que já estava mesclado no origin antes
desta migration ser criada localmente. Encadeada agora depois de "033"
(`033_pedido_indica_blacklist.py`), sem overlap de schema com nenhuma das
duas — motivo original da nota abaixo sobre o plano 11-02 não muda, só o
número que ele deve referenciar.

Nota para quem for executar o plano 11-02 (ainda não executado, reservava o
número "032" para outra mudança na mesma tabela: colunas de auditoria
requested_by_role/reviewed_by_role/previous_payload/origem): quando 11-02 for
executado, ele precisa apontar `down_revision = "034"` (esta migration) em vez
de "031"/"032" — senão gera dois heads.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "parametro_change_requests_requested_by_fkey",
        "parametro_change_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        "parametro_change_requests_reviewed_by_fkey",
        "parametro_change_requests",
        type_="foreignkey",
    )
    op.alter_column(
        "parametro_change_requests",
        "requested_by",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "parametro_change_requests_requested_by_fkey",
        "parametro_change_requests",
        "auth_users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "parametro_change_requests_reviewed_by_fkey",
        "parametro_change_requests",
        "auth_users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "parametro_change_requests_requested_by_fkey",
        "parametro_change_requests",
        type_="foreignkey",
    )
    op.drop_constraint(
        "parametro_change_requests_reviewed_by_fkey",
        "parametro_change_requests",
        type_="foreignkey",
    )
    op.alter_column(
        "parametro_change_requests",
        "requested_by",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "parametro_change_requests_requested_by_fkey",
        "parametro_change_requests",
        "auth_users",
        ["requested_by"],
        ["id"],
    )
    op.create_foreign_key(
        "parametro_change_requests_reviewed_by_fkey",
        "parametro_change_requests",
        "auth_users",
        ["reviewed_by"],
        ["id"],
    )
