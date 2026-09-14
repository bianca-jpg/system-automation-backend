"""Orquestrador do resumo do dashboard: junta stats de ERP e de canal."""

from __future__ import annotations

from collections import defaultdict

from app.modules.pedidos.infrastructure.resumo.stats_canais import _channel_stats
from app.modules.pedidos.infrastructure.resumo.stats_erp import _erp_orders_stats


def _percent(numerator: int, denominator: int) -> int:
    return int((numerator * 100 / denominator) + 0.5) if denominator > 0 else 0


async def obter_resumo_sql(repo, *, include_stock: bool = False) -> dict:
    # O mapa completo é apenas uma ponte de compatibilidade opt-in. O caminho
    # padrão calcula os indicadores no banco e não transfere ~20 mil chaves.
    stock_by_channel: dict[str, dict[str, int]] = {
        "Franquia": defaultdict(int),
        "Multimarca": defaultdict(int),
    }
    stock_all: dict[str, int] = defaultdict(int)
    if include_stock:
        available = await repo._estoque_disponivel(None)
        for (product, _size, channel), qty in available.items():
            if channel in stock_by_channel:
                stock_by_channel[channel][product] += int(qty)
        for channel in ("Franquia", "Multimarca"):
            for product, qty in stock_by_channel[channel].items():
                stock_all[product] += qty

    erp_count, erp_billing = await _erp_orders_stats(repo)
    base_stats, blocked_pieces, total_demand, editing_all = await _channel_stats(repo)

    def build(channel: str) -> dict:
        values = base_stats[channel]
        total = (
            values["liberados"]
            + values["blocked_credit"]
            + values["or_com"]
            + values["or_sem"]
        )
        return {
            "total_orders_count": total,
            "liberados_count": values["liberados"],
            "or_com_adequacao_count": values["or_com"],
            "or_sem_adequacao_count": values["or_sem"],
            "editing_order_count": values["editing_orders"],
            "editing_product_count": values["editing_products"],
            "processado_erp_count": erp_count[channel],
            "pecas_bloqueadas_count": blocked_pieces[channel],
            "pecas_bloqueadas_percent": _percent(
                blocked_pieces[channel], total_demand[channel]
            ),
            "bloqueados_sem_credito_count": values["blocked_credit"],
            "liberados_percent": _percent(values["liberados"], total),
            "bloqueados_sem_credito_percent": _percent(values["blocked_credit"], total),
        }

    stats = {channel: build(channel) for channel in ("Franquia", "Multimarca")}
    total_values = {
        key: stats["Franquia"][key] + stats["Multimarca"][key]
        for key in (
            "total_orders_count",
            "liberados_count",
            "or_com_adequacao_count",
            "or_sem_adequacao_count",
            "editing_order_count",
            "editing_product_count",
            "processado_erp_count",
            "pecas_bloqueadas_count",
            "bloqueados_sem_credito_count",
        )
    }
    total_values.update(
        {
            "pecas_bloqueadas_percent": _percent(
                total_values["pecas_bloqueadas_count"],
                total_demand["Franquia"] + total_demand["Multimarca"],
            ),
            "liberados_percent": _percent(
                total_values["liberados_count"], total_values["total_orders_count"]
            ),
            "bloqueados_sem_credito_percent": _percent(
                total_values["bloqueados_sem_credito_count"],
                total_values["total_orders_count"],
            ),
        }
    )
    total_values["editing_order_count"] = editing_all["orders"]
    total_values["editing_product_count"] = editing_all["products"]
    stats["Todos"] = total_values

    return {
        "stock_by_code": {
            "Todos": dict(stock_all),
            "Franquia": dict(stock_by_channel["Franquia"]),
            "Multimarca": dict(stock_by_channel["Multimarca"]),
        },
        "erp_billing": erp_billing,
        "erp_count": erp_count,
        "stats_by_channel": stats,
    }
