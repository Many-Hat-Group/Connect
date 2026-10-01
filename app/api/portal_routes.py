"""Portal pages (dashboard is filled in by Phase 4) and brand assets."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..services import branding, permissions
from ..services import settings as settings_svc
from ..web import redirect, render, require_user

router = APIRouter()


@router.get("/portal")
async def dashboard(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await render(request, db, "portal/dashboard.html", {"welcome": request.query_params.get("welcome")})


@router.get("/portal/session/{session_id}")
async def session_page(request: Request, session_id: str, user: User = Depends(require_user),
                       db: AsyncSession = Depends(get_db)):
    s = await permissions.load_session_for(db, user, session_id)
    return await render(request, db, "portal/session.html", {"session_id": str(s.id)})


@router.get("/brand.css")
async def brand_css(request: Request, db: AsyncSession = Depends(get_db)):
    b = (await settings_svc.load(db))["branding"]
    primary = b["primary_colour"] if branding.HEX.match(b["primary_colour"]) else "#1f4e79"
    accent = b["accent_colour"] if branding.HEX.match(b["accent_colour"]) else "#f0a020"
    css = (":root{"
           f"--brand-primary:{primary};--brand-primary-text:{branding.text_on(primary)};"
           f"--brand-accent:{accent};--brand-accent-text:{branding.text_on(accent)};"
           "}\n")
    etag = '"' + hashlib.sha256(css.encode()).hexdigest()[:16] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(css, media_type="text/css", headers={"ETag": etag, "Cache-Control": "no-cache"})


@router.get("/media/branding/{name}")
async def branding_media(name: str):
    found = branding.media_path(name)
    if not found:
        raise HTTPException(404)
    path, mtype = found
    return FileResponse(path, media_type=mtype, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Content-Disposition": "inline"})


@router.get("/favicon.ico")
async def favicon(db: AsyncSession = Depends(get_db)):
    b = (await settings_svc.load(db))["branding"]
    if b.get("favicon_url"):
        return redirect(b["favicon_url"], status=302)
    return redirect("/static/icons/favicon.svg", status=302)
