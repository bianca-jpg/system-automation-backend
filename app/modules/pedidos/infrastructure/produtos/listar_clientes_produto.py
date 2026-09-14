"""Projeção de clientes de um produto específico (grão pedido dentro do produto)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from app.modules.parametros import service as param_service
from app.modules.pedidos.domain.consultas import ConsultaClientesProduto
from app.modules.pedidos.domain.orcamento_edicao import OrcamentoPedidoSnapshot
from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido
from app.modules.pedidos.domain.versao_ordem import calcular_versao_ordem
from app.modules.pedidos.infrastructure.repositorio_orcamento_pedido import (
    carregar_orcamento_pedidos,
)
from app.modules.pedidos.infrastructure.sql.cursor import (
    _epoch_ms,
    _size_positions,
    _size_sort_key,
)
from app.modules.pedidos.infrastructure.sql.estoque_cte import _STATUS_SQL, _pairs_cte
from app.shared.config.settings import get_settings


async def listar_clientes_produto_sql(repo, consulta: ConsultaClientesProduto) -> dict:
    pairs_cte = _pairs_cte(
        consulta.estagio,
        status=consulta.status,
        scoped_product=True,
    )
    cursor_client = consulta.cursor[0] if consulta.cursor else None
    cursor_nr = consulta.cursor[1] if consulta.cursor else None
    stmt = text(
        f"""
        WITH
        {pairs_cte},
        scoped AS MATERIALIZED (
            SELECT * FROM pairs
            WHERE code = :code
              AND (:channel = 'Todos' OR channel = :channel)
        ),
        sizes AS (
            SELECT item.key AS size_key,
                   sum((item.value)::integer)::bigint AS qty
            FROM scoped s
            CROSS JOIN LATERAL jsonb_each_text(s.sizes) item
            GROUP BY item.key
        ),
        summary AS (
            SELECT
                count(*)::bigint AS total,
                coalesce(max(scoped.name), :code) AS summary_name,
                count(DISTINCT scoped.nr_pedido)::bigint AS total_clients,
                coalesce(sum(scoped.qty), 0)::bigint AS total_qty,
                coalesce(sum(scoped.value), 0)::numeric AS total_value,
                coalesce(sum(scoped.original_value), 0)::numeric
                    AS original_total_value,
                coalesce(
                    (SELECT jsonb_object_agg(size_key, qty ORDER BY size_key) FROM sizes),
                    '{{}}'::jsonb
                ) AS size_totals
            FROM scoped
        ),
        page AS (
            SELECT scoped.*, modification.original_items AS modification_original_items
            FROM scoped
            LEFT JOIN pedido_modificacoes modification
              ON modification.nr_pedido = scoped.nr_pedido
             AND modification.cd_prod_cor = scoped.code
            WHERE CAST(:cursor_client AS text) IS NULL
               OR lower(coalesce(scoped.client, '')) > CAST(:cursor_client AS text)
               OR (lower(coalesce(scoped.client, '')) = CAST(:cursor_client AS text)
                   AND scoped.nr_pedido < CAST(:cursor_nr AS integer))
            ORDER BY lower(coalesce(scoped.client, '')) ASC, scoped.nr_pedido DESC
            LIMIT :limit_plus_one
        )
        SELECT summary.*, page.*
        FROM summary LEFT JOIN page ON true
        ORDER BY lower(coalesce(page.client, '')) ASC, page.nr_pedido DESC
        """
    )
    params = {
        "code": consulta.codigo_produto,
        "channel": consulta.canal,
        "status_target": _STATUS_SQL[consulta.status],
        "cursor_client": cursor_client,
        "cursor_nr": cursor_nr,
        "limit_plus_one": consulta.limite + 1,
    }
    raw = (await repo.db.execute(stmt, params)).mappings().all()
    total = int(raw[0]["total"] or 0)
    page = [dict(row) for row in raw if row["nr_pedido"] is not None]
    has_more = len(page) > consulta.limite
    page = page[: consulta.limite]

    summary_row = raw[0]
    size_totals = {
        str(k): int(v) for k, v in dict(summary_row["size_totals"] or {}).items()
    }
    positions = await _size_positions(repo.db, [consulta.codigo_produto])
    size_keys = sorted(
        size_totals,
        key=lambda size: _size_sort_key(
            size, positions.get((consulta.codigo_produto, size))
        ),
    )
    targets = {(consulta.codigo_produto, size, consulta.canal) for size in size_keys}
    stock = await repo._estoque_disponivel(targets) if targets else {}
    stock_total = sum(stock.values())

    # Orçamento do pedido inteiro (D-12) — só para linhas "sem adequação"
    # (`pair["adequacao"]` já vem na query, D-03: nenhuma query extra para
    # decidir). Uma leitura em lote para a página toda, nunca por linha, e
    # pulada por completo quando a página é inteiramente "com adequação"
    # (D-01 também em custo, não só em payload).
    sem_adequacao_nr_pedidos = {
        int(pair["nr_pedido"]) for pair in page if not bool(pair["adequacao"])
    }
    orcamentos_por_pedido: dict[int, dict] = {}
    if sem_adequacao_nr_pedidos:
        settings = get_settings()
        tolerancia = await param_service.get_param_value(
            repo.db,
            "tolerancia_adequacao",
            default=settings.tolerancia_adequacao,
            cast=float,
        )
        snapshots = await carregar_orcamento_pedidos(repo.db, sem_adequacao_nr_pedidos)
        for nr_pedido in sem_adequacao_nr_pedidos:
            # Pedido do conjunto ausente na leitura (T-20-21): falha fechada
            # com total 0 em vez de omitir o objeto — omissão sinaliza "com
            # adequação" para o front, o que liberaria edição sem trava.
            snapshot = snapshots.get(nr_pedido) or OrcamentoPedidoSnapshot(
                nr_pedido=nr_pedido,
                total_original=0,
                consumido_adicao=0,
                consumido_corte=0,
            )
            # Limite e restante vêm do ledger (PD-09) — a projeção nunca
            # escreve floor(total * tolerancia) nem qualquer literal fixo.
            ledger = OrcamentoPedido(
                nr_pedido=nr_pedido,
                total_original=snapshot.total_original,
                consumido_previo_adicao=snapshot.consumido_adicao,
                consumido_previo_corte=snapshot.consumido_corte,
                tolerancia=tolerancia,
            )
            orcamentos_por_pedido[nr_pedido] = {
                "nr_pedido": nr_pedido,
                "limite_adicao": ledger.limite_adicao,
                "consumido_adicao": snapshot.consumido_adicao,
                "restante_adicao": ledger.restante_adicao,
                "limite_corte": ledger.limite_corte,
                "consumido_corte": snapshot.consumido_corte,
                "restante_corte": ledger.restante_corte,
            }

    rows: list[dict] = []
    for pair in page:
        sizes = {str(k): int(v) for k, v in dict(pair["sizes"] or {}).items()}
        qty = int(pair["qty"] or 0)
        value = float(pair["value"] or 0)
        original = float(pair["original_value"] or 0)
        item_stock = sum(
            stock.get((consulta.codigo_produto, size, str(pair["channel"])), 0)
            for size in sizes
        )
        item = {
            "name": str(pair["name"] or consulta.codigo_produto),
            "qty": qty,
            "code": consulta.codigo_produto,
            "unit_value": round(value / qty, 2) if qty > 0 else 0.0,
            "cd_status": "Regular",
            "stock": item_stock,
            "sizes": sizes,
        }
        modification_original_items = pair["modification_original_items"]
        original_item = item
        if modification_original_items:
            original_sizes: dict[str, int] = {}
            original_qty = 0
            original_value = Decimal(0)
            for raw_item in list(modification_original_items):
                if raw_item.get("status_item") == "Pedido em Stand By":
                    continue
                size = str(raw_item.get("sg_tamanho") or "").strip().upper()
                if not size or len(size) > 16:
                    continue
                size_qty = max(int(raw_item.get("qt_liquida") or 0), 0)
                original_sizes[size] = original_sizes.get(size, 0) + size_qty
                original_qty += size_qty
                original_value += Decimal(str(raw_item.get("vl_liquido") or 0))
            original_item = {
                **item,
                "qty": original_qty,
                "unit_value": round(float(original_value) / original_qty, 2)
                if original_qty > 0
                else 0.0,
                "sizes": original_sizes,
            }
        processed = pair["processed_at"]
        order = {
            "id": f"#{int(pair['nr_pedido'])}",
            "client": str(pair["client"] or f"Cliente {pair['nr_pedido']}"),
            "value": round(value, 2),
            "original_value_before_adequacao": round(original, 2),
            "status": str(pair["status"]),
            "motivo": str(pair["motivo"] or ""),
            "date": pair["order_date_raw"],
            "delivery_date": None,
            "canal": str(pair["channel"]),
            "adequacao_aplicada": bool(pair["adequacao"]),
            "adequacao_valor_ajustado": round(value - original, 2),
            "items": [item],
            "alert": None,
            "pedido_alterado": bool(modification_original_items)
            or round(value, 2) != round(original, 2),
            "original_items": [original_item],
            "processed_at": _epoch_ms(processed),
            "aprovado": bool(pair["aprovado"]),
            "approved_at": _epoch_ms(pair["approved_at"]),
            "origem": str(pair["origem"]),
            "confirmado_erp": bool(pair["confirmado_erp"]),
        }
        if not bool(pair["adequacao"]):
            # Chave ausente (não nula) para "com adequação" — PD-08: omissão
            # é o sinal que o front usa para decidir se mostra o medidor.
            order["orcamento_pedido"] = orcamentos_por_pedido[int(pair["nr_pedido"])]
        rows.append(
            {
                "order": order,
                "item": item,
                "version": calcular_versao_ordem(
                    tipo=str(pair["version_tipo"]),
                    created_at=pair["version_created_at"],
                    aprovado_em=pair["version_approved_at"],
                    itens=pair["version_items"],
                ),
            }
        )

    next_key = None
    if has_more and page:
        last = page[-1]
        next_key = (str(last["client"] or "").lower(), int(last["nr_pedido"]))
    return {
        "rows": rows,
        "total": total,
        "has_more": has_more,
        "next_key": next_key,
        "summary": {
            "code": consulta.codigo_produto,
            "name": str(summary_row["summary_name"] or consulta.codigo_produto),
            "channel": consulta.canal,
            "total_clients": int(summary_row["total_clients"] or 0),
            "total_qty": int(summary_row["total_qty"] or 0),
            "total_value": round(float(summary_row["total_value"] or 0), 2),
            "original_total_value": round(
                float(summary_row["original_total_value"] or 0), 2
            ),
            "stock": stock_total,
            "size_keys": size_keys,
            "size_totals": size_totals,
        },
    }
