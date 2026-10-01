"""Phase 5 permission matrix: private sessions are invisible (404) to other
techs over REST and websocket until shared or an invite is accepted, and
invisible again once revoked. Admin actions are audited."""
import pytest

from conftest import audit_types, create_user, login, make_client, portal_csrf
from helpers_sessions import make_session


async def tech_client(app, user, ip):
    c = make_client(app, ip)
    await c.__aenter__()
    await login(c, user)
    c.headers["X-CSRF-Token"] = await portal_csrf(c)
    return c


def endpoints(sid, stream):
    return [
        ("GET", f"/api/sessions/{sid}", None),
        ("GET", f"/portal/session/{sid}", None),
        ("GET", f"/api/sessions/{sid}/access", None),
        ("POST", f"/api/sessions/{sid}/view", {"stream_id": stream, "kind": "full"}),
        ("POST", f"/api/sessions/{sid}/quality", {"stream_id": stream, "preset": "clear"}),
        ("POST", f"/api/sessions/{sid}/audio", {"unmuted": True}),
        ("POST", f"/api/sessions/{sid}/share-all", {}),
        ("POST", f"/api/sessions/{sid}/invite", {"user_id": "00000000-0000-0000-0000-000000000000"}),
        ("POST", f"/api/sessions/{sid}/end", None),
        ("POST", f"/api/sessions/{sid}/kill", {}),
    ]


async def call(c, method, path, body):
    if method == "GET":
        return await c.get(path)
    return await c.post(path, json=body if body is not None else {})


async def ws_view(user, sess):
    """What the portal websocket gives this user for the session: presence
    marking and update fan-out."""
    from app import db
    from app.models import Session
    from app.services import sessions
    from app.ws.portal import heartbeat
    marked = await heartbeat(user, [str(sess.id)])
    async with db.sessionmaker()() as s:
        fresh = await s.get(Session, sess.id)
        gets_updates = str(user.id) in await sessions.recipients(s, fresh)
    return bool(marked), gets_updates


async def test_private_session_matrix(app):
    a = await create_user(email="a@example.com", name="Alice")
    b = await create_user(email="b@example.com", name="Bob")
    sess = await make_session(a)
    stream = str(sess.streams[0].id)
    cb = await tech_client(app, b, "203.0.113.2")
    try:
        # Read-only and harmless endpoints first; end/kill last.
        for method, path, body in endpoints(sess.id, stream):
            r = await call(cb, method, path, body)
            assert r.status_code == 404, (method, path, r.status_code)
        assert await ws_view(b, sess) == (False, False)
        dash = (await cb.get("/api/dashboard")).json()
        assert all(s["id"] != str(sess.id) for s in dash["sessions"])
    finally:
        await cb.__aexit__(None, None, None)
    from app import db
    from app.models import Session
    async with db.sessionmaker()() as s:
        assert (await s.get(Session, sess.id)).status == "live"   # B's end/kill did nothing


async def test_share_all_then_unshare(app):
    a = await create_user(email="a@example.com", name="Alice")
    b = await create_user(email="b@example.com", name="Bob")
    sess = await make_session(a)
    ca = await tech_client(app, a, "203.0.113.1")
    cb = await tech_client(app, b, "203.0.113.2")
    try:
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 404
        assert (await ca.post(f"/api/sessions/{sess.id}/share-all", json={})).status_code == 200
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 200
        assert await ws_view(b, sess) == (True, True)
        assert (await ca.post(f"/api/sessions/{sess.id}/share-all", json={"off": True})).status_code == 200
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 404
        assert await ws_view(b, sess) == (False, False)
    finally:
        await ca.__aexit__(None, None, None)
        await cb.__aexit__(None, None, None)
    assert "session.shared_all" in await audit_types()


async def test_invite_accept_and_revoke(app):
    a = await create_user(email="a@example.com", name="Alice")
    b = await create_user(email="b@example.com", name="Bob")
    sess = await make_session(a)
    stream = str(sess.streams[0].id)
    ca = await tech_client(app, a, "203.0.113.1")
    cb = await tech_client(app, b, "203.0.113.2")
    try:
        r = await ca.post(f"/api/sessions/{sess.id}/invite", json={"user_id": str(b.id)})
        access_id = r.json()["access_id"]
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 404   # pending is not access
        # Someone else cannot accept B's invite.
        assert (await ca.post(f"/api/invites/{access_id}/accept")).status_code == 404
        assert (await cb.post(f"/api/invites/{access_id}/accept")).status_code == 200
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 200
        r = await cb.post(f"/api/sessions/{sess.id}/view", json={"stream_id": stream, "kind": "full"})
        assert r.status_code == 200
        assert await ws_view(b, sess) == (True, True)
        assert (await ca.post(f"/api/invites/{access_id}/revoke")).status_code == 200
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 404
        assert (await cb.post(f"/api/sessions/{sess.id}/view", json={"stream_id": stream, "kind": "full"})).status_code == 404
        assert await ws_view(b, sess) == (False, False)
        from app.services import viewers
        assert all(v["user_id"] != str(b.id) for v in await viewers.list_for(stream))  # view closed
    finally:
        await ca.__aexit__(None, None, None)
        await cb.__aexit__(None, None, None)
    types = await audit_types()
    assert {"session.invite_sent", "session.invite_accepted", "session.invite_revoked"} <= set(types)


async def test_declined_invite_gives_no_access(app):
    a = await create_user(email="a@example.com")
    b = await create_user(email="b@example.com")
    sess = await make_session(a)
    ca = await tech_client(app, a, "203.0.113.1")
    cb = await tech_client(app, b, "203.0.113.2")
    try:
        access_id = (await ca.post(f"/api/sessions/{sess.id}/invite", json={"user_id": str(b.id)})).json()["access_id"]
        assert (await cb.post(f"/api/invites/{access_id}/decline")).status_code == 200
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 404
        assert (await cb.post(f"/api/invites/{access_id}/accept")).status_code == 404
    finally:
        await ca.__aexit__(None, None, None)
        await cb.__aexit__(None, None, None)
    assert "session.invite_declined" in await audit_types()


async def test_admin_join_transfer_and_grant_audited(app):
    a = await create_user(email="a@example.com", name="Alice")
    b = await create_user(email="b@example.com", name="Bob")
    c_user = await create_user(email="c@example.com", name="Cara")
    admin = await create_user(email="admin@example.com", name="Ada", role="admin")
    sess = await make_session(a)
    stream = str(sess.streams[0].id)
    cad = await tech_client(app, admin, "203.0.113.9")
    ca = await tech_client(app, a, "203.0.113.1")
    cb = await tech_client(app, b, "203.0.113.2")
    cc = await tech_client(app, c_user, "203.0.113.3")
    try:
        dash = (await cad.get("/api/dashboard")).json()
        assert any(s["id"] == str(sess.id) for s in dash["sessions"])   # admin sees everything
        assert (await cad.post(f"/api/sessions/{sess.id}/view", json={"stream_id": stream, "kind": "full"})).status_code == 200
        # A tech cannot transfer or grant, even on their own session.
        assert (await ca.post(f"/api/sessions/{sess.id}/transfer", json={"user_id": str(b.id)})).status_code == 404
        assert (await ca.post(f"/api/sessions/{sess.id}/grant", json={"user_id": str(b.id)})).status_code == 404
        assert (await cad.post(f"/api/sessions/{sess.id}/transfer", json={"user_id": str(b.id)})).status_code == 200
        assert (await cb.get(f"/api/sessions/{sess.id}")).status_code == 200
        assert (await ca.get(f"/api/sessions/{sess.id}")).status_code == 404     # old owner loses access
        assert (await cad.post(f"/api/sessions/{sess.id}/grant", json={"user_id": str(c_user.id)})).status_code == 200
        assert (await cc.get(f"/api/sessions/{sess.id}")).status_code == 200
        assert (await cad.post(f"/api/sessions/{sess.id}/share-all", json={})).status_code == 200
        assert (await ca.get(f"/api/sessions/{sess.id}")).status_code == 200
        assert (await cad.get("/admin/sessions?status=active")).status_code == 200
        assert (await cb.get("/admin/sessions")).status_code == 404
    finally:
        for c in (cad, ca, cb, cc):
            await c.__aexit__(None, None, None)
    types = await audit_types()
    assert {"session.admin_joined", "session.transferred", "session.access_granted", "session.shared_all"} <= set(types)
