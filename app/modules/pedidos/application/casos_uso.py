"""Casos de uso do módulo pedidos: orquestração entre o motor de adequação
(domínio) e os repositórios (infraestrutura), sem nenhuma dependência de HTTP."""

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.modules.pedidos.application.commands import AlterarGradesProdutoCommand
from app.modules.pedidos.application.ports import PedidosWritePort
from app.modules.pedidos.domain.edicao_grade import montar_grade_atualizada
from app.modules.pedidos.domain.orcamento_edicao import (
    OrcamentoPedidoSnapshot,
    calcular_contribuicao,
    validar_edicao_orcamento,
)
from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx
from app.modules.pedidos.domain.versao_ordem import calcular_versao_ordem

logger = logging.getLogger(__name__)


class ProcessamentoEmAndamentoError(RuntimeError):
    """Outro processamento de OR detém o lock transacional global."""


class ProdutoBatchNaoEncontradoError(RuntimeError):
    """Ao menos um cliente solicitado não pertence ao produto/canal."""


class ProdutoBatchConflitoError(RuntimeError):
    """O snapshot do modal ficou obsoleto ou a OR deixou de ser editável."""


# Janela em que uma OR gerada ainda fica na aba "Pedidos" antes de ir para o
# histórico. Depois disso (ou após aprovação manual) o pedido vai p/ o histórico
# de forma permanente. O relógio é ancorado em OrdemReserva.created_at.
_JANELA_ABERTO = timedelta(hours=24)


def _linha_base_par(itens_ativos: list[dict]) -> int:
    """Soma de `qt_solicitada` dos itens ativos do par, com fallback para
    `qt_liquida` quando a chave estiver ausente ou vazia (0 explícito é um
    valor preservado, não "vazio"). Duplica de propósito o critério de
    `_qt_solicitada_ou_fallback` em `domain/edicao_grade.py` (plano 20-02):
    este plano (20-03) não altera aquele arquivo (ver acceptance criteria
    do PLAN.md), então o mesmo cálculo é reproduzido aqui em vez de
    importado, para que o ledger de orçamento e a grade gravada nunca
    discordem sobre a linha de base do par."""
    total = 0
    for item in itens_ativos:
        raw = item.get("qt_solicitada")
        if raw is None or raw == "":
            raw = item.get("qt_liquida")
        total += max(int(raw or 0), 0)
    return total


async def carregar_referencia_posicoes(
    uow: PedidosWritePort, produtos: set[str]
) -> dict[str, dict[str, int]]:
    return await uow.carregar_referencia_posicoes(produtos)


async def salvar_linhas_linx(uow: PedidosWritePort, linhas: list[dict]) -> None:
    await uow.salvar_linhas_linx(linhas)


async def carregar_ordens_produto_para_update(
    uow: PedidosWritePort,
    *,
    cd_prod_cor: str,
    channel: str,
    order_ids: list[int] | None,
):
    return await uow.carregar_ordens_produto_para_update(
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        order_ids=order_ids,
    )


async def carregar_estoque_disponivel_alvos(
    uow: PedidosWritePort,
    targets: set[tuple[str, str, str]],
) -> dict[tuple[str, str, str], int]:
    return await uow.carregar_estoque_disponivel_alvos(targets)


async def carregar_orcamento_pedidos(
    uow: PedidosWritePort, nr_pedidos: set[int]
) -> dict[int, OrcamentoPedidoSnapshot]:
    return await uow.carregar_orcamento_pedidos(nr_pedidos)


async def carregar_tolerancia_adequacao(uow: PedidosWritePort) -> float:
    return await uow.carregar_tolerancia_adequacao()


async def atualizar_grades_em_lote(
    uow: PedidosWritePort, updates: list[dict]
) -> tuple[tuple[int, str], ...]:
    return await uow.atualizar_grades_em_lote(updates)


async def upsert_modificacoes_em_lote(
    uow: PedidosWritePort, values: list[dict]
) -> None:
    await uow.upsert_modificacoes_em_lote(values)


async def recalcular_estoque_virtual_alvos(
    uow: PedidosWritePort,
    targets: set[tuple[str, str, str]],
) -> None:
    await uow.recalcular_estoque_virtual_alvos(targets)


async def totais_produto_em_edicao(
    uow: PedidosWritePort,
    *,
    cd_prod_cor: str,
    channel: str,
    limite_criacao: datetime,
):
    return await uow.totais_produto_em_edicao(
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        limite_criacao=limite_criacao,
    )


async def aprovar_produto_canal(
    uow: PedidosWritePort,
    *,
    cd_prod_cor: str,
    channel: str,
    limite_criacao: datetime,
):
    return await uow.aprovar_produto_canal(
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        limite_criacao=limite_criacao,
    )


async def carregar_pares_aprovaveis_pedido_para_update(
    uow: PedidosWritePort,
    *,
    nr_pedido: int,
    cd_prod_cor: str | None,
    limite_criacao: datetime,
):
    return await uow.carregar_pares_aprovaveis_pedido_para_update(
        nr_pedido=nr_pedido,
        cd_prod_cor=cd_prod_cor,
        limite_criacao=limite_criacao,
    )


async def aprovar_ordem_reserva(
    uow: PedidosWritePort,
    nr_pedido: int,
    cd_prod_cor: str | None,
    *,
    limite_criacao: datetime,
):
    return await uow.aprovar_ordem_reserva(
        nr_pedido,
        cd_prod_cor,
        limite_criacao=limite_criacao,
    )


async def record_event(
    uow: PedidosWritePort,
    *,
    topic: str,
    event_type: str,
    payload: dict[str, object],
) -> None:
    await uow.record_event(topic=topic, event_type=event_type, payload=payload)


async def observe_entity_keys(
    uow: PedidosWritePort, topic: str, keys: Iterable[str]
) -> list[str]:
    return await uow.observe_entity_keys(topic, keys)


async def observe_active_entity_keys_scoped(
    uow: PedidosWritePort,
    topic: str,
    keys: Iterable[str],
    *,
    scope_prefixes: Iterable[str | int],
) -> list[str]:
    return await uow.observe_active_entity_keys_scoped(
        topic,
        keys,
        scope_prefixes=scope_prefixes,
    )


async def _adquirir_lock_processamento(uow: PedidosWritePort) -> None:
    acquired = await uow.adquirir_lock()
    if not acquired:
        raise ProcessamentoEmAndamentoError(
            "Outro processamento de ordens de reserva já está em andamento."
        )


async def _snapshot_alertas(
    uow: PedidosWritePort,
    *,
    cd_prod_cor: str | None = None,
    channel: str | None = None,
    order_ids: set[int] | None = None,
) -> set[tuple[int, str]]:
    return set(
        await uow.listar_chaves_alertas_ativos(
            cd_prod_cor=cd_prod_cor,
            channel=channel,
            order_ids=order_ids,
        )
    )


async def _registrar_novos_alertas(
    db: PedidosWritePort,
    antes: set[tuple[int, str]],
    *,
    reason: str,
    channel: str,
    cd_prod_cor: str,
    affected_order_ids: set[int] | None = None,
) -> None:
    depois_escopo = await _snapshot_alertas(
        db,
        cd_prod_cor=cd_prod_cor,
        channel=channel,
    )
    # A alteração de uma reserva muda o estoque do produto para todos os
    # pedidos pendentes que o contêm. Descobrimos o escopo pela união das
    # fotos antes/depois e então recalculamos TODOS os alertas de cada pedido
    # (inclusive os causados por outros produtos), evitando desativação falsa.
    scope = set(affected_order_ids or ())
    scope.update(nr for nr, _alert_type in antes)
    scope.update(nr for nr, _alert_type in depois_escopo)
    if not scope:
        return
    depois = await _snapshot_alertas(db, order_ids=scope)
    new_keys = await observe_active_entity_keys_scoped(
        db,
        "alerts",
        (f"{nr}:{alert_type}" for nr, alert_type in depois),
        scope_prefixes=scope,
    )
    novos = {(int(key.partition(":")[0]), key.partition(":")[2]) for key in new_keys}
    if not novos:
        return
    payload: dict[str, object] = {
        "reason": reason,
        "count": len(novos),
        "channel": channel,
    }
    await record_event(
        db,
        topic="alerts",
        event_type="alerts.new.v1",
        payload=payload,
    )


async def executar_alteracao_grades_produto(
    db: PedidosWritePort,
    *,
    cd_prod_cor: str,
    channel: str,
    payload: AlterarGradesProdutoCommand,
) -> dict:
    """Altera até 100 clientes do produto em uma transação all-or-nothing."""
    await _adquirir_lock_processamento(db)
    changes = {change.order_id: change for change in payload.changes}
    order_ids = sorted(changes)
    states = await carregar_ordens_produto_para_update(
        db,
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        order_ids=order_ids,
    )
    found_ids = {state.nr_pedido for state in states}
    missing = sorted(set(order_ids) - found_ids)
    if missing:
        raise ProdutoBatchNaoEncontradoError(
            "Clientes não encontrados no produto/canal: "
            + ", ".join(str(value) for value in missing[:10])
        )

    now = datetime.now(UTC)
    limit = now - _JANELA_ABERTO
    for state in states:
        if state.aprovado_em is not None or state.created_at < limit:
            raise ProdutoBatchConflitoError(
                f"A OR do pedido {state.nr_pedido} não está mais aberta para edição."
            )
        actual_version = calcular_versao_ordem(
            tipo=state.tipo,
            created_at=state.created_at,
            aprovado_em=state.aprovado_em,
            itens=state.itens,
        )
        if actual_version != changes[state.nr_pedido].expected_version:
            raise ProdutoBatchConflitoError(
                f"A grade do pedido {state.nr_pedido} mudou; recarregue antes de salvar."
            )

    old_qty_by_size: dict[str, int] = defaultdict(int)
    new_qty_by_size: dict[str, int] = defaultdict(int)
    for state in states:
        for item in state.itens:
            if item.get("status_item") == "Pedido em Stand By":
                continue
            size = str(item.get("sg_tamanho") or "").strip().upper()
            if size:
                old_qty_by_size[size] += max(int(item.get("qt_liquida") or 0), 0)
        for size, qty in changes[state.nr_pedido].sizes.items():
            if qty > 0:
                new_qty_by_size[size] += qty
    stock_targets = {
        (cd_prod_cor, size, channel)
        for size in set(old_qty_by_size) | set(new_qty_by_size)
    }
    if len(new_qty_by_size) > 100 or len(stock_targets) > 200:
        raise ProdutoBatchConflitoError(
            "A edição excede o limite de 100 tamanhos distintos do produto."
        )
    available = await carregar_estoque_disponivel_alvos(db, stock_targets)
    for size, new_qty in sorted(new_qty_by_size.items()):
        capacity = available.get((cd_prod_cor, size, channel), 0)
        capacity += old_qty_by_size.get(size, 0)
        if new_qty > capacity:
            raise ProdutoBatchConflitoError(
                f"Estoque insuficiente para redistribuir o tamanho {size}: "
                f"solicitado {new_qty}, capacidade {capacity}."
            )

    # Trava de orçamento ±5% (PD-05/PD-06/PD-07, Phase 20): só entra em jogo
    # quando o lote tem ao menos um par "sem adequação" — o caminho "com
    # adequação" não ganha nem uma query nova (D-01). A leitura acontece
    # aqui, depois do lock (_adquirir_lock_processamento na primeira linha
    # da função) e dentro da mesma transação, fechando a janela de corrida
    # de T-20-10. Um `nr_pedido` por par (PD-06): cada pedido do lote tem
    # seu próprio orçamento, nunca agregado por chamada.
    sem_states = [state for state in states if state.tipo == "sem"]
    if sem_states:
        tolerancia = await carregar_tolerancia_adequacao(db)
        orcamentos = await carregar_orcamento_pedidos(
            db, {state.nr_pedido for state in sem_states}
        )
        for state in sem_states:
            active_items = [
                item
                for item in state.itens
                if item.get("status_item") != "Pedido em Stand By"
            ]
            baseline_total = _linha_base_par(active_items)
            current_total = sum(
                max(int(item.get("qt_liquida") or 0), 0) for item in active_items
            )
            new_total = sum(
                qty
                for qty in changes[state.nr_pedido].sizes.values()
                if qty > 0
            )
            contribuicao_atual = calcular_contribuicao(
                baseline_total=baseline_total, quantidade=current_total
            )
            contribuicao_nova = calcular_contribuicao(
                baseline_total=baseline_total, quantidade=new_total
            )
            # Pedido ausente da leitura (sem linha em `pedidos` e sem OR
            # anterior) falha fechado: total original 0 recusa qualquer
            # variação, nunca "sem restrição" (T-20-13).
            snapshot = orcamentos.get(
                state.nr_pedido,
                OrcamentoPedidoSnapshot(
                    nr_pedido=state.nr_pedido,
                    total_original=0,
                    consumido_adicao=0,
                    consumido_corte=0,
                ),
            )
            validar_edicao_orcamento(
                snapshot=snapshot,
                tolerancia=tolerancia,
                contribuicao_atual=contribuicao_atual,
                contribuicao_nova=contribuicao_nova,
            )

    reference = await carregar_referencia_posicoes(db, {cd_prod_cor})
    product_reference = reference.get(cd_prod_cor, {})
    updates: list[dict] = []
    modifications: list[dict] = []
    linx_rows: list[dict] = []
    for state in states:
        try:
            new_items, _new_value = montar_grade_atualizada(
                nr_pedido=state.nr_pedido,
                cd_prod_cor=cd_prod_cor,
                itens_originais=state.itens,
                sizes=changes[state.nr_pedido].sizes,
                permitir_variacao_total=(state.tipo == "sem"),
            )
        except ValueError as exc:
            raise ProdutoBatchConflitoError(str(exc)) from exc
        if new_items == state.itens:
            continue
        requested_sizes = {
            size for size, qty in changes[state.nr_pedido].sizes.items() if qty > 0
        }
        missing_positions = sorted(requested_sizes - set(product_reference))
        if missing_positions:
            raise ProdutoBatchConflitoError(
                "Tamanhos sem posição Linx no produto "
                f"{cd_prod_cor}: {', '.join(missing_positions[:10])}."
            )
        linx = montar_linha_linx(
            state.nr_pedido,
            cd_prod_cor,
            new_items,
            product_reference,
            tipo=state.tipo,
        )
        requested_qty = sum(
            changes[state.nr_pedido].sizes[size] for size in requested_sizes
        )
        if linx is None or linx["qtde_embalada"] != requested_qty:
            raise ProdutoBatchConflitoError(
                f"A grade do pedido {state.nr_pedido} não pôde ser representada no Linx."
            )
        updates.append(
            {
                "nr_pedido": state.nr_pedido,
                "cd_prod_cor": cd_prod_cor,
                "itens": new_items,
            }
        )
        modifications.append(
            {
                "nr_pedido": state.nr_pedido,
                "cd_prod_cor": cd_prod_cor,
                "items": new_items,
                "original_items": state.itens,
            }
        )
        linx_rows.append(linx)

    if not updates:
        total_qty, total_value = await totais_produto_em_edicao(
            db,
            cd_prod_cor=cd_prod_cor,
            channel=channel,
            limite_criacao=limit,
        )
        return {
            "status": "success",
            "updated_count": 0,
            "total_qty": total_qty,
            "total_value": round(float(total_value), 2),
        }

    alerts_before = await _snapshot_alertas(
        db,
        cd_prod_cor=cd_prod_cor,
        channel=channel,
    )
    changed_pairs = await atualizar_grades_em_lote(db, updates)
    expected_pairs = tuple(
        (int(update["nr_pedido"]), cd_prod_cor) for update in updates
    )
    if changed_pairs != expected_pairs:
        raise ProdutoBatchConflitoError(
            "As ordens mudaram durante a edição; nenhuma alteração foi confirmada."
        )
    await upsert_modificacoes_em_lote(db, modifications)
    await recalcular_estoque_virtual_alvos(db, stock_targets)
    await salvar_linhas_linx(db, linx_rows)
    total_qty, total_value = await totais_produto_em_edicao(
        db,
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        limite_criacao=limit,
    )
    await record_event(
        db,
        topic="orders",
        event_type="orders.grade_changed.v1",
        payload={
            "productCode": cd_prod_cor,
            "channel": channel,
            "changedCount": len(changed_pairs),
        },
    )
    await _registrar_novos_alertas(
        db,
        alerts_before,
        reason="grade_changed",
        channel=channel,
        cd_prod_cor=cd_prod_cor,
        affected_order_ids={int(nr) for nr, _code in changed_pairs},
    )

    # Fusão de salvar+aprovar (D-09/PD-10/PD-11/PD-12, Phase 20): quando
    # TODOS os pares efetivamente alterados são "sem adequação", salvar
    # também finaliza aquela(s) OR(s), no mesmo commit — sem clique
    # separado de "Aprovar OR". Escopo restrito aos nr_pedido do lote e ao
    # cd_prod_cor deste produto (PD-10): nenhum outro pedido do mesmo
    # produto/canal, nem outro produto do mesmo pedido, é tocado. Lotes
    # mistos ('com' + 'sem') não aprovam nada (PD-11). Chama o port de
    # aprovação diretamente, nunca executar_aprovacao/executar_aprovacao_
    # produto — eles fariam commit próprio, quebrando a atomicidade
    # tudo-ou-nada desta função (PD-12).
    approved_count = 0
    state_by_pedido = {state.nr_pedido: state for state in states}
    changed_nr_pedidos = {int(nr) for nr, _code in changed_pairs}
    fusao_elegivel = bool(changed_nr_pedidos) and all(
        state_by_pedido[nr].tipo == "sem" for nr in changed_nr_pedidos
    )
    if fusao_elegivel:
        approved_pairs: list[tuple[int, str]] = []
        for nr_pedido in sorted(changed_nr_pedidos):
            pares_aprovaveis = await carregar_pares_aprovaveis_pedido_para_update(
                db,
                nr_pedido=nr_pedido,
                cd_prod_cor=cd_prod_cor,
                limite_criacao=limit,
            )
            resultado = await aprovar_ordem_reserva(
                db,
                nr_pedido,
                cd_prod_cor,
                limite_criacao=limit,
            )
            if resultado.changed:
                approved_pairs.extend(pares_aprovaveis)
                approved_count += resultado.alterados
        if approved_pairs:
            await observe_entity_keys(
                db,
                "history",
                (f"{nr}:{cd}" for nr, cd in approved_pairs),
            )
            await record_event(
                db,
                topic="orders",
                event_type="orders.approved.v1",
                payload={
                    "productCode": cd_prod_cor,
                    "channel": channel,
                    "changedCount": approved_count,
                    "source": "grade_save",
                },
            )
            await record_event(
                db,
                topic="history",
                event_type="history.changed.v1",
                payload={
                    "reason": "grade_save_approved",
                    "productCode": cd_prod_cor,
                    "channel": channel,
                    "changedCount": approved_count,
                },
            )

    await db.commit()
    return {
        "status": "success",
        "updated_count": len(changed_pairs),
        "total_qty": total_qty,
        "total_value": round(float(total_value), 2),
        "approved_count": approved_count,
    }


async def executar_aprovacao_produto(
    db: PedidosWritePort,
    *,
    cd_prod_cor: str,
    channel: str,
) -> dict:
    """Aprovação idempotente de todas as ORs do produto no canal exato."""
    await _adquirir_lock_processamento(db)
    result = await aprovar_produto_canal(
        db,
        cd_prod_cor=cd_prod_cor,
        channel=channel,
        limite_criacao=datetime.now(UTC) - _JANELA_ABERTO,
    )
    if result.matched_count == 0:
        raise ProdutoBatchNaoEncontradoError(
            f"Nenhuma OR encontrada para {cd_prod_cor} no canal {channel}."
        )
    if result.approved_pairs:
        await observe_entity_keys(
            db,
            "history",
            (f"{nr}:{cd}" for nr, cd in result.approved_pairs),
        )
        await record_event(
            db,
            topic="orders",
            event_type="orders.approved.v1",
            payload={
                "productCode": cd_prod_cor,
                "channel": channel,
                "changedCount": result.approved_count,
            },
        )
        await record_event(
            db,
            topic="history",
            event_type="history.changed.v1",
            payload={
                "reason": "product_approved",
                "productCode": cd_prod_cor,
                "channel": channel,
                "changedCount": result.approved_count,
            },
        )
        await db.commit()
    return {
        "status": "success",
        "matched_count": result.matched_count,
        "approved_count": result.approved_count,
        "already_approved_count": result.already_approved_count,
        "expired_count": result.expired_count,
    }


async def executar_aprovacao(
    db: PedidosWritePort, nr_pedido: int, cd_prod_cor: str | None = None
) -> bool:
    """Aprova manualmente OR(s) antes das 24h, movendo-as para o histórico.

    `cd_prod_cor` informado aprova apenas aquele par; omitido aprova todos os
    produtos daquele pedido. Idempotente. False = nenhuma OR encontrada (-> 404).
    """
    await _adquirir_lock_processamento(db)
    now = datetime.now(UTC)
    limite_criacao = now - _JANELA_ABERTO
    pares_alterados = await carregar_pares_aprovaveis_pedido_para_update(
        db,
        nr_pedido=nr_pedido,
        cd_prod_cor=cd_prod_cor,
        limite_criacao=limite_criacao,
    )
    resultado = await aprovar_ordem_reserva(
        db,
        nr_pedido,
        cd_prod_cor,
        limite_criacao=limite_criacao,
    )
    if not resultado.exists:
        return False
    if not resultado.changed:
        return True
    await observe_entity_keys(
        db,
        "history",
        (f"{nr}:{cd}" for nr, cd in pares_alterados),
    )
    await record_event(
        db,
        topic="orders",
        event_type="orders.approved.v1",
        payload={
            "orderId": nr_pedido,
            "productCode": cd_prod_cor,
            "changedCount": resultado.alterados,
        },
    )
    await record_event(
        db,
        topic="history",
        event_type="history.changed.v1",
        payload={"reason": "order_approved", "orderId": nr_pedido},
    )
    await db.commit()
    alvo = f"{nr_pedido}/{cd_prod_cor}" if cd_prod_cor else str(nr_pedido)
    logger.info(f"OR {alvo} aprovada manualmente (movida para histórico).")
    return True
