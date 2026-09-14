import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos import service
from app.modules.pedidos.application.commands import (
    AlteracaoGradeProduto,
    AlterarGradesProdutoCommand,
)
from app.modules.pedidos.application.schemas import (
    AlertasPageOut,
    AlterarGradesProdutoRequest,
    AlterarGradesProdutoResponse,
    AprovarProdutoResponse,
    CanalEspecifico,
    CanalFiltro,
    CriarProcessamentoRequest,
    DirecaoOrdenacao,
    EstagioProduto,
    EvolucaoFaturamentoItem,
    OrdenacaoProduto,
    PedidosLookupPageOut,
    PedidosResumoOut,
    ProcessamentoJobAccepted,
    ProcessamentoJobOut,
    ProcessamentoResultado,
    ProdutoClientesPageOut,
    ProdutosPageOut,
    StatusHistoricoFiltro,
)
from app.shared.config.settings import get_settings
from app.shared.database.session import get_db
from app.shared.infrastructure.rate_limit import (
    RateLimitUnavailableError,
    enforce_dual_fixed_window,
)
from app.shared.infrastructure.redis_client import get_redis
from app.shared.jobs.domain import JobIdempotencyConflict, JobReservationOutcome
from app.shared.pagination.cursor import CursorInvalidoError
from app.shared.security import CurrentUser, require_actor, require_viewer

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/pedidos", tags=["Pedidos"], dependencies=[Depends(require_viewer)]
)
alertas_router = APIRouter(
    prefix="/api/v1/alertas", tags=["Alertas"], dependencies=[Depends(require_viewer)]
)
_PROCESSING_USER_RATE_PER_MINUTE = 3
_PROCESSING_IP_RATE_PER_MINUTE = 10
_PROCESSING_RATE_WINDOW_SECONDS = 60
_RATE_LIMIT_UNAVAILABLE_RETRY_AFTER_SECONDS = 30


def _codigo_produto(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail="productCode deve conter ao menos um caractere não branco.",
        )
    return normalized


@router.get("/produtos", response_model=ProdutosPageOut)
async def listar_produtos(
    db: AsyncSession = Depends(get_db),
    stage: EstagioProduto = Query(default="aguardando"),
    channel: CanalFiltro = Query(default="Todos"),
    search: str = Query(default="", max_length=120),
    status: StatusHistoricoFiltro = Query(default="Todos"),
    sort: OrdenacaoProduto | None = Query(default=None),
    order: DirecaoOrdenacao | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=25),
) -> ProdutosPageOut:
    """Produtos agregados globalmente; ``Todos`` separa uma row por canal.

    Duas paginacoes coexistem: ``cursor`` (keyset, para scroll incremental) e
    ``page`` (offset, para a faixa numerada da tela). Sao mutuamente exclusivas.
    """
    if page is not None and cursor is not None:
        raise HTTPException(
            status_code=422,
            detail="Use cursor ou page, nunca os dois: as paginacoes sao exclusivas.",
        )
    common_sorts = {
        "lastOrderAt",
        "code",
        "name",
        "totalQty",
        "ordersCount",
        "totalValue",
    }
    allowed = set(common_sorts)
    if stage == "edicao":
        allowed.add("remainingWindow")
    selected_sort = sort or ("remainingWindow" if stage == "edicao" else "lastOrderAt")
    selected_order = order or ("asc" if stage == "edicao" else "desc")
    if selected_sort not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Ordenação {selected_sort} não é válida para o estágio {stage}.",
        )
    if stage != "historico" and status != "Todos":
        raise HTTPException(
            status_code=422,
            detail="O filtro status só é válido para o estágio histórico.",
        )
    try:
        result = await service.listar_produtos_page(
            db,
            estagio=stage,
            canal=channel,
            busca=search.strip(),
            status=status,
            ordenacao=selected_sort,
            direcao=selected_order,
            cursor=cursor,
            tamanho_pagina=page_size,
            pagina_numero=page,
        )
    except CursorInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProdutosPageOut.model_validate(result)


@router.get("/produtos/clientes", response_model=ProdutoClientesPageOut)
async def listar_clientes_produto(
    product_code: str = Query(..., alias="productCode", min_length=1, max_length=64),
    stage: EstagioProduto = Query(default="aguardando"),
    channel: CanalEspecifico = Query(...),
    status: StatusHistoricoFiltro = Query(default="Todos"),
    cursor: str | None = Query(default=None, max_length=512),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=25),
    db: AsyncSession = Depends(get_db),
) -> ProdutoClientesPageOut:
    if stage != "historico" and status != "Todos":
        raise HTTPException(
            status_code=422,
            detail="O filtro status só é válido para o estágio histórico.",
        )
    try:
        result = await service.listar_clientes_produto_page(
            db,
            codigo_produto=_codigo_produto(product_code),
            estagio=stage,
            canal=channel,
            status=status,
            cursor=cursor,
            tamanho_pagina=page_size,
        )
    except CursorInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProdutoClientesPageOut.model_validate(result)


@router.put(
    "/produtos/grades",
    response_model=AlterarGradesProdutoResponse,
    dependencies=[Depends(require_actor)],
)
async def alterar_grades_produto(
    payload: AlterarGradesProdutoRequest,
    product_code: str = Query(..., alias="productCode", min_length=1, max_length=64),
    channel: CanalEspecifico = Query(...),
    db: AsyncSession = Depends(get_db),
) -> AlterarGradesProdutoResponse:
    """Salva até 100 clientes com lock/versão e uma única transação."""
    try:
        result = await service.executar_alteracao_grades_produto(
            db,
            cd_prod_cor=_codigo_produto(product_code),
            channel=channel,
            payload=AlterarGradesProdutoCommand(
                changes=tuple(
                    AlteracaoGradeProduto(
                        order_id=change.order_id,
                        expected_version=change.expected_version,
                        sizes=dict(change.sizes),
                    )
                    for change in payload.changes
                )
            ),
        )
    except service.ProdutoBatchNaoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.OrcamentoPedidoExcedidoError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "orcamento_pedido_excedido",
                "message": str(exc),
                "nrPedido": exc.nr_pedido,
                "orcamento": exc.orcamento,
                "restanteAdicao": exc.restante_adicao,
                "restanteCorte": exc.restante_corte,
            },
        ) from exc
    except (
        service.ProcessamentoEmAndamentoError,
        service.ProdutoBatchConflitoError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AlterarGradesProdutoResponse.model_validate(result)


@router.post(
    "/produtos/aprovar",
    response_model=AprovarProdutoResponse,
    dependencies=[Depends(require_actor)],
)
async def aprovar_produto(
    product_code: str = Query(..., alias="productCode", min_length=1, max_length=64),
    channel: CanalEspecifico = Query(...),
    db: AsyncSession = Depends(get_db),
) -> AprovarProdutoResponse:
    """Aprova em lote somente o produto/canal solicitado; retry é idempotente."""
    try:
        result = await service.executar_aprovacao_produto(
            db,
            cd_prod_cor=_codigo_produto(product_code),
            channel=channel,
        )
    except service.ProdutoBatchNaoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ProcessamentoEmAndamentoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AprovarProdutoResponse.model_validate(result)


@router.get("/lookup", response_model=PedidosLookupPageOut)
async def lookup_pedidos(
    search: str = Query(default="", max_length=120),
    cursor: str | None = Query(default=None, max_length=512),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=25),
    db: AsyncSession = Depends(get_db),
) -> PedidosLookupPageOut:
    try:
        result = await service.listar_lookup_page(
            db,
            busca=search.strip(),
            cursor=cursor,
            tamanho_pagina=page_size,
        )
    except CursorInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PedidosLookupPageOut.model_validate(result)


@router.get("/resumo", response_model=PedidosResumoOut)
async def resumo_pedidos(
    include_stock: bool = Query(default=False, alias="includeStock"),
    db: AsyncSession = Depends(get_db),
) -> PedidosResumoOut:
    """Agregados autoritativos em SQL, sem carregar cards de pedido."""
    return PedidosResumoOut.model_validate(
        await service.obter_resumo(db, include_stock=include_stock)
    )


@alertas_router.get("", response_model=AlertasPageOut)
async def listar_alertas(
    db: AsyncSession = Depends(get_db),
    channel: CanalFiltro = Query(default="Todos"),
    search: str = Query(default="", max_length=120),
    cursor: str | None = Query(default=None, max_length=512),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=100),
) -> AlertasPageOut:
    try:
        result = await service.listar_alertas(
            db,
            canal=channel,
            busca=search.strip(),
            cursor=cursor,
            tamanho_pagina=page_size,
        )
    except CursorInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AlertasPageOut.model_validate(result)


@router.get("/evolucao-faturamento", response_model=list[EvolucaoFaturamentoItem])
async def evolucao_faturamento(db: AsyncSession = Depends(get_db)):
    """Série de faturamento planejado × distribuído das 3 coleções mais recentes, por canal.

    A coleção de cada pedido é definida pela `dt_emissao` (faixa do semestre civil):
    116 = jul–dez/2025, 117 = jan–jun/2026, 118 = jul/2026 até a data atual.
    Lê da tabela `faturamento_colecao` (pré-calculada na ingestão periódica do Postgres).
    O frontend filtra/soma por canal conforme o seletor do dashboard.
    """
    return await service.evolucao_faturamento(db)


@router.post(
    "/processamentos",
    response_model=ProcessamentoJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Job durável criado, reutilizado ou coalescido.",
            "headers": {
                "Location": {
                    "description": "Caminho canônico para consultar o job.",
                    "schema": {"type": "string"},
                }
            },
        },
        status.HTTP_409_CONFLICT: {
            "description": "Idempotency-Key já vinculada a outra intenção.",
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "Limite de novas intenções excedido.",
            "headers": {
                "Retry-After": {
                    "description": "Segundos até a próxima janela.",
                    "schema": {"type": "integer"},
                }
            },
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Ledger ou rate limit temporariamente indisponível.",
            "headers": {
                "Retry-After": {
                    "description": "Presente quando o rate limit está indisponível.",
                    "schema": {"type": "integer"},
                }
            },
        },
    },
)
async def criar_processamento(
    payload: CriarProcessamentoRequest,
    request: Request,
    response: Response,
    actor: Annotated[CurrentUser, Depends(require_actor)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
        ),
    ],
) -> ProcessamentoJobAccepted:
    """Registra a intenção bounded; o trabalho pesado nunca roda na request."""

    try:
        submission = await service.buscar_replay_job_processamento(
            owner_id=actor.id,
            idempotency_key=idempotency_key,
            mode=payload.mode,
            channel=payload.channel,
        )
        if submission is None:
            client_ip = request.client.host if request.client else "unknown"
            try:
                rate = await enforce_dual_fixed_window(
                    get_redis(),
                    namespace="orders_processing",
                    user_identity=str(actor.id),
                    ip_identity=client_ip,
                    secret=get_settings().jwt_secret,
                    user_limit=_PROCESSING_USER_RATE_PER_MINUTE,
                    ip_limit=_PROCESSING_IP_RATE_PER_MINUTE,
                    window_seconds=_PROCESSING_RATE_WINDOW_SECONDS,
                )
            except RateLimitUnavailableError as exc:
                cause = (
                    type(exc.__cause__).__name__
                    if exc.__cause__ is not None
                    else type(exc).__name__
                )
                logger.warning(
                    "Rate limit do processamento indisponível; error_type=%s",
                    cause,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Processamento temporariamente indisponível.",
                    headers={
                        "Retry-After": str(_RATE_LIMIT_UNAVAILABLE_RETRY_AFTER_SECONDS)
                    },
                ) from exc
            if not rate.allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Limite de processamentos excedido. Tente novamente mais tarde.",
                    headers={"Retry-After": str(rate.retry_after)},
                )
            submission = await service.solicitar_job_processamento(
                owner_id=actor.id,
                idempotency_key=idempotency_key,
                mode=payload.mode,
                channel=payload.channel,
            )
    except JobIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key já utilizada com outra operação.",
        ) from exc
    except service.InvalidProcessingRequest as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Intenção de processamento inválida.",
        ) from exc
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.error(
            "Registro de processamento indisponível; error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Processamento temporariamente indisponível.",
        ) from exc

    reservation = submission.reservation
    job = reservation.job
    status_url = f"/api/v1/pedidos/processamentos/{job.id}"
    response.headers["Location"] = status_url
    return ProcessamentoJobAccepted(
        job_id=job.id,
        status=job.status.value,
        replayed=reservation.outcome is JobReservationOutcome.REPLAYED,
        coalesced=reservation.outcome is JobReservationOutcome.COALESCED,
        status_url=status_url,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
    )


@router.get(
    "/processamentos/{jobId}",
    response_model=ProcessamentoJobOut,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Job não encontrado neste bounded context.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Status do job temporariamente indisponível.",
        },
    },
)
async def obter_processamento(
    job_id: Annotated[UUID, Path(alias="jobId")],
    actor: Annotated[CurrentUser, Depends(require_actor)],
) -> ProcessamentoJobOut:
    """Consulta global por capability; coalescência pode cruzar atores autorizados."""

    del actor
    try:
        current = await service.obter_job_processamento(job_id)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job de processamento não encontrado.",
            )
        job, spec = current
        if job.deadline_at is None:
            raise RuntimeError("processing_job_deadline_missing")
        safe_result = service.resultado_publico_processamento(job)
        return ProcessamentoJobOut(
            job_id=job.id,
            mode=spec.mode.value,
            channel=spec.channel.value,
            status=job.status.value,
            progress_current=job.progress_current,
            progress_total=job.progress_total,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            retryable=job.retryable,
            requested_at=job.requested_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            deadline_at=job.deadline_at,
            result=(
                ProcessamentoResultado.model_validate(safe_result)
                if safe_result is not None
                else None
            ),
            error_code=job.error_code,
        )
    except HTTPException:
        raise
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.error(
            "Consulta de processamento indisponível; error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Status do processamento temporariamente indisponível.",
        ) from exc


@router.post("/{nr_pedido}/aprovar", dependencies=[Depends(require_actor)])
async def aprovar_pedido(
    nr_pedido: int = Path(..., gt=0, le=2_147_483_647),
    cd_prod_cor: str | None = Query(
        None,
        min_length=1,
        max_length=64,
        description="Produto a aprovar; omitido aprova todos os do pedido",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Aprova manualmente OR(s) antes das 24h, movendo-as para o histórico.

    Como a OR é por produto, `cd_prod_cor` aprova apenas aquela; omitido aprova
    todos os produtos do pedido (o que a linha de pedido da tela faz). Idempotente.
    """
    try:
        ok = await service.executar_aprovacao(db, nr_pedido, cd_prod_cor)
    except service.ProcessamentoEmAndamentoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma ordem de reserva encontrada para este pedido.",
        )
    return {"status": "success", "message": f"Pedido {nr_pedido} aprovado."}
