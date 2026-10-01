import asyncio
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pyotp
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
async def database():
    from app.config import get_config
    url = get_config().database_url
    admin_url = url.rsplit("/", 1)[0] + "/connect"
    eng = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with eng.connect() as c:
        await c.execute(text("DROP DATABASE IF EXISTS connect_test WITH (FORCE)"))
        await c.execute(text("CREATE DATABASE connect_test"))
    await eng.dispose()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True,
                   capture_output=True)
    yield


@pytest.fixture(autouse=True)
async def clean_state():
    """Empty every table and the Redis test DB before each test."""
    from app import db, rds
    from app.services import settings as settings_svc
    async with db.engine().begin() as c:
        await c.execute(text("TRUNCATE audit_events, session_access, session_streams, join_codes, sessions, "
                             "users, settings, rustdesk_releases RESTART IDENTITY CASCADE"))
    await rds.redis().flushdb()
    settings_svc.invalidate()
    async with db.sessionmaker()() as s:
        await settings_svc.seed(s)
    yield


@pytest.fixture
async def app():
    from app.main import create_app
    from app.ws.hub import hub
    a = create_app()
    hub.start()
    yield a
    await hub.stop()


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, client=("203.0.113.50", 4000))
    async with httpx.AsyncClient(transport=transport, base_url="https://connect.test",
                                 headers={"Origin": "https://connect.test"}) as c:
        yield c


def make_client(app, ip="203.0.113.50"):
    transport = httpx.ASGITransport(app=app, client=(ip, 4000))
    return httpx.AsyncClient(transport=transport, base_url="https://connect.test",
                             headers={"Origin": "https://connect.test"})


CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


async def csrf_of(client, path="/login") -> str:
    r = await client.get(path)
    m = CSRF_RE.search(r.text)
    assert m, f"no csrf on {path}: {r.status_code}"
    return m.group(1)


async def create_user(email="tech@example.com", name="Tess Tech", role="tech", password="correct horse battery",
                      enrolled=True, must_change=False):
    from app import db
    from app.auth import totp
    from app.auth.passwords import hash_password
    from app.models import User, utcnow
    secret = totp.new_secret()
    async with db.sessionmaker()() as s:
        u = User(email=email, display_name=name, role=role, password_hash=hash_password(password),
                 must_change_password=must_change)
        if enrolled:
            u.totp_secret = totp.encrypt_secret(secret)
            u.totp_enrolled_at = utcnow()
            u.recovery_codes = totp.hash_recovery_codes(["aaaaa-bbbbb"])
        s.add(u)
        await s.commit()
        await s.refresh(u)
    u.plain_secret = secret
    u.plain_password = password
    return u


def current_code(secret: str, offset_steps: int = 0) -> str:
    import time
    t = pyotp.TOTP(secret)
    return t.at(int(time.time()) + offset_steps * 30)


async def login(client, user, code=None):
    token = await csrf_of(client, "/login")
    r = await client.post("/login", data={"csrf": token, "email": user.email, "password": user.plain_password})
    assert r.status_code == 303, r.text[:500]
    if r.headers["location"] == "/mfa/enrol":
        return r
    token = await csrf_of(client, "/login/mfa")
    r = await client.post("/login/mfa", data={"csrf": token, "code": code or current_code(user.plain_secret)})
    assert r.status_code == 303 and r.headers["location"] == "/portal", r.text[:300]
    return r


async def portal_csrf(client) -> str:
    return await csrf_of(client, "/account")


async def audit_types():
    from app import db
    from sqlalchemy import select
    from app.models import AuditEvent
    async with db.sessionmaker()() as s:
        return [e.event_type for e in (await s.scalars(select(AuditEvent).order_by(AuditEvent.id))).all()]
