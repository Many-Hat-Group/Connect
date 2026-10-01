"""Presence keys in Redis (playbook Section 6).

presence:tech:{session}:{user}  set by portal heartbeats for sessions the
                                tech has visible (tile or session view)
presence:customer:{session}     set by customer page heartbeats
Both expire after presence_ttl_seconds (default 30 s).
The sweeper records when presence was last seen (absent-since timestamps)
so timeouts survive a worker restart.
"""
from __future__ import annotations

import time

from ..rds import redis

DEFAULT_TTL = 30


async def tech_seen(session_id: str, user_id: str, ttl: int = DEFAULT_TTL) -> None:
    r = redis()
    async with r.pipeline(transaction=False) as p:
        p.set(f"presence:tech:{session_id}:{user_id}", int(time.time()), ex=ttl)
        p.sadd(f"presence:techs:{session_id}", user_id)
        p.expire(f"presence:techs:{session_id}", 86400)
        await p.execute()


async def tech_left(session_id: str, user_id: str) -> None:
    await redis().delete(f"presence:tech:{session_id}:{user_id}")


async def techs_present(session_id: str) -> list[str]:
    r = redis()
    members = await r.smembers(f"presence:techs:{session_id}")
    present = []
    for uid in members:
        if await r.exists(f"presence:tech:{session_id}:{uid}"):
            present.append(uid)
        else:
            await r.srem(f"presence:techs:{session_id}", uid)
    return present


async def customer_seen(session_id: str, ttl: int = DEFAULT_TTL) -> None:
    await redis().set(f"presence:customer:{session_id}", int(time.time()), ex=ttl)


async def customer_present(session_id: str) -> bool:
    return bool(await redis().exists(f"presence:customer:{session_id}"))


async def clear(session_id: str) -> None:
    r = redis()
    members = await r.smembers(f"presence:techs:{session_id}")
    keys = [f"presence:tech:{session_id}:{u}" for u in members]
    keys += [f"presence:techs:{session_id}", f"presence:customer:{session_id}",
             f"absent:tech:{session_id}", f"absent:customer:{session_id}"]
    await r.delete(*keys)
