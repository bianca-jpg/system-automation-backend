"""Cobertura da quick task 260831-kic (OBS-03 + achados residuais do audit).

Até esta quick task: `GET /docs`/`/redoc`/`/openapi.json` ficavam sempre
expostos (nenhum gate por ENV), o CORSMiddleware aceitava qualquer método e
qualquer header (`allow_methods=["*"]`/`allow_headers=["*"]`), e era possível
subir PROD com `CORS_ORIGINS` apontando só para localhost sem o boot falhar.
"""

from collections.abc import Callable
from typing import cast

import pytest
from pydantic import ValidationError

from app.main import _docs_urls, app
from app.shared.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    ctor = cast(Callable[..., Settings], Settings)
    return ctor(_env_file=None, **overrides)


def _monkeypatch_prod_remoto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Envs remotas mínimas para o Settings passar pelas checagens de PROD
    anteriores à de CORS_ORIGINS (DATABASE_URL, Redis/Celery, JWT_SECRET,
    METRICS_API_KEY)."""
    monkeypatch.setattr("app.shared.config.settings.ENV", "PROD")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://automation_app:senha-remota-forte@db.interno.project:5432/system_automation",
    )
    monkeypatch.setenv("REDIS_URL", "redis://cache.interno.project:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://cache.interno.project:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://cache.interno.project:6379/1")
    monkeypatch.setenv("JWT_SECRET", "a" * 40)
    monkeypatch.setenv("METRICS_API_KEY", "a" * 40)


def test_docs_urls_fecha_os_tres_campos_em_prod() -> None:
    assert _docs_urls("PROD") == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }


@pytest.mark.parametrize("env", ["DEV", "TEST", "dev", "hml", ""])
def test_docs_urls_mantem_defaults_fora_de_prod(env: str) -> None:
    assert _docs_urls(env) == {}


def test_cors_middleware_nao_usa_wildcard_de_metodo_ou_header() -> None:
    cors = next(
        m for m in app.user_middleware if cast(type, m.cls).__name__ == "CORSMiddleware"
    )
    allow_methods = cast(list[str], cors.kwargs["allow_methods"])
    allow_headers = cast(list[str], cors.kwargs["allow_headers"])

    assert "*" not in allow_methods
    assert "*" not in allow_headers
    assert set(allow_methods) == {
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "OPTIONS",
    }
    assert set(allow_headers) == {"Authorization", "Content-Type"}


def test_prod_com_cors_origins_so_localhost_falha_o_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _monkeypatch_prod_remoto(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:5173")

    with pytest.raises(ValidationError) as exc:
        _settings()

    assert (
        "PROD exige CORS_ORIGINS com ao menos uma origem que não seja localhost"
        in str(exc.value)
    )


def test_prod_com_cors_origins_vazio_falha_o_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _monkeypatch_prod_remoto(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", "")

    with pytest.raises(ValidationError) as exc:
        _settings()

    assert (
        "PROD exige CORS_ORIGINS com ao menos uma origem que não seja localhost"
        in str(exc.value)
    )


def test_prod_com_cors_origins_real_sobe_sem_erro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _monkeypatch_prod_remoto(monkeypatch)
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://or-automation.project.com.br,http://localhost:3000",
    )

    settings = _settings()

    assert settings.cors_origins_list == [
        "https://or-automation.project.com.br",
        "http://localhost:3000",
    ]
