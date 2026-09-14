from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.modules.ingestao.infrastructure.http.api_leitura import (
    obter_faturamento_colecao,
)
from app.modules.pedidos.application import consultas as consultas_app
from app.modules.pedidos.application.casos_uso import (
    ProcessamentoEmAndamentoError,
    ProdutoBatchConflitoError,
    ProdutoBatchNaoEncontradoError,
)
from app.modules.pedidos.application.casos_uso import (
    executar_alteracao_grades_produto as _executar_alteracao_grades_produto,
)
from app.modules.pedidos.application.casos_uso import (
    executar_aprovacao as _executar_aprovacao,
)
from app.modules.pedidos.application.casos_uso import (
    executar_aprovacao_produto as _executar_aprovacao_produto,
)
from app.modules.pedidos.domain.estoque_virtual import calcular_estoque_virtual
from app.modules.pedidos.domain.grade_linx import converter_grade_para_posicoes
from app.modules.pedidos.domain.motor_adequacao import (
    adequar_grade_produto,
    agrupar_por_produto,
    aplicar_tudo_ou_nada,
    is_sem_credito,
    marcar_stand_by,
    processar_pedidos,
)
from app.modules.pedidos.domain.orcamento_edicao import OrcamentoPedidoExcedidoError
from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx
from app.modules.pedidos.domain.value_objects import (
    canal_bucket,
    get_tamanho_idx,
)
from app.modules.pedidos.infrastructure.repositorio_consultas import (
    SqlPedidosReadRepository,
)
from app.modules.pedidos.infrastructure.repositorio_estoque_virtual import (
    carregar_estoque,
    recalcular_estoque_virtual,
)
from app.modules.pedidos.infrastructure.repositorio_ingestao_readmodel import (
    carregar_estoque_fisico,
    carregar_pedidos_itens,
    carregar_processados_erp,
)
from app.modules.pedidos.infrastructure.repositorio_ordens import salvar_ordens_reserva
from app.modules.pedidos.infrastructure.repositorio_ordens_linx import (
    salvar_linhas_linx,
)
from app.modules.pedidos.infrastructure.write_adapter import (
    SqlAlchemyPedidosWriteUnitOfWork,
)
from app.modules.pedidos.processing.application.service import (
    claim_processing_job,
    get_processing_status,
    mark_processing_failure,
    public_processing_result,
    run_claimed_processing_job,
    submit_processing_job,
)
from app.modules.pedidos.processing.domain import (
    InvalidProcessingRequest,
    ProcessingChannel,
    ProcessingMode,
    ProcessingRequest,
    new_processing_job,
)
from app.modules.pedidos.processing.infrastructure.adapters import (
    SqlAlchemyProcessingPlannerSource,
    SqlAlchemyProcessingRealtime,
)
from app.modules.pedidos.processing.infrastructure.repository_read import (
    SqlAlchemyProcessingRepository,
)
from app.modules.pedidos.processing.infrastructure.repository_writer import (
    SqlAlchemyProcessingWriter,
)
from app.modules.pedidos.processing.infrastructure.session_lock import (
    PostgresOrderMutationSessionLock,
    SqlAlchemyProcessingLeaseKeeper,
)
from app.modules.pedidos.processing.infrastructure.unit_of_work import (
    SqlAlchemyProcessingUnitOfWork,
)
from app.shared.database.session import async_session_factory
from app.shared.jobs.domain import DurableJob, JobSubmission, utc_now
from app.shared.jobs.infrastructure.repository import SqlAlchemyDurableJobRepository
from app.workers.durable_job_dispatcher import CeleryDurableJobDispatcher


def _pedido_processamento_request(*, mode: str, channel: str) -> ProcessingRequest:
    try:
        return ProcessingRequest(
            mode=ProcessingMode(mode),
            channel=ProcessingChannel(channel),
        )
    except ValueError:
        raise InvalidProcessingRequest("processing_request_invalid") from None


async def registrar_job_processamento(
    db,
    *,
    owner_id: int,
    idempotency_key: str,
    mode: str,
    channel: str,
) -> JobSubmission:
    return await submit_processing_job(
        jobs=SqlAlchemyDurableJobRepository(db),
        processing=SqlAlchemyProcessingRepository(db),
        unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
        dispatcher=CeleryDurableJobDispatcher(),
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request=_pedido_processamento_request(mode=mode, channel=channel),
    )


async def solicitar_job_processamento(
    *,
    owner_id: int,
    idempotency_key: str,
    mode: str,
    channel: str,
) -> JobSubmission:
    async with async_session_factory() as db:
        return await registrar_job_processamento(
            db,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            mode=mode,
            channel=channel,
        )


async def buscar_replay_job_processamento(
    *,
    owner_id: int,
    idempotency_key: str,
    mode: str,
    channel: str,
) -> JobSubmission | None:
    """Preflight read-only para replay persistido ignorar rate limit/Redis."""

    proposed = new_processing_job(
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        request=_pedido_processamento_request(mode=mode, channel=channel),
        requested_at=utc_now(),
    )
    async with async_session_factory() as db:
        reservation = await SqlAlchemyDurableJobRepository(db).find_idempotent(proposed)
    if reservation is None:
        return None
    return JobSubmission(reservation=reservation, broker_enqueued=False)


async def obter_job_processamento(
    job_id: UUID,
):
    async with async_session_factory() as db:
        return await get_processing_status(
            jobs=SqlAlchemyDurableJobRepository(db),
            processing=SqlAlchemyProcessingRepository(db),
            job_id=job_id,
        )


def resultado_publico_processamento(job: DurableJob) -> dict[str, int] | None:
    return public_processing_result(job)


async def marcar_falha_job_processamento(
    db,
    *,
    job_id: UUID,
    worker_id: str,
    error_code: str,
    retryable: bool,
) -> DurableJob | None:
    return await mark_processing_failure(
        job_id=job_id,
        worker_id=worker_id,
        jobs=SqlAlchemyDurableJobRepository(db),
        unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
        error_code=error_code,
        retryable=retryable,
    )


async def executar_job_processamento(
    *,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    job_id: UUID,
    worker_id: str,
) -> DurableJob | None:
    """Try-lock global -> claim na sessão presa -> plano/checkpoints duráveis."""

    async with PostgresOrderMutationSessionLock(engine) as lock_guard:
        db = lock_guard.session
        claimed = await claim_processing_job(
            job_id=job_id,
            worker_id=worker_id,
            jobs=SqlAlchemyDurableJobRepository(db),
            lock_guard=lock_guard,
            unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
        )
        if claimed is None:
            return None
        return await run_claimed_processing_job(
            claimed=claimed,
            worker_id=worker_id,
            jobs=SqlAlchemyDurableJobRepository(db),
            processing=SqlAlchemyProcessingRepository(db),
            planner=SqlAlchemyProcessingPlannerSource(db),
            writer=SqlAlchemyProcessingWriter(db),
            realtime=SqlAlchemyProcessingRealtime(db),
            lease_keeper=SqlAlchemyProcessingLeaseKeeper(session_factory),
            lock_guard=lock_guard,
            unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
        )


async def executar_alteracao_grades_produto(
    db, *, cd_prod_cor: str, channel: str, payload
) -> dict:
    return await _executar_alteracao_grades_produto(
        SqlAlchemyPedidosWriteUnitOfWork(db),
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        payload=payload,
    )


async def executar_aprovacao_produto(db, *, cd_prod_cor: str, channel: str) -> dict:
    return await _executar_aprovacao_produto(
        SqlAlchemyPedidosWriteUnitOfWork(db),
        cd_prod_cor=cd_prod_cor,
        channel=channel,
    )


async def executar_aprovacao(
    db, nr_pedido: int, cd_prod_cor: str | None = None
) -> bool:
    return await _executar_aprovacao(
        SqlAlchemyPedidosWriteUnitOfWork(db), nr_pedido, cd_prod_cor
    )


async def obter_resumo(db, *, include_stock: bool = False) -> dict:
    return await consultas_app.obter_resumo(
        SqlPedidosReadRepository(db), include_stock=include_stock
    )


async def listar_alertas(db, *, canal, busca, cursor, tamanho_pagina) -> dict:
    return await consultas_app.listar_alertas(
        SqlPedidosReadRepository(db),
        canal=canal,
        busca=busca,
        cursor=cursor,
        tamanho_pagina=tamanho_pagina,
    )


async def listar_produtos_page(
    db,
    *,
    estagio,
    canal,
    busca,
    status,
    ordenacao,
    direcao,
    cursor,
    tamanho_pagina,
    pagina_numero=None,
) -> dict:
    return await consultas_app.listar_produtos(
        SqlPedidosReadRepository(db),
        estagio=estagio,
        canal=canal,
        busca=busca,
        status=status,
        ordenacao=ordenacao,
        direcao=direcao,
        cursor=cursor,
        tamanho_pagina=tamanho_pagina,
        pagina_numero=pagina_numero,
    )


async def listar_clientes_produto_page(
    db,
    *,
    codigo_produto,
    estagio,
    canal,
    status,
    cursor,
    tamanho_pagina,
) -> dict:
    return await consultas_app.listar_clientes_produto(
        SqlPedidosReadRepository(db),
        codigo_produto=codigo_produto,
        estagio=estagio,
        canal=canal,
        status=status,
        cursor=cursor,
        tamanho_pagina=tamanho_pagina,
    )


async def listar_lookup_page(db, *, busca, cursor, tamanho_pagina) -> dict:
    return await consultas_app.listar_lookup(
        SqlPedidosReadRepository(db),
        busca=busca,
        cursor=cursor,
        tamanho_pagina=tamanho_pagina,
    )


async def evolucao_faturamento(db) -> list[dict]:
    """Série de faturamento planejado × distribuído das 3 coleções mais recentes,
    por canal. Lê do Postgres (pré-calculado na ingestão)."""
    return await obter_faturamento_colecao(db)


__all__ = [
    # casos de uso (chamados por routes.py)
    "registrar_job_processamento",
    "solicitar_job_processamento",
    "buscar_replay_job_processamento",
    "obter_job_processamento",
    "resultado_publico_processamento",
    "marcar_falha_job_processamento",
    "executar_job_processamento",
    "listar_alertas",
    "listar_produtos_page",
    "listar_clientes_produto_page",
    "listar_lookup_page",
    "obter_resumo",
    "evolucao_faturamento",
    "executar_alteracao_grades_produto",
    "executar_aprovacao",
    "executar_aprovacao_produto",
    "ProcessamentoEmAndamentoError",
    "ProdutoBatchConflitoError",
    "ProdutoBatchNaoEncontradoError",
    "OrcamentoPedidoExcedidoError",
    "InvalidProcessingRequest",
    # motor de adequação (consumido por test_pedidos_motor.py)
    "adequar_grade_produto",
    "agrupar_por_produto",
    "aplicar_tudo_ou_nada",
    "is_sem_credito",
    "marcar_stand_by",
    "processar_pedidos",
    "canal_bucket",
    "get_tamanho_idx",
    # conversão grade->posições Linx (consumida por test_grade_linx.py e pela Fase 3)
    "converter_grade_para_posicoes",
    # estoque virtual (PLACEBO — ver EstoqueVirtual em models.py)
    "calcular_estoque_virtual",
    "recalcular_estoque_virtual",
    # leitura cross-context (consumida por test_ingestao_api_leitura.py)
    "carregar_estoque",  # VIRTUAL: foto menos as ORs já geradas
    "carregar_estoque_fisico",  # foto crua do Databricks
    "carregar_pedidos_itens",
    "carregar_processados_erp",
    # escrita direta na camada de serviço (consumida por test_pedidos_routes.py)
    "salvar_ordens_reserva",
    # função pública de (re)gravar a linha Linx de um par — D-02a, chamável
    # de fora dos 2 fluxos de geração (ex.: futura edição manual, v1.2)
    "montar_linha_linx",
    "salvar_linhas_linx",
]
