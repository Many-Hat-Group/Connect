"""/ws/portal: the tech and admin dashboard websocket.

Auth: the portal session cookie (stage full). Origin must be our host.
Client messages:
  {"type": "hb", "visible": [session ids]}   every 10 s (tech presence)
  {"type": "ping"}
Server messages: notifications fanned out from Redis channels
ch:user:<id>, ch:techs and (admins) ch:admins.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..auth import websession
from ..config import get_config
from ..db import sessionmaker
from ..models import Session, User
from ..services import permissions, presence
from ..services import settings as settings_svc
from .hub import hub

router = APIRouter()
log = logging.getLogger("connect.ws.portal")


def origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    return bool(origin) and urlsplit(origin).netloc == get_config().public_host


async def authenticate(ws: WebSocket) -> tuple[websession.WebSession, User] | None:
    s = await websession.load(ws.cookies.get(get_config().session_cookie))
    if not s or not s.is_full:
        return None
    async with sessionmaker()() as db:
        user = await db.get(User, s.user_id)
        if not user or not user.is_active or user.must_change_password:
            return None
        db.expunge(user)
    return s, user


async def heartbeat(user: User, visible: list[str]) -> list[str]:
    """Mark the tech present on each visible session they may view."""
    ids = []
    for v in visible[:200]:
        try:
            ids.append(uuid.UUID(str(v)))
        except ValueError:
            continue
    if not ids:
        return []
    marked = []
    async with sessionmaker()() as db:
        st = await settings_svc.load(db)
        ttl = st["timeouts"]["presence_ttl_seconds"]
        rows = (await db.scalars(select(Session).where(Session.id.in_(ids), Session.status != "ended"))).all()
        for s in rows:
            u = await db.merge(user, load=False)
            if await permissions.can_view(db, u, s):
                await presence.tech_seen(str(s.id), str(user.id), ttl)
                marked.append(str(s.id))
    return marked


@router.websocket("/ws/portal")
async def portal_ws(ws: WebSocket):
    if not origin_ok(ws):
        await ws.close(code=4403)
        return
    auth = await authenticate(ws)
    if auth is None:
        await ws.close(code=4401)
        return
    wsess, user = auth
    await ws.accept()
    channels = [f"ch:user:{user.id}", "ch:techs"] + (["ch:admins"] if user.role == "admin" else [])
    hub.subscribe(ws, *channels)
    await ws.send_text(json.dumps({"type": "hello", "user": str(user.id), "role": user.role}))
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                break  # no heartbeat for 60 s
            # Re-check the web session (logout, deactivation, idle expiry).
            if await websession.load(wsess.sid, touch=False) is None:
                await ws.send_text(json.dumps({"type": "session.revoked"}))
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "hb":
                marked = await heartbeat(user, msg.get("visible") or [])
                await ws.send_text(json.dumps({"type": "hb.ack", "present": marked}))
            elif msg.get("type") == "ping":
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe_all(ws)
        try:
            await ws.close()
        except Exception:
            pass
