"""Rate limit Redis pequeno e agnóstico de bounded context."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

_NAMESPACE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_DUAL_FIXED_WINDOW_SCRIPT = """
local now = redis.call('TIME')
local epoch = tonumber(now[1])
local window_seconds = tonumber(ARGV[1])
local window_id = math.floor(epoch / window_seconds)
local retry_after = window_seconds - (epoch % window_seconds)

local user_key = KEYS[1] .. ':' .. window_id
local ip_key = KEYS[2] .. ':' .. window_id
local user_count = redis.call('INCR', user_key)
local ip_count = redis.call('INCR', ip_key)

if user_count == 1 then
  redis.call('EXPIRE', user_key, retry_after + 1)
end
if ip_count == 1 then
  redis.call('EXPIRE', ip_key, retry_after + 1)
end

return {user_count, ip_count, retry_after}
"""


class RateLimitUnavailableError(RuntimeError):
    """Redis não pôde avaliar a política; o chamador decide fail-open/closed."""


@dataclass(frozen=True, slots=True)
class FixedWindowDecision:
    allowed: bool
    retry_after: int
    user_count: int
    ip_count: int


def _opaque_key(*, namespace: str, scope: str, identity: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{scope}:{identity}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"automation:rate:{namespace}:{scope}:{digest}"


def _redis_integer(value: object) -> int:
    if not isinstance(value, (bytes, int, str)):
        raise TypeError("resposta Redis não inteira")
    return int(value)


async def enforce_dual_fixed_window(
    redis: Redis,
    *,
    namespace: str,
    user_identity: str,
    ip_identity: str,
    secret: str,
    user_limit: int,
    ip_limit: int,
    window_seconds: int = 60,
) -> FixedWindowDecision:
    """Incrementa os buckets usuário+IP atomicamente e devolve a decisão."""
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise ValueError("namespace de rate limit inválido")
    if user_limit < 1 or ip_limit < 1 or not 1 <= window_seconds <= 3_600:
        raise ValueError("limites de rate limit inválidos")

    try:
        raw = await cast(
            Awaitable[object],
            redis.eval(
                _DUAL_FIXED_WINDOW_SCRIPT,
                2,
                _opaque_key(
                    namespace=namespace,
                    scope="user",
                    identity=user_identity[:256],
                    secret=secret,
                ),
                _opaque_key(
                    namespace=namespace,
                    scope="ip",
                    identity=ip_identity[:256],
                    secret=secret,
                ),
                str(window_seconds),
            ),
        )
        if isinstance(raw, (bytes, str)) or not isinstance(raw, Sequence):
            raise TypeError("resposta Redis inválida")
        if len(raw) != 3:
            raise ValueError("resposta Redis incompleta")
        user_count, ip_count, retry_after = (_redis_integer(value) for value in raw)
    except (RedisError, OSError, TimeoutError, TypeError, ValueError) as exc:
        raise RateLimitUnavailableError("rate limit indisponível") from exc

    return FixedWindowDecision(
        allowed=user_count <= user_limit and ip_count <= ip_limit,
        retry_after=max(1, retry_after),
        user_count=user_count,
        ip_count=ip_count,
    )


__all__ = [
    "FixedWindowDecision",
    "RateLimitUnavailableError",
    "enforce_dual_fixed_window",
]
