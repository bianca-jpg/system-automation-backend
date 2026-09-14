"""adiciona auth_users.display_name, alimentada pelo claim `name` do SSO

Revision ID: 036
Revises: 035
Create Date: 2026-09-08 00:00:00.000000

O menu do usuário do painel mostrava o e-mail no lugar do nome. A causa não
estava no frontend: `sign_in_microsoft` validava o ID token do Entra e lia
apenas `email`/`preferred_username`, descartando o claim `name` que a Microsoft
já manda em todo login. Sem nome no banco, `AuthUserResponse` não tinha o que
serializar, e o frontend caía no fallback `display_name → email → "Usuário"`.

Esta coluna **não é a volta do `user_name` removido pela 035**. Aquele campo
pertencia ao cadastro local (e-mail + senha), era digitado pela própria pessoa
e morreu junto com o fluxo que o alimentava. `display_name` tem outra origem e
outro dono: é espelho do diretório corporativo, reescrito a cada autenticação
pelo `sign_in_microsoft`. Trocar de nome no AD se propaga sozinho no acesso
seguinte, e nenhum endpoint do painel oferece edição — não faria sentido, já
que o SSO sobrescreveria a alteração no próximo login.

Nullable de propósito, sem backfill possível: contas que já existem só ganham
nome quando a pessoa autenticar de novo. Um `UPDATE` de backfill precisaria de
uma fonte de nomes que o backend não tem (é justamente o que falta), e derivar
nome do e-mail produziria dado errado disfarçado de certo — "victoria.mollica"
não é um nome, é um login. Até o próximo login dessas contas, o frontend segue
no fallback do e-mail, que é o comportamento atual e não regride nada.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("display_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_users", "display_name")
