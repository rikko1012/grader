"""cap-3 review 2026-11-08, rendered: the classic board cards and the player page's forecast panel name the hold on a book /
Polymarket line priced far from even ("Scout grades lines priced near even.") - no side, no letter, chances still shown -
as the board table and Compare already did. Sanity holds get the same sentence (their own server text). ui_mock harness: no
server, no network. Every name / number here is a TEST FIXTURE."""
import copy

import pytest

pytest.importorskip("playwright.sync_api")
from sports.tests import test_ui_audit as A  # noqa: E402
from sports.tests import test_ui_compare as C  # noqa: E402
from sports.tests import test_ui_uifix as U  # noqa: E402

TEXT = "Scout grades lines priced near even."
CAP = {"b": "cap", "t": TEXT, "v": "cap-3"}
browser = A.browser


@pytest.mark.parametrize("width", [390, 1280])
def test_board_card_names_the_cap3_hold(browser, width):
    nba = C._fixture_board()
    q = nba["markets"][2]["q"][0]                                         # Fixture Charlie: a forecast line with a lean
    q["x"] = dict(CAP)
    api = A._two_league_api({"/api/sports/board": lambda qq: {"rows": {}} if qq.get("part") == "view" else
                             {"markets": [], "events": {}, "fc": {}} if qq.get("part") == "more" else nba})
    ctx, page, errors = A._page(browser, api, width=width)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.click('[data-tview="cards"]')
    page.wait_for_selector(".sw-research-card")
    held = page.locator('.sw-grade-held[data-held="cap"]')
    assert held.count() == 1 and held.inner_text() == TEXT
    card = held.locator("xpath=ancestor::div[contains(@class,'sw-offer')][1]")
    assert card.locator(".sw-scout .sw-chances").count() == 1             # the chances stay
    assert card.locator(".sw-grade, [data-grade-closed]").count() == 0     # no letter
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert not errors, errors
    ctx.close()


def test_player_page_forecast_panel_names_the_cap3_hold(browser):
    P = copy.deepcopy(U.PLAYER)
    q = P["markets"][0]["quotes"][0]
    q["forecast_id"] = "fc-1"
    q["x"] = dict(CAP)
    P["forecasts"] = {"fc-1": {"forecast_id": "fc-1", "model_version": "sm-1", "forecast_at": "2099-01-01T00:00:00Z", "line": q["line"],
                               "p_over": 0.1, "p_under": 0.9, "p_push": 0.0, "projection_mean": 15.0, "reason_codes": [],
                               "offer_snapshot_ids": [q["record_id"]], "input_snapshot_ids": [], "detail": {}}}
    ctx, page, errors = A._page(browser, U._app_api({"/api/sports/player": P}))
    page.goto(f"/sports#/player/{U.PID}?lg=nba&stat=points")
    page.wait_for_selector("#sw-fc-h")
    panel = page.locator("#sw-fc-h").locator("xpath=ancestor::section[1]")
    held = panel.locator('.sw-grade-held[data-held="cap"]')
    assert held.count() == 1 and held.inner_text() == TEXT
    assert panel.locator(".sw-grade").count() == 0
    body = panel.inner_text()
    assert "10.0%" in body and "90.0%" in body                            # chances stay
    assert not errors, errors
    ctx.close()


def test_graded_card_has_no_hold_line_and_a_sanity_hold_says_its_text(browser):
    """Only held quotes carry the line: the graded Charlie card has none; the fixture's sanity-held Delta line (sg-1) now
    says the board table's own text on its card too."""
    nba = C._fixture_board()
    nba["markets"][2]["q"][0]["x"]["gr"] = "A"
    api = A._two_league_api({"/api/sports/board": lambda qq: {"rows": {}} if qq.get("part") == "view" else
                             {"markets": [], "events": {}, "fc": {}} if qq.get("part") == "more" else nba})
    ctx, page, errors = A._page(browser, api)
    page.goto("/sports#/board?lg=nba")
    page.wait_for_selector(".sw-tb-row")
    page.click('[data-tview="cards"]')
    page.wait_for_selector(".sw-research-card")
    held = page.locator(".sw-grade-held")
    assert held.count() == 1 and held.get_attribute("data-held") == "ratio_low"
    assert held.inner_text() == "Held: projection is under 25% of the line"
    assert page.locator(".sw-grade").count() >= 1
    assert not errors, errors
    ctx.close()
