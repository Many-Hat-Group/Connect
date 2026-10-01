"""Phase 8 in the browser: relay-only health test, and no third-party
requests from customer or tech pages during a full session."""
import re

import pytest

from flows import customer_share, new_code_via_ui, new_ctx, tech_page
from stack import reset

USED: dict = {}


@pytest.fixture(autouse=True)
def fresh():
    reset()


def record(ctx, base_url):
    reqs = []
    ctx.on("request", lambda r: reqs.append(r.url))
    return reqs


def third_party(reqs, base_url):
    return sorted({u for u in reqs if not u.startswith((base_url, "data:", "blob:", "about:"))})


def test_relay_only_health_test(browser, base_url):
    ctx, page, _ = tech_page(browser, base_url, USED, "admin@example.com", "Ada", role="admin")
    page.goto(base_url + "/admin/health")
    for name in ("Database", "Redis", "Router", "Signal server", "Worker"):
        assert page.locator(f'[data-check="{name}"]').inner_text() == "OK", name
    page.click("#relay-run")
    page.wait_for_function("() => ['OK', 'Problem'].includes(document.getElementById('relay-result').textContent)", timeout=15000)
    assert page.inner_text("#relay-result") == "OK", page.inner_text("#relay-detail")
    assert "relay candidates" in page.inner_text("#relay-detail").lower()
    ctx.close()


def test_no_third_party_requests(browser, base_url):
    tctx, tpage, _ = tech_page(browser, base_url, USED)
    treqs = record(tctx, base_url)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    creqs = record(cctx, base_url)
    tpage.wait_for_selector(".tile", timeout=10000)
    tpage.click(".tile")
    tpage.wait_for_url(re.compile(r".*/portal/session/.*"))
    tpage.wait_for_function("() => window.__view && window.__view.viewer && window.__view.viewer.videoAt", timeout=15000)
    cpage.reload()   # customer page reload is also recorded from the start
    cpage.wait_for_selector(".frame-wrap iframe")
    tpage.wait_for_timeout(2000)
    assert treqs and creqs
    assert third_party(treqs, base_url) == []
    assert third_party(creqs, base_url) == []
    tctx.close(); cctx.close()
