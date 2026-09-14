"""remove colunas de ingestão escritas pelo full refresh e nunca lidas

Revision ID: 028
Revises: 027
Create Date: 2026-08-08

As três colunas removidas aqui só existiam do lado da ESCRITA: o full refresh
do Databricks as preenchia a cada sync e nenhuma consulta, projeção, rota ou
relatório deste backend jamais as leu.

* ``pedidos_processados_erp.vl_planejado`` / ``.vl_distribuido`` nasceram na
  revisão 010 como "valores do ERP". Quem alimenta o gráfico
  ``/evolucao-faturamento`` é ``faturamento_colecao`` (revisão 013), que tem
  colunas homônimas e **permanece intacta** — a agregação por coleção × canal é
  calculada no próprio Databricks, não a partir desta tabela. Aqui as duas
  colunas eram nulas na maioria das linhas e redundantes nas demais
  (``vl_planejado == vl_distribuido``). Sem perda de informação.

* ``pedidos.cd_colecao_ped`` vem da view ``system_automation_pedidos_em_aberto`` e é
  ``NOT NULL``, mas nenhum consumidor a lê: a coleção usada em regra de negócio
  é derivada de ``dt_emissao`` (ver ``databricks_reader.ler_faturamento_colecao``),
  justamente porque o código de coleção do pedido não é confiável para o
  recorte por semestre. O dado É reconstruível: ``pedidos`` é uma tabela de
  snapshot em full refresh (DELETE + INSERT a cada sincronização), então um
  ``sincronizar_pedidos`` contra o Databricks reconstrói a coluna inteira a
  partir da origem — nada aqui é fonte de verdade.

O ``downgrade`` recria as três colunas. Como ``cd_colecao_ped`` é ``NOT NULL``
e as linhas já existentes não têm valor, ela é recriada com ``server_default``
temporário (``0``), que é removido em seguida: o default existe apenas para
satisfazer a restrição durante o ``ALTER TABLE``, não para virar contrato de
schema. O valor real volta no próximo full refresh.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("pedidos_processados_erp", "vl_distribuido")
    op.drop_column("pedidos_processados_erp", "vl_planejado")
    op.drop_column("pedidos", "cd_colecao_ped")


def downgrade() -> None:
    # server_default temporário: satisfaz o NOT NULL nas linhas existentes sem
    # virar default permanente do schema (o valor real vem do próximo sync).
    op.add_column(
        "pedidos",
        sa.Column(
            "cd_colecao_ped",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("pedidos", "cd_colecao_ped", server_default=None)
    op.add_column(
        "pedidos_processados_erp",
        sa.Column("vl_planejado", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column(
        "pedidos_processados_erp",
        sa.Column("vl_distribuido", sa.Numeric(precision=16, scale=2), nullable=True),
    )
