"""Instrumentação dos três pontos de falha reais (Task 3): `/health/ready`
(banco_de_dados), `DatabricksIngestionSource.estoque()` (estoque, com
live-update D-07) e o handler global de exceção (banco_de_dados via erro de
conexão do SQLAlchemy em request real)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

import app.shared.infrastructure.redis_client as redis_client_module
from app.modules.health.domain.avisos import TERMOS_TECNICOS_PROIBIDOS
from app.modules.health.service import FonteIntegracao, listar_linhas_de_alerta
from app.modules.ingestao.infrastructure.adapters import DatabricksIngestionSource
from app.shared.infrastructure.databricks_client import DatabricksError
from app.shared.infrastructure.redis_client import close_redis, get_redis


def _descartar_redis_preso_a_outro_loop() -> None:
    """`TestClient.get(...)` roda a ASGI app num loop próprio (portal interno
    do Starlette), diferente do loop deste teste async. O singleton global
    `_redis` fica preso ao loop em que foi criado — `close_redis()` faz um
    `aclose()` gracioso que tenta ler/escrever no socket e por isso também
    falha ("attached to a different loop"). Descartar a referência direto
    (sem fechar) é seguro aqui: é conexão de teste, coletada pelo GC, e o
    próximo `get_redis()` cria um cliente novo já preso ao loop correto."""

    redis_client_module._redis = None


@pytest.fixture(autouse=True)
async def _limpar_chaves_de_aviso():
    chaves = [f"automation:aviso-integracao:{fonte.value}" for fonte in FonteIntegracao]
    redis = get_redis()
    await redis.delete(*chaves)
    yield
    _descartar_redis_preso_a_outro_loop()
    redis = get_redis()
    await redis.delete(*chaves)
    await close_redis()


def _readiness(client, *, db=None, schema=None, redis=None):
    """Mesmo helper de `test_health.py`, replicado aqui para não importar de
    outro arquivo `test_*` (convenção do projeto)."""

    with (
        patch(
            "app.modules.health.routes.check_database", new_callable=AsyncMock
        ) as mock_db,
        patch(
            "app.modules.health.routes.check_database_schema", new_callable=AsyncMock
        ) as mock_schema,
        patch(
            "app.modules.health.routes.check_redis", new_callable=AsyncMock
        ) as mock_redis,
    ):
        if db is None:
            mock_db.return_value = True
        else:
            mock_db.side_effect = db
        if schema is None:
            mock_schema.return_value = True
        else:
            mock_schema.side_effect = schema
        if redis is None:
            mock_redis.return_value = True
        else:
            mock_redis.side_effect = redis
        response = client.get("/health/ready")
    return response


# ---------------------------------------------------------------------------
# (a) /health/ready — fonte banco_de_dados (D-01)
# ---------------------------------------------------------------------------


async def test_health_ready_falha_abre_aviso_de_banco_de_dados_sem_vazar_termo():
    """TestClient próprio (não a fixture `client` de conftest.py): o shutdown do
    lifespan da app (que também fecha o singleton `get_redis()`, em
    `app/bootstrap.py`) precisa acontecer ANTES de qualquer leitura direta do
    aviso neste mesmo teste — senão o shutdown, rodando no loop interno do
    TestClient, colide com a conexão nova criada no loop deste teste async
    (mesmo pitfall do engine do Postgres em conftest.py)."""

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as c:
        response = _readiness(c, db=ConnectionError("db down"))

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    for termo in TERMOS_TECNICOS_PROIBIDOS:
        # "redis" é a própria chave do check `redis` no corpo pré-existente de
        # /health/ready (`checks.redis`, ver test_health.py) — não é vazamento
        # de texto de aviso, que nunca entra nesta resposta.
        if termo == "redis":
            continue
        assert termo not in response.text.lower()

    _descartar_redis_preso_a_outro_loop()
    linhas = await listar_linhas_de_alerta()
    assert any(linha["category"] == "integracao_banco_de_dados" for linha in linhas)


async def test_health_ready_sucesso_depois_de_falha_resolve_aviso():
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as c:
        _readiness(c, db=ConnectionError("db down"))
        _readiness(c)  # tudo ok

    _descartar_redis_preso_a_outro_loop()
    linhas = await listar_linhas_de_alerta()
    assert not any(linha["category"] == "integracao_banco_de_dados" for linha in linhas)


# ---------------------------------------------------------------------------
# (b) DatabricksIngestionSource.estoque() — fonte estoque (D-02/D-07)
# ---------------------------------------------------------------------------


async def test_estoque_falha_propaga_abre_aviso_e_publica_evento_realtime():
    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_estoque",
            new_callable=AsyncMock,
            side_effect=DatabricksError(
                "falha de transporte", kind="transport", retryable=True
            ),
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        with pytest.raises(DatabricksError):
            await DatabricksIngestionSource().estoque()

        linhas = await listar_linhas_de_alerta()
        assert any(linha["category"] == "integracao_estoque" for linha in linhas)

        mock_enqueue.assert_awaited_once()
        _, kwargs = mock_enqueue.call_args
        assert kwargs["topic"] == "alerts"
        assert kwargs["event_type"] == "alerts.changed.v1"
        assert kwargs["payload"] == {"reason": "estoque_indisponivel"}


async def test_estoque_sucesso_depois_de_falha_resolve_e_publica_recuperado():
    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_estoque",
            new_callable=AsyncMock,
            side_effect=DatabricksError(
                "falha de transporte", kind="transport", retryable=True
            ),
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ),
        pytest.raises(DatabricksError),
    ):
        await DatabricksIngestionSource().estoque()

    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_estoque",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        await DatabricksIngestionSource().estoque()

        linhas = await listar_linhas_de_alerta()
        assert not any(linha["category"] == "integracao_estoque" for linha in linhas)

        mock_enqueue.assert_awaited_once()
        _, kwargs = mock_enqueue.call_args
        assert kwargs["payload"] == {"reason": "estoque_recuperado"}


async def test_estoque_sucesso_sem_aviso_aberto_nao_publica_evento():
    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_estoque",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        await DatabricksIngestionSource().estoque()

    mock_enqueue.assert_not_awaited()


async def test_estoque_falha_na_publicacao_realtime_nao_mascara_databricks_error():
    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_estoque",
            new_callable=AsyncMock,
            side_effect=DatabricksError(
                "falha de transporte", kind="transport", retryable=True
            ),
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
            side_effect=Exception("publicacao falhou"),
        ),
        pytest.raises(DatabricksError),
    ):
        await DatabricksIngestionSource().estoque()

    linhas = await listar_linhas_de_alerta()
    assert any(linha["category"] == "integracao_estoque" for linha in linhas)


async def test_pedidos_em_aberto_falha_tambem_abre_aviso_de_estoque():
    """Regressão: em produção o Databricks rejeita a sessão inteira (token
    expirado, warehouse parado), e `pedidos_em_aberto()` é a PRIMEIRA leitura
    de `_coletar_snapshot` — ela falha antes de `estoque()` ser sequer chamado,
    então instrumentar só `estoque()` nunca abre o aviso nesse cenário real
    (5 sincronizações consecutivas falharam com `databricks_http_rejected` e
    nenhum aviso abriu, 2026-09-09). `pedidos_em_aberto` reporta na mesma fonte
    `estoque`: as duas leituras dependem do mesmo Databricks, e o texto do
    aviso já fala de "consultar o estoque", não de "sincronizar pedidos"."""

    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_pedidos_em_aberto",
            new_callable=AsyncMock,
            side_effect=DatabricksError(
                "sessão rejeitada", kind="http_rejected", retryable=False
            ),
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        with pytest.raises(DatabricksError):
            await DatabricksIngestionSource().pedidos_em_aberto()

        linhas = await listar_linhas_de_alerta()
        assert any(linha["category"] == "integracao_estoque" for linha in linhas)

        mock_enqueue.assert_awaited_once()
        _, kwargs = mock_enqueue.call_args
        assert kwargs["payload"] == {"reason": "estoque_indisponivel"}


async def test_pedidos_em_aberto_sucesso_resolve_aviso_de_estoque():
    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_pedidos_em_aberto",
            new_callable=AsyncMock,
            side_effect=DatabricksError(
                "sessão rejeitada", kind="http_rejected", retryable=False
            ),
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ),
        pytest.raises(DatabricksError),
    ):
        await DatabricksIngestionSource().pedidos_em_aberto()

    with (
        patch(
            "app.modules.ingestao.infrastructure.adapters.ler_pedidos_em_aberto",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.modules.ingestao.infrastructure.adapters.enqueue_realtime_event",
            new_callable=AsyncMock,
        ),
    ):
        await DatabricksIngestionSource().pedidos_em_aberto()

    linhas = await listar_linhas_de_alerta()
    assert not any(linha["category"] == "integracao_estoque" for linha in linhas)


# ---------------------------------------------------------------------------
# (c) handler global — fonte banco_de_dados via erro de conexão real (PD-04)
# ---------------------------------------------------------------------------


def _registrar_rota_temporaria(app, path: str, exc: Exception):
    async def _rota_que_explode():
        raise exc

    app.add_api_route(path, _rota_que_explode, methods=["GET"])


async def test_handler_global_operational_error_abre_aviso_de_banco():
    from app.main import app as fastapi_app

    path = "/__teste-avisos-operational-error"
    _registrar_rota_temporaria(
        fastapi_app,
        path,
        OperationalError("SELECT 1", None, Exception("conexao perdida")),
    )
    try:
        with TestClient(fastapi_app, raise_server_exceptions=False) as c:
            response = c.get(path)
    finally:
        fastapi_app.router.routes = [
            route for route in fastapi_app.router.routes if route.path != path
        ]

    assert response.status_code == 500
    _descartar_redis_preso_a_outro_loop()
    linhas = await listar_linhas_de_alerta()
    assert any(linha["category"] == "integracao_banco_de_dados" for linha in linhas)


async def test_handler_global_integrity_error_nao_abre_aviso():
    from app.main import app as fastapi_app

    path = "/__teste-avisos-integrity-error"
    _registrar_rota_temporaria(
        fastapi_app,
        path,
        IntegrityError("INSERT ...", None, Exception("unique violation")),
    )
    try:
        with TestClient(fastapi_app, raise_server_exceptions=False) as c:
            response = c.get(path)
    finally:
        fastapi_app.router.routes = [
            route for route in fastapi_app.router.routes if route.path != path
        ]

    assert response.status_code == 500
    _descartar_redis_preso_a_outro_loop()
    linhas = await listar_linhas_de_alerta()
    assert not any(linha["category"] == "integracao_banco_de_dados" for linha in linhas)
