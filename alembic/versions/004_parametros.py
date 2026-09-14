"""parametros e solicitacoes de alteracao

Revision ID: 004
Revises: 003
Create Date: 2026-06-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parametros",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chave", sa.String(length=128), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("tipo", sa.String(length=16), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_parametros_chave"), "parametros", ["chave"], unique=True)

    op.create_table(
        "parametro_change_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("target_chave", sa.String(length=128), nullable=True),
        sa.Column("change_type", sa.String(length=16), nullable=False),
        sa.Column(
            "proposed_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["auth_users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["auth_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_parametro_change_requests_requested_by"),
        "parametro_change_requests",
        ["requested_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_parametro_change_requests_status"),
        "parametro_change_requests",
        ["status"],
        unique=False,
    )

    # Seed dos parâmetros de adequação (antes apenas em variáveis de ambiente).
    op.bulk_insert(
        sa.table(
            "parametros",
            sa.column("chave", sa.String),
            sa.column("valor", sa.Text),
            sa.column("tipo", sa.String),
            sa.column("descricao", sa.Text),
        ),
        [
            {
                "chave": "tolerancia_adequacao",
                "valor": "0.05",
                "tipo": "float",
                "descricao": "Tolerância (fração) aplicada na adequação de grade dos pedidos.",
            },
            {
                "chave": "criterio_selecao",
                "valor": "valor",
                "tipo": "string",
                "descricao": "Critério de priorização na adequação ('valor' ou 'quantidade').",
            },
        ],
    )

    # Limpa papéis legados gravados nos usuários existentes.
    op.execute(
        "UPDATE auth_users SET roles = '[\"operacional\"]'::jsonb "
        "WHERE roles @> '[\"operador\"]'::jsonb"
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_parametro_change_requests_status"),
        table_name="parametro_change_requests",
    )
    op.drop_index(
        op.f("ix_parametro_change_requests_requested_by"),
        table_name="parametro_change_requests",
    )
    op.drop_table("parametro_change_requests")
    op.drop_index(op.f("ix_parametros_chave"), table_name="parametros")
    op.drop_table("parametros")
