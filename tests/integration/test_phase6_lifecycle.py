"""Phase 6: every end_reason with shortened timeouts, presence rules,
retention and CSV export."""
import asyncio
import csv
import io
import time
from datetime import timedelta

from sqlalchemy import select, update

from conftest import audit_types, create_user, login, make_client, portal_csrf
from helpers_sessions import make_session


async def set_timeouts(**kw):
    from app import db
    from app.services import settings as settings_svc
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "timeouts", kw)


async def reason(session_id):
    from app import db
    from app.models import Session
    async with db.sessionmaker()() as s:
        x = await s.get(Session, session_id)
        return x.status, x.end_reason


async def seen(sess, tech=None, customer=True):
    from app.services import presence
    if tech is not None:
        await presence.tech_seen(str(sess.id), str(tech.id), ttl=30)
    if customer:
        await presence.customer_seen(str(sess.id), ttl=30)


async def test_tech_absent_timeout():
    from app.services import lifecycle
    await set_timeouts(tech_absent_seconds=2, customer_lost_seconds=600)
    tech = await create_user()
    sess = await make_session(tech)
    await seen(sess, tech)
    assert (await lifecycle.sweep())["tech_absent_timeout"] == 0
    from app.services import presence
    await presence.tech_left(str(sess.id), str(tech.id))   # tech closed the portal
    await seen(sess, customer=True)
    await lifecycle.sweep()                                # absence clock starts
    await asyncio.sleep(2.2)
    await seen(sess, customer=True)
    assert (await lifecycle.sweep())["tech_absent_timeout"] == 1
    assert await reason(sess.id) == ("ended", "tech_absent_timeout")


async def test_second_tech_with_access_keeps_it_alive():
    from app.services import lifecycle, presence
    await set_timeouts(tech_absent_seconds=2, customer_lost_seconds=600)
    owner = await create_user(email="owner@example.com")
    other = await create_user(email="other@example.com")
    stranger = await create_user(email="stranger@example.com")
    sess = await make_session(owner, visibility="all_techs")
    await seen(sess, other)
    await lifecycle.sweep()
    await asyncio.sleep(2.2)
    await seen(sess, other)
    assert (await lifecycle.sweep())["tech_absent_timeout"] == 0
    assert (await reason(sess.id))[0] == "live"
    # A tech WITHOUT access does not count: make it private again.
    from app import db
    from app.models import Session
    async with db.sessionmaker()() as s:
        await s.execute(update(Session).where(Session.id == sess.id).values(visibility="private"))
        await s.commit()
    await presence.tech_left(str(sess.id), str(other.id))
    await presence.tech_seen(str(sess.id), str(stranger.id))
    await seen(sess, customer=True)
    await lifecycle.sweep()
    await asyncio.sleep(2.2)
    await presence.tech_seen(str(sess.id), str(stranger.id))
    await seen(sess, customer=True)
    assert (await lifecycle.sweep())["tech_absent_timeout"] == 1


async def test_customer_lost_timeout():
    from app.services import lifecycle
    await set_timeouts(tech_absent_seconds=600, customer_lost_seconds=2)
    tech = await create_user()
    sess = await make_session(tech)
    await seen(sess, tech, customer=False)
    await lifecycle.sweep()
    await asyncio.sleep(2.2)
    await seen(sess, tech, customer=False)
    assert (await lifecycle.sweep())["customer_lost_timeout"] == 1
    assert await reason(sess.id) == ("ended", "customer_lost_timeout")


async def test_customer_returning_resets_the_clock():
    from app.services import lifecycle
    await set_timeouts(tech_absent_seconds=600, customer_lost_seconds=3)
    tech = await create_user()
    sess = await make_session(tech)
    await seen(sess, tech, customer=False)
    await lifecycle.sweep()
    await asyncio.sleep(2)
    await seen(sess, tech, customer=True)   # back (e.g. browser restarted on a Mac)
    await lifecycle.sweep()
    await asyncio.sleep(2)
    await seen(sess, tech, customer=True)
    assert (await lifecycle.sweep())["customer_lost_timeout"] == 0


async def test_code_expired(app):
    from app import db
    from app.models import JoinCode, utcnow
    from app.services import lifecycle
    tech = await create_user()
    async with make_client(app) as c:
        await login(c, tech)
        tok = await portal_csrf(c)
        info = (await c.post("/api/sessions", headers={"X-CSRF-Token": tok})).json()
    async with db.sessionmaker()() as s:
        await s.execute(update(JoinCode).values(expires_at=utcnow() - timedelta(seconds=1)))
        await s.commit()
    assert (await lifecycle.sweep())["code_expired"] == 1
    assert await reason(info["session_id"]) == ("ended", "code_expired")
    assert "code.expired" in await audit_types()


async def test_every_end_reason_reachable(app):
    """customer_ended, tech_ended, tech_killed and admin_ended via their paths."""
    from app import db
    from app.models import Session
    from app.services import sessions
    from app.services.events import Actor
    tech = await create_user(email="t@example.com")
    admin = await create_user(email="a@example.com", role="admin")
    s1, s2, s3, s4 = [await make_session(tech) for _ in range(4)]
    async with db.sessionmaker()() as s:
        x = await s.get(Session, s1.id)
        await sessions.end(s, x, "customer_ended", Actor.customer(x.id))   # what /ws/customer {"type":"end"} calls
    async with make_client(app) as c:
        await login(c, tech)
        tok = await portal_csrf(c)
        await c.post(f"/api/sessions/{s2.id}/end", headers={"X-CSRF-Token": tok})
        await c.post(f"/api/sessions/{s3.id}/kill", json={}, headers={"X-CSRF-Token": tok})
    async with make_client(app, "203.0.113.77") as c:
        await login(c, admin)
        tok = await portal_csrf(c)
        await c.post(f"/api/sessions/{s4.id}/end", headers={"X-CSRF-Token": tok})
    got = [await reason(x.id) for x in (s1, s2, s3, s4)]
    assert got == [("ended", "customer_ended"), ("ended", "tech_ended"), ("ended", "tech_killed"),
                   ("ended", "admin_ended")]


async def test_end_revokes_tokens():
    from app import db
    from app.models import Session
    from app.services import customer_auth, sessions, signal_tokens
    from app.services.events import SYSTEM
    tech = await create_user()
    sess = await make_session(tech)
    ctok = await customer_auth.issue(str(sess.id))
    stok = await signal_tokens.issue(str(sess.id), "viewer", str(tech.id))
    assert await customer_auth.session_for(ctok) and await signal_tokens.validate(stok)
    async with db.sessionmaker()() as s:
        await sessions.end(s, await s.get(Session, sess.id), "tech_killed", SYSTEM)
    assert await customer_auth.session_for(ctok) is None
    assert await signal_tokens.validate(stok) is None


async def test_customer_warning_in_last_minute():
    from app import db
    from app.models import Session
    from app.rds import redis
    from app.services import settings as settings_svc
    from app.ws.customer import tech_warning
    await set_timeouts(tech_absent_seconds=300, tech_absent_warning_seconds=60)
    tech = await create_user()
    sess = await make_session(tech)
    async with db.sessionmaker()() as s:
        st = await settings_svc.load(s, fresh=True)
        x = await s.get(Session, sess.id)
        assert await tech_warning(x, st) is None
        await redis().set(f"absent:tech:{sess.id}", int(time.time()) - 200)
        assert await tech_warning(x, st) is None           # 100 s left: no warning yet
        await redis().set(f"absent:tech:{sess.id}", int(time.time()) - 250)
        w = await tech_warning(x, st)
        assert w["kind"] == "tech_absent" and 45 <= w["seconds_left"] <= 50


# ---------------------------------------------------------------- audit

async def test_csv_export_matches_filters_and_is_excel_friendly(app):
    from app import db
    from app.models import AuditEvent, Session
    from app.services.events import Actor, emit
    admin = await create_user(email="admin@example.com", name="Ada", role="admin")
    tech = await create_user(email="tech@example.com", name="Tess")
    s1 = await make_session(tech)
    s2 = await make_session(tech)
    async with db.sessionmaker()() as s:
        await s.execute(update(Session).where(Session.id == s2.id).values(customer_company="=HYPERLINK(evil)"))
        await s.commit()
        await emit(s, "session.viewed", Actor.user(tech), s1.id, {"note": "Café ✓"})
        await emit(s, "session.viewed", Actor.user(tech), s2.id, {})
        await emit(s, "session.audio_unmuted", Actor.user(tech), s1.id, {})
    async with make_client(app) as c:
        await login(c, admin)
        r = await c.get(f"/admin/audit.csv?type=session.viewed&tech={tech.id}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        raw = r.content
        assert raw.startswith("﻿".encode())
        assert b"\r\n" in raw
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        assert rows[0][:2] == ["Time (UTC)", "Event"]
        body = rows[1:]
        assert len(body) == 2 and {r[1] for r in body} == {"session.viewed"}
        assert all(r[3] == "Tess" for r in body)
        assert any("Café ✓" in r[8] for r in body)
        assert any(r[6] == "'=HYPERLINK(evil)" for r in body)   # formula injection neutralised
        r = await c.get("/admin/audit.csv?company=acme&type=session.audio_unmuted")
        assert len(list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))) == 2
        page = await c.get(f"/admin/audit?session={s1.id}")
        assert page.status_code == 200 and "session.audio_unmuted" in page.text
    assert "audit.exported" in await audit_types()


async def test_audit_is_admin_only(app):
    tech = await create_user()
    async with make_client(app) as c:
        await login(c, tech)
        assert (await c.get("/admin/audit")).status_code == 404
        assert (await c.get("/admin/audit.csv")).status_code == 404


async def test_retention_deletes_only_old_records():
    from app import db
    from app.models import AuditEvent, Session, utcnow
    from app.services import lifecycle
    from app.services import settings as settings_svc
    tech = await create_user()
    old_s = await make_session(tech, status="ended")
    new_s = await make_session(tech, status="ended")
    live_s = await make_session(tech)
    async with db.sessionmaker()() as s:
        await settings_svc.update(s, "retention", {"audit_days": 365, "ended_session_days": 365})
        now = utcnow()
        await s.execute(update(Session).where(Session.id == old_s.id).values(ended_at=now - timedelta(days=366)))
        await s.execute(update(Session).where(Session.id == new_s.id).values(ended_at=now - timedelta(days=364)))
        await s.execute(update(Session).where(Session.id == live_s.id).values(created_at=now - timedelta(days=400)))
        s.add_all([AuditEvent(ts=now - timedelta(days=366), event_type="auth.login", actor_type="tech", data={"k": "old"}),
                   AuditEvent(ts=now - timedelta(days=364), event_type="auth.login", actor_type="tech", data={"k": "new"})])
        await s.commit()
    out = await lifecycle.retention()
    assert out == {"audit_events": 1, "sessions": 1}
    async with db.sessionmaker()() as s:
        assert await s.get(Session, old_s.id) is None
        assert await s.get(Session, new_s.id) is not None
        assert await s.get(Session, live_s.id) is not None   # never delete a non-ended session
        left = [e.data.get("k") for e in (await s.scalars(select(AuditEvent))).all()]
        assert "old" not in left and "new" in left
