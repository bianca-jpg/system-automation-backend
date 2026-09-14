import os
import subprocess
import sys

from alembic.config import Config

from app.shared.config.settings import _resolve_postgres_host, _resolve_redis_url
from app.shared.database.options import alembic_config_url


def test_alembic_config_url_preserva_percent_encoding_de_credenciais() -> None:
    original = "postgresql+asyncpg://usuario:a%25b@db:5432/automation"
    config = Config()

    config.set_main_option("sqlalchemy.url", alembic_config_url(original))

    assert config.get_main_option("sqlalchemy.url") == original


def test_host_resolve_aliases_compose_fora_do_container(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.shared.config.settings.os.path.exists", lambda _path: False
    )
    monkeypatch.delenv("DEV_POSTGRES_HOST_LOCAL", raising=False)

    assert _resolve_postgres_host("dev_db") == "localhost"
    assert _resolve_postgres_host("system_automation_db") == "localhost"
    assert _resolve_postgres_host("database.example") == "database.example"


def test_redis_local_preserva_database_de_broker_e_resultado(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.shared.config.settings.os.path.exists", lambda _path: False
    )
    monkeypatch.setenv("REDIS_URL_LOCAL", "redis://localhost:6381")

    assert _resolve_redis_url("redis://redis:6379/0") == "redis://localhost:6381/0"
    assert _resolve_redis_url("redis://system_automation_redis:6379/1") == (
        "redis://localhost:6381/1"
    )


def _settings_prod_process(**overrides: str) -> subprocess.CompletedProcess[str]:
    prefixes = (
        "POSTGRES_",
        "DEV_POSTGRES_",
        "PROD_POSTGRES_",
        "REDIS_",
        "CELERY_",
    )
    exact = {"DATABASE_URL", "ENV", "JWT_SECRET"}
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in exact and not key.startswith(prefixes)
    }
    environment.update({"ENV": "prod", **overrides})
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.shared.config.settings import Settings; "
                "Settings(_env_file=None); print('settings-prod-ok')"
            ),
        ],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_settings_prod_falha_fechado_sem_segredos_ou_com_dev_only() -> None:
    ausente = _settings_prod_process()
    somente_dev = _settings_prod_process(
        DEV_POSTGRES_USER="dev",
        DEV_POSTGRES_PASSWORD="dev-password",
        DEV_POSTGRES_DB="dev_db",
        DEV_POSTGRES_HOST="dev.example",
        DEV_POSTGRES_PORT="5432",
    )

    assert ausente.returncode != 0
    assert somente_dev.returncode != 0
    assert "PROD exige" in (ausente.stderr + ausente.stdout)
    assert "PROD exige" in (somente_dev.stderr + somente_dev.stdout)


def test_settings_prod_aceita_somente_configuracao_remota_explicita() -> None:
    result = _settings_prod_process(
        POSTGRES_USER="prod_user",
        POSTGRES_PASSWORD="prod-password-with-entropy",
        POSTGRES_DB="automation_prod",
        POSTGRES_HOST="db.prod.internal",
        POSTGRES_PORT="5432",
        REDIS_URL="rediss://cache.prod.internal:6379/0",
        CELERY_BROKER_URL="rediss://cache.prod.internal:6379/0",
        CELERY_RESULT_BACKEND="rediss://cache.prod.internal:6379/1",
        JWT_SECRET="prod-jwt-secret-with-at-least-thirty-two-characters",
        METRICS_API_KEY="prod-metrics-api-key-with-thirty-two-chars",
        CORS_ORIGINS="https://or-automation.project.com.br",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "settings-prod-ok"
