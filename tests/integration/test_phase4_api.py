from conftest import audit_types, create_user, login, make_client, portal_csrf
from helpers_sessions import make_session


async def test_viewer_cap_enforced_server_side(app):
    from app import db
    from app.services import settings as settings_svc
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "limits", {"viewers_per_stream": 3})
    owner = await create_user(email="owner@example.com", role="admin")
    sess = await make_session(owner, visibility="all_techs")
    stream_id = str(sess.streams[0].id)
    results = []
    for i in range(4):
        u = await create_user(email=f"v{i}@example.com")
        async with make_client(app, f"203.0.113.{10 + i}") as c:
            await login(c, u)
            tok = await portal_csrf(c)
            r = await c.post(f"/api/sessions/{sess.id}/view", json={"stream_id": stream_id, "kind": "thumb"},
                             headers={"X-CSRF-Token": tok})
            results.append(r.status_code)
            if r.status_code == 200:
                url = r.json()["url"]
                assert url.startswith("/vdo/#") and "view=" in url and "bitrate=150" in url and "noaudio" in url
    assert results == [200, 200, 200, 409]


async def test_full_view_audited_and_admin_join(app):
    tech = await create_user(email="tech@example.com")
    admin = await create_user(email="admin@example.com", role="admin")
    sess = await make_session(tech)
    stream_id = str(sess.streams[0].id)
    async with make_client(app) as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        r = await c.post(f"/api/sessions/{sess.id}/view", json={"stream_id": stream_id, "kind": "full"},
                         headers={"X-CSRF-Token": tok})
        assert r.status_code == 200 and "volume=0" in r.json()["url"] and "scale=100" in r.json()["url"]
        r = await c.post(f"/api/sessions/{sess.id}/view/close", json={"stream_id": stream_id, "kind": "full"},
                         headers={"X-CSRF-Token": tok})
        assert r.status_code == 200
    types = await audit_types()
    assert {"session.viewed", "session.admin_joined", "session.view_closed"} <= set(types)


async def test_end_by_owner_and_admin_reasons(app):
    tech = await create_user(email="tech@example.com")
    admin = await create_user(email="admin@example.com", role="admin")
    s1 = await make_session(tech)
    s2 = await make_session(tech)
    async with make_client(app) as c:
        await login(c, tech)
        tok = await portal_csrf(c)
        r = await c.post(f"/api/sessions/{s1.id}/end", headers={"X-CSRF-Token": tok})
        assert r.json()["reason"] == "tech_ended"
    async with make_client(app, "203.0.113.99") as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        r = await c.post(f"/api/sessions/{s2.id}/end", headers={"X-CSRF-Token": tok})
        assert r.json()["reason"] == "admin_ended"
