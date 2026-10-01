import base64
import hashlib
import hmac
import os
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import pytest
from jinja2 import Environment

from app.services import codes, detection, instructions, turn_creds, vdo

SNAP = Path(__file__).parent / "snapshots" / "instructions.txt"


# ---------------------------------------------------------------- codes

def test_alphabet():
    assert codes.ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ"
    assert len(codes.ALPHABET) ** 4 == 279_841


def test_generate_uses_only_alphabet_and_is_spread():
    seen = Counter()
    for _ in range(5000):
        c = codes.generate()
        assert len(c) == 4 and set(c) <= set(codes.ALPHABET)
        seen.update(c)
    assert set(seen) == set(codes.ALPHABET)
    assert max(seen.values()) / min(seen.values()) < 1.5


@pytest.mark.parametrize("raw,expected", [
    ("abcd", "ABCD"), (" ab-cd ", "ABCD"), ("ABCI", None), ("ABCL", None), ("ABC0", None),
    ("ABC", None), ("ABCDE", None), ("", None), ("AB D", None),
])
def test_normalise(raw, expected):
    assert codes.normalise(raw) == expected


# ---------------------------------------------------------------- detection

UAS = {
    "win_chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "win_edge": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
    "win_firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "win_arm": "Mozilla/5.0 (Windows NT 10.0; ARM64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "mac_safari": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
    "mac_chrome": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "linux_firefox": "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "chromeos": "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "android": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
    "iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1",
    "iphone_chrome": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/141.0.0.0 Mobile/15E148 Safari/604.1",
    "ipad_old": "Mozilla/5.0 (iPad; CPU OS 16_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
}


@pytest.mark.parametrize("key,os_,browser,cls", [
    ("win_chrome", "windows", "chrome", "desktop"), ("win_edge", "windows", "edge", "desktop"),
    ("win_firefox", "windows", "firefox", "desktop"), ("mac_safari", "macos", "safari", "desktop"),
    ("mac_chrome", "macos", "chrome", "desktop"), ("linux_firefox", "linux", "firefox", "desktop"),
    ("chromeos", "chromeos", "chrome", "desktop"), ("android", "android", "chrome", "mobile"),
    ("iphone", "ios", "safari", "mobile"), ("iphone_chrome", "ios", "chrome", "mobile"),
    ("ipad_old", "ipados", "safari", "tablet"),
])
def test_user_agent(key, os_, browser, cls):
    d = detection.from_user_agent(UAS[key])
    assert (d.os, d.browser, d.device_class) == (os_, browser, cls)


def test_ipad_desktop_mode_detected_by_touch():
    d = detection.detect(UAS["mac_safari"], None, max_touch_points=5)
    assert d.os == "ipados" and d.os_key == "ios"


def test_client_hints_windows_11_and_arm():
    d = detection.detect(UAS["win_chrome"], {"platform": "Windows", "platformVersion": "15.0.0", "architecture": "arm", "bitness": "64"})
    assert d.os_version == "11" and d.arch == "arm64" and d.arch_certain
    d = detection.detect(UAS["win_chrome"], {"platform": "Windows", "platformVersion": "10.0.0", "architecture": "x86", "bitness": "64"})
    assert d.os_version == "10" and d.arch == "x86_64"


def test_mac_arch_unknown_without_hints():
    d = detection.detect(UAS["mac_chrome"])
    assert d.arch == "" and not d.arch_certain
    d = detection.detect(UAS["mac_chrome"], {"platform": "macOS", "architecture": "arm", "bitness": "64"})
    assert d.arch == "arm64"


# ---------------------------------------------------------------- TURN and URLs

def test_turn_credentials_hmac():
    from app.config import get_config
    user, pw = turn_creds.credentials("sess-1", ttl=100, now=1_000_000)
    assert user == "1000100:sess-1"
    expected = base64.b64encode(hmac.new(get_config().turn_shared_secret.encode(), user.encode(), hashlib.sha1).digest()).decode()
    assert pw == expected


def test_publisher_url_is_fragment_and_fully_encoded():
    url = vdo.publisher_url(room="R" * 30, password="P" * 40, stream_id="S" * 24, signal_token="a+b/c=",
                            turn_host="turn.example.com", session_id="sid", share_type="screen", label="Screen 1")
    assert url.startswith("/vdo/#") and "?" not in url.split("#", 1)[1]
    frag = url.split("#", 1)[1]
    parts = dict(p.split("=", 1) if "=" in p else (p, None) for p in frag.split("&"))
    assert unquote(parts["wss2"]).endswith("/signal?t=a+b/c=")
    assert "%2B" in parts["wss2"]  # '+' must never reach VDO.Ninja raw
    assert unquote(parts["label"]) == "Screen_1"
    assert "autostart" not in parts and "screenshare" in parts and parts["audiodevice"] == "0"
    for slot in ("stun", "addstun", "turn"):
        assert "turn.example.com" in unquote(parts[slot])
    assert unquote(parts["addstun"]).endswith("?transport=tcp")


def test_viewer_url_starts_muted_with_scene():
    url = vdo.viewer_url(room="r", password="p", stream_id="s", signal_token="t", turn_host="h", session_id="x")
    frag = url.split("#", 1)[1].split("&")
    assert "scene" in frag and "volume=0" in frag and "view=s" in frag


def test_random_ids():
    assert len(vdo.new_room()) == 30 and vdo.new_room().isalnum()
    assert len(vdo.new_password()) >= 32 and len(vdo.new_stream_id()) == 24
    assert len({vdo.new_stream_id() for _ in range(1000)}) == 1000


# ---------------------------------------------------------------- instructions

TEMPLATE = Environment(autoescape=True).from_string(
    "## {{ key }}\n# {{ e.title }}\n{% for s in e.steps %}- {{ s }}\n{% endfor %}"
    "{% if e.audio %}audio: {{ e.audio }}\n{% endif %}{% if e.note %}note: {{ e.note }}\n{% endif %}"
    "{% if e.unavailable %}unavailable\n{% endif %}")


def render_all() -> str:
    out = []
    for share, os_key, browser in instructions.all_keys():
        e = instructions.lookup(share, os_key, browser)
        assert e["title"] and e["steps"], (share, os_key, browser)
        for text in [e["title"], *e["steps"], e.get("audio") or "", e.get("note") or ""]:
            assert "—" not in text and "–" not in text, f"dash in {share}/{os_key}/{browser}: {text}"
        out.append(TEMPLATE.render(key=f"{share}/{os_key}/{browser}", e=e))
    return "\n".join(out)


def test_instructions_snapshot():
    rendered = render_all()
    if os.environ.get("UPDATE_SNAPSHOTS") or not SNAP.exists():
        SNAP.write_text(rendered)
    assert rendered == SNAP.read_text(), "instructions changed: review, then run with UPDATE_SNAPSHOTS=1"


@pytest.mark.parametrize("share,os_key,browser", [
    ("screen", "windows", "chrome"), ("screen", "windows", "edge"), ("screen", "windows", "firefox"),
    ("screen", "macos", "chrome"), ("screen", "macos", "edge"), ("screen", "macos", "firefox"), ("screen", "macos", "safari"),
    ("screen", "linux", "chrome"), ("screen", "linux", "firefox"), ("screen", "chromeos", "chrome"),
    ("screen", "android", "chrome"), ("screen", "ios", "safari"), ("camera", "ios", "chrome"),
    ("camera", "windows", "edge"), ("camera", "plan9", "lynx"), ("screen", "plan9", "lynx"),
])
def test_required_combinations_resolve(share, os_key, browser):
    e = instructions.lookup(share, os_key, browser)
    assert e["title"]
    if share == "screen" and os_key == "macos":
        assert any("Screen" in s and "Recording" in s for s in e["steps"])
    if share == "screen" and os_key == "ios":
        assert e.get("unavailable")
    if (share, os_key, browser) == ("screen", "windows", "chrome"):
        assert any("Also share system audio" in s for s in e["steps"])
    if browser == "firefox" and share == "screen":
        assert "can't share sound" in e["audio"]
