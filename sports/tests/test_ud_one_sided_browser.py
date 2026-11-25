"""cap-2 (owner 2026-11-06), rendered: a one-sided Underdog line on the board table, the board cards and the Underdog
sheet reads "Over only" with its line and price, never a Grader chance, lean, grade, edge or slip / track actions -
at 390 px and 1440 px with no sideways page scroll (ui_mock harness: no server, no network). Every name / number here
is a TEST FIXTURE."""
import pytest

from sports.tests import test_ui_compare as C
from sports.tests import ui_mock

ONE = "offer:underdog:e1:r1:ab"


@pytest.fixture(scope="module")
def browser():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                               # noqa: BLE001 - no browser binary on this machine
            pytest.skip(f"chromium not available: {e}")
        yield b
        b.close()


def _board():
    B = C._fixture_board()
    T = B["markets"][0]["q"][0]["t"]
    # a one-sided line that (wrongly) still carries a forecast and a graded signal: the page must ignore both
    one = {"r": ONE, "p": "underdog", "l": 4.5, "v": "one_sided", "o": [1, 0], "m": [0.86, None], "t": T, "su": T, "g": 0,
           "f": "f1", "x": {"v": "cf-2", "ln": "over", "pl": 0.62, "e": 6, "g": 0, "c": 4, "cw": ["fixture"], "s": 6, "n": 2, "gr": "B"},
           "nf": "Over only line - Grader grades lines that offer both sides"}
    B["markets"].append({"k": "e", "e": "g1", "pid": "nba:player:espn:e", "n": "Fixture Echo", "pos": "G", "tm": "BOS",
                         "tid": "nba:team:espn:2", "st": "threes", "pr": 1, "cap": "supported", "q": [one], "kn": [], "kr": [],
                         "alt": [], "promo": [], "mk": {"at": {ONE: {"po": 0.52, "pu": 0.48, "pp": 0, "n": 2}}}, "sc": 0})
    B["stats"]["threes"] = "3-Pointers Made"
    return B


def _api():
    from sports.tests import test_ui_uifix as U
    B = _board()

    def board(q):
        if q.get("part") == "view":
            return {"rows": {}}
        if q.get("part") == "more":
            return {"markets": [], "events": {}, "fc": {}}
        return B
    return U._app_api({"/api/sports/board": board, "/api/sports/compare/check": lambda q: (404, {"error": "x"})})


def _no_sideways(page):
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("width", [390, 1440])
def test_board_table_and_cards_label_the_side_and_never_grade(browser, width):
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    errors = ui_mock.install(page, _api())
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    exp = page.locator('.sw-tb-exp[data-tgroup]')
    for i in range(exp.count()):                               # open every player's other stats
        if exp.nth(i).get_attribute("aria-expanded") == "false":
            exp.nth(i).click()
    row = page.locator(f'tr[data-rid="{ONE}"]')
    assert row.count() == 1
    assert row.locator("[data-onesided]").inner_text() == "Over only"
    assert row.locator(".sw-tb-line").inner_text().startswith("4.5")
    assert row.locator(".sw-tb-scout").inner_text().strip() == "—"
    assert row.locator(".sw-tb-edge").inner_text().strip() in ("—", "")
    assert row.locator(".sw-tb-conf").inner_text().strip() == "—"
    assert row.locator(".sw-grade, .sw-tb-lean.over, .sw-tb-lean.under").count() == 0
    assert "O 52.0%" in row.locator(".sw-tb-mkt").inner_text()         # the plain market chance stays
    assert "No bet" not in row.inner_text()
    assert _no_sideways(page)
    page.click('[data-tview="cards"]')
    page.wait_for_selector(".sw-research-card")
    card = page.locator(f'.sw-actions-slot[data-rid="{ONE}"]').locator("xpath=ancestor::div[contains(@class,'sw-offer')][1]")
    txt = card.inner_text()
    assert "Over only" in txt and "Grader grades lines that offer both sides" in txt
    assert card.locator(".sw-scout[data-onesided]").count() == 1
    assert card.locator("[data-act-add], [data-act-track]").count() == 0
    assert "one_sided" not in txt and "62.0%" not in txt and "No bet" not in txt
    assert _no_sideways(page)
    import os
    if os.environ.get("SHOTS_DIR"):                             # owner review screenshots (optional)
        page.screenshot(path=os.path.join(os.environ["SHOTS_DIR"], f"ud_one_sided_cards_{width}.png"), full_page=True)
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_underdog_sheet_reads_over_only_with_no_chance(browser, width):
    from sports.tests import shots_audit_1105 as S
    from sports.tests import test_ui_uifix as U
    api = U._app_api({"/api/sports/sheets": lambda q: S.sheet_doc("underdog")})
    ctx = browser.new_context(viewport={"width": width, "height": 1000}, base_url=ui_mock.BASE)
    page = ctx.new_page()
    errors = ui_mock.install(page, api)
    page.add_init_script("try{localStorage.removeItem('scout.sports.sheets.v1')}catch(e){}")
    page.goto("/sports#/sheets/underdog?lg=nba")
    page.wait_for_selector("#shx-body tr")
    body = page.locator("#shx-body").inner_text()
    assert "Over only" in body and "one_sided" not in body and "Standard" in body
    assert _no_sideways(page)
    assert not errors, errors
    ctx.close()
