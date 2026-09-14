import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.auth.domain.roles import (
    ROLE_LEVEL,
    automationRole,
    has_min_level,
    normalize_roles,
)
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.modules.parametros.infrastructure.models import ParametroChangeRequest
from app.shared.config.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers de setup (acesso direto ao banco, fora do event loop do TestClient).
# ---------------------------------------------------------------------------


def _email_unico(prefixo: str) -> str:
    return f"{prefixo}-{uuid.uuid4().hex[:8]}@teste.project.com"


def _nova_sessao_isolada():
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return test_engine, async_sessionmaker(test_engine, expire_on_commit=False)


async def _criar_usuario_async(*, email, roles, confirmado):
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            user = AuthUser(
                email=email,
                roles=list(roles),
                confirmed_at=datetime.now(UTC) if confirmado else None,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id
    finally:
        await test_engine.dispose()


def criar_usuario(email, roles=None, confirmado=True):
    return asyncio.run(
        _criar_usuario_async(
            email=email,
            roles=roles or [automationRole.BASICO],
            confirmado=confirmado,
        )
    )


def token_de(user_id: int, email: str, roles: list[str]) -> str:
    token, _ = create_access_token(user_id=user_id, email=email, roles=roles)
    return token


async def _buscar_usuario_async(email):
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            return await session.scalar(select(AuthUser).where(AuthUser.email == email))
    finally:
        await test_engine.dispose()


def buscar_usuario(email):
    return asyncio.run(_buscar_usuario_async(email))


async def _criar_change_request_async(*, requested_by: int) -> int:
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            change_request = ParametroChangeRequest(
                requested_by=requested_by,
                target_chave="tolerancia_adequacao",
                change_type="update",
                proposed_payload={"valor": "0.08"},
                justification="teste de exclusão de usuário",
            )
            session.add(change_request)
            await session.commit()
            await session.refresh(change_request)
            return change_request.id
    finally:
        await test_engine.dispose()


def criar_change_request(*, requested_by: int) -> int:
    return asyncio.run(_criar_change_request_async(requested_by=requested_by))


async def _buscar_change_request_async(change_request_id: int):
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            return await session.get(ParametroChangeRequest, change_request_id)
    finally:
        await test_engine.dispose()


def buscar_change_request(change_request_id: int):
    return asyncio.run(_buscar_change_request_async(change_request_id))


# ---------------------------------------------------------------------------
# Teste de regressão: rotas removidas retornam 404
# ---------------------------------------------------------------------------


def test_endpoints_de_login_local_nao_existem_mais(client):
    rotas_removidas = [
        ("/api/auth/sign-in", {"identifier": "a@b.com", "password": "123"}),
        (
            "/api/auth/sign-in/confirm-new-password",
            {"email": "a@b.com", "new_password": "123", "session": "s"},
        ),
        (
            "/api/auth/register",
            {"user_name": "a", "email": "a@b.com", "password": "123", "cpf": "123"},
        ),
        (
            "/api/auth/register/confirm",
            {"email": "a@b.com", "confirmation_code": "000000"},
        ),
        ("/api/auth/password/recovery", {"email": "a@b.com"}),
        (
            "/api/auth/password/recovery/confirm",
            {"email": "a@b.com", "confirmation_code": "000000", "new_password": "123"},
        ),
    ]
    for url, body in rotas_removidas:
        resp = client.post(url, json=body)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# refresh-token
# ---------------------------------------------------------------------------


def test_refresh_token_invalido_retorna_401(client):
    resp = client.post(
        "/api/auth/token/refresh", json={"refresh_token": "token-invalido"}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid refresh token"


def test_refresh_token_valido_retorna_novo_access_token(client):
    email = _email_unico("refresh-valido")
    user_id = criar_usuario(email, confirmado=True)
    refresh_token = create_refresh_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )

    resp = client.post("/api/auth/token/refresh", json={"refresh_token": refresh_token})

    assert resp.status_code == 200
    assert resp.json()["token"]["access_token"]


# ---------------------------------------------------------------------------
# sign-out (SEC-06: exige o access token e grava o corte de revogação)
# ---------------------------------------------------------------------------


def test_sign_out_retorna_status_signed_out(client):
    email = _email_unico("sign-out-sucesso")
    user_id = criar_usuario(email, confirmado=True)
    access_token = token_de(user_id, email, [automationRole.BASICO])

    resp = client.post(
        "/api/auth/sign-out",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "signed_out"}


def test_sign_out_sem_token_retorna_401(client):
    resp = client.post("/api/auth/sign-out")

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /users e /users/{id}/roles — RBAC (require_admin) + regras de validação
# ---------------------------------------------------------------------------


def test_list_users_exige_admin(client):
    email_viewer = _email_unico("list-users-viewer")
    viewer_id = criar_usuario(email_viewer, confirmado=True, roles=[automationRole.BASICO])
    token_viewer = token_de(viewer_id, email_viewer, [automationRole.BASICO])

    resp = client.get(
        "/api/auth/users", headers={"Authorization": f"Bearer {token_viewer}"}
    )
    assert resp.status_code == 403

    email_admin = _email_unico("list-users-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    resp = client.get(
        "/api/auth/users",
        params={"search": email_admin},
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["pageSize"] == 25
    assert body["total"] >= len(body["rows"])
    assert any(u["email"] == email_admin for u in body["rows"])


def test_list_users_paginas_numeradas_busca_e_ordena_no_servidor(client):
    marker = f"page-users-{uuid.uuid4().hex[:8]}"
    emails = [f"{marker}-{suffix}@teste.project.com" for suffix in ("c", "a", "b")]
    admin_email = _email_unico("page-users-admin")
    admin_id = criar_usuario(
        admin_email,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    for email in emails:
        criar_usuario(email, confirmado=True)
    token_admin = token_de(admin_id, admin_email, [automationRole.ADMINISTRADOR])
    headers = {"Authorization": f"Bearer {token_admin}"}

    pages = [
        client.get(
            "/api/auth/users",
            params={
                "page": page,
                "pageSize": 1,
                "search": marker,
                "sort": "email",
                "order": "asc",
            },
            headers=headers,
        ).json()
        for page in (1, 2, 3)
    ]

    assert all(page["total"] == 3 for page in pages)
    assert all(page["totalPages"] == 3 for page in pages)
    assert [page["rows"][0]["email"] for page in pages] == sorted(emails)


def test_set_user_roles_papeis_invalidos_retorna_422(client):
    email_admin = _email_unico("set-roles-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    email_alvo = _email_unico("set-roles-alvo")
    alvo_id = criar_usuario(email_alvo, confirmado=True)

    resp = client.put(
        f"/api/auth/users/{alvo_id}/roles",
        json={"roles": ["papel_que_nao_existe"]},
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    assert resp.status_code == 422


def test_set_user_roles_usuario_inexistente_retorna_404(client):
    email_admin = _email_unico("set-roles-404-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    resp = client.put(
        "/api/auth/users/999999999/roles",
        json={"roles": [automationRole.GESTOR.value]},
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    assert resp.status_code == 404


def test_set_user_roles_sucesso_atualiza_papeis(client):
    email_admin = _email_unico("set-roles-ok-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    email_alvo = _email_unico("set-roles-ok-alvo")
    alvo_id = criar_usuario(email_alvo, confirmado=True, roles=[automationRole.BASICO])

    resp = client.put(
        f"/api/auth/users/{alvo_id}/roles",
        json={"roles": [automationRole.GESTOR.value]},
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    assert resp.status_code == 200
    assert resp.json()["roles"] == [automationRole.GESTOR.value]


def test_delete_user_auto_exclusao_retorna_400_e_nao_apaga(client):
    email_admin = _email_unico("delete-user-self-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    resp = client.delete(
        f"/api/auth/users/{admin_id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    assert resp.status_code == 400


def test_delete_user_id_inexistente_retorna_404(client):
    email_admin = _email_unico("delete-user-404-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    resp = client.delete(
        "/api/auth/users/999999999",
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    assert resp.status_code == 404


def test_delete_user_sucesso_revoga_acesso_do_alvo(client):
    email_admin = _email_unico("delete-user-ok-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    email_alvo = _email_unico("delete-user-ok-alvo")
    alvo_id = criar_usuario(email_alvo, confirmado=True)

    resp = client.delete(
        f"/api/auth/users/{alvo_id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )

    assert resp.status_code == 204


def test_delete_user_preserva_change_request_zerando_requested_by(client):
    email_admin = _email_unico("delete-user-cr-admin")
    admin_id = criar_usuario(
        email_admin,
        confirmado=True,
        roles=[automationRole.ADMINISTRADOR],
    )
    token_admin = token_de(admin_id, email_admin, [automationRole.ADMINISTRADOR])

    email_alvo = _email_unico("delete-user-cr-alvo")
    alvo_id = criar_usuario(email_alvo, confirmado=True)
    change_request_id = criar_change_request(requested_by=alvo_id)

    resp = client.delete(
        f"/api/auth/users/{alvo_id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp.status_code == 204

    change_request = buscar_change_request(change_request_id)
    assert change_request is not None
    assert change_request.id == change_request_id
    assert change_request.status == "pending"
    assert change_request.proposed_payload == {"valor": "0.08"}
    assert change_request.requested_by is None


# ---------------------------------------------------------------------------
# Unit tests puros: hierarquia de papéis, tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles,esperado",
    [
        (["operador"], [automationRole.OPERACIONAL.value]),
        (["hype_user"], [automationRole.OPERACIONAL.value]),
        (["hype_manager"], [automationRole.GESTOR.value]),
        (["hype_admin"], [automationRole.ADMINISTRADOR.value]),
        (["admin"], [automationRole.ADMINISTRADOR.value]),
        (["papel_desconhecido"], [automationRole.BASICO.value]),
        ([], [automationRole.BASICO.value]),
        (None, [automationRole.BASICO.value]),
        (
            [automationRole.GESTOR.value, "operador"],
            [automationRole.GESTOR.value, automationRole.OPERACIONAL.value],
        ),
    ],
)
def test_normalize_roles_mapeia_papeis_legados(roles, esperado):
    assert normalize_roles(roles) == esperado


@pytest.mark.parametrize("papel_usuario", list(automationRole))
@pytest.mark.parametrize("minimo", list(automationRole))
def test_has_min_level_hierarquia_completa(papel_usuario, minimo):
    esperado = ROLE_LEVEL[papel_usuario] >= ROLE_LEVEL[minimo]
    assert has_min_level([papel_usuario], minimo) == esperado


def test_has_min_level_casos_ilustrativos():
    assert has_min_level([automationRole.BASICO], automationRole.OPERACIONAL) is False
    assert has_min_level([automationRole.GESTOR], automationRole.BASICO) is True
    assert has_min_level([automationRole.GESTOR], automationRole.ADMINISTRADOR) is False
    assert has_min_level([automationRole.ADMIN_TECNICO], automationRole.ADMIN_TECNICO) is True
    assert has_min_level([], automationRole.BASICO) is True


def test_tokens_roundtrip_e_rejeicao_por_tipo():
    access, expires_in = create_access_token(
        user_id=1, email="a@b.com", roles=["basico"]
    )
    assert expires_in > 0
    payload_access = decode_access_token(access)
    assert payload_access["typ"] == "access"
    assert payload_access["sub"] == "1"

    refresh = create_refresh_token(user_id=1, email="a@b.com", roles=["basico"])
    payload_refresh = decode_refresh_token(refresh)
    assert payload_refresh["typ"] == "refresh"
    assert "jti" in payload_refresh

    # rejeição cruzada por tipo
    with pytest.raises(JWTError):
        decode_refresh_token(access)
    with pytest.raises(JWTError):
        decode_access_token(refresh)
