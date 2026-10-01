"""Redis-backed counters and lockouts."""
from ..rds import redis


async def hit(key: str, window: int) -> int:
    """Increment a fixed-window counter; returns the count in this window."""
    r = redis()
    async with r.pipeline(transaction=True) as p:
        p.incr(key)
        p.expire(key, window, nx=True)
        count, _ = await p.execute()
    return int(count)


async def count(key: str) -> int:
    v = await redis().get(key)
    return int(v) if v else 0


async def reset(key: str) -> None:
    await redis().delete(key)


async def lock(key: str, seconds: int) -> None:
    await redis().set(f"lock:{key}", "1", ex=seconds)


async def locked(key: str) -> int:
    """Remaining lock seconds, 0 if not locked."""
    ttl = await redis().ttl(f"lock:{key}")
    return max(int(ttl), 0)
