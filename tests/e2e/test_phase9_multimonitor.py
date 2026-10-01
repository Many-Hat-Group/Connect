"""Phase 9: two screen streams in one session, selector, only the selected
stream at full resolution, stopping one keeps the session live."""
import re
import time

import pytest

from flows import customer_share, new_code_via_ui, tech_page
from stack import psql, reset

USED: dict = {}
PER_FRAME = """() => (async () => {
  const out = {};
  for (const f of document.querySelectorAll('.frame-wrap iframe')) {
    const label = f.closest('.frame-wrap').querySelector('.frame-label span').textContent;
    const s = f.contentWindow.session; out[label] = [];
    for (const k in (s.pcs || {})) {
      if (s.pcs[k].connectionState !== 'connected') continue;
      const r = await s.pcs[k].getStats();
      r.forEach(x => { if (x.type === 'outbound-rtp' && x.kind === 'video') out[label].push({w: x.frameWidth, fps: x.framesPerSecond}); });
    }
  }
  return out;
})()"""


@pytest.fixture(autouse=True)
def fresh():
    reset()


def wait_until(fn, timeout=25, step=0.3):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"not met; last={last!r}")


def widths(cpage):
    return {k: [o["w"] for o in v if o["w"]] for k, v in cpage.evaluate(PER_FRAME).items()}


def test_two_screens_selector_and_stop_one(browser, base_url):
    tctx, tpage, _ = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    # Second screen.
    cpage.click("#add-screen")
    cpage.wait_for_function("() => document.querySelectorAll('.frame-wrap iframe').length === 2", timeout=10000)
    cpage.locator(".frame-wrap iframe").nth(1).scroll_into_view_if_needed()
    cpage.frame_locator(".frame-wrap iframe").nth(1).locator("text=SELECT SCREEN TO SHARE").click(force=True)
    cpage.wait_for_function("() => [...window.__connect.publishers.values()].filter(p => p.live).length === 2", timeout=20000)
    assert "2 screens" in cpage.inner_text("#status-text")
    assert cpage.locator(".frame-stop:visible").count() == 2
    # Dashboard tile shows both side by side.
    tpage.wait_for_selector(".tile .tile-media.multi", timeout=10000)
    tpage.wait_for_function("() => document.querySelectorAll('.tile .thumb-slot').length === 2")
    # Session view: selector with both; Screen 1 full, Screen 2 at thumbnail size.
    tpage.click(".tile")
    tpage.wait_for_url(re.compile(r".*/portal/session/.*"))
    tpage.wait_for_selector("#stream-tabs:not([hidden]) button >> nth=1")
    src_w = cpage.evaluate("() => { const s = document.querySelector('.frame-wrap iframe').contentWindow.session; return (s.streamSrc || s.screenStream).getVideoTracks()[0].getSettings().width; }")
    wait_until(lambda: (w := widths(cpage)) and w.get("Screen 1") == [src_w] and w.get("Screen 2") and max(w["Screen 2"]) <= 320 and w)
    # Switch to Screen 2: roles swap.
    tpage.click("#stream-tabs button:has-text('Screen 2')")
    wait_until(lambda: (w := widths(cpage)) and w.get("Screen 2") == [src_w] and w.get("Screen 1") and max(w["Screen 1"]) <= 320 and w)
    # Stop Screen 1 only: the session stays live on Screen 2.
    cpage.click(".frame-stop:has-text('Screen 1')")
    cpage.wait_for_function("() => document.querySelectorAll('.frame-wrap iframe').length === 1", timeout=10000)
    sid = tpage.url.rsplit("/", 1)[1]
    wait_until(lambda: psql(f"select count(*) from session_streams where session_id='{sid}' and status='live'").strip() == "1")
    assert psql(f"select status from sessions where id='{sid}'").strip() == "live"
    tpage.wait_for_function("() => document.getElementById('stream-tabs').hidden", timeout=10000)
    assert tpage.evaluate("() => window.__view.viewer && !!window.__view.viewer.videoAt")
    # Audio is only taken from the first stream.
    assert psql(f"select string_agg(label || ':' || coalesce(audio_available::text,'null'), ',' order by started_at) from session_streams where session_id='{sid}'").strip().startswith("Screen 1:")
    tctx.close(); cctx.close()
