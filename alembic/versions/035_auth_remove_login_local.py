"""remove colunas de senha/cadastro local e a tabela de OTP (SSO-only)

Revision ID: 035
Revises: 034
Create Date: 2026-08-27 00:00:00.000000

O login por e-mail e senha foi removido por completo do backend (quick task
260827-fqf): Microsoft Entra ID (SSO) passa a ser o único caminho de
autenticação. Sem cadastro público, sem OTP e sem recuperação de senha, as
colunas `password_hash`/`user_name`/`cpf`/`phone` de `auth_users` e a tabela
inteira `auth_otp_challenges` deixam de ter qualquer código que as leia ou
escreva.

Ordem do `upgrade()`:
1. Backfill de `confirmed_at` PRIMEIRO, enquanto a tabela ainda está intacta.
   Sem cadastro local não existe mais fluxo que confirme uma conta: o
   provisionamento por SSO (`sign_in_microsoft`) já grava `confirmed_at` no
   primeiro login, mas contas legadas cadastradas antes desta migration podem
   ter `confirmed_at IS NULL` para sempre, porque não sobra nenhum caminho de
   confirmação. Deixar essas linhas nulas teria dois efeitos ruins: o guard
   `confirmed_at is None` do módulo `realtime` bloquearia essas contas
   indefinidamente, e o alerta operacional "acesso pendente" (categoria
   `acesso`, já removido de `listar_alertas.py` nesta mesma quick task)
   ficaria alimentado por uma condição que nunca mais se resolve sozinha.
2. Drop de `uq_auth_users_cpf` (criada na 029) e das 4 colunas
   (`phone`, `cpf`, `user_name`, `password_hash`).
3. Drop do índice `ix_auth_otp_challenges_email` e da tabela
   `auth_otp_challenges`. Confirmado em produção (`pg_constraint`) que nenhuma
   FK de outra tabela aponta para `auth_otp_challenges` — a 027 já havia
   removido os outros 2 índices (`ix_auth_otp_challenges_id`,
   `ix_auth_otp_challenges_purpose`), então só o de `email` precisa sair aqui.

`downgrade()` restaura a ESTRUTURA no formato em que ela estava na 034, não
os dados: hashes de senha e códigos OTP não voltam (foram descartados, não
preservados em lugar nenhum) e o backfill de `confirmed_at` também não é
revertido — não há como saber, depois do fato, quais linhas eram nulas antes
do upgrade. `auth_otp_challenges` volta com `code` em `String(64)` (a 030
alargou a coluna para caber o digest HMAC-SHA256, não o código de 6 dígitos
em texto claro) e `password_hash` volta **nullable** (a 031 já a tinha
liberado antes desta migration existir).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Backfill primeiro, com a tabela ainda intacta.
    op.execute("UPDATE auth_users SET confirmed_at = now() WHERE confirmed_at IS NULL")

    # 2. Colunas exclusivas de senha/cadastro local.
    op.drop_constraint("uq_auth_users_cpf", "auth_users", type_="unique")
    op.drop_column("auth_users", "phone")
    op.drop_column("auth_users", "cpf")
    op.drop_column("auth_users", "user_name")
    op.drop_column("auth_users", "password_hash")

    # 3. Tabela de desafios de OTP, sem FK de nenhuma outra tabela apontando
    # para ela (confirmado via pg_constraint antes de escrever esta migration).
    op.drop_index(
        op.f("ix_auth_otp_challenges_email"), table_name="auth_otp_challenges"
    )
    op.drop_table("auth_otp_challenges")


def downgrade() -> None:
    # Restaura a tabela de OTP no formato pós-030 (code em String(64)).
    op.create_table(
        "auth_otp_challenges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="auth_otp_challenges_pkey"),
    )
    op.create_index(
        op.f("ix_auth_otp_challenges_email"),
        "auth_otp_challenges",
        ["email"],
        unique=False,
    )

    # Restaura as colunas de senha/cadastro local no formato pós-031
    # (password_hash já nullable desde a 031).
    op.add_column(
        "auth_users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "auth_users",
        sa.Column("user_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "auth_users",
        sa.Column("cpf", sa.String(length=11), nullable=True),
    )
    op.add_column(
        "auth_users",
        sa.Column("phone", sa.String(length=20), nullable=True),
    )
    op.create_unique_constraint("uq_auth_users_cpf", "auth_users", ["cpf"])
    # O backfill de confirmed_at (upgrade, passo 1) não é revertido: não há
    # como recuperar quais linhas eram NULL antes do upgrade.
