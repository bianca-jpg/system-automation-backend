"""Tabelas próprias do módulo pedidos (Adequação & Reserva).

GRÃO CANÔNICO: `(nr_pedido, cd_prod_cor)`. Uma OR é um PRODUTO, atendendo vários
clientes com a grade de tamanhos que cada um pediu — é assim que o ERP (Linx)
recebe. Cada linha aqui é a reserva de UM produto para UM cliente:

  - a OR de negócio (produto)      -> GROUP BY cd_prod_cor
  - a visão por cliente (só tela)  -> GROUP BY nr_pedido

Ver a migration 014 para o histórico dessa decisão.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.shared.database.base import Base


class PedidoProcessado(Base):
    """Pares (pedido, produto) já processados pelos botões deste app.

    Presença == aquele produto daquele pedido já gerou OR e sai da listagem de
    abertos. O pedido continua elegível para os OUTROS produtos dele.
    """

    __tablename__ = "pedidos_processados"

    nr_pedido = Column(Integer, primary_key=True)
    cd_prod_cor = Column(String(64), primary_key=True)
    processado_em = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrdemReserva(Base):
    """Reserva de um produto para um cliente (uma linha da OR do produto).

    `itens` é a GRADE de tamanhos deste produto para este cliente — uma unidade
    coesa, sempre lida e escrita junto. Não mistura produtos.
    """

    __tablename__ = "ordens_reserva"

    nr_pedido = Column(Integer, primary_key=True)
    cd_prod_cor = Column(String(64), primary_key=True)
    tipo = Column(String(8), nullable=False)  # 'sem' | 'com'
    itens = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Instante da aprovação MANUAL (botão "aprovar imediatamente"). NULL = ainda
    # não aprovado manualmente; nesse caso a aprovação é derivada por tempo
    # (created_at + 24h). Ver infrastructure/produtos/listar_produtos.py /
    # aprovar_ordem_reserva.
    aprovado_em = Column(DateTime(timezone=True), nullable=True)


Index(
    "ix_ordens_reserva_created_at_nr_pedido_cd_prod_cor",
    OrdemReserva.created_at.desc(),
    OrdemReserva.nr_pedido,
    OrdemReserva.cd_prod_cor,
)
Index(
    "ix_ordens_reserva_produto_pedido",
    OrdemReserva.cd_prod_cor,
    OrdemReserva.nr_pedido,
)


class EstoqueVirtual(Base):
    """PLACEBO TEMPORÁRIO — disponível para reserva, descontando as ORs do app.

    POR QUE EXISTE: a view de estoque no Databricks entrega uma foto CONGELADA por
    dia (`dt_estoque = current_date()`) e o app ainda não tem conexão com o ERP.
    Então uma OR gerada às 10h não reduz o disponível às 12h: dois pedidos
    diferentes do mesmo produto seriam ambos atendidos entre ciclos de sync,
    reservando mais peça do que existe.

    O QUE É: a projeção `foto(estoque) − ORs geradas desde a data da foto`,
    RECALCULADA do zero a cada gatilho — não é um saldo decrementado, então não
    acumula drift: qualquer falha ou rollback converge para o mesmo número. Ver
    `domain/estoque_virtual.py` para a regra.

    O reset diário não é código: quando `dt_estoque` avança, as ORs do dia
    anterior saem do filtro e a projeção volta a ser igual à foto.

    LIMITES ACEITOS: (1) reserva virtual não sobrevive à virada do dia — se a OR
    não chegou ao ERP, a peça reaparece como disponível amanhã; (2) enquanto a data
    da foto não muda, a projeção só é refeita quando o app gera OR, então uma
    reserva feita direto no Linx entra na conta só no próximo recálculo (erra para
    o lado conservador: reporta menos disponível).

    QUANDO REMOVER: quando o time de TI tornar a tabela mãe de estoque near
    real-time, ou quando existir integração com o ERP que atualize o disponível.
    Aí `carregar_estoque` volta a apontar para `estoque` e esta tabela, o
    `domain/estoque_virtual.py`, o `infrastructure/repositorio_estoque_virtual.py`
    e os gatilhos de recálculo saem juntos.
    """

    __tablename__ = "estoque_virtual"
    __table_args__ = (
        UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", "canal", name="uq_estoque_virtual_chave"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    cd_prod_cor = Column(String(64), nullable=False)
    sg_tamanho = Column(String(16), nullable=False)
    canal = Column(String(32), nullable=False)
    qt_disponivel = Column(Integer, nullable=False, default=0, server_default="0")
    # Foto de estoque que originou esta projeção. Divergir da dt_estoque atual de
    # `estoque` é o sinal de que a projeção está velha e precisa ser refeita.
    dt_estoque = Column(Date, nullable=True)
    recalculado_em = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PedidoStandbyMotivo(Base):
    """Por que o par (nr_pedido, cd_prod_cor) está fora de uma OR nesta rodada.

    GRÃO: o par, com UPSERT (nunca delete-then-insert) — o motivo pode mudar
    entre rodadas (ex. estoque -> crédito) sem a linha duplicar. Os 3 valores
    canônicos de `motivo` são `sem_credito`/`sem_estoque`/`furo_grade` (este
    arquivo não importa `domain/standby_motivo.py` para não acoplar
    infraestrutura a domínio; o CHECK constraint da migration é quem
    realmente impõe o vocabulário).

    `job_id` é só informativo/auditoria, sem `ForeignKey` — o job pode ser
    expurgado (mesma decisão de `pedido_processamentos`/`pedido_processamento_plan`,
    efêmeros e sujeitos a `ON DELETE CASCADE` a partir de `durable_jobs`).

    A linha é apagada quando o par finalmente vira OR ou some do recorte de
    pendentes. O wiring real (upsert, limpeza) fica para 16-04/16-05, fora do
    escopo desta plan — aqui só o schema.
    """

    __tablename__ = "pedido_standby_motivo"

    nr_pedido = Column(Integer, primary_key=True)
    cd_prod_cor = Column(String(64), primary_key=True)
    canal = Column(String(16), nullable=False)
    motivo = Column(String(16), nullable=False)
    execucoes_consecutivas = Column(
        Integer, nullable=False, default=1, server_default="1"
    )
    job_id = Column(UUID(as_uuid=True), nullable=False)
    atualizado_em = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PedidoModificacao(Base):
    """Grade editada de um produto de um pedido.

    "Grade" é conceito de produto: editar a grade altera os tamanhos de UM
    produto para UM cliente, não o pedido inteiro.
    """

    __tablename__ = "pedido_modificacoes"

    nr_pedido = Column(Integer, primary_key=True)
    cd_prod_cor = Column(String(64), primary_key=True)
    items = Column(JSONB, nullable=False)
    original_items = Column(JSONB, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class OrdemReservaLinx(Base):
    """Bandeja de saída no layout aceito pelo ERP Linx (`Tabelas_de_OR.csv`).

    Correlaciona com `OrdemReserva` via `(nr_pedido, cd_prod_cor)`, mas SEM FK:
    é uma projeção de saída para um sistema externo, não o modelo interno. Os
    campos sem fonte conhecida hoje (rastreados como INTG-03) ficam NULL.
    """

    __tablename__ = "ordens_reserva_linx"
    __table_args__ = (
        UniqueConstraint(
            "nr_pedido", "cd_prod_cor", name="uq_ordens_reserva_linx_chave"
        ),
    )

    # Controle interno.
    id = Column(Integer, primary_key=True, autoincrement=True)
    # Sem index=True: o unique (nr_pedido, cd_prod_cor) já serve de índice
    # (prefixo esquerdo do btree) — ver FaturamentoColecao em ingestao/models.py.
    nr_pedido = Column(Integer, nullable=False)
    cd_prod_cor = Column(String(64), nullable=False)
    tipo = Column(String(8), nullable=False)  # 'sem' | 'com'
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Layout Linx (Tabelas_de_OR.csv) — todas nullable; ver 01-RESEARCH.md
    # "Layout completo do CSV Linx" para tipo/fonte de cada coluna.
    nome_clifor = Column(String(255), nullable=True)
    produto = Column(String(64), nullable=True)
    cor_produto = Column(String(16), nullable=True)
    filial = Column(String(32), nullable=True)
    item = Column(Integer, nullable=True)
    pedido = Column(Integer, nullable=True)
    pedido_cor_produto = Column(String(64), nullable=True)
    romaneio = Column(String(32), nullable=True)
    caixa = Column(String(32), nullable=True)
    pedido_produto = Column(String(64), nullable=True)
    packs = Column(Integer, nullable=True)
    entrega = Column(DateTime(timezone=True), nullable=True)
    caixa_fechada = Column(Boolean, nullable=True)
    representante = Column(String(255), nullable=True)
    ipi = Column(Numeric(14, 2), nullable=True)
    preco1 = Column(Numeric(14, 2), nullable=True)
    preco2 = Column(Numeric(14, 2), nullable=True)
    preco3 = Column(Numeric(14, 2), nullable=True)
    preco4 = Column(Numeric(14, 2), nullable=True)
    desconto_item = Column(Numeric(14, 2), nullable=True)
    valor_embalado = Column(Numeric(14, 2), nullable=True)
    qtde_embalada = Column(Integer, nullable=True)
    origem = Column(String(32), nullable=True)
    mata_saldo = Column(Boolean, nullable=True)

    # Grade posicional resolvida via `produto_tamanho_posicao` (Fases 2 e 3).
    e1 = Column(Integer, nullable=True, server_default="0")
    e2 = Column(Integer, nullable=True, server_default="0")
    e3 = Column(Integer, nullable=True, server_default="0")
    e4 = Column(Integer, nullable=True, server_default="0")
    e5 = Column(Integer, nullable=True, server_default="0")
    e6 = Column(Integer, nullable=True, server_default="0")
    e7 = Column(Integer, nullable=True, server_default="0")
    e8 = Column(Integer, nullable=True, server_default="0")
    e9 = Column(Integer, nullable=True, server_default="0")
    e10 = Column(Integer, nullable=True, server_default="0")
    e11 = Column(Integer, nullable=True, server_default="0")
    e12 = Column(Integer, nullable=True, server_default="0")
    e13 = Column(Integer, nullable=True, server_default="0")
    e14 = Column(Integer, nullable=True, server_default="0")
    e15 = Column(Integer, nullable=True, server_default="0")
    e16 = Column(Integer, nullable=True, server_default="0")
    e17 = Column(Integer, nullable=True, server_default="0")
    e18 = Column(Integer, nullable=True, server_default="0")
    e19 = Column(Integer, nullable=True, server_default="0")
    e20 = Column(Integer, nullable=True, server_default="0")
    e21 = Column(Integer, nullable=True, server_default="0")
    e22 = Column(Integer, nullable=True, server_default="0")
    e23 = Column(Integer, nullable=True, server_default="0")
    e24 = Column(Integer, nullable=True, server_default="0")
    e25 = Column(Integer, nullable=True, server_default="0")
    e26 = Column(Integer, nullable=True, server_default="0")
    e27 = Column(Integer, nullable=True, server_default="0")
    e28 = Column(Integer, nullable=True, server_default="0")
    e29 = Column(Integer, nullable=True, server_default="0")
    e30 = Column(Integer, nullable=True, server_default="0")
    e31 = Column(Integer, nullable=True, server_default="0")
    e32 = Column(Integer, nullable=True, server_default="0")
    e33 = Column(Integer, nullable=True, server_default="0")
    e34 = Column(Integer, nullable=True, server_default="0")
    e35 = Column(Integer, nullable=True, server_default="0")
    e36 = Column(Integer, nullable=True, server_default="0")
    e37 = Column(Integer, nullable=True, server_default="0")
    e38 = Column(Integer, nullable=True, server_default="0")
    e39 = Column(Integer, nullable=True, server_default="0")
    e40 = Column(Integer, nullable=True, server_default="0")
    e41 = Column(Integer, nullable=True, server_default="0")
    e42 = Column(Integer, nullable=True, server_default="0")
    e43 = Column(Integer, nullable=True, server_default="0")
    e44 = Column(Integer, nullable=True, server_default="0")
    e45 = Column(Integer, nullable=True, server_default="0")
    e46 = Column(Integer, nullable=True, server_default="0")
    e47 = Column(Integer, nullable=True, server_default="0")
    e48 = Column(Integer, nullable=True, server_default="0")

    item_pedido = Column(Integer, nullable=True)
    ordem_producao = Column(String(64), nullable=True)
    licenciado_royalties = Column(Boolean, nullable=True)
    percent_desconto = Column(Numeric(6, 2), nullable=True)
    caixa_virtual = Column(String(32), nullable=True)
