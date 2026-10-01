"""Connect FastAPI application."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import db, rds
from .api import admin_health, admin_settings, licences, rustdesk_routes, admin_audit, admin_sessions, admin_users, auth_routes, customer_routes, portal_routes, setup_routes, tech_api
from .config import APP_DIR
from .services import settings as settings_svc
from .services import signal_tokens
from .web import Redirect, SecurityHeaders, redirect, render
from .ws import customer as customer_ws
from .ws import portal as portal_ws
from .ws.hub import hub

log = logging.getLogger("connect")


async def _settings_listener():
    """Invalidate the per-process settings cache when any process writes."""
    while True:
        try:
            ps = rds.redis().pubsub()
            await ps.subscribe("settings:invalidate")
            async for msg in ps.listen():
                if msg.get("type") == "message":
                    settings_svc.invalidate()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with db.sessionmaker()() as s:
        await settings_svc.seed(s)
    hub.start()
    listener = asyncio.create_task(_settings_listener())
    yield
    listener.cancel()
    await hub.stop()
    await rds.close()
    await db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeaders)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    @app.exception_handler(Redirect)
    async def _redirect(request: Request, exc: Redirect):
        return redirect(exc.url)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith(("/api/", "/internal/", "/dl/")) or "application/json" in request.headers.get("accept", ""):
            return Response(status_code=exc.status_code)
        try:
            async with db.sessionmaker()() as s:
                return await render(request, s, "error.html", {"status": exc.status_code, "detail": exc.detail},
                                    status=exc.status_code)
        except Exception:
            return PlainTextResponse(str(exc.status_code), status_code=exc.status_code)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        async with db.engine().connect() as c:
            await c.execute(text("select 1"))
        await rds.redis().ping()
        return PlainTextResponse("ok\n")

    @app.get("/internal/signal-auth", include_in_schema=False)
    async def signal_auth(request: Request):
        original = request.headers.get("x-original-uri", "")
        token = parse_qs(urlsplit(original).query).get("t", [""])[0]
        ok = await signal_tokens.validate(token)
        return Response(status_code=204 if ok else 403)

    for r in (auth_routes.router, setup_routes.router, admin_users.router, admin_sessions.router, admin_audit.router,
              admin_settings.router, admin_health.router, licences.router, portal_routes.router, tech_api.router,
              customer_routes.router, rustdesk_routes.router, portal_ws.router, customer_ws.router):
        app.include_router(r)
    return app


app = create_app()
