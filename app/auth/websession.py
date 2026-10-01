"""Server-side portal sessions in Redis.

Cookie: __Host-connect (Secure, HttpOnly, SameSite=Lax, Path=/).
Stages: "anon" (pre-login, only CSRF), "mfa" (password ok, TOTP pending),
"enrol" (password ok, TOTP enrolment required), "full".
Expiry: 12 h absolute, 1 h idle (config).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from ..config import get_config
from ..rds import redis

PREFIX = "websess:"


@dataclass
class WebSession:
    sid: str
    stage: str = "anon"
    user_id: str | None = None
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created: int = field(default_factory=lambda: int(time.time()))
    last_seen: int = field(default_factory=lambda: int(time.time()))
    data: dict = field(default_factory=dict)

    @property
    def is_full(self) -> bool:
        return self.stage == "full" and self.user_id is not None


def _serialise(s: WebSession) -> dict:
    import json
    return {"stage": s.stage, "user_id": s.user_id or "", "csrf": s.csrf, "created": s.created,
            "last_seen": s.last_seen, "data": json.dumps(s.data)}


async def save(s: WebSession) -> None:
    cfg = get_config()
    r = redis()
    remaining = cfg.session_absolute_seconds - (int(time.time()) - s.created)
    ttl = max(1, min(cfg.session_idle_seconds, remaining))
    async with r.pipeline(transaction=True) as p:
        p.hset(PREFIX + s.sid, mapping=_serialise(s))
        p.expire(PREFIX + s.sid, ttl)
        if s.user_id:
            p.sadd(f"{PREFIX}user:{s.user_id}", s.sid)
            p.expire(f"{PREFIX}user:{s.user_id}", cfg.session_absolute_seconds)
        await p.execute()


async def new(stage: str = "anon", user_id: str | None = None, data: dict | None = None) -> WebSession:
    s = WebSession(sid=secrets.token_urlsafe(32), stage=stage, user_id=user_id, data=data or {})
    await save(s)
    return s


async def load(sid: str | None, touch: bool = True) -> WebSession | None:
    import json
    if not sid or len(sid) > 100:
        return None
    h = await redis().hgetall(PREFIX + sid)
    if not h:
        return None
    cfg = get_config()
    now = int(time.time())
    s = WebSession(sid=sid, stage=h["stage"], user_id=h["user_id"] or None, csrf=h["csrf"],
                   created=int(h["created"]), last_seen=int(h["last_seen"]), data=json.loads(h.get("data") or "{}"))
    if now - s.created > cfg.session_absolute_seconds or now - s.last_seen > cfg.session_idle_seconds:
        await destroy(s)
        return None
    if touch and now - s.last_seen > 30:
        s.last_seen = now
        await save(s)
    return s


async def rotate(s: WebSession, stage: str, user_id: str | None) -> WebSession:
    """New session id on every privilege change (prevents fixation)."""
    await destroy(s)
    n = WebSession(sid=secrets.token_urlsafe(32), stage=stage, user_id=user_id, data=s.data)
    if stage == "full":
        n.created = int(time.time())  # absolute lifetime starts at full login
    await save(n)
    return n


async def destroy(s: WebSession) -> None:
    r = redis()
    await r.delete(PREFIX + s.sid)
    if s.user_id:
        await r.srem(f"{PREFIX}user:{s.user_id}", s.sid)


async def destroy_user(user_id: str, except_sid: str | None = None) -> None:
    r = redis()
    sids = await r.smembers(f"{PREFIX}user:{user_id}")
    for sid in sids:
        if sid != except_sid:
            await r.delete(PREFIX + sid)
            await r.srem(f"{PREFIX}user:{user_id}", sid)
    await r.publish(f"ch:user:{user_id}", '{"type":"session.revoked"}')
