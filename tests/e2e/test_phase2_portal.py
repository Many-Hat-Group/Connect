"""Phase 2 in a real browser: the wizard end to end under the strict CSP,
and the portal websocket connecting through the router."""
import re

import pytest

from stack import reset, totp_fresh

USED: dict = {}


@pytest.fixture(autouse=True)
def fresh():
    reset()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(ignore_https_errors=True)
    p = ctx.new_page()
    p.console_msgs = []
    p.on("console", lambda m: p.console_msgs.append(m.text))
    p.on("pageerror", lambda e: p.console_msgs.append("PAGEERROR " + str(e)))
    yield p
    ctx.close()


def csp_problems(p):
    return [m for m in p.console_msgs if "Content Security Policy" in m or m.startswith("PAGEERROR")]


def test_wizard_end_to_end_in_browser(page, base_url):
    assert page.goto(base_url + "/setup").status == 404
    page.goto(base_url + "/setup?token=e2e-setup-token")
    page.fill("input[name=email]", "pierce@example.com")
    page.fill("input[name=display_name]", "Pierce")
    page.fill("input[name=password]", "a long admin passphrase")
    page.fill("input[name=confirm]", "a long admin passphrase")
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/mfa/enrol$"))
    assert page.locator(".qr svg").count() == 1
    secret = page.locator("code.secret").text_content().replace(" ", "")
    page.fill("input[name=code]", totp_fresh(secret, USED))
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/mfa/recovery-codes$"))
    assert page.locator("ol.codes li").count() == 10
    page.click("text=I've saved them")
    page.wait_for_url(re.compile(r".*/setup/brand$"))
    page.fill("input[name=company_name]", "Example MSP")
    page.fill("input[name=company_short]", "EMSP")
    page.locator("input[name=primary_colour]").fill("#204060")
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/setup/urls$"))
    page.fill("input[name=connect_url]", base_url)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/setup/integrations$"))
    page.fill("input[name=turn_host]", "turn.test")
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/portal\?welcome=1$"))
    assert "Setup is complete" in page.content()
    # Brand applied via CSS variables, no inline styles needed.
    bg = page.evaluate("getComputedStyle(document.querySelector('.topbar')).backgroundColor")
    assert bg == "rgb(32, 64, 96)"
    assert "Example MSP" in page.title()
    # Portal websocket connects (hello) through edge and router.
    page.wait_for_function("() => performance.getEntriesByType('resource').length > 0")
    hello = page.evaluate("""() => new Promise((res) => {
        const ws = new WebSocket(`wss://${location.host}/ws/portal`);
        ws.onmessage = (e) => { res(JSON.parse(e.data)); ws.close(); };
        ws.onclose = (e) => res({closed: e.code});
        setTimeout(() => res({timeout: true}), 5000);
    })""")
    assert hello.get("type") == "hello" and hello.get("role") == "admin", hello
    # The token is now dead.
    assert page.goto(base_url + "/setup?token=e2e-setup-token").status == 404
    assert csp_problems(page) == [], csp_problems(page)


def test_websocket_rejected_without_login(page, base_url):
    page.goto(base_url + "/login")
    res = page.evaluate("""() => new Promise((res) => {
        const ws = new WebSocket(`wss://${location.host}/ws/portal`);
        ws.onmessage = (e) => res({msg: e.data});
        ws.onclose = (e) => res({closed: e.code});
        setTimeout(() => res({timeout: true}), 5000);
    })""")
    assert "msg" not in res, res
