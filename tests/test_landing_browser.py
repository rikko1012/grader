"""The signed-out landing page (web/landing.html) in a real browser against a local server.Handler, signed out, at 360, 390 and
1440 px: no sideways scroll, every link and button at least 44 px tall, Sign in goes to /auth/login, Join goes to the Whop URL,
and the page asks for nothing beyond this server and the app's own Google Fonts. Skipped where Playwright or Chromium is missing.
LANDING_SHOTS=<folder> keeps a full-page screenshot per width (landing-<width>.png)."""
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

import server
import whop_auth

sync_api = pytest.importorskip("playwright.sync_api")

JOIN = "https://whop.com/joined/1time-test/"
SIZES = ({"width": 360, "height": 780}, {"width": 390, "height": 844}, {"width": 1440, "height": 900})
FONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}          # the same family request the member pages make
SHOTS = Path(os.environ.get("LANDING_SHOTS") or "")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f"chromium missing: {e}")
        yield b
        b.close()


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)
    monkeypatch.setenv("SESSION_SECRET", "landing-browser-secret")
    monkeypatch.setenv("WHOP_UPGRADE_URL", JOIN)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0])):
        monkeypatch.setattr(server, name, val)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: str(s["width"]))
def test_landing_fits_every_width_with_big_targets_and_the_right_links(browser, base, size):
    ctx = browser.new_context(viewport=size)
    page = ctx.new_page()
    hosts, errors = [], []
    page.on("request", lambda r: hosts.append(urlparse(r.url).hostname))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/", wait_until="load")
    page.wait_for_timeout(300)
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"landing-{size['width']}.png"), full_page=True)
    assert not errors
    assert page.evaluate("document.documentElement.scrollWidth") <= size["width"]
    assert page.evaluate("document.body.scrollWidth") <= size["width"]
    # no element pokes out sideways either (an overflow hidden by clip would still look cut off)
    wide = page.evaluate("""w => [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && (r.right > w + 0.5 || r.left < -0.5) && getComputedStyle(e).position !== 'absolute'
      }).map(e => e.tagName + '.' + e.className)""", size["width"])
    assert not wide, wide
    small = page.evaluate("""() => [...document.querySelectorAll('a[href],button')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && r.height && getComputedStyle(e).visibility !== 'hidden' && (r.height < 44 || r.width < 44)
      }).map(e => e.textContent.trim() + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height))""")
    small = [s for s in small if not s.startswith("Skip to content")]   # the skip link sits off-screen until focused
    assert not small, small
    signin = page.locator("a", has_text="Sign in with Whop").first
    join = page.locator("a", has_text="Join on Whop").first
    assert signin.get_attribute("href") == "/auth/login" and signin.is_visible()
    assert join.get_attribute("href") == JOIN and join.is_visible()
    # both buttons sit in the first screen, on a phone too
    for el in (signin, join):
        assert el.bounding_box()["y"] + el.bounding_box()["height"] <= size["height"]
    assert set(hosts) <= {"127.0.0.1"} | FONT_HOSTS, set(hosts)
    ctx.close()


def test_a_sign_in_error_shows_as_text(browser, base):
    ctx = browser.new_context(viewport=SIZES[1])
    page = ctx.new_page()
    page.goto(base + "/?signin_error=" + "<b>Whop%20said%20no</b>")
    el = page.locator("#signin-error")
    assert el.is_visible() and el.text_content() == "<b>Whop said no</b>"
    ctx.close()
