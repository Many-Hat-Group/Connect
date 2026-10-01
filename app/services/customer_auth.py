"""Customer session tokens: random, HttpOnly cookie, scoped to one session.
Required by every customer endpoint, websocket and download."""
from __future__ import annotations

import secrets

from fastapi import Request

from ..config import get_config
from ..rds import redis

TTL = 12 * 3600


async def issue(session_id: str) -> str:
    token = secrets.token_urlsafe(32)
    r = redis()
    async with r.pipeline(transaction=True) as p:
        p.set(f"cust:tok:{token}", session_id, ex=TTL)
        p.sadd(f"cust:sess:{session_id}", token)
        p.expire(f"cust:sess:{session_id}", TTL)
        await p.execute()
    return token


async def session_for(token: str | None) -> str | None:
    if not token or len(token) > 100:
        return None
    return await redis().get(f"cust:tok:{token}")


async def from_request(request_or_ws) -> str | None:
    return await session_for(request_or_ws.cookies.get(get_config().customer_cookie))


async def revoke_session(session_id: str) -> None:
    r = redis()
    toks = await r.smembers(f"cust:sess:{session_id}")
    if toks:
        await r.delete(*[f"cust:tok:{t}" for t in toks])
    await r.delete(f"cust:sess:{session_id}")


def set_cookie(response, token: str) -> None:
    response.set_cookie(get_config().customer_cookie, token, max_age=TTL, path="/", secure=True,
                        httponly=True, samesite="strict")


def clear_cookie(response) -> None:
    response.delete_cookie(get_config().customer_cookie, path="/", secure=True, httponly=True, samesite="strict")
