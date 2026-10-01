"""Helpers for e2e tests against the running e2e stack (tests/e2e/stack.sh)."""
import re
import subprocess
import time
from pathlib import Path

import pyotp

ROOT = Path(__file__).resolve().parents[2]
STACK = [str(ROOT / "tests/e2e/stack.sh")]


def sh(*args, input=None) -> str:
    r = subprocess.run(STACK + list(args), capture_output=True, text=True, input=input)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout)
    return r.stdout


def psql(sql: str) -> str:
    return sh("exec", "-T", "postgres", "psql", "-U", "connect", "-d", "connect", "-tAc", sql)


def reset():
    psql("TRUNCATE audit_events, session_access, session_streams, join_codes, sessions, users, "
         "settings, rustdesk_releases RESTART IDENTITY CASCADE")
    sh("exec", "-T", "redis", "redis-cli", "-a", "e2e-redis", "--no-auth-warning", "FLUSHDB")
    sh("exec", "-T", "app", "python", "-c",
       "import asyncio\nfrom app import db\nfrom app.services import settings as s\n"
       "async def m():\n    async with db.sessionmaker()() as x: await s.seed(x)\nasyncio.run(m())")
    # Make all app processes drop cached settings.
    sh("exec", "-T", "redis", "redis-cli", "-a", "e2e-redis", "--no-auth-warning", "PUBLISH",
       "settings:invalidate", "all")


def totp_now(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def totp_fresh(secret: str, last: dict) -> str:
    """A code not used before by this test (waits for the next step if needed)."""
    t = pyotp.TOTP(secret)
    while True:
        step = int(time.time()) // 30
        if last.get(secret) != step:
            last[secret] = step
            return t.at(step * 30)
        time.sleep(1)


def create_user(email, name, role="tech", password="correct horse battery") -> dict:
    """Create an enrolled user inside the app container; returns its TOTP secret."""
    code = (
        "import asyncio\nfrom app import db\nfrom app.auth import totp\nfrom app.auth.passwords import hash_password\n"
        "from app.models import User, utcnow\n"
        "async def m():\n"
        "    sec = totp.new_secret()\n"
        "    async with db.sessionmaker()() as s:\n"
        f"        u = User(email={email!r}, display_name={name!r}, role={role!r}, password_hash=hash_password({password!r}),"
        " totp_secret=totp.encrypt_secret(sec), totp_enrolled_at=utcnow(), recovery_codes=[])\n"
        "        s.add(u); await s.commit(); print(u.id, sec)\n"
        "asyncio.run(m())\n")
    out = sh("exec", "-T", "app", "python", "-c", code).strip().split()
    return {"id": out[0], "secret": out[1], "email": email, "password": password, "name": name}


def login(page, base_url, user, used: dict):
    page.goto(base_url + "/login")
    page.fill("input[name=email]", user["email"])
    page.fill("input[name=password]", user["password"])
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/login/mfa$"))
    page.fill("input[name=code]", totp_fresh(user["secret"], used))
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/portal.*"))
