"""v3 group D (web/UI_V3.md): the signed-out landing page in v3, and the member-side sign-in gate / account / alerts surfaces.
Real server.Handler, SCOUT_UI_V3=1, fixture data and real-shaped reviews / public record only. 390x844 and 1280x900, dark and light.

The whole existing landing suite (tests/test_landing_browser.py: widths, 44 px targets, both buttons in the first screen, the reviews
carousel's timing, swipe, pause, no layout shift, text-only member content) runs again here with the flag on, through the `base`
fixture below, so v3 keeps every behaviour the page has today. V3_SHOTS=<folder> keeps screenshots."""
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
import whop_auth
from tests import test_landing_browser as L
from tests.test_landing_browser import *  # noqa: F401,F403  (re-runs the landing suite with the flag on)
from tests.test_landing_browser import seed, settle, JOIN  # noqa: F401
from tests.test_upgrade_browser import env, context, no_h_scroll  # noqa: F401

PHONE, DESK = {"width": 390, "height": 844}, {"width": 1280, "height": 900}
SHOTS = Path(os.environ.get("V3_SHOTS") or "")


class _Dark:
    """The inherited landing tests were written for the page's one (dark) theme: their "quieter" checks compare brightness. They run
    here in dark (prefers-color-scheme: dark, no stored theme); light is covered by the v3 tests in this file, which set it."""
    def __init__(self, b):
        self._b = b

    def new_context(self, **kw):
        kw.setdefault("color_scheme", "dark")
        return self._b.new_context(**kw)

    def __getattr__(self, k):
        return getattr(self._b, k)


@pytest.fixture(scope="module")
def browser():
    with L.sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except L.sync_api.Error as e:
            pytest.skip(f"chromium missing: {e}")
        yield _Dark(b)
        b.close()


@pytest.fixture
def base(monkeypatch, tmp_path):
    """tests/test_landing_browser.py's signed-out server, with v3 on for everyone (and the public record route open)."""
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))                 # an empty reviews store unless a test seeds one
    monkeypatch.delenv("REVIEWS_PAGE", raising=False)
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)
    monkeypatch.setenv("SESSION_SECRET", "landing-browser-secret")
    monkeypatch.setenv("LANDING_JOIN_URL", JOIN)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0])):
        monkeypatch.setattr(server, name, val)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def snap(page, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(250)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


# real-shaped /api/record answers (the fixture store has no settled forecasts): wins + losses per sport, as public_record.read gives them
RECORD = {"cs2": (1342, 1226), "lol": (561, 507), "nba": (3301, 2967), "nfl": (842, 776)}


def record_route(route):
    sp = route.request.url.split("sport=")[-1].split("&")[0]
    w, l = RECORD.get(sp, (0, 0))
    route.fulfill(status=200, content_type="application/json", body=json.dumps({"sport": sp, "totals": {"wins": w, "losses": l, "pushes": 0}}))


def open_landing(browser, base, size, theme, nrev=12, record=True, fonts=False, **kw):
    seed(nrev)
    ctx = browser.new_context(viewport=size, **kw)
    if not fonts:
        ctx.route("https://fonts.*/**", lambda r: r.abort())
    if record:
        ctx.route("**/api/record?*", record_route)
    ctx.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(base + "/", wait_until="load")
    page.wait_for_function("!!window.ScoutV3")
    if nrev:
        page.wait_for_selector("#rvq:not([hidden])")
    return ctx, page


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_landing_v3_shots(browser, base, size, theme):
    ctx, page = open_landing(browser, base, size, theme, fonts=bool(SHOTS.parts))
    try:
        if SHOTS.parts:
            page.evaluate("document.fonts.ready.then(() => 1)")
        settle(page, scroll=True)
        assert page.evaluate("document.documentElement.dataset.v3Page") == "landing"
        no_h_scroll(page)
        snap(page, f"landing-{size['width']}-{theme}-top")
        snap(page, f"landing-{size['width']}-{theme}-full", full=True)
        if SHOTS.parts:
            for sec in ("proofs", "why", "demo", "grade", "lines", "slips", "features", "how", "final"):
                page.locator("#" + sec).screenshot(path=str(SHOTS / f"landing-{size['width']}-{theme}-s-{sec}.png"))
        assert not page.errors, page.errors
    finally:
        ctx.close()
