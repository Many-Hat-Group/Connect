"""Shared Redis client."""
from redis.asyncio import Redis

from .config import get_config

_redis: Redis | None = None


def redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_config().redis_url, decode_responses=True)
    return _redis


async def close() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None
