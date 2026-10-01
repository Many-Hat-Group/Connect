"""Who is watching each stream (thumbnail or full view), with the
server-side cap on concurrent viewers per stream. Each viewer costs the
customer upload bandwidth (VDO.Ninja is a mesh), so the cap counts both
thumbnails and full views. A user holds at most one view per stream: opening
the full view replaces their thumbnail."""
from __future__ import annotations

import json
import time

from ..rds import redis

TTL = 30


def _key(stream_id: str, user_id: str) -> str:
    return f"view:{stream_id}:{user_id}"


async def list_for(stream_id: str) -> list[dict]:
    r = redis()
    out = []
    for uid in await r.smembers(f"views:{stream_id}"):
        v = await r.get(_key(stream_id, uid))
        if v:
            out.append(dict(json.loads(v), user_id=uid))
        else:
            await r.srem(f"views:{stream_id}", uid)
    return sorted(out, key=lambda x: x.get("since", 0))


async def add(stream_id: str, user_id: str, name: str, kind: str, cap: int) -> bool:
    """Register (or refresh) a view. False if the stream is at its cap."""
    current = await list_for(stream_id)
    mine = next((v for v in current if v["user_id"] == user_id), None)
    if mine is None and len(current) >= cap:
        return False
    r = redis()
    since = mine["since"] if mine else int(time.time())
    async with r.pipeline(transaction=True) as p:
        p.set(_key(stream_id, user_id), json.dumps({"name": name, "kind": kind, "since": since}), ex=TTL)
        p.sadd(f"views:{stream_id}", user_id)
        p.expire(f"views:{stream_id}", 86400)
        await p.execute()
    return True


async def remove(stream_id: str, user_id: str) -> None:
    r = redis()
    await r.delete(_key(stream_id, user_id))
    await r.srem(f"views:{stream_id}", user_id)


async def for_session(stream_ids: list[str]) -> dict[str, list[dict]]:
    return {sid: await list_for(sid) for sid in stream_ids}


async def refresh(stream_id: str, user_id: str) -> bool:
    """Heartbeat for an existing view; never creates one."""
    return bool(await redis().expire(_key(stream_id, user_id), TTL))
