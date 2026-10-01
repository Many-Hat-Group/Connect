"""Phase 5 in the browser: invite toast within 2 s, accept opens the session."""
import re
import time

import pytest

from flows import customer_share, new_code_via_ui, tech_page
from stack import psql, reset

USED: dict = {}


@pytest.fixture(autouse=True)
def fresh():
    reset()


def test_invite_toast_within_2s_and_accept(browser, base_url):
    actx, apage, alice = tech_page(browser, base_url, USED, "alice@example.com", "Alice")
    bctx, bpage, bob = tech_page(browser, base_url, USED, "bob@example.com", "Bob")
    bpage.wait_for_timeout(500)  # Bob's portal websocket is connected
    code = new_code_via_ui(apage)
    cctx, cpage = customer_share(browser, base_url, code)
    apage.wait_for_selector(".tile", timeout=10000)
    apage.click(".tile")
    apage.wait_for_url(re.compile(r".*/portal/session/.*"))
    sid = apage.url.rsplit("/", 1)[1]
    # Bob cannot see it yet.
    assert bpage.locator(".tile").count() == 0
    apage.click(".share-menu summary")
    apage.select_option("#invite-user", bob["id"])
    sent = time.time()
    apage.click("#invite-send")
    bpage.wait_for_selector(".toast:has-text('Alice invited you to a session')", timeout=2000)
    assert time.time() - sent < 2.0
    bpage.click(".toast:has-text('Alice invited you') >> text=Accept")
    bpage.wait_for_url(re.compile(rf".*/portal/session/{sid}$"))
    bpage.wait_for_function("() => window.__view && window.__view.viewer && window.__view.viewer.videoAt", timeout=15000)
    # Alice is told the result.
    apage.wait_for_selector(".toast:has-text('Bob accepted your invite')", timeout=5000)
    # The customer sees both viewers.
    cpage.wait_for_function("() => { const t = document.getElementById('viewers-text').textContent; return t.includes('Alice') && t.includes('Bob'); }", timeout=15000)
    # Alice revokes; Bob's view closes.
    apage.reload()
    apage.click(".share-menu summary")
    apage.click("[data-revoke]")
    bpage.wait_for_selector("text=You no longer have access to this session.", timeout=5000)
    types = psql(f"select event_type from audit_events where session_id='{sid}' order by id").split()
    assert {"session.invite_sent", "session.invite_accepted", "session.invite_revoked"} <= set(types)
    for c in (actx, bctx, cctx):
        c.close()
