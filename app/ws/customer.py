"""/ws/customer: the customer's share page.

Auth: the customer session cookie. Origin must be our host.
Client messages:
  {"type": "hello", "caps": {...}}                     device refinement
  {"type": "hb", "phase": "sharing"|"instructions", "live": [stream ids]}
  {"type": "stream", "stream": id, "event": "live"|"stopped", "audio": bool|null}
  {"type": "new_stream", "share_type": "screen"|"camera"}
  {"type": "end"}
Server messages: viewers, ended, frame (new publisher frame), warning, apply.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import sessionmaker
from ..models import Session
from ..services import customer_auth, detection, presence, sessions, viewers
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from .hub import hub
from .portal import origin_ok

router = APIRouter()
log = logging.getLogger("connect.ws.customer")


async def viewer_payload(s: Session) -> dict:
    ids = [str(st.id) for st in sessions.active_streams(s)]
    by_stream = await viewers.for_session(ids)
    names = []
    for vs in by_stream.values():
        for v in vs:
            if v["name"] not in names:
                names.append(v["name"])
    return {"type": "viewers", "names": names, "by_stream": {k: [v["name"] for v in vs] for k, vs in by_stream.items()}}


async def tech_warning(s: Session, st: dict) -> dict | None:
    """Countdown shown in the last minute before a tech-absent timeout."""
    from ..rds import redis
    since = await redis().get(f"absent:tech:{s.id}")
    if not since or s.status not in ("waiting", "live"):
        return None
    import time
    left = st["timeouts"]["tech_absent_seconds"] - (int(time.time()) - int(since))
    if left <= st["timeouts"]["tech_absent_warning_seconds"]:
        return {"type": "warning", "kind": "tech_absent", "seconds_left": max(0, left)}
    return None


async def handle(ws: WebSocket, sid: str, msg: dict) -> None:
    t = msg.get("type")
    async with sessionmaker()() as db:
        s = await db.get(Session, sessions.parse_uuid(sid))
        if s is None or s.status == "ended":
            await ws.send_text(json.dumps({"type": "ended", "reason": s.end_reason if s else "gone",
                                           "message": sessions.END_MESSAGES.get(s.end_reason, "This session has ended.")
                                           if s else "This session has ended."}))
            return
        st = await settings_svc.load(db)
        if t == "hello":
            caps = msg.get("caps") or {}
            d = detection.detect(s.user_agent or "", caps.get("hints") or {}, int(caps.get("maxTouchPoints") or 0))
            sessions.apply_device(s, d)
            await db.commit()
            from ..services import rustdesk_prompt
            await rustdesk_prompt.remember_device(sid, d)
            await presence.customer_seen(sid, st["timeouts"]["presence_ttl_seconds"])
            await ws.send_text(json.dumps(await viewer_payload(s)))
            await sessions.notify(db, s, {"type": "session.update"})
        elif t == "hb":
            await presence.customer_seen(sid, st["timeouts"]["presence_ttl_seconds"])
            await ws.send_text(json.dumps(await viewer_payload(s)))
            w = await tech_warning(s, st)
            await ws.send_text(json.dumps(w or {"type": "warning", "kind": None}))
        elif t == "stream":
            stream = next((x for x in s.streams if str(x.id) == str(msg.get("stream"))), None)
            if stream is None:
                return
            audio = msg.get("audio")
            audio = bool(audio) if isinstance(audio, bool) else None
            if msg.get("event") == "live":
                await sessions.stream_live(db, s, stream, audio)
            elif msg.get("event") == "audio" and audio is not None:
                await sessions.set_audio(db, s, stream, audio)
            elif msg.get("event") == "stopped":
                await sessions.stream_stopped(db, s, stream)
        elif t == "new_stream":
            share = msg.get("share_type")
            if share not in ("screen", "camera"):
                return
            active = sessions.active_streams(s)
            screens = [x for x in active if x.share_type == "screen"]
            if share == "screen" and len(screens) >= st["limits"]["max_screen_streams"]:
                await ws.send_text(json.dumps({"type": "error", "message": "You're already sharing the maximum number of screens."}))
                return
            if share == "camera" and any(x.share_type == "camera" for x in active):
                return
            from ..api.customer_routes import publisher_frame
            stream = await sessions.new_stream(db, s, share)
            first = sessions.primary_stream(s)
            audio_ok = not any(x.share_type == "screen" and x.status in ("starting", "live") and x.id != stream.id
                               for x in s.streams) and (first is None or first.id == stream.id or first.status == "stopped")
            frame = await publisher_frame(db, s, stream, st, audio_ok=audio_ok)
            await ws.send_text(json.dumps({"type": "frame", "frame": frame}))
        elif t == "end":
            await sessions.end(db, s, "customer_ended", Actor.customer(s.id))
        elif t == "rustdesk":
            from ..services import rustdesk_prompt
            await rustdesk_prompt.customer_response(db, s, msg)


@router.websocket("/ws/customer")
async def customer_ws(ws: WebSocket):
    if not origin_ok(ws):
        await ws.close(code=4403)
        return
    sid = await customer_auth.from_request(ws)
    if not sid:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.subscribe(ws, f"ch:customer:{sid}")
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                break
            if not await customer_auth.from_request(ws):
                # Token revoked (session ended or killed).
                await ws.send_text(json.dumps({"type": "ended", "reason": "revoked",
                                               "message": "This session has ended."}))
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict):
                try:
                    await handle(ws, sid, msg)
                except Exception:
                    log.exception("customer ws message failed")
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe_all(ws)
        try:
            await ws.close()
        except Exception:
            pass
