from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.domain.roles import automationRole, has_min_level
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import decode_access_token
from app.modules.realtime import service as use_cases
from app.modules.realtime.application.schemas import (
    MarkReadRequest,
    MarkReadResponse,
    RealtimeStatusResponse,
    TicketResponse,
)
from app.modules.realtime.domain import (
    CursorAheadError,
    InvalidRealtimeEvent,
    RealtimeUnavailableError,
    ReplayRequiredError,
    validate_topic,
)
from app.modules.realtime.infrastructure.connection_manager import (
    ConnectionLimitError,
    RealtimeConnection,
)
from app.modules.realtime.infrastructure.redis_gateway import TicketRateLimitedError
from app.modules.realtime.infrastructure.runtime import get_realtime_runtime
from app.shared.config.settings import get_settings
from app.shared.database.session import async_session_factory, get_db
from app.shared.security import CurrentUser, bearer_scheme, require_viewer

router = APIRouter(prefix="/api/v1/realtime", tags=["Realtime"])

_MAX_TOPICS_QUERY_LENGTH = 512
_MAX_CLIENT_MESSAGE_BYTES = 1_024


def _parse_topics(raw: str | None) -> list[str]:
    settings = get_settings()
    if raw is None or not raw.strip():
        return settings.realtime_allowed_topics_list
    if len(raw) > _MAX_TOPICS_QUERY_LENGTH:
        raise InvalidRealtimeEvent("topics excede 512 caracteres")
    return list(dict.fromkeys(_validate_allowed_topic(item) for item in raw.split(",")))


def _validate_allowed_topic(raw: str) -> str:
    topic = validate_topic(raw)
    if topic not in set(get_settings().realtime_allowed_topics_list):
        raise InvalidRealtimeEvent(f"tópico não permitido: {topic}")
    return topic


@router.post("/tickets", response_model=TicketResponse)
async def issue_ticket(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_viewer)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> TicketResponse:
    settings = get_settings()
    topics = settings.realtime_allowed_topics_list
    try:
        if credentials is None:
            raise JWTError("access token ausente")
        payload = decode_access_token(credentials.credentials)
        if int(payload["sub"]) != user.id:
            raise JWTError("access token pertence a outro usuário")
        access_expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    except (JWTError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acesso inválido ou expirado",
        ) from exc

    try:
        auth_user = await db.get(AuthUser, user.id)
        if (
            auth_user is None
            or auth_user.confirmed_at is None
            or not has_min_level(list(auth_user.roles or []), automationRole.BASICO)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuário sem acesso ao realtime",
            )
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Realtime ainda não está disponível neste ambiente",
        ) from exc

    runtime = get_realtime_runtime()
    client_ip = request.client.host if request.client else "unknown"
    try:
        ticket = await runtime.gateway.issue_ticket(
            user_id=user.id,
            client_ip=client_ip,
            access_expires_at=access_expires_at,
            topics=topics,
        )
    except TicketRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Limite de tickets realtime excedido",
            headers={"Retry-After": "60"},
        ) from exc
    except RealtimeUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Realtime temporariamente indisponível",
            headers={"Retry-After": "2"},
        ) from exc

    # O rate limit Redis ocorre antes da escrita de baseline. Um abuso de
    # emissão não gera transações desnecessárias no Postgres; se o baseline
    # falhar, o ticket opaco expira sozinho em poucos segundos e não autentica
    # um socket sem cursor/schema válido.
    try:
        await use_cases.initialize_user_baseline(db, user_id=user.id, topics=topics)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Realtime ainda não está disponível neste ambiente",
        ) from exc

    return TicketResponse.model_validate(
        {
            "ticket": ticket,
            "expires_in": settings.realtime_ticket_ttl_seconds,
            "websocket_url": "/api/v1/realtime/ws",
        }
    )


@router.get("/status", response_model=RealtimeStatusResponse)
async def get_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_viewer)],
    topics: Annotated[str | None, Query(max_length=_MAX_TOPICS_QUERY_LENGTH)] = None,
) -> dict[str, object]:
    try:
        requested_topics = _parse_topics(topics)
    except InvalidRealtimeEvent as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return await use_cases.realtime_status(
            db, user_id=user.id, topics=requested_topics
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Realtime ainda não está disponível neste ambiente",
        ) from exc


@router.post("/read", response_model=MarkReadResponse)
async def mark_read(
    body: MarkReadRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_viewer)],
) -> dict[str, object]:
    try:
        topic = _validate_allowed_topic(body.topic)
        result = await use_cases.mark_read(
            db,
            user_id=user.id,
            topic=topic,
            through_sequence=body.through_sequence,
        )
        await db.commit()
        return result
    except CursorAheadError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReplayRequiredError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidRealtimeEvent as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Realtime ainda não está disponível neste ambiente",
        ) from exc


async def _close(websocket: WebSocket, *, code: int, reason: str) -> None:
    with contextlib.suppress(RuntimeError, TimeoutError):
        await asyncio.wait_for(
            websocket.close(code=code, reason=reason[:123]),
            timeout=2.0,
        )


async def _fresh_user(user_id: int) -> AuthUser | None:
    async with async_session_factory() as db:
        user = await db.get(AuthUser, user_id)
        if (
            user is None
            or user.confirmed_at is None
            or not has_min_level(list(user.roles or []), automationRole.BASICO)
        ):
            return None
        # Materializa antes de fechar a sessão; o socket nunca segura conexão DB.
        _ = list(user.roles or [])
        return user


async def _sender(connection: RealtimeConnection) -> str:
    timeout = get_settings().realtime_send_timeout_seconds
    while True:
        message = await connection.queue.get()
        try:
            await asyncio.wait_for(
                connection.websocket.send_json(message),
                timeout=timeout,
            )
        except TimeoutError:
            await _close(connection.websocket, code=1013, reason="send_timeout")
            return "backpressure"


def _take_buffered_replay(
    connection: RealtimeConnection,
    *,
    after_sequence: int,
    through_sequence: int,
) -> list[dict[str, object]]:
    """Retira eventos live <= watermark que chegaram durante o replay.

    O hub marca eventId ao enfileirar. Por isso o replay DB pula esses IDs e
    este drain precisa enviá-los antes de ``replay_complete``. Eventos >M ficam
    na fila para o sender normal.
    """

    buffered: list[tuple[int, dict[str, object]]] = []
    retained: list[dict[str, object]] = []
    while True:
        try:
            message = connection.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        sequence = message.get("sequence")
        if isinstance(sequence, int) and sequence <= through_sequence:
            if sequence > after_sequence:
                buffered.append((sequence, message))
        else:
            retained.append(message)
    for message in retained:
        connection.queue.put_nowait(message)
    return [
        message for _sequence, message in sorted(buffered, key=lambda item: item[0])
    ]


async def _receiver(connection: RealtimeConnection) -> str:
    websocket = connection.websocket
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return "client"
            raw = message.get("text")
            if (
                not isinstance(raw, str)
                or len(raw.encode("utf-8")) > _MAX_CLIENT_MESSAGE_BYTES
            ):
                await _close(websocket, code=1009, reason="message_too_large")
                return "invalid_message"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                await _close(websocket, code=1007, reason="invalid_message")
                return "invalid_message"
            if not isinstance(body, dict) or body.get("type") != "pong":
                await _close(websocket, code=1003, reason="unsupported_message")
                return "invalid_message"
            connection.note_pong()
    except WebSocketDisconnect:
        return "client"


async def _watchdog(connection: RealtimeConnection) -> str:
    settings = get_settings()
    next_auth_check = monotonic() + settings.realtime_auth_revalidate_seconds
    while True:
        await asyncio.sleep(settings.realtime_heartbeat_seconds)
        now = monotonic()
        if (
            connection.access_expires_at is not None
            and datetime.now(UTC) >= connection.access_expires_at
        ):
            await _close(connection.websocket, code=1008, reason="auth_expired")
            return "auth"
        if now - connection.last_pong_at > settings.realtime_idle_timeout_seconds:
            await _close(connection.websocket, code=1001, reason="idle_timeout")
            return "idle"
        if now >= next_auth_check:
            if await _fresh_user(connection.user_id) is None:
                await _close(connection.websocket, code=1008, reason="auth_revoked")
                return "auth"
            next_auth_check = now + settings.realtime_auth_revalidate_seconds
        try:
            connection.queue.put_nowait({"type": "ping", "sentAt": int(now * 1_000)})
        except asyncio.QueueFull:
            await _close(connection.websocket, code=1013, reason="backpressure")
            return "backpressure"


@router.websocket("/ws")
async def websocket_realtime(
    websocket: WebSocket,
    ticket: str = Query(min_length=32, max_length=128),
    last_sequence: int = Query(
        default=0,
        alias="lastSequence",
        ge=0,
        le=9_223_372_036_854_775_807,
    ),
    topics: str | None = Query(default=None, max_length=_MAX_TOPICS_QUERY_LENGTH),
) -> None:
    settings = get_settings()
    origin = websocket.headers.get("origin")
    allowed_origins = {value.rstrip("/") for value in settings.cors_origins_list}
    if origin is None or origin.rstrip("/") not in allowed_origins:
        await _close(websocket, code=1008, reason="origin_not_allowed")
        return

    try:
        requested_topics = frozenset(_parse_topics(topics))
    except InvalidRealtimeEvent:
        await _close(websocket, code=1008, reason="invalid_topics")
        return

    runtime = get_realtime_runtime()
    try:
        claims = await runtime.gateway.consume_ticket(ticket)
    except RealtimeUnavailableError:
        await _close(websocket, code=1013, reason="realtime_unavailable")
        return
    if claims is None:
        # Expiração/reuso do ticket pede nova emissão; não representa uma
        # revogação permanente da autorização do usuário.
        await _close(websocket, code=4408, reason="invalid_ticket")
        return
    if datetime.now(UTC) >= claims.access_expires_at:
        await _close(websocket, code=1008, reason="auth_expired")
        return
    if requested_topics != claims.topics:
        await _close(websocket, code=1008, reason="ticket_topics_mismatch")
        return

    user = await _fresh_user(claims.user_id)
    if user is None:
        await _close(websocket, code=1008, reason="auth_revoked")
        return

    try:
        connection = await runtime.manager.register(
            websocket,
            user_id=user.id,
            topics=requested_topics,
            access_expires_at=claims.access_expires_at,
        )
    except ConnectionLimitError:
        await _close(websocket, code=1013, reason="connection_limit")
        return

    reason = "client"
    tasks: set[asyncio.Task[str]] = set()
    try:
        await websocket.accept()
        try:
            async with asyncio.timeout(settings.realtime_replay_timeout_seconds):
                async with async_session_factory() as db:
                    status_snapshot = await use_cases.realtime_status(
                        db, user_id=user.id, topics=sorted(requested_topics)
                    )
                await asyncio.wait_for(
                    websocket.send_json(
                        {
                            "type": "hello",
                            "version": 1,
                            "lastSequence": last_sequence,
                            "latestSequence": status_snapshot["lastSequence"],
                            "heartbeatSeconds": settings.realtime_heartbeat_seconds,
                            "topics": status_snapshot["topics"],
                        }
                    ),
                    timeout=settings.realtime_send_timeout_seconds,
                )

                try:
                    async with async_session_factory() as db:
                        replay = await use_cases.replay_events(
                            db,
                            topics=sorted(requested_topics),
                            after_sequence=last_sequence,
                            limit=settings.realtime_replay_max_events,
                            through_sequence=int(status_snapshot["lastSequence"]),
                        )
                except ReplayRequiredError as exc:
                    await asyncio.wait_for(
                        websocket.send_json(
                            {
                                "type": "resync_required",
                                "reason": str(exc),
                                "lastSequence": status_snapshot["lastSequence"],
                            }
                        ),
                        timeout=settings.realtime_send_timeout_seconds,
                    )
                    await _close(websocket, code=1012, reason="resync_required")
                    reason = "resync"
                    return
                else:
                    replay_watermark = int(status_snapshot["lastSequence"])
                    for envelope in replay:
                        if datetime.now(UTC) >= claims.access_expires_at:
                            await _close(
                                websocket,
                                code=1008,
                                reason="auth_expired",
                            )
                            reason = "auth"
                            return
                        if connection.remember_event(str(envelope.event_id)):
                            await asyncio.wait_for(
                                websocket.send_json(envelope.as_dict()),
                                timeout=settings.realtime_send_timeout_seconds,
                            )
                    for buffered in _take_buffered_replay(
                        connection,
                        after_sequence=last_sequence,
                        through_sequence=replay_watermark,
                    ):
                        await asyncio.wait_for(
                            websocket.send_json(buffered),
                            timeout=settings.realtime_send_timeout_seconds,
                        )
                    await asyncio.wait_for(
                        websocket.send_json(
                            {
                                "type": "replay_complete",
                                "lastSequence": replay_watermark,
                            }
                        ),
                        timeout=settings.realtime_send_timeout_seconds,
                    )
        except TimeoutError:
            await _close(websocket, code=1013, reason="replay_timeout")
            reason = "backpressure"
            return

        tasks = {
            asyncio.create_task(_sender(connection), name="realtime-ws-sender"),
            asyncio.create_task(_receiver(connection), name="realtime-ws-receiver"),
            asyncio.create_task(_watchdog(connection), name="realtime-ws-watchdog"),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is None:
                reason = task.result()
                break
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (WebSocketDisconnect, RuntimeError):
        reason = "client"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.manager.unregister(connection, reason=reason)
