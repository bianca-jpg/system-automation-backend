"""OR a nível de produto: chave composta (nr_pedido, cd_prod_cor)

Uma OR é um PRODUTO, atendendo vários clientes com a grade de tamanhos que cada
um pediu. O ERP (Linx) sempre foi nível produto e recebe a informação assim.
Cada linha destas tabelas passa a ser a reserva de um produto para um cliente;
a OR de negócio é o GROUP BY cd_prod_cor, e a visão por cliente (apresentação)
é o GROUP BY nr_pedido.

CONTEXTO: esta decisão já havia sido implementada e aplicada em 2026-07-23 por
uma migration `012_or_por_produto.py` que nunca foi commitada e cujo fonte se
perdeu (só restou o .pyc). O banco de dev ficou com a chave composta e o código
Python voltou ao modelo por pedido — o "schema drift" documentado em
test_pedidos_routes.py era essa migração abandonada no meio do caminho. Aqui ela
é refeita, com duas diferenças em relação à original:

  1) CONDICIONAL: a original assumia o schema por pedido e recriava as tabelas.
     Esta detecta o estado real de cada tabela, então roda tanto num banco que já
     está no formato por produto (dev: no-op) quanto num banco limpo (CI, prod).
  2) NÃO DESTRUTIVA no caminho de dev: a original dropava as tabelas. As linhas
     que existem hoje no dev são ORs por produto VÁLIDAS (o sistema funcionou
     nesse formato por algumas horas) e são preservadas.

No caminho "banco no formato antigo" as linhas app-geradas SÃO descartadas: uma
OR por pedido não é traduzível para OR por produto, porque os itens de todos os
produtos estão misturados num único JSONB. Isso não perde histórico — o
histórico real vem do ERP, via a tabela de ingestão pedidos_processados_erp.

Revision ID: 014
Revises: 013
Create Date: 2026-08-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# As 3 tabelas do módulo pedidos que passam a ter a chave composta.
_TABELAS = ("ordens_reserva", "pedidos_processados", "pedido_modificacoes")


def _tem_cd_prod_cor(conn, tabela: str) -> bool:
    return (
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t "
                "AND column_name = 'cd_prod_cor'"
            ),
            {"t": tabela},
        ).first()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()

    for tabela in _TABELAS:
        if _tem_cd_prod_cor(conn, tabela):
            # Banco de dev: a migration perdida já deixou a coluna e a PK
            # composta. Nada a fazer — apenas confirmamos a PK esperada.
            pk = (
                conn.execute(
                    sa.text(
                        "SELECT a.attname FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                        " AND a.attnum = ANY(i.indkey) "
                        "WHERE n.nspname = 'public' AND c.relname = :t "
                        "AND i.indisprimary "
                        "ORDER BY a.attname"
                    ),
                    {"t": tabela},
                )
                .scalars()
                .all()
            )
            if set(pk) != {"nr_pedido", "cd_prod_cor"}:
                raise RuntimeError(
                    f"{tabela}: coluna cd_prod_cor existe mas a PK é {pk}; "
                    "estado inesperado, revise manualmente antes de migrar."
                )
            continue

        # Banco no formato por pedido (ou limpo). Linhas app-geradas antigas não
        # são traduzíveis para o grão de produto: descartadas.
        op.execute(f'DELETE FROM public."{tabela}"')
        op.add_column(
            tabela,
            sa.Column("cd_prod_cor", sa.String(length=64), nullable=False),
            schema="public",
        )
        op.drop_constraint(f"{tabela}_pkey", tabela, type_="primary", schema="public")
        op.create_primary_key(
            f"{tabela}_pkey",
            tabela,
            ["nr_pedido", "cd_prod_cor"],
            schema="public",
        )


def downgrade() -> None:
    """Volta ao grão por pedido, descartando as ORs por produto.

    Não há tradução possível no sentido inverso (N linhas por pedido colapsariam
    numa só, com perda dos itens dos outros produtos), então as linhas são
    descartadas — igual ao upgrade no caminho oposto.
    """
    conn = op.get_bind()
    for tabela in _TABELAS:
        if not _tem_cd_prod_cor(conn, tabela):
            continue
        op.execute(f'DELETE FROM public."{tabela}"')
        op.drop_constraint(f"{tabela}_pkey", tabela, type_="primary", schema="public")
        op.drop_column(tabela, "cd_prod_cor", schema="public")
        op.create_primary_key(f"{tabela}_pkey", tabela, ["nr_pedido"], schema="public")
