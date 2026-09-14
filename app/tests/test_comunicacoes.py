import asyncio
import uuid
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import app
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.comunicacoes import service as communication_service
from app.modules.comunicacoes.application.schemas import EnviarComunicacaoRequest
from app.modules.comunicacoes.infrastructure.http.routes import enviar_comunicacao
from app.modules.comunicacoes.infrastructure.models import Comunicacao
from app.shared.config.settings import get_settings
from app.shared.infrastructure.rate_limit import enforce_dual_fixed_window
from app.shared.pagination.cursor import encode_cursor
from app.shared.security import (
    CurrentUser,
    get_current_user,
    require_actor,
    require_viewer,
)


class _FakeRateRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.eval_calls = 0
        self.seen_keys: list[str] = []
        self.fail = False

    async def eval(
        self,
        _script: str,
        _num_keys: int,
        user_key: str,
        ip_key: str,
        _window_seconds: int,
    ) -> list[int]:
        self.eval_calls += 1
        if self.fail:
            raise RedisError("redis indisponível")
        self.seen_keys.extend((user_key, ip_key))
        self.counts[user_key] = self.counts.get(user_key, 0) + 1
        self.counts[ip_key] = self.counts.get(ip_key, 0) + 1
        return [self.counts[user_key], self.counts[ip_key], 37]


def _viewer_override() -> CurrentUser:
    return CurrentUser(
        id=999998,
        email="viewer-comunicacoes@project.com",
        roles=["basico"],
    )


def _actor_override() -> CurrentUser:
    return CurrentUser(
        id=999999,
        email="actor-comunicacoes@project.com",
        roles=["operacional"],
    )


def _second_actor_override() -> CurrentUser:
    return CurrentUser(
        id=999997,
        email="actor2-comunicacoes@project.com",
        roles=["operacional"],
    )


def setup_module(_module):
    app.dependency_overrides[require_viewer] = _viewer_override
    app.dependency_overrides[require_actor] = _actor_override


def teardown_module(_module):
    app.dependency_overrides.pop(require_viewer, None)
    app.dependency_overrides.pop(require_actor, None)
    app.dependency_overrides.pop(get_current_user, None)


def _post_body(content: str = "Etapa0: pedido precisa de atencao.") -> dict:
    return {"recipient": "comercial@project.com", "content": content}


def _isolated_session_factory():
    database_url = get_settings().database_url
    assert (database_url.rsplit("/", 1)[-1]).endswith("_test"), (
        "testes de comunicação exigem um banco local terminado em _test"
    )
    engine = create_async_engine(database_url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _prepare_database() -> None:
    engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            for actor in (
                _viewer_override(),
                _actor_override(),
                _second_actor_override(),
            ):
                if await session.get(AuthUser, actor.id) is None:
                    session.add(
                        AuthUser(
                            id=actor.id,
                            email=actor.email or f"{actor.id}@example.test",
                            roles=actor.roles,
                            confirmed_at=datetime.now(UTC),
                        )
                    )
            await session.execute(delete(Comunicacao))
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def isolated_communications(monkeypatch) -> _FakeRateRedis:
    asyncio.run(_prepare_database())
    fake = _FakeRateRedis()
    monkeypatch.setattr(
        "app.modules.comunicacoes.infrastructure.http.routes.get_redis", lambda: fake
    )
    return fake


async def _replace_communications(rows: list[Comunicacao]) -> None:
    engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            await session.execute(delete(Comunicacao))
            session.add_all(rows)
            await session.commit()
    finally:
        await engine.dispose()


async def _delivery_snapshot(communication_id: str) -> dict:
    engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT status, attempt_count, message_id, subject "
                            "FROM communication_email_deliveries "
                            "WHERE communication_id = :communication_id"
                        ),
                        {"communication_id": communication_id},
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)
    finally:
        await engine.dispose()


async def _counts(communication_id: str) -> tuple[int, int]:
    engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            deliveries = int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM communication_email_deliveries "
                        "WHERE communication_id = :id"
                    ),
                    {"id": communication_id},
                )
                or 0
            )
            events = int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM realtime_outbox "
                        "WHERE event_type = 'communication.created' "
                        "AND payload->>'id' = :id"
                    ),
                    {"id": communication_id},
                )
                or 0
            )
            return deliveries, events
    finally:
        await engine.dispose()


class _AcceptedSender:
    async def enviar(self, _delivery) -> None:
        return None


async def _process_pending_once() -> None:
    engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            with patch.object(
                communication_service,
                "SmtpEmailSender",
                return_value=_AcceptedSender(),
            ):
                await communication_service.processar_entregas(session)
    finally:
        await engine.dispose()


def test_listar_comunicacoes_retorna_envelope_camel_case(client):
    response = client.get("/api/v1/comunicacoes?pageSize=25")
    assert response.status_code == 200
    assert set(response.json()) == {
        "rows",
        "total",
        "pageSize",
        "nextCursor",
        "hasMore",
    }


def test_paginacao_keyset_desempata_created_at_por_id_sem_repetir(client):
    instant = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)
    ids = [f"comm-keyset-{suffix}" for suffix in ("e", "d", "c", "b", "a")]
    asyncio.run(
        _replace_communications(
            [
                Comunicacao(
                    id=communication_id,
                    type="Email",
                    status="Enviado",
                    time="09:00",
                    content=communication_id,
                    recipient="comercial@project.com",
                    created_at=instant,
                )
                for communication_id in ids
            ]
        )
    )

    first = client.get("/api/v1/comunicacoes?pageSize=2").json()
    second = client.get(
        "/api/v1/comunicacoes",
        params={"pageSize": 2, "cursor": first["nextCursor"]},
    ).json()
    third = client.get(
        "/api/v1/comunicacoes",
        params={"pageSize": 2, "cursor": second["nextCursor"]},
    ).json()
    assert [row["id"] for row in first["rows"]] == ids[:2]
    assert [row["id"] for row in second["rows"]] == ids[2:4]
    assert [row["id"] for row in third["rows"]] == ids[4:]
    assert first["total"] == second["total"] == third["total"] == 5
    assert third["hasMore"] is False

    empty_cursor = encode_cursor(
        kind="communications",
        scope="created_at:desc,id:desc",
        key=[datetime(2098, 1, 1, tzinfo=UTC).isoformat(), "!"],
        secret=get_settings().jwt_secret,
    )
    empty = client.get(
        "/api/v1/comunicacoes", params={"pageSize": 2, "cursor": empty_cursor}
    ).json()
    assert empty["rows"] == []
    assert empty["total"] == 5


def test_paginacao_rejeita_limites_e_cursor_invalido(client):
    assert client.get("/api/v1/comunicacoes?pageSize=0").status_code == 422
    assert client.get("/api/v1/comunicacoes?pageSize=101").status_code == 422
    assert client.get("/api/v1/comunicacoes?cursor=nao-assinado").status_code == 422


def test_post_exige_actor_e_idempotency_key(client):
    missing_key = client.post("/api/v1/comunicacoes", json=_post_body())
    assert missing_key.status_code == 422

    app.dependency_overrides.pop(require_actor, None)
    app.dependency_overrides[get_current_user] = _viewer_override
    try:
        denied = client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json=_post_body(),
        )
    finally:
        app.dependency_overrides[require_actor] = _actor_override
        app.dependency_overrides.pop(get_current_user, None)
    assert denied.status_code == 403


def test_post_202_persiste_pending_antes_de_smtp_e_location(client):
    key = f"comm-test-{uuid.uuid4()}"
    with patch(
        "app.modules.comunicacoes.infrastructure.email_sender.aiosmtplib.send",
        new_callable=AsyncMock,
    ) as smtp:
        response = client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": key},
            json={**_post_body(), "orderRef": "12345"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "Pendente"
    assert body["attemptCount"] == 0
    assert body["maxAttempts"] == 5
    assert body["createdAt"]
    assert body["updatedAt"]
    assert body["nextAttemptAt"]
    assert "sentAt" not in body
    assert response.headers["Location"] == f"/api/v1/comunicacoes/{body['id']}"
    assert client.get(response.headers["Location"]).json() == body
    delivery = asyncio.run(_delivery_snapshot(body["id"]))
    assert delivery["status"] == "pending"
    assert delivery["attempt_count"] == 0
    assert delivery["subject"].endswith("Pedido 12345")
    assert delivery["message_id"].startswith("<")
    assert delivery["message_id"].endswith("@system-automation.project.com.br>")
    smtp.assert_not_awaited()


def test_get_status_reflete_progresso_do_worker_sem_dados_do_provider(client):
    created = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": f"status-progress-{uuid.uuid4()}"},
        json=_post_body("acompanhar progresso"),
    )
    assert created.status_code == 202
    asyncio.run(_process_pending_once())

    status_response = client.get(created.headers["Location"])
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "Enviado"
    assert body["attemptCount"] == 1
    assert body["maxAttempts"] == 5
    assert body["sentAt"]
    assert body["updatedAt"]
    assert "nextAttemptAt" not in body
    assert "lastError" not in status_response.text
    assert "provider" not in status_response.text.lower()


def test_post_nao_publica_no_broker_inline_e_beat_reconcilia(client):
    with patch("app.workers.celery_app.celery_app.send_task") as publish:
        response = client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": f"broker-offline-{uuid.uuid4()}"},
            json=_post_body("beat deve reconciliar"),
        )
    assert response.status_code == 202
    assert response.json()["status"] == "Pendente"
    assert asyncio.run(_delivery_snapshot(response.json()["id"]))["status"] == "pending"
    publish.assert_not_called()


def test_replay_mesma_key_retorna_mesmo_row_sem_novo_delivery_evento_ou_job(client):
    key = f"comm-test-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=_post_body("mensagem original"),
    )
    replay = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=_post_body("mensagem original"),
    )

    assert first.status_code == replay.status_code == 202
    assert replay.json() == first.json()
    assert asyncio.run(_counts(first.json()["id"])) == (1, 1)


def test_mesma_key_payload_diferente_retorna_409(client):
    key = f"comm-test-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=_post_body("mensagem original"),
    )
    conflict = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=_post_body("mensagem diferente"),
    )
    assert first.status_code == 202
    assert conflict.status_code == 409


def test_idempotencia_e_isolada_por_ator_sem_vazar_payload(client):
    key = f"actor-scope-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=_post_body("segredo do primeiro ator"),
    )
    app.dependency_overrides[require_actor] = _second_actor_override
    try:
        second = client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": key},
            json=_post_body("conteudo independente do segundo ator"),
        )
    finally:
        app.dependency_overrides[require_actor] = _actor_override

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert second.json()["content"] == "conteudo independente do segundo ator"
    assert "segredo" not in second.text


def test_rate_limit_bloqueia_nova_intencao_mas_permite_replay(
    client, isolated_communications
):
    keys = [f"rate-test-{uuid.uuid4()}" for _ in range(6)]
    allowed = [
        client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": key},
            json=_post_body(f"mensagem rate {index}"),
        )
        for index, key in enumerate(keys[:5])
    ]
    blocked = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": keys[5]},
        json=_post_body("mensagem bloqueada"),
    )
    replay = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": keys[0]},
        json=_post_body("mensagem rate 0"),
    )
    assert all(response.status_code == 202 for response in allowed)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "37"
    assert replay.json() == allowed[0].json()
    assert isolated_communications.eval_calls == 6


async def test_rate_limit_aplica_teto_de_ip_com_usuarios_distintos():
    redis = _FakeRateRedis()
    decisions = [
        await enforce_dual_fixed_window(
            cast(Redis, redis),
            namespace="smtp_test",
            user_identity=f"usuario-{index}",
            ip_identity="203.0.113.10",
            secret="segredo-de-teste",
            user_limit=5,
            ip_limit=20,
        )
        for index in range(21)
    ]
    assert all(decision.allowed for decision in decisions[:20])
    assert decisions[20].allowed is False
    assert all("203.0.113.10" not in key for key in redis.seen_keys)


def test_rate_limit_indisponivel_bloqueia_nova_intencao_mas_nao_replay(
    client, isolated_communications, caplog
):
    key = f"rate-closed-{uuid.uuid4()}"
    body = _post_body("envio persistido antes da indisponibilidade")
    created = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=body,
    )
    assert created.status_code == 202

    isolated_communications.fail = True
    replay = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": key},
        json=body,
    )
    blocked = client.post(
        "/api/v1/comunicacoes",
        headers={"Idempotency-Key": f"rate-closed-{uuid.uuid4()}"},
        json=_post_body("nova intenção durante indisponibilidade"),
    )
    assert replay.status_code == 202
    assert replay.json() == created.json()
    assert blocked.status_code == 503
    assert blocked.headers["Retry-After"] == "30"
    assert client.get("/api/v1/comunicacoes").json()["total"] == 1
    assert "Rate limit SMTP indisponível" in caplog.text
    assert str(_actor_override().id) not in caplog.text


def test_validacao_de_limites_e_header_injection(client):
    key = str(uuid.uuid4())
    assert (
        client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": "x" * 129},
            json=_post_body(),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json=_post_body("   "),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": "   chave com espaco   "},
            json=_post_body(),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/comunicacoes",
            headers={"Idempotency-Key": key},
            json={**_post_body(), "subject": "ok\r\nBcc: invasor@example.com"},
        ).status_code
        == 422
    )


async def test_commit_inicial_falha_sem_smtp_nem_enqueue():
    engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            real_commit = session.commit
            session.commit = AsyncMock(side_effect=RuntimeError("commit failure"))
            request = Request(
                {"type": "http", "client": ("203.0.113.10", 1234), "headers": []}
            )
            with (
                patch(
                    "app.modules.comunicacoes.infrastructure.email_sender.aiosmtplib.send",
                    new_callable=AsyncMock,
                ) as smtp,
                pytest.raises(RuntimeError, match="commit failure"),
            ):
                await enviar_comunicacao(
                    EnviarComunicacaoRequest(**_post_body("rollback atomico")),
                    request,
                    Response(),
                    _actor_override(),
                    session,
                    f"commit-fail-{uuid.uuid4()}",
                )
            smtp.assert_not_awaited()
            session.commit = real_commit
    finally:
        await engine.dispose()

    check_engine, factory = _isolated_session_factory()
    try:
        async with factory() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM comunicacoes WHERE content = :content"),
                {"content": "rollback atomico"},
            )
            assert count == 0
    finally:
        await check_engine.dispose()
