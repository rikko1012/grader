"""2026-11-05 structure review of the all-sports research hub, in a real browser against the real Handler and fixture data (no network,
no production writes): one toolbar row, compact rows under game header rows, ONE player card with a pinned Add, a slip column that keeps
its width, a phone bottom tab bar, My picks, the Slips tabs and the quiet empty states. Layout only: no grade, chance or stake moves."""
import re

import pytest

from tests.test_research_hub_browser import add_first, book, saved_state
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

FLAGS = ("SCOUT_WORKSPACE_V2", "SCOUT_BOARD_H2H", "SCOUT_GENIE", "SCOUT_SLIP_HEADER", "SCOUT_CLOSING_LINES", "SCOUT_PUBLIC_RECORD")
SMALL = """(root) => [...document.querySelectorAll(root+' button,'+root+' select,'+root+' input,'+root+' summary,'+root+' a')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && !e.closest('[hidden]') && getComputedStyle(e).visibility !== 'hidden'; })
  .filter(e => e.getBoundingClientRect().height < 39.5 && !(e.type === 'checkbox' && e.closest('label') && e.closest('label').getBoundingClientRect().height >= 39.5))
  .map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().height) + 'px ' + (e.textContent || '').trim().slice(0, 24))"""


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


def board(ctx, env, size=DESK, path='/#all-sports'):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    return p


def test_desktop_first_row_high_on_the_screen_with_ten_rows_and_one_toolbar(ctx, env):
    p = board(ctx, env)
    m = p.evaluate("""() => { const vh = innerHeight, rows = [...document.querySelectorAll('#hub-rows tr[data-record]')];
      const tb = document.querySelector('.h-toolbar').getBoundingClientRect();
      const ctl = [...document.querySelectorAll('.h-toolbar > *')].map(e => Math.round(e.getBoundingClientRect().top));
      return {first: document.querySelector('#hub-rows tr').getBoundingClientRect().top, toolbarH: tb.height, tops: ctl,
              visible: rows.filter(r => { const b = r.getBoundingClientRect(); return b.top >= 0 && b.bottom <= vh; }).length,
              heights: rows.slice(0, 12).map(r => Math.round(r.getBoundingClientRect().height)),
              player: Math.round(document.querySelector('#hub-rows tr[data-record] td.h-c-player').getBoundingClientRect().width),
              edge: !!document.querySelector('#hub-table thead .h-c-edge')}; }""")
    assert m['player'] >= 120, m                                 # the name keeps its width even with the Edge column on
    assert m['first'] <= 200, m                                  # was 512px before the review
    assert m['visible'] >= 10, (m, p.locator('#hub-sources').inner_text(), p.locator('#hub-health').inner_text(), p.locator('.h-table-title').inner_text())   # was 4
    assert m['toolbarH'] <= 48 and max(m['tops']) - min(m['tops']) <= 2, m   # sport tabs, search, platform, Filters, Views and refresh: one row
    assert all(56 <= h <= 64 for h in m['heights']), m           # compact rows (was 123px)
    # the old bands are gone: no matchup chips, no second filter row, no duplicate classic button or More tools group
    assert p.locator('#hub-games, .h-games, .h-tools-menu, .h-classic, .h-nav-mobile, .h-mobile-slip').count() == 0
    assert p.locator('#u-hub [data-hub=close]').locator('visible=true').count() == 1
    assert p.locator('.h-sidebar nav [data-hub=section]').all_inner_texts() == ['Board', 'Slips', 'My picks', 'Record', 'Tools']
    # matchup and countdown live on the game header rows, and a header click filters to that game
    head = p.locator('#hub-rows tr.h-game-row [data-hub=game]').first
    event = head.locator('.h-match').inner_text()
    assert head.locator('.h-countdown').inner_text().startswith('in ')
    head.click()
    assert p.locator('#hub-rows tr.h-game-row').count() == 1 and p.locator('#hub-rows .h-match').inner_text() == event
    assert p.locator('#hub-filter-n').inner_text() == '1'
    p.locator('#hub-rows [data-hub=game]').click()
    assert p.locator('#hub-rows tr.h-game-row').count() > 1
    assert p.locator('#hub-rows .h-match').count() == p.locator('#hub-rows tr.h-game-row').count()
    assert p.evaluate("document.querySelectorAll('#hub-rows tr[data-record] .h-c-player small').length") > 0
    assert not p.errors


def test_one_scout_cell_and_one_hit_strip_with_counts_in_tooltips(ctx, env):
    p = board(ctx, env)
    heads = p.locator('#hub-table thead th').all_text_contents()
    assert [h.strip() for h in heads][:6] == ['Watch', 'Player', 'Market', 'Line', 'Proj.', 'Scout']
    row = p.locator('#hub-rows tr[data-record]').first
    cells = row.locator('.h-strip .h-hit')
    assert cells.count() == 5                                     # L5 L10 L20 H2H SZN
    for i in range(cells.count()):
        c = cells.nth(i)
        text, title = c.inner_text(), c.get_attribute('title') or ''
        assert text == '—' or re.fullmatch(r'\d+%|\d+-\d+', text), text
        assert text == '—' or re.search(r'\d+ (Over|Under)', title), title     # the counts live in the tooltip
    scout = row.locator('.h-c-scout').inner_text()
    assert re.search(r'(Over|Under)\s+\d+\.\d%', scout) and (row.locator('.h-grade').count() == 1 or 'Lean' in scout or 'Research' in scout)
    # Edge only when a visible row has a value (the fixture's NBA board has none)
    p.locator('#hub-sports [data-id=nba]').click()
    assert p.locator('#hub-table thead .h-c-edge').count() == 0
    # the Sports grades note shows on the NBA and NFL tabs only
    assert p.locator('#hub-health details').count() == 1
    p.locator('#hub-sports [data-id=cs2]').click()
    assert p.locator('#hub-health details').count() == 0
    p.locator('#hub-sports [data-id=all]').click()
    assert p.locator('#hub-health details').count() == 0
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_every_entry_point_opens_the_one_card_with_a_pinned_add(ctx, env, size):
    p = board(ctx, env, size)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-q').fill('vargo')
    k = p.locator('#hub-rows tr[data-record]').first.get_attribute('data-record')
    # 1) the row
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail [data-hub=detail-add]:not([disabled])', timeout=20000)
    assert p.locator('#hub-card').count() == 1 and p.locator('#hub-detail').count() == 1
    assert p.locator('#hub-detail [role=tab]').all_inner_texts() == ['Overview', 'Lines', 'Stats', 'Ask']
    add = p.locator('#hub-detail [data-hub=detail-add]')
    y0 = add.bounding_box()['y']
    assert 0 <= y0 and y0 + add.bounding_box()['height'] <= (size['height'] if size else 900)   # visible without scrolling
    p.locator('#hub-card-body').evaluate('el => el.scrollTop = el.scrollHeight')
    assert abs(add.bounding_box()['y'] - y0) < 1                                                # pinned while the tab scrolls
    if size == PHONE:
        box = p.locator('#hub-card').bounding_box()
        assert box['x'] == 0 and box['width'] == 390 and p.locator('#hub-detail [data-hub=card-close]').inner_text().endswith('Back')
    else:
        assert p.locator('.h-slip').is_visible()                                                # the panel leaves the slip column in view
    assert not p.locator('#u-drawer').is_visible() and not p.locator('#u-tray').is_visible()
    # Escape / Back closes it
    p.locator('#hub-detail [data-hub=card-close]').click()
    assert p.locator('#hub-card').is_hidden()
    # 2) Compare opens the same card on Lines
    p.locator('#hub-rows .h-compare').first.click()
    p.wait_for_selector('#hub-detail .h-line-shop table')
    assert p.locator('#hub-detail [role=tab][aria-selected=true]').inner_text() == 'Lines'
    assert 'Seen' in p.locator('#hub-detail .h-line-shop thead').text_content()
    # 3) Ask is the Genie inside the card
    p.locator('#hub-detail [role=tab][data-id=ask]').click()
    assert p.locator('#hub-ask-q').is_visible() and 'Scout evidence guide' in p.locator('#hub-detail').inner_text()
    p.keyboard.press('Escape')
    assert p.locator('#hub-card').is_hidden()
    # 4) the Side button of a row without an offered Scout side, and 5) an alert notice, open the same card
    p.evaluate("""(k) => { const b = document.querySelector(`#hub-rows .h-add[data-id="${CSS.escape(k)}"]`); b.dataset.hub = 'research'; b.click(); }""", k)
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
    assert p.locator('#hub-rows tr.is-expanded').get_attribute('data-record') == k
    assert not p.locator('#u-drawer').is_visible()
    no_h_scroll(p)
    small = p.evaluate(SMALL, '#hub-detail')
    assert small == [], small
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_adding_keeps_the_board_width_and_says_on_slip(ctx, env, size):
    p = board(ctx, env, size)
    before = p.evaluate("[document.querySelector('#hub-table').getBoundingClientRect().width, (document.querySelector('.h-slip')||{}).offsetWidth||0]")
    if size == DESK:
        assert before[1] >= 240 and 'No picks yet' in p.locator('#hub-slip-content').inner_text()     # slim, but there when empty
    first = p.locator('#hub-rows .h-add:not([disabled]):not(.selected)').first
    k = first.get_attribute('data-id')
    first.click()
    btn = p.locator(f'#hub-rows .h-add[data-id="{k}"]')
    assert btn.inner_text() == 'On slip ✓'
    after = p.evaluate("[document.querySelector('#hub-table').getBoundingClientRect().width, (document.querySelector('.h-slip')||{}).offsetWidth||0]")
    assert after == before                                         # the first add never squeezes the table
    assert p.locator('#hub-slip-count').inner_text() == '1' and p.locator('#hub-tab-slip-n').inner_text() == '1'
    btn.click()                                                    # "On slip ✓" shows the pick; no old slip pop-up
    assert not p.locator('#u-tray').is_visible()
    if size == PHONE:
        p.wait_for_selector('#hub-studio-section:not([hidden]) #hub-studio-body .u-leg')
        assert p.locator('.h-tabbar [data-id=studio]').get_attribute('aria-current') == 'page'
    else:
        assert p.locator('#hub-slip-content .u-leg').count() == 1
    body = p.locator('#u-hub').inner_text()
    assert 'Slip builder' not in body and 'Slip Studio' not in body and 'Your lineup' not in body and 'Slip tray' not in body
    no_h_scroll(p)
    assert not p.errors


def test_phone_bottom_tab_bar_and_two_line_cards(ctx, env):
    p = board(ctx, env, PHONE)
    bar = p.locator('.h-tabbar')
    assert bar.is_visible() and [t.split('\n')[0].strip() for t in bar.locator('button').all_inner_texts()] == ['Board', 'Slip', 'My picks', 'Record', 'More']
    assert bar.bounding_box()['y'] + bar.bounding_box()['height'] >= 843
    assert p.locator('.h-sidebar').is_hidden() and p.locator('.h-slip').is_hidden()
    heights = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].slice(0,6).map(r=>Math.round(r.getBoundingClientRect().height))")
    assert all(120 <= h <= 160 for h in heights), heights          # was 393px
    assert p.locator('#hub-rows tr[data-record]').first.bounding_box()['y'] < 300
    small = p.evaluate(SMALL, '#u-hub')
    assert small == [], small
    p.locator('.h-tabbar [data-hub=more]').click()
    assert p.locator('#hub-more [data-hub=section][data-id=tools]').is_visible()
    p.locator('#hub-more [data-hub=section][data-id=tools]').click()
    assert p.locator('#hub-tools-section').is_visible() and p.locator('#hub-more').is_hidden()
    p.locator('.h-tabbar [data-id=record]').click()
    p.wait_for_selector('.h-record-card', timeout=30000)
    no_h_scroll(p)
    p.locator('.h-tabbar [data-id=studio]').click()
    assert p.locator('[data-hub=studio-tab][aria-selected=true]').inner_text().startswith('My slip')
    no_h_scroll(p)
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_my_picks_pending_then_settled_by_slip_without_units(ctx, env, size):
    p = board(ctx, env, size)
    p.goto(env.url + '/#/picks')
    p.wait_for_selector('#hub-picks-body .h-empty')
    assert 'No tracked picks yet' in p.locator('#hub-picks-body').inner_text()
    assert p.locator('#hub-picks-section .h-pick-filters').is_hidden() and p.locator('.h-pick-summary').count() == 0   # only the message
    p.goto(env.url + '/#/board')
    p.wait_for_selector('#hub-rows tr[data-record]')
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    for name in ('vargo', 'orlo'):
        p.locator('#hub-q').fill(name)
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
    p.goto(env.url + '/#/slips')
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])', timeout=20000)
    p.locator('#hub-studio-body [data-u=track]').click()
    p.wait_for_selector('#hub-studio-body .u-note.good')
    p.goto(env.url + '/#/picks')
    p.wait_for_selector('#hub-picks-body .h-picks-group .u-pick')
    text = p.locator('#hub-picks-body').inner_text()
    assert 'Pending' in text and '2 pending' in text and p.locator('#hub-picks-body .h-picks-group').count() == 1
    assert 'Underdog' in p.locator('#hub-picks-body .h-picks-group h4').inner_text()
    assert not re.search(r'\bunits?\b|return|profit', text.lower())
    p.locator('#hub-pick-status').select_option('hit')
    assert 'No picks in this view' in p.locator('#hub-picks-body').inner_text()
    p.locator('#hub-picks-body [data-hub=pick-clear]').click()
    assert p.locator('#hub-picks-body .u-pick').count() == 2
    nav = '.h-tabbar' if size == PHONE else '.h-sidebar'
    assert p.locator(f'{nav} [data-id=picks]').get_attribute('aria-current') == 'page'
    no_h_scroll(p)
    assert not p.errors


def test_slips_tabs_default_to_my_slip_and_progress_is_neutral(ctx, env):
    p = board(ctx, env)
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    p.locator('#hub-q').fill('vargo')
    p.locator('#hub-rows .h-add:not([disabled])').first.click()
    saved_state(p, lambda d: len((d.get('slip') or {}).get('legs', [])) == 1)
    p.locator('.h-sidebar [data-id=studio]').click()
    assert p.locator('[data-hub=studio-tab]').all_inner_texts()[0].startswith('My slip') and p.locator('#hub-studio-tab-n').inner_text() == '1'
    assert p.locator('[data-studio-panel=slip]').is_visible() and p.locator('#hub-generate').is_hidden()
    p.locator('#hub-studio-body [data-u=check]').click()
    p.wait_for_selector('#hub-studio-body .u-progress', timeout=20000)
    # one pick is a slip in progress, not a failure: neutral next step, no refused entry, no red problems
    assert 'Add 1 more pick' in p.locator('#hub-studio-body .u-progress').inner_text()
    assert p.locator('#hub-studio-body .u-entry.refused, #hub-studio-body .u-problems').count() == 0
    assert 'has not passed its checks' not in p.locator('#hub-studio-body').inner_text()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    assert p.locator('#hub-generate').is_visible()
    p.locator('[role=tab][data-hub=studio-tab][data-id=tracked]').click()
    assert p.locator('#hub-slip-history').is_visible()
    assert not p.errors


def test_empty_board_names_hidden_lines_and_tools_is_the_single_index(ctx, env):
    p = board(ctx, env)
    p.locator('#hub-q').fill('zzzz-nobody')
    empty = p.locator('#hub-empty')
    assert re.search(r'\d+ lines hidden by filters', empty.inner_text()) and empty.locator('[data-hub=clear-filters]').is_visible()
    assert 'No lines match' not in empty.inner_text()
    p.locator('.h-sidebar [data-id=tools]').click()
    tools = p.locator('#hub-tools-body').inner_text()
    for want in ('Moneylines', 'Single prop grader', 'Match research', 'Sheets and Compare', 'Classic boards', 'Your saved filters'):
        assert want in tools, want
    assert 'Ask the Genie' not in tools and p.locator('.h-sidebar').inner_text().count('Moneylines') == 0
    assert p.locator('#hub-tools-body a[href="/sports?view=classic#/sheets?lg=nba"]').count() == 1
    no_h_scroll(p)
    assert not p.errors


def test_record_collapses_empty_days_and_keeps_its_name(ctx, env):
    from gradecore.calendar import daily_record
    p = board(ctx, env)
    doc = {'calendar': daily_record([{'at': '2026-11-02T12:00:00Z', 'status': 'win'}], '2026-11-03T12:00:00Z', 'Fixture date · UTC')}
    p.route('**/api/upgrade/stated', lambda r: r.fulfill(json=doc))
    p.route('**/api/upgrade/results?*', lambda r: r.fulfill(json=doc))
    p.locator('.h-sidebar [data-id=record]').click()
    p.wait_for_selector('.h-record-calendar')
    assert p.locator('#hub-title').inner_text() == 'Record'
    grid = p.locator('.h-calendar-grid').first
    assert grid.locator('summary').count() == 1 and grid.locator('.h-cal-gap').count() == 2
    assert 'Results' not in p.locator('.h-sidebar nav').inner_text()
    assert not p.errors


def test_only_the_visible_sport_is_read_and_reads_revalidate(ctx, env):
    """Audit 2026-11-04 (polling): the CS2 board reads CS2 only; another sport is read when its tab opens; a refresh revalidates."""
    p = ctx.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    reads = []
    p.on('request', lambda r: reads.append(r) if '/api/upgrade/board' in r.url else None)
    p.goto(env.url + '/#/board?sport=cs2')
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_timeout(800)
    urls = [r.url.split('/api/upgrade/')[1] for r in reads]
    assert urls and all(u == 'board' for u in urls), urls                       # no LoL, NBA or NFL read for a CS2 board
    assert p.locator('#hub-sports [data-id=lol] span').count() == 0              # an unread board shows no guessed count
    p.locator('#hub-sports [data-id=lol]').click()
    p.wait_for_selector('#hub-rows tr[data-record^="lol|"]', timeout=20000)
    urls = [r.url.split('/api/upgrade/')[1] for r in reads]
    assert urls.count('board?sport=lol') == 1 and not any('nba' in u or 'nfl' in u for u in urls), urls
    n = len(reads)
    p.locator('.h-toolbar [data-hub=refresh]').click()
    p.wait_for_function('(n) => true', arg=n)
    p.wait_for_timeout(1500)
    again = reads[n:]
    assert [r.url.split('/api/upgrade/')[1] for r in again] == ['board?sport=lol']
    assert again[0].all_headers().get('if-none-match', '').startswith('"')                   # the ETag is sent back
    assert again[0].response().status == 304                                                  # unchanged: no body
    assert p.locator('#hub-rows tr[data-record^="lol|"]').count() > 0                         # and the rows stay
    assert not p.errors


def test_a_source_behind_gets_one_plain_banner_with_refresh(ctx, env):
    p = board(ctx, env)
    assert p.locator('#hub-sources').inner_text() == ''

    def behind(route):
        h = dict(route.request.headers); h.pop('if-none-match', None)
        response = route.fetch(headers=h)
        d = response.json()
        for name, f in d['feeds'].items():
            if name == 'prizepicks':
                f['state'] = 'stale'
                f['at'] = '2026-01-01T00:00:00Z'
        route.fulfill(response=response, json=d)
    p.route('**/api/upgrade/board*', behind)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('.h-toolbar [data-hub=refresh]').click()
    p.wait_for_selector('#hub-sources p', timeout=20000)
    text = p.locator('#hub-sources').inner_text()
    assert 'CS2 · PrizePicks last updated' in text and 'Underdog' not in text, text
    assert p.locator('#hub-sources [data-hub=refresh]').is_visible()
    assert not re.search(r'unsure|provisional|may be|not (betting|financial) advice', text.lower())
    assert all('|prizepicks|' not in k for k in p.locator('#hub-rows tr[data-record]').evaluate_all('rs=>rs.map(r=>r.dataset.record)'))
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_every_page_keeps_40px_targets_and_the_chart_dates(ctx, env, size):
    p = board(ctx, env, size)
    bad = p.evaluate(SMALL, '#u-hub')
    assert bad == [], bad
    p.locator('#hub-filters-toggle').click()
    assert p.evaluate(SMALL, '#hub-filter-pop') == []
    sort = p.locator('#hub-sort')
    assert sort.evaluate('e => e.scrollWidth <= e.clientWidth + 1') and sort.bounding_box()['width'] >= 140   # "Starting soon" is not clipped
    p.keyboard.press('Escape')
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg', timeout=20000)
    assert p.locator('#hub-detail .h-history-chart text.date').count() > 0 or         all('·' in t for t in p.locator('#hub-detail .h-history-chart text.opp').all_text_contents())   # each game's date under its name
    for tab in ('lines', 'stats', 'ask', 'overview'):
        p.locator(f'#hub-detail [role=tab][data-id={tab}]').click()
        bad = p.evaluate(SMALL, '#hub-card')
        assert bad == [], (tab, bad)
    p.keyboard.press('Escape')
    for route in ('/#/slips', '/#/picks', '/#/results', '/#/tools'):
        p.goto(env.url + route)
        p.wait_for_timeout(1500)
        bad = p.evaluate(SMALL, '#u-hub')
        assert bad == [], (route, bad)
        no_h_scroll(p)
    p.goto(env.url + '/#/slips?tab=build')
    p.wait_for_selector('#hub-generate')
    assert p.evaluate(SMALL, '#u-hub') == []
    assert not p.errors


def test_cs2_and_sports_routes_draw_the_same_toolbar(ctx, env):
    shape = """() => ({kids: [...document.querySelectorAll('.h-toolbar > *')].map(e => e.className || e.tagName),
      sports: [...document.querySelectorAll('#hub-sports button')].map(b => b.firstChild.textContent),
      platform: [...document.querySelectorAll('#hub-platform button')].map(b => b.textContent),
      filters: [...document.querySelectorAll('#hub-filter-pop select, #hub-filter-pop button')].map(e => e.id || e.dataset.hub),
      views: [...document.querySelectorAll('#hub-views-pop select, #hub-views-pop button')].map(e => e.id || e.dataset.hub),
      top: Math.round(document.querySelector('.h-toolbar').getBoundingClientRect().top),
      h: Math.round(document.querySelector('.h-toolbar').getBoundingClientRect().height)})"""
    seen = []
    for path in ('/', '/sports#/board?lg=nba'):
        p = open_page(ctx, env, path, DESK)
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        seen.append(p.evaluate(shape))
        assert not p.errors
        p.close()
    assert seen[0] == seen[1], seen


def test_owner_tools_stay_hidden_from_members_and_no_letter_rows_render_cleanly(browser, env):
    c = context(browser, env, DESK, user='member-1')
    try:
        def no_letters(route):
            h = dict(route.request.headers); h.pop('if-none-match', None)
            response = route.fetch(headers=h)
            d = response.json()
            for r in d['records']:
                r['scout']['grade'] = None                      # letters contained: the chance and Lean Over/Under remain
            route.fulfill(response=response, json=d)
        c.route('**/api/upgrade/board*', no_letters)
        p = open_page(c, env, '/#/tools', DESK)
        p.wait_for_selector('#hub-tools-body article')
        assert p.locator('[data-hub=grade-review]').count() == 0 and 'Grading comparison' not in p.locator('#u-hub').inner_text()
        p.goto(env.url + '/#/board?sport=cs2')
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        cells = p.locator('#hub-rows tr[data-record] .h-c-scout')
        assert p.locator('#hub-rows .h-grade').count() == 0
        for t in cells.all_inner_texts():
            assert re.search(r'\d+\.\d%', t) and ('Lean Over' in t or 'Lean Under' in t or 'Over' in t), t
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-detail .h-decision')
        assert 'Lean' in p.locator('#hub-detail .h-dec-chance').inner_text() or 'Scout side' in p.locator('#hub-detail .h-dec-chance').inner_text()
        assert not p.errors
    finally:
        c.close()
