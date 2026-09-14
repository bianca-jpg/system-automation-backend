from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

CanalFiltro = Literal["Todos", "Franquia", "Multimarca"]
CanalEspecifico = Literal["Franquia", "Multimarca"]
EstagioProduto = Literal["aguardando", "edicao", "historico"]
OrdenacaoProduto = Literal[
    "lastOrderAt",
    "code",
    "name",
    "totalQty",
    "ordersCount",
    "totalValue",
    "remainingWindow",
]
DirecaoOrdenacao = Literal["asc", "desc"]
StatusHistoricoFiltro = Literal[
    "Todos",
    "Com Adequação",
    "Sem Adequação",
    "Bloqueado Estoque",
    "Bloqueado Crédito",
]
ProcessamentoModo = Literal["adequar", "sem_adequar"]
ProcessamentoJobStatus = Literal[
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "skipped",
]


class CriarProcessamentoRequest(BaseModel):
    """Intenção bounded; as grades nunca trafegam no request do job."""

    model_config = ConfigDict(extra="forbid")

    mode: ProcessamentoModo
    channel: CanalFiltro


class ProcessamentoJobAccepted(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(serialization_alias="jobId")
    status: ProcessamentoJobStatus
    replayed: bool
    coalesced: bool
    status_url: str = Field(max_length=256, serialization_alias="statusUrl")
    progress_current: int = Field(ge=0, serialization_alias="progressCurrent")
    progress_total: int | None = Field(
        default=None,
        ge=0,
        serialization_alias="progressTotal",
    )


class ProcessamentoResultado(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    planned_count: int = Field(
        ge=0,
        le=100_000,
        validation_alias="plannedCount",
        serialization_alias="plannedCount",
    )
    applied_count: int = Field(
        ge=0,
        le=100_000,
        validation_alias="appliedCount",
        serialization_alias="appliedCount",
    )
    deferred_count: int = Field(
        ge=0,
        le=100_000,
        validation_alias="deferredCount",
        serialization_alias="deferredCount",
    )
    blocked_credit_count: int = Field(
        ge=0,
        le=100_000,
        validation_alias="blockedCreditCount",
        serialization_alias="blockedCreditCount",
    )


class ProcessamentoJobOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(serialization_alias="jobId")
    mode: ProcessamentoModo
    channel: CanalFiltro
    status: ProcessamentoJobStatus
    progress_current: int = Field(ge=0, serialization_alias="progressCurrent")
    progress_total: int | None = Field(
        default=None,
        ge=0,
        serialization_alias="progressTotal",
    )
    attempts: int = Field(ge=0, le=20)
    max_attempts: int = Field(ge=1, le=20, serialization_alias="maxAttempts")
    retryable: bool
    requested_at: datetime = Field(serialization_alias="requestedAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    started_at: datetime | None = Field(
        default=None,
        serialization_alias="startedAt",
    )
    finished_at: datetime | None = Field(
        default=None,
        serialization_alias="finishedAt",
    )
    deadline_at: datetime = Field(serialization_alias="deadlineAt")
    result: ProcessamentoResultado | None = None
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.:-]{0,63}$",
        serialization_alias="errorCode",
    )


class PedidoItemCardOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    qty: int
    code: str
    unit_value: float = Field(serialization_alias="unitValue")
    cd_status: str = Field(serialization_alias="cdStatus")
    stock: int = 0
    sizes: dict[str, int] | None = Field(default=None, max_length=100)

    @field_validator("sizes")
    @classmethod
    def validar_mapa_tamanhos(
        cls, value: dict[str, int] | None
    ) -> dict[str, int] | None:
        if value is None:
            return None
        if any(not key.strip() or len(key.strip()) > 16 for key in value):
            raise ValueError("chave de tamanho deve ter entre 1 e 16 caracteres")
        if any(qty < 0 for qty in value.values()):
            raise ValueError("quantidade por tamanho não pode ser negativa")
        if any(qty > 2_147_483_647 for qty in value.values()):
            raise ValueError("quantidade por tamanho excede o limite suportado")
        return {key.strip().upper(): qty for key, qty in value.items()}


class OrcamentoPedidoOut(BaseModel):
    """Orçamento ±5% do PEDIDO INTEIRO (todos os produtos, não só o da
    linha) — D-12. Embutido só nas linhas 'sem adequação' (PD-08); limite e
    restante vêm sempre de `OrcamentoPedido` (D-04/PD-09), nunca recalculados
    aqui."""

    model_config = ConfigDict(populate_by_name=True)

    nr_pedido: int = Field(gt=0, serialization_alias="nrPedido")
    limite_adicao: int = Field(ge=0, serialization_alias="limiteAdicao")
    consumido_adicao: int = Field(ge=0, serialization_alias="consumidoAdicao")
    restante_adicao: int = Field(ge=0, serialization_alias="restanteAdicao")
    limite_corte: int = Field(ge=0, serialization_alias="limiteCorte")
    consumido_corte: int = Field(ge=0, serialization_alias="consumidoCorte")
    restante_corte: int = Field(ge=0, serialization_alias="restanteCorte")


class PedidoCardOut(BaseModel):
    """Card de pedido estável usado pelas linhas de cliente de um produto."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    client: str
    value: float
    original_value_before_adequacao: float = Field(
        serialization_alias="originalValueBeforeAdequacao"
    )
    status: str
    motivo: str
    date: str | None = None
    delivery_date: str | None = Field(default=None, serialization_alias="deliveryDate")
    canal: Literal["Franquia", "Multimarca"]
    adequacao_aplicada: bool = Field(serialization_alias="adequacaoAplicada")
    adequacao_valor_ajustado: float = Field(
        serialization_alias="adequacaoValorAjustado"
    )
    items: list[PedidoItemCardOut]
    alert: str | None = None
    pedido_alterado: bool = Field(serialization_alias="pedidoAlterado")
    original_items: list[PedidoItemCardOut] = Field(serialization_alias="originalItems")
    processed_at: int | None = Field(default=None, serialization_alias="processedAt")
    aprovado: bool
    approved_at: int | None = Field(default=None, serialization_alias="approvedAt")
    origem: Literal["app", "erp", "app_erp"]
    confirmado_erp: bool = Field(serialization_alias="confirmadoErp")
    orcamento_pedido: OrcamentoPedidoOut | None = Field(
        default=None, serialization_alias="orcamentoPedido"
    )


class OrderStatsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total_orders_count: int = Field(serialization_alias="totalOrdersCount")
    liberados_count: int = Field(serialization_alias="liberadosCount")
    or_com_adequacao_count: int = Field(serialization_alias="orComAdequacaoCount")
    or_sem_adequacao_count: int = Field(serialization_alias="orSemAdequacaoCount")
    editing_order_count: int = Field(serialization_alias="editingOrderCount")
    editing_product_count: int = Field(serialization_alias="editingProductCount")
    processado_erp_count: int = Field(serialization_alias="processadoErpCount")
    pecas_bloqueadas_count: int = Field(serialization_alias="pecasBloqueadasCount")
    pecas_bloqueadas_percent: int = Field(serialization_alias="pecasBloqueadasPercent")
    bloqueados_sem_credito_count: int = Field(
        serialization_alias="bloqueadosSemCreditoCount"
    )
    liberados_percent: int = Field(serialization_alias="liberadosPercent")
    bloqueados_sem_credito_percent: int = Field(
        serialization_alias="bloqueadosSemCreditoPercent"
    )


class PedidosResumoOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stock_by_code: dict[str, dict[str, int]] = Field(serialization_alias="stockByCode")
    erp_billing: dict[str, float] = Field(serialization_alias="erpBilling")
    erp_count: dict[str, int] = Field(serialization_alias="erpCount")
    stats_by_channel: dict[str, OrderStatsOut] = Field(
        serialization_alias="statsByChannel"
    )


class AlertaOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    order_id: str | None = Field(default=None, serialization_alias="orderId")
    category: str | None = Field(default=None)
    type: Literal["error", "warning", "info"]
    title: str | None = Field(default=None)
    message: str
    time: str
    affected_count: int | None = Field(
        default=None, serialization_alias="affectedCount"
    )
    # Discriminador de origem (D-03): "negocio" cobre as 8 categorias já
    # existentes (default, sem tocar nelas); "integracao" identifica os
    # avisos de integração crítica (banco de dados/estoque). `kind` porque
    # `type` já significa severidade (error/warning/info) neste contrato.
    kind: Literal["negocio", "integracao"] = "negocio"


class AlertasPageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[AlertaOut]
    total: int
    page_size: int = Field(serialization_alias="pageSize")
    next_cursor: str | None = Field(serialization_alias="nextCursor")
    has_more: bool = Field(serialization_alias="hasMore")


class ProdutoProjectionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    channel: CanalEspecifico
    total_qty: int = Field(ge=0, serialization_alias="totalQty")
    total_value: float = Field(serialization_alias="totalValue")
    original_total_value: float = Field(serialization_alias="originalTotalValue")
    unit_value: float = Field(serialization_alias="unitValue")
    orders_count: int = Field(ge=0, serialization_alias="ordersCount")
    stock: int = Field(ge=0)
    sizes: dict[str, int] = Field(max_length=100)
    size_keys: list[str] = Field(max_length=100, serialization_alias="sizeKeys")
    last_order_at: str | None = Field(default=None, serialization_alias="lastOrderAt")
    processed_at: int | None = Field(default=None, serialization_alias="processedAt")
    remaining_window_ms: int | None = Field(
        default=None, ge=0, serialization_alias="remainingWindowMs"
    )
    adequacao_aplicada: bool = Field(serialization_alias="adequacaoAplicada")


class ProdutosPageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[ProdutoProjectionOut]
    total: int = Field(ge=0)
    page_size: int = Field(serialization_alias="pageSize")
    next_cursor: str | None = Field(serialization_alias="nextCursor")
    has_more: bool = Field(serialization_alias="hasMore")
    # Preenchidos apenas no modo numerado (`?page=`). No modo cursor saem como
    # null, entao os consumidores keyset atuais continuam validos.
    page: int | None = Field(default=None, ge=1)
    total_pages: int | None = Field(
        default=None, ge=1, serialization_alias="totalPages"
    )


class ProdutoClienteRowOut(BaseModel):
    order: PedidoCardOut
    item: PedidoItemCardOut
    version: str = Field(min_length=64, max_length=64)


class ProdutoClientesSummaryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    channel: CanalEspecifico
    total_clients: int = Field(ge=0, serialization_alias="totalClients")
    total_qty: int = Field(ge=0, serialization_alias="totalQty")
    total_value: float = Field(serialization_alias="totalValue")
    original_total_value: float = Field(serialization_alias="originalTotalValue")
    stock: int = Field(ge=0)
    size_keys: list[str] = Field(max_length=100, serialization_alias="sizeKeys")
    size_totals: dict[str, int] = Field(
        max_length=100, serialization_alias="sizeTotals"
    )


class ProdutoClientesPageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[ProdutoClienteRowOut]
    total: int = Field(ge=0)
    page_size: int = Field(serialization_alias="pageSize")
    next_cursor: str | None = Field(serialization_alias="nextCursor")
    has_more: bool = Field(serialization_alias="hasMore")
    summary: ProdutoClientesSummaryOut


class PedidoLookupOut(BaseModel):
    id: int = Field(gt=0)
    client: str = Field(min_length=1, max_length=255)
    canal: CanalEspecifico
    status: str = Field(min_length=1, max_length=64)
    motivo: str | None = Field(default=None, max_length=255)
    value: float


class PedidosLookupPageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[PedidoLookupOut]
    total: int = Field(ge=0)
    page_size: int = Field(serialization_alias="pageSize")
    next_cursor: str | None = Field(serialization_alias="nextCursor")
    has_more: bool = Field(serialization_alias="hasMore")


class ProdutoGradeChangeIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    order_id: int = Field(gt=0, le=2_147_483_647, validation_alias="orderId")
    expected_version: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        validation_alias="expectedVersion",
    )
    sizes: dict[str, StrictInt] = Field(min_length=1, max_length=100)

    @field_validator("sizes")
    @classmethod
    def validar_grade_dinamica(cls, value: dict[str, int]) -> dict[str, int]:
        normalized = {key.strip().upper(): qty for key, qty in value.items()}
        if any(not key or len(key) > 16 for key in normalized):
            raise ValueError("chave de tamanho deve ter entre 1 e 16 caracteres")
        if len(normalized) != len(value):
            raise ValueError("tamanhos duplicados após normalização")
        if any(qty < 0 or qty > 1_000_000 for qty in normalized.values()):
            raise ValueError("quantidade por tamanho deve estar entre 0 e 1000000")
        if sum(normalized.values()) <= 0:
            raise ValueError("a grade do cliente precisa ter quantidade total positiva")
        return normalized


class AlterarGradesProdutoRequest(BaseModel):
    changes: list[ProdutoGradeChangeIn] = Field(min_length=1, max_length=100)

    @field_validator("changes")
    @classmethod
    def validar_clientes_unicos(
        cls, value: list[ProdutoGradeChangeIn]
    ) -> list[ProdutoGradeChangeIn]:
        order_ids = [change.order_id for change in value]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("cada pedido pode aparecer uma única vez no lote")
        return value

    @model_validator(mode="after")
    def validar_tamanhos_do_lote(self) -> "AlterarGradesProdutoRequest":
        size_keys = {size for change in self.changes for size in change.sizes}
        if len(size_keys) > 100:
            raise ValueError("o lote pode tocar no máximo 100 tamanhos distintos")
        return self


class AlterarGradesProdutoResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    updated_count: int = Field(ge=0, serialization_alias="updatedCount")
    total_qty: int = Field(ge=0, serialization_alias="totalQty")
    total_value: float = Field(ge=0, serialization_alias="totalValue")
    approved_count: int = Field(ge=0, default=0, serialization_alias="approvedCount")


class AprovarProdutoResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    matched_count: int = Field(ge=0, serialization_alias="matchedCount")
    approved_count: int = Field(ge=0, serialization_alias="approvedCount")
    already_approved_count: int = Field(
        ge=0, serialization_alias="alreadyApprovedCount"
    )
    expired_count: int = Field(ge=0, serialization_alias="expiredCount")


class EvolucaoFaturamentoItem(BaseModel):
    """Ponto da série de evolução de faturamento por coleção × canal."""

    colecao: int
    canal: str  # 'Franquia' | 'Multimarca'
    planejado: float
    distribuido: float
