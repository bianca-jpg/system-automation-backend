import redis.asyncio as aioredis

from app.shared.config.settings import get_settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.effective_redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.realtime_redis_connect_timeout_seconds,
            socket_timeout=settings.realtime_redis_socket_timeout_seconds,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    return _redis


async def check_redis() -> bool:
    client = get_redis()
    return bool(await client.ping())


async def close_redis() -> None:
    """Fecha o pool Redis compartilhado e permite nova inicialização limpa."""

    global _redis
    client, _redis = _redis, None
    if client is not None:
        await client.aclose()
