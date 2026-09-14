"""remove coluna nunca escrita e índices redundantes do auth

Revision ID: 027
Revises: 026
Create Date: 2026-08-08

``auth_otp_challenges.session_token`` nasceu como parâmetro opcional sem
chamador: nenhuma versão do código jamais gravou valor nela, então todas as
linhas são NULL por construção e a remoção não perde dado. O token de sessão
real do fluxo de OTP é o JWT emitido por ``domain/tokens.create_session_token``
e trafega apenas na resposta HTTP — nunca foi persistido.

Os três índices removidos não servem consulta alguma:

* ``ix_auth_users_id`` e ``ix_auth_otp_challenges_id`` duplicam a chave
  primária, que já cria índice único sobre a mesma coluna. Custam escrita em
  todo INSERT sem jamais serem escolhidos pelo planner.
* ``ix_auth_otp_challenges_purpose`` tem cardinalidade 2 (``register`` e
  ``recovery``): seletividade baixa demais para o planner preferi-lo ao
  seq scan, e toda query do repositório filtra também por ``email``, já
  coberto por ``ix_auth_otp_challenges_email``.

A convenção oposta ("sem index=True: o unique já serve de índice") já está
documentada em ``ingestao/models.py`` e ``pedidos/models.py``; esta revisão
alinha o módulo auth a ela.

``CONCURRENTLY`` evita bloquear login e emissão de OTP durante o deploy. Um
índice inválido deixado por interrupção é removido antes do rerun.

Ordem das etapas: ``autocommit_block`` commita a transação corrente ao entrar,
então o ``ALTER TABLE`` e os ``CONCURRENTLY`` são commitados separadamente,
enquanto ``alembic_version`` só avança no fim. Por isso as operações
idempotentes (``DROP INDEX ... IF EXISTS`` / ``CREATE INDEX ... IF NOT
EXISTS``) vêm primeiro e a operação não idempotente sobre a coluna vem por
último: se um ``CONCURRENTLY`` for cancelado por lock/statement timeout, a
revisão continua em 026 com o schema íntegro e o rerun é limpo. Nenhum dos três
índices toca ``session_token``, então a ordem não cria dependência.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = {
    "ix_auth_users_id": ("auth_users", "id"),
    "ix_auth_otp_challenges_id": ("auth_otp_challenges", "id"),
    "ix_auth_otp_challenges_purpose": ("auth_otp_challenges", "purpose"),
}


def _drop_invalid_index(conn: sa.Connection, index_name: str) -> None:
    """Remove resto inválido de CREATE INDEX CONCURRENTLY interrompido."""

    invalid = bool(
        conn.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() "
                "AND c.relname = :index_name AND NOT i.indisvalid"
                ")"
            ),
            {"index_name": index_name},
        )
    )
    if invalid:
        # ``index_name`` vem apenas do mapa constante acima.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name in _INDEXES:
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')

    op.drop_column("auth_otp_challenges", "session_token")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for index_name, (table_name, columns) in reversed(list(_INDEXES.items())):
            _drop_invalid_index(conn, index_name)
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ({columns})'
            )

    op.add_column(
        "auth_otp_challenges",
        sa.Column("session_token", sa.String(length=512), nullable=True),
    )
