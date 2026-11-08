"""QoL 2026-11-07 (owner: "go over entire ui and do a QOL update"), classic CS2 page and the public record, in a real browser
against a local server.Handler with fixture data (no network, no production writes). UI only - nothing here touches a grade,
chance, stake or slip leg choice:
- board / Compare / Moneylines search ignores case and accents; a search with no match offers Clear search
- the player card is a real modal: focus moves to Close, Tab stays inside, closing returns focus to the row; rows open with Enter
- a failed card request offers Try again
- phone / tablet: the page's Slip button, slip sheet and the card's action bar sit above the Slip tray bar
(the slip sheet's Esc and the Undo on Clear / ✕ were rolled back on the owner's 2026-11-07 request, Slip studio as on 2f96bab)
- the filters shown / hidden choice survives a reload; a section picked from the nav opens at its own scroll place
- Single prop brings its progress line into view on a phone; Slate grades with Ctrl+Enter and says what to type when empty
- Matchroom closes on a backdrop click; phone tap targets on the board are 40px or more
- /record keeps the last record on screen (dimmed) while the next sport loads
AUDIT_SHOTS=<folder> keeps a screenshot of the changed surfaces."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

SHOTS = Path(os.environ["AUDIT_SHOTS"]) if os.environ.get("AUDIT_SHOTS") else None
COUNT = "(document.querySelector('#bcount').textContent.match(/^(\\d+) of/) || [])[1] || ''"
RECT = """(s) => { const e = document.querySelector(s); if (!e || e.hidden) return null; const r = e.getBoundingClientRect();
  return r.height ? [r.left, r.top, r.right, r.bottom] : null; }"""
ACTIVE_BL = "(document.activeElement && document.activeElement.dataset && document.activeElement.dataset.bl) || document.activeElement.id || ''"


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"qol1107-{name}-{p.viewport_size['width']}.png"))


def overlap(a, b):
    return bool(a and b and a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1)


def board(c, env, size, path="/?view=classic#board"):
    p = open_page(c, env, path, size)
    p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
    p.wait_for_timeout(400)
    return p


def failing_grade(p, status=500):
    calls = []
    def handle(route):
        calls.append(route.request.url)
        route.fulfill(status=status, json={"error": "Something went wrong - please try again (ref test)"})
    p.route("**/api/grade?*", handle)
    return calls


# ------------------------------------------------------------------------------------------------ search

def test_board_search_ignores_case_and_accents_and_offers_clear_search(browser, env):
    c = context(browser, env, DESK)
    try:
        p = board(c, env, DESK)
        p.fill("#bsearch", "vargo")
        p.wait_for_function(COUNT)
        plain = p.evaluate(COUNT)
        assert int(plain) >= 1
        for typed in ("VARGO", "Várgo", "VÁRGO "):
            p.fill("#bsearch", typed)
            p.wait_for_function(f"({COUNT}) === '{plain}'")
        p.fill("#bsearch", "zzzz")
        p.wait_for_selector("#boardout [data-mlclear]")
        text = p.locator("#boardout").inner_text()
        assert "No lines match “zzzz”" in text and "Reset filters" in text
        p.locator("#boardout [data-mlclear]").click()
        p.wait_for_selector("#boardout .brow2.crow")
        assert p.input_value("#bsearch") == ""
        assert p.evaluate("store.get('cs2s_bf', {}).q") == ""                 # the cleared search is what a reload keeps
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ card focus, Enter on a row, Try again

@pytest.mark.parametrize("size", [DESK, PHONE])
def test_card_takes_focus_keeps_tab_inside_and_gives_focus_back_to_its_row(browser, env, size):
    c = context(browser, env, size)
    try:
        p = board(c, env, size)
        calls = failing_grade(p)
        row = p.evaluate("document.querySelector('#boardout .brow2.crow').dataset.bl")
        p.evaluate("document.querySelector('#boardout .brow2.crow').focus()")
        assert p.evaluate(ACTIVE_BL) == row
        p.keyboard.press("Enter")                                            # Enter on a row opens its card, like a tap
        p.wait_for_function("!document.querySelector('#cardpop').hidden")
        assert p.evaluate("document.activeElement.id") == "popclose"
        for _ in range(12):
            p.keyboard.press("Tab")
            assert p.evaluate("document.querySelector('#popbody').contains(document.activeElement)")
        p.keyboard.press("Shift+Tab")
        assert p.evaluate("document.querySelector('#popbody').contains(document.activeElement)")
        # the failed request offers Try again, which asks for the same line once more
        p.wait_for_selector("#status [data-retrygrade]", timeout=15000)
        n = len(calls)
        shot(p, "card-try-again")
        p.locator("#status [data-retrygrade]").click()
        p.wait_for_function(f"document.querySelector('#status [data-retrygrade]')", timeout=15000)
        assert len(calls) == n + 1 and calls[-1].split("?")[1] == calls[0].split("?")[1]
        p.keyboard.press("Escape")
        p.wait_for_function("document.querySelector('#cardpop').hidden")
        assert p.evaluate(ACTIVE_BL) == row                                   # back on the row that opened it
        assert p.evaluate("location.hash") == "#board/all"
        # Space on a Top plays card opens it too, and ✕ gives focus back to that card
        top = p.evaluate("document.querySelector('#ot-board-overview .tplay').dataset.bl")
        p.evaluate("document.querySelector('#ot-board-overview .tplay').focus()")
        p.keyboard.press(" ")
        p.wait_for_function("!document.querySelector('#cardpop').hidden")
        p.locator("#popclose").click()
        p.wait_for_function("document.querySelector('#cardpop').hidden")
        assert p.evaluate(ACTIVE_BL) == top
        assert not p.errors
    finally:
        c.close()


def test_a_name_the_member_must_fix_gets_no_try_again(browser, env):
    c = context(browser, env, DESK)
    try:
        p = board(c, env, DESK)
        p.route("**/api/grade?*", lambda r: r.fulfill(status=404, json={"error": "No player found", "suggestions": ["vargo"]}))
        p.evaluate("location.hash = '#one'")
        p.fill("#player", "vargoo")
        p.fill("#line", "30.5")
        p.locator("#go").click()
        p.wait_for_selector("#status.err")
        assert p.locator("#status [data-dym]").count() == 1 and p.locator("#status [data-retrygrade]").count() == 0
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ phone / tablet slip vs the Slip tray

@pytest.mark.parametrize("width", [390, 700, 900])
def test_slip_button_and_sheet_sit_above_the_slip_tray(browser, env, width):
    size = {"width": width, "height": 844}
    c = context(browser, env, size)
    try:
        p = board(c, env, size)
        p.locator("#boardout .crow .bslip[data-bslip]:visible").first.click()
        p.wait_for_function("!document.querySelector('#slipbtn').hidden")
        p.wait_for_timeout(2300)                                              # the tray's lift is re-measured every 2 s
        tray, btn, modes = p.evaluate(RECT, "#u-tray-btn"), p.evaluate(RECT, "#slipbtn"), p.evaluate(RECT, "#modes")
        assert tray and btn and not overlap(tray, btn), (tray, btn)
        if modes and p.evaluate("getComputedStyle(document.getElementById('modes')).position") == "fixed":
            assert not overlap(tray, modes), (tray, modes)                    # the tray never covers the section nav
        p.locator("#slipbtn").click()                                          # a real tap reaches it (nothing on top)
        p.wait_for_function("!document.querySelector('#slip').hidden")
        assert not overlap(p.evaluate(RECT, "#slip"), p.evaluate(RECT, "#u-tray-btn"))
        shot(p, "slip-sheet")
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_phone_card_actions_stay_above_the_slip_tray(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = board(c, env, PHONE)
        failing_grade(p)
        p.locator("#boardout .brow2.crow b").first.click()
        p.wait_for_function("!document.querySelector('#cardpop').hidden")
        p.evaluate("""() => { document.querySelector('#out').innerHTML = '<section class="card" style="height:1600px">card</section>'; popActions();
          document.querySelector('#popbody').scrollTop = 1e6; }""")
        p.wait_for_timeout(2300)
        act, tray = p.evaluate(RECT, "#popact"), p.evaluate(RECT, "#u-tray-btn")
        assert act and tray and act[3] <= tray[1] + 1, (act, tray)
        shot(p, "card-actions")
        p.locator("#popclose").click()
        p.wait_for_function("document.querySelector('#cardpop').hidden")
        p.wait_for_timeout(2300)
        tray, modes = p.evaluate(RECT, "#u-tray-btn"), p.evaluate(RECT, "#modes")
        assert modes and tray[3] <= modes[1] + 1                              # the nav is back, the tray above it again
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ remembered filters, section scroll

@pytest.mark.parametrize("size", [DESK, PHONE])
def test_the_filters_shown_or_hidden_choice_survives_a_reload(browser, env, size):
    c = context(browser, env, size)
    try:
        p = board(c, env, size)
        collapsed = "document.querySelector('#board').classList.contains('filters-collapsed')"
        start = p.evaluate(collapsed)
        assert start is (size is PHONE)                                       # first visit: closed on a phone, open wider
        p.locator("#scout-filters").click()
        assert p.evaluate(collapsed) is not start
        p.reload()
        p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
        assert p.evaluate(collapsed) is not start
        assert p.locator("#scout-filters").get_attribute("aria-expanded") == ("true" if start else "false")
        assert not p.errors
    finally:
        c.close()


def test_a_section_from_the_nav_opens_at_its_own_scroll_place(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = board(c, env, PHONE)
        p.evaluate("window.scrollTo(0, 1200)")
        p.wait_for_timeout(200)
        y = p.evaluate("scrollY")
        assert y > 600
        p.locator("#mode button[data-v=slips]").click()
        p.wait_for_function("location.hash === '#slips'")
        p.wait_for_timeout(200)
        assert p.evaluate("scrollY") == 0                                      # Slips opens at its top
        p.locator("#mode button[data-v=board]").click()
        p.wait_for_selector("#boardout .brow2.crow")
        p.wait_for_timeout(300)
        assert abs(p.evaluate("scrollY") - y) <= 2                             # the board comes back where it was left
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Single prop, Slate

def test_single_prop_progress_comes_into_view_on_a_phone(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = open_page(c, env, "/?view=classic#one", PHONE)
        p.wait_for_selector("#player")
        failing_grade(p)
        p.fill("#player", "vargo")
        p.fill("#line", "34.5")
        p.locator("#go").click()
        p.wait_for_selector("#status.err")
        p.wait_for_timeout(600)
        r = p.evaluate(RECT, "#status")
        assert r and r[1] >= 0 and r[3] <= PHONE["height"] - 100, r          # on screen, above the bottom bars
        assert p.locator("#status [data-retrygrade]").count() == 1
        assert not p.errors
    finally:
        c.close()


def test_slate_grades_with_ctrl_enter_and_says_what_to_type_when_empty(browser, env):
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/?view=classic#slate", DESK)
        p.wait_for_selector("#slatetxt")
        calls = failing_grade(p)
        p.locator("#slatego").click()
        assert "Type at least one line first" in p.locator("#slatestatus").inner_text()
        assert p.evaluate("document.activeElement.id") == "slatetxt"
        p.fill("#slatetxt", "vargo 34.5 K")
        p.keyboard.press("Control+Enter")
        p.wait_for_function("document.querySelector('#slatestatus').textContent.includes('1/1 graded')", timeout=15000)
        assert len(calls) == 1 and "player=vargo" in calls[0]
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Matchroom backdrop, tap targets

def test_matchroom_closes_on_a_backdrop_click_and_returns_focus(browser, env):
    c = context(browser, env, DESK)
    try:
        p = board(c, env, DESK, "/?view=classic#matches/all")
        p.wait_for_selector("[data-ot-match]")
        opener = p.locator("[data-ot-match]").first
        opener.click()
        p.wait_for_function("document.querySelector('#ot-match-desk').open")
        box = p.evaluate(RECT, "#ot-match-desk")
        assert box[0] > 20                                                     # there is backdrop to its left
        p.mouse.click(5, 450)
        p.wait_for_function("!document.querySelector('#ot-match-desk').open")
        assert p.evaluate("document.activeElement.matches('[data-ot-match]')")
        opener.click()                                                         # a click inside the panel keeps it open
        p.wait_for_function("document.querySelector('#ot-match-desk').open")
        p.locator("#ot-desk-title").click()
        assert p.evaluate("document.querySelector('#ot-match-desk').open")
        assert not p.errors
    finally:
        c.close()


SMALL = """() => [...document.querySelectorAll('#boardout button, #ot-board-overview button, #scout-filters, #who a')]
  .filter(e => e.getClientRects().length && !e.closest('[hidden]')).map(e => { const r = e.getBoundingClientRect();
  return [e.className || e.id || e.tagName, (e.textContent || '').trim().slice(0, 16), Math.round(r.width), Math.round(r.height)]; })
  .filter(x => x[2] < 40 || x[3] < 40)"""


def test_phone_board_tap_targets_are_at_least_40px(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = board(c, env, PHONE)
        p.wait_for_selector("#boardout .u-res-btn", timeout=20000)
        assert p.evaluate(SMALL) == []
        no_h_scroll(p)
        shot(p, "phone-board")
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ /record

def test_record_keeps_the_last_sport_on_screen_while_the_next_loads(browser, env, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    doc = {"totals": {"wins": 6, "losses": 4, "pushes": 1, "observed": 0.6, "printed": 0.58}, "bands": [], "population": "p", "rule": "r"}
    c = browser.new_context(viewport=DESK)
    try:
        p = c.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        p.route("**/api/record?*", lambda r: r.fulfill(json=doc))
        p.goto(env.url + "/record")
        p.wait_for_function("document.querySelector('#record h2') && document.querySelector('#record h2').textContent === 'CS2'")
        p.evaluate("""() => { const f = window.fetch; window.fetch = (...a) => new Promise(ok => setTimeout(() => ok(f(...a)), 700)); }""")
        p.locator("[data-sport=lol]").click()
        p.wait_for_function("document.querySelector('#record').getAttribute('aria-busy') === 'true'")
        assert p.locator("#record h2").inner_text() == "CS2"                   # the old record stays, dimmed - no jump
        p.wait_for_function("document.querySelector('#record h2').textContent === 'LoL'")
        assert p.evaluate("document.querySelector('#record').hasAttribute('aria-busy')") is False
        assert not p.errors
    finally:
        c.close()
