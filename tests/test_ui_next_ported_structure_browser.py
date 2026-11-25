"""The 2026-11-05 structure review (tests/test_hub_structure_browser.py) on the redesign, the default since 2026-11-11: the same member
functions and the same numbers and words, read on html[data-ui=next]. Real browser, real Handler, fixture data only. The legacy file
keeps covering the rollback design; what only exists there (40px desktop targets, 56-64px rows, the Compare cell) is not repeated here
(TRIAGE_UI_NEXT_1112.md). Display only: no grade, chance or stake moves."""
import re

import pytest

import server
from tests.test_hub_structure_browser import FLAGS, board
from tests.test_research_hub_browser import book, saved_state
from tests.test_ui_next_browser import nxt
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

# a control a finger or pointer can hit: phone controls are 44px on the redesign, desktop controls 30-32px (was 40px on both)
SMALL = """([root, min]) => [...document.querySelectorAll(root+' button,'+root+' select,'+root+' input,'+root+' summary,'+root+' a')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && !e.closest('[hidden]') && getComputedStyle(e).visibility !== 'hidden'; })
  .filter(e => e.getBoundingClientRect().height < min - 0.5 && !(e.type === 'checkbox' && e.closest('label') && e.closest('label').getBoundingClientRect().height >= min - 0.5))
  .map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().height) + 'px ' + (e.textContent || '').trim().slice(0, 24))"""
PHONE_MIN, DESK_MIN = 40, 24


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


@pytest.fixture
def ctx(browser, env):
    c = nxt(browser, env)
    yield c
    c.close()


def is_next(p):
    assert p.locator('html').get_attribute('data-ui') == 'next'


def low(texts):
    return [t.strip().lower() for t in texts]


def test_desktop_first_row_high_on_the_screen_with_ten_rows_and_one_toolbar(ctx, env):
    p = board(ctx, env)
    is_next(p)
    m = p.evaluate("""() => { const vh = innerHeight, rows = [...document.querySelectorAll('#hub-rows tr[data-record]')];
      const tb = document.querySelector('.h-toolbar').getBoundingClientRect();
      const ctl = [...document.querySelectorAll('.h-toolbar > *')].filter(e => e.getBoundingClientRect().height > 0).map(e => { const r = e.getBoundingClientRect(); return Math.round(r.top + r.height / 2); });
      return {first: document.querySelector('#hub-rows tr').getBoundingClientRect().top, toolbarH: tb.height, mids: ctl,
              visible: rows.filter(r => { const b = r.getBoundingClientRect(); return b.top >= 0 && b.bottom <= vh; }).length,
              heights: rows.slice(0, 12).map(r => Math.round(r.getBoundingClientRect().height)),
              player: Math.round(document.querySelector('#hub-rows tr[data-record] td.h-c-player').getBoundingClientRect().width)}; }""")
    assert m['player'] >= 120, m                                 # the name keeps its width with the Edge column on
    assert m['first'] <= 200, m
    assert m['visible'] >= 10, m
    assert m['toolbarH'] <= 52 and max(m['mids']) - min(m['mids']) <= 3, m   # one toolbar row
    assert all(48 <= h <= 68 for h in m['heights']), m['heights']   # compact rows: 52px, 65px with a PrizePicks line kind under the line
    assert p.locator('#hub-games, .h-games, .h-tools-menu, .h-classic, .h-nav-mobile, .h-mobile-slip').count() == 0
    assert p.locator('#u-hub [data-hub=close]').locator('visible=true').count() == 1
    nav = [t.strip() for t in p.locator('.h-sidebar nav [data-hub=section]').all_text_contents()]
    assert [n for n in nav if n not in ('EV', 'Arbitrage', 'Sportsbooks')] == ['Board', 'Slips', 'My picks', 'Record', 'Tools'], nav     # Sportsbooks: main 3ef2fad
    in_sheet(p, '#hub-sort').select_option('start')
    head = p.locator('#hub-rows tr.h-game-row [data-hub=game]').first
    event = head.locator('.h-match').text_content()
    assert head.locator('.h-countdown').text_content().startswith('in ')
    head.click()
    assert p.locator('#hub-rows tr.h-game-row').count() == 1 and p.locator('#hub-rows .h-match').text_content() == event
    assert p.locator('#hub-filter-n').inner_text() == '1'
    p.locator('#hub-rows [data-hub=game]').click()
    assert p.locator('#hub-rows tr.h-game-row').count() > 1
    assert p.locator('#hub-rows .h-match').count() == p.locator('#hub-rows tr.h-game-row').count()
    assert p.evaluate("document.querySelectorAll('#hub-rows tr[data-record] .h-c-player small').length") > 0
    assert not p.errors


def test_one_scout_cell_and_one_hit_strip_with_counts_in_tooltips(ctx, env):
    p = board(ctx, env)
    is_next(p)
    heads = p.locator('#hub-table thead th').all_text_contents()
    assert [h.strip() for h in heads][:6] == ['Watch', 'Player', 'Market', 'Line', 'Proj.', 'Grader']
    row = p.locator('#hub-rows tr[data-record]').first
    cells = row.locator('.h-strip .h-hit')
    assert cells.count() == 5                                     # L5 L10 L20 H2H SZN
    for i in range(cells.count()):
        c = cells.nth(i)
        text, title = c.text_content().strip(), c.get_attribute('title') or ''
        assert text == '—' or re.fullmatch(r'\d+%|\d+-\d+', text), text
        assert text == '—' or re.search(r'\d+ (Over|Under)', title), title     # the counts live in the tooltip
    scout = row.locator('.h-c-scout').inner_text()
    assert re.search(r'(Over|Under)\s+\d+\.\d%', scout, re.I) and (row.locator('.h-grade').count() == 1 or 'lean' in scout.lower() or 'research' in scout.lower()), scout
    cs2_heads = p.locator('#hub-table thead th').all_text_contents()
    p.locator('#hub-sports [data-id=nba]').click()
    assert p.locator('#hub-table thead .h-c-edge').count() == 1 and p.locator('#hub-table thead th').all_text_contents() == cs2_heads
    assert p.locator('#hub-health details').count() == 1          # the Sports grades note: NBA and NFL tabs only
    p.locator('#hub-sports [data-id=cs2]').click()
    assert p.locator('#hub-health details').count() == 0
    p.locator('#hub-sports [data-id=all]').click()
    assert p.locator('#hub-health details').count() == 0
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_every_entry_point_opens_the_one_card_with_a_pinned_add(ctx, env, size):
    p = board(ctx, env, size)
    is_next(p)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-q').fill('vargo')
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => /vargo/i.test(r.textContent))")
    k = p.locator('#hub-rows tr[data-record]').first.get_attribute('data-record')
    # 1) the row
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail [data-hub=detail-add]:not([disabled])', timeout=20000)
    assert p.locator('#hub-card').count() == 1 and p.locator('#hub-detail').count() == 1
    assert low(p.locator('#hub-detail [role=tab]').all_text_contents()) == ['overview', 'lines', 'stats', 'ask']
    add = p.locator('#hub-detail [data-hub=detail-add]')
    y0 = add.bounding_box()['y']
    assert 0 <= y0 and y0 + add.bounding_box()['height'] <= size['height']                      # visible without scrolling
    p.locator('#hub-card-body').evaluate('el => el.scrollTop = el.scrollHeight')
    assert abs(add.bounding_box()['y'] - y0) < 1                                                # pinned while the tab scrolls
    box = p.locator('#hub-card').bounding_box()
    if size == PHONE:
        assert box['x'] == 0 and box['width'] == 390 and p.locator('#hub-detail [data-hub=card-close]').text_content().strip().endswith('Back')
    else:
        # the card sits beside the board, in the slip's column: the board's rows stay on screen to its left
        t = p.locator('#hub-table').bounding_box()
        assert box['x'] >= t['x'] + 300 and box['x'] + box['width'] <= 1441 and p.locator('#hub-rows tr[data-record]').first.is_visible()
    assert not p.locator('#u-drawer').is_visible() and not p.locator('#u-tray').is_visible()
    p.locator('#hub-detail [data-hub=card-close]').click()
    assert p.locator('#hub-card').is_hidden()
    if size == DESK:
        assert p.locator('.h-slip').is_visible()                                                # the slip column is back
    # 2) the row's Lines cell: the ladder lists every line of the prop, a line there opens the same card, and its Lines tab compares them
    lines = p.locator('#hub-rows [data-hub=ladder]').first
    lines.click()
    assert p.locator('#hub-rows [data-hub=ladder]').first.get_attribute('aria-expanded') == 'true'
    assert p.locator('#hub-rows tr.h-ladder-row tr[data-line]').count() >= 2
    p.locator('#hub-rows tr.h-ladder-row [data-hub=expand]').first.click()
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail [role=tab]')
    p.locator('#hub-detail [role=tab][data-id=lines]').click()
    p.wait_for_selector('#hub-detail .h-line-shop table')
    assert p.locator('#hub-detail [role=tab][aria-selected=true]').text_content().strip() == 'Lines'
    assert 'Seen' in p.locator('#hub-detail .h-line-shop thead').text_content()
    # 3) Ask is the Genie inside the card
    p.locator('#hub-detail [role=tab][data-id=ask]').click()
    assert p.locator('#hub-ask-q').is_visible() and 'scout evidence guide' in p.locator('#hub-detail').inner_text().lower()
    p.keyboard.press('Escape')
    assert p.locator('#hub-card').is_hidden()
    # 4) the Side button of a row without an offered Grader side opens the same card
    p.evaluate("""(k) => { const b = document.querySelector(`#hub-rows .h-add[data-id="${CSS.escape(k)}"]`); b.dataset.hub = 'research'; b.click(); }""", k)
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
    assert p.locator('#hub-rows tr.is-expanded').get_attribute('data-record') == k
    assert not p.locator('#u-drawer').is_visible()
    no_h_scroll(p)
    small = p.evaluate(SMALL, ['#hub-detail', PHONE_MIN if size == PHONE else DESK_MIN])
    assert small == [], small
    assert not p.errors


def test_phone_bottom_tab_bar_and_two_line_cards(ctx, env):
    p = board(ctx, env, PHONE)
    is_next(p)
    bar = p.locator('.h-tabbar')
    assert bar.is_visible() and [t.split('\n')[0].strip().lower() for t in bar.locator('button').all_inner_texts()] == ['board', 'slip', 'my picks', 'record', 'more']
    assert bar.bounding_box()['y'] + bar.bounding_box()['height'] >= 843
    assert p.locator('.h-sidebar').is_hidden() and p.locator('.h-slip').is_hidden()
    heights = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].slice(0,6).map(r=>Math.round(r.getBoundingClientRect().height))")
    assert all(56 <= h <= 110 for h in heights), heights          # phone simplification (2026-11-12): one compact row per prop
    assert p.locator('#hub-rows tr[data-record]').first.bounding_box()['y'] < 320
    small = p.evaluate(SMALL, ['#u-hub', PHONE_MIN])
    assert small == [], small
    p.locator('.h-tabbar [data-hub=more]').click()
    assert p.locator('#hub-more [data-hub=section][data-id=tools]').is_visible()
    p.locator('#hub-more [data-hub=section][data-id=tools]').click()
    assert p.locator('#hub-tools-section').is_visible() and p.locator('#hub-more').is_hidden()
    p.locator('.h-tabbar [data-hub=more]').click()                                    # phones: Record lives in More (2026-11-12)
    p.locator('#hub-more [data-hub=section][data-id=record]').click()
    p.wait_for_selector('.h-record-card', timeout=30000)
    no_h_scroll(p)
    p.locator('.h-tabbar [data-id=studio]').click()
    assert p.locator('[data-hub=studio-tab][aria-selected=true]').text_content().strip().startswith('My slip')
    no_h_scroll(p)
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_my_picks_pending_then_settled_by_slip_without_units(ctx, env, size):
    p = board(ctx, env, size)
    is_next(p)
    p.goto(env.url + '/#/picks')
    p.wait_for_selector('#hub-picks-body .h-empty')
    assert 'No tracked picks yet' in p.locator('#hub-picks-body').text_content()
    assert p.locator('#hub-picks-section .h-pick-filters').is_hidden() and p.locator('.h-pick-summary').count() == 0   # only the message
    p.goto(env.url + '/#/board')
    p.wait_for_selector('#hub-rows tr[data-record]')
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    for name in ('vargo', 'orlo'):
        p.locator('#hub-q').fill(name)
        p.wait_for_function("(n) => [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.textContent.toLowerCase().includes(n))", arg=name)
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
    p.goto(env.url + '/#/slips')
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])', timeout=20000)
    p.locator('#hub-studio-body [data-u=track]').click()
    p.wait_for_selector('#hub-studio-body .u-note.good')
    p.goto(env.url + '/#/picks')
    p.wait_for_selector('#hub-picks-body .h-picks-group .u-pick')
    text = p.locator('#hub-picks-body').inner_text().lower()
    assert 'pending' in text and '2 pending' in text and p.locator('#hub-picks-body .h-picks-group').count() == 1
    assert 'Underdog' in p.locator('#hub-picks-body .h-picks-group h4').text_content()
    assert not re.search(r'\bunits?\b|return|profit', text)
    p.locator('#hub-pick-status').select_option('hit')
    assert 'No picks in this view' in p.locator('#hub-picks-body').text_content()
    p.locator('#hub-picks-body [data-hub=pick-clear]').click()
    assert p.locator('#hub-picks-body .u-pick').count() == 2
    nav = '.h-tabbar' if size == PHONE else '.h-sidebar'
    assert p.locator(f'{nav} [data-id=picks]').get_attribute('aria-current') == 'page'
    no_h_scroll(p)
    assert not p.errors


def test_slips_tabs_default_to_my_slip_and_progress_is_neutral(ctx, env):
    p = board(ctx, env)
    is_next(p)
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    p.locator('#hub-q').fill('vargo')
    p.locator('#hub-rows .h-add:not([disabled])').first.click()
    saved_state(p, lambda d: len((d.get('slip') or {}).get('legs', [])) == 1)
    p.locator('.h-sidebar [data-id=studio]').click()
    assert p.locator('[data-hub=studio-tab]').all_text_contents()[0].strip().startswith('My slip') and p.locator('#hub-studio-tab-n').inner_text() == '1'
    assert p.locator('[data-studio-panel=slip]').is_visible() and p.locator('#hub-generate').is_hidden()
    p.locator('#hub-studio-body [data-u=check]').click()
    p.wait_for_selector('#hub-studio-body .u-progress', timeout=20000)
    # one pick is a slip in progress, not a failure: neutral next step, no refused entry, no red problems
    assert 'Add 1 more pick' in p.locator('#hub-studio-body .u-progress').text_content()
    assert p.locator('#hub-studio-body .u-entry.refused, #hub-studio-body .u-problems').count() == 0
    assert 'has not passed its checks' not in p.locator('#hub-studio-body').text_content()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    assert p.locator('#hub-generate').is_visible()
    p.locator('[role=tab][data-hub=studio-tab][data-id=tracked]').click()
    assert p.locator('#hub-slip-history').is_visible()
    assert not p.errors


def test_empty_board_names_hidden_lines_and_tools_is_the_single_index(ctx, env):
    p = board(ctx, env)
    is_next(p)
    p.locator('#hub-q').fill('zzzz-nobody')
    p.wait_for_selector('#hub-empty:not([hidden])')
    empty = p.locator('#hub-empty')
    assert re.search(r'\d+ lines hidden by your search “zzzz-nobody”', empty.text_content()) and empty.locator('[data-hub=clear-filters]').is_visible()   # H17: names the search
    assert 'No lines match' not in empty.text_content()
    p.locator('.h-sidebar [data-id=tools]').click()
    tools = p.locator('#hub-tools-body').inner_text().lower()
    for want in ('Moneylines', 'Single prop grader', 'Match research', 'Sheets and Compare', 'Classic boards', 'Your saved filters'):
        assert want.lower() in tools, want
    assert 'ask the genie' not in tools and p.locator('.h-sidebar').text_content().count('Moneylines') == 0
    assert p.locator('#hub-tools-body a[href="/sports?view=classic#/sheets?lg=nba"]').count() == 1
    no_h_scroll(p)
    assert not p.errors


def test_record_collapses_empty_days_and_keeps_its_name(ctx, env):
    from gradecore.calendar import daily_record
    p = board(ctx, env)
    is_next(p)
    doc = {'calendar': daily_record([{'at': '2026-11-02T12:00:00Z', 'status': 'win'}], '2026-11-03T12:00:00Z', 'Fixture date · UTC')}
    p.route('**/api/upgrade/stated', lambda r: r.fulfill(json=doc))
    p.route('**/api/upgrade/results?*', lambda r: r.fulfill(json=doc))
    p.locator('.h-sidebar [data-id=record]').click()
    p.wait_for_selector('.h-record-calendar')
    assert p.locator('#hub-title').text_content().strip() == 'Record' and p.locator('#hub-title').is_visible()
    grid = p.locator('.h-calendar-grid').first
    assert grid.locator('summary').count() == 1 and grid.locator('.h-cal-gap').count() == 2
    assert 'Results' not in p.locator('.h-sidebar nav').text_content()
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_every_page_keeps_its_targets_and_the_chart_dates(ctx, env, size):
    """Phone: every control is at least 40px high (the redesign's are 44px). Desktop: the redesign's controls are 30-32px by design,
    so the floor there is 24px, the height of a text link (nothing shrinks to a sliver)."""
    lim = PHONE_MIN if size == PHONE else DESK_MIN
    p = board(ctx, env, size)
    is_next(p)
    in_sheet(p, '#hub-sort').select_option('start')
    bad = p.evaluate(SMALL, ['#u-hub', lim])
    assert bad == [], bad
    p.locator('#hub-filters-toggle').click()
    bad = p.evaluate(SMALL, ['#hub-filter-pop', lim])
    assert bad == [], bad
    p.keyboard.press('Escape')
    assert p.locator('#hub-sort').evaluate('e => e.scrollWidth <= e.clientWidth + 1')            # the chosen sort's name is not clipped
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg', timeout=20000)
    assert p.locator('#hub-detail .h-history-chart text.date').count() > 0 or all('·' in t for t in p.locator('#hub-detail .h-history-chart text.opp').all_text_contents())
    for tab in ('lines', 'stats', 'ask', 'overview'):
        p.locator(f'#hub-detail [role=tab][data-id={tab}]').click()
        bad = p.evaluate(SMALL, ['#hub-card', lim])
        assert bad == [], (tab, bad)
    p.keyboard.press('Escape')
    for route in ('/#/slips', '/#/picks', '/#/results', '/#/tools'):
        p.goto(env.url + route)
        p.wait_for_timeout(1500)
        bad = p.evaluate(SMALL, ['#u-hub', lim])
        assert bad == [], (route, bad)
        no_h_scroll(p)
    p.goto(env.url + '/#/slips?tab=build')
    p.wait_for_selector('#hub-generate')
    bad = p.evaluate(SMALL, ['#u-hub', lim])
    assert bad == [], bad
    assert not p.errors


def test_owner_tools_stay_hidden_from_members_and_no_letter_rows_render_cleanly(browser, env):
    c = context(browser, env, DESK, user='member-1')
    c.add_cookies([{"name": server.UI_NEXT_COOKIE, "value": "next", "url": env.url}])
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
        is_next(p)
        assert p.locator('[data-hub=grade-review]').count() == 0 and 'Grading comparison' not in p.locator('#u-hub').text_content()
        p.goto(env.url + '/#/board?sport=cs2')
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        cells = p.locator('#hub-rows tr[data-record] .h-c-scout')
        assert p.locator('#hub-rows .h-grade').count() == 0 and cells.count() > 0
        for i in range(cells.count()):
            t = ' '.join(cells.nth(i).text_content().split())
            assert cells.nth(i).is_visible() and re.search(r'\d+\.\d%', t) and ('Lean Over' in t or 'Lean Under' in t or 'Over' in t), t
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-detail .h-decision')
        chance = p.locator('#hub-detail .h-dec-chance').text_content()
        assert 'Lean' in chance or 'Grader side' in chance, chance
        assert not p.errors
    finally:
        c.close()
