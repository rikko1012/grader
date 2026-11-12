"""QOL pass 2026-11-07 on the new board's Tools (Price check, Alerts, Best prices, Ask). Display and convenience only: no grade,
chance, stake, leg choice or ranking changes. The pass's Slips items (Slips tab in the address, Auto-build settings remembered,
Generate off with no sport, new slips scrolled into view, Clear asks once, Best prices "On slip") were rolled back on the owner's
2026-11-07 request (Slip studio as it was on 2f96bab), with their tests. Real Handler + the fixtures of
test_upgrade_browser (no network, no production data); every player, team and number is a TEST FIXTURE."""
import pytest

from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401
from tests.test_pickem_ev_browser import ev_on, ev_page  # noqa: F401
from tests.test_hub_shell_browser import market_doc, mt_page


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")


# ------------------------------------------------------------------------------------------------ Price check

def mt_env(monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")


def market_rows(p):
    return p.locator("#hub-market-results .h-pc-row:not(.h-pc-head)").count()


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_price_check_refresh_button_state_and_a_failed_refresh_keeps_the_sheet(browser, env, monkeypatch, size):
    mt_env(monkeypatch)
    c = context(browser, env, size)
    try:
        p = mt_page(c, env, market_doc())
        btn = p.locator('#hub-market-tools [data-hub="refresh-markets"]')
        assert btn.bounding_box()["height"] < 60                                  # it stretched to ~110px beside the heading
        rows = market_rows(p)
        assert rows > 0
        held, calls = [], []
        p.unroute("**/api/upgrade/market-tools")
        p.route("**/api/upgrade/market-tools", lambda r: (calls.append(1), held.append(r)))
        btn.click()
        p.wait_for_function("document.querySelector('#hub-market-tools [data-hub=\"refresh-markets\"]').disabled")
        assert btn.inner_text() == "Checking prices…"
        p.evaluate("document.querySelector('#hub-market-tools [data-hub=\"refresh-markets\"]').click()")   # a second press sends nothing
        p.wait_for_timeout(200)
        assert len(calls) == 1
        held[0].fulfill(status=503, json={"error": "market_tools_unavailable"})
        p.wait_for_function("document.querySelector('#hub-market-results').innerText.includes('Refresh prices to try again')")
        text = p.locator("#hub-market-results").inner_text()
        assert "Prices could not be refreshed" in text and market_rows(p) == rows   # the last good sheet stays on screen
        assert btn.is_enabled() and btn.inner_text() == "Refresh prices"
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_price_check_filters_are_remembered_and_no_match_clears_in_one_press(ctx, env, monkeypatch):
    mt_env(monkeypatch)
    p = mt_page(ctx, env, market_doc())
    p.locator("#hub-market-filters .h-mt-more summary").click()
    p.locator("#hub-mt-side").select_option("under")
    p.locator("#hub-mt-sort").select_option("chance")
    p.reload()
    p.wait_for_selector("#hub-market-filters .h-market-filters")
    p.locator("#hub-market-filters .h-mt-more summary").click()
    assert p.locator("#hub-mt-side").input_value() == "under" and p.locator("#hub-mt-sort").input_value() == "chance"
    assert "(2)" in p.locator("#hub-market-filters .h-mt-more summary").inner_text()
    p.locator("#hub-mt-q").fill("nobody-matches-this")
    p.wait_for_selector('#hub-market-results [data-hub="mt-clear"]')
    p.locator('#hub-market-results [data-hub="mt-clear"]').click()
    p.wait_for_function("document.querySelectorAll('#hub-market-results .h-pc-row:not(.h-pc-head)').length > 0")
    assert p.locator("#hub-mt-q").input_value() == "" and p.locator("#hub-mt-side").input_value() == "all"
    assert p.evaluate("JSON.parse(localStorage.getItem('scout-price-filters')).side") == "all"
    assert p.locator("#hub-mt-sort").input_value() == "chance"                 # Sort is not a filter: it stays
    assert not p.errors


def test_price_check_search_ignores_case_accents_and_word_order(ctx, env, monkeypatch):
    mt_env(monkeypatch)
    doc = market_doc()
    doc["sports"]["nba"]["ev"][0]["player"] = "Nikola Jokić"
    p = mt_page(ctx, env, doc)
    for q in ("jokic", "JOKIĆ nikola", "points jokic"):
        p.locator("#hub-mt-q").fill(q)
        p.wait_for_timeout(100)
        assert "Nikola Jokić" in p.locator("#hub-market-results").inner_text(), q
        assert market_rows(p) == 1, q
    assert not p.errors


def test_price_check_both_sides_figure_stays_inside_its_column(ctx, env, monkeypatch):
    mt_env(monkeypatch)
    p = mt_page(ctx, env, market_doc())
    p.wait_for_selector("#hub-market-results .h-pc-arb")
    over = p.evaluate("""() => [...document.querySelectorAll('#hub-market-results .h-pc-arb .h-pc-value')].map(c => {
        const chip = c.querySelector('.h-pc-chip').getBoundingClientRect(), cell = c.getBoundingClientRect(); return chip.right - cell.right; })""")
    assert over and max(over) <= 1, over
    assert not p.errors


# ------------------------------------------------------------------------------------------------ Alerts

@pytest.mark.parametrize("size", [DESK, PHONE])
def test_empty_alerts_list_opens_the_board_on_the_alert_menu(browser, env, v2, size):
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/#/alerts", size)
        p.wait_for_selector('#hub-alerts-body [data-hub="alerts-new"]', timeout=30000)
        p.locator('#hub-alerts-body [data-hub="alerts-new"]').click()
        p.wait_for_function("location.hash.startsWith('#/board')")
        assert not p.locator("#hub-board-section").is_hidden() and not p.locator("#hub-views-pop").is_hidden()
        assert p.evaluate("document.activeElement.dataset.hub") == "save-alert"
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_remove_alert_names_it_and_confirms(ctx, env, v2):
    def state(route):
        r = route.fetch()
        d = r.json()
        if route.request.method == "GET":
            d["prefs"] = dict(d.get("prefs") or {}, researchHub={"alerts": [{"id": "a1", "name": "Test kills", "filters": {"sport": "all", "book": "all"}}]})
        route.fulfill(response=r, json=d)
    ctx.route("**/api/upgrade/state*", state)
    p = open_page(ctx, env, "/#/alerts")
    p.wait_for_selector('#hub-alerts-body [data-hub="delete-alert"]', timeout=30000)
    btn = p.locator('#hub-alerts-body [data-hub="delete-alert"]')
    assert btn.get_attribute("aria-label") == "Remove Test kills"
    btn.click()
    p.wait_for_function("[...document.querySelectorAll('.u-toast')].some(t => t.textContent.includes('Removed Test kills.'))")
    assert p.locator('#hub-alerts-body [data-hub="alerts-new"]').count() == 1
    assert not p.errors


# ------------------------------------------------------------------------------------------------ Best prices

def test_best_prices_search_ignores_accents_and_keeps_focus_on_redraws(ctx, env, ev_on):
    p = ev_page(ctx, env)
    p.locator('[data-ev="app"][data-id="underdog"]').click()      # the fixture's leg with two independent books is an Underdog leg
    p.wait_for_selector("#hub-ev-body .ev-row", timeout=30000)
    p.locator("#ev-q").fill("REAVES points")
    p.wait_for_timeout(100)
    assert "Austin Reaves" in p.locator("#hub-ev-body .ev-list").first.inner_text()
    p.locator("#ev-q").fill("")
    books = p.locator('#hub-ev-body [data-ev="books"]').first
    books.focus()
    p.keyboard.press("Enter")
    p.wait_for_selector("#hub-ev-body .ev-src")
    assert p.evaluate("document.activeElement.dataset.ev") == "books"           # focus stays on the toggle after the redraw
    assert p.evaluate("document.activeElement.getAttribute('aria-expanded')") == "true"
    assert not p.errors


# ------------------------------------------------------------------------------------------------ Ask (Genie)

def test_genie_controls_look_off_while_a_question_is_checked(ctx, env, monkeypatch):
    monkeypatch.setenv("SCOUT_GENIE", "1")
    monkeypatch.delenv("SCOUT_GENIE_PERSONA", raising=False)
    held = []
    ctx.route("**/api/upgrade/genie", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
    p = open_page(ctx, env, "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.locator("#hub-rows tr[data-record]").first.locator(".h-player").click()
    p.wait_for_selector('#hub-card:not([hidden]) [data-hub="card-tab"][data-id="ask"]', timeout=20000)
    p.locator('[data-hub="card-tab"][data-id="ask"]').click()
    p.wait_for_selector("#hub-ask")
    p.locator('#hub-ask [data-hub="genie-ask"]').first.click()
    p.wait_for_function("document.querySelector('#hub-ask button[type=submit]').getAttribute('aria-disabled') === 'true'")
    sub = p.locator("#hub-ask button[type=submit]")
    assert sub.inner_text() == "Checking…" and float(sub.evaluate("e => getComputedStyle(e).opacity")) < 0.6
    assert all(b.get_attribute("aria-disabled") == "true" for b in p.locator('#hub-ask [data-hub="genie-ask"]').all())
    held[0].fulfill(json={"sentences": ["The saved projection is 33.0."], "quota": {"remaining_questions": 9, "questions_limit": 10}})
    p.wait_for_function("document.querySelector('#hub-ask-out').innerText.includes('saved projection')")
    assert sub.inner_text() == "Ask" and sub.get_attribute("aria-disabled") is None
    assert p.locator('#hub-ask [data-hub="genie-ask"][aria-disabled]').count() == 0
    assert not p.errors
