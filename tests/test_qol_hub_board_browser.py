"""Board QOL pass (owner 2026-11-07: "go over entire ui and do a QOL update"), new board list and player card, in a real browser against
the real Handler and fixture data (no network, no production writes). Display and navigation only: no grade, chance, projection,
stake, letter or slip leg moves.

- search ignores case, accents and extra spaces; Escape clears it; the phone keyboard's Search key puts the keyboard away
- the toolbar's search, platform and filters survive a reload in the same tab (a new tab starts clean)
- leaving the board and coming back returns to the same scroll place; Back to the same sport keeps the market filter
- Enter in the Save view box saves; focus comes back to Views; an empty Views menu says so and looks disabled
- a saved view on another sport opens that sport (address, board read), closes the menu and names the view
- Filters > Done hands focus back to Filters; the refresh button shows it is working
- phone: Back closes the full-screen card (and only the card); the game header keeps its countdown; card window buttons are 40px"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401
from tests.workspace_fixture import iso, row


def hub(c, env, size=DESK, path='/#all-sports'):
    p = open_page(c, env, path, size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    return p


def names(p):
    return p.locator('#hub-rows tr[data-record] .h-player b').all_inner_texts()


def test_search_ignores_case_accents_and_spaces_and_escape_clears(browser, env):
    soon = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    env.rows.append(row("pp:7099", "prizepicks", "Šárka", "Halcyon", "Norrland", 30.5, "More", 0.6, 900, soon))
    c = context(browser, env, DESK)
    try:
        p = hub(c, env)
        p.wait_for_function("[...document.querySelectorAll('#hub-rows .h-player b')].some(b => b.textContent === 'Šárka')")
        p.locator('#hub-q').fill('sarka')
        assert names(p) == ['Šárka']
        p.locator('#hub-q').fill('  VARGO  ')
        assert names(p) == ['vargo']
        p.locator('#hub-q').fill('halcyon   vs')                      # extra spaces inside the words too
        assert 'vargo' in names(p)
        p.keyboard.press('Escape')                                     # first Escape clears the search, focus stays in the box
        assert p.locator('#hub-q').input_value() == ''
        assert p.evaluate("document.activeElement.id") == 'hub-q'
        all_n = len(names(p))
        assert all_n > 3
        p.keyboard.press('Escape')                                       # a second one leaves the box
        assert p.evaluate("document.activeElement.id") != 'hub-q'
        assert p.locator('#hub-q').get_attribute('enterkeyhint') == 'search'
        assert not p.errors
    finally:
        c.close()


def test_toolbar_state_survives_reload_but_not_a_new_tab(browser, env):
    c = context(browser, env, DESK)
    try:
        p = hub(c, env)
        p.locator('#hub-platform [data-id=underdog]').click()
        p.locator('#hub-q').fill('vargo')
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-side').select_option('over')
        p.locator('#hub-filter-pop [data-hub=filters]').click()
        p.reload()
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        assert p.locator('#hub-q').input_value() == 'vargo'
        assert p.locator('#hub-platform [data-id=underdog]').get_attribute('aria-pressed') == 'true'
        assert p.locator('#hub-side').input_value() == 'over'
        assert p.locator('#hub-filter-n').inner_text() == '1'
        assert names(p) == ['vargo']
        p2 = hub(c, env)                                                 # another tab of the same browser starts clean
        assert p2.locator('#hub-q').input_value() == '' and p2.locator('#hub-platform [data-id=all]').get_attribute('aria-pressed') == 'true'
        assert not p.errors and not p2.errors
    finally:
        c.close()


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_board_place_is_kept_after_another_page_and_back(browser, env, size):
    c = context(browser, env, size)
    try:
        p = hub(c, env, size, '/#scout/board?sport=cs2')
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-stat').select_option(label='Kills')
        p.keyboard.press('Escape')
        p.locator('#hub-sports [data-id=all]').click()
        p.wait_for_function("document.querySelectorAll('#hub-rows tr[data-record]').length > 8")
        top = p.evaluate("(() => { const c = document.getElementById('hub-center'); c.scrollTop = Math.min(400, c.scrollHeight - c.clientHeight); return c.scrollTop; })()")
        assert top > 100
        nav = '.h-sidebar' if size == DESK else '.h-tabbar'
        p.locator(f'{nav} [data-id=picks]').click()
        p.wait_for_selector('#hub-picks-section:not([hidden])')
        p.locator(f'{nav} [data-id=board]').click()
        p.wait_for_selector('#hub-board-section:not([hidden])')
        assert abs(p.evaluate("document.getElementById('hub-center').scrollTop") - top) < 2
        # Back from another page to the same sport keeps the market filter the member left
        p.locator('#hub-sports [data-id=cs2]').click()
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-stat').select_option(label='Kills')
        p.keyboard.press('Escape')
        p.locator(f'{nav} [data-id=studio]').click()
        p.wait_for_selector('#hub-studio-section:not([hidden])')
        p.go_back()
        p.wait_for_selector('#hub-board-section:not([hidden])')
        assert p.locator('#hub-stat option:checked').inner_text() == 'Kills'
        assert p.locator('#hub-filter-n').inner_text() == '1'
        # a different sport still starts on All markets
        p.locator('#hub-sports [data-id=nba]').click()
        assert p.locator('#hub-stat').input_value() == 'all'
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_saved_view_enter_saves_focus_returns_and_another_sport_opens_properly(browser, env):
    c = context(browser, env, DESK)
    try:
        p = hub(c, env, DESK, '/#scout/board?sport=nba')
        p.locator('#hub-views-toggle').click()
        assert p.locator('#hub-views').is_disabled() and 'No saved views yet' in p.locator('#hub-views').inner_text()
        p.locator('#hub-views-pop [data-hub=save-view]').click()
        p.locator('#hub-save-name').fill('NBA board')
        p.keyboard.press('Enter')                                        # Enter saves (it pressed Cancel before)
        p.wait_for_function("document.querySelectorAll('#hub-views option').length === 2")
        assert not p.locator('#hub-save-dialog').evaluate("d => d.open")
        assert p.evaluate("document.activeElement.id") == 'hub-views-toggle'
        p.locator('#hub-sports [data-id=cs2]').click()
        p.wait_for_function("location.hash.includes('sport=cs2')")
        p.locator('#hub-views-toggle').click()
        assert p.locator('#hub-views').is_enabled()
        p.locator('#hub-views').select_option('0')
        p.wait_for_function("location.hash.includes('sport=nba')")
        assert p.locator('#hub-sports [data-id=nba]').get_attribute('aria-pressed') == 'true'
        assert p.locator('#hub-views-pop').is_hidden()
        p.wait_for_function("document.getElementById('u-toast').textContent.includes('NBA board')")
        p.wait_for_selector('#hub-rows tr[data-record]')
        assert all(t == 'NBA' for t in p.locator('#hub-rows tr[data-record] .h-player i').all_inner_texts())
        p.reload()                                                      # the address followed, so a reload keeps the view's sport
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        assert p.locator('#hub-sports [data-id=nba]').get_attribute('aria-pressed') == 'true'
        assert not p.errors
    finally:
        c.close()


def test_filters_done_returns_focus_and_refresh_shows_it_is_working(browser, env):
    c = context(browser, env, DESK)
    try:
        p = hub(c, env)
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-filter-pop [data-hub=filters]').click()
        assert p.locator('#hub-filter-pop').is_hidden()
        assert p.evaluate("document.activeElement.id") == 'hub-filters-toggle'
        assert p.locator('#u-hub .h-refresh').get_attribute('aria-busy') == 'false'
        held = []
        p.route('**/api/upgrade/board*', lambda r: held.append(r))                  # the board reads wait until the busy state is seen
        p.locator('#u-hub .h-refresh').click()
        p.wait_for_function("document.querySelector('#u-hub .h-refresh').getAttribute('aria-busy') === 'true'")
        assert p.locator('#u-hub .h-refresh').get_attribute('title') == 'Refreshing…'
        for _ in range(100):                                             # let the held reads reach the handler
            if held:
                break
            p.wait_for_timeout(50)
        assert held and p.locator('#u-hub .h-refresh').get_attribute('aria-busy') == 'true'
        for r in held:
            r.continue_()
        p.unroute('**/api/upgrade/board*')
        p.wait_for_function("document.querySelector('#u-hub .h-refresh').getAttribute('aria-busy') === 'false'", timeout=15000)
        assert p.locator('#hub-rows tr[data-record]').count() > 0
        assert not p.errors
    finally:
        c.close()


def test_phone_back_closes_only_the_card_countdown_and_targets(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = hub(c, env, PHONE)
        # the game header keeps its start countdown on a phone, inside the screen
        cd = p.locator('#hub-rows .h-game-head .h-countdown').first
        assert cd.is_visible() and cd.inner_text().startswith('in ')
        assert cd.bounding_box()['x'] + cd.bounding_box()['width'] <= PHONE['width']
        p.locator('#hub-sports [data-id=cs2]').click()
        p.locator('.h-tabbar [data-id=picks]').click()
        p.locator('.h-tabbar [data-id=board]').click()
        p.wait_for_selector('#hub-rows tr[data-record]')
        url = p.url
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        small = p.evaluate("""[...document.querySelectorAll('#hub-detail [data-hub=history-window]')].filter(b => b.offsetParent)
          .map(b => b.getBoundingClientRect()).filter(r => r.width < 39.5 || r.height < 39.5).length""")
        assert small == 0
        p.go_back()                                                      # the phone's Back closes the card and stays on the board
        p.wait_for_selector('#hub-card', state='hidden')
        assert p.url == url and p.locator('#hub-board-section').is_visible()
        # closed with its own Back button, the extra entry is taken off: Back then leaves the board as before
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        p.locator('#hub-detail [data-hub=card-close]').click()
        p.wait_for_function("!(history.state && history.state.hubCard)")
        p.go_back()
        p.wait_for_selector('#hub-picks-section:not([hidden])')
        # Escape closes it too, the same way
        p.go_forward()
        p.wait_for_selector('#hub-board-section:not([hidden]) #hub-rows tr[data-record]')
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        p.keyboard.press('Escape')
        p.wait_for_function("!(history.state && history.state.hubCard)")
        assert p.locator('#hub-card').is_hidden() and p.url == url
        # the keyboard's Search key puts the keyboard away
        p.locator('#hub-q').fill('vargo')
        p.keyboard.press('Enter')
        assert p.evaluate("document.activeElement.id") != 'hub-q' and names(p) == ['vargo']
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_desktop_card_adds_no_history_entry(browser, env):
    c = context(browser, env, DESK)
    try:
        p = hub(c, env)
        n = p.evaluate("history.length")
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        assert p.evaluate("history.length") == n
        p.keyboard.press('Escape')
        assert p.locator('#hub-card').is_hidden()
        assert not p.errors
    finally:
        c.close()


def test_phone_back_still_closes_a_card_after_leaving_the_board_from_a_card(browser, env):
    """Reviewer finding (2026-11-07): a card closed because the member moved to another page ("On slip ✓" -> Slips) left its history
    entry marked as the card's. Back to the board landed on that marked entry, so the next card added no entry of its own and the
    phone's Back left that card open. Now the leaving close clears the marker, and a marker found with no card open is cleared too."""
    c = context(browser, env, PHONE)
    try:
        p = hub(c, env, PHONE)
        p.locator('#hub-sports [data-id=cs2]').click()
        p.wait_for_selector('#hub-rows tr[data-record]')
        n = p.locator('#hub-rows .h-player').count()
        added = False
        for i in range(n):
            p.locator('#hub-rows .h-player').nth(i).click()
            p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
            add = p.locator('#hub-detail [data-hub=detail-add]')
            if add.count() and add.is_enabled():
                add.click()
                p.wait_for_function("document.querySelector('#hub-detail [data-hub=detail-add]')?.textContent === 'On slip ✓'", timeout=10000)
                added = True
                break
            p.go_back()
            p.wait_for_selector('#hub-card', state='hidden')
        assert added, "no card in the fixture can be added to the slip"
        p.locator('#hub-detail [data-hub=detail-add]').click()            # "On slip ✓" -> the Slips page, card closed
        p.wait_for_selector('#hub-studio-section:not([hidden])')
        assert p.locator('#hub-card').is_hidden()
        p.go_back()                                                       # back on the board
        p.wait_for_selector('#hub-board-section:not([hidden]) #hub-rows tr[data-record]')
        assert not p.evaluate("!!(history.state && history.state.hubCard)")
        url = p.url
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        p.go_back()                                                       # one Back closes this card and stays on the board
        p.wait_for_selector('#hub-card', state='hidden')
        assert p.url == url and p.locator('#hub-board-section').is_visible()
        # a reload with the card up leaves a marked entry and no card: the next card still gets its own entry
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        p.reload()
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        assert p.locator('#hub-card').is_hidden()
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
        p.go_back()
        p.wait_for_selector('#hub-card', state='hidden')
        assert p.locator('#hub-board-section').is_visible()
        assert not p.errors
    finally:
        c.close()
