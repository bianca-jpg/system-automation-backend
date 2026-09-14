"""Composição SQL das fontes de planejamento e do evento consolidado."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.parametros import service as param_service
from app.modules.pedidos.domain.value_objects import CANAIS, montar_chave_estoque
from app.modules.pedidos.infrastructure import (
    repositorio_estoque_virtual as estoque_repo,
)
from app.modules.pedidos.infrastructure import (
    repositorio_ingestao_readmodel as ingestao_repo,
)
from app.modules.pedidos.infrastructure.repositorio_orcamento_pedido import (
    carregar_pedido_budget_tuplas,
)
from app.modules.pedidos.infrastructure.resumo.listar_chaves_alertas_ativos import (
    listar_chaves_alertas_ativos_sql,
)
from app.modules.pedidos.processing.domain import (
    InvalidProcessingRequest,
    PendingSnapshotStats,
    ProcessingChannel,
)
from app.modules.realtime import observe_active_entity_keys, record_event
from app.shared.config.settings import get_settings

_PENDING_BASE_SQL = """
pending_base AS (
    SELECT
        p.id, p.nr_pedido, p.cd_prod_cor, p.sg_tamanho, p.ds_grupo,
        p.qt_entregar AS qt_liquida, p.vl_liquido,
        p.status_credito, p.client, p.canal, p.ds_produto, p.data,
        p.indica_blacklist,
        CASE
            WHEN upper(coalesce(p.canal, '')) LIKE 'MULTIMARCA%'
              OR upper(coalesce(p.canal, '')) LIKE 'MM%'
            THEN 'Multimarca'
            WHEN upper(coalesce(p.canal, '')) LIKE 'FRANQUIA%'
              OR upper(coalesce(p.canal, '')) LIKE 'FRQ%'
            THEN 'Franquia'
            ELSE NULL
        END AS canonical_channel
    FROM pedidos p
    WHERE NOT EXISTS (
        SELECT 1 FROM pedidos_processados local_done
        WHERE local_done.nr_pedido = p.nr_pedido
          AND local_done.cd_prod_cor = p.cd_prod_cor
    )
      AND NOT EXISTS (
        SELECT 1 FROM pedidos_processados_erp erp_done
        WHERE erp_done.nr_pedido = p.nr_pedido
          AND erp_done.cd_prod_cor = p.cd_prod_cor
    )
      AND NOT EXISTS (
        SELECT 1 FROM ordens_reserva existing_or
        WHERE existing_or.nr_pedido = p.nr_pedido
          AND existing_or.cd_prod_cor = p.cd_prod_cor
    )
),
pair_scope AS (
    SELECT
        nr_pedido,
        cd_prod_cor,
        CASE
            WHEN count(*) FILTER (WHERE canonical_channel IS NULL) = 0
             AND count(DISTINCT canonical_channel) = 1
            THEN min(canonical_channel)
            ELSE NULL
        END AS canonical_channel,
        bool_or(canonical_channel = :channel) AS touches_requested_channel
    FROM pending_base
    GROUP BY nr_pedido, cd_prod_cor
),
invalid_scope AS (
    SELECT count(*)::integer AS invalid_count
    FROM pair_scope
    WHERE canonical_channel IS NULL
      AND (:channel = 'Todos' OR touches_requested_channel)
),
eligible_pairs AS (
    SELECT nr_pedido, cd_prod_cor
    FROM pair_scope
    WHERE canonical_channel IS NOT NULL
      AND (:channel = 'Todos' OR canonical_channel = :channel)
),
eligible_items AS (
    SELECT base.*
    FROM pending_base base
    JOIN eligible_pairs USING (nr_pedido, cd_prod_cor)
)
"""


class SqlAlchemyProcessingPlannerSource:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def inspect_pending(self, channel: ProcessingChannel) -> PendingSnapshotStats:
        row = (
            await self._db.execute(
                text(
                    f"""
                    WITH {_PENDING_BASE_SQL}
                    , pair_stats AS (
                        SELECT
                            nr_pedido,
                            cd_prod_cor,
                            count(*)::integer AS item_count,
                            coalesce(
                                sum(pg_column_size(to_jsonb(eligible_items))),
                                0
                            )::bigint AS estimated_bytes
                        FROM eligible_items
                        GROUP BY nr_pedido, cd_prod_cor
                    )
                    SELECT
                        (SELECT invalid_count FROM invalid_scope) AS invalid_count,
                        count(*)::integer AS pair_count,
                        coalesce(sum(item_count), 0)::integer AS item_count,
                        coalesce(sum(estimated_bytes), 0)::bigint AS estimated_bytes,
                        coalesce(max(item_count), 0)::integer AS max_pair_item_count
                    FROM pair_stats
                    """
                ),
                {"channel": channel.value},
            )
        ).one()
        if int(row.invalid_count or 0):
            raise InvalidProcessingRequest("pending_pair_channel_is_not_canonical")
        return PendingSnapshotStats(
            pair_count=int(row.pair_count or 0),
            item_count=int(row.item_count or 0),
            estimated_bytes=int(row.estimated_bytes or 0),
            max_pair_item_count=int(row.max_pair_item_count or 0),
        )

    async def load_pending_items(self, channel: ProcessingChannel) -> list[dict]:
        result = await self._db.stream(
            text(
                f"""
                WITH {_PENDING_BASE_SQL}
                SELECT
                    nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo,
                    qt_liquida, vl_liquido, status_credito, client,
                    canal, ds_produto, data, indica_blacklist
                FROM eligible_items
                ORDER BY nr_pedido DESC, cd_prod_cor, sg_tamanho, id
                """
            ).execution_options(yield_per=2_000),
            {"channel": channel.value},
        )
        items: list[dict] = []
        try:
            async for row in result.mappings():
                items.append(
                    {
                        "nr_pedido": int(row["nr_pedido"]),
                        "cd_prod_cor": str(row["cd_prod_cor"]),
                        "sg_tamanho": str(row["sg_tamanho"]),
                        "ds_grupo": str(row["ds_grupo"]),
                        "qt_liquida": int(row["qt_liquida"]),
                        "vl_liquido": float(row["vl_liquido"]),
                        "status_credito": row["status_credito"],
                        "client": row["client"],
                        "canal": row["canal"],
                        "ds_produto": row["ds_produto"],
                        "data": row["data"],
                        "indica_blacklist": bool(row["indica_blacklist"]),
                    }
                )
        finally:
            await result.close()
        return items

    async def load_stock(self, product_codes: set[str]) -> dict[str, dict[str, int]]:
        """Carrega só códigos candidatos; cada cálculo usa <=200 alvos."""

        result: dict[str, dict[str, int]] = {channel: {} for channel in CANAIS}
        codes = sorted(product_codes)
        for offset in range(0, len(codes), 200):
            code_chunk = codes[offset : offset + 200]
            stock_rows = (
                await self._db.execute(
                    text(
                        """
                        SELECT cd_prod_cor, sg_tamanho, canal
                        FROM estoque
                        WHERE cd_prod_cor = ANY(CAST(:codes AS text[]))
                        ORDER BY cd_prod_cor, sg_tamanho, canal
                        """
                    ),
                    {"codes": code_chunk},
                )
            ).all()
            targets = [
                (str(code), str(size).strip().upper(), str(channel))
                for code, size, channel in stock_rows
                if channel in CANAIS
            ]
            for target_offset in range(0, len(targets), 200):
                target_chunk = set(targets[target_offset : target_offset + 200])
                available = await estoque_repo.carregar_estoque_disponivel_alvos(
                    self._db,
                    target_chunk,
                )
                for (code, size, channel), qty in available.items():
                    result[channel][montar_chave_estoque(code, size)] = qty
        return result

    async def load_adequation_config(self) -> tuple[str, float]:
        settings = get_settings()
        criterion = await param_service.get_param_value(
            self._db,
            "criterio_selecao",
            default=settings.criterio_selecao,
            cast=str,
        )
        tolerance = await param_service.get_param_value(
            self._db,
            "tolerancia_adequacao",
            default=settings.tolerancia_adequacao,
            cast=float,
        )
        return criterion, tolerance

    async def load_size_reference(
        self, product_codes: set[str]
    ) -> dict[str, dict[str, int]]:
        return await ingestao_repo.carregar_referencia_posicoes(
            self._db,
            product_codes,
        )

    async def load_pedido_budget(
        self, nr_pedidos: set[int]
    ) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
        """Orçamento por pedido (ALOC-09): total original — incluindo a
        correção do denominador que encolhe (invariante J5): soma também o
        `qt_solicitada` de ORs cujo par já sumiu de `pedidos` — e o consumido
        prévio de adição/corte em ORs anteriores, sem compensar um pelo outro.

        A implementação agora vive em
        `infrastructure/repositorio_orcamento_pedido.py` (Phase 20): a SQL
        passou a ser compartilhada com a leitura sob demanda para a edição
        manual de "sem adequação", que precisa do ramo agregado por total do
        par (PD-01) que o motor automático não usava. Esta assinatura e este
        contrato de retorno (três dicionários paralelos) não mudam.
        """

        return await carregar_pedido_budget_tuplas(self._db, nr_pedidos)


class SqlAlchemyProcessingRealtime:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record_progress(
        self,
        *,
        event_id: UUID,
        occurred_at: datetime,
        payload: Mapping[str, object],
    ) -> None:
        await record_event(
            self._db,
            topic="orders",
            event_type="orders.processing.progress.v1",
            payload=dict(payload),
            occurred_at=occurred_at,
            event_id=event_id,
        )

    async def reconcile_alerts(
        self,
        *,
        event_id: UUID,
        occurred_at: datetime,
        reason: str,
    ) -> None:
        active = await listar_chaves_alertas_ativos_sql(self._db)
        new_keys = await observe_active_entity_keys(
            self._db,
            "alerts",
            (f"{order_id}:{alert_type}" for order_id, alert_type in active),
            baseline_if_empty=True,
            allow_empty_snapshot=True,
        )
        if new_keys:
            await record_event(
                self._db,
                topic="alerts",
                event_type="alerts.new.v1",
                payload={"reason": reason, "count": len(new_keys)},
                occurred_at=occurred_at,
                event_id=event_id,
            )

    async def record_completion(
        self,
        *,
        event_id: UUID,
        occurred_at: datetime,
        payload: Mapping[str, object],
    ) -> None:
        await record_event(
            self._db,
            topic="orders",
            event_type="orders.processing.completed.v1",
            payload=dict(payload),
            occurred_at=occurred_at,
            event_id=event_id,
        )


__all__ = ["SqlAlchemyProcessingPlannerSource", "SqlAlchemyProcessingRealtime"]
