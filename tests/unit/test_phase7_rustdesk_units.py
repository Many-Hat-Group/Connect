import pytest

from app.services import rustdesk
from app.services.detection import detect
from app.services.rustdesk_prompt import choose

V = "1.5.0"
# Asset names as produced by rustdesk 1.5.0 .github/workflows/flutter-build.yml
NAMES = [
    f"rustdesk-{V}-x86_64.exe", f"rustdesk-{V}-x86_64.msi", f"rustdesk-{V}-aarch64.exe", f"rustdesk-{V}-x86-sciter.exe",
    f"rustdesk-{V}-aarch64.dmg", f"rustdesk-{V}-x86_64.dmg",
    f"rustdesk-{V}-x86_64.deb", f"rustdesk-{V}-aarch64.deb", f"rustdesk-{V}-armv7-sciter.deb",
    f"rustdesk-unattended-wayland-{V}-x86_64.deb",
    f"rustdesk-{V}-0.x86_64.rpm", f"rustdesk-{V}-0.aarch64.rpm", f"rustdesk-{V}-0.x86_64-suse.rpm",
    f"rustdesk-{V}-x86_64.AppImage", f"rustdesk-{V}-aarch64.AppImage", f"rustdesk-{V}-x86_64.flatpak",
    f"rustdesk-{V}-universal.apk", f"rustdesk-{V}-aarch64.apk", f"rustdesk-{V}-armv7.apk", f"rustdesk-{V}-x86_64.apk",
    f"rustdesk-{V}-unsigned.tar.gz", f"rustdesk-{V}-x86_64.pkg.tar.zst",
]


def test_asset_mapping_regex_table():
    mapped = {(a["platform"], a["arch"], a["kind"]): a["filename"] for a in rustdesk.map_assets([{"name": n} for n in NAMES])}
    assert mapped == {
        ("windows", "x86_64", "exe"): f"rustdesk-{V}-x86_64.exe",
        ("windows", "x86_64", "msi"): f"rustdesk-{V}-x86_64.msi",
        ("windows", "arm64", "exe"): f"rustdesk-{V}-aarch64.exe",
        ("macos", "arm64", "dmg"): f"rustdesk-{V}-aarch64.dmg",
        ("macos", "x86_64", "dmg"): f"rustdesk-{V}-x86_64.dmg",
        ("linux", "x86_64", "deb"): f"rustdesk-{V}-x86_64.deb",
        ("linux", "arm64", "deb"): f"rustdesk-{V}-aarch64.deb",
        ("linux", "x86_64", "rpm"): f"rustdesk-{V}-0.x86_64.rpm",
        ("linux", "arm64", "rpm"): f"rustdesk-{V}-0.aarch64.rpm",
        ("linux", "x86_64", "appimage"): f"rustdesk-{V}-x86_64.AppImage",
        ("linux", "arm64", "appimage"): f"rustdesk-{V}-aarch64.AppImage",
        ("android", "universal", "apk"): f"rustdesk-{V}-universal.apk",
        ("android", "arm64", "apk"): f"rustdesk-{V}-aarch64.apk",
        ("android", "armv7", "apk"): f"rustdesk-{V}-armv7.apk",
    }


def test_windows_filename_carries_server_details():
    name, problem = rustdesk.windows_filename("rd.example.com", "AbC123=", "relay.example.com")
    assert problem is None
    assert name == "rustdesk-host=rd.example.com,key=AbC123=,relay=relay.example.com,.exe"
    name, _ = rustdesk.windows_filename("rd.example.com", "k", "rd.example.com")
    assert name == "rustdesk-host=rd.example.com,key=k,.exe"   # relay same as host is omitted


@pytest.mark.parametrize("key", ["abc/def=", "a:b", "a,b", 'a"b', "a*b"])
def test_windows_filename_fallback_for_invalid_characters(key):
    name, problem = rustdesk.windows_filename("rd.example.com", key)
    assert name == "rustdesk.exe" and problem


W = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
MAC_SAFARI = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15"
LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1"


@pytest.mark.parametrize("ua,hints,touch,expected", [
    (W, {"platform": "Windows", "architecture": "x86", "bitness": "64"}, 0, ("windows", "x86_64", False)),
    (W, None, 0, ("windows", "x86_64", False)),
    (W, {"platform": "Windows", "architecture": "arm", "bitness": "64"}, 0, ("windows", "arm64", False)),
    (MAC, {"platform": "macOS", "architecture": "arm", "bitness": "64"}, 0, ("macos", "arm64", False)),
    (MAC, {"platform": "macOS", "architecture": "x86", "bitness": "64"}, 0, ("macos", "x86_64", False)),
    (MAC_SAFARI, None, 0, ("macos", "arm64", True)),      # no Client Hints: ask, default Apple chip
    (LINUX, None, 0, ("linux", "x86_64", False)),
    (ANDROID, None, 5, ("android", "universal", False)),
])
def test_detection_picks_download(ua, hints, touch, expected):
    c = choose(detect(ua, hints, touch))
    assert c["available"] and (c["platform"], c["arch"], bool(c.get("ask_mac_chip"))) == expected


@pytest.mark.parametrize("ua,touch", [(IPHONE, 5), (MAC_SAFARI, 5)])   # iPhone, and iPad in desktop mode
def test_ios_not_available(ua, touch):
    c = choose(detect(ua, None, touch))
    assert c["available"] is False and "iPhone or iPad" in c["reason"]
