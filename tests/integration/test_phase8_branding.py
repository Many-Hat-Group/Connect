"""Phase 8: every branding field reaches customer pages, portal, emails and
titles with no code edits; settings validation; headers; licences; health."""
import re

from conftest import audit_types, create_user, login, make_client, portal_csrf

MARK = {
    "product_name": "ZetaDesk", "company_name": "Quokka IT Pty Ltd", "company_short": "QIT",
    "support_phone": "02 9999 0000", "support_email": "help@quokka.example", "footer_text": "Quokka footer line",
    "primary_colour": "#2a5d34", "accent_colour": "#ffcc33",
    "connect_url": "https://connect.quokka.example", "help_url": "https://help.quokka.example",
    "privacy_url": "https://quokka.example/privacy",
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


async def test_every_branding_field_applies_everywhere(app):
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        r = await c.post("/admin/branding", data={"csrf": tok, **MARK},
                         files={"logo": ("l.png", PNG, "image/png"), "favicon": ("f.png", PNG, "image/png")})
        assert r.status_code == 303, r.text[:500]
        portal = (await c.get("/portal")).text
        css = (await c.get("/brand.css")).text
    async with make_client(app, "198.51.100.99") as anon:
        choose = (await anon.get("/")).text
        entry = (await anon.get("/screen")).text
        login_page = (await anon.get("/login")).text
    from app import db
    from app.services import branding, settings as settings_svc
    async with db.sessionmaker()() as s:
        st = await settings_svc.load(s, fresh=True)
    email = branding.render_email(st, "Session ended", "Your session has ended.")
    logo = st["branding"]["logo_url"]
    fav = st["branding"]["favicon_url"]
    assert logo.startswith("/media/branding/logo-") and fav.startswith("/media/branding/favicon-")
    for page in (portal, choose, entry, login_page):
        assert f"<title>" in page and "ZetaDesk" in page.split("<title>")[1].split("</title>")[0]
        assert "Quokka IT Pty Ltd" in page and "02 9999 0000" in page and "help@quokka.example" in page
        assert "Quokka footer line" in page and MARK["help_url"] in page and MARK["privacy_url"] in page
        assert f'src="{logo}"' in page and f'href="{fav}"' in page
    assert "Only continue if you are on the phone with Quokka IT Pty Ltd" in entry
    assert "--brand-primary:#2a5d34" in css and "--brand-accent:#ffcc33" in css
    for v in ("ZetaDesk", "Quokka IT Pty Ltd", "02 9999 0000", "help@quokka.example", "Quokka footer line",
              MARK["connect_url"]):
        assert v in email
    assert "settings.changed" in await audit_types()


async def test_logo_served_with_strict_headers(app):
    from app.services import branding
    url = branding.store_upload(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')
    async with make_client(app) as c:
        r = await c.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
        assert (await c.get("/media/branding/../../etc/passwd")).status_code == 404


async def test_branding_rejects_bad_input(app):
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        for bad in ({"primary_colour": "red"}, {"help_url": "javascript:alert(1)"}, {"company_name": "<script>"},
                    {"connect_url": "http://insecure.example"}):
            r = await c.post("/admin/branding", data={"csrf": tok, **{**MARK, **bad}})
            assert r.status_code == 400, bad
        r = await c.post("/admin/branding", data={"csrf": tok, **MARK}, files={"logo": ("x.gif", b"GIF89a", "image/gif")})
        assert r.status_code == 400


async def test_low_contrast_warns(app):
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        await c.post("/admin/branding", data={"csrf": tok, **{**MARK, "primary_colour": "#dddddd"}})
        page = (await c.get("/admin/branding")).text
        assert 'id="contrast-warnings"' in page and "contrast" in page


async def test_settings_page_saves_and_validates(app):
    from app import db
    from app.services import settings as settings_svc
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        page = (await c.get("/admin/settings")).text
        form = dict(re.findall(r'name="([a-z_]+\.[a-z_]+)"[^>]*value="([^"]*)"', page))
        form["csrf"] = await portal_csrf(c)
        form["limits.viewers_per_stream"] = "2"
        form["timeouts.code_ttl_seconds"] = "900"
        form["rustdesk.host"] = "rd.example.com"
        r = await c.post("/admin/settings", data=form)
        assert r.status_code == 303, r.text[:800]
        async with db.sessionmaker()() as s:
            st = await settings_svc.load(s, fresh=True)
        assert st["limits"]["viewers_per_stream"] == 2 and st["timeouts"]["code_ttl_seconds"] == 900
        assert st["rustdesk"]["host"] == "rd.example.com"
        bad = dict(form, **{"limits.viewers_per_stream": "99"})
        assert (await c.post("/admin/settings", data=bad)).status_code == 400
        bad = dict(form, **{"timeouts.tech_absent_warning_seconds": "600", "timeouts.tech_absent_seconds": "300"})
        assert (await c.post("/admin/settings", data=bad)).status_code == 400


async def test_health_page(app):
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        page = (await c.get("/admin/health")).text
        assert 'data-check="Database"' in page and 'data-check="Redis"' in page and "Relay-only connection" in page
        cfg = (await c.get("/admin/health/turn-test")).json()
        assert len(cfg["iceServers"]) == 3 and all(":" in s["username"] for s in cfg["iceServers"])


async def test_licences_page_public(app):
    async with make_client(app) as c:
        page = (await c.get("/licences")).text
        for name in ("VDO.Ninja", "handshake server", "RustDesk", "coturn"):
            assert name in page
        assert "github.com/steveseguin/vdo.ninja/tree/v30.2" in page and "AGPL-3.0" in page


async def test_admin_pages_admin_only(app):
    tech = await create_user()
    async with make_client(app) as c:
        await login(c, tech)
        for p in ("/admin/settings", "/admin/branding", "/admin/health", "/admin/rustdesk", "/admin/health/turn-test"):
            assert (await c.get(p)).status_code == 404, p


async def test_security_headers_on_every_page_type(app):
    admin = await create_user(email="admin@example.com", role="admin")
    async with make_client(app) as c:
        await login(c, admin)
        for path in ("/", "/screen", "/login", "/portal", "/admin/users", "/admin/audit", "/licences", "/nope"):
            h = (await c.get(path)).headers
            csp = h["content-security-policy"]
            assert "script-src 'self';" in csp and "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp, path
            assert "frame-ancestors 'self'" in csp and "object-src 'none'" in csp and "base-uri 'none'" in csp, path
            assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "strict-origin-when-cross-origin"
            assert h["x-frame-options"] == "SAMEORIGIN" and h["cache-control"] == "no-store"
            pp = h["permissions-policy"]
            capture = path in ("/", "/screen")
            assert ("display-capture=(self)" in pp) == capture and ("camera=()" in pp) == (not capture), path
