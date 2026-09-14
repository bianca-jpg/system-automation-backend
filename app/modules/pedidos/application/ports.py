"""Ports do contexto Pedidos.

Application conhece apenas estes contratos. A composição com SQLAlchemy,
parâmetros e realtime fica em ``infrastructure.write_adapter``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.modules.pedidos.domain.consultas import (
    ConsultaAlertas,
    ConsultaClientesProduto,
    ConsultaLookupPedidos,
    ConsultaProdutos,
    PaginaCursor,
)
from app.modules.pedidos.domain.orcamento_edicao import OrcamentoPedidoSnapshot


class PedidosReadRepository(Protocol):
    async def obter_resumo(self, *, include_stock: bool = False) -> dict: ...

    async def listar_alertas(self, consulta: ConsultaAlertas) -> PaginaCursor: ...

    async def listar_produtos(self, consulta: ConsultaProdutos) -> PaginaCursor: ...

    async def listar_clientes_produto(
        self, consulta: ConsultaClientesProduto
    ) -> dict: ...

    async def listar_lookup(self, consulta: ConsultaLookupPedidos) -> PaginaCursor: ...


@dataclass(frozen=True, slots=True)
class OrdemProdutoState:
    nr_pedido: int
    cd_prod_cor: str
    tipo: str
    itens: list[dict]
    created_at: datetime
    aprovado_em: datetime | None


@dataclass(frozen=True, slots=True)
class ResultadoAprovacaoProduto:
    matched_count: int
    approved_pairs: tuple[tuple[int, str], ...]
    already_approved_count: int
    expired_count: int

    @property
    def approved_count(self) -> int:
        return len(self.approved_pairs)


@dataclass(frozen=True, slots=True)
class ResultadoAprovacaoOrdem:
    encontrados: int
    alterados: int

    @property
    def exists(self) -> bool:
        return self.encontrados > 0

    @property
    def changed(self) -> bool:
        return self.alterados > 0


class PedidosWritePort(Protocol):
    """Unit of work transacional para comandos de Pedidos."""

    async def adquirir_lock(self) -> bool: ...

    async def carregar_referencia_posicoes(
        self, produtos: set[str]
    ) -> dict[str, dict[str, int]]: ...

    async def salvar_linhas_linx(self, linhas: list[dict]) -> None: ...

    async def listar_chaves_alertas_ativos(
        self,
        *,
        cd_prod_cor: str | None = None,
        channel: str | None = None,
        order_ids: set[int] | None = None,
    ) -> list[tuple[int, str]]: ...

    async def carregar_ordens_produto_para_update(
        self,
        *,
        cd_prod_cor: str,
        channel: str,
        order_ids: list[int] | None,
    ) -> list[OrdemProdutoState]: ...

    async def carregar_estoque_disponivel_alvos(
        self, targets: set[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], int]: ...

    async def carregar_orcamento_pedidos(
        self, nr_pedidos: set[int]
    ) -> dict[int, OrcamentoPedidoSnapshot]: ...

    async def carregar_tolerancia_adequacao(self) -> float: ...

    async def atualizar_grades_em_lote(
        self, updates: list[dict]
    ) -> tuple[tuple[int, str], ...]: ...

    async def upsert_modificacoes_em_lote(self, values: list[dict]) -> None: ...

    async def recalcular_estoque_virtual_alvos(
        self, targets: set[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], int]: ...

    async def totais_produto_em_edicao(
        self,
        *,
        cd_prod_cor: str,
        channel: str,
        limite_criacao: datetime,
    ) -> tuple[int, Decimal]: ...

    async def aprovar_produto_canal(
        self,
        *,
        cd_prod_cor: str,
        channel: str,
        limite_criacao: datetime,
    ) -> ResultadoAprovacaoProduto: ...

    async def carregar_pares_aprovaveis_pedido_para_update(
        self,
        *,
        nr_pedido: int,
        cd_prod_cor: str | None,
        limite_criacao: datetime,
    ) -> tuple[tuple[int, str], ...]: ...

    async def aprovar_ordem_reserva(
        self,
        nr_pedido: int,
        cd_prod_cor: str | None,
        *,
        limite_criacao: datetime,
    ) -> ResultadoAprovacaoOrdem: ...

    async def record_event(
        self, *, topic: str, event_type: str, payload: dict[str, object]
    ) -> None: ...

    async def observe_entity_keys(
        self, topic: str, keys: Iterable[str]
    ) -> list[str]: ...

    async def observe_active_entity_keys_scoped(
        self,
        topic: str,
        keys: Iterable[str],
        *,
        scope_prefixes: Iterable[str | int],
    ) -> list[str]: ...

    async def commit(self) -> None: ...
