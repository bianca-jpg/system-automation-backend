from __future__ import annotations

import time
from typing import Any

import httpx
from jose import JWTError
from jose import jwt as jose_jwt

from app.modules.auth.domain.exceptions import TokenMicrosoftInvalidoError

# JWKS do Entra ID roda por bem mais que isso, mas 1h é um teto seguro: evita
# bater na Microsoft em todo login sem arriscar ficar com chave desatualizada
# por muito tempo (rotação sem aviso é o caso raro que o retry abaixo cobre).
_JWKS_CACHE_TTL_SECONDS = 3600
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def _obter_jwks(tenant_id: str) -> dict[str, Any]:
    cached = _jwks_cache.get(tenant_id)
    if cached is not None and time.monotonic() - cached[0] < _JWKS_CACHE_TTL_SECONDS:
        return cached[1]

    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        jwks = response.json()

    _jwks_cache[tenant_id] = (time.monotonic(), jwks)
    return jwks


def _encontrar_chave(jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
    return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)


async def validar_id_token_microsoft(
    id_token: str, *, tenant_id: str, client_id: str
) -> dict[str, Any]:
    """Verifica assinatura, emissor, audiência e validade do ID token da Microsoft."""
    try:
        header = jose_jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise TokenMicrosoftInvalidoError() from exc

    jwks = await _obter_jwks(tenant_id)
    chave = _encontrar_chave(jwks, header.get("kid"))
    if chave is None:
        # A chave pode ter rotacionado desde o último cache: descarta e busca
        # uma vez mais antes de desistir.
        _jwks_cache.pop(tenant_id, None)
        jwks = await _obter_jwks(tenant_id)
        chave = _encontrar_chave(jwks, header.get("kid"))
    if chave is None:
        raise TokenMicrosoftInvalidoError()

    try:
        return jose_jwt.decode(
            id_token,
            chave,
            algorithms=["RS256"],
            audience=client_id,
            issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        )
    except JWTError as exc:
        raise TokenMicrosoftInvalidoError() from exc
