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
        for a in ("Get today's graded board", "Sign in with Whop"):          # both in the first phone screen (standing rule)
            loc = page.locator(".hero a", has_text=a).first
            assert loc.is_visible() and loc.bounding_box()["y"] + loc.bounding_box()["height"] <= size["height"], a
        assert page.locator(".hero a.btn.primary").get_attribute("href") == JOIN
        first = page.locator(".term .tr").first                              # the first real row's chance and grade show in the first screen
        assert first.locator(".pv").bounding_box()["y"] + first.locator(".pv").bounding_box()["height"] <= size["height"]
        assert first.locator(".gr b").bounding_box()["y"] + first.locator(".gr b").bounding_box()["height"] <= size["height"]
        for sel in (".hero", "body"):
            assert page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).backgroundColor") in ("rgb(8, 11, 19)", "rgb(12, 18, 34)"), sel
        small = page.evaluate("""() => [...document.querySelectorAll('a,button,summary,input')].filter(e => e.offsetParent && !e.closest('.dock')
            && e.getBoundingClientRect().height < 44).map(e => e.textContent.trim().slice(0, 30))""")
        assert not small, small
        no_h_scroll(page)
        assert page.locator("#reviews .rv").count() >= 1 and page.locator("#rvq").count() == 0
        assert page.locator("#term-at").text_content().startswith("checked ")    # a 3-minute-old board: the browser says so
        assert page.evaluate("document.documentElement.classList.contains('live')")
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
        assert row.locator(".gr b").text_content() == "–"
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
        page.goto(lbase + "/?signin_error=%3Cb%3ENope%3C%2Fb%3E")
        err = page.locator("#signin-error")
        assert err.is_visible() and err.text_content() == "<b>Nope</b>" and err.locator("b").count() == 0
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
            assert p0 == f"{landing_v3.kills_chance(30.5, 32.4) * 100:.1f}"           # at the real line: the board's number
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
