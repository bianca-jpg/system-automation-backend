"""Guard de `GET /metrics` — API key fixa em header, não RBAC de usuário.

O scraper Prometheus não tem usuário/JWT (não faz login), então os guards
hierárquicos de `app.modules.auth.infrastructure.http.dependencies`
(`require_viewer`…`require_tecnico`) não servem aqui — a via correta é uma
chave fixa comparada em tempo constante contra `Settings.metrics_api_key`.

Mora em `app/shared/metrics/`, não em `app/shared/security/`: aquele pacote
existe só para reexportar o RBAC do bounded context de auth (Open Host
Service de Identidade & Acesso); um guard de chave de infraestrutura para um
endpoint de telemetria não pertence a ele.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.shared.config.settings import get_settings

METRICS_API_KEY_HEADER = "X-Metrics-Key"

metrics_key_scheme = APIKeyHeader(name=METRICS_API_KEY_HEADER, auto_error=False)


async def require_metrics_key(chave: str | None = Depends(metrics_key_scheme)) -> None:
    # get_settings() é chamado aqui dentro (não no import do módulo) para o
    # teste poder patchar app.shared.metrics.security.get_settings.
    esperada = get_settings().metrics_api_key

    if not esperada:
        # Fail-closed: sem chave configurada, o endpoint nunca abre — nem
        # com header ausente, nem com qualquer valor enviado.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="METRICS_API_KEY não está configurada",
        )

    if not chave:
        # Sem WWW-Authenticate: API key de header não é challenge de HTTP
        # auth (diferente do Bearer usado pelo RBAC de usuário).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Metrics-Key header",
        )

    # Comparação timing-safe sobre bytes: compare_digest levanta TypeError
    # com str não-ASCII, e um header UTF-8 arbitrário viraria 500 em vez de
    # 403 se comparássemos str direto.
    if not hmac.compare_digest(chave.encode("utf-8"), esperada.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Metrics-Key header",
        )
