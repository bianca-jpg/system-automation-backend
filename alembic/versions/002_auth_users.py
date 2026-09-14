"""
auth users and otp challenges

Revision ID: 002
Revises: 001
Create Date: 2026-05-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(op.f("ix_auth_users_email"), "auth_users", ["email"], unique=True)
    op.create_index(op.f("ix_auth_users_id"), "auth_users", ["id"], unique=False)

    op.create_table(
        "auth_otp_challenges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("session_token", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_auth_otp_challenges_email"),
        "auth_otp_challenges",
        ["email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_otp_challenges_id"), "auth_otp_challenges", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_auth_otp_challenges_purpose"),
        "auth_otp_challenges",
        ["purpose"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_auth_otp_challenges_purpose"), table_name="auth_otp_challenges"
    )
    op.drop_index(op.f("ix_auth_otp_challenges_id"), table_name="auth_otp_challenges")
    op.drop_index(
        op.f("ix_auth_otp_challenges_email"), table_name="auth_otp_challenges"
    )
    op.drop_table("auth_otp_challenges")
    op.drop_index(op.f("ix_auth_users_id"), table_name="auth_users")
    op.drop_index(op.f("ix_auth_users_email"), table_name="auth_users")
    op.drop_table("auth_users")
