"""Reconstrução determinística de uma grade dinâmica de OR."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.modules.pedidos.domain.rateio import ratear_hamilton as _allocate
from app.modules.pedidos.domain.rateio import ratear_inteiro as _allocate_int

_CENT = Decimal("0.01")


def _money(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (ArithmeticError, ValueError):
        return Decimal(0)


def _qt_solicitada_ou_fallback(item: dict) -> int:
    """Lê `qt_solicitada` do item, caindo para `qt_liquida` quando ausente/vazia.

    Existe para cobrir ORs gravadas antes deste campo existir. Um
    `qt_solicitada` explicitamente igual a 0 é um valor válido (não é
    "ausente ou vazia") e é preservado como zero, em vez de ser reinflado
    para a quantidade líquida.
    """
    raw = item.get("qt_solicitada")
    if raw is None or raw == "":
        raw = item.get("qt_liquida")
    return max(int(raw or 0), 0)


def montar_grade_atualizada(
    *,
    nr_pedido: int,
    cd_prod_cor: str,
    itens_originais: list[dict],
    sizes: dict[str, int],
    permitir_variacao_total: bool = False,
) -> tuple[list[dict], Decimal]:
    """Troca tamanhos/quantidades sem receber valores financeiros do cliente.

    O preço corrente e o preço original são derivados exclusivamente do estado
    bloqueado da OR. Cada total é recalculado pelo preço unitário médio e então
    rateado por Hamilton; assim a soma em centavos permanece exata mesmo com
    tamanhos numéricos e arredondamentos.

    `permitir_variacao_total` é **opt-in** (padrão `False`, comportamento
    preservado) e pertence exclusivamente a ORs "sem adequação": quando o
    chamador o habilita, a grade nova pode ter uma quantidade total diferente
    da atual — a guarda de total preservado deixa de rodar. ORs "com
    adequação" nunca devem passar `True` aqui (decisão de negócio D-01); é
    responsabilidade do chamador, que conhece `state.tipo`.

    Quando `permitir_variacao_total=True`, `qt_solicitada` de cada item
    devolvido deixa de significar "o que foi pedido naquele tamanho
    específico" e passa a representar a **linha de base do par rateada**: a
    soma de `qt_solicitada` dos itens originais ativos, redistribuída pelos
    tamanhos novos por `ratear_inteiro`. Isso mantém a soma de `qt_solicitada`
    do par invariante ao longo de edições sucessivas — tamanho novo ou
    zerado não infla nem encolhe essa linha de base. Quando o parâmetro está
    desligado (o caminho "com adequação" de hoje), `qt_solicitada` continua
    sendo, como sempre foi, igual à quantidade líquida nova de cada item —
    dívida conhecida e deliberadamente não corrigida nesta função (fica para
    uma fase futura decidir).
    """
    positive_sizes = {
        str(size).strip().upper(): int(qty)
        for size, qty in sizes.items()
        if int(qty) > 0
    }
    if not positive_sizes:
        raise ValueError("grade precisa manter quantidade total positiva")
    active = [
        dict(item)
        for item in itens_originais
        if item.get("status_item") != "Pedido em Stand By"
    ]
    if not active:
        raise ValueError("ordem de reserva não possui itens editáveis")

    current_qty = sum(max(int(item.get("qt_liquida") or 0), 0) for item in active)
    if current_qty <= 0:
        raise ValueError("ordem de reserva possui quantidade financeira inválida")
    current_total = sum((_money(item.get("vl_liquido")) for item in active), Decimal(0))
    original_total = sum(
        (
            _money(item.get("vl_liquido")) - _money(item.get("diff_valor"))
            for item in active
        ),
        Decimal(0),
    )
    new_qty = sum(positive_sizes.values())
    if not permitir_variacao_total and new_qty != current_qty:
        raise ValueError(
            "edição de grade só redistribui tamanhos e deve preservar a quantidade total"
        )
    baseline_qty = sum(_qt_solicitada_ou_fallback(item) for item in active)
    baseline_allocations = (
        _allocate_int(baseline_qty, positive_sizes) if permitir_variacao_total else {}
    )
    new_total = current_total.quantize(_CENT, rounding=ROUND_HALF_UP)
    new_original_total = original_total.quantize(_CENT, rounding=ROUND_HALF_UP)
    current_allocations = _allocate(new_total, positive_sizes)
    original_allocations = _allocate(new_original_total, positive_sizes)

    by_size = {
        str(item.get("sg_tamanho") or "").strip().upper(): item for item in active
    }
    base = active[0]
    result: list[dict] = []
    for size in sorted(positive_sizes):
        meta = by_size.get(size, base)
        current_value = current_allocations[size]
        original_value = original_allocations[size]
        result.append(
            {
                "nr_pedido": nr_pedido,
                "cd_prod_cor": cd_prod_cor,
                "sg_tamanho": size,
                "ds_grupo": meta.get("ds_grupo") or base.get("ds_grupo") or cd_prod_cor,
                "qt_liquida": positive_sizes[size],
                "vl_liquido": float(current_value),
                "client": meta.get("client") or base.get("client"),
                "canal": meta.get("canal") or base.get("canal"),
                "status_credito": meta.get("status_credito")
                or base.get("status_credito"),
                "ds_produto": meta.get("ds_produto") or base.get("ds_produto"),
                "data": meta.get("data") or base.get("data"),
                "status_item": "Gerar OR",
                "qt_solicitada": (
                    baseline_allocations[size]
                    if permitir_variacao_total
                    else positive_sizes[size]
                ),
                "diff_valor": float((current_value - original_value).quantize(_CENT)),
            }
        )
    return result, new_total


__all__ = ["montar_grade_atualizada"]
