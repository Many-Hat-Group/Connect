"""Short-lived per-participant tokens for the VDO.Ninja handshake server.

The router's auth_request asks /internal/signal-auth whether the `t=` query
value on /signal is valid. Tokens live in Redis and are indexed by session so
that ending a session revokes them all at once.
"""
import secrets

from ..rds import redis

TTL = 4 * 3600


def _key(token: str) -> str:
    return f"signal:tok:{token}"


async def issue(session_id: str, role: str, subject: str) -> str:
    token = secrets.token_urlsafe(32)
    r = redis()
    async with r.pipeline(transaction=True) as p:
        p.set(_key(token), f"{session_id}|{role}|{subject}", ex=TTL)
        p.sadd(f"signal:sess:{session_id}", token)
        p.expire(f"signal:sess:{session_id}", TTL)
        p.sadd(f"signal:sessuser:{session_id}:{subject}", token)
        p.expire(f"signal:sessuser:{session_id}:{subject}", TTL)
        await p.execute()
    return token


async def validate(token: str) -> str | None:
    if not token or len(token) > 100:
        return None
    return await redis().get(_key(token))


async def revoke_session(session_id: str) -> None:
    r = redis()
    tokens = await r.smembers(f"signal:sess:{session_id}")
    if tokens:
        await r.delete(*[_key(t) for t in tokens])
    await r.delete(f"signal:sess:{session_id}")


async def revoke_user(session_id: str, subject: str) -> None:
    """Revoke the signal tokens one participant holds for a session."""
    r = redis()
    key = f"signal:sessuser:{session_id}:{subject}"
    tokens = await r.smembers(key)
    if tokens:
        await r.delete(*[_key(t) for t in tokens])
        await r.srem(f"signal:sess:{session_id}", *tokens)
    await r.delete(key)
