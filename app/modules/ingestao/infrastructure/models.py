"""Tabelas que recebem os dados ingeridos do Databricks.

Snapshot do estado atual das views do Databricks (full refresh a cada
sincronização). A lógica de de-para espelha os antigos converter_pedidos.py /
converter_estoque.py.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class Pedido(Base):
    __tablename__ = "pedidos"
    __table_args__ = (
        UniqueConstraint(
            "nr_pedido", "cd_prod_cor", "sg_tamanho", name="uq_pedido_item"
        ),
        Index(
            "ix_pedidos_cd_prod_cor_nr_pedido",
            "cd_prod_cor",
            "nr_pedido",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nr_pedido: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    sg_tamanho: Mapped[str] = mapped_column(String(16), nullable=False)
    ds_grupo: Mapped[str] = mapped_column(String(128), nullable=False)
    qt_entregar: Mapped[int] = mapped_column(Integer, nullable=False)
    vl_liquido: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status_credito: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ds_produto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data: Mapped[str | None] = mapped_column(String(64), nullable=True)
    indica_blacklist: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PedidoProcessadoErp(Base):
    """Pedidos JÁ processados no ERP (reserva feita ou embalado), vindos do
    Databricks — fonte de verdade EXTERNA sobre o que foi processado de fato.

    Não confundir com `pedidos_processados` (PedidoProcessado, módulo pedidos),
    que marca o que foi processado pelos BOTÕES deste app (ainda sem conexão com
    o ERP). Aqui só entram linhas efetivamente processadas (indica_reserva OU
    indica_embalado = true) e do ano configurado (INGESTAO_ANO_HISTORICO); presença
    na tabela == processado. Full refresh a cada sync, como pedidos/estoque.
    """

    __tablename__ = "pedidos_processados_erp"
    __table_args__ = (
        UniqueConstraint(
            "nr_pedido", "cd_prod_cor", "sg_tamanho", name="uq_proc_erp_item"
        ),
        Index(
            "ix_pedidos_processados_erp_cd_prod_cor_nr_pedido",
            "cd_prod_cor",
            "nr_pedido",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nr_pedido: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    sg_tamanho: Mapped[str] = mapped_column(String(16), nullable=False)
    ds_grupo: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canal: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ds_produto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # dt_emissao (data real do pedido)
    qt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vl_liquido: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    indica_reserva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    indica_embalado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PedidoProdutoRead(Base):
    """Projeção persistida por par para filas, histórico e lookup bounded.

    É totalmente derivada dos snapshots de tamanhos. ERP tem precedência para
    um par ainda publicado pelas duas fontes; o full refresh a reconstrói na
    mesma transação das tabelas-base.
    """

    __tablename__ = "pedido_produto_read"
    __table_args__ = (
        CheckConstraint("source IN ('pending', 'erp')", name="ck_ppr_source"),
        CheckConstraint("nr_pedido > 0", name="ck_ppr_nr_pedido_positivo"),
        CheckConstraint("length(trim(cd_prod_cor)) BETWEEN 1 AND 64", name="ck_ppr_cd"),
        CheckConstraint("canal IN ('Franquia', 'Multimarca')", name="ck_ppr_canal"),
        CheckConstraint("qty >= 0", name="ck_ppr_qty_nao_negativa"),
        CheckConstraint(
            "source = 'erp' OR value >= 0",
            name="ck_ppr_value_nao_negativo",
        ),
        CheckConstraint(
            "jsonb_typeof(sizes) = 'object' AND "
            "jsonb_array_length(jsonb_path_query_array(sizes, '$.keyvalue()')) "
            "BETWEEN 1 AND 100",
            name="ck_ppr_sizes_bounded",
        ),
        Index(
            "ix_ppr_source_product_channel",
            "source",
            "cd_prod_cor",
            "canal",
            "nr_pedido",
        ),
    )

    source: Mapped[str] = mapped_column(String(8), primary_key=True)
    nr_pedido: Mapped[int] = mapped_column(Integer, primary_key=True)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), primary_key=True)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canal: Mapped[str] = mapped_column(String(32), nullable=False)
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    order_date_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qty: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sizes: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    credito_bloqueado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    indica_reserva: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    indica_embalado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    indica_blacklist: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class Estoque(Base):
    """Foto de estoque do dia vinda do Databricks (full refresh).

    A view de origem já filtra `dt_estoque = current_date()`, então cada dia é uma
    foto CONGELADA: ela não reflete reservas feitas ao longo do dia. Quem enxerga
    o disponível descontando as ORs geradas pelo app é `estoque_virtual`
    (app/modules/pedidos/models.py) — esta tabela é a foto crua, imutável entre
    sincronizações.
    """

    __tablename__ = "estoque"
    __table_args__ = (
        UniqueConstraint("cd_prod_cor", "sg_tamanho", "canal", name="uq_estoque_chave"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    sg_tamanho: Mapped[str] = mapped_column(String(16), nullable=False)
    canal: Mapped[str] = mapped_column(String(32), nullable=False)
    qt_disponivel: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Data da foto na origem. FORA do unique: o full refresh mantém uma foto por
    # vez. Nullable para dispensar backfill na migration — o próximo sync
    # preenche. É a âncora de reset do estoque virtual: quando esta data avança,
    # as reservas virtuais do dia anterior deixam de ser descontadas.
    dt_estoque: Mapped[date | None] = mapped_column(Date, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FaturamentoColecao(Base):
    """Agregado pré-calculado do ERP por semestre × canal (3 coleções × 2 canais = ~6 linhas).

    Preenchido pela ingestão periódica (sincroniza_pedidos_processados_erp).
    Fonte de verdade para o gráfico de evolução do dashboard (sem query ao Databricks).
    """

    __tablename__ = "faturamento_colecao"
    __table_args__ = (
        UniqueConstraint("colecao", "canal", name="uq_faturamento_colecao_canal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Sem index=True: o unique (colecao, canal) já serve de índice para as
    # consultas por coleção (prefixo esquerdo do btree).
    colecao: Mapped[int] = mapped_column(Integer, nullable=False)
    canal: Mapped[str] = mapped_column(String(32), nullable=False)
    vl_planejado: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    vl_distribuido: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProdutoTamanhoPosicao(Base):
    """Referência tamanho -> posição da grade Linx.

    Vem da view Databricks `programa_estagio.refined.system_automation_prod_tamanho_ref`,
    em full refresh a cada sync, como as demais fontes deste módulo. Sem ela não
    há como preencher as posições `e1..e48` de `ordens_reserva_linx` (Fases 2 e 3
    deste milestone).
    """

    __tablename__ = "produto_tamanho_posicao"
    __table_args__ = (
        UniqueConstraint(
            "cd_prod_cor", "sg_tamanho", name="uq_produto_tamanho_posicao_chave"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Sem index=True: o unique (cd_prod_cor, sg_tamanho) já serve de índice
    # (prefixo esquerdo do btree).
    cd_prod_cor: Mapped[str] = mapped_column(String(64), nullable=False)
    sg_tamanho: Mapped[str] = mapped_column(String(16), nullable=False)
    nr_posicao: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
