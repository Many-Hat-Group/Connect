"""Branding validation, contrast checks and logo uploads."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from defusedxml import ElementTree as DET
from xml.etree import ElementTree as ET

from ..config import get_config

MAX_UPLOAD = 2 * 1024 * 1024
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
TEXT_FIELDS = {
    "product_name": 60, "company_name": 120, "company_short": 20, "support_phone": 40,
    "support_email": 200, "footer_text": 200,
}
URL_FIELDS = ("connect_url", "help_url", "privacy_url")


class BrandingError(ValueError):
    pass


# ---------------------------------------------------------------- colours

def _lum(hex_colour: str) -> float:
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(bg: str) -> str:
    """Black or white text, whichever contrasts better with bg."""
    return "#ffffff" if contrast(bg, "#ffffff") >= contrast(bg, "#111111") else "#111111"


def contrast_warnings(primary: str, accent: str) -> list[str]:
    out = []
    if contrast(primary, text_on(primary)) < 4.5:
        out.append(f"The primary colour has low contrast with its text ({contrast(primary, text_on(primary)):.1f}:1, "
                   "needs 4.5:1 for WCAG AA).")
    if contrast(primary, "#ffffff") < 3:
        out.append(f"The primary colour is hard to see on a white page ({contrast(primary, '#ffffff'):.1f}:1, "
                   "aim for 3:1).")
    if contrast(accent, text_on(accent)) < 4.5:
        out.append(f"The accent colour has low contrast with its text ({contrast(accent, text_on(accent)):.1f}:1).")
    return out


# ---------------------------------------------------------------- fields

def clean_text(field: str, value: str) -> str:
    value = (value or "").strip()
    if len(value) > TEXT_FIELDS[field]:
        raise BrandingError(f"{field.replace('_', ' ').capitalize()} is too long.")
    if any(ch in value for ch in "<>\x00"):
        raise BrandingError(f"{field.replace('_', ' ').capitalize()} contains characters that aren't allowed.")
    return value


def clean_colour(value: str) -> str:
    value = (value or "").strip()
    if not HEX.match(value):
        raise BrandingError("Colours must be written like #1f4e79.")
    return value.lower()


def clean_url(value: str, allow_empty: bool = True) -> str:
    value = (value or "").strip()
    if not value and allow_empty:
        return ""
    u = urlsplit(value)
    if u.scheme != "https" or not u.netloc or any(c in value for c in "<>\"' "):
        raise BrandingError(f"{value} is not a valid https:// address.")
    return value


def clean_host(value: str, allow_empty: bool = True) -> str:
    value = (value or "").strip().lower()
    if not value and allow_empty:
        return ""
    if not re.fullmatch(r"[a-z0-9.-]+(:[0-9]{1,5})?", value) or ".." in value:
        raise BrandingError(f"{value} is not a valid host name.")
    return value


# ---------------------------------------------------------------- uploads

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_ELEMENTS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "defs", "lineargradient", "radialgradient", "stop", "title", "desc", "clippath", "mask", "symbol",
}
SVG_ATTRS = {
    "viewbox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d",
    "points", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-opacity", "stroke-dasharray", "opacity", "transform", "id", "class",
    "offset", "stop-color", "stop-opacity", "gradientunits", "gradienttransform", "clip-path", "clip-rule",
    "mask", "font-family", "font-size", "font-weight", "text-anchor", "preserveaspectratio", "version",
    "xmlns", "dominant-baseline", "letter-spacing",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def sanitise_svg(data: bytes) -> bytes:
    """Allow-list SVG sanitiser: drops scripts, event handlers, styles,
    foreign content, external references and anything not on the lists."""
    try:
        root = DET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as e:
        raise BrandingError("That SVG file could not be read.") from e
    if _local(root.tag) != "svg":
        raise BrandingError("That file is not an SVG image.")

    def clean(el: ET.Element) -> None:
        for child in list(el):
            if not isinstance(child.tag, str) or _local(child.tag) not in SVG_ELEMENTS:
                el.remove(child)
                continue
            clean(child)
        for attr in list(el.attrib):
            name = _local(attr)
            val = el.attrib[attr]
            if name == "href" and val.startswith("#"):
                continue
            if name not in SVG_ATTRS or "url(" in val.lower().replace(" ", "") and not val.strip().startswith("url(#"):
                del el.attrib[attr]
            elif "javascript:" in val.lower() or "data:" in val.lower():
                del el.attrib[attr]

    clean(root)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8")


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        return "svg"
    return None


def store_upload(data: bytes, kind: str = "logo") -> str:
    """Validate and store a branding image; returns its public URL path."""
    if len(data) > MAX_UPLOAD:
        raise BrandingError("Images must be 2 MB or smaller.")
    if not data:
        raise BrandingError("The file is empty.")
    ext = sniff(data)
    if ext is None:
        raise BrandingError("Upload a PNG, JPEG or SVG image.")
    if ext == "svg":
        data = sanitise_svg(data)
    digest = hashlib.sha256(data).hexdigest()[:32]
    d = get_config().media_dir / "branding"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{kind}-{digest}.{ext}"
    path = d / name
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return f"/media/branding/{name}"


MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml"}


def media_path(name: str) -> tuple[Path, str] | None:
    if not re.fullmatch(r"(logo|favicon)-[0-9a-f]{32}\.(png|jpg|svg)", name):
        return None
    p = get_config().media_dir / "branding" / name
    if not p.is_file():
        return None
    return p, MEDIA_TYPES[name.rsplit(".", 1)[1]]
