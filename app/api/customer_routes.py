"""Customer pages: choose, enter code and details, share (publisher)."""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db import get_db
from ..models import Session
from ..services import codes, customer_auth, detection, instructions, sessions, signal_tokens, vdo
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from ..web import client_ip, redirect, render

router = APIRouter()
CSRF_COOKIE = "__Host-connect-ccsrf"
BAD_CODE = ("That code didn't work. Check the four letters with your technician. "
            "Codes can only be used once and expire after 10 minutes.")


def _set_csrf(response, token: str) -> None:
    response.set_cookie(CSRF_COOKIE, token, max_age=3600, path="/", secure=True, httponly=True, samesite="strict")


def _check_csrf(request: Request, token: str) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin and urlsplit(origin).netloc != get_config().public_host:
        raise HTTPException(403, "Please reload the page and try again.")
    if not cookie or not token or not secrets.compare_digest(cookie, token):
        raise HTTPException(403, "Please reload the page and try again.")


async def _entry(request: Request, db: AsyncSession, share_type: str | None, code: str = "", error: str | None = None,
                 form: dict | None = None, status: int = 200):
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    st = await settings_svc.load(db)
    resp = await render(request, db, "customer/entry.html", {
        "share_type": share_type, "code": code, "error": error, "form": form or {}, "ccsrf": token,
        "code_minutes": st["timeouts"]["code_ttl_seconds"] // 60}, status=status)
    _set_csrf(resp, token)
    return resp


@router.get("/")
async def choose(request: Request, db: AsyncSession = Depends(get_db)):
    if await customer_auth.from_request(request):
        return redirect("/s")
    return await render(request, db, "customer/choose.html", {})


@router.get("/screen")
async def screen_entry(request: Request, db: AsyncSession = Depends(get_db)):
    return await _entry(request, db, "screen")


@router.get("/camera")
async def camera_entry(request: Request, db: AsyncSession = Depends(get_db)):
    return await _entry(request, db, "camera")


@router.get("/join/{code}")
async def join_link(request: Request, code: str, db: AsyncSession = Depends(get_db)):
    norm = codes.normalise(code) or ""
    return await _entry(request, db, None, code=norm)


@router.post("/start")
async def start(request: Request, ccsrf: str = Form(""), code: list[str] = Form([]), share_type: str = Form(""),
                name: str = Form(""), company: str = Form(""), ticket: str = Form(""),
                db: AsyncSession = Depends(get_db)):
    _check_csrf(request, ccsrf)
    code = "".join(code)[:12]  # four single-letter boxes, or one box without JavaScript
    ip = client_ip(request) or "0.0.0.0"
    st = await settings_svc.load(db)
    lim = st["limits"]
    form = {"name": name, "company": company, "ticket": ticket}
    share = share_type if share_type in ("screen", "camera") else None
    name, company, ticket = name.strip()[:120], company.strip()[:160], ticket.strip()[:60]
    if not share:
        return await _entry(request, db, None, code, "Choose whether to share your screen or your camera.", form, 400)
    if not name or not company:
        return await _entry(request, db, share, code, "Please enter your name and company.", form, 400)

    if await sessions.ip_blocked(ip):
        await emit(db, "code.failed_attempt", Actor("customer"), data={"reason": "ip_blocked"}, ip=ip)
        return await _entry(request, db, share, "", BAD_CODE, form, 400)

    res = await codes.claim(db, code, ip, lim)
    if res.locked_seconds:
        return await _entry(request, db, share, "", f"Too many attempts. Please wait {max(1, res.locked_seconds // 60)} "
                            "minutes, then try again.", form, 429)
    if not res.ok:
        await db.rollback()
        n, locked_now, alert = await codes.record_failure(ip, lim)
        await emit(db, "code.failed_attempt", Actor("customer"), data={"attempt": n}, ip=ip)
        if locked_now:
            await emit(db, "code.ip_locked", Actor("system"), data={"seconds": lim["code_lockout_seconds"]}, ip=ip)
        if alert:
            await emit(db, "code.bruteforce_alert", Actor("system"),
                       data={"failures_last_hour": await codes.bruteforce_banner()}, ip=ip)
            from ..ws.hub import publish
            await publish("ch:admins", {"type": "alert.bruteforce", "notify": {
                "title": "Possible join code guessing", "body": "Many wrong codes in the last hour. Check the audit log.",
                "sticky": True}})
        status = 429 if locked_now else 400
        msg = (f"Too many attempts. Please wait {lim['code_lockout_seconds'] // 60} minutes, then try again."
               if locked_now else BAD_CODE)
        return await _entry(request, db, share, "", msg, form, status)

    device = detection.detect(request.headers.get("user-agent", ""))
    s = await sessions.submit_details(db, res.session_id, share_type=share, name=name, company=company,
                                      ticket=ticket, ip=ip, user_agent=request.headers.get("user-agent", ""),
                                      device=device)
    token = await customer_auth.issue(str(s.id))
    resp = redirect("/s")
    customer_auth.set_cookie(resp, token)
    resp.delete_cookie(CSRF_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return resp


async def current_customer_session(request: Request, db: AsyncSession) -> Session | None:
    sid = await customer_auth.from_request(request)
    if not sid:
        return None
    s = await db.get(Session, sessions.parse_uuid(sid))
    return s


async def publisher_frame(db: AsyncSession, s: Session, stream, settings: dict, audio_ok: bool) -> dict:
    tok = await signal_tokens.issue(str(s.id), "publisher", str(stream.id))
    turn_host = get_config().turn_host or settings["turn"]["host"]
    url = vdo.publisher_url(room=s.vdo_room, password=s.vdo_password, stream_id=stream.vdo_stream_id,
                            signal_token=tok, turn_host=turn_host, session_id=str(s.id),
                            share_type=stream.share_type, label=stream.label, audio_ok=audio_ok)
    return {"id": str(stream.id), "label": stream.label, "share_type": stream.share_type, "url": url,
            "status": stream.status}


@router.get("/s")
async def share_page(request: Request, db: AsyncSession = Depends(get_db)):
    s = await current_customer_session(request, db)
    if s is None or s.status == "ended":
        resp = await render(request, db, "customer/ended.html", {
            "message": sessions.END_MESSAGES.get(s.end_reason, "This session has ended.") if s else
            "This session has ended or the link has expired."}, status=200 if s else 404)
        customer_auth.clear_cookie(resp)
        return resp
    st = await settings_svc.load(db)
    active = sessions.active_streams(s)
    if not active:
        active = [await sessions.new_stream(db, s, s.share_type)]
    frames = [await publisher_frame(db, s, x, st, audio_ok=(i == 0)) for i, x in enumerate(active)]
    device = detection.detect(s.user_agent or "")
    data = {
        "session_id": str(s.id), "share_type": s.share_type, "frames": frames,
        "instructions": instructions.for_client(s.share_type),
        "device": {"os": device.os_key, "browser": device.browser},
        "max_screens": st["limits"]["max_screen_streams"],
        "company": st["branding"]["company_name"],
    }
    entry = instructions.lookup(s.share_type, device.os_key, device.browser)
    return await render(request, db, "customer/share.html", {
        "s": s, "page_data": json.dumps(data).replace("<", "\\u003c"), "entry": entry})


@router.post("/s/end")
async def end_from_form(request: Request, db: AsyncSession = Depends(get_db)):
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin or urlsplit(origin).netloc != get_config().public_host:
        raise HTTPException(403)
    s = await current_customer_session(request, db)
    if s is None:
        raise HTTPException(404)
    await sessions.end(db, s, "customer_ended", Actor.customer(s.id), ip=client_ip(request))
    return redirect("/s")
