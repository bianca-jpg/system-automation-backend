"""persiste nome, cpf e telefone coletados no registro

Revision ID: 029
Revises: 028
Create Date: 2026-08-12

``RegisterRequest`` já exigia ``user_name``/``cpf`` e aceitava ``phone`` desde
que o cadastro público existe, mas ``AuthUser`` nunca teve colunas para eles:
o caso de uso de registro descartava os três valores em silêncio depois de
validá-los. O front-end de cadastro sempre colheu os três campos do usuário
(ver ``features/auth`` no front) — este dado só não tinha onde morar.

As três colunas nascem nulas: contas seedadas e qualquer registro feito antes
desta revisão não têm valor para retroagir. Novo registro sempre grava os
três (``cpf`` é obrigatório em ``RegisterRequest``); daí o ``unique`` em
``cpf`` sem índice parcial — Postgres não considera NULL para violação de
unicidade, então múltiplas contas antigas sem CPF convivem sem conflito.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_users", sa.Column("user_name", sa.String(length=255), nullable=True)
    )
    op.add_column("auth_users", sa.Column("cpf", sa.String(length=11), nullable=True))
    op.add_column("auth_users", sa.Column("phone", sa.String(length=20), nullable=True))
    op.create_unique_constraint("uq_auth_users_cpf", "auth_users", ["cpf"])


def downgrade() -> None:
    op.drop_constraint("uq_auth_users_cpf", "auth_users", type_="unique")
    op.drop_column("auth_users", "phone")
    op.drop_column("auth_users", "cpf")
    op.drop_column("auth_users", "user_name")
