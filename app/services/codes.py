"""Join codes: 4 letters from A to Z minus I, L and O (23 letters,
279,841 combinations). Single use, expire after code_ttl_seconds unused,
claim attempts rate limited per IP and watched globally."""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import ratelimit
from ..models import JoinCode, utcnow
from ..rds import redis

log = logging.getLogger("connect.codes")
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"
assert len(ALPHABET) == 23 and not set("ILO") & set(ALPHABET)
LENGTH = 4
BRUTEFORCE_BANNER_KEY = "alert:code_bruteforce"


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def normalise(raw: str) -> str | None:
    """Uppercase, strip spaces and dashes. None if it can't be a valid code."""
    code = "".join(ch for ch in (raw or "").upper() if not ch.isspace() and ch != "-")
    if len(code) != LENGTH or any(ch not in ALPHABET for ch in code):
        return None
    return code


async def issue(db: AsyncSession, session_id, user_id, ttl_seconds: int) -> JoinCode:
    for _ in range(20):
        jc = JoinCode(code=generate(), session_id=session_id, created_by=user_id,
                      expires_at=utcnow() + timedelta(seconds=ttl_seconds))
        try:
            async with db.begin_nested():
                db.add(jc)
            return jc
        except IntegrityError:
            continue  # collided with an open code; pick another
    raise RuntimeError("could not allocate a join code")


class ClaimResult:
    def __init__(self, ok: bool, session_id=None, locked_seconds: int = 0):
        self.ok, self.session_id, self.locked_seconds = ok, session_id, locked_seconds


async def claim(db: AsyncSession, raw_code: str, ip: str, limits: dict) -> ClaimResult:
    """Atomically claim an open, unexpired code. Wrong, expired and used codes
    are indistinguishable to the caller."""
    ip_key = f"code:fail:{ip}"
    locked = await ratelimit.locked(ip_key)
    if locked:
        return ClaimResult(False, locked_seconds=locked)
    code = normalise(raw_code)
    session_id = None
    if code:
        now = utcnow()
        session_id = await db.scalar(
            update(JoinCode)
            .where(JoinCode.code == code, JoinCode.claimed_at.is_(None), JoinCode.revoked.is_(False),
                   JoinCode.expires_at > now)
            .values(claimed_at=now, claimed_ip=ip)
            .returning(JoinCode.session_id))
    if session_id is not None:
        return ClaimResult(True, session_id=session_id)
    return ClaimResult(False)


async def record_failure(ip: str, limits: dict) -> tuple[int, bool, bool]:
    """Count a failed claim. Returns (ip failures, ip now locked, global alert raised now)."""
    ip_key = f"code:fail:{ip}"
    n = await ratelimit.hit(ip_key, limits["code_fail_window_seconds"])
    locked_now = False
    if n >= limits["code_fail_per_ip"]:
        await ratelimit.lock(ip_key, limits["code_lockout_seconds"])
        await ratelimit.reset(ip_key)
        locked_now = True
    g = await ratelimit.hit("code:fail:global", 3600)
    alert = False
    if g > limits["code_fail_global_per_hour"]:
        # Raise the admin banner once per hour.
        alert = bool(await redis().set(BRUTEFORCE_BANNER_KEY, g, ex=3600, nx=True))
        if alert:
            log.warning("join code brute force suspected: %s failed claims in the last hour", g)
    return n, locked_now, alert


async def bruteforce_banner() -> int | None:
    v = await redis().get(BRUTEFORCE_BANNER_KEY)
    return int(v) if v else None
