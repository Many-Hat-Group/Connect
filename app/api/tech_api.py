"""Tech JSON API (dashboard and session view). CSRF via X-CSRF-Token.
Every handler goes through services.permissions; unauthorised lookups 404."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db import get_db
from ..models import AuditEvent, JoinCode, Session, SessionAccess, User, utcnow
from ..rds import redis
from ..services import permissions, quality, sessions, signal_tokens, vdo, viewers
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from ..web import client_ip, csrf_protect, require_user
from ..ws.hub import publish

router = APIRouter(prefix="/api")


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def visible_sessions(db: AsyncSession, user: User, include_ended: bool = False) -> list[Session]:
    q = select(Session).order_by(Session.created_at.desc())
    if not include_ended:
        q = q.where(Session.status != "ended")
    if user.role != "admin":
        accepted = select(SessionAccess.session_id).where(SessionAccess.user_id == user.id,
                                                          SessionAccess.status == "accepted")
        q = q.where(or_(Session.owner_id == user.id, Session.visibility == "all_techs", Session.id.in_(accepted)))
    return list((await db.scalars(q.limit(200))).unique().all())


async def summary_with_viewers(s: Session) -> dict:
    v = await viewers.for_session([str(st.id) for st in sessions.active_streams(s)])
    return sessions.summary(s, v)


@router.post("/sessions", dependencies=[Depends(csrf_protect)])
async def create_session(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    s, jc = await sessions.create(db, user, st, ip=client_ip(request))
    base = st["urls"]["connect_url"].rstrip("/")
    return JSONResponse({"session_id": str(s.id), "code": jc.code, "expires_at": jc.expires_at.isoformat(),
                         "link": f"{base}/join/{jc.code}", "ttl": st["timeouts"]["code_ttl_seconds"]}, status_code=201)


@router.get("/dashboard")
async def dashboard(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    rows = await visible_sessions(db, user)
    pending = []
    live = []
    base = st["urls"]["connect_url"].rstrip("/")
    for s in rows:
        if s.status == "pending":
            if s.owner_id != user.id:
                continue
            jc = await db.scalar(select(JoinCode).where(JoinCode.session_id == s.id, JoinCode.claimed_at.is_(None),
                                                        JoinCode.revoked.is_(False)))
            if jc and jc.expires_at > utcnow():
                pending.append({"session_id": str(s.id), "code": jc.code, "expires_at": jc.expires_at.isoformat(),
                                "link": f"{base}/join/{jc.code}"})
        else:
            live.append(await summary_with_viewers(s))
    from ..services.codes import bruteforce_banner
    banner = await bruteforce_banner() if user.role == "admin" else None
    return {"pending": pending, "sessions": live, "me": str(user.id), "role": user.role,
            "bruteforce": banner, "viewer_cap": st["limits"]["viewers_per_stream"]}


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id)
    events = (await db.scalars(select(AuditEvent).where(AuditEvent.session_id == s.id)
                               .order_by(AuditEvent.id))).all()
    timeline = [{"ts": e.ts.isoformat(), "type": e.event_type, "actor_type": e.actor_type,
                 "actor": (e.data or {}).get("actor_name"), "data": e.data} for e in events]
    out = await summary_with_viewers(s)
    out["timeline"] = timeline
    out["customer_ip"] = str(s.customer_ip) if s.customer_ip else None
    out["user_agent"] = s.user_agent
    out["browser_version"] = s.browser_version
    out["can_admin"] = permissions.can_admin(user)
    return out


async def _stream(s: Session, stream_id: str):
    st = next((x for x in s.streams if str(x.id) == str(stream_id)), None)
    if st is None or st.status == "stopped":
        raise HTTPException(404)
    return st


async def push_customer_viewers(db: AsyncSession, s: Session) -> None:
    from ..ws.customer import viewer_payload
    await publish(f"ch:customer:{s.id}", await viewer_payload(s))


@router.post("/sessions/{session_id}/view", dependencies=[Depends(csrf_protect)])
async def open_view(request: Request, session_id: str, body: dict = Body(...), user: User = Depends(require_user),
                    db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id)
    if s.status == "ended":
        return err(410, "This session has ended.")
    stream = await _stream(s, body.get("stream_id"))
    kind = "full" if body.get("kind") == "full" else "thumb"
    st = await settings_svc.load(db)
    cap = st["limits"]["viewers_per_stream"]
    if not await viewers.add(str(stream.id), str(user.id), user.display_name, kind, cap):
        return err(409, f"This stream already has {cap} viewers. Close another view first.")
    tok = await signal_tokens.issue(str(s.id), "viewer", str(user.id))
    turn_host = get_config().turn_host or st["turn"]["host"]
    common = dict(room=s.vdo_room, password=s.vdo_password, stream_id=stream.vdo_stream_id, signal_token=tok,
                  turn_host=turn_host, session_id=str(s.id))
    if kind == "full":
        url = vdo.viewer_url(**common, scale=100, audio=stream == sessions.primary_stream(s), start_muted=True)
        await emit(db, "session.viewed", Actor.user(user), s.id, {"stream": stream.label}, ip=client_ip(request))
        if user.role == "admin" and s.owner_id != user.id and await redis().set(
                f"admin_joined:{s.id}:{user.id}", 1, nx=True, ex=86400):
            await emit(db, "session.admin_joined", Actor.user(user), s.id, {}, ip=client_ip(request))
    else:
        url = vdo.viewer_url(**common, bitrate=quality.THUMB["bitrate"], width=quality.THUMB["width"], audio=False,
                             start_muted=True)
    if kind == "full":
        # Other tabs of this tech close their thumbnail of this stream.
        await publish(f"ch:user:{user.id}", {"type": "view.full", "open": True, "stream_id": str(stream.id)})
    await push_customer_viewers(db, s)
    await sessions.notify(db, s, {"type": "session.update"})
    return {"url": url, "stream_id": str(stream.id), "kind": kind, "label": stream.label,
            "preset": quality.DEFAULT, "presets": quality.PRESETS}


@router.post("/sessions/{session_id}/view/close", dependencies=[Depends(csrf_protect)])
async def close_view(request: Request, session_id: str, body: dict = Body(...), user: User = Depends(require_user),
                     db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id)
    stream_id = str(body.get("stream_id"))
    current = {v["user_id"]: v for v in await viewers.list_for(stream_id)}
    mine = current.get(str(user.id))
    await viewers.remove(stream_id, str(user.id))
    if mine and mine["kind"] == "full" and body.get("kind") == "full":
        await emit(db, "session.view_closed", Actor.user(user), s.id, {}, ip=client_ip(request))
        await publish(f"ch:user:{user.id}", {"type": "view.full", "open": False, "stream_id": stream_id})
    elif mine and mine["kind"] != body.get("kind"):
        # A stale close for the other kind (e.g. thumbnail closing after the
        # full view replaced it): keep the current view.
        await viewers.add(stream_id, str(user.id), mine["name"], mine["kind"], 10**6)
        return {"ok": True}
    await push_customer_viewers(db, s)
    await sessions.notify(db, s, {"type": "session.update"})
    return {"ok": True}


@router.post("/sessions/{session_id}/audio", dependencies=[Depends(csrf_protect)])
async def audio(request: Request, session_id: str, body: dict = Body(...), user: User = Depends(require_user),
                db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id)
    if body.get("detected") is not None:
        # Viewer saw (or did not see) an audio track: fills audio_available
        # when the customer page could not tell.
        stream = await _stream(s, body.get("stream_id"))
        if stream.audio_available is None or (body["detected"] and not stream.audio_available):
            await sessions.set_audio(db, s, stream, bool(body["detected"]))
        return {"ok": True}
    if body.get("unmuted"):
        await emit(db, "session.audio_unmuted", Actor.user(user), s.id, {"volume": body.get("volume")},
                   ip=client_ip(request))
    return {"ok": True}


@router.post("/sessions/{session_id}/quality", dependencies=[Depends(csrf_protect)])
async def set_quality(request: Request, session_id: str, body: dict = Body(...), user: User = Depends(require_user),
                      db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id)
    preset = body.get("preset")
    if preset not in quality.PRESETS:
        return err(400, "Unknown preset.")
    stream = await _stream(s, body.get("stream_id"))
    p = quality.PRESETS[preset]
    await publish(f"ch:customer:{s.id}", {"type": "apply", "stream": str(stream.id), "fps": p["fps"]})
    await emit(db, "session.quality_changed", Actor.user(user), s.id, {"preset": preset, "stream": stream.label},
               ip=client_ip(request))
    return {"ok": True, "preset": p}


@router.post("/sessions/{session_id}/end", dependencies=[Depends(csrf_protect)])
async def end_session(request: Request, session_id: str, user: User = Depends(require_user),
                      db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id, need="manage")
    reason = "admin_ended" if user.role == "admin" and s.owner_id != user.id else "tech_ended"
    if s.status == "pending":
        reason = "tech_ended"
    await sessions.end(db, s, reason, Actor.user(user), ip=client_ip(request))
    return {"ok": True, "reason": reason}


@router.post("/sessions/{session_id}/kill", dependencies=[Depends(csrf_protect)])
async def kill_session(request: Request, session_id: str, body: dict = Body(default={}),
                       user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id, need="manage")
    st = await settings_svc.load(db)
    block = st["limits"]["kill_block_seconds"] if body.get("block_ip") else 0
    await sessions.end(db, s, "tech_killed", Actor.user(user), ip=client_ip(request), block_ip_seconds=block)
    return {"ok": True, "blocked": bool(block)}
