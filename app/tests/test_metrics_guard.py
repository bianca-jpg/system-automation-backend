"""Cobertura do guard de `GET /metrics`.

Até a Fase 260827-emo, `GET /metrics` (Prometheus) era público — qualquer
cliente que alcançasse a porta 8000 lia telemetria interna (conexões
WebSocket do realtime, backlog do outbox, resultados/duração de SMTP das
comunicações) sem nenhuma credencial. Este arquivo cobre o guard por API key
de header (`X-Metrics-Key` / `METRICS_API_KEY`) que fecha esse buraco:
fail-fast de boot em PROD (Settings), e os códigos 401/403/200/503 do
endpoint.
"""

import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import status
from pydantic import ValidationError

from app.shared.config.settings import Settings
from app.shared.metrics.router import router
from app.shared.metrics.security import require_metrics_key

_CHAVE_VALIDA = "a" * 40


def _settings(**overrides: object) -> Settings:
    # pyright sintetiza o __init__ de Settings a partir dos campos do
    # modelo e não enxerga o __init__ real de BaseSettings (que aceita
    # _env_file); o cast do construtor para o Callable real resolve isso
    # sem afetar o comportamento em runtime — os valores continuam vindo
    # só das variáveis de ambiente monkeypatched em cada teste.
    ctor = cast(Callable[..., Settings], Settings)
    return ctor(_env_file=None, **overrides)


def _monkeypatch_prod_remoto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Envs remotas mínimas para o Settings passar pelas checagens de PROD
    anteriores à de METRICS_API_KEY (DATABASE_URL, Redis/Celery, JWT_SECRET)."""
    monkeypatch.setattr("app.shared.config.settings.ENV", "PROD")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://automation_app:senha-remota-forte@db.interno.project:5432/system_automation",
    )
    monkeypatch.setenv("REDIS_URL", "redis://cache.interno.project:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://cache.interno.project:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://cache.interno.project:6379/1")
    monkeypatch.setenv("JWT_SECRET", "a" * 40)
    monkeypatch.delenv("METRICS_API_KEY", raising=False)


def test_metrics_api_key_default_vazio_sem_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRICS_API_KEY", raising=False)

    assert _settings().metrics_api_key == ""


def test_metrics_api_key_recebe_valor_da_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRICS_API_KEY", "chave-de-teste")

    assert _settings().metrics_api_key == "chave-de-teste"


def test_prod_sem_metrics_api_key_falha_o_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    _monkeypatch_prod_remoto(monkeypatch)

    with pytest.raises(ValidationError) as exc:
        _settings()

    assert "PROD exige METRICS_API_KEY com pelo menos 32 caracteres" in str(exc.value)


def test_prod_com_metrics_api_key_curta_falha_o_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _monkeypatch_prod_remoto(monkeypatch)
    monkeypatch.setenv("METRICS_API_KEY", "a" * 31)

    with pytest.raises(ValidationError) as exc:
        _settings()

    assert "PROD exige METRICS_API_KEY com pelo menos 32 caracteres" in str(exc.value)


def test_prod_com_metrics_api_key_forte_sobe_sem_erro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _monkeypatch_prod_remoto(monkeypatch)
    monkeypatch.setenv("METRICS_API_KEY", "a" * 64)
    monkeypatch.setenv("CORS_ORIGINS", "https://or-automation.project.com.br")

    settings = _settings()

    assert settings.metrics_api_key == "a" * 64


def _patch_metrics_settings(metrics_api_key: str):
    return patch(
        "app.shared.metrics.security.get_settings",
        return_value=SimpleNamespace(metrics_api_key=metrics_api_key),
    )


def test_metrics_sem_header_responde_401_e_nao_devolve_metricas(client) -> None:
    with _patch_metrics_settings(_CHAVE_VALIDA):
        resp = client.get("/metrics")

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert "python_info" not in resp.text


def test_metrics_com_header_vazio_responde_401(client) -> None:
    with _patch_metrics_settings(_CHAVE_VALIDA):
        resp = client.get("/metrics", headers={"X-Metrics-Key": ""})

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_metrics_com_chave_errada_responde_403(client) -> None:
    with _patch_metrics_settings(_CHAVE_VALIDA):
        resp = client.get("/metrics", headers={"X-Metrics-Key": "chave-errada"})

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_metrics_com_chave_correta_responde_200(client) -> None:
    with _patch_metrics_settings(_CHAVE_VALIDA):
        resp = client.get("/metrics", headers={"X-Metrics-Key": _CHAVE_VALIDA})

    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers["content-type"].startswith("text/plain")
    assert len(resp.content) > 0


def test_metrics_sem_chave_configurada_responde_503_mesmo_com_header(
    client,
) -> None:
    with _patch_metrics_settings(""):
        resp_sem_header = client.get("/metrics")
        resp_com_header = client.get(
            "/metrics", headers={"X-Metrics-Key": "qualquer-coisa"}
        )

    assert resp_sem_header.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert resp_com_header.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_require_metrics_key_usa_comparacao_constant_time() -> None:
    assert "compare_digest" in inspect.getsource(require_metrics_key)


def test_router_de_metrics_carrega_o_guard_por_construcao() -> None:
    dependencies = [dep.dependency for dep in router.dependencies]
    assert require_metrics_key in dependencies


def test_health_e_home_continuam_publicos(client) -> None:
    assert client.get("/health").status_code == status.HTTP_200_OK
    assert client.get("/").status_code == status.HTTP_200_OK
