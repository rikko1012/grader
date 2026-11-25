"""Member navigation and sportsbook UI against the real Handler, isolated prices only."""
from datetime import datetime, timedelta, timezone

import pytest
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, context, open_page, no_h_scroll


@pytest.mark.parametrize('width',[390,1440])
def test_draftkings_reference_props_search_and_no_fake_prices(browser,env,monkeypatch,tmp_path,width):
    monkeypatch.setenv('UI_NEXT','1');monkeypatch.setenv('SCOUT_WORKSPACE_V2','1')
    c=context(browser,env,{'width':width,'height':900});p=c.new_page()
    now=datetime.now(timezone.utc);errors=[];p.on('pageerror',lambda e:errors.append(str(e)))
    props=[{'player':'Example Guard','market':'points','label':'Points Milestones - 1st Quarter','period':'1q',
            'kind':'milestone','line':4.5,'seen_at':now.isoformat(),'reported_prices':[-120,105]},
           {'player':'Example Center','market':'rebounds','label':'Total Rebounds','period':'game',
            'kind':'line','line':9.5,'seen_at':now.isoformat()}]
    p.route('**/api/upgrade/game-books?*',lambda r:r.fulfill(json={'league':'nba','source':'ESPN','observed_at':now.isoformat(),
      'events':[{'id':'espn:1','away':'Toronto Raptors','home':'Philadelphia 76ers',
      'start_utc':(now+timedelta(days=1)).isoformat(),'books':{},'reference_props':props}]}))
    try:
        p.goto(env.url+'/#/books');section=p.locator('.h-reference-props');section.wait_for()
        assert 'does not identify which price' in section.inner_text()
        section.locator('summary').click()
        assert '5+' in section.inner_text() and 'First quarter' in section.inner_text()
        assert '-120 / +105' in section.inner_text()
        assert 'side not supplied' in section.inner_text().lower()
        section.locator('input').fill('rebounds')
        assert section.locator('[data-gb-reference]:visible').count()==1
        assert 'Example Center' in section.locator('[data-gb-reference]:visible').inner_text()
        section.locator('input').fill('no match')
        assert '0 matching' in section.inner_text()
        section.locator('input').fill('')
        assert section.locator('[data-gb-reference]:visible').count()==2
        no_h_scroll(p);assert not errors
        p.screenshot(path=str(tmp_path/f'reference-props-{width}.png'))
    finally:c.close()


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




@pytest.mark.parametrize('width', [390, 1440])
def test_draftkings_in_main_board_selector(browser, env, monkeypatch, width):
    import json
    import workspace_game_books
    import book_providers
    monkeypatch.setenv('UI_NEXT', '1')
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_BOOK_LINES', '1')
    monkeypatch.setenv('SCOUT_BOOK_DRAFTKINGS', '1')
    book_providers.reset()
    now = datetime.now(timezone.utc)
    doc = {'league': 'nba', 'events': [{'id': 'espn:1', 'start_utc': (now+timedelta(hours=3)).isoformat(),
           'reference_props': [{'player_id': '301', 'player': 'Karl Anders', 'market': 'points', 'period': 'game',
             'line': 22.5, 'kind': 'line', 'revision_id': 99, 'seen_at': now.isoformat(), 'reported_prices': [-117, 105]}]}]}
    monkeypatch.setattr(workspace_game_books, 'shared', lambda srv, lg: (200, json.dumps(doc if lg=='nba' else {'league':lg,'events':[]}).encode()))
    c=context(browser,env,{'width':width,'height':900});p=c.new_page();errors=[]
    p.on('pageerror',lambda e:errors.append(str(e)))
    try:
        p.goto(env.url+'/#/board')
        p.wait_for_selector('#hub-platform [data-hub="book"][data-id="draftkings"]:not([hidden])',state='attached',timeout=20000)
        dk=in_sheet(p,'#hub-platform [data-hub="book"][data-id="draftkings"]');dk.wait_for(state='visible',timeout=20000);dk.click();close_sheet(p)
        p.locator('tr[data-record]').first.wait_for()
        assert 'Karl Anders' in p.locator('tr[data-record]').first.inner_text()
        assert '-117 / +105' in p.locator('tr[data-record]').first.inner_text()
        # owner 2026-11-13: Grader's saved read at the exact same line sits in the shared Grader cell (here both chances: no side at this
        # line); the reported prices stay in the Line cell. A line with no saved read keeps the "side not supplied" small caps.
        first = p.locator('tr[data-record]').first
        assert 'side not supplied' in first.text_content().lower() or ('Over' in first.locator('.h-c-scout').inner_text() and 'Under' in first.locator('.h-c-scout').inner_text())
        assert not p.locator('tr[data-record] button:has-text("+ Add")').count()
        assert p.locator('th.h-c-scout').inner_text().strip().lower() == 'grader'          # the same column as every other book
        p.locator('tr[data-record] .h-player').first.click()
        p.locator('.h-extra-detail').wait_for()
        assert '-117 / +105' in p.locator('.h-extra-detail').inner_text()
        no_h_scroll(p)
        assert not errors
    finally:
        c.close();book_providers.reset()

