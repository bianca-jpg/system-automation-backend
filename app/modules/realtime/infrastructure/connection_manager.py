"""Hub local de WebSockets com filas bounded e deduplicação."""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from fastapi import WebSocket

from app.shared.config.settings import Settings

from .metrics import (
    ACTIVE_CONNECTIONS,
    DELIVERED_EVENTS,
    DROPPED_CONNECTIONS,
    WS_CLOSED,
)

_DEDUPE_WINDOW = 2_048


class ConnectionLimitError(RuntimeError):
    pass


@dataclass(slots=True)
class RealtimeConnection:
    websocket: WebSocket
    user_id: int
    topics: frozenset[str]
    queue_size: int
    access_expires_at: datetime | None = None
    connection_id: str = field(default_factory=lambda: uuid4().hex)
    last_pong_at: float = field(default_factory=monotonic)
    queue: asyncio.Queue[dict[str, Any]] = field(init=False)
    _seen_event_ids: OrderedDict[str, None] = field(
        init=False, default_factory=OrderedDict
    )

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.queue_size)

    def remember_event(self, event_id: str) -> bool:
        if event_id in self._seen_event_ids:
            self._seen_event_ids.move_to_end(event_id)
            return False
        self._seen_event_ids[event_id] = None
        if len(self._seen_event_ids) > _DEDUPE_WINDOW:
            self._seen_event_ids.popitem(last=False)
        return True

    def note_pong(self) -> None:
        self.last_pong_at = monotonic()


class LocalConnectionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connections: dict[str, RealtimeConnection] = {}
        self._by_user: dict[int, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(
        self,
        websocket: WebSocket,
        *,
        user_id: int,
        topics: frozenset[str],
        access_expires_at: datetime | None = None,
    ) -> RealtimeConnection:
        async with self._lock:
            if (
                len(self._connections)
                >= self._settings.realtime_max_connections_per_instance
            ):
                raise ConnectionLimitError("limite de conexões da instância atingido")
            if (
                len(self._by_user[user_id])
                >= self._settings.realtime_max_connections_per_user
            ):
                raise ConnectionLimitError("limite de conexões do usuário atingido")
            connection = RealtimeConnection(
                websocket=websocket,
                user_id=user_id,
                topics=topics,
                queue_size=self._settings.realtime_connection_queue_size,
                access_expires_at=access_expires_at,
            )
            self._connections[connection.connection_id] = connection
            self._by_user[user_id].add(connection.connection_id)
            ACTIVE_CONNECTIONS.inc()
            return connection

    async def unregister(
        self, connection: RealtimeConnection, *, reason: str = "client"
    ) -> None:
        async with self._lock:
            removed = self._connections.pop(connection.connection_id, None)
            if removed is None:
                return
            user_connections = self._by_user.get(connection.user_id)
            if user_connections is not None:
                user_connections.discard(connection.connection_id)
                if not user_connections:
                    self._by_user.pop(connection.user_id, None)
            ACTIVE_CONNECTIONS.dec()
            WS_CLOSED.labels(reason=reason).inc()

    async def broadcast(self, envelope: dict[str, Any]) -> None:
        topic = envelope.get("topic")
        event_id = envelope.get("eventId")
        if not isinstance(topic, str) or not isinstance(event_id, str):
            return
        connections = list(self._connections.values())
        overloaded: list[RealtimeConnection] = []
        for connection in connections:
            if topic not in connection.topics or not connection.remember_event(
                event_id
            ):
                continue
            try:
                connection.queue.put_nowait(envelope)
                DELIVERED_EVENTS.inc()
            except asyncio.QueueFull:
                overloaded.append(connection)

        for connection in overloaded:
            DROPPED_CONNECTIONS.inc()
            await self.unregister(connection, reason="backpressure")
            asyncio.create_task(
                self._safe_close(connection.websocket, code=1013, reason="backpressure")
            )

    async def disconnect_all(self, *, code: int, reason: str) -> None:
        connections = list(self._connections.values())
        for connection in connections:
            await self.unregister(connection, reason=reason)
        if connections:
            await asyncio.gather(
                *(
                    self._safe_close(
                        connection.websocket,
                        code=code,
                        reason=reason,
                    )
                    for connection in connections
                ),
                return_exceptions=True,
            )

    async def close_all(self) -> None:
        await self.disconnect_all(code=1001, reason="shutdown")

    @staticmethod
    async def _safe_close(websocket: WebSocket, *, code: int, reason: str) -> None:
        with contextlib.suppress(RuntimeError, TimeoutError):
            await asyncio.wait_for(
                websocket.close(code=code, reason=reason[:123]),
                timeout=2.0,
            )
