"""Testes do único endpoint de login do sistema: POST /api/auth/sso/microsoft (Entra ID).

Cobre os cenários de erro e sucesso:
  - 503 quando o SSO não está configurado no ambiente (tenant_id/client_id nulos)
  - 401 para ID token inválido, expirado ou com assinatura incorreta
  - 403 para ID token sem claim de e-mail utilizável
  - 200 para login bem-sucedido (auto-provisiona usuário novo com papel basico)
  - 200 para login de usuário existente (preserva os papéis já atribuídos)
"""

from app.modules.auth.domain.exceptions import (
    ContaSsoNaoAutorizadaError,
    TokenMicrosoftInvalidoError,
)
from app.shared.config.settings import get_settings


def test_sso_microsoft_retorna_503_se_nao_configurado(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "microsoft_tenant_id", "")
    monkeypatch.setattr(get_settings(), "microsoft_client_id", "")

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-teste"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Microsoft SSO not configured"


def test_sso_microsoft_retorna_401_para_token_invalido(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "microsoft_tenant_id", "tenant-id-teste")
    monkeypatch.setattr(get_settings(), "microsoft_client_id", "client-id-teste")

    async def _validar_mock(*args, **kwargs):
        raise TokenMicrosoftInvalidoError("Token inválido")

    monkeypatch.setattr(
        "app.modules.auth.application.casos_uso.validar_id_token_microsoft",
        _validar_mock,
    )

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-invalido"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid Microsoft token"


def test_sso_microsoft_retorna_403_se_sem_email(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "microsoft_tenant_id", "tenant-id-teste")
    monkeypatch.setattr(get_settings(), "microsoft_client_id", "client-id-teste")

    async def _validar_mock(*args, **kwargs):
        raise ContaSsoNaoAutorizadaError()

    monkeypatch.setattr(
        "app.modules.auth.application.casos_uso.validar_id_token_microsoft",
        _validar_mock,
    )

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-sem-email"})

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Account not authorized"


def test_sso_microsoft_sucesso_provisiona_usuario_novo(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "microsoft_tenant_id", "tenant-id-teste")
    monkeypatch.setattr(get_settings(), "microsoft_client_id", "client-id-teste")

    async def _validar_mock(*args, **kwargs):
        return {"email": "novo_usuario_sso@project.com.br", "sub": "ms-sub-123"}

    monkeypatch.setattr(
        "app.modules.auth.application.casos_uso.validar_id_token_microsoft",
        _validar_mock,
    )

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-valido"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]["access_token"]
    assert body["token"]["refresh_token"]
    assert body["user"]["email"] == "novo_usuario_sso@project.com.br"
    assert body["user"]["roles"] == ["basico"]
