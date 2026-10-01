"""Admin: branding and general settings (playbook Phase 8)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..services import branding
from ..services import settings as settings_svc
from ..services.events import Actor, emit
from ..web import client_ip, csrf_protect, redirect, render, require_admin

router = APIRouter(prefix="/admin")

# (section, key, label, min, max, unit)
NUMBER_FIELDS = [
    ("timeouts", "code_ttl_seconds", "Join code lifetime", 60, 3600, "seconds"),
    ("timeouts", "tech_absent_seconds", "End the session when no tech is present for", 30, 7200, "seconds"),
    ("timeouts", "customer_lost_seconds", "End the session when the customer is gone for", 30, 3600, "seconds"),
    ("timeouts", "tech_absent_warning_seconds", "Warn the customer this long before a tech-absent timeout", 10, 600, "seconds"),
    ("timeouts", "presence_ttl_seconds", "Presence lasts (without a heartbeat)", 15, 120, "seconds"),
    ("limits", "code_fail_per_ip", "Wrong codes allowed per IP", 2, 50, "attempts"),
    ("limits", "code_fail_window_seconds", "Wrong-code counting window", 60, 86400, "seconds"),
    ("limits", "code_lockout_seconds", "Wrong-code lockout", 60, 86400, "seconds"),
    ("limits", "code_fail_global_per_hour", "Admin alert after this many wrong codes per hour (all IPs)", 5, 10000, "attempts"),
    ("limits", "login_fail_per_ip", "Failed sign-ins allowed per IP", 3, 100, "attempts"),
    ("limits", "login_fail_per_account", "Failed sign-ins allowed per account", 3, 50, "attempts"),
    ("limits", "login_window_seconds", "Sign-in counting window", 60, 86400, "seconds"),
    ("limits", "login_lockout_seconds", "Sign-in lockout", 60, 86400, "seconds"),
    ("limits", "viewers_per_stream", "Viewers per stream (thumbnails count)", 1, 10, "viewers"),
    ("limits", "max_screen_streams", "Screens a customer can share at once", 1, 4, "screens"),
    ("limits", "kill_block_seconds", "IP block after Kill", 60, 86400, "seconds"),
    ("retention", "audit_days", "Keep audit events for", 30, 3650, "days"),
    ("retention", "ended_session_days", "Keep ended session records for", 30, 3650, "days"),
    ("rustdesk", "sync_interval_hours", "Check for RustDesk updates every", 1, 168, "hours"),
]


@router.get("/settings")
async def settings_page(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db, fresh=True)
    return await render(request, db, "admin/settings.html", {"st": st, "fields": NUMBER_FIELDS, "errors": {},
                                                             "saved": request.query_params.get("saved")})


@router.post("/settings", dependencies=[Depends(csrf_protect)])
async def settings_save(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    st = await settings_svc.load(db, fresh=True)
    changes: dict[str, dict] = {}
    errors = {}
    for section, key, label, lo, hi, unit in NUMBER_FIELDS:
        raw = str(form.get(f"{section}.{key}", "")).strip()
        try:
            v = int(raw)
            if not lo <= v <= hi:
                raise ValueError
        except ValueError:
            errors[f"{section}.{key}"] = f"{label}: enter a whole number from {lo} to {hi}."
            continue
        if v != st[section].get(key):
            changes.setdefault(section, {})[key] = v
    try:
        turn_host = branding.clean_host(str(form.get("turn.host", "")), allow_empty=False)
        if turn_host != st["turn"]["host"]:
            changes.setdefault("turn", {})["host"] = turn_host
        key = str(form.get("rustdesk.key", "")).strip()
        if len(key) > 200 or any(c.isspace() for c in key):
            raise branding.BrandingError("The RustDesk key looks wrong. Paste the public key exactly.")
        for field, value in (("host", branding.clean_host(str(form.get("rustdesk.host", "")))),
                             ("relay", branding.clean_host(str(form.get("rustdesk.relay", "")))), ("key", key)):
            if value != st["rustdesk"].get(field):
                changes.setdefault("rustdesk", {})[field] = value
    except branding.BrandingError as e:
        errors["hosts"] = str(e)
    t = {**st["timeouts"], **changes.get("timeouts", {})}
    if t["tech_absent_warning_seconds"] >= t["tech_absent_seconds"]:
        errors["timeouts.tech_absent_warning_seconds"] = "The warning must start before the timeout."
    if errors:
        return await render(request, db, "admin/settings.html", {"st": st, "fields": NUMBER_FIELDS, "errors": errors,
                                                                 "form": form, "saved": None}, status=400)
    for section, c in changes.items():
        diff = await settings_svc.update(db, section, c, admin.id)
        await emit(db, "settings.changed", Actor.user(admin), data={"section": section, "changes": diff},
                   ip=client_ip(request))
    return redirect("/admin/settings?saved=1")


BRAND_TEXT = ["product_name", "company_name", "company_short", "support_phone", "support_email", "footer_text"]


@router.get("/branding")
async def branding_page(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_svc.load(db, fresh=True)
    b = st["branding"]
    return await render(request, db, "admin/branding.html", {
        "b": b, "u": st["urls"], "error": None, "warnings": branding.contrast_warnings(b["primary_colour"], b["accent_colour"]),
        "saved": request.query_params.get("saved")})


@router.post("/branding", dependencies=[Depends(csrf_protect)])
async def branding_save(request: Request, logo: UploadFile | None = File(None), favicon: UploadFile | None = File(None),
                        admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    form = await request.form()
    st = await settings_svc.load(db, fresh=True)
    b, u = dict(st["branding"]), dict(st["urls"])
    try:
        for f in BRAND_TEXT:
            b[f] = branding.clean_text(f, str(form.get(f, "")))
        if not b["product_name"] or not b["company_name"]:
            raise branding.BrandingError("Product name and company name are required.")
        b["primary_colour"] = branding.clean_colour(str(form.get("primary_colour", "")))
        b["accent_colour"] = branding.clean_colour(str(form.get("accent_colour", "")))
        u["connect_url"] = branding.clean_url(str(form.get("connect_url", "")), allow_empty=False).rstrip("/")
        u["help_url"] = branding.clean_url(str(form.get("help_url", "")))
        u["privacy_url"] = branding.clean_url(str(form.get("privacy_url", "")))
        if logo is not None and logo.filename:
            b["logo_url"] = branding.store_upload(await logo.read(branding.MAX_UPLOAD + 1), "logo")
        elif form.get("remove_logo"):
            b["logo_url"] = ""
        if favicon is not None and favicon.filename:
            b["favicon_url"] = branding.store_upload(await favicon.read(branding.MAX_UPLOAD + 1), "favicon")
        elif form.get("remove_favicon"):
            b["favicon_url"] = ""
    except branding.BrandingError as e:
        return await render(request, db, "admin/branding.html", {"b": b, "u": u, "error": str(e), "warnings": [],
                                                                 "saved": None}, status=400)
    for section, new in (("branding", b), ("urls", u)):
        diff = await settings_svc.update(db, section, new, admin.id)
        if diff:
            await emit(db, "settings.changed", Actor.user(admin), data={"section": section, "changes": diff},
                       ip=client_ip(request))
    return redirect("/admin/branding?saved=1")
