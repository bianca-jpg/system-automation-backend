"""Rotação de refresh token com detecção de reuso por `jti`.

Este arquivo cobre a rotação de refresh token com detecção de reuso por
`jti`, complementando `test_auth_sessao_revogacao.py` (que cobre o corte de
sign-out por usuário, SEC-06). Quando um refresh token já rotacionado é
reapresentado, isso é lido como sinal de roubo/replay: o refresh é rejeitado
com 401 e a sessão inteira do usuário é revogada, reaproveitando o mesmo
corte `auth:signed_out_since:{user_id}` que o `sign_out` já grava.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from jose import jwt
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.domain.roles import automationRole
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import (
    create_refresh_token,
    decode_refresh_token,
)
from app.shared.config.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers de setup — mesmo padrão de test_auth_sessao_revogacao.py: engine
# descartável (NullPool) para não prender conexão a um event loop diferente
# do TestClient.
# ---------------------------------------------------------------------------


def _email_unico(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex[:8]}@teste.project.com"


def _nova_sessao_isolada():
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return test_engine, async_sessionmaker(test_engine, expire_on_commit=False)


async def _criar_usuario_async(*, email, confirmado):
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            user = AuthUser(
                email=email,
                roles=[automationRole.BASICO],
                confirmed_at=datetime.now(UTC) if confirmado else None,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id
    finally:
        await test_engine.dispose()


def criar_usuario(email, confirmado=True):
    return asyncio.run(_criar_usuario_async(email=email, confirmado=confirmado))


# ---------------------------------------------------------------------------
# Helpers de Redis — cliente novo por chamada, sem usar o get_redis()
# singleton da aplicação, pelo mesmo raciocínio do engine descartável acima.
# ---------------------------------------------------------------------------


async def _ler_redis_async(chave: str) -> str | None:
    cliente = aioredis.from_url(
        get_settings().effective_redis_url, decode_responses=True
    )
    try:
        return await cliente.get(chave)
    finally:
        await cliente.aclose()


def _ler_redis(chave: str) -> str | None:
    return asyncio.run(_ler_redis_async(chave))


# ---------------------------------------------------------------------------
# 1. refresh legítimo grava o jti do token novo
# ---------------------------------------------------------------------------


def test_refresh_legitimo_grava_o_jti_do_token_novo(client):
    """Falha hoje: a chave auth:refresh_jti:{user_id} não existe ainda."""
    email = _email_unico("reuso-grava-jti")
    user_id = criar_usuario(email)
    refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": refresh_token})

    assert resp.status_code == 200
    token_novo = resp.json()["token"]["refresh_token"]
    jti_novo = decode_refresh_token(token_novo)["jti"]
    assert _ler_redis(f"auth:refresh_jti:{user_id}") == jti_novo


# ---------------------------------------------------------------------------
# 2. reapresentar refresh token já rotacionado retorna 401
# ---------------------------------------------------------------------------


def test_reapresentar_refresh_token_ja_rotacionado_retorna_401(client):
    """Falha hoje: o segundo refresh(A) devolve 200 em vez de 401."""
    email = _email_unico("reuso-401")
    user_id = criar_usuario(email)
    token_a = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    primeiro = client.post("/api/auth/token/refresh", json={"refresh_token": token_a})
    assert primeiro.status_code == 200

    segundo = client.post("/api/auth/token/refresh", json={"refresh_token": token_a})
    assert segundo.status_code == 401
    assert segundo.json()["detail"] == "Invalid refresh token"


# ---------------------------------------------------------------------------
# 3. reuso revoga a sessão inteira (mesmo mecanismo do sign_out)
# ---------------------------------------------------------------------------


def test_reuso_revoga_a_sessao_inteira(client):
    """Falha hoje: o segundo refresh(A) devolve 200 em vez de 401."""
    email = _email_unico("reuso-revoga-sessao")
    user_id = criar_usuario(email)
    token_a = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    primeiro = client.post("/api/auth/token/refresh", json={"refresh_token": token_a})
    assert primeiro.status_code == 200
    token_b = primeiro.json()["token"]["refresh_token"]

    # iat tem resolução de 1 segundo e a comparação em refresh_token é "<":
    # sem este intervalo, o token B emitido no MESMO segundo do corte
    # sobreviveria por coincidência de relógio, e o teste passaria a medir
    # sorte em vez de comportamento.
    time.sleep(1.1)

    reuso = client.post("/api/auth/token/refresh", json={"refresh_token": token_a})
    assert reuso.status_code == 401

    assert _ler_redis(f"auth:signed_out_since:{user_id}") is not None

    tambem_revogado = client.post(
        "/api/auth/token/refresh", json={"refresh_token": token_b}
    )
    assert tambem_revogado.status_code == 401


# ---------------------------------------------------------------------------
# 4. primeiro refresh sem jti guardado é aceito e passa a guardar
# ---------------------------------------------------------------------------


def test_primeiro_refresh_sem_jti_guardado_e_aceito_e_passa_a_guardar(client):
    """Falha hoje só na parte da chave: o refresh já é aceito (200), mas
    auth:refresh_jti:{user_id} nunca é gravada."""
    email = _email_unico("reuso-primeiro-refresh")
    user_id = criar_usuario(email)
    # Simula um token emitido antes deste deploy: nada no Redis ainda.
    token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": token})

    assert resp.status_code == 200
    assert _ler_redis(f"auth:refresh_jti:{user_id}") is not None


# ---------------------------------------------------------------------------
# 5. refresh de token sem claim jti não é tratado como reuso
# ---------------------------------------------------------------------------


def test_refresh_de_token_sem_claim_jti_nao_e_tratado_como_reuso(client):
    """Teste de guarda: passa antes e depois da implementação — a ausência
    de jti nunca é reuso, mesmo havendo jti guardado no Redis."""
    email = _email_unico("reuso-sem-claim-jti")
    user_id = criar_usuario(email)
    token_inicial = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    # Popula a chave de jti com um refresh legítimo primeiro.
    primeiro = client.post(
        "/api/auth/token/refresh", json={"refresh_token": token_inicial}
    )
    assert primeiro.status_code == 200

    settings = get_settings()
    now = datetime.now(UTC)
    payload_sem_jti = {
        "typ": "refresh",
        "sub": str(user_id),
        "email": email,
        "roles": [automationRole.BASICO],
        "iat": int(now.timestamp()),
        "auth_time": int(now.timestamp()),
        "exp": int(now.timestamp()) + 7 * 86_400,
    }
    token_sem_jti = jwt.encode(
        payload_sem_jti, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": token_sem_jti})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. fail-open quando o Redis está indisponível
# ---------------------------------------------------------------------------


def test_fail_open_quando_redis_indisponivel(client, monkeypatch):
    """Teste de guarda: passa antes e depois — Redis fora do ar não pode
    virar 500 nem apagão de refresh."""
    email = _email_unico("reuso-fail-open")
    user_id = criar_usuario(email)
    refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    class _RedisIndisponivel:
        async def get(self, *args, **kwargs):
            raise RedisError("redis indisponível")

        async def set(self, *args, **kwargs):
            raise RedisError("redis indisponível")

    fake_redis = _RedisIndisponivel()
    monkeypatch.setattr(
        "app.modules.auth.application.casos_uso.get_redis", lambda: fake_redis
    )

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": refresh_token})

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. reuso de um usuário não afeta outro
# ---------------------------------------------------------------------------


def test_reuso_de_um_usuario_nao_afeta_outro(client):
    """Teste de guarda: passa antes e depois — a chave é por usuário e o
    corte não vaza entre contas."""
    email_a = _email_unico("reuso-usuario-a")
    email_b = _email_unico("reuso-usuario-b")
    user_id_a = criar_usuario(email_a)
    user_id_b = criar_usuario(email_b)

    token_a = create_refresh_token(
        user_id=user_id_a, email=email_a, roles=[automationRole.BASICO]
    )
    token_b = create_refresh_token(
        user_id=user_id_b, email=email_b, roles=[automationRole.BASICO]
    )

    primeiro_a = client.post("/api/auth/token/refresh", json={"refresh_token": token_a})
    assert primeiro_a.status_code == 200

    # Ação que provoca o reuso em A (pós-implementação vira 401; pré-
    # implementação seguiria 200). O status desta chamada não é o que este
    # teste prova — é coberto por test_reapresentar_refresh_token_ja_
    # rotacionado_retorna_401. Aqui o que importa é que B, adiante, nunca é
    # afetado pelo que acontece com A.
    client.post("/api/auth/token/refresh", json={"refresh_token": token_a})

    resp_b = client.post("/api/auth/token/refresh", json={"refresh_token": token_b})
    assert resp_b.status_code == 200
