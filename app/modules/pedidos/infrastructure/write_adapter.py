"""Composição SQLAlchemy dos ports de escrita de Pedidos."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parametros import service as param_service
from app.modules.pedidos.application.ports import (
    OrdemProdutoState,
    ResultadoAprovacaoOrdem,
    ResultadoAprovacaoProduto,
)
from app.modules.pedidos.domain.orcamento_edicao import OrcamentoPedidoSnapshot
from app.modules.pedidos.infrastructure import (
    repositorio_estoque_virtual as estoque_repo,
)
from app.modules.pedidos.infrastructure import (
    repositorio_ingestao_readmodel as ingestao_read_repo,
)
from app.modules.pedidos.infrastructure import (
    repositorio_orcamento_pedido as orcamento_repo,
)
from app.modules.pedidos.infrastructure import (
    repositorio_ordens as ordens_repo,
)
from app.modules.pedidos.infrastructure import (
    repositorio_ordens_linx as linx_repo,
)
from app.modules.pedidos.infrastructure import (
    repositorio_produto_writes as produto_repo,
)
from app.modules.pedidos.infrastructure.resumo.listar_chaves_alertas_ativos import (
    listar_chaves_alertas_ativos_sql,
)
from app.modules.realtime import (
    observe_active_entity_keys_scoped,
    observe_entity_keys,
    record_event,
)
from app.shared.config.settings import get_settings
from app.shared.database.advisory_locks import ORDER_STATE_MUTATION_LOCK


class SqlAlchemyPedidosWriteUnitOfWork:
    """Adapter transacional; não commita fora do comando da aplicação."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def adquirir_lock(self) -> bool:
        acquired = await self.db.scalar(
            select(func.pg_try_advisory_xact_lock(ORDER_STATE_MUTATION_LOCK))
        )
        return bool(acquired)

    async def carregar_referencia_posicoes(
        self, produtos: set[str]
    ) -> dict[str, dict[str, int]]:
        return await ingestao_read_repo.carregar_referencia_posicoes(self.db, produtos)

    async def salvar_linhas_linx(self, linhas: list[dict]) -> None:
        await linx_repo.salvar_linhas_linx(self.db, linhas)

    async def listar_chaves_alertas_ativos(
        self,
        *,
        cd_prod_cor: str | None = None,
        channel: str | None = None,
        order_ids: set[int] | None = None,
    ) -> list[tuple[int, str]]:
        return await listar_chaves_alertas_ativos_sql(
            self.db,
            cd_prod_cor=cd_prod_cor,
            channel=channel,
            order_ids=order_ids,
        )

    async def carregar_ordens_produto_para_update(
        self,
        *,
        cd_prod_cor: str,
        channel: str,
        order_ids: list[int] | None,
    ) -> list[OrdemProdutoState]:
        states = await produto_repo.carregar_ordens_produto_para_update(
            self.db,
            cd_prod_cor=cd_prod_cor,
            channel=channel,
            order_ids=order_ids,
        )
        return [
            OrdemProdutoState(
                nr_pedido=state.nr_pedido,
                cd_prod_cor=state.cd_prod_cor,
                tipo=state.tipo,
                itens=state.itens,
                created_at=state.created_at,
                aprovado_em=state.aprovado_em,
            )
            for state in states
        ]

    async def carregar_estoque_disponivel_alvos(
        self, targets: set[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], int]:
        return await estoque_repo.carregar_estoque_disponivel_alvos(self.db, targets)

    async def carregar_orcamento_pedidos(
        self, nr_pedidos: set[int]
    ) -> dict[int, OrcamentoPedidoSnapshot]:
        return await orcamento_repo.carregar_orcamento_pedidos(self.db, nr_pedidos)

    async def carregar_tolerancia_adequacao(self) -> float:
        settings = get_settings()
        return await param_service.get_param_value(
            self.db,
            "tolerancia_adequacao",
            default=settings.tolerancia_adequacao,
            cast=float,
        )

    async def atualizar_grades_em_lote(
        self, updates: list[dict]
    ) -> tuple[tuple[int, str], ...]:
        return await produto_repo.atualizar_grades_em_lote(self.db, updates)

    async def upsert_modificacoes_em_lote(self, values: list[dict]) -> None:
        await produto_repo.upsert_modificacoes_em_lote(self.db, values)

    async def recalcular_estoque_virtual_alvos(
        self, targets: set[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], int]:
        return await estoque_repo.recalcular_estoque_virtual_alvos(self.db, targets)

    async def totais_produto_em_edicao(
        self,
        *,
        cd_prod_cor: str,
        channel: str,
        limite_criacao: datetime,
    ) -> tuple[int, Decimal]:
        return await produto_repo.totais_produto_em_edicao(
            self.db,
            cd_prod_cor=cd_prod_cor,
            channel=channel,
            limite_criacao=limite_criacao,
        )

    async def aprovar_produto_canal(
        self,
        *,
        cd_prod_cor: str,
        channel: str,
        limite_criacao: datetime,
    ) -> ResultadoAprovacaoProduto:
        result = await produto_repo.aprovar_produto_canal(
            self.db,
            cd_prod_cor=cd_prod_cor,
            channel=channel,
            limite_criacao=limite_criacao,
        )
        return ResultadoAprovacaoProduto(
            matched_count=result.matched_count,
            approved_pairs=result.approved_pairs,
            already_approved_count=result.already_approved_count,
            expired_count=result.expired_count,
        )

    async def carregar_pares_aprovaveis_pedido_para_update(
        self,
        *,
        nr_pedido: int,
        cd_prod_cor: str | None,
        limite_criacao: datetime,
    ) -> tuple[tuple[int, str], ...]:
        return await produto_repo.carregar_pares_aprovaveis_pedido_para_update(
            self.db,
            nr_pedido=nr_pedido,
            cd_prod_cor=cd_prod_cor,
            limite_criacao=limite_criacao,
        )

    async def aprovar_ordem_reserva(
        self,
        nr_pedido: int,
        cd_prod_cor: str | None,
        *,
        limite_criacao: datetime,
    ) -> ResultadoAprovacaoOrdem:
        result = await ordens_repo.aprovar_ordem_reserva(
            self.db,
            nr_pedido,
            cd_prod_cor,
            limite_criacao=limite_criacao,
        )
        return ResultadoAprovacaoOrdem(
            encontrados=result.encontrados,
            alterados=result.alterados,
        )

    async def record_event(
        self, *, topic: str, event_type: str, payload: dict[str, object]
    ) -> None:
        await record_event(
            self.db,
            topic=topic,
            event_type=event_type,
            payload=payload,
        )

    async def observe_entity_keys(self, topic: str, keys: Iterable[str]) -> list[str]:
        return await observe_entity_keys(self.db, topic, keys)

    async def observe_active_entity_keys_scoped(
        self,
        topic: str,
        keys: Iterable[str],
        *,
        scope_prefixes: Iterable[str | int],
    ) -> list[str]:
        return await observe_active_entity_keys_scoped(
            self.db,
            topic,
            keys,
            scope_prefixes=scope_prefixes,
        )

    async def commit(self) -> None:
        await self.db.commit()


__all__ = ["SqlAlchemyPedidosWriteUnitOfWork"]
