"""v3 group D (web/UI_V3.md): the signed-out landing page in v3 (landing_v3.py + web/v3/landing.html, the Prop Grader page built to the
owner's Nov 23 brief: view-only product pictures, one call to action per screen),
and the member-side sign-in gate / account / alerts surfaces. Real server.Handler, SCOUT_UI_V3=1, fixture data, a real-shaped board
snapshot and real-shaped reviews only. 390x844 and 1280x900, dark and light. V3_SHOTS=<folder> keeps screenshots.

The flag-off landing (web/landing.html, its carousel and demos) keeps its own suite in tests/test_landing_browser.py; under v3 the
signed-out page is the new one, so its carousel checks are replaced here by the static review wall's."""
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import landing_v3
import server
import whop_auth
import workspace_api
from tests import test_landing_browser as L
from tests.test_landing_browser import seed, JOIN  # noqa: F401
from tests.test_landing_v3 import board, hltv_off
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
    monkeypatch.setenv("LANDING_TRIAL_URL", JOIN)                      # the v3 landing's buttons: the free-trial checkout
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


# ---------------------------------------------------------------------------------------------------------- the v3 landing page
@pytest.fixture
def lbase(base, monkeypatch):
    """base + a real-shaped CS2 board snapshot (no feed reads) and a fresh landing build."""
    monkeypatch.setattr(workspace_api, "_SNAP", dict(workspace_api._SNAP, body=json.dumps(board()).encode()))
    monkeypatch.setattr(workspace_api, "snapshot", lambda s: workspace_api._SNAP)
    hltv_off(monkeypatch)                                               # no HLTV database: the map module stays out unless a test gives maps
    landing_v3.reset()
    yield base
    landing_v3.reset()


def warm(base, nrev):
    """Seed reviews, ask once, let the one background build finish (it reads the shared answers), so the page has every block."""
    import urllib.request
    seed(nrev)
    urllib.request.urlopen(base + "/").read()
    for _ in range(200):
        if not landing_v3._BUILDER["busy"]:
            break
        time.sleep(0.05)
    landing_v3._STATE["built"] = 0.0
    urllib.request.urlopen(base + "/").read()
    for _ in range(200):
        if not landing_v3._BUILDER["busy"]:
            break
        time.sleep(0.05)


def open_page(browser, base, size, theme, motion="reduce"):
    ctx = browser.new_context(viewport=size, reduced_motion=motion)
    ctx.route("https://fonts.*/**", lambda r: r.abort())
    ctx.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(base + "/", wait_until="load")
    return ctx, page




@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_landing_v3_first_screen_passes_the_five_second_test(browser, lbase, size, theme):
    """What it is (H1), which apps and sports, a real graded row with a letter, line and model estimate, one call to action and the
    price: all in the first screen. Pinned dark, no sideways scroll, no small targets, no script errors."""
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, size, theme)
    try:
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        h = size["height"]

        def inside(sel):
            b = page.locator(sel).first.bounding_box()
            return b is not None and b["y"] >= 0 and b["y"] + b["height"] <= h

        for sel in ("h1", ".hero-for", ".hero .lead", "#hero-cta", ".hero .terms"):    # critic round 3: the sports line at every width
            assert inside(sel), sel
        assert page.locator("#hero-cta").get_attribute("href") == JOIN
        assert page.locator(".hero .terms").text_content() == "Then $24.99/month. Cancel anytime."
        btn, terms = page.locator("#hero-cta").bounding_box(), page.locator(".hero .terms").bounding_box()
        assert 0 <= terms["y"] - (btn["y"] + btn["height"]) < 24
        if size is DESK:
            assert inside(".br:not(.br-h)") and inside(".br:not(.br-h) .gb")          # the first graded row, letter and all
            assert page.locator(".phone").is_visible() and page.locator(".side").is_visible()
        else:
            # critic round 4: phones see real board rows (a compact frame, no sidebar) above the phone, not one prop
            assert page.locator(".browser").is_visible() and not page.locator(".side").is_visible() and page.locator(".phone").is_visible()
            assert inside(".br:not(.br-h)") and inside(".br:not(.br-h) .gb") and inside(".url")
            assert page.evaluate("[...document.querySelectorAll('.br:not(.br-h) .b-ln, .br:not(.br-h) .b-pk, .br:not(.br-h) .b-p b')]"
                                 ".every(e => e.scrollWidth <= e.clientWidth + 1)")      # lines, pick and estimate never cut
            assert page.evaluate("getComputedStyle(document.querySelector('.url')).fontFamily").startswith('"Geist Mono"')
        assert page.locator(".hero a.btn").count() == 1                                # one call to action in the hero
        assert page.locator(".hero-proof").count() == 0 and page.locator(".hero a").count() == 1   # brief 3.2: nothing else in the hero
        small = page.evaluate("""() => [...document.querySelectorAll('a,button')].filter(e => e.offsetParent && !e.closest('.dock')
            && !e.classList.contains('btn-nav') && !e.classList.contains('skip') && e.getBoundingClientRect().height < 44).map(e => e.textContent.trim().slice(0, 30))""")
        assert not small, small
        nav = page.locator(".btn-nav").bounding_box()
        assert nav["height"] >= 36
        no_h_scroll(page)
        assert page.title().startswith("Prop Grader")
        snap(page, f"landing-brief-{size['width']}-{theme}", full=True)
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_product_pictures_are_view_only(browser, lbase):
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, DESK, "dark")
    try:
        bad = page.evaluate("""() => { const foc = 'a[href],button,input,select,textarea,[tabindex]';
            return [...document.querySelectorAll('[aria-hidden="true"],[role="img"]')].flatMap(n => [...n.querySelectorAll(foc)]).length
              + [...document.querySelectorAll('.browser *,.phone *,.panel *,.ss-fig *')].filter(e => getComputedStyle(e).cursor === 'pointer').length }""")
        assert bad == 0
        before = page.content()
        page.locator(".br:not(.br-h)").first.click()
        page.locator(".ph-add").click()                                                # an inert members-only note
        assert page.content() == before                                                # nothing opens, toggles or reveals
        tabbed = []
        for _ in range(40):
            page.keyboard.press("Tab")
            tabbed.append(page.evaluate("document.activeElement.closest('.browser,.phone,.panel,.ss-fig') ? 'frame' : document.activeElement.tagName"))
        assert "frame" not in tabbed
    finally:
        ctx.close()


def test_landing_v3_motion_plays_once_and_reduced_motion_shows_final_frames(browser, lbase):
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, PHONE, "dark", motion="reduce")
    try:
        assert not page.evaluate("document.documentElement.classList.contains('motion')")
        assert page.locator("#ss-st").text_content() == "Slip ready" and page.locator("#ss-cta").count() == 0   # no stage CTA (D15)
        assert page.locator(".ss-board").is_visible()                                  # phones: a 2x2 strip of mini cards above the slip
        assert page.evaluate("[...document.querySelectorAll('.sbx>.sc')].every(e => getComputedStyle(e).opacity === '0')")   # end frame:
        assert page.evaluate("[...document.querySelectorAll('.sad')].every(e => getComputedStyle(e).opacity === '0')")      # one line, not
        assert page.locator(".ss-done").is_visible() and page.locator(".ss-done").text_content() == "All 4 legs are in the slip"   # 4 copies
        assert page.evaluate("[...document.querySelectorAll('.rv')].every(e => getComputedStyle(e).opacity === '1')")
        assert page.evaluate("[...document.querySelectorAll('.slot .sc')].every(e => getComputedStyle(e).opacity === '1')")
    finally:
        ctx.close()
    ctx, page = open_page(browser, lbase, DESK, "dark", motion="no-preference")
    try:
        assert page.evaluate("document.documentElement.classList.contains('motion')")
        assert page.locator("#ss-st").text_content() == "Slip ready"                  # off screen: always the final frame
        assert page.locator("#ss .slot.on").count() == 0 and page.locator("#ss .slot .sc").first.evaluate("e => getComputedStyle(e).opacity") == "1"
        page.locator("#ss").scroll_into_view_if_needed()
        page.wait_for_function("document.getElementById('ss-st').textContent.startsWith('Building')", timeout=4000)
        page.wait_for_function("document.getElementById('ss').classList.contains('ready')", timeout=8000)
        page.wait_for_timeout(600)
        assert page.locator("#ss .slot.on").count() == 4 and page.locator("#ss a").count() == 0
        page.wait_for_timeout(3000)
        assert page.locator("#ss-st").text_content() == "Slip ready"                  # holds: no loop
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        assert page.locator("#ss-st").text_content() == "Slip ready"                  # left the screen: the final frame, re-armed
        page.locator("#ss").scroll_into_view_if_needed()
        page.wait_for_function("document.getElementById('ss-st').textContent.startsWith('Building')", timeout=8000)  # replays after leaving
        page.wait_for_function("document.getElementById('ss').classList.contains('ready')", timeout=8000)
        assert page.evaluate("[...document.querySelectorAll('.ghost')].length") == 0
        assert not page.errors, page.errors
    finally:
        ctx.close()
    ctx, page = open_page(browser, lbase, PHONE, "dark", motion="no-preference")
    try:
        page.evaluate("document.querySelector('#ss').scrollIntoView({block:'center'})")
        page.wait_for_function("document.getElementById('ss-st').textContent === 'Building 2 of 4'", timeout=4000)
        page.wait_for_function("document.getElementById('ss').classList.contains('ready')", timeout=6000)   # mini cards fly down
        assert page.locator("#ss .slot.on").count() == 4 and page.evaluate("[...document.querySelectorAll('.ghost')].length") == 0
        assert page.locator("#ss .sbx.gone").count() == 4                            # each flown card left its 'Added to slip' outline
        page.wait_for_timeout(400)                                                    # ... and "Slip ready" folds them into one line
        assert page.evaluate("getComputedStyle(document.querySelector('.ss-done')).opacity") == "1"
        assert page.evaluate("[...document.querySelectorAll('.sad')].every(e => getComputedStyle(e).opacity === '0')")
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_inside_text_shows_with_motion_and_no_map_panel(browser, lbase):
    """No HLTV in these tests: row B has no panel to reveal, so its text must not wait for one."""
    warm(lbase, 12)
    for size in (DESK, PHONE):
        ctx, page = open_page(browser, lbase, size, "dark", motion="no-preference")
        try:
            assert page.locator(".kbars").count() == 0
            for sel in (".irow.flip .itxt", ".irow:not(.flip) .itxt"):
                page.locator(sel).scroll_into_view_if_needed()
                page.wait_for_timeout(900)
                assert page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).opacity") == "1", sel
            box = page.locator(".irow.flip .itxt").bounding_box()
            assert box["x"] < size["width"] / 2                                      # un-flipped to the left when it stands alone
        finally:
            ctx.close()


def test_landing_v3_faq_is_an_accessible_accordion(browser, lbase):
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, DESK, "dark", motion="no-preference")
    try:
        qs = page.locator("#acc button")
        assert qs.count() == 5 and qs.nth(0).get_attribute("aria-expanded") == "true" and page.locator("#a1").is_visible()
        assert not page.locator("#a2").is_visible()
        qs.nth(1).click()
        page.wait_for_timeout(400)
        assert qs.nth(1).get_attribute("aria-expanded") == "true" and page.locator("#a2").is_visible()
        assert qs.nth(0).get_attribute("aria-expanded") == "false" and not page.locator("#a1").is_visible()
        qs.nth(1).focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        assert all(qs.nth(i).get_attribute("aria-expanded") == "false" for i in range(5))     # all may be closed
        page.keyboard.press("Space")
        page.wait_for_timeout(400)
        assert qs.nth(1).get_attribute("aria-expanded") == "true"
        assert page.locator("#a3").get_attribute("hidden") == "until-found"
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_sign_in_error_is_text(browser, lbase):
    warm(lbase, 0)
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(lbase + "/?signin_error=%3Cb%3ENope%3C%2Fb%3E")
        err = page.locator("#signin-error")
        assert err.is_visible() and err.text_content() == "Sign-in did not complete. Please try again." and err.locator("b").count() == 0
        page.goto(lbase + "/?signin_error=expired")
        assert err.is_visible() and err.text_content() == "Sign-in expired. Please try again."
    finally:
        ctx.close()


def test_landing_v3_phone_bar_never_covers_content(browser, lbase):
    """The phone bar shows once the hero button has left the screen, hides over the Slip button, the pricing card and the final call
    to action, and the page keeps static room for it at the bottom."""
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, PHONE, "dark")
    try:
        dock = page.locator("#dock")
        shown = lambda: dock.evaluate("d => d.classList.contains('show')")    # noqa: E731
        assert not shown() and not dock.is_visible()                              # hidden = visibility:hidden, so never focusable
        page.locator("#how").scroll_into_view_if_needed()
        page.wait_for_function("document.getElementById('dock').classList.contains('show')")
        a = dock.locator("a")
        assert a.text_content() == "Try free for 1 day" and a.get_attribute("href") == JOIN and a.get_attribute("tabindex") is None
        assert dock.locator(".dk-t").text_content() == "then $24.99/mo" and a.bounding_box()["height"] >= 44   # brief 3: the bar's words
        assert page.evaluate("getComputedStyle(document.body).paddingBottom") == "76px" and dock.bounding_box()["height"] == 76   # rule 5.11
        a.focus()
        assert page.evaluate("document.activeElement === document.querySelector('#dock a')")   # keyboard / switch users reach it
        page.wait_for_timeout(300)
        assert not page.locator(".btn-nav").is_visible()                              # one Try on screen: the header pill steps aside
        si = page.locator(".nav-cta .tl").bounding_box()                              # and Sign in sits flush right (no hole)
        assert abs(si["x"] + si["width"] - (PHONE["width"] - 20)) <= 1, si
        for sel in (".ss-cta", "#plan", "#final"):
            page.locator(sel).scroll_into_view_if_needed()
            page.evaluate(f"document.querySelector('{sel}').scrollIntoView({{block:'center'}})")
            page.wait_for_timeout(400)
            assert not shown(), sel
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(400)
        page.evaluate("document.getElementById('dock').classList.add('show')")   # forced shown at the very bottom: covers nothing
        page.wait_for_timeout(500)
        top = dock.bounding_box()["y"]
        for sel in (".foot .rg", ".foot nav"):
            box = page.locator(sel).bounding_box()
            assert box["y"] + box["height"] <= top + 0.5, (sel, box, top)
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




@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_landing_v3_web_vitals_lcp_and_cls(browser, lbase, size):
    """Largest contentful paint and layout shift on the served page (local server, web fonts blocked so the fallback face is what
    paints): LCP under 2.5 s and CLS under 0.1, the "good" Core Web Vitals marks, with the dock and the reveal running."""
    warm(lbase, 12)
    ctx = browser.new_context(viewport=size, reduced_motion="no-preference")
    ctx.route("https://fonts.*/**", lambda r: r.abort())
    ctx.add_init_script("""window.__cls=0;window.__lcp=0;
      new PerformanceObserver(l=>{for(const e of l.getEntries()){if(!e.hadRecentInput)window.__cls+=e.value}}).observe({type:'layout-shift',buffered:true});
      new PerformanceObserver(l=>{for(const e of l.getEntries()){window.__lcp=e.startTime;window.__lcpEl=e.element&&e.element.tagName}}).observe({type:'largest-contentful-paint',buffered:true});""")
    page = ctx.new_page()
    try:
        page.goto(lbase + "/", wait_until="load")
        page.wait_for_timeout(800)
        for y in range(0, 4000, 500):
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(60)
        lcp, cls = page.evaluate("window.__lcp"), page.evaluate("window.__cls")
        assert lcp < 2500, lcp
        assert cls < 0.1, cls
    finally:
        ctx.close()


