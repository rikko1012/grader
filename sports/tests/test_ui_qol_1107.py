"""QoL pass 2026-11-07, classic sports site (NBA / NFL): day-to-day friction fixed without touching any number.
Search ignores case / accents / spaces; the board table header stays in view; toasts can be dismissed and clear
themselves; Escape closes the slip panel; the Source box closes on an outside tap and gives focus back; Enter saves a
view; a reload keeps the board's scroll; sheets and the slate get a working Reset; Compare clear / remove can be undone;
empty-slip buttons look off; the tab title names the page; a tab left open offers a refresh; phone Compare toggles reach
40 px. Node checks: ui_qol_1107_test.js. Browser flows: ui_mock harness (no server, no network). SHOTS_DIR=<folder>
keeps screenshots. Every name / number here is a TEST FIXTURE."""
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_qol_1107_pure_checks_in_node():
    out = subprocess.run(["node", str(Path(__file__).with_name("ui_qol_1107_test.js")), str(WEB / "sports")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


# ----------------------------------------------------------------------------------------------- browser flows
pytest.importorskip("playwright.sync_api")
from sports.tests import ui_mock  # noqa: E402
from sports.tests import test_ui_compare as C  # noqa: E402
from sports.tests import test_ui_uifix as U  # noqa: E402

SHOTS = os.environ.get("SHOTS_DIR")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                               # noqa: BLE001 - no browser binary on this machine
            pytest.skip(f"chromium not available: {e}")
        yield b
        b.close()


def _page(browser, answers, width=1280, height=900, init=None):
    ctx = browser.new_context(viewport={"width": width, "height": height}, base_url=ui_mock.BASE)
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    return ctx, page, ui_mock.install(page, answers)


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}_{width}.png"), full_page=True)


def _board_api(board):
    def handler(q):
        if q.get("part") == "view":
            return {"rows": {}}
        if q.get("part") == "more":
            return {"markets": [], "events": {}, "fc": {}}
        return board
    return U._app_api({"/api/sports/board": handler, "/api/sports/compare/check": lambda q: (404, {"error": "x"})})


def _accent_board():
    B = C._fixture_board()
    B["markets"][0]["n"] = "Nikola Jokić"
    return B


def _long_board(n=60):
    B = C._fixture_board()
    base = B["markets"][1]
    for i in range(n):
        m = copy.deepcopy(base)
        m["k"] = f"x{i}"
        m["pid"] = f"nba:player:espn:x{i}"
        m["n"] = f"Fixture Extra {i:02d}"
        for q in m["q"]:
            q["r"] = q["r"].replace("b1", f"x{i}")
        m["mk"] = {"at": {q["r"]: {"po": 0.5, "pu": 0.5, "pp": 0, "n": 3} for q in m["q"]}}
        B["markets"].append(m)
    return B


TABLE_VIEW = "try { localStorage.setItem('scout.sports.board.v1', JSON.stringify({view: 'table'})) } catch (e) {}"


# ---- search ignores case, accents and extra spaces (board, sheets, slate)
@pytest.mark.parametrize("width", [390, 1440])
def test_board_search_ignores_accents_and_case(browser, width):
    ctx, page, errors = _page(browser, _board_api(_accent_board()), width, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.fill("#sw-q", "  JOKIC ")
    page.wait_for_timeout(200)
    body = page.locator("#sw-bbody").inner_text()
    assert "Nikola Jokić" in body and "Fixture Bravo" not in body, body[:300]
    page.fill("#sw-q", "zzz")
    page.wait_for_selector("text=No props match these filters")
    assert "Nothing on the board matches “zzz”" in page.locator("#sw-bbody").inner_text()
    assert not errors, errors
    ctx.close()


def test_slate_search_folds_and_reset_is_off_until_needed(browser):
    ctx, page, errors = _page(browser, U._app_api())
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector("#sw-sq")
    page.evaluate("try { localStorage.removeItem('scout.sports.filters.v1') } catch (e) {}")
    page.goto("/sports#/slate?lg=nfl")
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector(".sw-game")
    assert page.locator("#sw-sreset").is_disabled()
    page.fill("#sw-sq", "  CÉLTICS ")
    page.wait_for_timeout(150)
    assert page.locator(".sw-game").count() >= 1 and not page.locator("#sw-sreset").is_disabled()
    page.click("#sw-sreset")
    assert page.locator("#sw-sq").input_value() == "" and page.locator("#sw-sreset").is_disabled()
    assert page.evaluate("document.activeElement.id") == "sw-sq"
    href = page.locator(".sw-game a", has_text="Props for this game").first.get_attribute("href")
    assert href.startswith("#/board?lg=nba&ev=")
    assert not errors, errors
    ctx.close()


# ---- sheets: Reset filters (off at the defaults), also inside the empty table; search folds accents
@pytest.mark.parametrize("width", [390, 1440])
def test_sheet_reset_filters(browser, width):
    from sports.tests import shots_audit_1105 as SA
    api = U._app_api({"/api/sports/sheets": lambda q: SA.sheet_doc("prizepicks")})
    ctx, page, errors = _page(browser, api, width, init="try{localStorage.removeItem('scout.sports.sheets.v1')}catch(e){}")
    page.goto("/sports#/sheets/prizepicks?lg=nba")
    page.wait_for_selector("#shx-body tr")
    total = page.locator("#shx-count").inner_text()
    assert page.locator("#shx-reset").is_disabled()
    page.fill("#shx-q", "no such player")
    page.wait_for_selector("#shx-body [data-shx-reset]")
    assert not page.locator("#shx-reset").is_disabled()
    _shot(page, "qol_sheet_empty_reset", width)
    page.click("#shx-body [data-shx-reset]")
    page.wait_for_selector("#shx-body tr th")
    assert page.locator("#shx-count").inner_text() == total and page.locator("#shx-q").input_value() == ""
    assert page.locator("#shx-reset").is_disabled()
    page.select_option("#shx-lean", "over")
    assert not page.locator("#shx-reset").is_disabled()
    page.click("#shx-reset")
    assert page.locator("#shx-lean").input_value() == "any" and page.locator("#shx-reset").is_disabled()
    stored = json.loads(page.evaluate("localStorage.getItem('scout.sports.sheets.v1')"))
    assert stored["nba:prizepicks"]["lean"] == "any"
    assert not errors, errors
    ctx.close()


# ---- the board table header stays under the sticky site header on a wide screen
def test_board_table_header_sticks_on_wide_screens(browser):
    ctx, page, errors = _page(browser, _board_api(_long_board()), 1440, 800, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.wait_for_function("document.querySelector('.sw-tb-wrap.sw-fit') !== null")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
    page.wait_for_timeout(100)
    got = page.evaluate("""() => { const th = document.querySelector('.sw-tb thead th'), top = document.getElementById('sw-top');
        return {th: th.getBoundingClientRect().top, head: top.getBoundingClientRect().bottom, y: window.scrollY}; }""")
    assert got["y"] > 600 and abs(got["th"] - got["head"]) <= 2, got
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert not errors, errors
    ctx.close()


def _with_site_css(page):
    """The live page also loads /scout-ui.css (72 px header, 196 px side nav from 1100 px); the harness does not."""
    css = (WEB / "scout-ui.css").read_bytes()
    page.route("**/scout-ui.css*", lambda route: route.fulfill(status=200, content_type="text/css", body=css))


@pytest.mark.parametrize("width", [1100, 1180, 1440])
def test_board_table_with_site_css_never_scrolls_the_page_sideways(browser, width):
    """Bug: from 1100 px the table box lost its sideways scroll so the header could stick; with the site's side nav a
    table wider than its column pushed the whole page sideways (142 px at 1100), and the header stuck at 68 px under
    a 72 px site header. Now only a table that fits drops its scroll box, at the header's measured height."""
    ctx, page, errors = _page(browser, _board_api(_long_board()), width, 800, init=TABLE_VIEW)
    _with_site_css(page)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.wait_for_timeout(150)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    if page.locator(".sw-tb-wrap.sw-fit").count():
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        page.wait_for_timeout(100)
        got = page.evaluate("""() => ({th: document.querySelector('.sw-tb thead th').getBoundingClientRect().top,
                                       head: document.getElementById('sw-top').getBoundingClientRect().bottom})""")
        assert abs(got["th"] - got["head"]) <= 2, got
    else:
        assert page.evaluate("getComputedStyle(document.querySelector('.sw-tb-wrap')).overflowX") == "auto"
    if width == 1440:
        assert page.locator(".sw-tb-wrap.sw-fit").count() == 1
    assert not errors, errors
    ctx.close()


# ---- toasts: Dismiss button, Escape, and they clear themselves
def test_toast_dismiss_escape_and_auto_clear(browser):
    ctx, page, errors = _page(browser, _board_api(C._fixture_board()), 1280, init=TABLE_VIEW)
    page.clock.install()
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    rid = "offer:prizepicks:a1:r1:ab"
    page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()
    toast = page.locator("#sw-toast")
    assert toast.inner_text().startswith("Added to Compare") and toast.locator(".sw-toast-x").count() == 1
    toast.locator(".sw-toast-x").click()
    assert toast.inner_text().strip() == ""
    page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()                 # remove again -> new toast
    assert toast.inner_text().startswith("Removed from Compare")
    page.locator("body").click(position={"x": 5, "y": 400})
    page.keyboard.press("Escape")
    assert toast.inner_text().strip() == ""
    page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()
    assert toast.inner_text().strip() != ""
    page.mouse.move(5, 600)
    page.clock.run_for(11000)
    assert toast.inner_text().strip() == ""
    assert not errors, errors
    ctx.close()


# ---- saved views: Enter in the name box saves
def test_saved_view_enter_saves(browser):
    ctx, page, errors = _page(browser, _board_api(C._fixture_board()), 1280, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.locator(".sw-saved-views > summary").click()
    page.fill("#sw-view-name", "Fixture view")
    page.press("#sw-view-name", "Enter")
    assert "Fixture view" in page.locator("#sw-view-list").inner_text()
    assert page.locator("#sw-view-name").input_value() == ""
    assert not errors, errors
    ctx.close()


# ---- a reload keeps the board's scroll position
def test_board_reload_keeps_scroll(browser):
    ctx, page, errors = _page(browser, _board_api(_long_board()), 1280, 700, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.evaluate("window.scrollTo(0, 900)")
    page.wait_for_timeout(450)                                                    # the 300 ms save
    page.reload()
    page.wait_for_selector(".sw-tb-row")
    page.wait_for_function("Math.abs(window.scrollY - 900) <= 3", timeout=5000)
    page.goto("/sports#/slate?lg=nba")                                            # another page starts at the top
    page.wait_for_selector(".sw-game")
    assert page.evaluate("window.scrollY") == 0
    assert not errors, errors
    ctx.close()


# ---- Escape closes the open slip panel; focus returns to its bar
def test_escape_closes_the_slip_tray(browser):
    leg = {"platform": "prizepicks", "record_id": "offer:prizepicks:a1:r1:ab", "side": "over", "player": "Fixture Alpha",
           "player_id": "nba:player:espn:a", "team": "BOS", "line": 20.5, "start_utc": "2099-01-01T00:00:00Z", "stat": "points"}
    init = ("try { localStorage.setItem('ss:draft:nba:prizepicks', JSON.stringify({legs: [%s]}));"
            " localStorage.setItem('ss:ctx', JSON.stringify({league: 'nba', platform: 'prizepicks'}));"
            " sessionStorage.setItem('sw:tray:open', '1'); } catch (e) {}") % json.dumps(leg)
    ctx, page, errors = _page(browser, _board_api(C._fixture_board()), 390, init=init + TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector("#sw-tray.sw-tray-open")
    page.locator("#sw-tray .sw-tray-h").focus()
    page.keyboard.press("Escape")
    assert page.locator("#sw-tray.sw-tray-open").count() == 0
    assert page.evaluate("document.activeElement.hasAttribute('data-tray-toggle')")
    assert page.evaluate("sessionStorage.getItem('sw:tray:open')") == "0"
    assert not errors, errors
    ctx.close()


# ---- the Source box: an outside tap closes it and focus returns to its button
def test_source_dialog_outside_click_and_focus_return(browser):
    ctx, page, errors = _page(browser, U._app_api())
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector(".sw-game")
    page.locator(".sw-gdetails > summary").first.click()
    btn = page.locator(".sw-gdetails .sw-srcbtn").first
    btn.click()
    assert page.locator("#sw-src[open]").count() == 1
    page.mouse.click(5, 5)                                                        # the backdrop
    assert page.locator("#sw-src[open]").count() == 0
    assert page.evaluate("document.activeElement.classList.contains('sw-srcbtn')")
    btn.click()
    page.keyboard.press("Escape")
    assert page.locator("#sw-src[open]").count() == 0
    assert not errors, errors
    ctx.close()


# ---- Compare: Clear comparison and Remove can be undone from the toast
def test_compare_clear_and_remove_undo(browser):
    ctx, page, errors = _page(browser, _board_api(C._fixture_board()), 1280, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    for rid in ("offer:prizepicks:a1:r1:ab", "offer:prizepicks:b1:r1:ab"):
        page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()
    page.goto("/sports#/compare?lg=nba")
    page.wait_for_selector(".sw-cmp-card")
    page.click("#sw-cmp-clear")
    page.wait_for_selector("text=Nothing to compare yet")
    assert "Cleared 2 props from Compare." in page.locator("#sw-toast").inner_text()
    page.click("#sw-cmp-undo")
    page.wait_for_selector(".sw-cmp-card")
    assert page.locator(".sw-cmp-card").count() == 2
    page.locator(".sw-cmp-card .sw-cmp-x").first.click()
    page.wait_for_function("document.querySelectorAll('.sw-cmp-card').length === 1 || document.body.innerText.includes('Nothing to compare yet')")
    assert "Removed from Compare" in page.locator("#sw-toast").inner_text()
    page.click("#sw-cmp-undo")
    page.wait_for_function("document.querySelectorAll('.sw-cmp-card').length === 2")
    assert not errors, errors
    ctx.close()


# ---- Slip Studio: on an empty slip, Copy / Check result / Clear all look off
def test_empty_slip_buttons_are_disabled(browser):
    ctx, page, errors = _page(browser, U._slips_api(U.POOL_RESEARCH))
    page.goto("/sports#/slips?lg=nba")
    page.wait_for_selector("#ss-pool p")
    for b in ("#ss-copy", "#ss-result", "#ss-clear"):
        assert page.locator(b).is_disabled(), b
    assert not page.locator("#ss-recheck").is_disabled()                         # still reloads the pool
    assert "Add to slip" in page.locator("#ss-draft").inner_text()
    assert not errors, errors
    ctx.close()


# ---- the tab title names the page and league
def test_tab_title_follows_the_page(browser):
    ctx, page, errors = _page(browser, _board_api(C._fixture_board()), 1280, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    assert page.title() == "Prop board · NBA · 1time Scout Sports"
    page.goto("/sports#/slate?lg=nfl")
    page.wait_for_selector("#sw-sq")
    assert page.title() == "Slate · NFL · 1time Scout Sports"
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=points")
    page.wait_for_selector(".sw-phead h1")
    assert page.title() == "Test Wing · NBA · 1time Scout Sports"
    assert not errors, errors
    ctx.close()


# ---- a tab left open for 10+ minutes offers a refresh when the member comes back (no automatic refetch)
def test_stale_tab_offers_a_refresh(browser):
    ctx, page, errors = _page(browser, U._app_api())
    page.clock.install()
    page.goto("/sports#/slate?lg=nba")
    page.wait_for_selector(".sw-game")
    hide = """h => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => h});
                     document.dispatchEvent(new Event('visibilitychange')); }"""
    page.evaluate(hide, True)
    page.clock.fast_forward(5 * 60000)
    page.evaluate(hide, False)
    assert page.locator("#sw-stale-tab").count() == 0                            # 5 min: nothing
    page.evaluate(hide, True)
    page.clock.fast_forward(7 * 60000)
    page.evaluate(hide, False)
    assert "loaded 12 min ago" in page.locator("#sw-stale-tab").inner_text()
    page.click("#sw-stale-reload")
    page.wait_for_selector(".sw-game")
    assert page.locator("#sw-stale-tab").count() == 0
    assert not errors, errors
    ctx.close()


# ---- phones: Compare toggles reach 40 px; no sideways page scroll
def test_phone_compare_toggles_are_tappable(browser):
    ctx, page, errors = _page(browser, _board_api(C._fixture_board()), 390, init=TABLE_VIEW)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    hs = page.evaluate("[...document.querySelectorAll('.sw-cmp-mini')].map(b => b.getBoundingClientRect().height)")
    assert hs and min(hs) >= 40, hs
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    _shot(page, "qol_board_phone", 390)
    assert not errors, errors
    ctx.close()
