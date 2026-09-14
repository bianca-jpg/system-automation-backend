import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status

from app.shared.database.session import DatabaseSchemaNotReadyError

# Strings proibidas no corpo bruto da resposta de /health/ready: qualquer nome de
# classe de exceção (ou trecho de mensagem) que vazaria detalhe interno para um
# cliente externo. Acrescentar aqui é o único ponto de manutenção para novos cenários.
_STRINGS_PROIBIDAS_NO_CORPO = (
    "ConnectionError",
    "ConnectionRefusedError",
    "RuntimeError",
    "DatabaseSchemaNotReadyError",
    "Traceback",
    "db down",
    "redis down",
    "Alembic",
)


def _readiness(client, *, db=None, schema=None, redis=None):
    """Chama GET /health/ready patchando os três checks no ponto de import do módulo.

    `db`/`schema`/`redis` recebem um `side_effect` (exceção) ou `None` para sucesso.
    """
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


def test_health_liveness(client):
    response = client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_home(client):
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "Online"


def test_core_status_exige_autenticacao(client):
    """`/v1/status` está atrás de require_viewer (o router `core` declara a
    dependência), diferente de `/` e `/health*`, que são públicos para o probe
    do ECS. Sem Bearer o esperado é 401 — este teste nasceu antes do RBAC e
    afirmava 200."""
    response = client.get("/v1/status")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_health_readiness_ok(client):
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
        mock_db.return_value = True
        mock_schema.return_value = True
        mock_redis.return_value = True
        response = client.get("/health/ready")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "databaseSchema": "ok", "redis": "ok"},
    }


def test_health_readiness_degraded(client):
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
        mock_db.side_effect = ConnectionError("db down")
        mock_redis.return_value = True
        response = client.get("/health/ready")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "status": "degraded",
        "checks": {
            "database": "fail",
            "databaseSchema": "unavailable",
            "redis": "ok",
        },
    }
    mock_schema.assert_not_awaited()


def test_health_readiness_rejects_outdated_schema(client):
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
        mock_db.return_value = True
        mock_schema.side_effect = DatabaseSchemaNotReadyError(
            "database schema is not at Alembic head"
        )
        mock_redis.return_value = True
        response = client.get("/health/ready")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "status": "degraded",
        "checks": {
            "database": "ok",
            "databaseSchema": "fail",
            "redis": "ok",
        },
    }


@pytest.mark.parametrize(
    "db,schema,redis",
    [
        pytest.param(ConnectionError("db down"), None, None, id="postgres-fora"),
        pytest.param(
            None,
            DatabaseSchemaNotReadyError("database schema is not at Alembic head"),
            ConnectionRefusedError("redis down"),
            id="schema-atrasado-e-redis-fora",
        ),
    ],
)
def test_health_readiness_nao_vaza_nome_de_classe_de_excecao(client, db, schema, redis):
    response = _readiness(client, db=db, schema=schema, redis=redis)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    for proibida in _STRINGS_PROIBIDAS_NO_CORPO:
        assert proibida not in response.text
    assert set(response.json()["checks"].values()) <= {"ok", "fail", "unavailable"}


def test_health_readiness_loga_nome_da_classe_no_servidor(client, caplog):
    with caplog.at_level(logging.ERROR):
        response = _readiness(client, db=ConnectionError("db down"))

    assert "ConnectionError" in caplog.text
    assert "database" in caplog.text
    assert "ConnectionError" not in response.text
