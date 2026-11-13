"""Legacy hub tests that failed on the redesign (html[data-ui=next], the default since 2026-11-11), ported: the member function or
the number/text each one checks, on the new design. Sources: tests/test_research_hub_browser.py, tests/test_role_hub_rows_browser.py
and tests/test_workspace_v2_browser.py (those keep covering the rollback design, UI_NEXT=0). What changed for these tests: one board
row per player + stat with a "+N lines" ladder (no "+N props" toggle, no Compare cell), headings and labels upper-cased by CSS (text
is compared case-insensitively or with text_content()), phone toolbar heights. Real Handler + fixtures, no network. Display only."""
import re

import pytest

import tests.test_card_and_chips_browser as cc
from tests.test_research_hub_browser import add_first, book, hub, saved_state
from tests.test_role_hub_rows_browser import MEASURE, SPAN
from tests.test_role_tags_browser import CASES, NONE, PLACEHOLDER, close_card, hub_card, hub_row, record_role, roles_env  # noqa: F401
from tests.test_ui_next_browser import nxt
from tests.test_upgrade_browser import browser, env, open_page, no_h_scroll, DESK, PHONE  # noqa: F401


@pytest.fixture
def ctx(browser, env):
    """a member on the redesign: the legacy helpers (hub, book, add_first, ...) then run on the new design"""
    c = nxt(browser, env)
    yield c
    c.close()


@pytest.fixture
def next_board(monkeypatch):
    """tests.test_card_and_chips_browser.open_board builds its own context: give it the redesign's"""
    monkeypatch.setattr(cc, "context", nxt)
    return cc.open_board


def is_next(p):
    assert p.locator("html").get_attribute("data-ui") == "next"


def rows(p):
    return p.locator("#hub-rows tr.h-row[data-record]")


def up(s):
    return (s or "").upper()


# ------------------------------------------------------------------------------------ tests/test_research_hub_browser.py


def test_all_four_boards_search_ladder_the_one_card_and_the_way_back_to_classic(ctx, env):
    """legacy: test_all_four_real_boards_filters_research_and_original_navigation ("+N props" is now the row's ladder of lines)"""
    p = hub(ctx, env)
    is_next(p)
    p.wait_for_function("document.querySelectorAll('#hub-rows tr[data-record^=nba]').length > 0")
    assert all(p.locator('#hub-rows tr[data-record^=' + s + ']').count() for s in ('cs2', 'lol', 'nba', 'nfl'))
    assert 'SAMPLE' not in p.locator('#u-hub').text_content()
    p.locator('#hub-q').fill('vargo')
    p.wait_for_function("document.querySelectorAll('#hub-rows tr.h-row[data-record]').length === 1")
    assert up(p.locator('#hub-count').inner_text()) == '1 PLAYER · 1 PROP · 4 LINES'
    btn = p.locator('#hub-rows [data-hub=ladder]')
    assert btn.get_attribute('aria-expanded') == 'false' and p.locator('#hub-rows tr[data-line]').count() == 0
    btn.click()
    assert p.locator('#hub-rows [data-hub=ladder]').get_attribute('aria-expanded') == 'true'
    assert p.locator('#hub-rows tr.h-ladder-row tr[data-line]').count() == 4          # every vargo line, one row each
    assert rows(p).count() == 1 and 'orlo' not in p.locator('#hub-rows').inner_text()
    p.locator('#hub-rows tr.h-row .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg', timeout=15000)
    # ONE card: books and the Genie are tabs of the same card; the old drawer is not used inside the hub
    assert [t.strip() for t in p.locator('#hub-detail [role=tab]').all_text_contents()] == ['Overview', 'Lines', 'Stats', 'Ask']
    p.locator('#hub-detail [role=tab][data-id=lines]').click()
    p.wait_for_selector('#hub-detail .h-line-shop table')
    assert 'Seen' in p.locator('#hub-detail .h-line-shop thead').text_content()
    p.locator('#hub-detail [role=tab][data-id=ask]').click()
    assert 'SCOUT EVIDENCE GUIDE' in up(p.locator('#hub-detail').inner_text())
    assert not p.locator('#u-drawer').is_visible() and not p.locator('#u-tray').is_visible()
    p.locator('#hub-detail [data-hub=card-close]').click()
    assert p.locator('#hub-card').is_hidden()
    p.locator('.h-sidebar [data-hub=close]').click()
    assert p.locator('#u-hub').is_hidden()
    assert not p.locator('#board').get_attribute('inert')
    assert not p.errors


def test_phone_filters_wait_in_the_pop_over_and_the_first_prop_is_on_the_first_screen(ctx, env):
    """legacy: test_phone_keeps_filters_available_without_covering_first_prop (its 300px was the old toolbar's height; the redesign's
    44px controls put the first prop a little lower, still in the top half of the phone screen and fully on it)"""
    p = hub(ctx, env, PHONE)
    is_next(p)
    assert not p.locator('#hub-stat').is_visible()                     # the other filters wait in the Filters pop-over
    first = rows(p).first.bounding_box()
    assert first['y'] < PHONE['height'] / 2 and first['height'] > 60, first
    assert p.locator('#hub-rows tr.h-row .h-player').first.bounding_box()['y'] + 44 < PHONE['height'] - 60      # above the tab bar
    p.locator('#hub-filters-toggle').click()
    assert p.locator('#hub-stat').is_visible()
    p.keyboard.press('Escape')
    assert not p.locator('#hub-stat').is_visible()
    book(p, 'prizepicks')
    got = rows(p).evaluate_all('(rows)=>rows.map(r=>r.dataset.record)')
    assert got and all(x.startswith(('cs2|prizepicks|', 'lol|prizepicks|', 'nba|prizepicks|', 'nfl|prizepicks|')) for x in got), got
    no_h_scroll(p)
    assert not p.errors


def test_tracked_picks_page_filters_by_sport_and_outcome_and_opens_receipts(ctx, env):
    """legacy: test_tracked_picks_have_a_page_with_sport_status_filters_and_receipts"""
    p = hub(ctx, env)
    is_next(p)
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    for name in ('vargo', 'orlo'):
        p.locator('#hub-q').fill(name)
        p.locator('#hub-rows tr.h-row .h-add:not([disabled])').first.click()
    p.locator('.h-sidebar [data-id=studio]').click()
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])', timeout=20000)
    p.locator('#hub-studio-body [data-u=track]').click()
    p.wait_for_selector('#hub-studio-body .u-note.good')
    p.locator('.h-sidebar [data-id=picks]').click()
    p.wait_for_selector('#hub-picks-body .u-pick')
    assert p.locator('#hub-picks-body .u-pick').count() == 2
    p.locator('#hub-picks-body .u-pick button').first.click()
    assert 'CHANCE YOU SAW' in up(p.locator('#hub-picks-body').inner_text())
    p.locator('#hub-pick-status').select_option('hit')
    assert 'No picks in this view' in p.locator('#hub-picks-body').text_content()
    assert p.locator('#hub-picks-body .u-pick').count() == 0
    p.locator('#hub-pick-status').select_option('all')
    p.locator('#hub-pick-sport').select_option('nba')
    assert p.locator('#hub-picks-body .u-pick').count() == 0
    p.locator('#hub-pick-sport').select_option('cs2')
    assert p.locator('#hub-picks-body .u-pick').count() == 2
    assert not p.errors


@pytest.mark.parametrize('path', ['/#slips', '/sports#/slips?lg=nfl', '/lol#scout/studio'])
def test_slip_studio_holds_the_one_account_draft_on_every_sport_route(ctx, env, path):
    """legacy: test_slip_studio_uses_the_shared_account_draft_on_every_sport_route"""
    p = hub(ctx, env)
    is_next(p)
    book(p, 'prizepicks')
    for sport in ('cs2', 'lol', 'nba', 'nfl'):
        p.locator('#hub-sports [data-id=' + sport + ']').click()
        add_first(p)
    saved_state(p, lambda d: len((d.get('slip') or {}).get('legs', [])) == 4)
    p.goto(env.url + path)
    p.wait_for_selector('#hub-studio-body .u-entry', timeout=20000)
    is_next(p)
    assert p.locator('#hub-title').text_content().strip() == 'Slips' and p.locator('#hub-title').is_visible()
    assert p.locator('[data-hub=studio-tab][aria-selected=true]').text_content().strip().startswith('My slip')
    assert p.locator('#hub-studio-body .u-leg').count() == 4
    p.locator('#hub-studio-body [data-u=check]').click()
    p.wait_for_selector('#hub-studio-body .u-status b:has-text("ready")', timeout=20000)
    # owner sign-off 2026-11-05: the PrizePicks help-center table is verified, so the entry shows its payout (4-pick Power 10x)
    body = p.locator('#hub-studio-body').text_content()
    assert 'All-hit payout' in body and 'only in its app' not in body and 'Published payout tiers' not in body
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_board_rows_show_real_counts_placeholders_the_letter_and_the_lines_cell(ctx, env, size):
    """legacy: test_board_rows_show_real_counts_placeholders_chips_and_compare. Same numbers; the Compare cell's summary of the other
    lines ("3 lines · 34.5-42.5") is the Lines button ("+3 lines", "2 apps · 34.5-42.5") and its ladder."""
    first = [True]

    def hits(route):
        response = route.fetch()
        d = response.json()
        got = d['rows']
        if first[0]:
            first[0] = False
            d = {'rows': {}, 'complete': False}           # the shared pass has not reached any row yet
        else:
            for k, v in got.items():
                v['l20'] = {'over': 7, 'under': 2, 'push': 0, 'n': 9}
            d['complete'] = True
        route.fulfill(response=response, json=d)
    p = ctx.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    p.set_viewport_size(size)
    p.route('**/api/upgrade/hits', hits)
    p.goto(env.url + '/#all-sports')
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    is_next(p)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-q').fill('vargo')
    row = rows(p).first
    p.wait_for_selector('#hub-rows .h-s-l5 .h-hit-wait', state='attached')
    assert row.locator('.h-s-l5').text_content().strip() == ''          # a placeholder, never a dash
    # ONE hit strip: the rate on the cell, the exact counts in its tooltip
    l10 = row.locator('.h-s-l10 .h-hit')
    assert l10.text_content() == '60%' and l10.get_attribute('title').startswith('6 Over of the last 10 at this line')
    assert l10.is_visible() or p.viewport_size['width'] < 600         # phones (2026-11-12): the windows are on the card, not the row
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    l20 = row.locator('.h-s-l20 .h-hit')
    assert l20.text_content() == '78%' and '7 Over of the last 9' in l20.get_attribute('title') and '(9 games on file)' in l20.get_attribute('title')
    assert 'h-few' in l20.get_attribute('class') and (l20.is_visible() or p.viewport_size['width'] < 600)
    assert '—' not in row.locator('.h-s-l5').text_content()
    assert row.locator('.h-c-scout .h-grade.h-grade-b').inner_text() == 'B'                 # side, chance and letter in one Scout cell
    scout = row.locator('.h-c-scout').text_content()
    assert 'Over' in scout and '61.2%' in scout and row.locator('.h-c-scout .h-direction').is_visible()
    assert row.locator('.h-chevron').count() == 0
    # the Lines cell: how many other lines, on how many apps, over which range; the ladder under the row lists each one
    btn = row.locator('.h-c-compare .h-lines-btn')
    assert btn.is_visible() and btn.locator('b').text_content() == '+3 lines' and btn.locator('small').text_content() == '2 apps · 34.5–42.5'
    assert btn.get_attribute('aria-label') == 'Show all 4 lines for vargo Kills, 2 apps, 34.5–42.5'
    box = btn.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= size['width'] + 1 and box['height'] >= (44 if size == PHONE else 30), box
    btn.click()
    lines = p.locator('#hub-rows tr.h-ladder-row tr[data-line]')
    got = [(ln.locator('.h-ld-book').text_content().split()[0], ln.locator('.h-ld-line b').text_content()) for ln in lines.all()]
    assert got == [('PrizePicks', '34.5'), ('PrizePicks', '38.5'), ('Underdog', '38.5'), ('PrizePicks', '42.5')], got
    assert all(ln.is_visible() for ln in lines.all())
    p.locator('#hub-sports [data-id=nba]').click()
    p.locator('#hub-q').fill('Karl Anders')
    nba = rows(p).first
    assert nba.locator('.h-grade').count() == 0 and nba.locator('.h-c-scout .h-hold-why').count() == 1     # the plain reason (2026-11-12)
    nba.locator('.h-player').click()
    p.locator('#hub-detail [role=tab][data-id=stats]').click()
    p.wait_for_selector('#hub-detail .h-stat-group h5', timeout=15000)
    assert p.locator('#hub-detail .h-full-research a').get_attribute('href').startswith('/sports?view=classic#/player/')
    heads = {up(h) for h in p.locator('#hub-detail .h-stat-group h5').all_text_contents()}
    # card-and-chips 2026-11-06: factor-engine rows (weight 0) are headed as not part of Scout's forecast, never "Model inputs"
    assert {'MATCHUP', 'AVERAGES', "MATCHUP NUMBERS (NOT PART OF SCOUT'S FORECAST)"} <= heads and 'MODEL INPUTS' not in heads, heads
    no_h_scroll(p)
    assert not p.errors


# ------------------------------------------------------------------------------------ tests/test_workspace_v2_browser.py


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')


def start(ctx, env, path='/#/board', width=1440):
    p = open_page(ctx, env, path, {'width': width, 'height': 900})
    p.wait_for_selector('#u-hub.h-v2')
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    is_next(p)
    return p


@pytest.mark.parametrize('width', [390, 1440])
def test_a_props_lines_are_folded_and_open_and_close_without_losing_the_card(ctx, env, v2, width):
    """legacy: test_player_props_are_collapsed_and_expand_without_losing_research (the "+N props" toggle is the ladder)"""
    p = start(ctx, env, width=width)
    p.locator('#hub-q').fill('vargo')
    p.wait_for_selector('#hub-rows [data-hub=ladder]')
    assert rows(p).count() == 1 and p.locator('#hub-rows tr[data-line]').count() == 0
    toggle = p.locator('#hub-rows [data-hub=ladder]').first
    assert toggle.get_attribute('aria-expanded') == 'false'
    toggle.click()
    assert p.locator('#hub-rows tr[data-line]').count() == 4 and p.locator('#hub-rows [data-hub=ladder]').first.get_attribute('aria-expanded') == 'true'
    p.locator('#hub-rows tr[data-line] .h-ld-book').last.click()               # a line of the ladder opens its own card
    p.wait_for_selector('#hub-detail .h-history-controls')
    assert p.locator('#hub-detail #hub-card-line option:checked').text_content().strip().startswith('PrizePicks 42.5')
    if width >= 1101:
        assert p.locator('#hub-rows tr[data-line]').count() == 4              # the ladder stays open beside the card
    p.locator('#hub-detail [data-hub=card-close]').click()                     # phone: the card is a sheet; Back returns to the board
    p.locator('#hub-rows [data-hub=ladder]').first.click()
    assert p.locator('#hub-rows tr[data-line]').count() == 0 and rows(p).count() == 1
    assert p.locator('#hub-card').is_hidden()
    no_h_scroll(p)
    assert not p.errors


def test_by_game_keeps_the_boards_filters_and_opens_one_games_board(ctx, env, v2):
    """legacy: test_game_route_uses_live_fixture_records_and_scoped_board"""
    p = start(ctx, env)
    p.locator('#hub-q').fill('not-a-player')
    p.locator('#hub-filters-toggle').click()
    p.locator('#hub-watch').click()
    p.locator('#hub-soonOnly').click()
    p.evaluate("location.hash='/games'")
    # By game is a view of the board: it keeps the board's filters and says how many games they hide
    p.wait_for_selector('#hub-games-body:not([hidden]) .h-empty')
    assert re.search(r'\d+ games? hidden by filters', p.locator('#hub-games-body').text_content())
    p.locator('#hub-games-body [data-hub=clear-filters]').click()
    p.wait_for_selector('.h-game-card')
    assert p.locator('#hub-board-section').is_visible() and p.locator('#hub-board-section [data-hub=view][data-id=games]').get_attribute('aria-pressed') == 'true'
    card = p.locator('.h-game-card').first
    event = card.locator('h2').text_content().strip()
    card.locator('button').click()
    assert '#/board' in p.url
    p.wait_for_selector('#hub-rows tr[data-record]')
    assert p.locator('#hub-rows .h-match').all_text_contents() == [event]       # one game header: the board filtered to that game
    assert p.locator('#hub-rows [data-hub=game]').get_attribute('aria-pressed') == 'true'
    p.go_back()
    p.wait_for_selector('#hub-games-body:not([hidden])')
    assert not p.errors


# ------------------------------------------------------------------------------------ tests/test_role_hub_rows_browser.py

ROLE_SIZES = [DESK, {"width": 1280, "height": 900}, {"width": 1200, "height": 900}, {"width": 1101, "height": 900}, {"width": 1100, "height": 900}, PHONE]
w_id = lambda s: str(s["width"])  # noqa: E731

# the board's own boxes: nothing in a row is drawn over another cell or past the row (the phone row is a grid of cells)
OVERLAP = """() => { const out = [];
  for (const tr of document.querySelectorAll('#hub-rows tr.h-row[data-record]')) { if (!tr.offsetParent) continue;
    const row = tr.getBoundingClientRect(), cells = [...tr.cells].filter(c => getComputedStyle(c).display !== 'none' && c.getBoundingClientRect().width > 0);
    const box = cells.map(c => [c.className.split(' ')[0], c.getBoundingClientRect()]);
    for (const [n, b] of box) if (b.left < row.left - 1 || b.right > row.right + 1) out.push([tr.dataset.record, n, 'outside the row']);
    for (let i = 0; i < box.length; i++) for (let j = i + 1; j < box.length; j++) { const a = box[i][1], b = box[j][1];
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) out.push([tr.dataset.record, box[i][0], box[j][0]]); } }
  return out; }"""


def check_rows_next(p, size, where):
    """tests.test_role_hub_rows_browser.check_rows on the redesign: the tag costs the name no width and the row no height, name and tag
    stay inside the player cell, the second line is one line, desktop rows stay compact. The name is read whole from 1181px up and on
    the phone; at 1101-1180px (the narrowest desktop, with the slip column) a long name may end in an ellipsis but keeps >= 56px."""
    w = size["width"]
    got = p.evaluate(MEASURE)
    assert got, where
    assert p.evaluate(OVERLAP) == [], where
    for r in got:
        assert r["nw"] == r["bare"]["nw"] and r["nh"] == r["bare"]["nh"], (where, r)                    # the name keeps its width
        assert r["h"] == r["bare"]["h"], (where, r)                                                     # the tag adds no height
        if r["sub"] and r["nw"] == 0:
            continue                                      # By player: the name and its role tag are on the player's header row
        if w > 1180 or w <= 1100:
            assert not r["cut"], (where, r)
        else:
            assert r["nw"] >= 56, (where, r)
        assert (r["tagIn"] or w < 600) and r["bare"]["nameIn"], (where, r)          # phones (2026-11-12): no role tag on the row
        assert r["line2"] <= 18, (where, r)                                                             # the second line stays one line
        if r["tag"] is not None:
            assert r["tag"] == r["tag"].strip() and r["tag"], (where, r)
        else:
            assert not PLACEHOLDER.search(r["text"]), (where, r)
        if w > 1100:
            # the compact desktop row; NBA / NFL rows carry one more 10px line, their odds (owner 2026-11-12), plus a wrapped app tag
            assert r["h"] <= (92 if where in ("nba", "nfl") else 72), (where, r)
        if w >= 1280:
            assert r["tagFull"], (where, r)                                                             # the whole role is read
    if w <= 1100:
        no_h_scroll(p)
    return got


def lines_cells(p):
    """every row's Lines cell: on screen, inside the table, and for a prop with more lines a button that can be pressed"""
    return p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr.h-row[data-record]')].filter(tr => tr.offsetParent).map(tr => {
      const td = tr.querySelector('td.h-c-compare'), e = td.querySelector('.h-lines-btn,.h-lines-one'), r = e ? e.getBoundingClientRect() : null,
        t = document.querySelector('.h-table-wrap').getBoundingClientRect();
      return {key: tr.dataset.record, kind: e ? e.className.split(' ')[0] : null, w: r ? Math.round(r.width) : 0, h: r ? Math.round(r.height) : 0,
              inside: !!r && r.left >= t.left - 1 && r.right <= Math.min(t.right, innerWidth) + 1, text: e ? e.textContent : ''}; })""")


@pytest.mark.parametrize("size", ROLE_SIZES, ids=w_id)
def test_every_sports_rows_carry_the_cards_role_and_keep_name_and_lines(browser, roles_env, next_board, size):
    """legacy: test_every_sports_rows_carry_the_cards_role [1101] [1100] [390] (the real bugs it met on the redesign, see
    tests/UI_NEXT_TRIAGE.md: the phone row's grid with the production column flags, the name's 42px lost to a hidden face, and the
    Lines cell hidden at 1101-1180px), at every legacy width plus 1200."""
    p = next_board(browser, roles_env, size)
    try:
        is_next(p)
        for sp, name, want in CASES:
            cc.sport(p, sp)
            p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
            every = check_rows_next(p, size, sp)
            assert any(r["tag"] for r in every) or sp != "cs2", (sp, every)
            cells = lines_cells(p)
            cells = [c for c in cells if not (size["width"] < 600 and c["kind"] == "h-lines-one")]   # phones (2026-11-12): a one-line row names no app
            assert cells and all(c["kind"] and c["inside"] and c["w"] > 20 and c["h"] >= 12 for c in cells), (sp, cells)   # the Lines cell is on screen
            assert all(c["h"] >= (44 if size["width"] <= 1100 else 28) for c in cells if c["kind"] == "h-lines-btn"), (sp, cells)
            row = hub_row(p, name)
            assert row["tag"] == want, (sp, row)
            if size["width"] > 1100:
                assert row["h"] <= 72, (sp, row)
            card = hub_card(p, name)
            assert card["tag"] == row["tag"] == record_role(p, sp, name), (sp, card, row)     # board row = card = record, one text
            close_card(p)
        for sp, name in NONE:
            cc.sport(p, sp)
            row = hub_row(p, name)
            assert row["tag"] is None and not PLACEHOLDER.search(row["text"]), (sp, row)
            assert record_role(p, sp, name) is None
            p.locator("#hub-q").fill("")
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("width", [1101, 1150, 1180])
def test_the_ladder_opens_from_the_board_on_the_narrowest_desktop(browser, roles_env, next_board, width):
    """real bug (not in a legacy test by name; found porting test_every_sports_rows_carry_the_cards_role[1101]): at 1101-1180px the
    Lines column was display:none, so a prop's other lines could not be opened from the board."""
    p = next_board(browser, roles_env, {"width": width, "height": 900})
    try:
        is_next(p)
        cc.sport(p, "cs2")
        btn = p.locator("#hub-rows tr.h-row", has=p.locator(".h-player b", has_text=re.compile("^vargo$"))).locator("[data-hub=ladder]")
        assert btn.is_visible() and btn.locator("b").text_content() == "+3 lines"
        assert "4 lines for vargo Kills, 2 apps, 34.5–42.5" in btn.get_attribute("aria-label")       # apps and range stay in its label
        assert p.locator("#hub-table thead th.h-c-compare").is_visible()
        btn.click()
        lines = p.locator("#hub-rows tr.h-ladder-row tr[data-line]")
        assert lines.count() == 4 and all(ln.is_visible() for ln in lines.all())
        assert p.evaluate("document.querySelector('.h-table-wrap').scrollWidth <= document.querySelector('.h-table-wrap').clientWidth + 1")
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("size", [DESK, PHONE], ids=w_id)
def test_a_players_header_ladder_and_by_game_carry_the_tag(browser, roles_env, next_board, size):
    """legacy: test_more_props_sub_rows_and_by_game_carry_the_tag. The "+N props" sub-rows are gone: a search that lands on one player
    lists the props under the player's header, which carries the role tag once; the lines are the ladder. By game is unchanged."""
    if size["width"] < 600:
        pytest.skip("phone simplification (owner 2026-11-12): the phone row no longer carries role tags; the card has it")
    p = next_board(browser, roles_env, size)
    try:
        is_next(p)
        cc.sport(p, "cs2")
        hub_row(p, "vargo")
        head = p.locator("#hub-rows tr.h-player-row .h-player-head")
        assert head.count() == 1 and head.locator("b").first.text_content() == "vargo"
        tag = head.locator(".h-role")
        assert tag.count() == 1 and tag.text_content() == CASES[0][2] and tag.is_visible()
        assert tag.evaluate("e => e.scrollWidth <= e.clientWidth + 1")                    # the whole role is read
        hb, tb = head.bounding_box(), tag.bounding_box()
        assert tb["x"] >= hb["x"] and tb["x"] + tb["width"] <= hb["x"] + hb["width"] + 1 and hb["height"] <= 60, (hb, tb)
        p.locator("#hub-rows [data-hub=ladder]").first.click()
        p.wait_for_function("document.querySelectorAll('#hub-rows tr.h-ladder-row tr[data-line]').length === 4", timeout=10000)
        check_rows_next(p, size, "by-player")
        p.locator("#hub-q").fill("")
        # By game: the game cards name no player; the game's own board (Show this game's props) draws the same rows
        p.locator("[data-hub=view][data-id=games]").first.click()
        p.wait_for_selector("#hub-games-body [data-hub=game-board]", timeout=20000)
        vargo_game = p.evaluate("""() => { const b = [...document.querySelectorAll('#hub-games-body .h-game-card')].find(c => /Halcyon/.test(c.textContent));
          return b ? b.querySelector('[data-hub=game-board]').dataset.id : null; }""")
        assert vargo_game
        p.locator(f"#hub-games-body [data-hub=game-board][data-id='{vargo_game}']").click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record] .h-player b')].some(b => b.innerText === 'vargo')", timeout=20000)
        got = check_rows_next(p, size, "by-game")
        tags = {r["name"]: r["tag"] for r in got}
        assert tags["vargo"] == CASES[0][2] and tags.get("brandr", None) is None, tags
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


def test_game_header_rows_span_the_columns_on_screen_and_the_player_column_keeps_its_width(browser, roles_env, next_board):
    """legacy: test_game_header_rows_span_only_the_columns_on_screen (the NFL fixture board falls back to start time, so game header
    rows head its rows). Its player column was 84px at 1200px on the redesign (26px of name once the hidden face's 42px were taken
    off): now >= 100px at every width, with the name read whole."""
    p = next_board(browser, roles_env, DESK)
    try:
        is_next(p)
        cc.sport(p, "nfl")
        p.wait_for_selector("#hub-rows tr.h-game-row", timeout=20000)
        for w in (1440, 1200, 1101, 1440, 1150, 1300, 1181, 1279):
            p.set_viewport_size({"width": w, "height": 900})
            p.wait_for_timeout(150)
            got = p.evaluate(SPAN)
            assert got["spans"] and set(got["spans"]) == {got["shown"]}, (w, got)
            assert got["fill"] <= 1 and got["player"] >= 100, (w, got)                  # the rows reach the table's right edge
            names = p.evaluate("[...document.querySelectorAll('#hub-rows tr.h-row .h-player b')].map(b => [b.textContent, b.clientWidth, b.scrollWidth])")
            assert names and all(cw >= 60 and sw <= cw + 1 for _, cw, sw in names), (w, names)
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------ legacy bodies, unchanged (role rows, desktop)
# In the listed run (2026-11-12 01:29-02:00, started before c0c5b06 was committed) these legacy tests could not press a desktop row's
# name. That did not happen again on c0c5b06: their bodies pass on the redesign as written, with and without this branch's fixes
# (tests/UI_NEXT_TRIAGE.md). They run here unchanged on a member's redesign context, so the redesign keeps what they check.

import tests.test_role_hub_rows_browser as rh
import tests.test_role_tags_browser as rt


def opens_next(monkeypatch, mod):
    real = mod.open_board

    def opened(*a, **k):
        p = real(*a, **k)
        is_next(p)
        return p
    monkeypatch.setattr(mod, "open_board", opened)


@pytest.mark.parametrize("size", [DESK, {"width": 1101, "height": 900}], ids=w_id)
def test_rows_stay_compact_with_a_leg_on_the_slip_on_next(browser, roles_env, next_board, monkeypatch, size):
    """legacy: test_role_hub_rows_browser.py::test_rows_stay_compact_with_a_leg_on_the_slip [1440] [1101]."""
    opens_next(monkeypatch, rh)
    rh.test_rows_stay_compact_with_a_leg_on_the_slip(browser, roles_env, size)


def test_hub_card_carries_the_records_role_and_rows_stay_compact_on_next(browser, roles_env, next_board, monkeypatch):
    """legacy: test_role_tags_browser.py::test_hub_card_carries_the_records_role_and_rows_stay_compact[size0] (1440px)."""
    monkeypatch.setattr(rt, "context", nxt)
    opens_next(monkeypatch, rt)
    rt.test_hub_card_carries_the_records_role_and_rows_stay_compact(browser, roles_env, DESK)
