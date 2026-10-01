"""Phase 7: RustDesk sync against a fake GitHub (httpx MockTransport),
fail-closed hash checks, approval, pin and rollback, gated downloads."""
import hashlib
import re
import shutil

import httpx
import pytest

from conftest import audit_types, create_user, login, make_client, portal_csrf
from helpers_sessions import make_session

PLATFORM_FILES = ["x86_64.exe", "x86_64.msi", "aarch64.exe", "aarch64.dmg", "x86_64.dmg", "x86_64.deb",
                  "universal.apk", "x86-sciter.exe"]


class FakeGitHub:
    def __init__(self):
        self.releases = {}
        self.latest = None
        self.tamper = set()       # asset names whose bytes differ from the published digest
        self.no_digest = False
        self.prerelease = False

    def add(self, version):
        assets = []
        for suffix in PLATFORM_FILES:
            name = f"rustdesk-{version}-{suffix}"
            body = f"{name} binary".encode()
            assets.append({"name": name, "size": len(body), "browser_download_url": f"https://dl.example/{name}",
                           "digest": None if self.no_digest else "sha256:" + hashlib.sha256(body).hexdigest(),
                           "_body": body})
        self.releases[version] = {"tag_name": version, "prerelease": self.prerelease, "draft": False,
                                  "published_at": "2026-09-01T00:00:00Z", "assets": assets}
        self.latest = version

    def handler(self, request: httpx.Request):
        if request.url.path.endswith("/releases/latest"):
            rel = dict(self.releases[self.latest])
            rel["assets"] = [{k: v for k, v in a.items() if k != "_body"} for a in rel["assets"]]
            return httpx.Response(200, json=rel)
        name = request.url.path.rsplit("/", 1)[1]
        for rel in self.releases.values():
            for a in rel["assets"]:
                if a["name"] == name:
                    return httpx.Response(200, content=a["_body"] + (b"TAMPERED" if name in self.tamper else b""))
        return httpx.Response(404)


@pytest.fixture
def gh(tmp_path, monkeypatch):
    from app.config import get_config
    from app.services import rustdesk
    monkeypatch.setattr(get_config(), "rustdesk_dir", tmp_path / "rustdesk")
    monkeypatch.setattr(rustdesk, "authenticode", lambda p: "unsigned (test)")
    fake = FakeGitHub()
    monkeypatch.setattr(rustdesk, "transport", httpx.MockTransport(fake.handler))
    yield fake


async def state():
    from app import db
    from app.services import rustdesk
    async with db.sessionmaker()() as s:
        return {r.version: (r.status, r.pinned) for r in await rustdesk.releases(s)}


async def test_sync_downloads_verifies_and_admin_page_shows(app, gh):
    from app.config import get_config
    from app.services import rustdesk
    gh.add("1.5.0")
    res = await rustdesk.sync()
    assert res == {"version": "1.5.0", "result": "activated"}
    assert await state() == {"1.5.0": ("active", False)}
    d = get_config().rustdesk_dir / "1.5.0"
    assert sorted(p.name for p in d.iterdir()) == sorted(f"rustdesk-1.5.0-{s}" for s in PLATFORM_FILES if "sciter" not in s)
    assert not [p for p in d.parent.iterdir() if p.name.startswith(".tmp")]
    assert (await rustdesk.sync())["result"] == "up to date"
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        page = (await c.get("/admin/rustdesk")).text
        assert "Version 1.5.0" in page and "active" in page and "GitHub digest" in page and "Last check" in page
        assert "Mac with Apple chip" in page
    assert "rustdesk.synced" in await audit_types()


async def test_hash_mismatch_fails_closed(gh):
    from app.config import get_config
    from app.services import rustdesk
    gh.add("1.4.9")
    await rustdesk.sync()
    gh.add("1.5.0")
    gh.tamper.add("rustdesk-1.5.0-x86_64.dmg")
    res = await rustdesk.sync()
    assert "mismatch" in res["error"]
    assert await state() == {"1.4.9": ("active", False)}            # old version stays active
    assert not (get_config().rustdesk_dir / "1.5.0").exists()
    assert not [p for p in get_config().rustdesk_dir.iterdir() if p.name.startswith(".tmp")]
    assert "rustdesk.sync_failed" in await audit_types()
    lc = await rustdesk.last_check()
    assert lc["ok"] is False


async def test_no_digest_waits_for_approval(app, gh):
    from app import db
    from app.services import rustdesk
    gh.add("1.4.9")
    await rustdesk.sync()
    gh.no_digest = True
    gh.add("1.5.0")
    res = await rustdesk.sync()
    assert "approval" in res["result"]
    assert await state() == {"1.5.0": ("pending_approval", False), "1.4.9": ("active", False)}
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        r = await c.post("/admin/rustdesk/approve", data={"csrf": tok, "version": "1.5.0"})
        assert r.status_code == 303
    assert await state() == {"1.5.0": ("active", False), "1.4.9": ("previous", False)}
    assert "rustdesk.approved" in await audit_types()


async def test_keep_two_pin_and_rollback(app, gh):
    from app.config import get_config
    from app.services import rustdesk
    for v in ("1.4.8", "1.4.9", "1.5.0"):
        gh.add(v)
        await rustdesk.sync()
    assert await state() == {"1.5.0": ("active", False), "1.4.9": ("previous", False), "1.4.8": ("archived", False)}
    assert not (get_config().rustdesk_dir / "1.4.8").exists()
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        assert (await c.post("/admin/rustdesk/rollback", data={"csrf": tok})).status_code == 303
        assert await state() == {"1.5.0": ("previous", False), "1.4.9": ("active", False), "1.4.8": ("archived", False)}
        await c.post("/admin/rustdesk/pin", data={"csrf": tok, "version": "1.4.9"})
        gh.add("1.5.1")
        assert "pinned" in (await rustdesk.sync())["result"]
        st = await state()
        assert st["1.4.9"] == ("active", True) and st["1.5.1"][0] == "previous"
    assert {"rustdesk.rolled_back", "rustdesk.pinned"} <= set(await audit_types())


async def test_prerelease_is_skipped(gh):
    from app.services import rustdesk
    gh.prerelease = True
    gh.add("1.6.0")
    res = await rustdesk.sync()
    assert "not a stable release" in res["error"] and await state() == {}


async def customer_client(app, sess):
    from app.services import customer_auth
    c = make_client(app, "198.51.100.200")
    await c.__aenter__()
    c.cookies.set("__Host-connect-cust", await customer_auth.issue(str(sess.id)), domain="connect.test")
    return c


async def test_downloads_need_token_and_carry_filename(app, gh):
    from app import db
    from app.services import rustdesk
    from app.services import settings as settings_svc
    gh.add("1.5.0")
    await rustdesk.sync()
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "rustdesk", {"host": "rd.example.com", "key": "AbC123=", "relay": ""})
    tech = await create_user()
    sess = await make_session(tech)
    async with make_client(app, "198.51.100.201") as anon:
        assert (await anon.get("/dl/rustdesk/windows/x86_64")).status_code == 404
        anon.cookies.set("__Host-connect-cust", "forged", domain="connect.test")
        assert (await anon.get("/dl/rustdesk/windows/x86_64")).status_code == 404
    c = await customer_client(app, sess)
    try:
        r = await c.get("/dl/rustdesk/windows/x86_64")
        assert r.status_code == 200 and r.content == b"rustdesk-1.5.0-x86_64.exe binary"
        assert 'filename="rustdesk-host=rd.example.com,key=AbC123=,.exe"' in r.headers["content-disposition"]
        r = await c.get("/dl/rustdesk/macos/arm64")
        assert r.status_code == 200 and "rustdesk-1.5.0-aarch64.dmg" in r.headers["content-disposition"]
        assert (await c.get("/dl/rustdesk/windows/x86_64?kind=msi")).status_code == 200
        assert (await c.get("/dl/rustdesk/linux/x86_64?kind=../../etc")).status_code == 404
        assert (await c.get("/dl/rustdesk/ios/arm64")).status_code == 404
    finally:
        await c.__aexit__(None, None, None)
    types = await audit_types()
    assert types.count("session.rustdesk_downloaded") == 3


async def test_key_with_slash_falls_back_and_warns_admin(app, gh):
    from app import db
    from app.services import rustdesk
    from app.services import settings as settings_svc
    gh.add("1.5.0")
    await rustdesk.sync()
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "rustdesk", {"host": "rd.example.com", "key": "abc/def=", "relay": ""})
    tech = await create_user()
    admin = await create_user(email="admin@example.com", role="admin")
    sess = await make_session(tech)
    c = await customer_client(app, sess)
    try:
        r = await c.get("/dl/rustdesk/windows/x86_64")
        assert 'filename="rustdesk.exe"' in r.headers["content-disposition"]
    finally:
        await c.__aexit__(None, None, None)
    async with make_client(app) as a:
        await login(a, admin)
        page = (await a.get("/admin/rustdesk")).text
        assert 'data-testid="filename-warning"' in page and "can&#39;t be used in a Windows file name" in page


async def test_downloads_refused_after_session_end(app, gh):
    from app import db
    from app.models import Session
    from app.services import rustdesk, sessions
    from app.services.events import SYSTEM
    gh.add("1.5.0")
    await rustdesk.sync()
    tech = await create_user()
    sess = await make_session(tech)
    c = await customer_client(app, sess)
    try:
        assert (await c.get("/dl/rustdesk/android/universal")).status_code == 200
        async with db.sessionmaker()() as s:
            await sessions.end(s, await s.get(Session, sess.id), "tech_ended", SYSTEM)
        assert (await c.get("/dl/rustdesk/android/universal")).status_code == 404
    finally:
        await c.__aexit__(None, None, None)


async def test_prompt_and_decline_audited(app, gh):
    from app import db
    from app.models import Session
    from app.services import rustdesk, rustdesk_prompt
    gh.add("1.5.0")
    await rustdesk.sync()
    tech = await create_user()
    sess = await make_session(tech)
    async with make_client(app) as c:
        await login(c, tech)
        tok = await portal_csrf(c)
        r = await c.post(f"/api/sessions/{sess.id}/rustdesk", headers={"X-CSRF-Token": tok})
        assert r.status_code == 200 and r.json()["version"] == "1.5.0"
    async with db.sessionmaker()() as s:
        await rustdesk_prompt.customer_response(s, await s.get(Session, sess.id), {"action": "declined"})
    assert {"session.rustdesk_prompted", "session.rustdesk_declined"} <= set(await audit_types())
