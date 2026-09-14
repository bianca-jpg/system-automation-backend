"""Revogação de sessão (SEC-06): corte por usuário no Redis pós sign-out.

Este arquivo cresce em duas camadas:
  - testes puros de token (`iat` no refresh, sem HTTP nem Redis);
  - testes de ponta a ponta via `client`, que provam que um refresh token
    emitido antes do sign-out é rejeitado depois dele, e que um sign-in novo
    continua funcionando (o corte não bloqueia a conta).
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.domain.roles import automationRole
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.shared.config.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers de setup — mesmo padrão de test_auth_flows.py/test_parametros.py:
# engine descartável (NullPool) para não prender conexão a um event loop
# diferente do TestClient.
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
# tokens puros — iat no refresh token
# ---------------------------------------------------------------------------


def test_refresh_token_contem_iat_em_segundos():
    token = create_refresh_token(user_id=1, email="a@b.c", roles=[])
    payload = decode_refresh_token(token)

    assert isinstance(payload["iat"], int)
    assert abs(int(datetime.now(UTC).timestamp()) - payload["iat"]) < 5


def test_refresh_token_iat_nao_excede_exp():
    token = create_refresh_token(user_id=1, email="a@b.c", roles=[])
    payload = decode_refresh_token(token)

    assert payload["iat"] < payload["exp"]


def test_access_token_permanece_sem_iat():
    token, _ = create_access_token(user_id=1, email="a@b.c", roles=[])
    payload = decode_access_token(token)

    assert "iat" not in payload


# ---------------------------------------------------------------------------
# ponta a ponta — corte de sign-out via Redis real (banco 15)
# ---------------------------------------------------------------------------


def test_refresh_funciona_antes_do_sign_out(client):
    """Controle: sem sign-out, o refresh emitido na sessão continua válido."""
    email = _email_unico("revogacao-controle")
    user_id = criar_usuario(email)
    refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": refresh_token})

    assert resp.status_code == 200
    assert resp.json()["token"]["access_token"]


def test_refresh_apos_sign_out_e_rejeitado(client):
    email = _email_unico("revogacao-rejeitado")
    user_id = criar_usuario(email)
    access_token, _ = create_access_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )
    refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    # iat tem resolução de 1 segundo e a comparação em refresh_token é "<":
    # sem este intervalo, um refresh emitido no MESMO segundo do corte
    # sobreviveria por coincidência de relógio — o sleep torna o teste
    # determinístico em vez de disfarçar o comportamento real.
    time.sleep(1.1)

    sign_out = client.post(
        "/api/auth/sign-out", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert sign_out.status_code == 200

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": refresh_token})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid refresh token"


def test_novo_token_emitido_apos_sign_out_gera_refresh_valido(client):
    """Prova que o corte não bloqueia a conta: um token emitido após sign-out é válido."""
    email = _email_unico("revogacao-novo-signin")
    user_id = criar_usuario(email)
    access_token, _ = create_access_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    time.sleep(1.1)

    sign_out = client.post(
        "/api/auth/sign-out", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert sign_out.status_code == 200

    time.sleep(1.1)

    novo_refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    resp = client.post(
        "/api/auth/token/refresh", json={"refresh_token": novo_refresh_token}
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# teto absoluto de sessão — auth_time não se move a cada renovação
# ---------------------------------------------------------------------------


def test_refresh_token_contem_auth_time_igual_ao_iat_por_padrao():
    token = create_refresh_token(user_id=1, email="a@b.c", roles=[])
    payload = decode_refresh_token(token)

    assert payload["auth_time"] == payload["iat"]


def test_refresh_preserva_auth_time_original_apos_renovacao(client):
    """auth_time não pode "andar" a cada refresh — senão o teto absoluto vira
    janela deslizante de novo, o mesmo bug que este teto corrige."""
    email = _email_unico("teto-preserva-auth-time")
    user_id = criar_usuario(email)
    primeiro_refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )
    auth_time_original = decode_refresh_token(primeiro_refresh_token)["auth_time"]

    time.sleep(1.1)

    resp = client.post(
        "/api/auth/token/refresh", json={"refresh_token": primeiro_refresh_token}
    )
    assert resp.status_code == 200
    segundo_refresh_token = resp.json()["token"]["refresh_token"]
    payload_segundo = decode_refresh_token(segundo_refresh_token)

    assert payload_segundo["auth_time"] == auth_time_original
    assert payload_segundo["iat"] > auth_time_original


def test_refresh_rejeitado_apos_teto_absoluto_de_sessao(client):
    email = _email_unico("teto-absoluto-rejeitado")
    user_id = criar_usuario(email)

    teto = get_settings().auth_absolute_session_seconds
    auth_time_expirado = int(datetime.now(UTC).timestamp()) - teto - 60
    refresh_token_velho = create_refresh_token(
        user_id=user_id,
        email=email,
        roles=[automationRole.BASICO],
        auth_time=auth_time_expirado,
    )

    resp = client.post(
        "/api/auth/token/refresh", json={"refresh_token": refresh_token_velho}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid refresh token"


def test_refresh_aceito_dentro_do_teto_absoluto(client):
    email = _email_unico("teto-absoluto-aceito")
    user_id = criar_usuario(email)

    teto = get_settings().auth_absolute_session_seconds
    auth_time_valido = int(datetime.now(UTC).timestamp()) - teto + 60
    refresh_token_no_limite = create_refresh_token(
        user_id=user_id,
        email=email,
        roles=[automationRole.BASICO],
        auth_time=auth_time_valido,
    )

    resp = client.post(
        "/api/auth/token/refresh", json={"refresh_token": refresh_token_no_limite}
    )

    assert resp.status_code == 200


def test_sign_out_de_um_usuario_nao_afeta_outro(client):
    email_a = _email_unico("revogacao-usuario-a")
    email_b = _email_unico("revogacao-usuario-b")
    user_id_a = criar_usuario(email_a)
    user_id_b = criar_usuario(email_b)

    access_token_a, _ = create_access_token(
        user_id=user_id_a, email=email_a, roles=[automationRole.BASICO]
    )
    refresh_token_b = create_refresh_token(
        user_id=user_id_b, email=email_b, roles=[automationRole.BASICO]
    )

    time.sleep(1.1)

    sign_out_a = client.post(
        "/api/auth/sign-out", headers={"Authorization": f"Bearer {access_token_a}"}
    )
    assert sign_out_a.status_code == 200

    resp_b = client.post(
        "/api/auth/token/refresh", json={"refresh_token": refresh_token_b}
    )

    assert resp_b.status_code == 200
