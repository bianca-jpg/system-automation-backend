from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.modules.realtime.domain import RealtimeEventEnvelope, ReplayRequiredError
from app.modules.realtime.infrastructure.connection_manager import (
    LocalConnectionManager,
    RealtimeConnection,
)
from app.modules.realtime.infrastructure.http.routes import _take_buffered_replay
from app.modules.realtime.infrastructure.redis_gateway import TicketClaims
from app.shared.config.settings import Settings
from app.shared.database.session import get_db
from app.shared.security import CurrentUser, require_viewer

_TICKET = "t" * 43
_ORIGIN = "http://testserver"
_ACCESS_EXPIRES_AT = datetime(2099, 1, 1, tzinfo=UTC)
_TOPICS = frozenset({"orders", "alerts", "communications", "history"})


def _settings() -> Settings:
    overrides: dict[str, object] = {"CORS_ORIGINS": _ORIGIN}
    # `_env_file` é um kwarg especial de `BaseSettings.__init__` (não um campo
    # do modelo); o pyright sintetiza o __init__ a partir dos campos e não
    # reconhece esse parâmetro, mesmo sendo válido em runtime. Confinado a
    # esta única linha do helper.
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


def _claims(
    *,
    access_expires_at: datetime = _ACCESS_EXPIRES_AT,
    topics: frozenset[str] = _TOPICS,
) -> TicketClaims:
    return TicketClaims(
        user_id=42,
        access_expires_at=access_expires_at,
        topics=topics,
    )


def _status_snapshot() -> dict[str, object]:
    return {
        "lastSequence": 12,
        "topics": {
            "orders": {
                "latestSequence": 12,
                "lastReadSequence": 10,
                "unseen": 2,
                "latest": None,
            },
            "alerts": {
                "latestSequence": 8,
                "lastReadSequence": 8,
                "unseen": 0,
                "latest": None,
            },
        },
    }


@pytest.fixture
def realtime_client():
    db = AsyncMock()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_viewer] = lambda: CurrentUser(
        id=42,
        email="realtime-test@project.com",
        roles=["basico"],
    )
    client = TestClient(app)
    try:
        yield client, db
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_viewer, None)


def test_emite_ticket(realtime_client):
    client, db = realtime_client
    settings = _settings()
    gateway = SimpleNamespace(issue_ticket=AsyncMock(return_value=_TICKET))
    runtime = SimpleNamespace(gateway=gateway)
    initialize = AsyncMock()
    db.get.return_value = SimpleNamespace(
        confirmed_at=datetime(2026, 8, 8, tzinfo=UTC),
        roles=["basico"],
    )

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.initialize_user_baseline",
            new=initialize,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.decode_access_token",
            return_value={
                "sub": "42",
                "exp": int(_ACCESS_EXPIRES_AT.timestamp()),
            },
        ),
    ):
        response = client.post(
            "/api/v1/realtime/tickets",
            headers={"Authorization": "Bearer test-jwt"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ticket": _TICKET,
        "expiresIn": 30,
        "websocketUrl": "/api/v1/realtime/ws",
    }
    initialize.assert_awaited_once_with(
        db,
        user_id=42,
        topics=["orders", "alerts", "communications", "history"],
    )
    db.commit.assert_awaited_once()
    gateway.issue_ticket.assert_awaited_once_with(
        user_id=42,
        client_ip="testclient",
        access_expires_at=_ACCESS_EXPIRES_AT,
        topics=["orders", "alerts", "communications", "history"],
    )


def test_ticket_exige_bearer_mesmo_com_guard_de_usuario_resolvido(realtime_client):
    client, _db = realtime_client

    response = client.post("/api/v1/realtime/tickets")

    assert response.status_code == 401


def test_status_preserva_mapa_de_topicos(realtime_client):
    client, db = realtime_client
    snapshot = _status_snapshot()
    realtime_status = AsyncMock(return_value=snapshot)

    with patch(
        "app.modules.realtime.infrastructure.http.routes.use_cases.realtime_status",
        new=realtime_status,
    ):
        response = client.get(
            "/api/v1/realtime/status",
            params={"topics": "orders,alerts"},
        )

    assert response.status_code == 200
    assert response.json() == snapshot
    realtime_status.assert_awaited_once_with(
        db,
        user_id=42,
        topics=["orders", "alerts"],
    )


def test_status_recusa_query_de_topicos_acima_do_limite(realtime_client):
    client, _db = realtime_client

    response = client.get(
        "/api/v1/realtime/status",
        params={"topics": "o" * 513},
    )

    assert response.status_code == 422


def test_mark_read_recusa_lista_de_topicos_em_campo_singular(realtime_client):
    client, _db = realtime_client
    mark_read = AsyncMock(
        return_value={"topic": "orders", "lastReadSequence": 12, "unseen": 0}
    )

    with patch(
        "app.modules.realtime.infrastructure.http.routes.use_cases.mark_read",
        new=mark_read,
    ):
        response = client.post(
            "/api/v1/realtime/read",
            json={"topic": "orders,alerts", "throughSequence": 12},
        )

    assert response.status_code == 422
    mark_read.assert_not_awaited()


def test_websocket_recusa_origin_nao_permitida(realtime_client):
    client, _db = realtime_client
    settings = _settings()
    gateway = SimpleNamespace(consume_ticket=AsyncMock(return_value=_claims()))
    runtime = SimpleNamespace(gateway=gateway)

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}",
            headers={"origin": "https://evil.example"},
        ),
    ):
        pass

    assert exc_info.value.code == 1008
    gateway.consume_ticket.assert_not_awaited()


def test_websocket_recusa_ticket_invalido(realtime_client):
    client, _db = realtime_client
    settings = _settings()
    gateway = SimpleNamespace(consume_ticket=AsyncMock(return_value=None))
    runtime = SimpleNamespace(gateway=gateway)

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}",
            headers={"origin": _ORIGIN},
        ),
    ):
        pass

    assert exc_info.value.code == 4408
    gateway.consume_ticket.assert_awaited_once_with(_TICKET)


@pytest.mark.parametrize(
    "claims,query",
    [
        (_claims(access_expires_at=datetime(2000, 1, 1, tzinfo=UTC)), ""),
        (_claims(), "&topics=orders"),
    ],
)
def test_websocket_recusa_access_expirado_ou_topicos_fora_do_ticket(
    realtime_client,
    claims,
    query,
):
    client, _db = realtime_client
    settings = _settings()
    runtime = SimpleNamespace(
        gateway=SimpleNamespace(consume_ticket=AsyncMock(return_value=claims)),
    )

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}{query}",
            headers={"origin": _ORIGIN},
        ),
    ):
        pass

    assert exc_info.value.code == 1008


def test_websocket_hello_inclui_status_dos_topicos_solicitados(realtime_client):
    client, _db = realtime_client
    settings = _settings()
    snapshot = _status_snapshot()
    gateway = SimpleNamespace(consume_ticket=AsyncMock(return_value=_claims()))
    runtime = SimpleNamespace(
        gateway=gateway,
        manager=LocalConnectionManager(settings),
    )

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes._fresh_user",
            new=AsyncMock(return_value=SimpleNamespace(id=42)),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.realtime_status",
            new=AsyncMock(return_value=snapshot),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.replay_events",
            new=AsyncMock(return_value=[]),
        ),
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}",
            headers={"origin": _ORIGIN},
        ) as websocket,
    ):
        hello = websocket.receive_json()
        replay_complete = websocket.receive_json()
        websocket.close()

    assert hello["type"] == "hello"
    assert hello["lastSequence"] == 0
    assert hello["latestSequence"] == 12
    assert hello["topics"] == snapshot["topics"]
    assert replay_complete == {"type": "replay_complete", "lastSequence": 12}


def test_websocket_aplica_replay_antes_de_avancar_ao_watermark(realtime_client):
    client, _db = realtime_client
    settings = _settings()
    runtime = SimpleNamespace(
        gateway=SimpleNamespace(consume_ticket=AsyncMock(return_value=_claims())),
        manager=LocalConnectionManager(settings),
    )
    replay = [
        RealtimeEventEnvelope(
            event_id=UUID(int=sequence),
            sequence=sequence,
            event_type="order.changed",
            topic="orders",
            occurred_at=datetime(2026, 8, 8, 4, sequence, tzinfo=UTC),
            payload={"orderId": sequence},
        )
        for sequence in (11, 12)
    ]

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes._fresh_user",
            new=AsyncMock(return_value=SimpleNamespace(id=42)),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.realtime_status",
            new=AsyncMock(return_value=_status_snapshot()),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.replay_events",
            new=AsyncMock(return_value=replay),
        ) as replay_events,
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}&lastSequence=10",
            headers={"origin": _ORIGIN},
        ) as websocket,
    ):
        frames = [websocket.receive_json() for _ in range(4)]
        websocket.close()

    assert [frame["type"] for frame in frames] == [
        "hello",
        "order.changed",
        "order.changed",
        "replay_complete",
    ]
    assert frames[0]["lastSequence"] == 10
    assert frames[0]["latestSequence"] == 12
    assert [frames[1]["sequence"], frames[2]["sequence"]] == [11, 12]
    assert frames[3]["lastSequence"] == 12
    replay_events.assert_awaited_once()
    assert replay_events.await_args is not None
    assert replay_events.await_args.kwargs["after_sequence"] == 10
    assert replay_events.await_args.kwargs["through_sequence"] == 12


def test_buffer_live_durante_replay_preserva_apenas_eventos_apos_watermark():
    connection = RealtimeConnection(
        websocket=AsyncMock(),
        user_id=42,
        topics=_TOPICS,
        queue_size=8,
    )
    for sequence in (9, 11, 12, 13):
        connection.queue.put_nowait(
            {
                "type": "order.changed",
                "eventId": f"event-{sequence}",
                "sequence": sequence,
            }
        )
    connection.queue.put_nowait({"type": "ping"})

    replay_buffer = _take_buffered_replay(
        connection,
        after_sequence=10,
        through_sequence=12,
    )

    assert [message["sequence"] for message in replay_buffer] == [11, 12]
    assert connection.queue.get_nowait()["sequence"] == 13
    assert connection.queue.get_nowait() == {"type": "ping"}
    assert connection.queue.empty()


def test_websocket_ticket_nao_pode_ser_reutilizado(realtime_client):
    client, _db = realtime_client
    settings = _settings()
    gateway = SimpleNamespace(consume_ticket=AsyncMock(side_effect=[_claims(), None]))
    runtime = SimpleNamespace(
        gateway=gateway,
        manager=LocalConnectionManager(settings),
    )

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes._fresh_user",
            new=AsyncMock(return_value=SimpleNamespace(id=42)),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.realtime_status",
            new=AsyncMock(return_value=_status_snapshot()),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.replay_events",
            new=AsyncMock(return_value=[]),
        ),
    ):
        with client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}",
            headers={"origin": _ORIGIN},
        ) as websocket:
            assert websocket.receive_json()["type"] == "hello"
            assert websocket.receive_json()["type"] == "replay_complete"
            websocket.close()

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(
                f"/api/v1/realtime/ws?ticket={_TICKET}",
                headers={"origin": _ORIGIN},
            ),
        ):
            pass

    assert exc_info.value.code == 4408
    assert gateway.consume_ticket.await_count == 2


def test_websocket_cursor_adiante_exige_resync(realtime_client):
    client, _db = realtime_client
    settings = _settings()
    gateway = SimpleNamespace(consume_ticket=AsyncMock(return_value=_claims()))
    runtime = SimpleNamespace(
        gateway=gateway,
        manager=LocalConnectionManager(settings),
    )

    with (
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.get_realtime_runtime",
            return_value=runtime,
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes._fresh_user",
            new=AsyncMock(return_value=SimpleNamespace(id=42)),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.realtime_status",
            new=AsyncMock(return_value=_status_snapshot()),
        ),
        patch(
            "app.modules.realtime.infrastructure.http.routes.use_cases.replay_events",
            new=AsyncMock(
                side_effect=ReplayRequiredError("cursor 999 está além do watermark 12")
            ),
        ),
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}&lastSequence=999",
            headers={"origin": _ORIGIN},
        ) as websocket,
    ):
        assert websocket.receive_json()["type"] == "hello"
        resync = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert resync == {
        "type": "resync_required",
        "reason": "cursor 999 está além do watermark 12",
        "lastSequence": 12,
    }
    assert exc_info.value.code == 1012


def test_websocket_recusa_last_sequence_negativa(realtime_client):
    client, _db = realtime_client

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            f"/api/v1/realtime/ws?ticket={_TICKET}&lastSequence=-1",
            headers={"origin": _ORIGIN},
        ),
    ):
        pass

    assert exc_info.value.code == 1008
