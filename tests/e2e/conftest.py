import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from vdo import CHROMIUM_ARGS

BASE_URL = os.environ.get("CONNECT_TEST_BASE", "https://connect.test:8443")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def browser():
    # Chromium must not use any proxy for the test hostnames.
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    with sync_playwright() as p:
        b = p.chromium.launch(args=CHROMIUM_ARGS, headless=True)
        yield b
        b.close()


class NetLog:
    """Records every request a browser context makes, plus CSP violations."""

    def __init__(self, ctx, base_url):
        self.base_url = base_url
        self.requests: list[str] = []
        self.console: list[str] = []
        ctx.on("request", lambda r: self.requests.append(r.url))

    def attach(self, page):
        page.on("console", lambda m: self.console.append(m.text))

    def external(self):
        return sorted({u for u in self.requests
                       if not u.startswith(self.base_url) and not u.startswith(("data:", "blob:"))})

    def csp_violations(self):
        return [c for c in self.console if "Content Security Policy" in c]

    def save(self, name):
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / f"{name}.netlog.json").write_text(json.dumps(
            {"requests": self.requests, "external": self.external(), "csp_violations": self.csp_violations()},
            indent=2))


def http_get(path, base_url=BASE_URL):
    """Plain HTTPS GET to the test edge without following redirects.
    Playwright's request API resolves names in Node, which does not know
    connect.test, so use the standard library and pin the address."""
    import http.client
    import ssl
    from urllib.parse import urlsplit
    u = urlsplit(base_url)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection("127.0.0.1", u.port or 443, context=ctx, timeout=10)
    conn.request("GET", path, headers={"Host": u.netloc})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


@pytest.fixture
def harness(browser, base_url):
    """A page on our origin that frames VDO.Ninja and records iframe events."""
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1600, "height": 1000})
    log = NetLog(ctx, base_url)
    page = ctx.new_page()
    log.attach(page)
    page.goto(base_url + "/spike/frame.html")
    page.netlog = log
    yield page
    ctx.close()
