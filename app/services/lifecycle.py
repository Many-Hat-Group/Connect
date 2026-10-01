"""Worker jobs: the sweeper (timeouts) and retention (playbook Phase 6).

Absence is tracked as "absent since" timestamps in Redis
(absent:tech:<session>, absent:customer:<session>), so a worker restart
does not reset the clocks. All thresholds come from settings.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import sessionmaker
from ..models import AuditEvent, JoinCode, Session, User, utcnow
from ..rds import redis
from . import permissions, presence, sessions
from . import settings as settings_svc
from .events import SYSTEM, emit

log = logging.getLogger("connect.lifecycle")


async def _absent_for(key: str, present: bool, now: int) -> int:
    """Seconds this party has been absent (0 if present)."""
    r = redis()
    if present:
        await r.delete(key)
        return 0
    await r.set(key, now, nx=True, ex=7 * 86400)
    since = await r.get(key)
    return now - int(since) if since else 0


async def tech_present(db: AsyncSession, s: Session) -> bool:
    """At least one tech who still has access has the session visible."""
    for uid in await presence.techs_present(str(s.id)):
        u = await db.get(User, uid)
        if u and await permissions.can_view(db, u, s):
            return True
    return False


async def sweep() -> dict:
    """One pass. Returns counts for logs and tests."""
    ended = {"code_expired": 0, "tech_absent_timeout": 0, "customer_lost_timeout": 0}
    async with sessionmaker()() as db:
        st = await settings_svc.load(db, fresh=True)
        t = st["timeouts"]
        now_dt = utcnow()
        now = int(time.time())

        # Codes that expired unused end their pending session.
        expired = (await db.execute(
            select(JoinCode, Session).join(Session, Session.id == JoinCode.session_id)
            .where(JoinCode.claimed_at.is_(None), JoinCode.revoked.is_(False), JoinCode.expires_at <= now_dt)
        )).all()
        for jc, s in expired:
            jc.revoked = True
            await emit(db, "code.expired", SYSTEM, s.id, {"code": jc.code}, commit=False)
            if s.status == "pending":
                await sessions.end(db, s, "code_expired", SYSTEM)
                ended["code_expired"] += 1
            else:
                await db.commit()

        active = (await db.scalars(select(Session).where(Session.status.in_(("waiting", "live"))))).unique().all()
        for s in active:
            tech_gone = await _absent_for(f"absent:tech:{s.id}", await tech_present(db, s), now)
            cust_gone = await _absent_for(f"absent:customer:{s.id}", await presence.customer_present(str(s.id)), now)
            if tech_gone >= t["tech_absent_seconds"]:
                await sessions.end(db, s, "tech_absent_timeout", SYSTEM, data={"absent_seconds": tech_gone})
                ended["tech_absent_timeout"] += 1
            elif cust_gone >= t["customer_lost_seconds"]:
                await sessions.end(db, s, "customer_lost_timeout", SYSTEM, data={"absent_seconds": cust_gone})
                ended["customer_lost_timeout"] += 1
    if any(ended.values()):
        log.info("sweep ended sessions: %s", ended)
    return ended


async def retention() -> dict:
    """Delete audit events and ended sessions older than the retention settings."""
    async with sessionmaker()() as db:
        st = await settings_svc.load(db, fresh=True)
        r = st["retention"]
        audit_cutoff = utcnow() - timedelta(days=int(r["audit_days"]))
        session_cutoff = utcnow() - timedelta(days=int(r["ended_session_days"]))
        a = await db.execute(delete(AuditEvent).where(AuditEvent.ts < audit_cutoff))
        s = await db.execute(delete(Session).where(Session.status == "ended", Session.ended_at < session_cutoff))
        await db.commit()
        out = {"audit_events": a.rowcount or 0, "sessions": s.rowcount or 0}
    if any(out.values()):
        log.info("retention deleted %s", out)
    return out
