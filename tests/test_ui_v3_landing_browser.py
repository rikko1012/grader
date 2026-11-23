"""v3 group D (web/UI_V3.md): the signed-out landing page in v3 (landing_v3.py + web/v3/landing.html, direction C "line terminal"),
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
from tests.test_landing_v3 import board, hltv_off, map_rows, map_totals, MOVER_MAPS
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
def test_landing_v3_first_screen_targets_and_width(browser, lbase, size, theme):
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, size, theme)
    try:
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"    # pinned dark: a stored light theme does not apply
        for a in ("Try Prop Grader free for 1 day", "Sign in with Whop"):   # both in the first phone screen (standing rule)
            loc = page.locator(".hero a", has_text=a).first
            assert loc.is_visible() and loc.bounding_box()["y"] + loc.bounding_box()["height"] <= size["height"], a
        assert page.locator(".hero a.btn.primary").get_attribute("href") == JOIN
        btn, terms = page.locator(".hero a.btn.primary").bounding_box(), page.locator(".hero .terms").bounding_box()
        assert terms["y"] >= btn["y"] + btn["height"] and terms["y"] - (btn["y"] + btn["height"]) < 24     # the price right under the button
        assert page.locator(".hero .terms").text_content() == "Then $24.99/month. Cancel anytime from your Whop account."
        proof = page.locator("#proof").bounding_box()                        # the proof strip: first screen too (owner blueprint)
        assert proof["y"] + proof["height"] <= size["height"], proof
        assert page.locator("#proof li").count() >= 3 and page.locator("#proof .pf-post").is_visible()   # no record store here: 3 tiles
        if size is DESK:                                                     # desktop: the live sample's first row beside the headline
            first = page.locator(".term .tr").first
            assert first.locator(".pv").bounding_box()["y"] + first.locator(".pv").bounding_box()["height"] <= size["height"]
            assert first.locator(".gr b").bounding_box()["y"] + first.locator(".gr b").bounding_box()["height"] <= size["height"]
        else:                                                                # phone: 3 sample rows, each with its break-even
            rows = page.locator(".term .tr")
            vis = [i for i in range(rows.count()) if rows.nth(i).is_visible()]
            assert len(vis) == 3 and all(rows.nth(i).locator(".rc small").is_visible() for i in vis)
        for sel in (".hero", "body"):
            assert page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).backgroundColor") in ("rgb(8, 11, 19)", "rgb(12, 18, 34)"), sel
        small = page.evaluate("""() => [...document.querySelectorAll('a,button,summary,input')].filter(e => e.offsetParent && !e.closest('.dock')
            && e.getBoundingClientRect().height < 44).map(e => e.textContent.trim().slice(0, 30))""")
        assert not small, small
        no_h_scroll(page)
        assert page.locator("#reviews .rv").count() >= 1 and page.locator("#rvq").count() == 0
        assert page.locator(".brand").first.text_content().strip() == "PropGrader" and page.title().startswith("Prop Grader")
        assert page.locator("#term-at").text_content().startswith("checked ")    # a 3-minute-old board: the browser says so
        assert page.evaluate("document.documentElement.classList.contains('live')")
        assert page.locator("#term-st").text_content() == "Live sample · CS2 board"   # "Live" only beside a fresh board
        rects = page.evaluate("[...document.querySelectorAll('.nw')].filter(e => e.offsetParent).map(e => e.getClientRects().length)")
        assert rects and all(n == 1 for n in rects), rects                               # 1-800-GAMBLER never splits across lines
        snap(page, f"landing-c-{size['width']}-{theme}", full=True)
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_book_tap_shows_that_lines_chance(browser, lbase):
    warm(lbase, 0)
    ctx, page = open_page(browser, lbase, DESK, "dark")
    try:
        row = page.locator(".tr", has=page.locator(".who b", has_text="Gap"))
        assert row.locator(".pv").text_content().startswith("58.3")
        row.locator("button.bk", has_text="13.5").click()
        assert row.locator(".pv").text_content().startswith("52.2")
        assert row.locator("button.bk", has_text="13.5").get_attribute("aria-pressed") == "true"
        assert row.locator(".gr b").text_content() == "Lean Over"            # sweep L3: under the B- cut it reads Lean, never a dash
        row.locator("button.bk", has_text="12.5").click()
        assert row.locator(".pv").text_content().startswith("58.3") and row.locator(".gr b").text_content() == "B-"
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_motion_plays_once_and_reduced_motion_shows_everything(browser, lbase):
    warm(lbase, 12)
    for motion, expect in (("reduce", False), ("no-preference", True)):
        ctx, page = open_page(browser, lbase, PHONE, "dark", motion=motion)
        try:
            assert page.evaluate("document.documentElement.classList.contains('motion')") is expect
            if not expect:                                                   # nothing hidden waiting for an animation
                assert page.evaluate("getComputedStyle(document.querySelector('.tools .tile')).opacity") == "1"
            page.locator("#faq").scroll_into_view_if_needed()
            page.wait_for_timeout(900)
            assert page.evaluate("getComputedStyle(document.querySelector('.qs')).opacity") == "1"
        finally:
            ctx.close()


def test_landing_v3_sign_in_error_is_text(browser, lbase):
    warm(lbase, 0)
    ctx = browser.new_context(viewport=PHONE)
    page = ctx.new_page()
    try:
        page.goto(lbase + "/?signin_error=%3Cb%3ENope%3C%2Fb%3E")         # sweep L8: URL text is never shown, only the page's own words
        err = page.locator("#signin-error")
        assert err.is_visible() and err.text_content() == "Sign-in did not complete - please try again" and err.locator("b").count() == 0
        page.goto(lbase + "/?signin_error=expired")
        assert err.is_visible() and err.text_content() == "Sign-in expired - please try again"
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


def gw_board():
    """board() with Zeta's kills row carrying a projection whose formula chance is the row's own (the widget draws only then)."""
    b = board()
    p = landing_v3.kills_chance(30.5, 32.4)
    b["records"][0]["scout"].update(p_side=round(p, 4), projection=32.4)
    return b


def test_landing_v3_row_opens_in_place_and_a_line_tap_updates_the_read(browser, lbase):
    warm(lbase, 0)
    ctx, page = open_page(browser, lbase, PHONE, "dark")
    try:
        first = page.locator(".tr").first
        assert not first.locator(".dt").is_visible() and first.locator("button.who").get_attribute("aria-expanded") == "false"
        first.locator("button.who").click()                                  # rows start closed; a tap opens the read in place
        assert first.locator(".dt").is_visible() and first.locator("button.who").get_attribute("aria-expanded") == "true"
        first.locator("button.bk", has_text="13.5").click()                 # the other app's line: the open read follows it
        assert first.locator('.dt [data-k="p"]').text_content() == "52.2"
        assert first.locator('.dt-h [data-k="line"]').text_content() == "13.5" and first.locator('.dt-h [data-k="app"]').text_content() == "Prizepicks"
        first.locator("button.who").click()
        assert not first.locator(".dt").is_visible() and first.locator("button.who").get_attribute("aria-expanded") == "false"
        second = page.locator(".tr").nth(1)
        second.locator("button.who").click()
        assert second.locator(".dt").is_visible()
        no_h_scroll(page)
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_two_maps_pool_and_move_the_line(browser, lbase, monkeypatch):
    import hltv_maps
    monkeypatch.setattr(workspace_api, "_SNAP", dict(workspace_api._SNAP, body=json.dumps(gw_board()).encode()))
    rows = {"Mover": map_rows(MOVER_MAPS)}                              # Mover: the soonest lettered Kills Maps 1-2 row on gw_board()
    hltv_off(monkeypatch, lambda nick, team, since: (77005, rows[nick]) if nick in rows else (None, []))
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (list(hltv_maps.ACTIVE_DUTY), "constant"))
    landing_v3.reset()
    warm(lbase, 0)
    tot = map_totals(MOVER_MAPS)
    pooled = lambda a, b: (tot[a][0] + tot[b][0]) / (tot[a][1] + tot[b][1])
    for size in (PHONE, DESK):
        ctx, page = open_page(browser, lbase, size, "dark")
        try:
            assert page.locator(".pool-h small").text_content() == "Mover · Kills · Maps 1-2 · line 32.5 on Underdog"
            assert page.locator("#pool-v").text_content() == f"{pooled('Mirage', 'Dust 2'):.2f}"   # Mirage + Dust 2: summed kills / summed maps
            page.locator("button.map", has_text="Inferno").click()                        # the oldest choice drops out
            assert page.locator("#pool-names").text_content() == "Dust 2 + Inferno"
            assert page.locator("#pool-v").text_content() == f"{pooled('Dust 2', 'Inferno'):.2f}"
            d = pooled('Dust 2', 'Inferno') - 32.5 / 2
            assert page.locator("#pool-d").text_content() == ("+" if d >= 0 else "−") + f"{abs(d):.2f}"
            p0 = page.locator("#gw-p").text_content()
            assert p0 == landing_v3._pct(round(landing_v3.kills_chance(30.5, 32.4), 4))   # at the real line: the board's own rounded number
            slider = page.locator("#gw-line")
            slider.focus()
            for _ in range(8):
                page.keyboard.press("ArrowRight")                                          # line 34.5: past the projection, Under
            assert page.locator("#gw-lv").text_content() == "34.5" and page.locator("#gw-side").text_content().startswith("Under")
            want = 1 - landing_v3.kills_chance(34.5, 32.4)
            assert abs(float(page.locator("#gw-p").text_content()) - want * 100) < 0.11
            assert "Under" in slider.get_attribute("aria-valuetext")
            no_h_scroll(page)
            assert not page.errors, page.errors
        finally:
            ctx.close()


def test_landing_v3_move_the_line_prints_the_boards_rounded_number_at_the_real_line(browser, lbase, monkeypatch):
    """Defect Nov 23: the board row said 0.59 (59.0%) and the widget printed the formula's 58.9 after load. At the real line it now
    prints the board's own number, on load and after dragging away and back."""
    b = board()
    p = landing_v3.kills_chance(30.5, 32.4)
    b["records"][0]["scout"].update(p_side=round(p, 2), projection=32.4)
    monkeypatch.setattr(workspace_api, "_SNAP", dict(workspace_api._SNAP, body=json.dumps(b).encode()))
    landing_v3.reset()
    warm(lbase, 0)
    ctx, page = open_page(browser, lbase, PHONE, "dark")
    try:
        assert page.locator("#gw-p").text_content() == "59.0" and page.locator("#gw-g").text_content() == "B"
        slider = page.locator("#gw-line")
        slider.focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#gw-p").text_content() != "59.0"
        page.keyboard.press("ArrowLeft")
        assert page.locator("#gw-lv").text_content() == "30.5" and page.locator("#gw-p").text_content() == "59.0"
        assert "59.0 percent" in slider.get_attribute("aria-valuetext")
        assert not page.errors, page.errors
    finally:
        ctx.close()


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


def test_landing_v3_a_line_without_last_10_hides_it_never_keeps_the_other_lines(browser, base, monkeypatch):
    b = board()
    pp = next(r for r in b["records"] if r["offer"]["platform"] == "prizepicks")
    pp["history"] = {"l10": None}
    pp["parity"] = {"be": 0.577, "entry": "PrizePicks power 2-pick"}
    pp["offer"]["seen_at"] = "2026-11-22T10:04:00+00:00"
    monkeypatch.setattr(workspace_api, "_SNAP", dict(workspace_api._SNAP, body=json.dumps(b).encode()))
    monkeypatch.setattr(workspace_api, "snapshot", lambda s: workspace_api._SNAP)
    landing_v3.reset()
    warm(base, 0)
    ctx, page = open_page(browser, base, DESK, "dark")
    try:
        row = page.locator(".tr").first
        row.locator("button.who").click()
        assert row.locator('.dt [data-c="l10"]').is_visible() and row.locator(".rc .l10").is_visible()
        row.locator("button.bk", has_text="13.5").click()
        assert not row.locator('.dt [data-c="l10"]').is_visible() and not row.locator(".rc .l10").is_visible()
        assert row.locator('.dt [data-k="entry"]').text_content() == "PrizePicks power 2-pick"
        assert row.locator('.dt [data-k="be"]').text_content() == "57.7" and row.locator(".rc small b").text_content() == "57.7"
        assert row.locator('.dt [data-k="seen"]').text_content() == "10:04"
        row.locator("button.bk", has_text="12.5").click()
        assert row.locator('.dt [data-k="l10"]').text_content() == "6 of 10" and row.locator(".rc .l10").is_visible()
        assert row.locator('.dt [data-k="entry"]').text_content() == "Underdog standard 2-pick"
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_phone_bar_is_trial_first_and_never_covers_content(browser, lbase):
    """The phone bar ("Try free for 1 day · then $24.99/mo") shows once the hero button scrolls away; the page keeps static room for it
    at the bottom, so even shown at the very end it covers neither the footer nor its 21+ line."""
    warm(lbase, 12)
    ctx, page = open_page(browser, lbase, PHONE, "dark")
    try:
        dock = page.locator("#dock")
        assert not dock.evaluate("d => d.classList.contains('show')")       # the hero button is on screen: no bar yet
        page.locator("#tools").scroll_into_view_if_needed()
        page.wait_for_function("document.getElementById('dock').classList.contains('show')")
        a = dock.locator("a")
        assert a.text_content() == "Try free for 1 day · then $24.99/mo" and a.get_attribute("href") == JOIN
        assert a.bounding_box()["height"] >= 44
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(400)
        page.evaluate("document.getElementById('dock').classList.add('show')")   # force it shown at the very bottom
        page.wait_for_timeout(500)
        top = dock.bounding_box()["y"]
        for sel in (".foot .rg", ".foot nav", ".rg-close"):
            box = page.locator(sel).bounding_box()
            assert box["y"] + box["height"] <= top + 0.5, (sel, box, top)
        assert "21 and over" in page.locator(".foot .rg").text_content()
        assert not page.errors, page.errors
    finally:
        ctx.close()


# ---------------------------------------------------------------------------------------------- sweep 2026-11-23 (landing)
def _with_board(monkeypatch, b):
    monkeypatch.setattr(workspace_api, "_SNAP", dict(workspace_api._SNAP, body=json.dumps(b).encode()))
    landing_v3.reset()


def test_landing_v3_move_the_line_keeps_the_boards_number_at_the_real_line(browser, lbase, monkeypatch):
    """sweep L1: the formula gives 56.9 at the real line 31.5 (projection 32.98) but the board says 57.0 B-: the page keeps 57.0 B- on load
    and whenever the slider comes back to 31.5; any other line is the formula's."""
    b = board()
    b["records"][0]["scout"].update(side="over", p_side=0.57, grade="B-", projection=32.98)
    b["records"][0]["offer"]["line"] = 31.5
    _with_board(monkeypatch, b)
    warm(lbase, 0)
    ctx, page = open_page(browser, lbase, DESK, "dark")
    try:
        assert page.locator("#gw-p").text_content() == "57.0" and page.locator("#gw-g").text_content() == "B-"
        slider = page.locator("#gw-line")
        slider.focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#gw-lv").text_content() == "32.0"
        assert page.locator("#gw-p").text_content() == f"{landing_v3.kills_chance(32.0, 32.98) * 100:.1f}"
        page.keyboard.press("ArrowLeft")
        assert page.locator("#gw-p").text_content() == "57.0" and page.locator("#gw-g").text_content() == "B-"
        assert "57.0 percent, grade B-" in slider.get_attribute("aria-valuetext")
        assert page.locator("#gw-ladder li.on").get_attribute("data-g") == "B-"
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_tier_link_only_for_a_letter_on_the_record(browser, lbase, monkeypatch):
    """sweep L2: the read's 'See the X tier' link shows for a letter the record has a tier for (57%+: A+ .. B), never for B- or Lean."""
    b = board()
    pp = next(r for r in b["records"] if r["offer"]["platform"] == "prizepicks")
    pp["scout"].update(p_side=0.575, grade="B")                              # Gap: UD 12.5 is B- (lead), PP 13.5 is B
    _with_board(monkeypatch, b)
    warm(lbase, 0)
    ctx, page = open_page(browser, lbase, DESK, "dark")
    try:
        row = page.locator(".tr", has=page.locator(".who b", has_text="Gap"))
        row.locator("button.who").click()
        rec = row.locator(".dt-rec")
        assert not rec.is_visible()                                           # B- lead: no tier link
        row.locator("button.bk", has_text="13.5").click()
        assert rec.is_visible() and rec.locator('[data-k="g"]').text_content() == "B"
        row.locator("button.bk", has_text="12.5").click()
        assert not rec.is_visible()
        assert not page.errors, page.errors
    finally:
        ctx.close()


def test_landing_v3_a_tap_during_the_count_in_keeps_its_own_chance(browser, lbase):
    """sweep L4: the hero chances count in once (motion on). A line tapped while the count-in runs keeps its own chance when it ends; the
    count-in never puts the lead line's number back. Frames are queued and run by hand, so the tap lands mid count-in every time."""
    warm(lbase, 0)
    ctx = browser.new_context(viewport=DESK, reduced_motion="no-preference")
    ctx.route("https://fonts.*/**", lambda r: r.abort())
    ctx.add_init_script("window.__raf=[];window.requestAnimationFrame=function(f){window.__raf.push(f);return window.__raf.length;};")
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    try:
        page.goto(lbase + "/", wait_until="load")
        page.wait_for_function("document.documentElement.classList.contains('motion') && window.__raf.length > 0")   # count-in started
        row = page.locator(".tr", has=page.locator(".who b", has_text="Gap"))
        row.locator("button.bk", has_text="13.5").click()                    # mid count-in
        page.evaluate("(()=>{for(let i=0;i<5;i++){const q=window.__raf.splice(0);q.forEach(f=>f(performance.now()+400*(i+1)));}})()")
        assert row.locator(".pv").text_content().startswith("52.2"), row.locator(".pv").text_content()
        assert row.locator(".mt").evaluate("e=>e.style.getPropertyValue('--p')") == "52.2"
        mover = page.locator(".tr", has=page.locator(".who b", has_text="Mover"))
        assert mover.locator(".pv").text_content().startswith("64.0")        # an untapped row ends on its own lead value
        assert not page.errors, page.errors
    finally:
        ctx.close()


def _tie_rows():
    """HLTV map rows (hltv_maps row shape) whose two most played pool maps pool to exactly 14.625 kills per map: Mirage 647 kills in 50
    maps, Dust 2 757 in 46 (the live dav1g case), plus Inferno and Ancient to fill the module."""
    from datetime import date, timedelta as td
    today = date.today()
    out, i = [], 0
    for name, n, total in (("Mirage", 50, 647), ("Dust 2", 46, 757), ("Inferno", 12, 170), ("Ancient", 10, 150)):
        base, extra = divmod(total, n)
        for j in range(n):
            k = base + (1 if j < extra else 0)
            out.append((name, (today - td(days=2 + (i * 3) % 340)).isoformat(), 9000 + i, k, 12, k // 2, 13, 8, 1.0, 75.0, 70.0))
            i += 1
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def test_landing_v3_two_maps_reads_the_same_on_load_and_after_reselecting(browser, lbase, monkeypatch):
    """sweep L6: (647 + 757) / 96 = 14.625 exactly. The server prints 14.62; choosing other maps and then the same two again must read
    14.62 again (the page shows the server's string for each pair), never 14.63."""
    import hltv_maps
    rows = {"Mover": _tie_rows()}
    hltv_off(monkeypatch, lambda nick, team, since: (77005, rows[nick]) if nick in rows else (None, []))
    monkeypatch.setattr(hltv_maps, "pool_for", lambda mid=None: (list(hltv_maps.ACTIVE_DUTY), "constant"))
    _with_board(monkeypatch, gw_board())
    warm(lbase, 0)
    ctx, page = open_page(browser, lbase, DESK, "dark")
    try:
        assert page.locator("#pool-names").text_content() == "Mirage + Dust 2"
        first = (page.locator("#pool-v").text_content(), page.locator("#pool-d").text_content())
        assert first[0] == "14.62"
        for m in ("Inferno", "Mirage", "Dust 2"):                            # away and back to the same two maps
            page.locator("button.map", has_text=m).click()
        assert page.locator("#pool-names").text_content() == "Mirage + Dust 2"
        assert (page.locator("#pool-v").text_content(), page.locator("#pool-d").text_content()) == first
        assert not page.errors, page.errors
    finally:
        ctx.close()
