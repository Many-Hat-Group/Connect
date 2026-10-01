"""Connect FastAPI application."""
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from . import db, rds
from .services import signal_tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await rds.close()
    await db.dispose()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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

    return app


app = create_app()
