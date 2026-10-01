"""Phase 2 acceptance: auth, MFA gating, rate limiting, wizard, branding."""
import re

from conftest import (
    audit_types, create_user, csrf_of, current_code, login, make_client, portal_csrf,
)

PORTAL_PAGES = ["/portal", "/account", "/admin/users"]


async def test_pages_require_login(client):
    for p in PORTAL_PAGES:
        r = await client.get(p)
        assert r.status_code == 303 and r.headers["location"] == "/login", p


async def test_password_ok_but_no_totp_cannot_reach_portal(client):
    u = await create_user(enrolled=False)
    r = await login(client, u)
    assert r.headers["location"] == "/mfa/enrol"
    for p in PORTAL_PAGES:
        r = await client.get(p)
        assert r.status_code == 303 and r.headers["location"] == "/mfa/enrol", p
    ws_ok = await _portal_ws_allowed(client)
    assert not ws_ok


async def test_mfa_pending_cannot_reach_portal(client):
    u = await create_user()
    token = await csrf_of(client, "/login")
    r = await client.post("/login", data={"csrf": token, "email": u.email, "password": u.plain_password})
    assert r.headers["location"] == "/login/mfa"
    r = await client.get("/portal")
    assert r.headers["location"] == "/login/mfa"


async def test_enrolment_flow_then_portal(client):
    u = await create_user(enrolled=False)
    await login(client, u)
    r = await client.get("/mfa/enrol")
    assert r.status_code == 200 and "<svg" in r.text
    secret = re.search(r'class="secret">([A-Z2-7 ]+)<', r.text).group(1).replace(" ", "")
    token = await csrf_of(client, "/mfa/enrol")
    r = await client.post("/mfa/enrol", data={"csrf": token, "code": "000000"})
    assert r.status_code == 400
    r = await client.post("/mfa/enrol", data={"csrf": token, "code": current_code(secret)})
    assert r.status_code == 303 and r.headers["location"] == "/mfa/recovery-codes"
    r = await client.get("/mfa/recovery-codes")
    codes = re.findall(r"<code>([a-z0-9]{5}-[a-z0-9]{5})</code>", r.text)
    assert len(codes) == 10
    assert (await client.get("/mfa/recovery-codes")).status_code == 303  # shown once
    assert (await client.get("/portal")).status_code == 200
    types = await audit_types()
    assert "auth.mfa_enrolled" in types and "auth.login" in types


async def test_totp_replay_and_recovery_code(app):
    u = await create_user()
    async with make_client(app) as c1:
        code = current_code(u.plain_secret)
        await login(c1, u, code)
    async with make_client(app) as c2:
        token = await csrf_of(c2, "/login")
        await c2.post("/login", data={"csrf": token, "email": u.email, "password": u.plain_password})
        token = await csrf_of(c2, "/login/mfa")
        r = await c2.post("/login/mfa", data={"csrf": token, "code": code})
        assert r.status_code == 401  # same code cannot be used twice
        r = await c2.post("/login/mfa", data={"csrf": token, "code": "AAAAA-BBBBB"})
        assert r.status_code == 303
    assert "auth.recovery_code_used" in await audit_types()


async def test_login_rate_limit_per_account_and_audit(app):
    u = await create_user()
    async with make_client(app, "198.51.100.7") as c:
        token = await csrf_of(c, "/login")
        for _ in range(5):
            r = await c.post("/login", data={"csrf": token, "email": u.email, "password": "wrong password!"})
            assert r.status_code == 401
        # Locked now, even with the right password.
        r = await c.post("/login", data={"csrf": token, "email": u.email, "password": u.plain_password})
        assert r.status_code == 429 and "Too many attempts" in r.text
    # Another IP is also blocked for this account.
    async with make_client(app, "198.51.100.8") as c:
        token = await csrf_of(c, "/login")
        r = await c.post("/login", data={"csrf": token, "email": u.email, "password": u.plain_password})
        assert r.status_code == 429
    types = await audit_types()
    assert types.count("auth.login_failed") >= 6 and "auth.locked_out" in types


async def test_login_rate_limit_per_ip(app):
    async with make_client(app, "198.51.100.9") as c:
        token = await csrf_of(c, "/login")
        for i in range(10):
            await c.post("/login", data={"csrf": token, "email": f"nobody{i}@example.com", "password": "x" * 12})
        u = await create_user(email="other@example.com")
        r = await c.post("/login", data={"csrf": token, "email": u.email, "password": u.plain_password})
        assert r.status_code == 429


async def test_mfa_failures_lock_out(client):
    u = await create_user()
    token = await csrf_of(client, "/login")
    await client.post("/login", data={"csrf": token, "email": u.email, "password": u.plain_password})
    token = await csrf_of(client, "/login/mfa")
    for _ in range(4):
        r = await client.post("/login/mfa", data={"csrf": token, "code": "000000"})
        assert r.status_code == 401
    r = await client.post("/login/mfa", data={"csrf": token, "code": "000000"})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    types = await audit_types()
    assert types.count("auth.mfa_failed") == 5 and "auth.locked_out" in types


async def test_csrf_required_on_login(client):
    await csrf_of(client, "/login")
    r = await client.post("/login", data={"email": "a@b.c", "password": "x"})
    assert r.status_code == 403


async def test_session_cookie_flags(client):
    r = await client.get("/login")
    sc = r.headers["set-cookie"]
    assert sc.startswith("__Host-connect=")
    for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert flag.lower() in sc.lower()
    assert "domain" not in sc.lower()


async def test_session_rotates_on_login(client):
    u = await create_user()
    await client.get("/login")
    before = client.cookies.get("__Host-connect")
    await login(client, u)
    after = client.cookies.get("__Host-connect")
    assert before and after and before != after


async def test_logout(client):
    u = await create_user()
    await login(client, u)
    token = await portal_csrf(client)
    r = await client.post("/logout", data={"csrf": token})
    assert r.status_code == 303
    assert (await client.get("/portal")).status_code == 303
    assert "auth.logout" in await audit_types()


async def test_forced_password_change(client):
    u = await create_user(must_change=True)
    await login(client, u)
    r = await client.get("/portal")
    assert r.headers["location"] == "/account/password"
    token = await csrf_of(client, "/account/password")
    r = await client.post("/account/password", data={"csrf": token, "current": u.plain_password,
                                                     "new": "short", "confirm": "short"})
    assert r.status_code == 400
    r = await client.post("/account/password", data={"csrf": token, "current": u.plain_password,
                                                     "new": "a brand new passphrase", "confirm": "a brand new passphrase"})
    assert r.status_code == 303
    assert (await client.get("/portal")).status_code == 200


async def _portal_ws_allowed(client) -> bool:
    from app.ws.portal import authenticate

    class FakeWS:
        cookies = {"__Host-connect": client.cookies.get("__Host-connect")}
    return await authenticate(FakeWS()) is not None


# ---------------------------------------------------------------- wizard

async def test_wizard_requires_token_and_locks_after_admin(app, client):
    assert (await client.get("/setup")).status_code == 404
    assert (await client.get("/setup?token=wrong")).status_code == 404
    r = await client.get("/setup?token=test-setup-token")
    assert r.status_code == 200
    token = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
    r = await client.post("/setup", data={"csrf": token, "token": "test-setup-token", "email": "boss@example.com",
                                          "display_name": "Boss", "password": "a strong passphrase",
                                          "confirm": "a strong passphrase"})
    assert r.status_code == 303 and r.headers["location"] == "/mfa/enrol"
    # Token is now dead for everyone, permanently.
    async with make_client(app, "198.51.100.20") as other:
        assert (await other.get("/setup?token=test-setup-token")).status_code == 404
        tok = await csrf_of(other, "/login")
        r = await other.post("/setup", data={"csrf": tok, "token": "test-setup-token", "email": "x@example.com",
                                             "display_name": "X", "password": "a strong passphrase",
                                             "confirm": "a strong passphrase"})
        assert r.status_code == 404
    # Finish the wizard as the new admin.
    r = await client.get("/mfa/enrol")
    secret = re.search(r'class="secret">([A-Z2-7 ]+)<', r.text).group(1).replace(" ", "")
    token = await csrf_of(client, "/mfa/enrol")
    await client.post("/mfa/enrol", data={"csrf": token, "code": current_code(secret)})
    r = await client.get("/mfa/recovery-codes")
    assert 'href="/setup/brand"' in r.text
    token = await csrf_of(client, "/setup/brand")
    r = await client.post("/setup/brand", data={"csrf": token, "product_name": "Connect", "company_name": "Acme IT",
                                                "company_short": "ACME", "primary_colour": "#123456",
                                                "accent_colour": "#ffcc00"},
                          files={"logo": ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script><rect width="1" height="1"/></svg>', "image/svg+xml")})
    assert r.status_code == 303, r.text[:400]
    token = await csrf_of(client, "/setup/urls")
    r = await client.post("/setup/urls", data={"csrf": token, "connect_url": "https://connect.test",
                                               "help_url": "https://help.example.com", "privacy_url": ""})
    assert r.status_code == 303
    token = await csrf_of(client, "/setup/integrations")
    r = await client.post("/setup/integrations", data={"csrf": token, "turn_host": "turn.example.com",
                                                       "rustdesk_host": "rd.example.com", "rustdesk_key": "abc=",
                                                       "rustdesk_relay": ""})
    assert r.status_code == 303 and r.headers["location"].startswith("/portal")
    # Wizard steps are now gone, even for the admin.
    assert (await client.get("/setup/brand")).status_code == 404
    assert (await client.get("/setup?token=test-setup-token")).status_code == 404
    types = await audit_types()
    assert "setup.completed" in types and "user.created" in types and types.count("settings.changed") >= 4


async def test_brand_colour_changes_pages_without_code_change(client):
    from app import db
    from app.services import settings as settings_svc
    r = await client.get("/brand.css")
    assert "--brand-primary:#1f4e79" in r.text
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "branding", {"primary_colour": "#7a1f3d", "company_name": "Other MSP"})
    r = await client.get("/brand.css")
    assert "--brand-primary:#7a1f3d" in r.text
    page = await client.get("/login")
    assert 'href="/brand.css' in page.text and "Other MSP" in page.text


# ---------------------------------------------------------------- admin users

async def test_admin_user_management(client):
    admin = await create_user(email="admin@example.com", name="Ada Admin", role="admin")
    await login(client, admin)
    token = await portal_csrf(client)
    r = await client.post("/admin/users", data={"csrf": token, "email": "new@example.com",
                                                "display_name": "Nina New", "role": "tech"})
    assert r.status_code == 200
    temp = re.search(r'data-testid="temp-password">([^<]+)<', r.text).group(1)
    assert len(temp) == 19
    from app import db
    from app.models import User
    from sqlalchemy import select
    async with db.sessionmaker()() as s:
        nina = await s.scalar(select(User).where(User.email == "new@example.com"))
    r = await client.post(f"/admin/users/{nina.id}", data={"csrf": token, "action": "deactivate"})
    assert r.status_code == 303
    # Last admin cannot be demoted or deactivated.
    r = await client.post(f"/admin/users/{admin.id}", data={"csrf": token, "action": "role", "role": "tech"})
    assert r.status_code == 400
    r = await client.post(f"/admin/users/{nina.id}", data={"csrf": token, "action": "reset_mfa"})
    assert r.status_code == 200
    types = await audit_types()
    assert {"user.created", "user.updated", "user.mfa_reset"} <= set(types)


async def test_tech_cannot_reach_admin(client):
    u = await create_user()
    await login(client, u)
    assert (await client.get("/admin/users")).status_code == 404


async def test_deactivated_user_is_signed_out(app):
    admin = await create_user(email="admin@example.com", role="admin")
    tech = await create_user()
    async with make_client(app) as t, make_client(app, "203.0.113.60") as a:
        await login(t, tech)
        assert (await t.get("/portal")).status_code == 200
        await login(a, admin)
        token = await portal_csrf(a)
        await a.post(f"/admin/users/{tech.id}", data={"csrf": token, "action": "deactivate"})
        assert (await t.get("/portal")).status_code == 303


async def test_security_headers(client):
    r = await client.get("/login")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'self'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers["permissions-policy"]
