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


VISIBLE = ("#hero-title", ".hero-k", "#rvq", "#rvq-prev", "#rvq-next", "#rvq-pp", "#rvq-all", ".lede", "#hero-rec-n", "#hero-rec-t", "#hero-books",
           "#hc", "#hc-sport", ".hc-src", "#hc-pause", "#hc-av", "#hc-name", "#hc-meta", "#hc-chips .tag", "#hc-pct", "#hc-side", "#hc-why", "#hc-grade",
           "#hc-pl", "#hc-ll", "#hc-nav",
           "#proof-record-n", "#proof-record-t", "#proof-sports", "#proof-reviews-n", "#mq", "#mq-pause", ".books h2",
           "#why-title", "#vs-seg", "#vs-list", "#demo-title", "#bd-tabs", "#bd-rows .prow", "#pcard", "#bd-src",
           "#grade-title", "#gd-svg", "#gd-line", "#gd-proj", "#gd-grade", "#gd-break", "#gd-ladder", "#gd-note",
           "#lines-title", "#lt-chips", "#lt-track", "#lt-list", "#lt-live", "#slips-title", "#ss-zoom", "#sb", "#sb-legs", "#sb-out",
           "#features-title", "#how-title", "#steps", "#reviews-line", "#final-title", "footer.foot")


def check_landing_fields(page, size):
    """Every field the landing shows today is still on screen in v3 (scrolled into view one by one), with the v3 shape on the card."""
    for sel in VISIBLE:
        loc = page.locator(sel).first
        loc.scroll_into_view_if_needed()
        assert loc.is_visible(), sel
    # the previous design's screenshots are not drawn under v3 (they showed a UI that no longer matches); the live card leads
    assert not page.locator(".board-shot img").is_visible() and not page.locator(".card-shot img").is_visible()
    # the review ticker sits after the hero's buttons (and, on the phone, after the live card), never between headline and sentence
    order = page.evaluate("""(() => { const y = s => document.querySelector(s).getBoundingClientRect().top;
      return [y('#hero-title'), y('.lede'), y('.hero .ctas'), y('#hc'), y('#rvq')]; })()""")
    assert order[0] < order[1] < order[2] < order[4], order
    if size is not DESK:
        assert order[3] < order[4], order
    if size is DESK:
        assert page.locator(".topnav").is_visible()
        assert page.locator(".hero .btn.ghost").is_visible()                       # Try the example board (desktop, as today)
    assert page.locator("#hc-nav button").count() == 4 and page.locator(".fcard").count() == 8 and page.locator("#steps li").count() == 3
    pct = page.locator("#hc-pct").text_content().strip()
    assert pct.endswith("%") and len(pct.split(".")[-1]) == 2                     # one decimal + %
    assert page.locator("#hc-side").text_content().split()[0] in ("Over", "Under")
    assert page.locator(".hc .ring small").text_content() == "Model estimate"
    p = page.evaluate("getComputedStyle(document.querySelector('.v3-meter>i')).getPropertyValue('--p')").strip()
    assert abs(float(p) - float(pct[:-1]) / 100) < 1e-6
    assert page.locator("header.top .brand b").text_content() == "Scout" and page.locator("header.top .brand").get_attribute("aria-label") == "1time Scout"
    assert page.locator("#proof-record-n").text_content().replace(",", "").strip().isdigit()     # the real record count, never a word or a zero
    # the headline's second line is plain ink (no transparent gradient text)
    assert page.evaluate("getComputedStyle(document.querySelector('.hero h1 .nb')).webkitTextFillColor") != "rgba(0, 0, 0, 0)"
    for a in ("Join on Whop", "Sign in with Whop"):
        assert page.locator("a", has_text=a).first.is_visible()
    assert page.locator("header.top a.btn.primary").get_attribute("href") == JOIN or size is PHONE


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_landing_v3_controls_still_work(browser, base, size):
    """Click through every demo with the flag on: why switch, board tabs and rows, grade sliders, slip builder, slip zoom, hero sports."""
    ctx, page = open_landing(browser, base, size, "dark")
    try:
        settle(page)
        page.locator("#hc-nav button[data-v=cs2]").click()
        page.wait_for_function("document.getElementById('hc-sport').textContent.startsWith('CS2')")
        assert page.locator("#hc").evaluate("e => e.classList.contains('stopped')")
        page.locator("#vs-seg [data-v=without]").click()
        assert page.locator(".vs").get_attribute("data-mode") == "without"
        page.locator("#bd-tabs [data-v=nba]").click()
        page.wait_for_function("document.querySelector('#bd-src').textContent.startsWith('NBA')")
        page.locator("#bd-rows .prow").nth(1).click()
        assert page.locator("#bd-rows .prow").nth(1).get_attribute("aria-pressed") == "true"
        before = page.locator("#gd-big").text_content()
        page.locator("#gd-line").fill("33.5")
        page.locator("#gd-line").dispatch_event("input")
        assert page.locator("#gd-line-v").text_content() == "33.5" and page.locator("#gd-big").text_content() != before
        n0 = page.locator("#sb-legs li").count()
        page.locator("#sb-plus").click()
        page.wait_for_function(f"document.querySelectorAll('#sb-legs li:not(.leave)').length !== {n0}")
        page.locator("#ss-zoom").click()
        page.wait_for_selector("#ss-dlg[open]")
        page.locator("#ss-close").click()
        page.wait_for_function("!document.getElementById('ss-dlg').open")
        assert not page.errors, page.errors
    finally:
        ctx.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_landing_v3_sign_in_error_and_price(browser, base, monkeypatch, theme):
    """A failed sign-in still reads as text, and LANDING_PRICE still rides on the Join buttons, in v3."""
    monkeypatch.setenv("LANDING_PRICE", "$24.99/month")
    seed(0)
    ctx = browser.new_context(viewport=PHONE)
    ctx.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    page = ctx.new_page()
    page.goto(base + "/?signin_error=Whop%20said%20no")
    page.wait_for_function("!!window.ScoutV3")
    assert page.locator("#signin-error").is_visible() and page.locator("#signin-error").text_content() == "Whop said no"
    assert "$24.99/month" in page.locator(".ctas a.btn.primary").first.text_content()
    snap(page, f"landing-signin-error-390-{theme}")
    ctx.close()


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
        check_landing_fields(page, size)
        assert not page.errors, page.errors
    finally:
        ctx.close()


# ---------------------------------------------------------------- member side: the no-access gate, Tools (preferences, alerts), sign-out
def member_page(browser, env, monkeypatch, path, size, theme, ready, member=True):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    if not member:
        monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": False} if v and v.startswith("good-") else None)
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + path)
    p.wait_for_selector(ready, timeout=30000)
    p.wait_for_function("!!window.ScoutV3")
    return c, p


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_gate_no_access_v3(browser, env, monkeypatch, size, theme):
    c, p = member_page(browser, env, monkeypatch, "/", size, theme, "#gate:not([hidden]) h3", member=False)
    try:
        p.wait_for_timeout(400)
        g = p.locator("#gate")
        assert g.locator("h3").text_content() == "Your Whop account doesn't have access yet"
        assert g.get_by_text("The grader is for paid members only.").is_visible()
        for txt, href in (("Sign out", "/auth/logout"), ("Signed up just now? Check again", "/auth/login")):
            assert g.locator("a", has_text=txt).get_attribute("href") == href and g.locator("a", has_text=txt).is_visible()
        assert g.get_by_text("Just joined?").is_visible() and g.locator(".perk").count() == 3
        for perk in ("Single Prop", "Slate", "Prop Board"):
            assert g.locator(".perk b", has_text=perk).is_visible()
        assert g.get_by_text("Projections from HLTV match data").is_visible()
        up = g.locator("a.go")
        if up.count():
            assert up.is_visible() and up.bounding_box()["height"] >= 44
        # a visitor without access has no member nav behind the header: brand + theme only
        assert p.locator(".sc-v3-bar .sc-brand").is_visible() and p.locator(".sc-v3-bar [data-v3=theme]").is_visible()
        assert not p.locator(".sc-v3-tabs").is_visible() and not p.locator(".sc-v3-bar .sc-nav").is_visible()
        no_h_scroll(p)
        snap(p, f"gate-noaccess-{size['width']}-{theme}", full=True)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_tools_account_v3(browser, env, monkeypatch, size, theme):
    c, p = member_page(browser, env, monkeypatch, "/#/tools", size, theme, "#hub-tools-section:not([hidden])")
    try:
        p.wait_for_selector("#hub-tools-body .v3-acct")
        acct = p.locator("#hub-tools-body .v3-acct")
        assert acct.is_visible() and acct.locator("a").get_attribute("href") == "/auth/logout"
        assert acct.locator("b").text_content().startswith("Signed in with Whop")
        arts = p.locator("#hub-tools-body .h-tool-grid>article")
        assert arts.count() >= 3 and all(arts.nth(i).is_visible() for i in range(arts.count()))
        assert p.locator("#hub-tools-alerts").count() == 1
        # leave and come back: the hub rewrites the Tools body, the Account card comes back once
        p.evaluate("location.hash='#/picks'")
        p.wait_for_selector("#hub-picks-section:not([hidden])")
        p.evaluate("location.hash='#/tools'")
        p.wait_for_selector("#hub-tools-section:not([hidden]) .v3-acct")
        assert p.locator(".v3-acct").count() == 1
        no_h_scroll(p)
        p.wait_for_timeout(300)
        snap(p, f"tools-{size['width']}-{theme}", full=False)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_member_nav_stays_while_sign_in_is_checked(browser, env, monkeypatch, size):
    """index.html starts every load as body.locked until /auth/me answers: the gate rules key on the gate actually showing, so a
    member never sees the v3 nav vanish (or the classic header / page width change) while that answer is on its way."""
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, size)
    held = []
    c.route("**/auth/me", lambda r: held.append(r))
    p = c.new_page()
    p.goto(env.url + "/?view=classic")
    p.wait_for_function("!!window.ScoutV3 && document.querySelector('.sc-v3-bar')")
    assert held and p.evaluate("document.body.classList.contains('locked')")
    nav = ".sc-v3-tabs" if size is PHONE else ".sc-v3-bar .sc-nav"
    assert p.locator(nav).is_visible()
    assert p.locator("#gate").is_hidden()
    for r in held:
        r.continue_()
    c.close()


# ---------------------------------------------------------------------------------------------------- the review ticker under v3
# v3 (approved mocks 01 / 02, polish1): the reviews follow the product - headline, sentence, buttons, then the numbers and the ticker
# (on a phone the live card comes first). The landing suite's ticker checks run again here with that placement; everything else in
# them (rotation, pause on hover / focus, arrows, swipe, no layout shift) is unchanged. Positions are page positions (the ticker can
# sit below the fold, so stepping it may scroll the page).
from tests.test_landing_browser import _open, _idx, SIZES, seed_long  # noqa: E402,F401
import re  # noqa: E402


@pytest.mark.parametrize("size", (SIZES[1], SIZES[-1]), ids=lambda s: str(s["width"]))
def test_carousel_under_the_headline_advances_and_pauses(browser, base, size):
    seed(3)
    ctx, page, errors = _open(browser, base, size)
    page.wait_for_selector("#rvq:not([hidden])")
    top = lambda sel: page.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().top + scrollY")
    assert top("#hero-title") < top(".lede") < top(".hero .ctas") < top("#rvq")
    assert page.locator("#rvq").bounding_box()["height"] <= (64 if size["width"] > 640 else 100)
    fs = lambda sel: page.evaluate(f"parseFloat(getComputedStyle(document.querySelector('{sel}')).fontSize)")
    assert fs("#rvq .rvq-s.on blockquote") < fs(".lede")
    assert page.locator("#rvq .rvq-s").count() == 3 and page.locator("#rvq .rvq-s.on").count() == 1
    assert page.locator("#rvq .rvq-s.on figcaption").text_content().startswith("Member C. · ")
    assert page.locator("#rvq-who").text_content() == page.locator("#rvq .rvq-s.on figcaption").text_content()
    assert page.locator("#rvq-all").text_content() == "All reviews" and page.locator("#rvq-all").get_attribute("href") == "/reviews"
    away = (5, 5)
    page.locator("#rvq").scroll_into_view_if_needed()                     # the ticker turns only while it is on screen
    page.wait_for_timeout(400)                                             # its visibility observer runs on a real frame
    page.clock.run_for(100)
    assert _idx(page) == 0
    page.mouse.move(*away)
    page.clock.run_for(6000)
    assert _idx(page) == 1
    page.locator("#rvq-stage").scroll_into_view_if_needed()
    page.hover("#rvq-stage")
    page.clock.run_for(20000)
    assert _idx(page) == 1
    page.mouse.move(*away)
    page.clock.run_for(6000)
    assert _idx(page) == 2
    page.focus("#rvq-next")
    page.clock.run_for(20000)
    assert _idx(page) == 2
    page.keyboard.press("Enter")
    assert _idx(page) == 0
    page.focus(".ctas a")
    page.clock.run_for(6000)
    assert _idx(page) == 1
    page.locator("#rvq-prev").click()
    page.locator("#rvq-prev").click()
    assert _idx(page) == 2
    assert not errors
    ctx.close()


def test_carousel_swipes_on_a_phone(browser, base):
    seed(3)
    ctx, page, errors = _open(browser, base, SIZES[1], has_touch=True)
    page.wait_for_selector("#rvq:not([hidden])")
    page.locator("#rvq-stage").scroll_into_view_if_needed()
    b = page.locator("#rvq-stage").bounding_box()
    y = b["y"] + b["height"] / 2
    page.mouse.move(b["x"] + b["width"] - 10, y)
    page.mouse.down()
    page.mouse.move(b["x"] + 20, y, steps=5)
    page.mouse.up()
    assert _idx(page) == 1
    page.mouse.move(b["x"] + 20, y)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] - 10, y, steps=5)
    page.mouse.up()
    assert _idx(page) == 0
    assert not errors
    ctx.close()


@pytest.mark.parametrize("kind", ("short", "long"))
@pytest.mark.parametrize("size", (SIZES[0], SIZES[1], SIZES[-1]), ids=lambda s: str(s["width"]))
def test_carousel_fills_without_moving_the_join_button(browser, base, size, kind):
    seed(3) if kind == "short" else seed_long()
    ctx = browser.new_context(viewport=size)
    page = ctx.new_page()
    errors, held = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(lambda url: "/api/reviews" in url, lambda route: held.append(route))
    page.goto(base + "/", wait_until="load")
    page.evaluate("document.fonts.ready.then(() => 1)")
    page.wait_for_timeout(200)
    assert page.evaluate("document.documentElement.classList.contains('has-reviews')")
    assert page.locator("#rvq").is_hidden()
    y = lambda: page.evaluate("document.querySelector('.ctas a').getBoundingClientRect().top + scrollY")
    y0 = y()
    assert held, "the reviews request was not made"
    for r in held:
        r.continue_()
    page.wait_for_selector("#rvq:not([hidden])")
    page.wait_for_timeout(100)
    tops = [y()]
    for _ in range(3):
        page.locator("#rvq-next").click()
        tops.append(y())
    assert all(abs(v - y0) <= 2 for v in tops), (y0, tops)
    assert not errors
    ctx.close()
