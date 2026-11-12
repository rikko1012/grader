"""Member navigation and sportsbook UI against the real Handler, isolated prices only."""
from datetime import datetime, timedelta, timezone

import pytest
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll


@pytest.mark.parametrize('width', [390, 768, 1440])
@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_sportsbooks_route_filters_refresh_and_layout(browser, env, monkeypatch, tmp_path, width, theme):
    monkeypatch.setenv('UI_NEXT', '1')
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    c = context(browser, env, {'width': width, 'height': 900})
    c.add_init_script("localStorage.setItem('scout-theme', '%s')" % theme)
    p = c.new_page()
    errors = []
    p.on('pageerror', lambda e: errors.append(str(e)))
    now = datetime.now(timezone.utc)
    def response(route):
        league = 'nfl' if 'league=nfl' in route.request.url else 'nba'
        route.fulfill(json={'league': league, 'source': 'ESPN', 'observed_at': now.isoformat(), 'events': [{
            'id': 'espn:test', 'away': 'Golden State Warriors', 'home': 'Minnesota Timberwolves',
            'start_utc': (now + timedelta(days=1)).isoformat(), 'seen_at': None,
            'books': {'draftkings': {'id': 'draftkings', 'label': 'DraftKings', 'markets': {
                'money-line': {'awayOdds': 150, 'homeOdds': -170},
                'spread': {'awaySpread': 3.5, 'homeSpread': -3.5, 'awayOdds': -110, 'homeOdds': -110},
                'totals': {'total': 231.5, 'overOdds': -115, 'underOdds': -105}}}}}]})
    p.route('**/api/upgrade/game-books?*', response)
    try:
        p.goto(env.url + '/#/books')
        panel = p.locator('#hub-book-board')
        panel.locator('summary').wait_for()
        assert p.locator('#hub-title').inner_text().lower() == 'sportsbooks'
        assert panel.locator('[data-gb=book]').input_value() == 'draftkings'
        assert panel.locator('.h-book-controls label').first.evaluate('(e)=>getComputedStyle(e).color') == panel.locator('h2').evaluate('(e)=>getComputedStyle(e).color')
        assert 'Quote age unavailable' in panel.inner_text()
        panel.locator('summary').click()
        assert '+150' in panel.inner_text() and 'Over 231.5' in panel.inner_text()
        panel.locator('[data-gb=refresh]').click()
        panel.locator('details[open]').wait_for()
        assert panel.locator('[data-gb=refresh]').evaluate('(e)=>e===document.activeElement')
        panel.locator('[data-gb=league]').focus()
        panel.locator('[data-gb=league]').select_option('nfl')
        p.wait_for_function("document.querySelector('#hub-book-board [data-gb=league]')?.value==='nfl' && document.querySelector('#hub-book-board').getAttribute('aria-busy')==='false'")
        assert panel.locator('[data-gb=league]').evaluate('(e)=>e===document.activeElement')
        no_h_scroll(p)
        # The summary, including both team names, must fit its panel.
        assert panel.locator('summary').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        p.screenshot(path=str(tmp_path / f'books-{width}-{theme}.png'))
        p.reload()
        panel.locator('summary').wait_for()
        assert p.locator('#hub-books-section').is_visible()
        if width == 390:
            p.locator('#hub-more-toggle').click()
            p.locator('#hub-more [data-id=books]').click()
            assert p.locator('#hub-more').is_hidden()
        else:
            assert p.locator('.h-sidebar [data-id=books]').get_attribute('aria-current') == 'page'
        assert not errors
    finally:
        c.close()


def test_empty_and_failed_books_keep_an_honest_selection(browser, env, monkeypatch):
    monkeypatch.setenv('UI_NEXT', '1')
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    c = context(browser, env)
    p = c.new_page()
    calls = []
    def response(route):
        calls.append(1)
        if len(calls) == 1:
            return route.fulfill(status=503, json={'error': 'unavailable'})
        route.fulfill(json={'source': 'ESPN', 'pending': True, 'events': []})
    p.route('**/api/upgrade/game-books?*', response)
    try:
        p.goto(env.url + '/#/books')
        p.locator('#hub-book-board [data-gb=retry]').click()
        p.locator('#hub-book-board [data-gb=book]').wait_for()
        assert p.locator('#hub-book-board [data-gb=book]').input_value() == 'draftkings'
        assert 'prices are being collected' in p.locator('#hub-book-board').inner_text().lower()
        assert 'no upcoming game prices' not in p.locator('#hub-book-board').inner_text().lower()
    finally:
        c.close()


def test_grouped_prop_keeps_keyboard_focus_when_offer_id_changes(browser, env):
    from tests.test_ui_next_browser import nxt, nba, ladder_board, row_of, DESK
    c = nxt(browser, env, DESK, board=ladder_board())
    try:
        p = nba(c, env, DESK)
        player = row_of(p, 'Shai Ladder', 'Points').locator('.h-player')
        player.focus()
        # Emulate the focused offer being superseded before the next grouped-row paint.
        player.evaluate("e=>e.dataset.id='superseded-offer'")
        p.locator('#hub-q').evaluate("e=>e.dispatchEvent(new Event('input',{bubbles:true}))")
        p.wait_for_function("document.activeElement?.classList.contains('h-player') && document.activeElement.dataset.id!=='superseded-offer'")
        assert 'Shai Ladder' in p.evaluate('document.activeElement.innerText')
        p.keyboard.press('ArrowRight')
        p.wait_for_selector('.h-ladder')
        assert not p.errors
    finally:
        c.close()



