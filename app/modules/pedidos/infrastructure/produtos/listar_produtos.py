"""Projeção paginada de produtos (grão produto+canal)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text

from app.modules.pedidos.domain.consultas import ConsultaProdutos, PaginaCursor
from app.modules.pedidos.infrastructure.sql.cursor import (
    _cursor_parameter,
    _cursor_value,
    _epoch_ms,
    _size_positions,
    _size_sort_key,
)
from app.modules.pedidos.infrastructure.sql.estoque_cte import _STATUS_SQL, _pairs_cte

_SORT_SQL = {
    "lastOrderAt": ("coalesce(last_order_at, date '1970-01-01')", "date"),
    "code": ("code", "text"),
    "name": ("lower(name)", "text"),
    "totalQty": ("total_qty", "bigint"),
    "ordersCount": ("orders_count", "bigint"),
    "totalValue": ("total_value", "numeric"),
    "remainingWindow": (
        "coalesce(processed_at, timestamptz '1970-01-01 00:00:00+00')",
        "timestamptz",
    ),
}


async def listar_produtos_sql(repo, consulta: ConsultaProdutos) -> PaginaCursor:
    pairs_cte = _pairs_cte(consulta.estagio, status=consulta.status)
    sort_expr, sort_type = _SORT_SQL[consulta.ordenacao]
    direction = "ASC" if consulta.direcao == "asc" else "DESC"
    cursor_predicate = "true"
    cursor_value = None
    cursor_code = None
    cursor_channel = None
    # Offset e keyset sao mutuamente exclusivos; a rota ja rejeita as duas
    # paginacoes juntas, entao o keyset so entra quando nao ha offset.
    page_offset_sql = "" if consulta.offset is None else "OFFSET :page_offset"
    if consulta.cursor is not None:
        cursor_value, cursor_code, cursor_channel = consulta.cursor
        cursor_value = _cursor_parameter(cursor_value, sort_type)
        operator = ">" if consulta.direcao == "asc" else "<"
        cursor_predicate = f"""(
            {sort_expr} {operator} CAST(:cursor_value AS {sort_type})
            OR (
                {sort_expr} = CAST(:cursor_value AS {sort_type})
                AND (code > :cursor_code OR (code = :cursor_code AND channel > :cursor_channel))
            )
        )"""
    stmt = text(
        f"""
        WITH
        {pairs_cte},
        product_base AS MATERIALIZED (
            SELECT
                p.code,
                coalesce(max(nullif(p.name, '')), p.code) AS name,
                p.channel,
                sum(p.qty)::bigint AS total_qty,
                sum(p.value)::numeric AS total_value,
                sum(p.original_value)::numeric AS original_total_value,
                count(*)::bigint AS orders_count,
                max(p.order_date) AS last_order_at,
                min(p.processed_at) AS processed_at,
                bool_or(p.adequacao) AS adequacao_aplicada
            FROM pairs p
            WHERE (:channel = 'Todos' OR p.channel = :channel)
            GROUP BY p.code, p.channel
            HAVING :search_empty
                OR bool_or(lower(p.code) LIKE :search ESCAPE '\\')
                OR bool_or(lower(coalesce(p.name, '')) LIKE :search ESCAPE '\\')
                OR bool_or(p.nr_pedido::text LIKE :search ESCAPE '\\')
                OR bool_or(lower(coalesce(p.client, '')) LIKE :search ESCAPE '\\')
        ),
        totals AS (SELECT count(*)::bigint AS total FROM product_base),
        page AS (
            SELECT *, {sort_expr} AS cursor_value
            FROM product_base
            WHERE {cursor_predicate}
            ORDER BY {sort_expr} {direction}, code ASC, channel ASC
            {page_offset_sql}
            LIMIT :limit_plus_one
        )
        SELECT totals.total, page.*
        FROM totals
        LEFT JOIN page ON true
        ORDER BY page.cursor_value {direction}, page.code ASC, page.channel ASC
        """
    )
    params = {
        "channel": consulta.canal,
        "search": f"%{consulta.busca.lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%",
        "search_empty": not consulta.busca,
        "status_target": _STATUS_SQL[consulta.status],
        "cursor_value": cursor_value,
        "cursor_code": cursor_code,
        "cursor_channel": cursor_channel,
        "limit_plus_one": consulta.limite + 1,
    }
    if consulta.offset is not None:
        params["page_offset"] = consulta.offset
    raw = (await repo.db.execute(stmt, params)).mappings().all()
    total = int(raw[0]["total"] or 0)
    page = [dict(row) for row in raw if row["code"] is not None]
    has_more = len(page) > consulta.limite
    page = page[: consulta.limite]
    codes = [str(row["code"]) for row in page]

    sizes_by_product: dict[tuple[str, str], dict[str, int]] = {}
    if codes:
        size_rows = (
            await repo.db.execute(
                text(
                    f"""
                    WITH {pairs_cte}
                    SELECT p.code, p.channel, sizes.key AS size_key,
                           sum((sizes.value)::integer)::bigint AS qty
                    FROM pairs p
                    CROSS JOIN LATERAL jsonb_each_text(p.sizes) sizes
                    WHERE p.code = ANY(CAST(:codes AS varchar[]))
                      AND (:channel = 'Todos' OR p.channel = :channel)
                    GROUP BY p.code, p.channel, sizes.key
                    """
                ),
                {
                    "codes": codes,
                    "channel": consulta.canal,
                    "status_target": _STATUS_SQL[consulta.status],
                },
            )
        ).all()
        for code, channel, size, qty in size_rows:
            sizes_by_product.setdefault((str(code), str(channel)), {})[str(size)] = int(
                qty
            )

    positions = await _size_positions(repo.db, codes)
    targets = {
        (code, size, channel)
        for row in page
        for code in [str(row["code"])]
        for channel in [str(row["channel"])]
        for size in sizes_by_product.get((code, channel), {})
    }
    stock = await repo._estoque_disponivel(targets) if targets else {}
    now = datetime.now(UTC)
    rows: list[dict] = []
    for row in page:
        code = str(row["code"])
        channel = str(row["channel"])
        sizes = sizes_by_product.get((code, channel), {})
        size_keys = sorted(
            sizes,
            key=lambda size: _size_sort_key(size, positions.get((code, size))),
        )
        total_qty = int(row["total_qty"] or 0)
        processed = row["processed_at"]
        remaining = None
        if consulta.estagio == "edicao" and processed is not None:
            processed_utc = (
                processed if processed.tzinfo else processed.replace(tzinfo=UTC)
            )
            remaining = max(
                0, int(((processed_utc.timestamp() + 86400) - now.timestamp()) * 1000)
            )
        rows.append(
            {
                "code": code,
                "name": str(row["name"] or code),
                "channel": channel,
                "total_qty": total_qty,
                "total_value": round(float(row["total_value"] or 0), 2),
                "original_total_value": round(
                    float(row["original_total_value"] or 0), 2
                ),
                "unit_value": round(float(row["total_value"] or 0) / total_qty, 2)
                if total_qty > 0
                else 0.0,
                "orders_count": int(row["orders_count"] or 0),
                "stock": sum(stock.get((code, size, channel), 0) for size in sizes),
                "sizes": sizes,
                "size_keys": size_keys,
                "last_order_at": row["last_order_at"].isoformat()
                if row["last_order_at"] is not None
                else None,
                "processed_at": _epoch_ms(processed),
                "remaining_window_ms": remaining,
                "adequacao_aplicada": bool(row["adequacao_aplicada"]),
            }
        )
    next_key = None
    if has_more and page:
        last = page[-1]
        next_key = (
            _cursor_value(last["cursor_value"]),
            str(last["code"]),
            str(last["channel"]),
        )
    return PaginaCursor(rows=rows, total=total, has_more=has_more, next_key=next_key)
