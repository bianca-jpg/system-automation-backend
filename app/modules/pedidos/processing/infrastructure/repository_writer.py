"""Aplicação idempotente de um chunk já congelado do plano (writes, sem commit)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pedidos.infrastructure import (
    repositorio_estoque_virtual as estoque_repo,
)
from app.modules.pedidos.infrastructure import repositorio_ordens as ordens_repo
from app.modules.pedidos.infrastructure import repositorio_ordens_linx as linx_repo
from app.modules.pedidos.infrastructure.models import (
    OrdemReserva,
    OrdemReservaLinx,
    PedidoStandbyMotivo,
)
from app.modules.pedidos.processing.domain import (
    APPLY_CHUNK_SIZE,
    STOCK_TARGET_CHUNK_SIZE,
    PlannedPair,
    ProcessingPlanConflict,
    fingerprint_pair_source,
)
from app.modules.pedidos.processing.infrastructure.chunking import _chunks


class SqlAlchemyProcessingWriter:
    """Aplica um chunk já congelado sem consultas globais e sem commit."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def _payload(
        row: PlannedPair,
    ) -> tuple[str, list[dict], dict | None, set[tuple[str, str, str]]]:
        payload: Mapping[str, Any] = row.payload
        tipo = payload.get("type")
        items = payload.get("items")
        linx = payload.get("linx")
        targets = payload.get("stockTargets")
        if tipo not in {"com", "sem"} or not isinstance(items, list):
            raise ProcessingPlanConflict("processing_plan_payload_invalid")
        if linx is not None and not isinstance(linx, dict):
            raise ProcessingPlanConflict("processing_plan_linx_invalid")
        if not isinstance(targets, list):
            raise ProcessingPlanConflict("processing_plan_stock_targets_invalid")
        parsed_targets: set[tuple[str, str, str]] = set()
        for target in targets:
            if not isinstance(target, list) or len(target) != 3:
                raise ProcessingPlanConflict("processing_plan_stock_target_invalid")
            parsed_targets.add((str(target[0]), str(target[1]), str(target[2])))
        return str(tipo), [dict(item) for item in items], linx, parsed_targets

    async def _assert_plan_still_current(
        self,
        rows: Sequence[PlannedPair],
    ) -> None:
        pairs = [(row.nr_pedido, row.cd_prod_cor) for row in rows]
        existing_or = await self._db.scalar(
            select(OrdemReserva.nr_pedido)
            .where(tuple_(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor).in_(pairs))
            .order_by(OrdemReserva.nr_pedido, OrdemReserva.cd_prod_cor)
            .with_for_update()
            .limit(1)
        )
        if existing_or is not None:
            # Checkpoint e OR são uma única transação. Uma OR existente para uma
            # row ainda planned só pode ser mutação externa/stale, nunca replay.
            raise ProcessingPlanConflict("processing_plan_stale")
        existing_linx = await self._db.scalar(
            select(OrdemReservaLinx.nr_pedido)
            .where(
                tuple_(OrdemReservaLinx.nr_pedido, OrdemReservaLinx.cd_prod_cor).in_(
                    pairs
                )
            )
            .limit(1)
        )
        if existing_linx is not None:
            raise ProcessingPlanConflict("processing_plan_stale")

        key_payload = json.dumps(
            [{"nr_pedido": nr, "cd_prod_cor": code} for nr, code in sorted(pairs)],
            ensure_ascii=False,
        )
        current_rows = (
            (
                await self._db.execute(
                    text(
                        """
                        WITH requested AS (
                            SELECT key.nr_pedido, key.cd_prod_cor
                            FROM jsonb_to_recordset(CAST(:pairs AS jsonb))
                                AS key(nr_pedido integer, cd_prod_cor text)
                        )
                        SELECT
                            p.id, p.nr_pedido, p.cd_prod_cor, p.sg_tamanho,
                            p.ds_grupo, p.qt_entregar AS qt_liquida, p.vl_liquido,
                            p.status_credito, p.client, p.canal, p.ds_produto, p.data,
                            p.indica_blacklist
                        FROM pedidos p
                        JOIN requested key
                          ON key.nr_pedido = p.nr_pedido
                         AND key.cd_prod_cor = p.cd_prod_cor
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
                            SELECT 1 FROM ordens_reserva current_or
                            WHERE current_or.nr_pedido = p.nr_pedido
                              AND current_or.cd_prod_cor = p.cd_prod_cor
                        )
                        ORDER BY p.nr_pedido, p.cd_prod_cor, p.sg_tamanho, p.id
                        """
                    ),
                    {"pairs": key_payload},
                )
            )
            .mappings()
            .all()
        )
        by_pair: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for current in current_rows:
            pair = (int(current["nr_pedido"]), str(current["cd_prod_cor"]))
            by_pair[pair].append(
                {
                    "nr_pedido": pair[0],
                    "cd_prod_cor": pair[1],
                    "sg_tamanho": str(current["sg_tamanho"]),
                    "ds_grupo": str(current["ds_grupo"]),
                    "qt_liquida": int(current["qt_liquida"]),
                    "vl_liquido": float(current["vl_liquido"]),
                    "status_credito": current["status_credito"],
                    "client": current["client"],
                    "canal": current["canal"],
                    "ds_produto": current["ds_produto"],
                    "data": current["data"],
                    "indica_blacklist": bool(current["indica_blacklist"]),
                }
            )
        for planned in rows:
            source_hash = planned.payload.get("sourceHash")
            current = by_pair.get((planned.nr_pedido, planned.cd_prod_cor))
            if (
                not isinstance(source_hash, str)
                or current is None
                or fingerprint_pair_source(current) != source_hash
            ):
                raise ProcessingPlanConflict("processing_plan_stale")

    async def _assert_stock_capacity(self, rows: Sequence[PlannedPair]) -> None:
        requested: dict[tuple[str, str, str], int] = defaultdict(int)
        for row in rows:
            _tipo, items, _linx, _targets = self._payload(row)
            channel = str(row.payload.get("channel") or "")
            for item in items:
                if item.get("status_item") == "Pedido em Stand By":
                    continue
                qty = max(int(item.get("qt_liquida") or 0), 0)
                size = str(item.get("sg_tamanho") or "").strip().upper()
                if qty:
                    requested[(row.cd_prod_cor, size, channel)] += qty
        targets = sorted(requested)
        available: dict[tuple[str, str, str], int] = {}
        for target_chunk in _chunks(targets, STOCK_TARGET_CHUNK_SIZE):
            available.update(
                await estoque_repo.carregar_estoque_disponivel_alvos(
                    self._db,
                    set(target_chunk),
                )
            )
        if any(requested[target] > available.get(target, 0) for target in targets):
            raise ProcessingPlanConflict("processing_plan_stale")

    async def apply_pairs(self, rows: Sequence[PlannedPair]) -> None:
        if not rows or len(rows) > APPLY_CHUNK_SIZE:
            raise ValueError("processing_apply_chunk_out_of_range")
        pairs = {(row.nr_pedido, row.cd_prod_cor) for row in rows}
        if len(pairs) != len(rows):
            raise ProcessingPlanConflict("processing_apply_duplicate_pair")

        await self._assert_plan_still_current(rows)
        await self._assert_stock_capacity(rows)
        values: list[dict[str, Any]] = []
        for planned in rows:
            tipo, items, _linx, _targets = self._payload(planned)
            values.append(
                {
                    "nr_pedido": planned.nr_pedido,
                    "cd_prod_cor": planned.cd_prod_cor,
                    "tipo": tipo,
                    "itens": items,
                }
            )
        inserted = (
            await self._db.scalars(
                pg_insert(OrdemReserva)
                .values(values)
                .on_conflict_do_nothing(
                    index_elements=[
                        OrdemReserva.nr_pedido,
                        OrdemReserva.cd_prod_cor,
                    ]
                )
                .returning(OrdemReserva.nr_pedido)
            )
        ).all()
        if len(inserted) != len(values):
            raise ProcessingPlanConflict("processing_plan_stale")

        await ordens_repo.salvar_processados(self._db, set(pairs))
        # O par acabou de virar OR, então não está mais em stand-by: a linha
        # de `pedido_standby_motivo` tem de sair na MESMA transação que
        # gravou a OR (critério 3 do ROADMAP), simétrico às demais escritas
        # deste método. `sorted(pairs)` (não o `set` cru) para ordem
        # determinística de aquisição de lock de linha, mesmo argumento que
        # já governa `sorted(stock_targets)` mais abaixo. A limpeza de
        # órfãos do full refresh de ingestão (`reconstruir_pedido_produto_read`)
        # é deliberadamente de outro plano (Pitfall 3 do 16-RESEARCH.md).
        await self._db.execute(
            delete(PedidoStandbyMotivo).where(
                tuple_(
                    PedidoStandbyMotivo.nr_pedido, PedidoStandbyMotivo.cd_prod_cor
                ).in_(sorted(pairs))
            )
        )
        linx_rows: list[dict] = []
        stock_targets: set[tuple[str, str, str]] = set()
        for row in rows:
            _tipo, _items, linx, targets = self._payload(row)
            if linx is not None:
                if (
                    int(linx.get("nr_pedido", 0)) != row.nr_pedido
                    or str(linx.get("cd_prod_cor", "")) != row.cd_prod_cor
                ):
                    raise ProcessingPlanConflict("processing_plan_linx_pair_mismatch")
                linx_rows.append(linx)
            stock_targets.update(targets)
        if linx_rows:
            await linx_repo.salvar_linhas_linx(self._db, linx_rows)
        sorted_targets = sorted(stock_targets)
        for target_chunk in _chunks(sorted_targets, STOCK_TARGET_CHUNK_SIZE):
            await estoque_repo.recalcular_estoque_virtual_alvos(
                self._db,
                set(target_chunk),
            )


__all__ = ["SqlAlchemyProcessingWriter"]
