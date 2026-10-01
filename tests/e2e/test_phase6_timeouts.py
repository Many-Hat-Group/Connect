"""Phase 6 in the browser with the real worker: when the tech disappears,
the customer sees a countdown, then the session ends."""
import pytest

from flows import customer_share, new_code_via_ui, tech_page
from stack import psql, reset, sh

USED: dict = {}


@pytest.fixture(autouse=True)
def fresh():
    reset()
    yield
    reset()  # restores default timeouts


def set_timeouts(**kw):
    import json
    psql("UPDATE settings SET value = value || '" + json.dumps(kw) + "'::jsonb WHERE key = 'timeouts'")
    sh("exec", "-T", "redis", "redis-cli", "-a", "e2e-redis", "--no-auth-warning", "PUBLISH", "settings:invalidate", "x")


def test_tech_absent_countdown_then_end(browser, base_url):
    set_timeouts(tech_absent_seconds=14, tech_absent_warning_seconds=12, presence_ttl_seconds=4,
                 customer_lost_seconds=600)
    tctx, tpage, tech = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    tpage.wait_for_selector(".tile", timeout=10000)
    sid = tpage.locator(".tile").get_attribute("data-session")
    tctx.close()   # the tech closes the portal
    cpage.wait_for_selector("#warning:not([hidden])", timeout=30000)
    assert "will end in" in cpage.inner_text("#warning")
    cpage.wait_for_selector("#ended:not([hidden])", timeout=30000)
    assert "wasn't connected" in cpage.inner_text("#ended-message")
    assert psql(f"select end_reason from sessions where id='{sid}'").strip() == "tech_absent_timeout"
    cctx.close()
