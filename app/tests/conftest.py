import asyncio
import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL, make_url

_TEST_DATABASE_HOSTS = {"127.0.0.1", "localhost", "::1", "dev_db", "postgres"}
_TEST_REDIS_HOSTS = {"127.0.0.1", "localhost", "::1", "redis"}


def _configure_isolated_test_database() -> None:
    explicit_url = os.getenv("TEST_DATABASE_URL")
    inherited_url = os.getenv("DATABASE_URL")
    candidate = explicit_url

    if candidate is None and inherited_url:
        inherited = make_url(inherited_url)
        if (inherited.database or "").endswith("_test"):
            candidate = inherited_url

    if candidate is None:
        in_container = os.path.exists("/.dockerenv")
        host = (
            os.getenv("DEV_POSTGRES_HOST", "dev_db")
            if in_container
            else os.getenv("DEV_POSTGRES_HOST_LOCAL", "localhost")
        )
        candidate = URL.create(
            drivername="postgresql+asyncpg",
            username=os.getenv(
                "DEV_POSTGRES_USER", os.getenv("POSTGRES_USER", "automation")
            ),
            password=os.getenv(
                "DEV_POSTGRES_PASSWORD", os.getenv("POSTGRES_PASSWORD", "automation")
            ),
            host=host,
            port=int(
                os.getenv("DEV_POSTGRES_PORT", os.getenv("POSTGRES_PORT", "5432"))
            ),
            database="system_automation_test",
        ).render_as_string(hide_password=False)

    parsed = make_url(candidate)
    if parsed.host not in _TEST_DATABASE_HOSTS or not (parsed.database or "").endswith(
        "_test"
    ):
        raise RuntimeError(
            "Tests require a local/Docker PostgreSQL database whose name ends in '_test'."
        )
    os.environ["DATABASE_URL"] = candidate


_configure_isolated_test_database()


def _configure_isolated_test_redis() -> None:
    """Keep tests away from shared DEV/PROD Redis instances.

    ``TEST_REDIS_URL`` is the only supported override.  The dedicated database
    number is intentional: realtime tickets, streams and rate-limit counters
    must never collide with the local application database (normally ``/0``).
    """

    explicit_url = os.getenv("TEST_REDIS_URL")
    if explicit_url is None:
        host = "redis" if os.path.exists("/.dockerenv") else "localhost"
        explicit_url = f"redis://{host}:6379/15"

    parsed = urlsplit(explicit_url)
    database = parsed.path.removeprefix("/")
    if (
        parsed.scheme not in {"redis", "rediss"}
        or parsed.hostname not in _TEST_REDIS_HOSTS
        or database != "15"
    ):
        raise RuntimeError(
            "Tests require a local/Docker Redis URL using the dedicated database 15."
        )

    os.environ["REDIS_URL"] = explicit_url
    os.environ["CELERY_BROKER_URL"] = explicit_url
    os.environ["CELERY_RESULT_BACKEND"] = explicit_url


_configure_isolated_test_redis()

os.environ.setdefault("JWT_SECRET", "test-secret")
# ENV é resolvido em tempo de import em app/shared/config/settings.py
# (os.getenv("ENV", "dev").upper()). Governa postgres_ssl_mode e as travas de PROD.
os.environ.setdefault("ENV", "dev")
# Readers validate their source table before reaching the mocked Databricks
# client. Keep that contract deterministic in CI instead of inheriting a local
# .env merely because one happens to exist on the developer machine.
os.environ.setdefault("DATABRICKS_TABELA_PEDIDOS", "test.schema.pedidos")
os.environ.setdefault("DATABRICKS_TABELA_ESTOQUE", "test.schema.estoque")
os.environ.setdefault(
    "DATABRICKS_TABELA_PEDIDOS_PROCESSADOS",
    "test.schema.pedidos_processados",
)
os.environ.setdefault("DATABRICKS_TABELA_TAMANHO_REF", "test.schema.tamanho_ref")

if os.path.exists("/.dockerenv"):
    os.environ.setdefault("DEV_POSTGRES_HOST", "dev_db")

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _limpar_avisos_integracao_e_redis_singleton_apos_teste():
    """Testes de `/health/ready` e da ingestão (quick task 260909-ek7) acionam
    `registrar_falha_integracao`/`registrar_sucesso_integracao` de verdade a
    cada check/sincronização — sem essa limpeza, dois problemas se somam:

    1. O aviso ficaria aberto no Redis de teste (db 15) e vazaria para outros
       arquivos que iteram as linhas de alerta (`listar_linhas_de_alerta`),
       como `test_pedidos_read_projection.py`. A limpeza usa cliente novo por
       chamada (mesmo padrão de `test_auth_reuso_refresh_token.py`), nunca o
       singleton, para não prender a conexão a um event loop já encerrado.

    2. O singleton `get_redis()` (`app/shared/infrastructure/redis_client.py`)
       fica preso ao event loop do PRIMEIRO teste que o inicializa; testes
       seguintes (pytest-asyncio cria um loop novo por teste, por padrão)
       herdam uma conexão presa a um loop já fechado e explodem com
       "attached to a different loop" — mesmo pitfall do engine do Postgres
       logo abaixo, agora também presente no Redis porque a instrumentação
       de avisos de integração passou a tocar `get_redis()` em código de
       produção acionado por testes que antes nunca usavam Redis. Descartar
       a referência (sem fechar) é seguro em escopo de teste: o próximo
       `get_redis()` cria um cliente novo já preso ao loop correto."""

    yield

    import redis.asyncio as aioredis

    import app.shared.infrastructure.redis_client as redis_client_module
    from app.shared.config.settings import get_settings

    async def _limpar() -> None:
        cliente = aioredis.from_url(
            get_settings().effective_redis_url, decode_responses=True
        )
        try:
            await cliente.delete(
                "automation:aviso-integracao:banco_de_dados",
                "automation:aviso-integracao:estoque",
            )
        finally:
            await cliente.aclose()

    asyncio.run(_limpar())
    redis_client_module._redis = None


@pytest.fixture(autouse=True)
def _dispose_db_engine_after_test():
    """asyncpg prende a conexão ao event loop que a abriu; testes async diretos
    (pytest-asyncio) e testes via TestClient (anyio) usam loops diferentes, e o
    engine é um singleton global (app.shared.database.session). Sem descartar o
    pool entre testes, o próximo teste pode reusar uma conexão presa a um loop já
    fechado ("another operation is in progress"). Mesmo padrão já usado em
    app/workers/tasks/ingestao.py para o mesmo problema."""
    yield
    from app.shared.database.session import engine

    asyncio.run(engine.dispose())
