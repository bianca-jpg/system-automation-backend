"""Perfil do usuário: nome vindo do SSO, rótulo do papel e GET /api/auth/users/me.

O menu do painel mostrava o e-mail no lugar do nome porque `sign_in_microsoft`
lia só o claim de e-mail do ID token e descartava o `name`, e porque
`AuthUserResponse` não tinha campo de nome para serializar (quick 260908-prf).
Estes testes fixam as três pontas: captura do claim, rótulo do papel e o
endpoint de perfil próprio.
"""

import pytest

from app.modules.auth.domain.roles import ROLE_TITLE, automationRole, role_title
from app.shared.config.settings import get_settings


def _configurar_sso(monkeypatch):
    monkeypatch.setattr(get_settings(), "microsoft_tenant_id", "tenant-id-teste")
    monkeypatch.setattr(get_settings(), "microsoft_client_id", "client-id-teste")


def _mockar_id_token(monkeypatch, payload: dict):
    async def _validar_mock(*args, **kwargs):
        return payload

    monkeypatch.setattr(
        "app.modules.auth.application.casos_uso.validar_id_token_microsoft",
        _validar_mock,
    )


# --------------------------------------------------------------------------
# Domínio: role_title
# --------------------------------------------------------------------------


def test_role_title_cobre_todos_os_papeis_do_enum():
    """Papel novo no enum sem rótulo é erro de implementação, não da UI."""
    for papel in automationRole:
        assert papel in ROLE_TITLE, f"{papel} sem rótulo em ROLE_TITLE"


@pytest.mark.parametrize(
    ("papeis", "esperado"),
    [
        (["basico"], "Básico"),
        (["operacional"], "Operacional"),
        (["gestor"], "Gestor"),
        (["administrador"], "Administrador"),
        (["admin_tecnico"], "Admin Técnico"),
    ],
)
def test_role_title_traduz_cada_papel(papeis, esperado):
    assert role_title(papeis) == esperado


def test_role_title_usa_o_papel_de_maior_nivel():
    # Espelha max_role_level: quem acumula papéis é descrito pelo mais alto,
    # independente da ordem em que eles vêm na lista.
    assert role_title(["basico", "admin_tecnico", "gestor"]) == "Admin Técnico"
    assert role_title(["administrador", "basico"]) == "Administrador"


def test_role_title_sem_papel_cai_no_minimo():
    # normalize_roles devolve [BASICO] para lista vazia/None.
    assert role_title([]) == "Básico"
    assert role_title(None) == "Básico"


def test_role_title_de_papel_desconhecido_nao_desaparece_da_tela():
    # normalize_roles descarta o desconhecido e sobra o mínimo — o importante
    # é não devolver string vazia, que deixaria o campo do perfil em branco.
    assert role_title(["papel_que_nao_existe"]) == "Básico"


def test_role_title_traduz_papel_legado():
    # LEGACY_ROLE_MAP: "admin" antigo é administrador.
    assert role_title(["admin"]) == "Administrador"


# --------------------------------------------------------------------------
# SSO: captura do claim `name`
# --------------------------------------------------------------------------


def test_sso_grava_display_name_do_claim_name(client, monkeypatch):
    _configurar_sso(monkeypatch)
    _mockar_id_token(
        monkeypatch,
        {
            "email": "perfil_nome_novo@project.com.br",
            "name": "Maria Aparecida de Souza",
            "sub": "ms-sub-nome",
        },
    )

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-valido"})

    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["display_name"] == "Maria Aparecida de Souza"
    # O rótulo acompanha o papel mínimo do auto-provisionamento.
    assert user["role_title"] == "Básico"


def test_sso_sem_claim_name_deixa_display_name_nulo(client, monkeypatch):
    """Claim `name` é opcional no OIDC — ausência não pode virar "None" em texto."""
    _configurar_sso(monkeypatch)
    _mockar_id_token(
        monkeypatch,
        {"email": "perfil_sem_nome@project.com.br", "sub": "ms-sub-sem-nome"},
    )

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-valido"})

    assert resp.status_code == 200
    assert resp.json()["user"]["display_name"] is None


def test_sso_claim_name_em_branco_nao_grava_string_vazia(client, monkeypatch):
    _configurar_sso(monkeypatch)
    _mockar_id_token(
        monkeypatch,
        {
            "email": "perfil_nome_branco@project.com.br",
            "name": "   ",
            "sub": "ms-sub-branco",
        },
    )

    resp = client.post("/api/auth/sso/microsoft", json={"id_token": "token-valido"})

    assert resp.status_code == 200
    assert resp.json()["user"]["display_name"] is None


def test_sso_atualiza_display_name_quando_o_nome_muda_no_diretorio(
    client, monkeypatch
):
    """O diretório é a fonte de verdade: troca de nome se propaga no login seguinte."""
    _configurar_sso(monkeypatch)
    email = "perfil_nome_mudou@project.com.br"

    _mockar_id_token(monkeypatch, {"email": email, "name": "Ana Antiga"})
    primeiro = client.post("/api/auth/sso/microsoft", json={"id_token": "t1"})
    assert primeiro.json()["user"]["display_name"] == "Ana Antiga"

    _mockar_id_token(monkeypatch, {"email": email, "name": "Ana Nova"})
    segundo = client.post("/api/auth/sso/microsoft", json={"id_token": "t2"})

    assert segundo.status_code == 200
    assert segundo.json()["user"]["display_name"] == "Ana Nova"


def test_sso_sem_claim_name_nao_apaga_nome_ja_guardado(client, monkeypatch):
    """Login sem o claim não pode zerar o nome que um login anterior gravou."""
    _configurar_sso(monkeypatch)
    email = "perfil_preserva_nome@project.com.br"

    _mockar_id_token(monkeypatch, {"email": email, "name": "Carla Mantida"})
    client.post("/api/auth/sso/microsoft", json={"id_token": "t1"})

    _mockar_id_token(monkeypatch, {"email": email})
    segundo = client.post("/api/auth/sso/microsoft", json={"id_token": "t2"})

    assert segundo.status_code == 200
    assert segundo.json()["user"]["display_name"] == "Carla Mantida"


# --------------------------------------------------------------------------
# GET /api/auth/users/me
# --------------------------------------------------------------------------


def test_users_me_devolve_o_perfil_da_conta_autenticada(client, monkeypatch):
    _configurar_sso(monkeypatch)
    _mockar_id_token(
        monkeypatch,
        {"email": "perfil_me@project.com.br", "name": "Joana Ribeiro"},
    )
    login = client.post("/api/auth/sso/microsoft", json={"id_token": "token-valido"})
    token = login.json()["token"]["access_token"]

    resp = client.get(
        "/api/auth/users/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "perfil_me@project.com.br"
    assert body["display_name"] == "Joana Ribeiro"
    assert body["role_title"] == "Básico"
    assert body["roles"] == ["basico"]


def test_users_me_exige_autenticacao(client):
    """Sem token não há "próprio perfil" para devolver."""
    resp = client.get("/api/auth/users/me")

    assert resp.status_code in (401, 403)


def test_users_me_e_acessivel_ao_papel_minimo(client, monkeypatch):
    """Ver o próprio nome não é privilégio: `basico` também alcança o endpoint.

    A listagem vizinha (`GET /users`) exige administrador; este endpoint não
    pode herdar esse guard, ou o perfil ficaria invisível para quem tem menos
    acesso — justamente a maioria das contas.
    """
    _configurar_sso(monkeypatch)
    _mockar_id_token(
        monkeypatch,
        {"email": "perfil_basico@project.com.br", "name": "Pedro Básico"},
    )
    login = client.post("/api/auth/sso/microsoft", json={"id_token": "token-valido"})
    assert login.json()["user"]["roles"] == ["basico"]
    token = login.json()["token"]["access_token"]

    perfil = client.get(
        "/api/auth/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    listagem = client.get(
        "/api/auth/users", headers={"Authorization": f"Bearer {token}"}
    )

    assert perfil.status_code == 200
    assert listagem.status_code == 403
