import pytest

from app.services import branding


def test_contrast_and_text_colour():
    assert round(branding.contrast("#000000", "#ffffff")) == 21
    assert branding.text_on("#1f4e79") == "#ffffff"
    assert branding.text_on("#f0f000") == "#111111"
    assert branding.contrast_warnings("#1f4e79", "#f0a020") == []
    assert branding.contrast_warnings("#dddddd", "#f0a020")


@pytest.mark.parametrize("v,ok", [("#1f4e79", True), ("#FFF", False), ("red", False),
                                  ("#1f4e79;}body{display:none", False)])
def test_colour_validation(v, ok):
    if ok:
        assert branding.clean_colour(v) == v.lower()
    else:
        with pytest.raises(branding.BrandingError):
            branding.clean_colour(v)


def test_url_validation():
    assert branding.clean_url("https://example.com/privacy") == "https://example.com/privacy"
    for bad in ("http://example.com", "javascript:alert(1)", "https://", 'https://x.com/"onmouseover'):
        with pytest.raises(branding.BrandingError):
            branding.clean_url(bad)


def test_svg_sanitiser_strips_active_content():
    evil = b"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" onload="alert(1)" viewBox="0 0 10 10">
      <script>alert(1)</script><style>*{}</style>
      <foreignObject><div>x</div></foreignObject>
      <a xlink:href="javascript:alert(1)"><rect width="5" height="5"/></a>
      <image href="https://evil.example/x.png"/>
      <rect width="10" height="10" fill="url(https://evil.example/x)" onclick="x()" style="fill:red"/>
      <use href="#ok"/><circle id="ok" r="2" fill="#123456"/>
    </svg>"""
    out = branding.sanitise_svg(evil).decode()
    low = out.lower()
    for bad in ("script", "onload", "onclick", "foreignobject", "javascript", "evil.example", "style", "<image"):
        assert bad not in low, (bad, out)
    assert "<circle" in out and 'fill="#123456"' in out


def test_svg_rejects_entities():
    bomb = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>'
    with pytest.raises(branding.BrandingError):
        branding.sanitise_svg(bomb)


def test_upload_type_and_size(tmp_path, monkeypatch):
    from app.config import get_config
    monkeypatch.setattr(get_config(), "media_dir", tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    url = branding.store_upload(png)
    assert url.startswith("/media/branding/logo-") and url.endswith(".png")
    with pytest.raises(branding.BrandingError):
        branding.store_upload(b"GIF89a....")
    with pytest.raises(branding.BrandingError):
        branding.store_upload(b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024 + 1))
