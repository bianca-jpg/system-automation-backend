"""Ciclo de vida do relay PostgreSQL/Redis e fanout de cada instância API."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from app.modules.realtime.domain import (
    InvalidRealtimeEvent,
    RealtimeEventEnvelope,
    RealtimeUnavailableError,
    normalize_payload,
)
from app.shared.config.settings import Settings, get_settings
from app.shared.database.session import async_session_factory
from app.shared.infrastructure.redis_client import get_redis

from . import repository
from .connection_manager import LocalConnectionManager
from .metrics import OUTBOX_PENDING, RELAY_FAILURES
from .redis_gateway import RedisRealtimeGateway

logger = logging.getLogger(__name__)

_RELAY_LEADER_LOCK = 7_401_802


class RealtimeRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.instance_id = uuid4().hex
        self.manager = LocalConnectionManager(settings)
        self.gateway = RedisRealtimeGateway(get_redis(), settings)
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            return
        # TestClient e graceful restart podem fechar o singleton Redis entre
        # lifespans; renova o gateway sem recriar o hub usado pelas rotas.
        self.gateway = RedisRealtimeGateway(get_redis(), self.settings)
        self._tasks = [
            asyncio.create_task(self._relay_loop(), name="realtime-outbox-relay"),
            asyncio.create_task(self._stream_loop(), name="realtime-stream-listener"),
            asyncio.create_task(self._cleanup_loop(), name="realtime-outbox-cleanup"),
        ]
        logger.info(
            "Runtime realtime iniciado", extra={"instance_id": self.instance_id}
        )

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=5.0)
            if pending:
                logger.warning(
                    "Tasks realtime não encerraram dentro do timeout",
                    extra={"pending_tasks": len(pending)},
                )
                for task in pending:
                    task.cancel()
        await self.manager.close_all()
        logger.info(
            "Runtime realtime encerrado", extra={"instance_id": self.instance_id}
        )

    async def _relay_loop(self) -> None:
        interval = self.settings.realtime_relay_interval_ms / 1_000
        failure_backoff = 1.0
        while True:
            try:
                processed_any = False
                # O transaction-level lock cobre claim, XADD e ack do ciclo.
                # Cancelamento/rollback sempre o libera; não existe session
                # lock capaz de voltar preso ao pool durante o teardown.
                async with async_session_factory() as db:
                    is_leader = bool(
                        await db.scalar(
                            text("SELECT pg_try_advisory_xact_lock(:key)"),
                            {"key": _RELAY_LEADER_LOCK},
                        )
                    )
                    if not is_leader:
                        await db.rollback()
                        await asyncio.sleep(interval)
                        continue

                    for _ in range(self.settings.realtime_relay_batch_size):
                        claimed = await repository.claim_outbox_batch(
                            db,
                            instance_id=self.instance_id,
                            batch_size=1,
                        )
                        if not claimed:
                            break
                        processed_any = True
                        row = claimed[0]
                        envelope = RealtimeEventEnvelope(
                            event_id=row.event_id,
                            sequence=row.sequence,
                            event_type=row.event_type,
                            topic=row.topic,
                            occurred_at=row.occurred_at,
                            payload=row.payload,
                            version=row.version,
                        )
                        try:
                            await self.gateway.publish_envelope(envelope.as_dict())
                        except RealtimeUnavailableError as exc:
                            RELAY_FAILURES.inc()
                            await repository.mark_publish_failed(
                                db,
                                sequence=row.sequence,
                                instance_id=self.instance_id,
                                attempts=row.attempts,
                                error_name=exc.__class__.__name__,
                            )
                            break

                        marked = await repository.mark_published(
                            db,
                            sequence=row.sequence,
                            instance_id=self.instance_id,
                        )
                        if not marked:
                            raise RuntimeError("lease do outbox expirou antes do ack")
                    await db.commit()

                if processed_any:
                    async with async_session_factory() as db:
                        OUTBOX_PENDING.set(await repository.pending_outbox_count(db))
                failure_backoff = 1.0
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - runtime deve se autorrecuperar
                logger.warning(
                    "Relay realtime degradado; nova tentativa agendada",
                    extra={"error_type": exc.__class__.__name__},
                )
                await asyncio.sleep(failure_backoff)
                failure_backoff = min(failure_backoff * 2, 30.0)

    @staticmethod
    def _parse_envelope(
        raw: dict[str, object], *, payload_max_bytes: int
    ) -> dict[str, Any]:
        occurred_raw = raw.get("occurredAt")
        payload = raw.get("payload")
        if not isinstance(occurred_raw, str) or not isinstance(payload, dict):
            raise InvalidRealtimeEvent("envelope Redis incompleto")
        envelope = RealtimeEventEnvelope(
            event_id=UUID(str(raw.get("eventId"))),
            sequence=int(str(raw.get("sequence"))),
            event_type=str(raw.get("type")),
            topic=str(raw.get("topic")),
            occurred_at=datetime.fromisoformat(occurred_raw),
            payload=normalize_payload(payload, max_bytes=payload_max_bytes),
            version=int(str(raw.get("version"))),
        )
        return envelope.as_dict()

    async def _stream_loop(self) -> None:
        # Sem consumer group: cada instância mantém seu próprio cursor e recebe
        # todas as entradas, garantindo fanout para seus sockets locais.
        last_id = "$"
        last_sequence: int | None = None
        degraded = False
        backoff = 1.0
        while True:
            try:
                last_id, raw_envelopes = await self.gateway.read_stream(
                    after_id=last_id
                )
                if degraded:
                    # Tickets podem voltar a funcionar entre a recuperação do
                    # Redis e esta primeira leitura. Fecha também essas conexões
                    # para que todas recomecem pelo replay PostgreSQL.
                    await self.manager.disconnect_all(
                        code=1013,
                        reason="transport_recovered",
                    )
                    degraded = False
                backoff = 1.0
                for raw in raw_envelopes:
                    try:
                        envelope = self._parse_envelope(
                            raw,
                            payload_max_bytes=self.settings.realtime_payload_max_bytes,
                        )
                    except (InvalidRealtimeEvent, TypeError, ValueError):
                        logger.warning("Entrada inválida ignorada no Redis Stream")
                        continue
                    sequence = int(envelope["sequence"])
                    if last_sequence is not None and sequence <= last_sequence:
                        # XADD é pelo menos uma vez; repetição já entregue não
                        # deve alterar a progressão do listener.
                        continue
                    if last_sequence is not None and sequence > last_sequence + 1:
                        async with async_session_factory() as db:
                            gap = await repository.stream_gap_requires_resync(
                                db,
                                after_sequence=last_sequence,
                                before_sequence=sequence,
                            )
                        if gap:
                            logger.warning(
                                "Gap detectado no Redis Stream; sockets locais "
                                "serão reconciliados via replay PostgreSQL"
                            )
                            await self.manager.disconnect_all(
                                code=1013,
                                reason="stream_gap",
                            )
                    last_sequence = sequence
                    await self.manager.broadcast(envelope)
            except asyncio.CancelledError:
                raise
            except RealtimeUnavailableError as exc:
                logger.warning(
                    "Listener realtime degradado; mantendo REST operacional",
                    extra={"error_type": exc.__class__.__name__},
                )
                # Um listener parado pode perder entradas já aparadas pelo
                # MAXLEN. Fecha sockets locais para que o cliente reconecte e
                # use o replay durável do PostgreSQL, em vez de manter uma
                # conexão aparentemente saudável com um gap silencioso.
                await self.manager.disconnect_all(
                    code=1013,
                    reason="transport_unavailable",
                )
                last_sequence = None
                degraded = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as exc:  # noqa: BLE001 - listener se autorrecupera
                logger.warning(
                    "Listener realtime falhou; sockets usarão replay no retry",
                    extra={"error_type": exc.__class__.__name__},
                )
                await self.manager.disconnect_all(
                    code=1013,
                    reason="listener_error",
                )
                last_sequence = None
                degraded = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.settings.realtime_cleanup_interval_seconds)
                async with async_session_factory() as db:
                    deleted = await repository.cleanup_published(
                        db,
                        retention_days=self.settings.realtime_outbox_retention_days,
                    )
                    await db.commit()
                if deleted:
                    logger.info(
                        "Retenção do outbox realtime aplicada",
                        extra={"deleted_rows": deleted},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - cleanup não derruba API
                logger.warning(
                    "Cleanup realtime falhou; será repetido",
                    extra={"error_type": exc.__class__.__name__},
                )


_runtime: RealtimeRuntime | None = None


def get_realtime_runtime() -> RealtimeRuntime:
    global _runtime
    if _runtime is None:
        _runtime = RealtimeRuntime(get_settings())
    return _runtime
