"""Phase 4: dashboard tiles and thumbnails, session view presets, audio,
hidden-tab pausing, kill."""
import re
import time

import pytest

from flows import PUB_OUTBOUND, PUB_TRACK, customer_share, new_code_via_ui, new_ctx, tech_page
from stack import psql, reset

USED: dict = {}


@pytest.fixture(autouse=True)
def fresh():
    reset()


def wait_until(fn, timeout=20, step=0.25):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"condition not met, last value {last!r}")


def outbound(cpage):
    return cpage.evaluate(PUB_OUTBOUND)


def test_tile_thumbnail_within_5s_and_session_view(browser, base_url):
    tctx, tpage, tech = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    assert tpage.locator("#pending li").count() == 1
    cctx, cpage = customer_share(browser, base_url, code)
    live_at = time.time() * 1000
    # Tile appears and its thumbnail receives video within 5 s of going live.
    tpage.wait_for_selector(".tile", timeout=5000)
    tpage.wait_for_function("""() => { for (const t of window.__dash.tiles.values())
        for (const v of t.thumbs.values()) if (v && v.videoAt) return true; return false; }""", timeout=5000)
    video_at = tpage.evaluate("""() => { for (const t of window.__dash.tiles.values())
        for (const v of t.thumbs.values()) if (v && v.videoAt) return v.videoAt; }""")
    assert video_at - live_at < 5000
    tile_text = tpage.inner_text(".tile")
    assert "Cathy Customer" in tile_text and "Acme Pty Ltd" in tile_text and "Live" in tile_text
    # The thumbnail is a low-bitrate, small viewer.
    # The first frames go out at source size until the viewer's vw=320
    # request reaches the publisher.
    thumb = wait_until(lambda: [o for o in outbound(cpage) if o["w"] and o["w"] <= 320], timeout=15)
    assert thumb
    # The customer sees who is viewing.
    cpage.wait_for_function("() => document.getElementById('viewers-text').textContent.includes('Tess Tech')", timeout=15000)

    # Open the session view: the dashboard thumbnail closes, a full view opens.
    tpage.click(".tile")
    tpage.wait_for_url(re.compile(r".*/portal/session/.*"))
    tpage.wait_for_function("() => window.__view && window.__view.viewer && window.__view.viewer.videoAt", timeout=15000)
    src = cpage.evaluate(PUB_TRACK)
    full = wait_until(lambda: [o for o in outbound(cpage) if o["w"] and o["w"] >= src["w"] * 0.95])
    assert len(outbound(cpage)) == 1, outbound(cpage)  # thumbnail connection was closed
    assert full[0]["w"] == src["w"], (full, src)        # native resolution

    # Presets: Smooth halves the resolution; Clear caps the framerate at 5.
    tpage.select_option("#preset", "smooth")
    half = wait_until(lambda: [o for o in outbound(cpage) if o["w"] and o["w"] <= src["w"] * 0.55])
    assert half
    tpage.select_option("#preset", "clear")
    wait_until(lambda: [o for o in outbound(cpage) if o["w"] == src["w"]])
    slow = wait_until(lambda: [o for o in outbound(cpage) if o["fps"] is not None and o["fps"] <= 6], timeout=25)
    assert slow
    tpage.select_option("#preset", "balanced")
    fast = wait_until(lambda: [o for o in outbound(cpage) if o["fps"] and o["fps"] > 8], timeout=25)
    assert fast
    sid = tpage.url.rsplit("/", 1)[1]
    events = psql(f"select event_type || ':' || coalesce(data->>'preset','') from audit_events where session_id='{sid}' order by id").split()
    assert "session.viewed:" in events and "session.quality_changed:smooth" in events and "session.quality_changed:clear" in events
    tctx.close(); cctx.close()


def test_audio_starts_muted_and_unmutes(browser, base_url):
    tctx, tpage, tech = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    tpage.wait_for_selector(".tile", timeout=10000)
    tpage.click(".tile")
    tpage.wait_for_function("() => window.__view && window.__view.viewer && window.__view.viewer.audioTrack", timeout=15000)

    def volume():
        st = tpage.evaluate("() => window.__view.viewer.request({getDetailedState: true})")
        vals = [v.get("videoVolume") for v in (st or {}).get("detailedState", {}).values() if isinstance(v, dict)]
        return vals[0] if vals else None
    assert wait_until(lambda: volume() is not None and [volume()])[0] == 0
    assert tpage.inner_text("#mute") == "Unmute"
    tpage.click("#mute")
    assert abs(wait_until(lambda: (volume() or 0) > 0 and [volume()])[0] - 0.8) < 0.01
    sid = tpage.url.rsplit("/", 1)[1]
    assert "session.audio_unmuted" in psql(f"select event_type from audit_events where session_id='{sid}'")
    tctx.close(); cctx.close()


def test_hidden_tab_pauses_thumbnails(browser, base_url):
    tctx, tpage, tech = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    tpage.wait_for_function("""() => { for (const t of window.__dash.tiles.values())
        for (const v of t.thumbs.values()) if (v && v.videoAt) return true; return false; }""", timeout=10000)
    wait_until(lambda: len(outbound(cpage)) == 1)
    tpage.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true});
        Object.defineProperty(document, 'hidden', {value: true, configurable: true});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    # The thumbnail viewer is gone: no connection, so no inbound video bytes.
    wait_until(lambda: tpage.evaluate("() => document.querySelectorAll('.tile iframe').length") == 0)
    wait_until(lambda: len([o for o in outbound(cpage)]) == 0, timeout=20)
    tpage.evaluate("""() => { Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
        Object.defineProperty(document, 'hidden', {value: false, configurable: true});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    tpage.wait_for_function("() => document.querySelectorAll('.tile iframe').length === 1", timeout=10000)
    tctx.close(); cctx.close()


def test_kill_tears_down_customer_and_old_token_fails(browser, base_url):
    tctx, tpage, tech = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    cpage.evaluate("""() => { window.__held = []; document.querySelectorAll('.frame-wrap iframe').forEach(f => {
        const s = f.contentWindow.session; [s.streamSrc, s.screenStream].forEach(st => st && st.getTracks().forEach(t => window.__held.push(t))); }); }""")
    tpage.wait_for_selector(".tile", timeout=10000)
    tpage.click(".tile")
    tpage.wait_for_url(re.compile(r".*/portal/session/.*"))
    sid = tpage.url.rsplit("/", 1)[1]
    tpage.click("#kill")
    tpage.check("#kill-block")
    tpage.click("#kill-confirm")
    cpage.wait_for_selector("#ended:not([hidden])", timeout=5000)
    cpage.wait_for_function("() => window.__held.every(t => t.readyState === 'ended')", timeout=5000)
    row = psql(f"select status, end_reason from sessions where id='{sid}'").strip()
    assert row == "ended|tech_killed"
    # Reconnecting with the old token fails: page and websocket.
    cpage.goto(base_url + "/s")
    assert "Session ended" in cpage.content()
    code_ws = cpage.evaluate("""() => new Promise(res => { const ws = new WebSocket(`wss://${location.host}/ws/customer`);
        ws.onclose = e => res(e.code); ws.onmessage = () => res('message'); setTimeout(() => res('timeout'), 5000); })""")
    assert code_ws != "message"
    events = psql(f"select event_type from audit_events where session_id='{sid}' order by id").split()
    assert "session.ip_blocked" in events
    # The blocked IP cannot claim a new code for an hour.
    code2 = None
    tpage.goto(base_url + "/portal")
    code2 = new_code_via_ui(tpage)
    c2ctx, c2 = new_ctx(browser)
    c2.goto(base_url + "/screen")
    c2.locator(".code-box").first.click(); c2.keyboard.type(code2)
    c2.fill("input[name=name]", "X"); c2.fill("input[name=company]", "Y")
    c2.click("button[type=submit]")
    assert "didn't work" in c2.inner_text("#form-error")
    tctx.close(); cctx.close(); c2ctx.close()


def test_one_portal_websocket_per_page(browser, base_url):
    ctx, page, _ = tech_page(browser, base_url, USED)
    sockets = []
    page.on("websocket", lambda ws: sockets.append(ws.url))
    page.goto(base_url + "/portal")
    page.wait_for_timeout(2000)
    assert [u for u in sockets if u.endswith("/ws/portal")] == [f"wss://{base_url.split('://')[1]}/ws/portal"]
    assert page.locator(".sound-prompt").count() == 1
    ctx.close()
