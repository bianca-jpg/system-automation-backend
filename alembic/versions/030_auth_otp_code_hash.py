"""alarga auth_otp_challenges.code para digest hmac-sha256

Revision ID: 030
Revises: 029
Create Date: 2026-08-12

A coluna ``auth_otp_challenges.code`` passa a guardar
``HMAC-SHA256(code, key=JWT_SECRET)`` em hex (64 caracteres) em vez do código
de 6 dígitos em texto claro (SEC-05). O digest não cabe em ``varchar(6)``, daí
o alargamento para ``varchar(64)``.

Nenhum backfill é necessário: os desafios de OTP são efêmeros — têm TTL de 15
minutos (``OTP_TTL_MINUTES`` em ``repositorio_otp.py``) e ``remover_desafios``
já apaga o desafio anterior a cada novo pedido. Os códigos vigentes no
momento do deploy simplesmente expiram e o usuário pede outro.

O ``downgrade`` apaga as linhas antes de encurtar a coluna: um digest de 64
caracteres não cabe de volta em ``varchar(6)`` e o ``ALTER TABLE`` falharia
com ``StringDataRightTruncation``. Como os desafios são efêmeros e
reemitíveis, descartá-los é a operação correta e sem perda de informação.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "auth_otp_challenges",
        "code",
        type_=sa.String(length=64),
        existing_type=sa.String(length=6),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Descarta os desafios vigentes antes de encurtar a coluna: um digest de
    # 64 caracteres não cabe em varchar(6) e o ALTER TABLE falharia com
    # StringDataRightTruncation. Desafios são efêmeros (TTL de 15 minutos) e
    # reemitíveis, então apagar é seguro e sem perda de informação real.
    op.execute("DELETE FROM auth_otp_challenges")
    op.alter_column(
        "auth_otp_challenges",
        "code",
        type_=sa.String(length=6),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
