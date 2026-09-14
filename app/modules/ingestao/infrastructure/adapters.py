"""Adapters concretos dos ports da aplicação de ingestão."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.modules.health.service import (
    FonteIntegracao,
    registrar_falha_integracao,
    registrar_sucesso_integracao,
)
from app.modules.ingestao.infrastructure.databricks_reader import (
    ler_estoque,
    ler_faturamento_colecao,
    ler_pedidos_em_aberto,
    ler_pedidos_processados_erp,
    ler_referencia_tamanhos,
)
from app.modules.ingestao.infrastructure.models import (
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
    ProdutoTamanhoPosicao,
)
from app.modules.ingestao.infrastructure.realtime_snapshot import (
    carregar_chaves_realtime,
)
from app.modules.ingestao.infrastructure.repositorio_snapshot import (
    reconstruir_pedido_produto_read,
    substituir_estoque,
    substituir_faturamento_colecao,
    substituir_pedidos,
    substituir_pedidos_processados_erp,
    substituir_referencia_tamanhos,
)
from app.modules.realtime import (
    enqueue_realtime_event,
    observe_active_entity_keys,
    observe_entity_keys,
)
from app.shared.database.advisory_locks import (
    INGESTION_JOB_LOCK,
    ORDER_STATE_MUTATION_LOCK,
)
from app.shared.database.session import async_session_factory
from app.shared.infrastructure.databricks_client import DatabricksError

logger = logging.getLogger(__name__)

_MUTATION_LOCK_TIMEOUT_SECONDS = 12.0
_MUTATION_LOCK_POLL_SECONDS = 0.2


async def _ler_databricks_com_aviso_estoque(
    ler: Callable[[], Awaitable[list[dict]]],
) -> list[dict]:
    """Executa uma leitura do Databricks reportando falha/sucesso na fonte
    `estoque` (D-02/D-07).

    Compartilhado entre `pedidos_em_aberto` e `estoque`: na prática, uma
    rejeição do Databricks (token expirado, warehouse parado — `DatabricksError`
    com qualquer `kind`) derruba a PRIMEIRA leitura de `_coletar_snapshot`
    (`app/modules/ingestao/application/casos_uso.py::_coletar_snapshot`), que é
    `pedidos_em_aberto`, não `estoque`. Sem isso, a coleta aborta ali e
    `estoque()` nunca chega a ser chamado — o aviso nunca abre mesmo com o
    Databricks genuinamente fora do ar, porque a instrumentação original só
    cobria o método que, na ordem real de chamadas, nunca roda. Descoberto ao
    testar com o Databricks de fato instável (2026-09-09): 5 jobs de sync
    consecutivos falharam com `databricks_http_rejected` e nenhum aviso abriu.

    Do ponto de vista de quem usa o sistema, "não conseguimos falar com o
    Databricks" e "o estoque está indisponível" são o mesmo evento — daí as
    duas leituras reportarem na mesma fonte `FonteIntegracao.ESTOQUE`, sem
    introduzir uma fonte nova.
    """

    try:
        linhas = await ler()
    except DatabricksError as exc:
        await registrar_falha_integracao(
            FonteIntegracao.ESTOQUE, detalhe_tecnico=exc.kind
        )
        await _publicar_evento_estoque_realtime(reason="estoque_indisponivel")
        raise

    aviso_resolvido = await registrar_sucesso_integracao(FonteIntegracao.ESTOQUE)
    if aviso_resolvido is not None:
        await _publicar_evento_estoque_realtime(reason="estoque_recuperado")
    return linhas


async def _publicar_evento_estoque_realtime(*, reason: str) -> None:
    """Live-update instantâneo do aviso de `estoque` (D-07): publica no tópico
    `alerts` numa sessão PRÓPRIA, nunca na sessão de escrita da ingestão (que
    nem existe ainda quando `estoque()` falha — a falha acontece na coleta,
    antes de `_aplicar_snapshot` abrir a transação).

    Best-effort: nunca propaga. Uma falha aqui (ex.: Postgres também fora do
    ar) não pode mascarar o `DatabricksError` original, que é levantado por
    fora, independentemente do resultado desta função. `payload` carrega só
    `reason` (nunca `detalhe_tecnico`/nome de exceção — D-04 vale também para
    o que trafega no WebSocket).
    """

    try:
        async with async_session_factory() as db:
            await enqueue_realtime_event(
                db,
                topic="alerts",
                event_type="alerts.changed.v1",
                payload={"reason": reason},
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Falha ao publicar evento realtime de estoque (reason=%s): %s",
            reason,
            type(exc).__name__,
        )


class DatabricksIngestionSource:
    async def pedidos_em_aberto(self) -> list[dict]:
        return await _ler_databricks_com_aviso_estoque(ler_pedidos_em_aberto)

    async def estoque(self) -> list[dict]:
        return await _ler_databricks_com_aviso_estoque(ler_estoque)

    async def pedidos_processados_erp(self) -> list[dict]:
        return await ler_pedidos_processados_erp()

    async def faturamento_colecao(self) -> list[dict]:
        return await ler_faturamento_colecao()

    async def referencia_tamanhos(self) -> list[dict]:
        return await ler_referencia_tamanhos()


class SqlSnapshotRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def acquire_mutation_lock(self) -> bool:
        """Espera brevemente um write corrente sem repetir a coleta externa."""

        deadline = time.monotonic() + _MUTATION_LOCK_TIMEOUT_SECONDS
        while True:
            acquired = bool(
                await self.db.scalar(
                    select(func.pg_try_advisory_xact_lock(ORDER_STATE_MUTATION_LOCK))
                )
            )
            if acquired:
                return True
            restante = deadline - time.monotonic()
            if restante <= 0:
                return False
            await asyncio.sleep(min(_MUTATION_LOCK_POLL_SECONDS, restante))

    async def _count(self, model: type) -> int:
        return int(await self.db.scalar(select(func.count()).select_from(model)) or 0)

    async def count_pedidos(self) -> int:
        return await self._count(Pedido)

    async def count_estoque(self) -> int:
        return await self._count(Estoque)

    async def count_processados_erp(self) -> int:
        return await self._count(PedidoProcessadoErp)

    async def count_faturamento(self) -> int:
        return await self._count(FaturamentoColecao)

    async def count_referencia_tamanhos(self) -> int:
        return await self._count(ProdutoTamanhoPosicao)

    async def replace_pedidos(self, itens: Iterable[dict]) -> None:
        await substituir_pedidos(self.db, itens)

    async def replace_estoque(
        self,
        agregado: dict[tuple, int],
        *,
        dt_estoque: date | None,
    ) -> None:
        await substituir_estoque(self.db, agregado, dt_estoque=dt_estoque)

    async def replace_processados_erp(self, itens: Iterable[dict]) -> None:
        await substituir_pedidos_processados_erp(self.db, itens)

    async def replace_faturamento(self, itens: Iterable[dict]) -> None:
        await substituir_faturamento_colecao(self.db, itens)

    async def replace_referencia_tamanhos(self, itens: Iterable[dict]) -> None:
        await substituir_referencia_tamanhos(self.db, itens)

    async def rebuild_read_model(self) -> int:
        return await reconstruir_pedido_produto_read(self.db)

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()


class SqlRealtimeSnapshotAdapter:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def load_keys(
        self,
    ) -> tuple[list[tuple[int, str]], list[tuple[int, str]], list[tuple[int, str]]]:
        return await carregar_chaves_realtime(self.db)

    async def observe_entities(
        self,
        topic: str,
        keys: Iterable[str] | Mapping[str, object],
    ) -> list[str]:
        return await observe_entity_keys(self.db, topic, keys)

    async def observe_active_entities(
        self,
        topic: str,
        keys: Mapping[str, object],
        *,
        allow_empty_snapshot: bool,
    ) -> list[str]:
        return await observe_active_entity_keys(
            self.db,
            topic,
            keys,
            allow_empty_snapshot=allow_empty_snapshot,
        )

    async def enqueue(
        self,
        *,
        topic: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        await enqueue_realtime_event(
            self.db,
            topic=topic,
            event_type=event_type,
            payload=payload,
        )


def _engine_from_session(db: AsyncSession) -> AsyncEngine:
    bind = db.bind
    if isinstance(bind, AsyncEngine):
        return bind
    if isinstance(bind, AsyncConnection):
        return bind.engine
    raise RuntimeError("AsyncSession de ingestão não possui AsyncEngine configurado")


class PostgresIngestionJobLock:
    """Lock de sessão em conexão dedicada, seguro através da coleta externa."""

    def __init__(self, db: AsyncSession) -> None:
        self.engine = _engine_from_session(db)

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[bool]:
        async with self.engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": INGESTION_JOB_LOCK},
                )
            )
            await connection.commit()
            if not acquired:
                yield False
                return
            try:
                yield True
            finally:
                released = bool(
                    await connection.scalar(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": INGESTION_JOB_LOCK},
                    )
                )
                await connection.commit()
                if not released:
                    logger.error(
                        "Lock de job da ingestão não estava adquirido ao liberar"
                    )


__all__ = [
    "DatabricksIngestionSource",
    "PostgresIngestionJobLock",
    "SqlRealtimeSnapshotAdapter",
    "SqlSnapshotRepository",
]
