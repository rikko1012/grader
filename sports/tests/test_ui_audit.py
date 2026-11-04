"""Sports member UI audit (2026-10-26): regression tests for the bugs fixed in the audit. Self-contained browser flows
(sports/tests/ui_mock.py: Playwright, every request answered in-process) plus static checks. Every name and number in
the fixtures is a TEST FIXTURE."""
import copy
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


# ------------------------------------------------------------------------------------------------- static checks
def test_no_hedge_or_disclaimer_sentences_in_member_copy():
    """Owner: no hedge / status labels and no 'not advice / not recommendations' lines on member pages. (rg-1's own
    "Research grade" tag is the owner's 2026-10-25 wording and stays.)"""
    for f in ("app.js", "compare.js", "sheets.js"):
        js = (WEB / f).read_text(encoding="utf-8")
        for bad in ("not validated recommendations", "not recommendations", "Not validated yet:", "capture-only)",
                    "still in forward validation", "no grades or stakes", "Not in Scout data:", "Research only: grades"):
            assert bad not in js, (f, bad)


def test_compare_unranked_label_is_not_ranked():
    js = (WEB / "compare.js").read_text(encoding="utf-8")
    assert "No pick" not in js and ">Not ranked<" in js and "Why not ranked" in js


def test_slip_primary_button_keeps_its_dark_ink_inside_the_shell():
    """body.sw button {color: inherit} (0,1,2) used to beat .ss-primary (0,1,0): white text on orange (1.85:1)."""
    css = (WEB / "slips.css").read_text(encoding="utf-8")
    assert re.search(r"body\.sw \.ss-primary\s*\{[^}]*color:\s*var\(--ss-accent-ink\)", css)


# ----------------------------------------------------------------------------------------------- browser flows
pytest.importorskip("playwright.sync_api")
from sports.tests import ui_mock  # noqa: E402
from sports.tests import test_ui_compare as C  # noqa: E402
from sports.tests import test_ui_uifix as U  # noqa: E402

SLOW_NFL = """(() => { const f = window.fetch; window.fetch = function (u) { const p = f.apply(this, arguments);
  return (window.__slow && String(u).includes('league=nfl')) ? p.then(r => new Promise(res => setTimeout(() => res(r), 1200))) : p; }; })();"""


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


def _page(browser, answers, width=1280, init=None):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, base_url=ui_mock.BASE)
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    return ctx, page, ui_mock.install(page, answers)


def _nfl_board():
    B = copy.deepcopy(C._fixture_board())
    for m in B["markets"]:
        m["n"] = m["n"].replace("Fixture", "Gridiron")
    return B


def _two_league_api(extra=None):
    nba, nfl = C._fixture_board(), _nfl_board()

    def board(q):
        if q.get("part") == "view":
            return {"rows": {}}
        if q.get("part") == "more":
            return {"markets": [], "events": {}, "fc": {}}
        return nfl if q.get("league") == "nfl" else nba
    d = {"/api/sports/board": board}
    d.update(extra or {})
    return U._app_api(d)


def test_league_switch_never_paints_the_older_reply(browser):
    """Bug: NBA -> NFL -> NBA quickly; the slow NFL board reply landed last and painted NFL players under the NBA
    toggle and eyebrow. Every view now drops a reply that belongs to an older render."""
    ctx, page, errors = _page(browser, _two_league_api(), init=SLOW_NFL)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.evaluate("window.__slow = true")
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_timeout(100)
    page.click("#sw-league button[data-lg=nba]")
    page.wait_for_timeout(2000)                                                     # the NFL reply has arrived by now
    body = page.locator("#sw-main").inner_text()
    assert "Fixture Alpha" in body and "Gridiron" not in body, body[:400]
    assert page.locator('#sw-league button[aria-pressed="true"]').inner_text() == "NBA"
    # same for a view module (Compare loads the board through the app)
    page.goto("/sports#/compare?lg=nba")
    page.wait_for_selector("#sw-main .sw-eyebrow")
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_timeout(100)
    page.click("#sw-league button[data-lg=nba]")
    page.wait_for_timeout(2000)
    assert page.locator("#sw-main .sw-eyebrow").first.inner_text().startswith("NBA")
    assert not errors, errors
    ctx.close()


def test_slip_studio_drops_a_pool_reply_for_another_league(browser):
    nba_pool = dict(U.POOL_RESEARCH, offers=111)
    nfl_pool = dict(U.POOL_EMPTY, league="nfl", offers=999)
    api = U._slips_api(nba_pool)
    api["/api/sports/slips/pool"] = lambda q: nfl_pool if q.get("league") == "nfl" else nba_pool
    ctx, page, errors = _page(browser, api, init=SLOW_NFL)
    page.goto("/sports#/slips?lg=nba")
    page.wait_for_selector("#ss-pool p")
    page.evaluate("window.__slow = true")
    page.click("#sw-league button[data-lg=nfl]")
    page.wait_for_timeout(100)
    page.click("#sw-league button[data-lg=nba]")
    page.wait_for_timeout(2000)
    pool = page.locator("#ss-pool").inner_text()
    assert "111" in pool and "999" not in pool and "NFL" not in pool, pool
    assert not page.locator("#ss-build").is_disabled()          # the NFL reply (0 eligible) used to lock NBA's Build
    assert not errors, errors
    ctx.close()


def test_board_table_cards_and_compare_show_the_same_one_decimal_chance(browser):
    """Bug: the board table and cards rounded chances to whole numbers (62%) while the player page, compare, sheets,
    slips and My picks showed 62.0% - the same quote read differently across surfaces."""
    ctx, page, errors = _page(browser, C._api())
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    rid = "offer:prizepicks:a1:r1:ab"
    assert page.locator(f'tr[data-rid="{rid}"] .sw-tb-scout').inner_text().endswith("O 62.0% U 38.0%")
    assert "O 56.0%" in page.locator(f'tr[data-rid="{rid}"] .sw-tb-mkt').inner_text()
    page.locator(f'tr[data-rid="{rid}"] [data-cmp-add]').click()
    page.click('[data-tview="cards"]')
    page.wait_for_selector(".sw-research-card")
    card = page.locator(f'.sw-actions-slot[data-rid="{rid}"]').locator("xpath=ancestor::div[contains(@class,'sw-offer')]")
    nums = card.locator(".sw-scout .sw-chances").inner_text()
    assert "62.0%" in nums and "38.0%" in nums and not re.search(r"\b62%", nums), nums
    page.click('[data-tview="table"]')
    page.goto("/sports#/compare?lg=nba")
    page.wait_for_selector(".sw-cmp-card")
    assert "Over 62.0%" in page.locator(f'.sw-cmp-card[data-rid="{rid}"]').inner_text()
    assert not errors, errors
    ctx.close()


def test_lean_badge_wraps_on_phones_instead_of_covering_the_line(browser):
    """Bug: at 360 px the "A Under RESEARCH GRADE" badge (inline-flex, no gap) ran over the line / book cell."""
    nba = C._fixture_board()
    for m in nba["markets"]:
        for q in m["q"]:
            if q.get("x", {}).get("ln"):
                q["x"]["gr"] = "A"
    api = _two_league_api({"/api/sports/board": lambda qq: {"rows": {}} if qq.get("part") == "view" else
                           {"markets": [], "events": {}, "fc": {}} if qq.get("part") == "more" else nba})
    ctx, page, errors = _page(browser, api, width=360)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    assert page.locator(".sw-tb-lean .sw-grade-tag").count() >= 2
    hits = page.evaluate("""() => [...document.querySelectorAll('.sw-tb-row:not([hidden])')].map(tr => {
        const a = tr.querySelector('.sw-tb-line').getBoundingClientRect(), lean = tr.querySelector('.sw-tb-lean');
        const boxes = [lean, ...lean.querySelectorAll('*')].map(e => e.getBoundingClientRect()).filter(r => r.width);
        const box = lean.getBoundingClientRect();
        const overlap = boxes.some(b => !(b.left >= a.right - 0.5 || b.right <= a.left + 0.5 || b.top >= a.bottom - 0.5 || b.bottom <= a.top + 0.5));
        const inside = boxes.every(b => b.left >= box.left - 0.5 && b.right <= box.right + 0.5);   // the tag never spills out
        return [tr.dataset.rid, overlap, inside && box.right <= window.innerWidth + 0.5]; })""")
    assert hits and all(not h[1] and h[2] for h in hits), hits
    gap = page.evaluate("getComputedStyle(document.querySelector('.sw-tb-lean')).columnGap")
    assert gap not in ("normal", "0px"), gap
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert not errors, errors
    ctx.close()


def test_grade_legend_only_when_a_letter_can_show(browser):
    """Bug: a contained league (NFL: no research letters anywhere) still printed the A / B / C legend."""
    nfl = _nfl_board()
    for m in nfl["markets"]:
        for q in m["q"]:
            q.pop("x", None)
        m["nv"] = "NFL model not validated - projection only"
    ctx, page, errors = _page(browser, _two_league_api({"/api/sports/board": lambda q: {"rows": {}} if q.get("part") == "view" else
                                                        {"markets": [], "events": {}, "fc": {}} if q.get("part") == "more" else
                                                        (nfl if q.get("league") == "nfl" else C._fixture_board())}))
    page.goto("/sports#/board?lg=nfl")
    page.wait_for_selector(".sw-tb-row")
    assert page.locator("[data-grade-banner]").count() == 0 and page.locator("[data-grade]").count() == 0
    ctx.close()
    nba = C._fixture_board()
    nba["markets"][0]["q"][0]["x"]["gr"] = "A"
    ctx, page, errors = _page(browser, _two_league_api({"/api/sports/board": lambda q: {"rows": {}} if q.get("part") == "view" else
                                                        {"markets": [], "events": {}, "fc": {}} if q.get("part") == "more" else nba}))
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    assert page.locator("[data-grade-banner]").count() == 1
    assert not errors, errors
    ctx.close()


def test_card_grade_names_a_closed_side(browser):
    """The table said "not offered here" when the graded side is closed on that book; the cards / player page did not."""
    nba = C._fixture_board()
    q = nba["markets"][2]["q"][0]                                         # Fixture Charlie, Under lean
    q["x"]["gr"] = "A"
    q["o"] = [1, 0]                                                       # Under closed on Underdog
    api = _two_league_api({"/api/sports/board": lambda qq: {"rows": {}} if qq.get("part") == "view" else
                           {"markets": [], "events": {}, "fc": {}} if qq.get("part") == "more" else nba})
    ctx, page, errors = _page(browser, api)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.click('[data-tview="cards"]')
    page.wait_for_selector(".sw-research-card")
    note = page.locator("[data-grade-closed]")
    assert note.count() == 1 and note.inner_text() == "Under not offered on Underdog"
    page.click('[data-tview="table"]')
    assert not errors, errors
    ctx.close()


def _player_with_stale_book():
    P = copy.deepcopy(U.PLAYER)
    fresh = dict(U.QUOTES[0], record_id="offer:prizepicks:reb1:r1:ab", line=7.5)
    stale = dict(U.QUOTES[1], record_id="offer:fanduel:fd1:r1:ab", platform="fanduel", line=7.5,
                 last_seen_at="2020-01-01T00:00:00Z")                      # a book with no usable price now
    P["markets"].append({"stat": "rebounds", "stat_label": "Rebounds", "primary": False, "quotes": [fresh, stale],
                         "alternates": [], "kalshi": [], "market": None, "event_id": U.EV["event_id"], "team": "BOS",
                         "capability": "supported"})
    return P


def test_player_stat_tab_keeps_books_without_a_price_hidden(browser):
    """Bug: the first stat hid books without a usable price, but switching stat tabs listed every stored quote."""
    ctx, page, errors = _page(browser, U._app_api({"/api/sports/player": _player_with_stale_book()}))
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=rebounds")
    page.wait_for_selector(".sw-qcard")
    direct = page.locator(".sw-qcard").count()
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=points")
    page.wait_for_selector(".sw-qcard")
    page.click('.sw-stats-tabs button[data-stat="rebounds"]')
    page.wait_for_timeout(200)
    assert direct == 1 and page.locator(".sw-qcard").count() == 1
    assert "FanDuel" not in page.locator("#sw-strip").inner_text()
    assert not errors, errors
    ctx.close()


def test_player_page_hides_empty_distribution_sections(browser):
    P = copy.deepcopy(U.PLAYER)
    q = P["markets"][0]["quotes"][0]
    q["forecast_id"] = "fc-1"
    P["forecasts"] = {"fc-1": {"forecast_id": "fc-1", "model_version": "sm-1", "forecast_at": "2099-01-01T00:00:00Z", "line": q["line"],
                               "p_over": 0.4, "p_under": 0.6, "p_push": 0.0, "projection_mean": 19.0, "reason_codes": ["gp:history-validated"],
                               "offer_snapshot_ids": [q["record_id"]], "input_snapshot_ids": [], "detail": {}}}
    P["forecast_detail"] = {"fc-1": {"ladder_withheld": "the forecast stores no distribution parameters"}}
    ctx, page, errors = _page(browser, U._app_api({"/api/sports/player": P}))
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=points")
    page.wait_for_selector("#sw-fc-h")
    body = page.locator("#sw-main").inner_text()
    for gone in ("Outcome distribution", "At another line", "Not drawn", "stores no distribution", "not yet on live lines"):
        assert gone.lower() not in body.lower(), gone
    assert "40.0%" in body and "60.0%" in body
    assert not errors, errors
    ctx.close()


def test_started_game_says_started_not_no_lines_yet(browser):
    ctx, page, errors = _page(browser, U._app_api())
    page.goto("/sports#/game/" + U.EV2["event_id"] + "?lg=nba")
    page.wait_for_selector("#sw-gameprops .sw-state")
    t = page.locator("#sw-gameprops").inner_text()
    assert "This game has started" in t and "No lines yet" not in t
    assert not errors, errors
    ctx.close()


def test_slash_focuses_the_search_box(browser):
    ctx, page, errors = _page(browser, C._api())
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.locator("h1").click()
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement && document.activeElement.id") == "sw-q"
    page.keyboard.type("bravo")
    page.wait_for_timeout(200)
    assert page.locator(".sw-tb-row:not([hidden])").count() == 1
    assert not errors, errors
    ctx.close()


def test_sort_indicator_and_phone_select_follow_the_sort_in_effect():
    """Audit P2 (2026-11-04): a board with no edge is ordered by start time, so the Edge header must not carry the arrow /
    aria-sort and the phone "Sort by" select must show the order in effect. Semantics: sports/tests/ui_table_test.js."""
    js = (WEB / "table.js").read_text(encoding="utf-8")
    assert "function effectiveSort(rows, key)" in js and "effectiveSort: effectiveSort" in js
    assert 'th("edge", "Edge (pts)", hs, "num")' in js and 'th("player", "Player", hs, "sw-tb-player-h")' in js
    assert "st.effective = eff" in js and "var cur = st.effective || st.sort" in js
    assert "sorted by start time, soonest first" in js
    app = (WEB / "app.js").read_text(encoding="utf-8")
    html_at, bar_at = app.index("window.SportsTable.html("), app.index("window.SportsTable.sortBar(TB)")
    assert html_at < bar_at                                # the select is drawn after the table knows the sort in effect


def test_market_fallback_cell_is_display_only_and_behind_its_switch():
    js = (WEB / "table.js").read_text(encoding="utf-8")
    assert "r.marketFallback = !mkc && fb && fb.n > 0" in js and 'data-fallback="' in js
    assert "Not used for lean or edge" in js and "books, stale" in js
    mv = (WEB.parents[1] / "sports" / "market_view.py").read_text(encoding="utf-8")
    assert 'FALLBACK_FLAG = "SPORTS_MARKET_FALLBACK"' in mv and "if fallback and head_c is None" in mv
    bv = (WEB.parents[1] / "sports" / "board_view.py").read_text(encoding="utf-8")
    assert '"fb"' not in bv and "fallback" not in bv       # the board signal never reads the fallback
