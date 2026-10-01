"""Phase 7 in the browser: tech requests remote control, the customer sees
the prompt with the right download, gets the renamed exe, can decline."""
import pytest

from flows import customer_share, new_code_via_ui, tech_page
from stack import psql, reset, sh

USED: dict = {}
SEED = r'''
import asyncio, hashlib
from pathlib import Path
from app import db
from app.models import RustdeskRelease, utcnow
from app.services import rustdesk, settings as st
async def m():
    d = Path("/data/rustdesk/1.5.0"); d.mkdir(parents=True, exist_ok=True)
    assets = []
    for name in ["rustdesk-1.5.0-x86_64.exe", "rustdesk-1.5.0-x86_64.deb", "rustdesk-1.5.0-aarch64.dmg"]:
        body = (name + " test").encode(); (d / name).write_bytes(body)
        assets += rustdesk.map_assets([{"name": name, "size": len(body)}])
    for a in assets: a["sha256"] = "test"; a["verified_by"] = "github_digest"; a.pop("url", None); a.pop("digest", None)
    async with db.sessionmaker()() as s:
        s.add(RustdeskRelease(version="1.5.0", tag="1.5.0", status="active", assets=assets, fetched_at=utcnow()))
        await s.commit()
        await st.update(s, "rustdesk", {"host": "rd.example.com", "key": "AbC123=", "relay": ""})
asyncio.run(m())
'''


@pytest.fixture(autouse=True)
def fresh():
    reset()
    sh("exec", "-T", "-u", "root", "app", "python", "-c", SEED)


def test_prompt_download_and_decline(browser, base_url):
    tctx, tpage, tech = tech_page(browser, base_url, USED)
    code = new_code_via_ui(tpage)
    cctx, cpage = customer_share(browser, base_url, code)
    tpage.wait_for_selector(".tile", timeout=10000)
    tpage.click(".tile")
    tpage.wait_for_selector("#rustdesk:not([hidden])")
    tpage.click("#rustdesk")
    cpage.wait_for_selector(".modal >> text=would like to control your computer", timeout=5000)
    assert "Only continue if you are on the phone with" in cpage.inner_text(".modal")
    btn = cpage.locator("#rd-download")
    assert "Download for Linux" in btn.inner_text()      # headless Chromium reports Linux x86_64
    assert "rd.example.com" in cpage.inner_text(".modal")  # non-Windows: server details to type in
    with cpage.expect_download() as dl:
        cpage.click("#rd-download")
    assert dl.value.suggested_filename == "rustdesk-1.5.0-x86_64.deb"
    # The Windows build carries the server details in its file name.
    with cpage.expect_download() as dl2:
        cpage.evaluate("() => { const a = document.createElement('a'); a.href = '/dl/rustdesk/windows/x86_64'; document.body.appendChild(a); a.click(); }")
    assert dl2.value.suggested_filename == "rustdesk-host=rd.example.com,key=AbC123=,.exe"
    tpage.wait_for_selector(".toast:has-text('is downloading RustDesk')", timeout=5000)
    cpage.click("#rd-decline")
    tpage.wait_for_selector(".toast:has-text('declined remote control')", timeout=5000)
    sid = tpage.url.rsplit("/", 1)[1]
    events = psql(f"select event_type from audit_events where session_id='{sid}'").split()
    assert {"session.rustdesk_prompted", "session.rustdesk_downloaded", "session.rustdesk_declined"} <= set(events)
    tctx.close(); cctx.close()
