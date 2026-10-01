"""Phase 3 in a real browser: customer joins with a code, shares (fake
media), session goes live with audio recorded; iPhone without
getDisplayMedia gets camera only; ending releases capture."""
import re

import pytest

from stack import create_user, login, psql, reset

USED: dict = {}
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1")


@pytest.fixture(autouse=True)
def fresh():
    reset()


def new_ctx(browser, **kw):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900}, **kw)
    p = ctx.new_page()
    p.msgs = []
    p.on("console", lambda m: p.msgs.append(m.text))
    p.on("pageerror", lambda e: p.msgs.append("PAGEERROR " + str(e)))
    p.on("dialog", lambda d: d.accept())
    return ctx, p


def tech_code(browser, base_url):
    tech = create_user("tech@example.com", "Tess Tech")
    ctx, page = new_ctx(browser)
    login(page, base_url, tech, USED)
    info = page.evaluate("""async () => {
        const r = await fetch('/api/sessions', {method: 'POST', headers: {'X-CSRF-Token': document.body.dataset.csrf}});
        return await r.json(); }""")
    return ctx, page, info


def enter(page, base_url, path, code, name="Cathy Customer", company="Acme Pty Ltd"):
    page.goto(base_url + path)
    page.locator(".code-box").first.click()
    page.keyboard.type(code.lower())
    page.fill("input[name=name]", name)
    page.fill("input[name=company]", company)


def session_row(sid):
    out = psql(f"select status, coalesce(audio_available::text,'null'), coalesce(end_reason,''), share_type, os, browser "
               f"from sessions where id = '{sid}'").strip()
    return out.split("|")


def test_screen_share_goes_live_and_records_audio(browser, base_url):
    tctx, tpage, info = tech_code(browser, base_url)
    ctx, page = new_ctx(browser)
    enter(page, base_url, "/screen", info["code"])
    # The boxes hold one uppercase letter each.
    assert "".join(page.locator(".code-box").evaluate_all("els => els.map(e => e.value)")) == info["code"]
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/s$"))
    assert session_row(info["session_id"])[0] == "waiting"
    frame = page.frame_locator(".frame-wrap iframe").first
    frame.locator("text=SELECT SCREEN TO SHARE").click(force=True)
    page.wait_for_function("() => [...window.__connect.publishers.values()].some(p => p.live)", timeout=20000)
    page.wait_for_function(f"() => true", timeout=1000)
    for _ in range(20):
        row = session_row(info["session_id"])
        if row[0] == "live" and row[1] != "null":
            break
        page.wait_for_timeout(500)
    assert row[0] == "live" and row[1] in ("true", "false"), row
    assert row[3] == "screen"
    assert "You are sharing your screen" in page.inner_text("#status-text")
    events = psql(f"select event_type from audit_events where session_id = '{info['session_id']}' order by id").split()
    assert "session.stream_started" in events

    # Keep references to the capture tracks, then end from the customer side.
    n = page.evaluate("""() => { window.__held = [];
        document.querySelectorAll('.frame-wrap iframe').forEach(f => { const s = f.contentWindow.session;
          [s.streamSrc, s.screenStream].forEach(st => st && st.getTracks().forEach(t => window.__held.push(t))); });
        return window.__held.length; }""")
    assert n > 0
    page.click("#end-session")
    page.wait_for_selector("#ended:not([hidden])", timeout=10000)
    page.wait_for_function("() => window.__held.every(t => t.readyState === 'ended')", timeout=10000)
    assert session_row(info["session_id"])[0:3] == ["ended", row[1], "customer_ended"]
    # The old customer token no longer works.
    page.goto(base_url + "/s")
    assert "Session ended" in page.content()
    assert [m for m in page.msgs if "Content Security Policy" in m and "/vdo/" not in m] == []
    ctx.close(); tctx.close()


def test_iphone_gets_camera_only(browser, base_url):
    tctx, tpage, info = tech_code(browser, base_url)
    ctx, page = new_ctx(browser, user_agent=IPHONE_UA, is_mobile=True, has_touch=True)
    ctx.add_init_script("try { delete MediaDevices.prototype.getDisplayMedia; } catch (e) {}")
    page.goto(base_url + "/")
    assert page.evaluate("!!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia)") is False
    assert page.locator("#choose-screen").is_hidden()
    assert page.locator("#no-screen-note").is_visible()
    # Even the /screen link turns into a camera share.
    enter(page, base_url, "/screen", info["code"])
    assert page.locator("h1").inner_text() == "Share your camera"
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/s$"))
    try:
        page.wait_for_function("() => [...window.__connect.publishers.values()].some(p => p.live)", timeout=20000)
    except Exception:
        print("FRAME TEXT", page.frames[1].evaluate("document.body.innerText")[:500])
        print("MSGS", [m[:200] for m in page.msgs][-20:])
        raise
    row = session_row(info["session_id"])
    assert row[0] == "live" and row[3] == "camera" and row[4] == "ios" and row[5] == "safari", row
    assert "iPhone or iPad" in page.inner_text("#instr-title")
    ctx.close(); tctx.close()


def test_code_box_rejects_i_l_o(browser, base_url):
    ctx, page = new_ctx(browser)
    page.goto(base_url + "/screen")
    page.locator(".code-box").first.click()
    page.keyboard.type("l")
    assert page.locator(".code-box").first.input_value() == ""
    assert "never use the letter L" in page.inner_text("#code-hint")
    ctx.close()
