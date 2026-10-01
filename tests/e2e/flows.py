"""Reusable browser flows for e2e tests."""
import re

from stack import create_user, login

PUB_OUTBOUND = """() => (async () => {
  const out = [];
  for (const f of document.querySelectorAll('.frame-wrap iframe')) {
    const s = f.contentWindow.session;
    for (const k in (s.pcs || {})) {
      const r = await s.pcs[k].getStats();
      r.forEach(x => { if (x.type === 'outbound-rtp' && x.kind === 'video')
        out.push({pc: k, w: x.frameWidth, h: x.frameHeight, fps: x.framesPerSecond, bytes: x.bytesSent}); });
    }
  }
  return out;
})()"""

PUB_TRACK = """() => { const f = document.querySelector('.frame-wrap iframe'); const s = f.contentWindow.session;
  const t = (s.streamSrc || s.screenStream).getVideoTracks()[0]; const st = t.getSettings();
  return {w: st.width, h: st.height, fps: st.frameRate}; }"""


def new_ctx(browser, **kw):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 950}, **kw)
    p = ctx.new_page()
    p.msgs = []
    p.on("console", lambda m: p.msgs.append(m.text))
    p.on("pageerror", lambda e: p.msgs.append("PAGEERROR " + str(e)))
    p.on("dialog", lambda d: d.accept())
    return ctx, p


def tech_page(browser, base_url, used, email="tech@example.com", name="Tess Tech", role="tech"):
    user = create_user(email, name, role)
    ctx, page = new_ctx(browser)
    login(page, base_url, user, used)
    return ctx, page, user


def new_code_via_ui(page):
    page.click("#new-session")
    page.wait_for_selector("#code-panel:not([hidden])")
    code = page.inner_text("[data-testid=join-code]").strip()
    assert re.fullmatch(r"[A-Z]{4}", code), code
    return code


def customer_share(browser, base_url, code, share="screen", name="Cathy Customer", company="Acme Pty Ltd"):
    ctx, page = new_ctx(browser)
    page.goto(base_url + ("/screen" if share == "screen" else "/camera"))
    page.locator(".code-box").first.click()
    page.keyboard.type(code)
    page.fill("input[name=name]", name)
    page.fill("input[name=company]", company)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r".*/s$"))
    if share == "screen":
        page.frame_locator(".frame-wrap iframe").first.locator("text=SELECT SCREEN TO SHARE").click(force=True)
    page.wait_for_function("() => [...window.__connect.publishers.values()].some(p => p.live)", timeout=20000)
    return ctx, page
