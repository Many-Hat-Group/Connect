"""Phase 3: code claim, identical failure responses, lockout, customer token."""
import re
from datetime import timedelta

from sqlalchemy import select, update

from conftest import audit_types, create_user, login, make_client, portal_csrf

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


async def new_code(app):
    import uuid
    tech = await create_user(email=f"t{uuid.uuid4().hex[:8]}@example.com")
    async with make_client(app, "203.0.113.200") as t:
        await login(t, tech)
        token = await portal_csrf(t)
        r = await t.post("/api/sessions", headers={"X-CSRF-Token": token})
        assert r.status_code == 201, r.text
        return r.json(), tech


async def ccsrf(c, path="/screen"):
    r = await c.get(path)
    return re.search(r'name="ccsrf" value="([^"]+)"', r.text).group(1)


async def submit(c, code, token, name="Cathy Customer", company="Acme", share="screen"):
    return await c.post("/start", data={"ccsrf": token, "code": list(code), "name": name, "company": company,
                                       "ticket": "T-42", "share_type": share}, headers={"User-Agent": UA})


def body_text(r):
    m = re.search(r'id="form-error">([^<]+)<', r.text)
    return m.group(1) if m else None


async def test_valid_claim_creates_waiting_session_and_cookie(app):
    info, tech = await new_code(app)
    async with make_client(app, "198.51.100.30") as c:
        tok = await ccsrf(c)
        r = await submit(c, info["code"].lower(), tok)
        assert r.status_code == 303 and r.headers["location"] == "/s"
        sc = r.headers.get_list("set-cookie")
        cust = [x for x in sc if x.startswith("__Host-connect-cust=")][0].lower()
        assert "httponly" in cust and "secure" in cust and "samesite=strict" in cust
        page = await c.get("/s")
        assert page.status_code == 200 and 'id="page-data"' in page.text
        assert "/vdo/#room=" in page.text and "volume" not in page.text.split("page-data")[1][:0]
    from app import db
    from app.models import Session
    async with db.sessionmaker()() as s:
        sess = await s.get(Session, info["session_id"])
        assert sess.status == "waiting" and sess.customer_name == "Cathy Customer" and sess.ticket_number == "T-42"
        assert sess.os == "windows" and sess.browser == "chrome" and str(sess.customer_ip) == "198.51.100.30"
    types = await audit_types()
    assert {"code.issued", "code.claimed", "session.details_submitted"} <= set(types)


async def test_bad_expired_and_used_codes_look_identical(app):
    used, _ = await new_code(app)
    expired, _ = await new_code(app)
    from app import db
    from app.models import JoinCode, utcnow
    async with db.sessionmaker()() as s:
        await s.execute(update(JoinCode).where(JoinCode.code == expired["code"]).values(expires_at=utcnow() - timedelta(seconds=1)))
        await s.commit()
    async with make_client(app, "198.51.100.31") as c:
        tok = await ccsrf(c)
        assert (await submit(c, used["code"], tok)).status_code == 303
    responses = []
    for code, ip in ((used["code"], "198.51.100.32"), (expired["code"], "198.51.100.33"), ("ZZZZ", "198.51.100.34")):
        async with make_client(app, ip) as c:
            tok = await ccsrf(c)
            r = await submit(c, code, tok)
            responses.append((r.status_code, body_text(r)))
    assert len(set(responses)) == 1 and responses[0][0] == 400, responses


async def test_sixth_failure_from_ip_is_locked_and_audited(app):
    info, _ = await new_code(app)
    async with make_client(app, "198.51.100.40") as c:
        tok = await ccsrf(c)
        statuses = [(await submit(c, "ZZZZ", tok)).status_code for _ in range(5)]
        assert statuses == [400, 400, 400, 400, 429]
        r = await submit(c, info["code"], tok)  # the right code, but locked out
        assert r.status_code == 429 and "Too many attempts" in r.text
    types = await audit_types()
    assert types.count("code.failed_attempt") == 5 and "code.ip_locked" in types
    # Another IP is unaffected and the code still works.
    async with make_client(app, "198.51.100.41") as c:
        tok = await ccsrf(c)
        assert (await submit(c, info["code"], tok)).status_code == 303


async def test_customer_csrf_and_origin(app):
    info, _ = await new_code(app)
    async with make_client(app, "198.51.100.50") as c:
        await ccsrf(c)
        r = await c.post("/start", data={"ccsrf": "forged", "code": list(info["code"]), "name": "x", "company": "y",
                                         "share_type": "screen"})
        assert r.status_code == 403
        tok = await ccsrf(c)
        r = await c.post("/start", data={"ccsrf": tok, "code": list(info["code"]), "name": "x", "company": "y",
                                         "share_type": "screen"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403


async def test_customer_pages_require_token(app):
    async with make_client(app, "198.51.100.60") as c:
        r = await c.get("/s")
        assert r.status_code == 404
        c.cookies.set("__Host-connect-cust", "made-up-token", domain="connect.test")
        assert (await c.get("/s")).status_code == 404


async def test_global_bruteforce_banner(app):
    from app.services import codes
    from app.services import settings as settings_svc
    from app import db
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "limits", {"code_fail_global_per_hour": 3})
    for i in range(5):
        async with make_client(app, f"198.51.100.{100 + i}") as c:
            tok = await ccsrf(c)
            await submit(c, "ZZZZ", tok)
    assert await codes.bruteforce_banner() is not None
    assert (await audit_types()).count("code.bruteforce_alert") == 1


async def test_join_link_prefills_and_asks_share_type(app):
    info, _ = await new_code(app)
    async with make_client(app, "198.51.100.70") as c:
        r = await c.get(f"/join/{info['code']}")
        assert r.status_code == 200
        letters = re.findall(r'class="code-box"[^>]*value="([A-Z])"', r.text)
        assert "".join(letters) == info["code"]
        assert 'value="screen"' in r.text and 'value="camera"' in r.text
