"""Casos de uso da ingestão Databricks -> PostgreSQL.

O fluxo completo tem duas fases deliberadas:

1. coleta e normalização das cinco fontes, sem lock do estado de pedidos;
2. swap PostgreSQL curto e atômico sob o lock compartilhado de mutações.

Um lock de job independente é mantido desde antes da coleta para coalescer duas
sincronizações. As dependências concretas são fornecidas pelo composition root
``app.modules.ingestao.service`` através dos ports da aplicação.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

from app.modules.ingestao.application.ports import (
    IngestionJobLock,
    IngestionSourceReader,
    RealtimeSnapshotPort,
    SnapshotRepository,
)
from app.modules.ingestao.application.snapshot import (
    EstoquePreparado,
    FaturamentoPreparado,
    PedidosPreparados,
    ProcessadosErpPreparados,
    ReferenciaTamanhosPreparada,
    SnapshotPreparado,
)
from app.modules.ingestao.domain.agregacao import (
    FaturamentoOrigemInvalidaError,
    agregar_estoque,
    agregar_itens_pedidos,
    agregar_itens_processados_erp,
    agregar_referencia_tamanhos,
    processar_faturamento_colecao,
)

logger = logging.getLogger(__name__)

_MIN_SWAP_BUDGET_SECONDS = 30.0


class SincronizacaoEmAndamentoError(RuntimeError):
    """Outra sincronização ou mutação do estado de pedidos está em andamento."""


class SincronizacaoTimeoutError(TimeoutError):
    """O snapshot excedeu seu orçamento total antes de concluir com segurança."""


def _dt_foto_estoque(datas: frozenset[date]) -> date | None:
    if not datas:
        logger.warning("Estoque: nenhuma dt_estoque válida nas linhas lidas.")
        return None
    if len(datas) > 1:
        logger.warning(
            "Estoque: %d datas distintas na mesma leitura (%s) - a view deveria "
            "trazer só a foto do dia; usando a mais recente.",
            len(datas),
            sorted(datas),
        )
    dt_foto = max(datas)
    hoje = datetime.now(UTC).date()
    if dt_foto != hoje:
        logger.warning(
            "Estoque: foto de %s, não de hoje (%s) - o job do Databricks pode "
            "estar atrasado; a adequação vai usar estoque desatualizado.",
            dt_foto,
            hoje,
        )
    return dt_foto


async def _preparar_pedidos(source: IngestionSourceReader) -> PedidosPreparados:
    linhas = await source.pedidos_em_aberto()
    agrupado, descartadas = agregar_itens_pedidos(linhas)
    preparado = PedidosPreparados(
        itens=tuple(agrupado.values()),
        linhas_brutas=len(linhas),
        descartadas=descartadas,
        substituir=bool(agrupado),
    )
    if not preparado.substituir:
        logger.warning(
            "Pedidos: 0 itens válidos (%d linhas brutas, %d descartadas) - "
            "mantendo snapshot anterior e marcando a leitura como incompleta.",
            preparado.linhas_brutas,
            preparado.descartadas,
        )
    return preparado


async def _preparar_estoque(source: IngestionSourceReader) -> EstoquePreparado:
    linhas = await source.estoque()
    agregado, diagnostico = agregar_estoque(linhas)
    preparado = EstoquePreparado(
        agregado=agregado,
        linhas_brutas=len(linhas),
        diagnostico=diagnostico,
        dt_foto=_dt_foto_estoque(diagnostico.datas) if agregado else None,
        substituir=bool(agregado),
    )
    if not preparado.substituir:
        logger.warning(
            "Estoque: 0 chaves válidas (%d linhas brutas, %d ignoradas, %d sem "
            "disponível) - mantendo a foto anterior de estoque.",
            preparado.linhas_brutas,
            diagnostico.ignoradas,
            diagnostico.sem_disponivel,
        )
    elif diagnostico.duplicadas:
        logger.warning(
            "Estoque: %d colisão(ões) de chave (canal/produto/tamanho) - a origem "
            "deveria entregar 1 linha por chave; a granularidade pode ter mudado.",
            diagnostico.duplicadas,
        )
    return preparado


async def _preparar_processados_erp(
    source: IngestionSourceReader,
) -> ProcessadosErpPreparados:
    linhas = await source.pedidos_processados_erp()
    agrupado, descartadas = agregar_itens_processados_erp(linhas)
    preparado = ProcessadosErpPreparados(
        itens=tuple(agrupado.values()),
        linhas_brutas=len(linhas),
        descartadas=descartadas,
        substituir=bool(agrupado),
    )
    if not preparado.substituir:
        logger.warning(
            "Pedidos processados (ERP): 0 itens válidos (%d linhas brutas, "
            "%d descartadas) - mantendo snapshot anterior.",
            preparado.linhas_brutas,
            preparado.descartadas,
        )
    return preparado


async def _preparar_faturamento(
    source: IngestionSourceReader,
) -> FaturamentoPreparado:
    linhas = await source.faturamento_colecao()
    try:
        processados, _diagnostico = processar_faturamento_colecao(linhas)
    except FaturamentoOrigemInvalidaError as exc:
        diagnostico = exc.diagnostico
        logger.error(
            "Faturamento por coleção rejeitado: linhas=%d descartadas=%d "
            "categorias=%s; mantendo snapshot anterior.",
            diagnostico.linhas,
            diagnostico.descartadas,
            ",".join(diagnostico.razoes),
        )
        raise
    itens = tuple(processados)
    preparado = FaturamentoPreparado(
        itens=itens,
        linhas_brutas=len(linhas),
        substituir=bool(itens),
    )
    if not preparado.substituir:
        logger.warning(
            "Faturamento por coleção: 0 pontos válidos (%d linhas brutas) - "
            "mantendo snapshot anterior do dashboard.",
            preparado.linhas_brutas,
        )
    return preparado


async def _preparar_referencia(
    source: IngestionSourceReader,
) -> ReferenciaTamanhosPreparada:
    linhas = await source.referencia_tamanhos()
    agrupado, descartadas, conflitos = agregar_referencia_tamanhos(linhas)
    preparado = ReferenciaTamanhosPreparada(
        itens=tuple(agrupado.values()),
        linhas_brutas=len(linhas),
        descartadas=descartadas,
        conflitos_por_produto=conflitos,
        substituir=bool(agrupado),
    )
    if not preparado.substituir:
        logger.warning(
            "Referência tamanho->posição: 0 linhas válidas (%d linhas brutas) - "
            "mantendo snapshot anterior de produto_tamanho_posicao.",
            preparado.linhas_brutas,
        )
    return preparado


async def _coletar_snapshot(source: IngestionSourceReader) -> SnapshotPreparado:
    """Lê e agrega uma fonte por vez; linhas brutas saem de escopo imediatamente."""

    pedidos = await _preparar_pedidos(source)
    estoque = await _preparar_estoque(source)
    processados_erp = await _preparar_processados_erp(source)
    faturamento = await _preparar_faturamento(source)
    referencia = await _preparar_referencia(source)
    return SnapshotPreparado(
        pedidos=pedidos,
        estoque=estoque,
        processados_erp=processados_erp,
        faturamento=faturamento,
        referencia=referencia,
    )


async def _persistir_pedidos(
    repo: SnapshotRepository, preparado: PedidosPreparados
) -> int:
    if not preparado.substituir:
        return await repo.count_pedidos()
    await repo.replace_pedidos(preparado.itens)
    logger.info(
        "Pedidos sincronizados: %d itens (%d linhas brutas, %d descartadas).",
        len(preparado.itens),
        preparado.linhas_brutas,
        preparado.descartadas,
    )
    return len(preparado.itens)


async def _persistir_estoque(
    repo: SnapshotRepository, preparado: EstoquePreparado
) -> int:
    if not preparado.substituir:
        return await repo.count_estoque()
    await repo.replace_estoque(preparado.agregado, dt_estoque=preparado.dt_foto)
    diagnostico = preparado.diagnostico
    logger.info(
        "Estoque sincronizado: %d chaves (cd/tam/canal), foto de %s (%d linhas "
        "brutas, %d ignoradas, %d sem disponível).",
        len(preparado.agregado),
        preparado.dt_foto or "data desconhecida",
        preparado.linhas_brutas,
        diagnostico.ignoradas,
        diagnostico.sem_disponivel,
    )
    return len(preparado.agregado)


async def _persistir_processados_erp(
    repo: SnapshotRepository, preparado: ProcessadosErpPreparados
) -> int:
    if not preparado.substituir:
        return await repo.count_processados_erp()
    await repo.replace_processados_erp(preparado.itens)
    logger.info(
        "Pedidos processados (ERP) sincronizados: %d itens (%d linhas brutas, "
        "%d descartadas).",
        len(preparado.itens),
        preparado.linhas_brutas,
        preparado.descartadas,
    )
    return len(preparado.itens)


async def _persistir_faturamento(
    repo: SnapshotRepository, preparado: FaturamentoPreparado
) -> int:
    if not preparado.substituir:
        return await repo.count_faturamento()
    await repo.replace_faturamento(preparado.itens)
    logger.info(
        "Faturamento por coleção sincronizado: %d pontos (%d linhas brutas).",
        len(preparado.itens),
        preparado.linhas_brutas,
    )
    return len(preparado.itens)


async def _persistir_referencia(
    repo: SnapshotRepository, preparado: ReferenciaTamanhosPreparada
) -> int:
    if not preparado.substituir:
        return await repo.count_referencia_tamanhos()
    await repo.replace_referencia_tamanhos(preparado.itens)
    for cd_prod_cor, avisos in preparado.conflitos_por_produto.items():
        logger.warning(
            "produto %s: conflito de posição na referência: %s", cd_prod_cor, avisos
        )
    logger.info(
        "Referência tamanho->posição sincronizada: %d itens (%d linhas brutas, "
        "%d descartadas).",
        len(preparado.itens),
        preparado.linhas_brutas,
        preparado.descartadas,
    )
    return len(preparado.itens)


async def sincronizar_pedidos(
    source: IngestionSourceReader, repo: SnapshotRepository
) -> int:
    return await _persistir_pedidos(repo, await _preparar_pedidos(source))


async def sincronizar_estoque(
    source: IngestionSourceReader, repo: SnapshotRepository
) -> int:
    return await _persistir_estoque(repo, await _preparar_estoque(source))


async def sincronizar_pedidos_processados_erp(
    source: IngestionSourceReader, repo: SnapshotRepository
) -> int:
    return await _persistir_processados_erp(
        repo, await _preparar_processados_erp(source)
    )


async def sincronizar_faturamento_colecao(
    source: IngestionSourceReader, repo: SnapshotRepository
) -> int:
    return await _persistir_faturamento(repo, await _preparar_faturamento(source))


async def sincronizar_referencia_tamanhos(
    source: IngestionSourceReader, repo: SnapshotRepository
) -> int:
    return await _persistir_referencia(repo, await _preparar_referencia(source))


async def _registrar_novidades_realtime(
    realtime: RealtimeSnapshotPort,
    *,
    orders_snapshot_complete: bool = True,
) -> None:
    pedidos_produtos, alertas, historico_erp = await realtime.load_keys()
    pedidos_por_chave = {
        f"{nr_pedido}:{cd_prod_cor}": nr_pedido
        for nr_pedido, cd_prod_cor in pedidos_produtos
    }
    novos_produtos = (
        await realtime.observe_entities("orders", pedidos_por_chave)
        if orders_snapshot_complete
        else []
    )

    alertas_por_chave = {
        f"{nr_pedido}:{alert_type}": (nr_pedido, alert_type)
        for nr_pedido, alert_type in alertas
    }
    novos_alertas = (
        await realtime.observe_active_entities(
            "alerts",
            alertas_por_chave,
            allow_empty_snapshot=True,
        )
        if orders_snapshot_complete
        else []
    )

    novos_por_pedido: dict[int, int] = {}
    for product_key in novos_produtos:
        nr_pedido = pedidos_por_chave[product_key]
        novos_por_pedido[nr_pedido] = novos_por_pedido.get(nr_pedido, 0) + 1
    if novos_produtos:
        await realtime.enqueue(
            topic="orders",
            event_type="orders.changed.v1",
            payload={
                "newOrderCount": len(novos_por_pedido),
                "newProductCount": len(novos_produtos),
                "reason": "databricks_sync",
            },
        )

    if novos_alertas:
        await realtime.enqueue(
            topic="alerts",
            event_type="alerts.changed.v1",
            payload={
                "newAlertCount": len(novos_alertas),
                "reason": "databricks_sync",
            },
        )

    chaves_historico = {
        f"{nr_pedido}:{cd_prod_cor}" for nr_pedido, cd_prod_cor in historico_erp
    }
    novos_historicos = await realtime.observe_entities("history", chaves_historico)
    if novos_historicos:
        await realtime.enqueue(
            topic="history",
            event_type="history.changed",
            payload={"count": len(novos_historicos), "reason": "erp_sync"},
        )

    if novos_produtos or novos_alertas or novos_historicos:
        logger.info(
            "Novidades realtime anexadas ao outbox: %d produto(s) em %d pedido(s), "
            "%d alerta(s), %d item(ns) histórico(s).",
            len(novos_produtos),
            len(novos_por_pedido),
            len(novos_alertas),
            len(novos_historicos),
        )


async def _aplicar_snapshot(
    repo: SnapshotRepository,
    realtime: RealtimeSnapshotPort,
    preparado: SnapshotPreparado,
) -> dict[str, int]:
    if not await repo.acquire_mutation_lock():
        await repo.rollback()
        raise SincronizacaoEmAndamentoError(
            "Estado de pedidos permaneceu ocupado por 12 s; "
            "a sincronização coletada não foi aplicada."
        )

    try:
        pedidos = await _persistir_pedidos(repo, preparado.pedidos)
        estoque = await _persistir_estoque(repo, preparado.estoque)
        processados_erp = await _persistir_processados_erp(
            repo, preparado.processados_erp
        )
        produtos_read = await repo.rebuild_read_model()
        logger.info("Projeção pedido-produto reconstruída: %d pares.", produtos_read)
        faturamento = await _persistir_faturamento(repo, preparado.faturamento)
        referencia = await _persistir_referencia(repo, preparado.referencia)
        await _registrar_novidades_realtime(
            realtime,
            orders_snapshot_complete=preparado.pedidos.substituir,
        )
        await repo.commit()
    except BaseException:
        await repo.rollback()
        raise

    return {
        "pedidos_inseridos": pedidos,
        "estoque_chaves": estoque,
        "processados_erp": processados_erp,
        "faturamento_colecoes": faturamento,
        "referencia_tamanho_posicao": referencia,
    }


async def sincronizar_tudo(
    source: IngestionSourceReader,
    repo: SnapshotRepository,
    realtime: RealtimeSnapshotPort,
    job_lock: IngestionJobLock,
    *,
    total_timeout_seconds: float = 1_800.0,
) -> dict[str, int]:
    """Coalesce o job inteiro, coleta sem bloquear writes e aplica atomicamente."""

    async with job_lock.hold() as acquired:
        if not acquired:
            raise SincronizacaoEmAndamentoError(
                "Sincronização do Databricks já em andamento; aguarde a conclusão."
            )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total_timeout_seconds
        collection_deadline = deadline - _MIN_SWAP_BUDGET_SECONDS
        try:
            async with asyncio.timeout_at(collection_deadline):
                preparado = await _coletar_snapshot(source)
        except TimeoutError as exc:
            raise SincronizacaoTimeoutError(
                "Coleta Databricks excedeu o orçamento total da sincronização."
            ) from exc

        if deadline - loop.time() < _MIN_SWAP_BUDGET_SECONDS:
            raise SincronizacaoTimeoutError(
                "Coleta terminou sem orçamento seguro para iniciar o swap PostgreSQL."
            )
        try:
            async with asyncio.timeout_at(deadline):
                return await _aplicar_snapshot(repo, realtime, preparado)
        except TimeoutError as exc:
            raise SincronizacaoTimeoutError(
                "Swap PostgreSQL excedeu o orçamento total da sincronização."
            ) from exc


__all__ = [
    "SincronizacaoEmAndamentoError",
    "SincronizacaoTimeoutError",
    "sincronizar_estoque",
    "sincronizar_faturamento_colecao",
    "sincronizar_pedidos",
    "sincronizar_pedidos_processados_erp",
    "sincronizar_referencia_tamanhos",
    "sincronizar_tudo",
]
