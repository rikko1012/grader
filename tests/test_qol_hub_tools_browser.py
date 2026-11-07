"""QOL pass 2026-11-07 on the new board's Slips and Tools (Slip studio, Auto-build, slip column, Price check, Alerts, Best prices,
Ask). Display and convenience only: no grade, chance, stake, leg choice or ranking changes. Real Handler + the fixtures of
test_upgrade_browser (no network, no production data); every player, team and number is a TEST FIXTURE."""
import pytest

from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401
from tests.test_pickem_ev_browser import ev_on, ev_page  # noqa: F401
from tests.test_hub_shell_browser import market_doc, mt_page


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")


def slips_page(ctx, env, path="/#/slips", size=None):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector("#hub-generation-lines", state="attached", timeout=30000)
    p.wait_for_timeout(400)
    return p


# ------------------------------------------------------------------------------------------------ Slips: tab in the address

def test_slips_tab_is_kept_in_the_address_across_reload(ctx, env, v2):
    p = slips_page(ctx, env)
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    assert p.evaluate("location.hash") == "#/slips?tab=build"
    hist = p.evaluate("history.length")
    p.locator('[role=tab][data-hub=studio-tab][data-id=tracked]').click()
    assert p.evaluate("location.hash") == "#/slips?tab=tracked"
    assert p.evaluate("history.length") == hist                        # a tab switch replaces the entry, never adds one
    p.reload()
    p.wait_for_selector('[data-studio-panel=tracked]:not([hidden])', timeout=30000)
    assert p.locator('[role=tab][aria-selected=true]').get_attribute("data-id") == "tracked"
    p.locator('[role=tab][data-hub=studio-tab][data-id=slip]').click()
    assert p.evaluate("location.hash") == "#/slips"
    # More > Auto-build slips opens with the tab in the address too
    p.locator('#u-hub [data-hub="section"][data-id="board"]').locator("visible=true").first.click()
    p.evaluate("document.querySelector('#hub-more [data-hub=\"studio-open\"][data-id=\"build\"]').click()")
    p.wait_for_function("location.hash === '#/slips?tab=build'")
    assert not p.errors


def test_slips_tab_in_the_address_without_v2(ctx, env, monkeypatch):
    monkeypatch.delenv("SCOUT_WORKSPACE_V2", raising=False)
    p = slips_page(ctx, env, "/#scout/studio")
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    assert p.evaluate("location.hash") == "#scout/studio?tab=build"
    p.reload()
    p.wait_for_selector('#hub-generate:visible', timeout=30000)
    assert not p.errors


# ------------------------------------------------------------------------------------------------ Auto-build

def test_autobuild_settings_are_remembered_and_generate_is_off_without_a_sport(ctx, env, v2):
    p = slips_page(ctx, env, "/#/slips?tab=build")
    p.locator("#hub-generation-book").select_option("underdog")
    p.locator("#hub-generation-slips").select_option("4")
    p.locator("#hub-generation-count").select_option("3")
    p.locator("#hub-generation-repeats").select_option("none")
    p.locator('#hub-generation-sports input[value="lol"]').uncheck()
    p.reload()
    p.wait_for_selector("#hub-generate:visible", timeout=30000)
    assert p.locator("#hub-generation-book").input_value() == "underdog"
    assert p.locator("#hub-generation-slips").input_value() == "4"
    assert p.locator("#hub-generation-count").input_value() == "3"
    assert p.locator("#hub-generation-repeats").input_value() == "none"
    assert p.locator('#hub-generation-sports input[value="lol"]').is_checked() is False
    assert p.locator('#hub-generation-sports input[value="cs2"]').is_checked() is True
    # no sport ticked: Generate is off and the reason is on screen, before any press
    for box in p.locator("#hub-generation-sports input").all():
        box.uncheck()
    assert p.locator("#hub-generate").is_disabled()
    assert "Choose at least one sport" in p.locator("#hub-generation-status").inner_text()
    p.locator('#hub-generation-sports input[value="cs2"]').check()
    assert p.locator("#hub-generate").is_enabled() and p.locator("#hub-generation-status").inner_text() == ""
    # a stored value the control does not offer is ignored
    p.evaluate("localStorage.setItem('scout-autobuild', JSON.stringify({book:'nope', slips:'999', off:['cs2','lol','nba','nfl']}))")
    p.reload()
    p.wait_for_selector("#hub-generate:visible", timeout=30000)
    assert p.locator("#hub-generation-book").input_value() == "prizepicks" and p.locator("#hub-generation-slips").input_value() == "8"
    assert p.locator("#hub-generation-sports input:checked").count() == 4          # every sport off would build nothing: all back on
    assert not p.errors


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_generated_slips_come_into_view_with_focus_on_their_heading(browser, env, v2, size):
    c = context(browser, env, size)
    try:
        p = slips_page(c, env, "/#/slips?tab=build", size)
        p.locator("#hub-generate").click()
        p.wait_for_function("!document.querySelector('#hub-generate').disabled", timeout=30000)
        assert "slips generated and rechecked" in p.locator("#hub-generation-status").inner_text()
        assert p.evaluate("document.activeElement && document.activeElement.closest('#hub-generated') && document.activeElement.tagName") == "H2"
        top = p.evaluate("document.getElementById('hub-generation-status').getBoundingClientRect().top")
        assert 4 <= top < 200, top                                            # the result line sits at the top, the slips under it
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Clear asks once

def add_two(p):
    p.goto(p.url.split("#")[0] + "#/board?sport=cs2")
    p.wait_for_selector("#hub-rows [data-hub=add]", timeout=30000)
    for i in range(2):
        p.locator("#hub-rows [data-hub=add]").nth(i).click()
        p.wait_for_timeout(250)


def test_clear_in_the_slip_needs_a_second_press(ctx, env, v2):
    p = open_page(ctx, env, "/#/board?sport=cs2")
    add_two(p)
    p.wait_for_function("document.getElementById('hub-slip-count').textContent === '2'", timeout=15000)
    clear = p.locator(".h-slip [data-u=clear]")
    clear.click()
    assert clear.inner_text() == "Clear 2 picks?" and p.locator("#hub-slip-count").inner_text() == "2"
    assert "remove all 2 picks" in clear.get_attribute("aria-label")
    p.wait_for_timeout(5300)                                                   # unconfirmed: it goes back to Clear
    assert clear.inner_text() == "Clear" and p.locator("#hub-slip-count").inner_text() == "2"
    clear.click()
    p.locator(".h-slip [data-u=clear]").click()
    p.wait_for_function("document.getElementById('hub-slip-count').textContent === '0'", timeout=5000)
    assert not p.errors


def test_clear_on_my_slip_needs_a_second_press(ctx, env, v2):
    p = open_page(ctx, env, "/#/board?sport=cs2", PHONE)
    add_two(p)
    p.locator('#u-hub .h-tabbar [data-hub="section"][data-id="studio"]').click()
    p.wait_for_selector("#hub-studio-body [data-u=clear]", timeout=15000)
    p.locator("#hub-studio-body [data-u=clear]").click()
    assert p.locator("#hub-studio-body [data-u=clear]").inner_text() == "Clear 2 picks?"
    assert p.locator("#hub-studio-body .u-leg").count() == 2
    p.locator("#hub-studio-body [data-u=clear]").click()
    p.wait_for_selector('#hub-studio-body [data-hub="browse-props"]', timeout=5000)
    assert not p.errors


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


def test_best_prices_leg_on_the_slip_says_so(ctx, env, ev_on, v2):
    """The pick'em fixture's legs are put on the NBA board too (as a member arriving from the board would have them), so "+ Slip"
    really adds. Before: the button kept reading "+ Slip" after the add; now it reads "On slip ✓", focus stays on it, and the other
    side of the same line says which side is on the slip."""
    from tests.test_pickem_ev_browser import ITEMS

    def board(route):
        r = route.fetch()
        d = r.json()
        d["records"] = list(d.get("records") or []) + [rec for rec, _ in ITEMS]
        route.fulfill(response=r, json=d)
    ctx.route("**/api/upgrade/board?sport=nba*", board)
    p = open_page(ctx, env, "/#/board?sport=nba")
    p.wait_for_selector("#hub-rows tr", timeout=30000)
    p.evaluate("location.hash = '#/ev'")
    p.wait_for_selector("#hub-ev-body .ev-row", timeout=30000)
    add = p.locator('#hub-ev-body .ev-list:not(.ev-list-model) [data-ev="add"]').first
    key, side = add.get_attribute("data-id"), add.get_attribute("data-side")
    sel = f'#hub-ev-body [data-ev="add"][data-id="{key}"][data-side="{side}"]'
    assert p.locator(sel).inner_text() == "+ Slip"
    p.locator(sel).click()
    p.wait_for_function(f"document.querySelector('{sel}').textContent === 'On slip ✓'", timeout=10000)
    assert p.evaluate("document.activeElement.dataset.ev") == "add"
    assert "is on your slip" in p.locator(sel).get_attribute("aria-label")
    p.locator("#hub-ev-body .ev-drawer summary").click()
    p.locator('#hub-ev-body .ev-quick [data-ev="evonly"]').click()        # every priced leg: the other side of the line is listed too
    other = p.locator(f'#hub-ev-body [data-ev="add"][data-id="{key}"]:not([data-side="{side}"])')
    assert other.count() == 1 and other.first.inner_text() == ("Over" if side == "over" else "Under") + " on slip"
    # removing it in the slip column turns the button back
    p.locator(".h-slip [data-u=leg-x]").first.click()
    p.wait_for_function(f"document.querySelector('{sel}').textContent === '+ Slip'", timeout=10000)
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
