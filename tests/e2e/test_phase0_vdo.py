"""Phase 0 verification spike: unmodified, fully self-hosted VDO.Ninja.

Requires the Phase 0 stack: tests/phase0/up.sh up -d
Run: make test-phase0
"""
import secrets

import pytest

from conftest import http_get
from vdo import JS_LOCAL_TRACKS, JS_STATS, JS_VIDEOS, fragment, ice_params, turn_credentials

TURN_SECRET = "phase0-test-secret-not-for-production"
TURN_HOST = "turn.test"
SIGNAL = "connect.test:8443/signal"


ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def rand(n=32):
    return "".join(secrets.choice(ALNUM) for _ in range(n))


class Session:
    def __init__(self, relay=False):
        self.room = rand(32)
        self.password = rand(32)
        self.stream = rand(24)
        self.relay = relay
        self.user, self.cred = turn_credentials(TURN_SECRET, rand(12))

    def common(self, token):
        p = {
            "room": self.room,
            "password": self.password,
            "wss2": f"{SIGNAL}?t={token}",
            **ice_params(TURN_HOST, self.user, self.cred),
            "cleanoutput": None,
            "iframetarget": None,
        }
        if self.relay:
            p["relay"] = None
        return p

    def publisher(self, share="screen", **extra):
        p = dict(self.common("pubtoken"), push=self.stream, autostart=None, label="Screen_1")
        p["screenshare" if share == "screen" else "webcam"] = None
        p.update(extra)
        return "/vdo/" + fragment(p)

    def viewer(self, **extra):
        p = dict(self.common("viewtoken"), view=self.stream, scene=None)
        p.update(extra)
        return "/vdo/" + fragment(p)


def add_frame(page, base_url, fid, path):
    page.evaluate("([i, s]) => addFrame(i, s)", [fid, base_url + path])


def events(page, fid=None):
    ev = page.evaluate("window.__events")
    return [e["data"] for e in ev if fid is None or e["src"] == fid]


def actions(page, fid):
    return [e.get("action") for e in events(page, fid) if isinstance(e, dict)]


def wait_action(page, fid, action, timeout=20000, value=None):
    page.wait_for_function(
        """([fid, a, v]) => window.__events.some(e => e.src === fid && e.data && e.data.action === a
                          && (v === null || e.data.value === v))""",
        arg=[fid, action, value], timeout=timeout)


def wait_video(page, fid, timeout=20000):
    page.wait_for_function(
        """(fid) => { const d = document.getElementById(fid).contentDocument;
                      return d && [...d.querySelectorAll('video')].some(v => v.videoWidth > 0 && !v.paused); }""",
        arg=fid, timeout=timeout)


def start_pair(page, base_url, s, share="screen", pub_extra=None, view_extra=None):
    add_frame(page, base_url, "pub", s.publisher(share, **(pub_extra or {})))
    wait_action(page, "pub", "seeding", value=True)
    add_frame(page, base_url, "view", s.viewer(**(view_extra or {})))
    wait_video(page, "view")


@pytest.mark.parametrize("share", ["screen", "camera"])
def test_publish_and_view_fully_self_hosted(harness, base_url, share):
    s = Session()
    start_pair(harness, base_url, s, share)
    stats = harness.evaluate(JS_STATS, "view")
    video = [i for i in stats["inbound"] if i["kind"] == "video"]
    assert video and video[0]["bytes"] > 0
    # Only our coturn is configured: no Google/Cloudflare STUN, no vdo.ninja TURN.
    flat = [u for urls in stats["iceServers"] for u in (urls if isinstance(urls, list) else [urls])]
    assert flat and all(TURN_HOST in u for u in flat), flat
    harness.wait_for_timeout(2000)
    log = harness.netlog
    log.save(f"phase0-{share}")
    assert log.external() == []
    assert log.csp_violations() == [], log.csp_violations()
    pub_actions = actions(harness, "pub")
    if share == "screen":
        assert "screen-share-state" in pub_actions
    else:
        assert "camera-share" in pub_actions
    assert "push-connection" in pub_actions  # viewer connected, seen by publisher
    assert "new-audio-track-added" in actions(harness, "view")


def test_relay_only_through_coturn(harness, base_url):
    s = Session(relay=True)
    start_pair(harness, base_url, s)
    stats = harness.evaluate(JS_STATS, "view")
    assert stats["iceTransportPolicy"] == "relay"
    assert stats["pairs"] and all(p["local"] == "relay" for p in stats["pairs"]), stats["pairs"]


def test_signal_token_reaches_auth_and_bad_token_is_refused(harness, base_url):
    s = Session()
    good = s.publisher()
    bad = good.replace("t%3Dpubtoken", "t%3Dnotvalid")
    assert bad != good
    add_frame(harness, base_url, "bad", bad)
    harness.wait_for_timeout(6000)
    assert "seeding" not in actions(harness, "bad")
    seen = harness.evaluate("fetch('/spike/seen-tokens').then(r => r.text())").split(",")
    assert "notvalid" in seen  # token survived VDO.Ninja's URL handling intact
    add_frame(harness, base_url, "pub", good)
    wait_action(harness, "pub", "seeding", value=True)
    seen = harness.evaluate("fetch('/spike/seen-tokens').then(r => r.text())").split(",")
    assert "pubtoken" in seen


def test_label_reaches_viewer(harness, base_url):
    s = Session()
    start_pair(harness, base_url, s)
    info = [e for e in events(harness, "view") if e.get("action") == "view-connection-info"]
    assert info and info[0]["value"]["label"] == "Screen 1"


def test_viewer_starts_muted_and_unmutes_via_api(harness, base_url):
    s = Session()
    start_pair(harness, base_url, s, view_extra={"volume": 0})
    vids = harness.evaluate(JS_VIDEOS, "view")
    assert vids[0]["volume"] == 0 or vids[0]["muted"], vids
    harness.evaluate("send('view', {volume: 0.8})")
    harness.wait_for_timeout(1000)
    vids = harness.evaluate(JS_VIDEOS, "view")
    assert abs(vids[0]["volume"] - 0.8) < 0.01 and not vids[0]["muted"], vids


def test_viewer_side_scale_and_bitrate(harness, base_url):
    s = Session()
    start_pair(harness, base_url, s, view_extra={"scale": 100})
    harness.wait_for_timeout(4000)
    full = [i for i in harness.evaluate(JS_STATS, "view")["inbound"] if i["kind"] == "video"][0]
    harness.evaluate("send('view', {scale: 50})")
    harness.wait_for_timeout(6000)
    half = [i for i in harness.evaluate(JS_STATS, "view")["inbound"] if i["kind"] == "video"][0]
    assert half["w"] <= full["w"] * 0.55, (full, half)


def test_thumbnail_params_limit_resolution(harness, base_url):
    s = Session()
    start_pair(harness, base_url, s, view_extra={"bitrate": 150, "vw": 320, "noaudio": None})
    harness.wait_for_timeout(4000)
    st = harness.evaluate(JS_STATS, "view")
    video = [i for i in st["inbound"] if i["kind"] == "video"][0]
    assert video["w"] <= 320, video
    assert not [i for i in st["inbound"] if i["kind"] == "audio" and i["bytes"]], st["inbound"]


def test_publisher_close_releases_capture_and_viewer_sees_end(harness, base_url):
    s = Session()
    start_pair(harness, base_url, s)
    assert any(t["state"] == "live" for t in harness.evaluate(JS_LOCAL_TRACKS, "pub"))
    tracks = harness.evaluate("""(id) => { const s = document.getElementById(id).contentWindow.session;
        window.__held = []; [s.streamSrc, s.screenStream].forEach(st => st && st.getTracks().forEach(t => window.__held.push(t)));
        return window.__held.length; }""", "pub")
    assert tracks > 0
    harness.evaluate("document.getElementById('pub').remove()")
    wait_action(harness, "view", "end-view-connection", timeout=30000)
    states = harness.evaluate("window.__held.map(t => t.readyState)")
    assert all(st == "ended" for st in states), states


def test_hangup_api_and_screen_share_events(harness, base_url):
    s = Session()
    start_pair(harness, base_url, s)
    harness.evaluate("send('pub', {close: true})")
    wait_action(harness, "pub", "hungup", timeout=10000)
    wait_action(harness, "view", "end-view-connection", timeout=30000)


def test_sandboxed_viewer_opaque_origin(harness, base_url):
    """Tech viewer in a sandbox without allow-same-origin: VDO.Ninja runs in an
    opaque origin, so script inside it cannot reach our cookies or APIs."""
    s = Session()
    add_frame(harness, base_url, "pub", s.publisher())
    wait_action(harness, "pub", "seeding", value=True)
    url = base_url + s.viewer().replace("&iframetarget", "")
    harness.evaluate("""(src) => { const f = document.createElement('iframe'); f.id = 'view';
        f.sandbox = 'allow-scripts'; f.allow = 'autoplay; fullscreen'; f.width = 640; f.height = 360;
        f.src = src; document.getElementById('frames').appendChild(f); }""", url)
    harness.wait_for_function("""() => window.__events.some(e => e.src === 'view' && e.data && e.data.action === 'new-video-track-added')""",
                              timeout=20000)
    # Parent cannot touch the frame's DOM (opaque origin), but the API still works.
    with pytest.raises(Exception):
        harness.evaluate("document.getElementById('view').contentDocument.body.innerHTML")
    harness.evaluate("send('view', {getStats: true})")


def test_vdo_without_trailing_slash_redirects(harness, base_url):
    status, headers, _ = http_get("/vdo")
    assert status == 301 and headers["location"].endswith("/vdo/")


def test_vdo_security_headers(harness, base_url):
    status, h, _ = http_get("/vdo/")
    assert status == 200
    assert "frame-ancestors 'self'" in h["content-security-policy"]
    assert h["x-frame-options"] == "SAMEORIGIN"
    assert "display-capture=(self)" in h["permissions-policy"]
    for path in ("/vdo/README.md", "/vdo/.git/config", "/vdo/turnserver_basic.conf"):
        assert http_get(path)[0] == 404


def test_vdo_refuses_top_level_navigation(harness, base_url):
    r = harness.goto(base_url + "/vdo/")
    assert r.status == 403
