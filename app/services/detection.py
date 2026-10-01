"""Device detection from the User-Agent plus User-Agent Client Hints.

Used only to pick instruction text, record OS and browser, and choose the
RustDesk download. Whether screen sharing is offered is decided in the
browser by feature detection (navigator.mediaDevices.getDisplayMedia).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass
class Device:
    os: str = "unknown"          # windows macos linux chromeos android ios ipados unknown
    os_version: str = ""
    browser: str = "unknown"     # chrome edge firefox safari samsung opera unknown
    browser_version: str = ""
    device_class: str = "desktop"  # desktop mobile tablet
    arch: str = ""               # x86_64 arm64 x86 armv7 ""(unknown)
    arch_certain: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def os_key(self) -> str:
        return "ios" if self.os == "ipados" else self.os


def _ver(m: re.Match | None, group: int = 1) -> str:
    return m.group(group).replace("_", ".") if m else ""


def from_user_agent(ua: str) -> Device:
    ua = ua or ""
    d = Device()

    # ---- OS
    if m := re.search(r"Windows NT ([\d.]+)", ua):
        d.os, d.os_version = "windows", {"10.0": "10 or 11", "6.3": "8.1", "6.2": "8", "6.1": "7"}.get(m.group(1), m.group(1))
        if re.search(r"ARM64|aarch64", ua):
            d.arch, d.arch_certain = "arm64", True
        elif re.search(r"Win64|x64|WOW64|amd64", ua, re.I):
            d.arch, d.arch_certain = "x86_64", False  # ARM64 Windows also reports x64 in the UA
        else:
            d.arch = "x86"
    elif m := re.search(r"(iPhone|iPod)(?:.*?OS ([\d_]+))?", ua):
        d.os, d.os_version, d.device_class, d.arch, d.arch_certain = "ios", _ver(m, 2), "mobile", "arm64", True
    elif m := re.search(r"iPad(?:.*?OS ([\d_]+))?", ua):
        d.os, d.os_version, d.device_class, d.arch, d.arch_certain = "ipados", _ver(m), "tablet", "arm64", True
    elif m := re.search(r"Android ([\d.]+)", ua):
        d.os, d.os_version = "android", m.group(1)
        d.device_class = "mobile" if "Mobile" in ua else "tablet"
        d.arch = "arm64"
    elif "CrOS" in ua:
        d.os = "chromeos"
        d.os_version = _ver(re.search(r"CrOS \S+ ([\d.]+)", ua))
    elif m := re.search(r"Mac OS X ([\d_.]+)", ua):
        d.os, d.os_version = "macos", _ver(m)
        # UA is frozen at 10.15.7 and does not reveal Apple chip vs Intel.
        d.arch = ""
    elif re.search(r"Linux|X11", ua):
        d.os = "linux"
        if re.search(r"x86_64|amd64", ua):
            d.arch, d.arch_certain = "x86_64", True
        elif re.search(r"aarch64|arm64", ua):
            d.arch, d.arch_certain = "arm64", True
        elif re.search(r"armv7", ua):
            d.arch, d.arch_certain = "armv7", True

    # ---- Browser (order matters: Edge and Opera also say Chrome; Chrome also says Safari)
    if m := re.search(r"Edg(?:e|A|iOS)?/([\d.]+)", ua):
        d.browser, d.browser_version = "edge", m.group(1)
    elif m := re.search(r"(?:OPR|Opera)/([\d.]+)", ua):
        d.browser, d.browser_version = "opera", m.group(1)
    elif m := re.search(r"SamsungBrowser/([\d.]+)", ua):
        d.browser, d.browser_version = "samsung", m.group(1)
    elif m := re.search(r"(?:Firefox|FxiOS)/([\d.]+)", ua):
        d.browser, d.browser_version = "firefox", m.group(1)
    elif m := re.search(r"(?:Chrome|CriOS)/([\d.]+)", ua):
        d.browser, d.browser_version = "chrome", m.group(1)
    elif m := re.search(r"Version/([\d.]+).*Safari/", ua):
        d.browser, d.browser_version = "safari", m.group(1)
    return d


def apply_client_hints(d: Device, hints: dict | None, max_touch_points: int = 0) -> Device:
    """Refine with navigator.userAgentData.getHighEntropyValues(...) results
    and the touch point count (to spot iPadOS reporting as a Mac)."""
    hints = hints or {}
    platform = (hints.get("platform") or "").lower()
    pv = str(hints.get("platformVersion") or "")
    arch = (hints.get("architecture") or "").lower()
    bitness = str(hints.get("bitness") or "")
    if platform == "windows":
        d.os = "windows"
        try:
            # platformVersion major >= 13 means Windows 11.
            d.os_version = "11" if int(pv.split(".")[0]) >= 13 else "10"
        except ValueError:
            pass
    elif platform == "macos":
        d.os = "macos"
        if pv:
            d.os_version = pv
    elif platform == "chrome os" or platform == "chromeos":
        d.os = "chromeos"
    elif platform == "android":
        d.os = "android"
        if pv:
            d.os_version = pv
    elif platform == "linux":
        d.os = "linux"
    if hints.get("mobile") is True and d.device_class == "desktop":
        d.device_class = "mobile"
    if arch:
        if arch == "arm":
            d.arch = "arm64" if bitness == "64" else "armv7"
        elif arch == "x86":
            d.arch = "x86_64" if bitness == "64" else "x86"
        d.arch_certain = True
    if d.os == "macos" and max_touch_points > 1:
        d.os, d.device_class, d.arch, d.arch_certain = "ipados", "tablet", "arm64", True
    return d


def detect(ua: str, hints: dict | None = None, max_touch_points: int = 0) -> Device:
    return apply_client_hints(from_user_agent(ua), hints, max_touch_points)
