"""Contrato HTTP de GET /api/v1/alertas com avisos de integração (Task 4).

Helper de auth replicado localmente (~15 linhas, mesmo padrão de
`test_pedidos_routes.py:37-78`) porque o projeto não tem módulo de helpers
compartilhado e a convenção é não importar de arquivos `test_*`.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.shared.infrastructure.redis_client as redis_client_module
from app.modules.auth.domain.roles import automationRole
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import create_access_token
from app.modules.health.domain.avisos import TERMOS_TECNICOS_PROIBIDOS, FonteIntegracao
from app.modules.health.service import (
    registrar_falha_integracao,
    registrar_sucesso_integracao,
)
from app.shared.config.settings import get_settings
from app.shared.infrastructure.redis_client import get_redis


def _reset_redis_preso_a_outro_loop() -> None:
    """`client.get(...)` (TestClient) roda a ASGI app num portal com loop
    próprio, diferente do loop deste teste async. Chamar isso ENTRE tocar o
    Redis diretamente (`await registrar_...`) e chamar `client.get(...)`
    evita o singleton `get_redis()` ficar preso ao loop errado nos dois
    sentidos (mesmo pitfall de `test_avisos_integracao_instrumentacao.py`)."""

    redis_client_module._redis = None

# ---------------------------------------------------------------------------
# Helper de auth
# ---------------------------------------------------------------------------


async def _criar_usuario_async(*, email: str, roles: list) -> int:
    test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            user = AuthUser(
                email=email, roles=list(roles), confirmed_at=datetime.now(UTC)
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id
    finally:
        await test_engine.dispose()


async def _token_viewer() -> str:
    """Testes daqui já são `async def` (pytest-asyncio, `asyncio_mode=auto`) —
    diferente de `test_pedidos_routes.py` (testes síncronos), aqui o helper
    precisa ser `await`ado direto: `asyncio.run()` dentro de uma coroutine já
    rodando levanta `RuntimeError`."""

    email = f"alertas-integracao-{uuid.uuid4().hex[:8]}@teste.project.com"
    user_id = await _criar_usuario_async(email=email, roles=[automationRole.BASICO])
    return create_access_token(
        user_id=user_id, email=email, roles=[automationRole.BASICO]
    )[0]


async def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {await _token_viewer()}"}


@pytest.fixture(autouse=True)
async def _limpar_chaves_de_aviso():
    chaves = [f"automation:aviso-integracao:{fonte.value}" for fonte in FonteIntegracao]
    redis = get_redis()
    await redis.delete(*chaves)
    yield
    _reset_redis_preso_a_outro_loop()
    redis = get_redis()
    await redis.delete(*chaves)


async def test_alertas_traz_avisos_de_integracao_no_inicio_com_kind_e_categoria(
    client,
):
    await registrar_falha_integracao(FonteIntegracao.BANCO_DE_DADOS)
    await registrar_falha_integracao(FonteIntegracao.ESTOQUE)

    _reset_redis_preso_a_outro_loop()
    response = client.get("/api/v1/alertas", headers=await _auth_header())

    assert response.status_code == 200
    body = response.json()

    linhas_integracao = [row for row in body["rows"] if row["kind"] == "integracao"]
    assert len(linhas_integracao) == 2
    # No início de `rows` (D-03).
    assert body["rows"][0]["kind"] == "integracao"
    assert body["rows"][1]["kind"] == "integracao"
    categorias = {row["category"] for row in linhas_integracao}
    assert categorias == {"integracao_banco_de_dados", "integracao_estoque"}
    assert body["total"] >= 2


async def test_alertas_de_negocio_seguem_com_kind_negocio_por_default(client):
    _reset_redis_preso_a_outro_loop()
    response = client.get("/api/v1/alertas", headers=await _auth_header())

    assert response.status_code == 200
    body = response.json()
    for row in body["rows"]:
        assert row["kind"] in ("negocio", "integracao")
        if row["category"] not in (
            "integracao_banco_de_dados",
            "integracao_estoque",
        ):
            assert row["kind"] == "negocio"


async def test_alertas_nao_vaza_termo_tecnico_no_corpo_bruto(client):
    await registrar_falha_integracao(
        FonteIntegracao.BANCO_DE_DADOS, detalhe_tecnico="ConnectionError"
    )
    await registrar_falha_integracao(
        FonteIntegracao.ESTOQUE, detalhe_tecnico="transport"
    )

    _reset_redis_preso_a_outro_loop()
    response = client.get("/api/v1/alertas", headers=await _auth_header())

    for termo in TERMOS_TECNICOS_PROIBIDOS:
        assert termo not in response.text.lower()


async def test_alertas_sem_aviso_aberto_nao_tem_linha_de_integracao(client):
    _reset_redis_preso_a_outro_loop()
    response = client.get("/api/v1/alertas", headers=await _auth_header())

    assert response.status_code == 200
    body = response.json()
    assert not any(row["kind"] == "integracao" for row in body["rows"])


async def test_alertas_apos_resolver_aviso_linha_de_integracao_some(client):
    await registrar_falha_integracao(FonteIntegracao.ESTOQUE)
    await registrar_sucesso_integracao(FonteIntegracao.ESTOQUE)

    _reset_redis_preso_a_outro_loop()
    response = client.get("/api/v1/alertas", headers=await _auth_header())

    body = response.json()
    assert not any(row["kind"] == "integracao" for row in body["rows"])
