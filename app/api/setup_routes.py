"""First-run wizard. Step 1 needs the one-time setup token printed by
install.sh and is permanently disabled once any admin exists. Later steps
are only for that admin, and only until setup is marked complete."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import ratelimit, websession
from ..auth.passwords import hash_password, policy_error
from ..config import get_config
from ..db import get_db
from ..models import User
from ..services import branding
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from ..web import (
    client_ip, csrf_protect, ensure_websession, redirect, render, require_admin, set_session_cookie,
)

router = APIRouter()
STEPS = ["account", "mfa", "brand", "urls", "integrations"]


async def admin_exists(db: AsyncSession) -> bool:
    return bool(await db.scalar(select(func.count()).select_from(User).where(User.role == "admin")))


async def _check_token(request: Request, db: AsyncSession, token: str) -> None:
    if await admin_exists(db):
        raise HTTPException(404)
    ip = client_ip(request) or "unknown"
    if await ratelimit.locked(f"setup:{ip}"):
        raise HTTPException(404)
    expected = get_config().setup_token
    if not expected or not token or not secrets.compare_digest(token, expected):
        if await ratelimit.hit(f"setup:fail:{ip}", 900) >= 10:
            await ratelimit.lock(f"setup:{ip}", 900)
        raise HTTPException(404)


async def _setup_admin(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)) -> User:
    st = await settings_svc.load(db, fresh=True)
    if st["setup"].get("complete"):
        raise HTTPException(404)
    return user


@router.get("/setup")
async def setup_start(request: Request, token: str = "", db: AsyncSession = Depends(get_db)):
    await _check_token(request, db, token)
    await ensure_websession(request)
    return await render(request, db, "wizard/account.html", {"token": token, "error": None, "form": {},
                                                             "steps": STEPS, "step": "account"})


@router.post("/setup", dependencies=[Depends(csrf_protect)])
async def setup_account(request: Request, token: str = Form(""), email: str = Form(""),
                        display_name: str = Form(""), password: str = Form(""), confirm: str = Form(""),
                        db: AsyncSession = Depends(get_db)):
    await _check_token(request, db, token)
    form = {"email": email, "display_name": display_name}
    email_n = email.strip().lower()
    error = None
    if "@" not in email_n or len(email_n) > 320:
        error = "Enter a valid email address."
    elif not display_name.strip() or len(display_name) > 120:
        error = "Enter your name."
    elif password != confirm:
        error = "The passwords don't match."
    else:
        error = policy_error(password, email_n)
    if error:
        return await render(request, db, "wizard/account.html", {"token": token, "error": error, "form": form,
                                                                 "steps": STEPS, "step": "account"}, status=400)
    user = User(email=email_n, display_name=display_name.strip(), role="admin",
                password_hash=hash_password(password), auth_provider="local")
    db.add(user)
    await db.flush()
    await emit(db, "user.created", Actor.user(user), data={"user_id": str(user.id), "email": email_n,
                                                            "role": "admin", "via": "setup_wizard"},
               ip=client_ip(request))
    s = await ensure_websession(request)
    s = await websession.rotate(s, "enrol", str(user.id))
    s.data["after_recovery"] = "/setup/brand"
    await websession.save(s)
    resp = redirect("/mfa/enrol")
    set_session_cookie(resp, s)
    return resp


@router.get("/setup/brand")
async def brand_form(request: Request, user: User = Depends(_setup_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    return await render(request, db, "wizard/brand.html", {"b": st["branding"], "error": None, "warnings": [],
                                                           "steps": STEPS, "step": "brand"})


@router.post("/setup/brand", dependencies=[Depends(csrf_protect)])
async def brand_submit(request: Request, product_name: str = Form(""), company_name: str = Form(""),
                       company_short: str = Form(""), primary_colour: str = Form(""),
                       accent_colour: str = Form(""), logo: UploadFile | None = File(None),
                       user: User = Depends(_setup_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db, fresh=True)
    changes = {}
    try:
        changes["product_name"] = branding.clean_text("product_name", product_name) or st["branding"]["product_name"]
        changes["company_name"] = branding.clean_text("company_name", company_name)
        changes["company_short"] = branding.clean_text("company_short", company_short)
        changes["primary_colour"] = branding.clean_colour(primary_colour)
        changes["accent_colour"] = branding.clean_colour(accent_colour)
        if logo is not None and logo.filename:
            changes["logo_url"] = branding.store_upload(await logo.read(branding.MAX_UPLOAD + 1), "logo")
    except branding.BrandingError as e:
        b = dict(st["branding"], product_name=product_name, company_name=company_name,
                 company_short=company_short, primary_colour=primary_colour, accent_colour=accent_colour)
        return await render(request, db, "wizard/brand.html", {"b": b, "error": str(e), "warnings": [],
                                                               "steps": STEPS, "step": "brand"}, status=400)
    diff = await settings_svc.update(db, "branding", changes, user.id)
    await emit(db, "settings.changed", Actor.user(user), data={"section": "branding", "changes": diff},
               ip=client_ip(request))
    warnings = branding.contrast_warnings(changes["primary_colour"], changes["accent_colour"])
    if warnings:
        st = await settings_svc.load(db, fresh=True)
        return await render(request, db, "wizard/brand.html", {"b": st["branding"], "error": None,
                                                               "warnings": warnings, "saved": True,
                                                               "steps": STEPS, "step": "brand"})
    return redirect("/setup/urls")


@router.get("/setup/urls")
async def urls_form(request: Request, user: User = Depends(_setup_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    return await render(request, db, "wizard/urls.html", {"u": st["urls"], "error": None,
                                                          "steps": STEPS, "step": "urls"})


@router.post("/setup/urls", dependencies=[Depends(csrf_protect)])
async def urls_submit(request: Request, connect_url: str = Form(""), help_url: str = Form(""),
                      privacy_url: str = Form(""), user: User = Depends(_setup_admin),
                      db: AsyncSession = Depends(get_db)):
    try:
        changes = {"connect_url": branding.clean_url(connect_url, allow_empty=False).rstrip("/"),
                   "help_url": branding.clean_url(help_url), "privacy_url": branding.clean_url(privacy_url)}
    except branding.BrandingError as e:
        u = {"connect_url": connect_url, "help_url": help_url, "privacy_url": privacy_url}
        return await render(request, db, "wizard/urls.html", {"u": u, "error": str(e),
                                                              "steps": STEPS, "step": "urls"}, status=400)
    diff = await settings_svc.update(db, "urls", changes, user.id)
    await emit(db, "settings.changed", Actor.user(user), data={"section": "urls", "changes": diff},
               ip=client_ip(request))
    return redirect("/setup/integrations")


@router.get("/setup/integrations")
async def integrations_form(request: Request, user: User = Depends(_setup_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db)
    return await render(request, db, "wizard/integrations.html", {"t": st["turn"], "r": st["rustdesk"],
                                                                  "error": None, "steps": STEPS,
                                                                  "step": "integrations"})


@router.post("/setup/integrations", dependencies=[Depends(csrf_protect)])
async def integrations_submit(request: Request, turn_host: str = Form(""), rustdesk_host: str = Form(""),
                              rustdesk_key: str = Form(""), rustdesk_relay: str = Form(""),
                              user: User = Depends(_setup_admin), db: AsyncSession = Depends(get_db)):
    try:
        turn = {"host": branding.clean_host(turn_host, allow_empty=False)}
        key = rustdesk_key.strip()
        if len(key) > 200 or any(c.isspace() for c in key):
            raise branding.BrandingError("The RustDesk key looks wrong. Paste the public key exactly.")
        rd = {"host": branding.clean_host(rustdesk_host), "key": key, "relay": branding.clean_host(rustdesk_relay)}
    except branding.BrandingError as e:
        return await render(request, db, "wizard/integrations.html", {
            "t": {"host": turn_host}, "r": {"host": rustdesk_host, "key": rustdesk_key, "relay": rustdesk_relay},
            "error": str(e), "steps": STEPS, "step": "integrations"}, status=400)
    for section, changes in (("turn", turn), ("rustdesk", rd)):
        diff = await settings_svc.update(db, section, changes, user.id)
        await emit(db, "settings.changed", Actor.user(user), data={"section": section, "changes": diff},
                   ip=client_ip(request))
    await settings_svc.update(db, "setup", {"complete": True}, user.id)
    await emit(db, "setup.completed", Actor.user(user), ip=client_ip(request))
    return redirect("/portal?welcome=1")
