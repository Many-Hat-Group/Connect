"""Session lifecycle (playbook Section 6): create, claim, streams, end."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import JoinCode, Session, SessionAccess, SessionStream, User, utcnow
from ..rds import redis
from ..ws.hub import publish
from . import codes, customer_auth, presence, signal_tokens, vdo
from .detection import Device
from .events import SYSTEM, Actor, emit

log = logging.getLogger("connect.sessions")

END_REASONS = {"customer_ended", "tech_ended", "tech_killed", "admin_ended", "tech_absent_timeout",
               "customer_lost_timeout", "code_expired"}
END_MESSAGES = {
    "customer_ended": "You ended the session.",
    "tech_ended": "Your technician has ended the session. Thanks for your time.",
    "tech_killed": "This session has been closed.",
    "admin_ended": "This session has been closed by the support team.",
    "tech_absent_timeout": "The session ended because your technician wasn't connected.",
    "customer_lost_timeout": "The session ended because the connection was lost.",
    "code_expired": "This code has expired.",
}


# ---------------------------------------------------------------- create / claim

async def create(db: AsyncSession, user: User, settings: dict, ip: str | None = None) -> tuple[Session, JoinCode]:
    s = Session(status="pending", owner_id=user.id, visibility="private",
                vdo_room=vdo.new_room(), vdo_password=vdo.new_password())
    db.add(s)
    await db.flush()
    jc = await codes.issue(db, s.id, user.id, settings["timeouts"]["code_ttl_seconds"])
    await emit(db, "code.issued", Actor.user(user), s.id, {"code": jc.code, "expires_at": jc.expires_at.isoformat()},
               ip=ip, commit=False)
    await db.commit()
    await notify(db, s, {"type": "session.update"})
    return s, jc


async def submit_details(db: AsyncSession, session_id, *, share_type: str, name: str, company: str,
                         ticket: str, ip: str | None, user_agent: str, device: Device) -> Session:
    s = await db.get(Session, session_id)
    s.status = "waiting"
    s.share_type = share_type
    s.customer_name, s.customer_company, s.ticket_number = name, company, ticket or None
    s.customer_ip, s.user_agent = ip, (user_agent or "")[:1000]
    apply_device(s, device)
    actor = Actor.customer(s.id)
    await emit(db, "code.claimed", actor, s.id, {"ip": ip}, ip=ip, commit=False)
    await emit(db, "session.details_submitted", actor, s.id, {
        "name": name, "company": company, "ticket": ticket or None, "share_type": share_type,
        "os": s.os, "browser": s.browser, "device_class": s.device_class}, ip=ip, commit=False)
    await db.commit()
    await notify(db, s, {"type": "session.update"}, toast={
        "title": f"{name} ({company}) has joined",
        "body": f"Code accepted. Waiting for them to share their {share_type}.", "session": str(s.id)})
    return s


def apply_device(s: Session, d: Device) -> None:
    s.os, s.os_version = d.os, (d.os_version or "")[:40]
    s.browser, s.browser_version = d.browser, (d.browser_version or "")[:40]
    s.device_class = d.device_class


# ---------------------------------------------------------------- streams

def stream_label(s: Session, share_type: str) -> str:
    if share_type == "camera":
        return "Camera"
    in_use = {st.label for st in s.streams if st.status in ("starting", "live")}
    n = 1
    while f"Screen {n}" in in_use:
        n += 1
    return f"Screen {n}"


async def new_stream(db: AsyncSession, s: Session, share_type: str) -> SessionStream:
    st = SessionStream(session_id=s.id, vdo_stream_id=vdo.new_stream_id(), label=stream_label(s, share_type),
                       share_type=share_type, status="starting")
    db.add(st)
    await db.commit()
    await db.refresh(s, ["streams"])
    return st


def active_streams(s: Session) -> list[SessionStream]:
    return [st for st in s.streams if st.status in ("starting", "live")]


async def stream_live(db: AsyncSession, s: Session, st: SessionStream, audio: bool | None) -> None:
    if st.status == "live":
        if audio is not None and st.audio_available is None:
            await set_audio(db, s, st, audio)
        return
    st.status = "live"
    st.started_at = utcnow()
    if audio is not None:
        st.audio_available = audio
    first_live = s.status != "live"
    if first_live:
        s.status = "live"
        s.started_at = s.started_at or utcnow()
    if audio is not None and st == primary_stream(s):
        s.audio_available = audio
    await emit(db, "session.stream_started", Actor.customer(s.id), s.id,
               {"stream": st.label, "share_type": st.share_type, "audio": audio}, commit=False)
    await db.commit()
    await notify(db, s, {"type": "session.update"}, toast={
        "title": f"{s.customer_name} is sharing their {st.share_type}" + (f" ({st.label})" if st.label != "Camera" else ""),
        "body": f"{s.customer_company}" + (f", ticket {s.ticket_number}" if s.ticket_number else ""),
        "session": str(s.id)} if first_live or st.label != "Screen 1" else None)


def primary_stream(s: Session) -> SessionStream | None:
    """Audio is only ever taken from the first stream (avoids echo)."""
    return s.streams[0] if s.streams else None


async def set_audio(db: AsyncSession, s: Session, st: SessionStream, audio: bool) -> None:
    st.audio_available = audio
    if st == primary_stream(s):
        s.audio_available = audio
    await db.commit()
    await notify(db, s, {"type": "session.update"})


async def stream_stopped(db: AsyncSession, s: Session, st: SessionStream, why: str = "customer") -> None:
    if st.status == "stopped":
        return
    st.status = "stopped"
    st.stopped_at = utcnow()
    await emit(db, "session.stream_stopped", Actor.customer(s.id) if why == "customer" else SYSTEM, s.id,
               {"stream": st.label, "why": why}, commit=False)
    await db.commit()
    await redis().delete(f"viewers:{st.id}")
    await notify(db, s, {"type": "session.update"})


# ---------------------------------------------------------------- end

async def end(db: AsyncSession, s: Session, reason: str, actor: Actor, ip: str | None = None,
              block_ip_seconds: int = 0, data: dict | None = None) -> bool:
    """End a session and revoke everything it issued. Idempotent."""
    assert reason in END_REASONS, reason
    if s.status == "ended":
        return False
    if block_ip_seconds and s.customer_ip:
        # Before any database change, so a Redis failure cannot half-end a session.
        await redis().set(f"block:ip:{s.customer_ip}", str(s.id), ex=block_ip_seconds)
    s.status = "ended"
    s.ended_at = utcnow()
    s.end_reason = reason
    for st in s.streams:
        if st.status != "stopped":
            st.status = "stopped"
            st.stopped_at = s.ended_at
    for jc in (await db.scalars(select(JoinCode).where(JoinCode.session_id == s.id,
                                                       JoinCode.claimed_at.is_(None)))).all():
        jc.revoked = True
    payload = {"reason": reason, **(data or {})}
    await emit(db, "session.ended", actor, s.id, payload, ip=ip, commit=False)
    if block_ip_seconds and s.customer_ip:
        await emit(db, "session.ip_blocked", actor, s.id, {"ip": str(s.customer_ip), "seconds": block_ip_seconds},
                   ip=ip, commit=False)
    await db.commit()
    # Tell the customer page first so it tears down capture, then revoke.
    await publish(f"ch:customer:{s.id}", {"type": "ended", "reason": reason, "message": END_MESSAGES[reason]})
    await signal_tokens.revoke_session(str(s.id))
    await customer_auth.revoke_session(str(s.id))
    await presence.clear(str(s.id))
    r = redis()
    for st in s.streams:
        await r.delete(f"viewers:{st.id}")
    await notify(db, s, {"type": "session.update"})
    return True


async def ip_blocked(ip: str | None) -> bool:
    return bool(ip) and bool(await redis().exists(f"block:ip:{ip}"))


# ---------------------------------------------------------------- notifications

async def recipients(db: AsyncSession, s: Session) -> set[str]:
    """Every active user who may view this session."""
    q = select(User.id).where(User.is_active.is_(True))
    if s.visibility == "all_techs":
        rows = await db.scalars(q)
    else:
        accepted = select(SessionAccess.user_id).where(SessionAccess.session_id == s.id,
                                                       SessionAccess.status == "accepted")
        rows = await db.scalars(q.where(or_(User.role == "admin", User.id == s.owner_id, User.id.in_(accepted))))
    return {str(r) for r in rows}


async def notify(db: AsyncSession, s: Session, message: dict, toast: dict | None = None,
                 only: set[str] | None = None) -> None:
    msg = dict(message, session_id=str(s.id))
    if toast:
        msg["notify"] = toast
    for uid in (only or await recipients(db, s)):
        await publish(f"ch:user:{uid}", msg)


def summary(s: Session, viewers: dict[str, list[dict]] | None = None) -> dict:
    viewers = viewers or {}
    return {
        "id": str(s.id), "status": s.status, "share_type": s.share_type, "visibility": s.visibility,
        "owner_id": str(s.owner_id), "owner_name": s.owner.display_name if s.owner else None,
        "customer_name": s.customer_name, "customer_company": s.customer_company, "ticket": s.ticket_number,
        "os": s.os, "os_version": s.os_version, "browser": s.browser, "device_class": s.device_class,
        "audio_available": s.audio_available,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None, "end_reason": s.end_reason,
        "streams": [{"id": str(st.id), "label": st.label, "share_type": st.share_type, "status": st.status,
                     "audio": st.audio_available, "viewers": viewers.get(str(st.id), [])}
                    for st in s.streams if st.status != "stopped"],
    }


def parse_uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None
