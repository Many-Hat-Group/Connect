"""Shared web plumbing: templates, security headers, CSRF, auth dependencies."""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import websession
from .config import APP_DIR, get_config
from .db import get_db
from .models import User
from .services import settings as settings_svc

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
STATIC_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted((APP_DIR / "static").rglob("*")) if p.is_file())
).hexdigest()[:12]


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


# ---------------------------------------------------------------- headers

def app_csp() -> str:
    host = get_config().public_host
    return ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            f"media-src 'self' blob:; font-src 'self'; connect-src 'self' wss://{host}; "
            "frame-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'none'; object-src 'none'")


# Customer pages that frame the publisher and need camera/mic/screen capture.
CAPTURE_EXACT = {"/", "/screen", "/camera", "/s", "/start"}
CAPTURE_PREFIX = ("/join/", "/s/")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", app_csp())
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["X-Frame-Options"] = "SAMEORIGIN"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        path = request.url.path
        if path in CAPTURE_EXACT or path.startswith(CAPTURE_PREFIX):
            h["Permissions-Policy"] = ("camera=(self), microphone=(self), display-capture=(self), "
                                       "autoplay=(self), fullscreen=(self), geolocation=(), payment=(), usb=()")
        else:
            h["Permissions-Policy"] = ("camera=(), microphone=(), display-capture=(), autoplay=(self), "
                                       "fullscreen=(self), geolocation=(), payment=(), usb=()")
        if not path.startswith("/static/"):
            h.setdefault("Cache-Control", "no-store")
        return response


# ---------------------------------------------------------------- sessions

def set_session_cookie(response, s: websession.WebSession) -> None:
    cfg = get_config()
    response.set_cookie(cfg.session_cookie, s.sid, max_age=cfg.session_absolute_seconds, path="/",
                        secure=True, httponly=True, samesite="lax")


def clear_session_cookie(response) -> None:
    response.delete_cookie(get_config().session_cookie, path="/", secure=True, httponly=True, samesite="lax")


async def current_websession(request: Request) -> websession.WebSession | None:
    if hasattr(request.state, "ws"):
        return request.state.ws
    s = await websession.load(request.cookies.get(get_config().session_cookie))
    request.state.ws = s
    return s


async def ensure_websession(request: Request) -> websession.WebSession:
    """A session for CSRF on pre-login pages (stage anon)."""
    s = await current_websession(request)
    if s is None:
        s = await websession.new()
        request.state.ws = s
        request.state.set_cookie = True
    return s


# ---------------------------------------------------------------- CSRF

def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # older browsers; the token still protects us
    return urlsplit(origin).netloc == get_config().public_host


async def csrf_protect(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    s = await current_websession(request)
    token = request.headers.get("x-csrf-token")
    if not token:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            token = form.get("csrf")
    if s is None or not token or not secrets.compare_digest(str(token), s.csrf) or not _same_origin(request):
        raise HTTPException(403, "CSRF check failed. Reload the page and try again.")


# ---------------------------------------------------------------- users

async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    s = await current_websession(request)
    if not s or not s.is_full:
        return None
    user = await db.get(User, s.user_id)
    if user is None or not user.is_active:
        await websession.destroy(s)
        return None
    request.state.user = user
    return user


async def require_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    s = await current_websession(request)
    if s is None or s.user_id is None:
        raise Redirect("/login")
    if s.stage == "enrol":
        raise Redirect("/mfa/enrol")
    if s.stage == "mfa":
        raise Redirect("/login/mfa")
    user = await optional_user(request, db)
    if user is None:
        raise Redirect("/login")
    if user.must_change_password and request.url.path not in ("/account/password", "/logout"):
        raise Redirect("/account/password")
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(404)
    return user


# ---------------------------------------------------------------- rendering

async def render(request: Request, db: AsyncSession, name: str, ctx: dict[str, Any] | None = None,
                 status: int = 200) -> HTMLResponse:
    st = await settings_svc.load(db)
    s = await current_websession(request)
    context = {
        "request": request,
        "brand": st["branding"],
        "urls": st["urls"],
        "settings": st,
        "csrf": s.csrf if s else "",
        "user": getattr(request.state, "user", None),
        "static_v": STATIC_VERSION,
        "path": request.url.path,
    }
    context.update(ctx or {})
    response = templates.TemplateResponse(request, name, context, status_code=status)
    if getattr(request.state, "set_cookie", False) and s:
        set_session_cookie(response, s)
    return response


def redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status)
