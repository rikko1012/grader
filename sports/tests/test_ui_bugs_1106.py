"""Bug sweep 2026-11-06, member UI group (sports pages): regression tests reproducing each report's failure case.
#48 player history at a missing line, #49 no Compare toggle on a one-sided board-table row, #50 the model lab poll keeps
the admin's half-filled form, #51 Most-consistent Game sort by start (node), #52 Slip Studio drops a reply that lands after
the member left, #53 Games-without panel starts at the page's stat / line. Node checks: ui_bugs_1106_test.js. Browser flows:
ui_mock harness (no server, no network). SHOTS_DIR=<folder> keeps 390 / 1440 px screenshots. Every name / number here is
a TEST FIXTURE."""
import copy
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_bugs_1106_pure_checks_in_node():
    out = subprocess.run(["node", str(Path(__file__).with_name("ui_bugs_1106_test.js")), str(WEB / "sports")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


def test_consistent_sheet_passes_its_events_to_the_sort():
    js = (WEB / "sports" / "sheets.js").read_text(encoding="utf-8")
    assert "sortRows(consistentFilter(D.rows, f), st.sort, st.dir, D.events)" in js
    assert "function sortRows(rows, key, dir, events)" in js


# ----------------------------------------------------------------------------------------------- browser flows
pytest.importorskip("playwright.sync_api")
from sports.tests import ui_mock  # noqa: E402
from sports.tests import test_ui_uifix as U  # noqa: E402
from sports.tests import test_ud_one_sided_browser as OS  # noqa: E402

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


def _page(browser, answers, width=1280):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    return ctx, page, ui_mock.install(page, answers)


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}_{width}.png"), full_page=True)


# ---- #48: a stat whose only book quotes are stale has no shown line -> no Over / Under anywhere in its history
def _player_without_a_shown_line():
    P = copy.deepcopy(U.PLAYER)
    P["markets"][0]["quotes"] = [dict(U.QUOTES[0], platform="fanduel", record_id="offer:fanduel:fd1:r1:ab",
                                      last_seen_at="2020-01-01T00:00:00Z")]       # a book price older than 15 minutes
    return P


@pytest.mark.parametrize("width", [390, 1440])
def test_history_at_no_line_never_marks_games_over(browser, width):
    ctx, page, errors = _page(browser, U._app_api({"/api/sports/player": _player_without_a_shown_line()}), width)
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=points")
    page.wait_for_selector(".sw-finals .sw-final")
    assert page.locator(".sw-final-over, .sw-final-under, .sw-final-push").count() == 0
    finals = page.locator(".sw-finals").inner_text()
    assert "▲" not in finals and "▼" not in finals and "●" not in finals
    page.locator("details.sw-more > summary", has_text="Games as a table").click()
    cells = page.locator("details.sw-more table.sw-mini tbody tr:not(.sw-season-row) td:nth-child(4)").all_inner_texts()
    assert cells and all(c.strip() == "—" for c in cells), cells
    chart = page.locator("svg.sw-chart").first
    assert "No line is shown" in chart.get_attribute("aria-label")
    assert chart.locator("line[stroke-dasharray='5 4']").count() == 0          # no line drawn at the zero baseline
    assert "Over 0 · Under 0" in page.locator("#sw-hi-h").locator("xpath=..").inner_text()
    _shot(page, "bug48_history_no_line", width)
    assert not errors, errors
    ctx.close()


def test_history_with_a_line_still_marks_over_and_under(browser):
    ctx, page, errors = _page(browser, U._app_api())
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=points")
    page.wait_for_selector(".sw-finals .sw-final")
    assert page.locator(".sw-final-over, .sw-final-under, .sw-final-push").count() == page.locator(".sw-finals .sw-final").count()
    assert page.locator("svg.sw-chart line[stroke-dasharray='5 4']").count() == 1
    assert not errors, errors
    ctx.close()


# ---- #49: a one-sided line on the board table gets no Compare toggle (its card has none either)
@pytest.mark.parametrize("width", [390, 1440])
def test_one_sided_table_row_has_no_compare_toggle(browser, width):
    ctx, page, errors = _page(browser, OS._api(), width)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    exp = page.locator('.sw-tb-exp[data-tgroup]')
    for i in range(exp.count()):
        if exp.nth(i).get_attribute("aria-expanded") == "false":
            exp.nth(i).click()
    row = page.locator(f'tr[data-rid="{OS.ONE}"]')
    assert row.count() == 1 and row.locator("[data-onesided]").inner_text() == "Over only"
    assert row.locator(".sw-cmp-mini").count() == 0
    assert page.locator("tr.sw-tb-row .sw-cmp-mini").count() >= 1                 # standard lines keep their toggle
    _shot(page, "bug49_board_one_sided", width)
    assert not errors, errors
    ctx.close()


# ---- #50: the 5-second poll while an experiment is queued keeps the admin's stat and ticked features
OVERVIEW = {"sources": {"ds-1": True, "canonical": False}, "registry": {"api": "fixture"}, "feature_registry": "fr-fixture",
            "targets": {"nba": ["points", "rebounds", "assists"], "nfl": ["passing_yards"]},
            "algorithms": {"ridge": "Ridge (fixture)", "prior_mean": "Prior mean (fixture)"},
            "features": [{"name": n, "sports": ["nba"], "sources": ["ds-1"], "definition": "fixture " + n, "missing": "skip"}
                         for n in ("prior_n", "rest_days", "pace")],
            "experiments": [{"id": "exp-fixture-1", "state": "queued", "league": "nba", "stat": "points", "algorithm": "ridge",
                             "seed": 1, "created_at": "2099-01-01T00:00:00Z"}]}


def test_model_lab_poll_keeps_the_half_filled_form(browser):
    calls = []

    def overview(q):
        calls.append(1)
        return OVERVIEW
    ctx, page, errors = _page(browser, {"/api/sports/admin/lab/overview": overview})
    page.goto("/sports/lab.html")
    page.fill("#lab-token", "fixture-admin")
    page.click("#lab-login")
    page.wait_for_selector("#lab-list tr[data-id]")
    page.select_option("#lab-form select[name=stat]", "assists")
    page.check("#lab-feats input[value=rest_days]")
    page.check("#lab-feats input[value=pace]")
    n0 = len(calls)
    page.wait_for_timeout(5600)                                               # one poll has run
    assert len(calls) > n0
    assert page.locator("#lab-form select[name=stat]").input_value() == "assists"
    ticked = page.evaluate("[...document.querySelectorAll('#lab-feats input[name=feat]:checked')].map(c => c.value)")
    assert ticked == ["rest_days", "pace"], ticked
    assert not errors, errors
    ctx.close()


# ---- #52: a Slip Studio reply that lands after the member left #/slips is dropped quietly (no unhandled rejection)
def test_slip_studio_reply_after_leaving_is_dropped(browser):
    held = []
    ctx, page, errors = _page(browser, U._slips_api(U.POOL_RESEARCH))
    console = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)

    def hold(route):
        held.append(route)
    page.route("**/api/sports/slips/pool*", hold)
    page.goto("/sports#/slips?lg=nba")
    page.wait_for_selector("#ss-pool")
    page.wait_for_function("document.querySelector('#ss-pool .ss-skel') !== null")
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the pool request was sent"
    page.evaluate("location.hash = '#/board?lg=nba'")
    page.wait_for_function("!document.getElementById('ss-league')")
    import json
    for r in held:
        r.fulfill(status=200, content_type="application/json", body=json.dumps(U.POOL_RESEARCH))
    page.wait_for_timeout(800)
    bad = [e for e in errors + console if "reading 'value'" in e or "Cannot read properties of null" in e]
    assert not bad, bad
    assert not errors, errors
    ctx.close()


# ---- #53: the Games-without panel starts from the stat tab and line the player page passes
def _player_two_stats():
    P = copy.deepcopy(U.PLAYER)
    reb = [dict(U.QUOTES[0], record_id="offer:prizepicks:reb1:r1:ab", line=9.5)]
    P["markets"].append({"stat": "rebounds", "stat_label": "Rebounds", "primary": False, "quotes": reb, "alternates": [],
                         "kalshi": [], "market": None, "event_id": U.EV["event_id"], "team": "BOS", "capability": "supported"})
    return P


def _games_two_stats():
    gs = copy.deepcopy(U.GAMES)
    for i, g in enumerate(gs):
        g["values"]["rebounds"] = 6 + (i % 7)
    return gs


MATE = "nba:player:espn:88"


def _without_api():
    out = {g["event_id"]: {"out": ({MATE: "dnp"} if i % 3 == 0 else {})} for i, g in enumerate(U.GAMES)}
    return {"team_id": U.BOS["team_id"], "games": out, "players": {MATE: "Fixture Teammate"}}


@pytest.mark.parametrize("width", [390, 1440])
def test_games_without_panel_follows_the_page_stat_and_line(browser, width):
    api = U._app_api({"/api/sports/player": _player_two_stats(), "/api/sports/history": {"games": _games_two_stats()},
                      "/api/sports/without": _without_api()})
    ctx, page, errors = _page(browser, api, width)
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=rebounds")
    page.wait_for_selector("#gwx-stat")
    assert page.locator("#gwx-stat").input_value() == "rebounds"
    assert page.locator("#gwx-line").input_value() == "9.5"
    assert "at 9.5" in page.locator("#gwx-out").inner_text()
    page.locator("#sw-without").scroll_into_view_if_needed()
    _shot(page, "bug53_without_rebounds", width)
    # the member's own line on the main box: the panel follows it
    page.fill("#sw-cline", "11")
    page.dispatch_event("#sw-cline", "change")
    page.wait_for_function("document.querySelector('#gwx-line') && document.querySelector('#gwx-line').value === '11'")
    assert page.locator("#gwx-stat").input_value() == "rebounds"
    assert not errors, errors
    ctx.close()
