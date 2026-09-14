"""Casos de uso de leitura de Pedidos, dependentes apenas do port."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import ceil

from app.modules.pedidos.application.ports import PedidosReadRepository
from app.modules.pedidos.domain.consultas import (
    Canal,
    ConsultaAlertas,
    ConsultaClientesProduto,
    ConsultaLookupPedidos,
    ConsultaProdutos,
    DirecaoOrdenacao,
    EstagioProduto,
    OrdenacaoProduto,
    StatusHistorico,
)
from app.shared.config.settings import get_settings
from app.shared.pagination.cursor import decode_cursor, encode_cursor


def _scope(**values: object) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _alerta_cursor_valido(key: tuple) -> bool:
    return (
        len(key) == 3
        and isinstance(key[0], str)
        and len(key[0]) <= 64
        and isinstance(key[1], int)
        and key[1] > 0
        and key[2] in {"error", "warning"}
    )


def _produto_cursor_valido(key: tuple, *, ordenacao: OrdenacaoProduto) -> bool:
    if (
        len(key) != 3
        or not isinstance(key[1], str)
        or not 1 <= len(key[1]) <= 64
        or key[2] not in {"Franquia", "Multimarca"}
    ):
        return False
    value = key[0]
    if ordenacao in {"totalQty", "ordersCount"}:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        )
    if ordenacao == "totalValue":
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            return False
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return False
        return parsed.is_finite()
    if not isinstance(value, str) or len(value) > 255:
        return False
    if ordenacao in {"lastOrderAt", "remainingWindow"}:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
    return True


def _cliente_cursor_valido(key: tuple) -> bool:
    return (
        len(key) == 2
        and isinstance(key[0], str)
        and len(key[0]) <= 255
        and isinstance(key[1], int)
        and key[1] > 0
    )


def _lookup_cursor_valido(key: tuple) -> bool:
    if not _cliente_cursor_valido(key):
        return False
    try:
        datetime.fromisoformat(key[0])
    except ValueError:
        return False
    return True


async def obter_resumo(
    repo: PedidosReadRepository, *, include_stock: bool = False
) -> dict:
    return await repo.obter_resumo(include_stock=include_stock)


async def listar_alertas(
    repo: PedidosReadRepository,
    *,
    canal: Canal,
    busca: str,
    cursor: str | None,
    tamanho_pagina: int,
) -> dict:
    scope = _scope(channel=canal, search=busca)
    secret = get_settings().jwt_secret
    cursor_key = decode_cursor(
        cursor,
        kind="alertas",
        scope=scope,
        secret=secret,
        key_size=3,
        key_validator=_alerta_cursor_valido,
    )
    pagina = await repo.listar_alertas(
        ConsultaAlertas(
            canal=canal,
            busca=busca,
            limite=tamanho_pagina,
            cursor=cursor_key,
        )
    )
    next_cursor = (
        encode_cursor(kind="alertas", scope=scope, key=pagina.next_key, secret=secret)
        if pagina.next_key is not None
        else None
    )
    return {
        "rows": pagina.rows,
        "total": pagina.total,
        "page_size": tamanho_pagina,
        "next_cursor": next_cursor,
        "has_more": pagina.has_more,
    }


async def listar_produtos(
    repo: PedidosReadRepository,
    *,
    estagio: EstagioProduto,
    canal: Canal,
    busca: str,
    status: StatusHistorico,
    ordenacao: OrdenacaoProduto,
    direcao: DirecaoOrdenacao,
    cursor: str | None,
    tamanho_pagina: int,
    pagina_numero: int | None = None,
) -> dict:
    scope = _scope(
        stage=estagio,
        channel=canal,
        search=busca,
        status=status,
        sort=ordenacao,
        order=direcao,
    )
    secret = get_settings().jwt_secret
    # No modo numerado nao existe cursor a validar: a posicao vem do numero da
    # pagina. Decodificar assim mesmo faria a rota rejeitar um cursor que o
    # chamador nunca enviou.
    cursor_key = (
        None
        if pagina_numero is not None
        else decode_cursor(
            cursor,
            kind="pedidos-produtos",
            scope=scope,
            secret=secret,
            key_size=3,
            key_validator=lambda key: _produto_cursor_valido(key, ordenacao=ordenacao),
        )
    )
    offset = None if pagina_numero is None else (pagina_numero - 1) * tamanho_pagina
    pagina = await repo.listar_produtos(
        ConsultaProdutos(
            estagio=estagio,
            canal=canal,
            busca=busca,
            status=status,
            ordenacao=ordenacao,
            direcao=direcao,
            limite=tamanho_pagina,
            cursor=cursor_key,
            offset=offset,
        )
    )
    next_cursor = (
        encode_cursor(
            kind="pedidos-produtos",
            scope=scope,
            key=pagina.next_key,
            secret=secret,
        )
        if pagina.next_key is not None
        else None
    )
    # `total_pages` sempre >= 1: uma lista vazia continua sendo "pagina 1 de 1",
    # e zero paginas deixaria a faixa de paginacao sem numero nenhum.
    total_pages = max(1, ceil(pagina.total / tamanho_pagina)) if tamanho_pagina else 1
    return {
        "rows": pagina.rows,
        "total": pagina.total,
        "page_size": tamanho_pagina,
        "next_cursor": next_cursor,
        "has_more": pagina.has_more,
        "page": pagina_numero,
        "total_pages": total_pages if pagina_numero is not None else None,
    }


async def listar_clientes_produto(
    repo: PedidosReadRepository,
    *,
    codigo_produto: str,
    estagio: EstagioProduto,
    canal: Canal,
    status: StatusHistorico,
    cursor: str | None,
    tamanho_pagina: int,
) -> dict:
    scope = _scope(
        productCode=codigo_produto,
        stage=estagio,
        channel=canal,
        status=status,
    )
    secret = get_settings().jwt_secret
    cursor_key = decode_cursor(
        cursor,
        kind="pedidos-produto-clientes",
        scope=scope,
        secret=secret,
        key_size=2,
        key_validator=_cliente_cursor_valido,
    )
    pagina = await repo.listar_clientes_produto(
        ConsultaClientesProduto(
            codigo_produto=codigo_produto,
            estagio=estagio,
            canal=canal,
            status=status,
            limite=tamanho_pagina,
            cursor=cursor_key,
        )
    )
    next_cursor = (
        encode_cursor(
            kind="pedidos-produto-clientes",
            scope=scope,
            key=pagina["next_key"],
            secret=secret,
        )
        if pagina["next_key"] is not None
        else None
    )
    return {
        "rows": pagina["rows"],
        "total": pagina["total"],
        "page_size": tamanho_pagina,
        "next_cursor": next_cursor,
        "has_more": pagina["has_more"],
        "summary": pagina["summary"],
    }


async def listar_lookup(
    repo: PedidosReadRepository,
    *,
    busca: str,
    cursor: str | None,
    tamanho_pagina: int,
) -> dict:
    scope = _scope(search=busca)
    secret = get_settings().jwt_secret
    cursor_key = decode_cursor(
        cursor,
        kind="pedidos-lookup",
        scope=scope,
        secret=secret,
        key_size=2,
        key_validator=_lookup_cursor_valido,
    )
    pagina = await repo.listar_lookup(
        ConsultaLookupPedidos(
            busca=busca,
            limite=tamanho_pagina,
            cursor=cursor_key,
        )
    )
    next_cursor = (
        encode_cursor(
            kind="pedidos-lookup",
            scope=scope,
            key=pagina.next_key,
            secret=secret,
        )
        if pagina.next_key is not None
        else None
    )
    return {
        "rows": pagina.rows,
        "total": pagina.total,
        "page_size": tamanho_pagina,
        "next_cursor": next_cursor,
        "has_more": pagina.has_more,
    }

