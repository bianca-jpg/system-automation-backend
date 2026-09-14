"""tabelas produto_tamanho_posicao (referência tamanho->posição) e ordens_reserva_linx (layout de saída do ERP Linx)

Revision ID: 015
Revises: 014
Create Date: 2026-08-04

AVISO: nesta fase as 2 tabelas nascem vazias (sem backfill, LINX-04), então o
`downgrade()` (drop_table) não perde nenhum dado. A partir da Fase 3, quando
`ordens_reserva_linx` passa a ter linhas reais, rodar este `downgrade` em
qualquer ambiente com dados É DESTRUTIVO — as linhas seriam perdidas.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "produto_tamanho_posicao",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("sg_tamanho", sa.String(length=16), nullable=False),
        sa.Column("nr_posicao", sa.Integer(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", name="uq_produto_tamanho_posicao_chave"
        ),
    )

    op.create_table(
        "ordens_reserva_linx",
        # Controle interno.
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nr_pedido", sa.Integer(), nullable=False),
        sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
        sa.Column("tipo", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Layout Linx (Tabelas_de_OR.csv) — todas nullable.
        sa.Column("nome_clifor", sa.String(length=255), nullable=True),
        sa.Column("produto", sa.String(length=64), nullable=True),
        sa.Column("cor_produto", sa.String(length=16), nullable=True),
        sa.Column("filial", sa.String(length=32), nullable=True),
        sa.Column("item", sa.Integer(), nullable=True),
        sa.Column("pedido", sa.Integer(), nullable=True),
        sa.Column("pedido_cor_produto", sa.String(length=64), nullable=True),
        sa.Column("romaneio", sa.String(length=32), nullable=True),
        sa.Column("caixa", sa.String(length=32), nullable=True),
        sa.Column("pedido_produto", sa.String(length=64), nullable=True),
        sa.Column("packs", sa.Integer(), nullable=True),
        sa.Column("entrega", sa.DateTime(timezone=True), nullable=True),
        sa.Column("caixa_fechada", sa.Boolean(), nullable=True),
        sa.Column("representante", sa.String(length=255), nullable=True),
        sa.Column("ipi", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("preco1", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("preco2", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("preco3", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("preco4", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("desconto_item", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("valor_embalado", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("qtde_embalada", sa.Integer(), nullable=True),
        sa.Column("origem", sa.String(length=32), nullable=True),
        sa.Column("mata_saldo", sa.Boolean(), nullable=True),
        # Grade posicional e1..e48 — atalho de list comprehension aceitável só
        # nesta migration; no model (app/modules/pedidos/models.py) cada coluna
        # é declarada explicitamente, uma por linha.
        *[
            sa.Column(f"e{i}", sa.Integer(), nullable=True, server_default=sa.text("0"))
            for i in range(1, 49)
        ],
        sa.Column("item_pedido", sa.Integer(), nullable=True),
        sa.Column("ordem_producao", sa.String(length=64), nullable=True),
        sa.Column("licenciado_royalties", sa.Boolean(), nullable=True),
        sa.Column("percent_desconto", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("caixa_virtual", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nr_pedido", "cd_prod_cor", name="uq_ordens_reserva_linx_chave"
        ),
    )


def downgrade() -> None:
    op.drop_table("ordens_reserva_linx")
    op.drop_table("produto_tamanho_posicao")
