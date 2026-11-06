"""Audit 2026-11-05, board group (A6, A13, A14, B2, B3, B5, B6, B7, headshots), in a real browser against the real Handler and fixture
data (no network: ESPN images are blocked here, so every face shows its initials). Display only: no grade, chance, stake or letter moves.
AUDIT_SHOTS=<folder> keeps a 390px and a 1440px screenshot of every changed surface."""
import os
import re
from pathlib import Path

import pytest

from tests.test_research_hub_browser import book, saved_state
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

FLAGS = ("SCOUT_WORKSPACE_V2", "SCOUT_BOARD_H2H", "SCOUT_GENIE", "SCOUT_SLIP_HEADER", "SCOUT_CLOSING_LINES", "SCOUT_PUBLIC_RECORD")
SHOTS = Path(os.environ.get("AUDIT_SHOTS") or "")
SIZES = [DESK, PHONE]


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


def board(ctx, env, size=DESK, path='/#all-sports'):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    return p


def sport(p, s):
    p.locator(f'#hub-sports [data-id={s}]').click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{s}|'))")


def keys(p):
    return p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].map(r => r.dataset.record)")


def rates(p, w):
    """the rate on each row's hit cell for window w (None for an empty cell), in board order"""
    return p.evaluate("""(w) => [...document.querySelectorAll('#hub-rows tr[data-record] .h-s-' + w + ' .h-hit')].map(e => {
      const m = /^(\\d+)%$/.exec(e.textContent.trim()); return m ? Number(m[1]) : null; })""", w)


# ---------------------------------------------------------------------------------------------------------- A6 / B5: sorting


def test_sort_menu_and_headers_sort_every_sport_and_the_choice_is_remembered(ctx, env):
    p = board(ctx, env)
    # default: Edge where the Edge column exists (SCOUT_BOARD_H2H on here)
    assert p.locator('#hub-sort').input_value() == 'edge'
    assert p.locator('#hub-table th.h-c-edge').get_attribute('aria-sort') == 'descending'
    opts = p.locator('#hub-sort option').evaluate_all('os => os.map(o => o.value)')
    assert opts[:3] == ['edge', 'chance', 'start'] and {'l5', 'l10', 'l20', 'h2h', 'season', 'line'} <= set(opts)
    shot(p, 'a6-sort-default-edge')
    # header click: L10 high to low, empty cells last; a second click turns it around
    p.locator('#hub-table [data-hub=sort-col][data-id=l10]').click()
    assert p.locator('#hub-sort').input_value() == 'l10'
    got = rates(p, 'l10')
    filled = [x for x in got if x is not None]
    assert filled == sorted(filled, reverse=True) and got[:len(filled)] == filled, got
    assert p.locator('#hub-table [data-hub=sort-col][data-id=l10]').get_attribute('data-sort') == 'descending'
    p.locator('#hub-table [data-hub=sort-col][data-id=l10]').click()
    got = [x for x in rates(p, 'l10') if x is not None]
    assert got == sorted(got) and p.locator('#hub-table [data-hub=sort-col][data-id=l10]').get_attribute('data-sort') == 'ascending'
    shot(p, 'a6-sort-l10-asc')
    # the menu: Line low to high, the direction button flips it
    p.locator('#hub-sort').select_option('line')
    lines = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record] td.h-c-line>b')].map(b => parseFloat(b.firstChild.textContent))")
    assert lines == sorted(lines), lines
    p.locator('#hub-sort-dir').click()
    lines = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record] td.h-c-line>b')].map(b => parseFloat(b.firstChild.textContent))")
    assert lines == sorted(lines, reverse=True), lines
    # Scout chance header
    p.locator('#hub-table [data-hub=sort-col][data-id=chance]').click()
    ch = p.evaluate("""[...document.querySelectorAll('#hub-rows tr[data-record] .h-direction span')].map(e => parseFloat(e.textContent))""")
    assert ch == sorted(ch, reverse=True), ch
    # every sport sorts the same way and shows the same columns (B5)
    heads = {}
    for s in ('cs2', 'lol', 'nba', 'nfl'):
        sport(p, s)
        assert p.locator('#hub-sort').input_value() == 'chance'
        heads[s] = [h.strip() for h in p.locator('#hub-table thead th').all_text_contents()]
        ch = p.evaluate("""[...document.querySelectorAll('#hub-rows tr[data-record] .h-direction span')].map(e => parseFloat(e.textContent))""")
        assert ch == sorted(ch, reverse=True), (s, ch)
    assert heads['nba'] == heads['cs2'] == heads['nfl'] == heads['lol'], heads
    assert 'Edge vs break-even' in heads['nba'] and p.locator('#hub-strip-head, #hub-table thead .h-strip-head .h-th-sort').count() == 5
    # remembered for this member: on the account and across a reload
    saved_state(p, lambda d: ((d.get('prefs') or {}).get('researchHub') or {}).get('sort') == {'key': 'chance', 'dir': 'desc'})
    p.reload()
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    assert p.locator('#hub-sort').input_value() == 'chance'
    p.evaluate("localStorage.clear()")
    p.reload()
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sort').value === 'chance'", timeout=15000)   # from the account copy
    assert not p.errors


def test_start_time_sort_keeps_game_headers_and_flag_off_defaults_to_start(ctx, env, monkeypatch):
    monkeypatch.delenv("SCOUT_BOARD_H2H", raising=False)
    p = board(ctx, env)
    assert p.locator('#hub-sort').input_value() == 'start' and p.locator('#hub-sort option[value=edge]').count() == 0
    assert p.locator('#hub-rows tr.h-game-row').count() > 1
    starts = p.evaluate("[...document.querySelectorAll('#hub-rows .h-countdown[data-start]')].map(e => Date.parse(e.dataset.start))")
    assert starts == sorted(starts)
    assert p.locator('#hub-table thead .h-strip-head span').all_inner_texts() == ['L5', 'L10', 'L20']
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B5: NBA / NFL rows


@pytest.mark.parametrize('size', SIZES)
def test_sport_tag_stays_whole_under_the_edge_sort(ctx, env, size):
    """Follow-up 2026-11-06: with 'Team vs Opp' under the name (any sort but start time) only the matchup text is cut; the sport
    tag keeps its full text inside its cell at 390 and 1440."""
    p = board(ctx, env, size)
    assert p.locator('#hub-sort').input_value() == 'edge'
    bad = p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr[data-record] .h-player small')].map(s => {
      const i = s.querySelector(':scope > i'), td = s.closest('td'), v = s.querySelector(':scope > .h-vs');
      if (!i || getComputedStyle(i).display === 'none') return null;
      const a = i.getBoundingClientRect(), c = td.getBoundingClientRect();
      return (a.right <= c.right + 0.5 && i.scrollWidth <= i.clientWidth + 1 && i.textContent.trim() && a.width > 8 && v) ? null
        : {row: s.closest('tr').dataset.record, tag: i.textContent, right: a.right, cell: c.right, w: a.width}; }).filter(Boolean)""")
    assert bad == [], bad
    shown = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record] .h-player small>i')].filter(i => getComputedStyle(i).display !== 'none').length")
    assert shown > 0
    assert p.locator('#hub-rows .h-player small .h-vs').first.inner_text().count(' vs ') == 1
    shot(p, 'b5-sport-tag-edge-sort')
    assert not p.errors


@pytest.mark.parametrize('size', SIZES)
def test_sports_rows_name_the_game_once_and_never_cut_research_grade(ctx, env, size):
    p = board(ctx, env, size)
    sport(p, 'nba')
    p.locator('#hub-sort').select_option('start')
    heads = p.locator('#hub-rows .h-game-head > span:not(.h-countdown)').all_inner_texts() if size == DESK else []
    assert all(not t.startswith('NBA · NBA') for t in heads) and (size != DESK or all(t.startswith('NBA · ') for t in heads)), heads
    tags = p.locator('#hub-rows .h-grade-tag')
    assert tags.count() >= 1
    for i in range(tags.count()):
        assert tags.nth(i).inner_text().replace('\n', ' ') == 'Research grade'
        assert tags.nth(i).evaluate('e => e.scrollWidth <= e.clientWidth + 1 && getComputedStyle(e).textOverflow !== "ellipsis"')
    if size == DESK:
        h = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].map(r => Math.round(r.getBoundingClientRect().height))")
        assert all(56 <= x <= 64 for x in h), h
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-card-head')
    assert 'NBA · NBA' not in p.locator('#hub-detail .h-card-head').inner_text()
    p.keyboard.press('Escape')
    p.locator('#hub-board-section [data-hub=view][data-id=games]').click()
    p.wait_for_selector('#hub-games-body .h-game-card')
    assert all(t.strip() == 'NBA' for t in p.locator('#hub-games-body .h-eyebrow').all_inner_texts())
    shot(p, 'b5-nba-by-game')
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B6: the chart's line


def test_chart_dashed_line_is_the_offered_line_and_the_median_is_separate(ctx, env):
    p = board(ctx, env)
    sport(p, 'nba')
    p.locator('#hub-q').fill('Karl')
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg .line-mark', state='attached', timeout=20000)
    line = float(p.locator('#hub-detail #hub-card-line option:checked').inner_text().split()[-1])
    m = p.evaluate("""() => { const svg = document.querySelector('#hub-detail .h-history-chart svg');
      const ticks = [...svg.querySelectorAll('line.grid')].map((l, i) => [Number(svg.querySelectorAll('line.grid + text')[i].textContent), Number(l.getAttribute('y1'))]);
      const lm = svg.querySelector('.line-mark'), mm = svg.querySelector('.median-mark');
      return {ticks, line: Number(lm.getAttribute('y1')), lab: svg.querySelector('.line-lab').textContent, labY: Number(svg.querySelector('.line-lab').getAttribute('y')),
              median: mm ? Number(mm.getAttribute('y1')) : null, mlab: svg.querySelector('.median-lab')?.textContent,
              dash: getComputedStyle(lm).strokeDasharray, mdash: mm ? getComputedStyle(mm).strokeDasharray : null}; }""")
    (v0, y0), (v1, y1) = m['ticks'][0], m['ticks'][1]
    at = y0 + (line - v0) * (y1 - y0) / (v1 - v0)
    assert abs(at - m['line']) < 0.6, m                     # the dashed line sits at the offered line on the chart's own scale
    # named on the line itself (just above or just below it), or in the top margin when bars fill both spots
    assert m['lab'] == f'Line {line:g}' and (abs(m['labY'] - (m['line'] - 4)) < 0.6 or abs(m['labY'] - (m['line'] + 12)) < 0.6
                                             or m['labY'] <= 16), m
    # follow-up 2026-11-06: the label's box never covers a bar, the newest game's value or the median label
    clash = p.evaluate("""() => { const svg = document.querySelector('#hub-detail .h-history-chart svg');
      const b = (e) => { const r = e.getBBox(); return [r.x, r.y, r.x + r.width, r.y + r.height]; };
      const lab = b(svg.querySelector('.line-lab'));
      const others = [...svg.querySelectorAll('rect'), ...svg.querySelectorAll('text:not(.line-lab):not(.opp):not(.date)')]
        .filter(e => !(e.tagName === 'text' && e.previousElementSibling && e.previousElementSibling.matches('line.grid')));
      return others.map(e => [e.tagName + ':' + (e.textContent || '').slice(0, 20), b(e)])
        .filter(([, q]) => lab[0] < q[2] - 0.5 && lab[2] > q[0] + 0.5 && lab[1] < q[3] - 0.5 && lab[3] > q[1] + 0.5).map(x => x[0]); }""")
    assert clash == [], clash
    assert m['mlab'] and m['mlab'].startswith('Median ') and m['median'] is not None and m['dash'] != m['mdash'], m
    vals = sorted(float(t) for t in p.locator('#hub-detail .h-history-table tbody td:nth-child(3)').all_text_contents())
    mid = len(vals) // 2
    med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    assert m['mlab'] == f'Median {round(med, 1):g}', (m, med)
    assert 'Median' in p.locator('#hub-detail .h-legend').inner_text()
    shot(p, 'b6-chart-line-and-median')
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B7: tabs, sticky bar, scroll


@pytest.mark.parametrize('size', SIZES)
def test_sport_tabs_keep_their_width_toolbar_sticks_and_a_sport_change_scrolls_up(ctx, env, size):
    import time

    page_ctx = ctx
    slow = {'on': True}

    def hold(route):
        if slow['on'] and 'sport=nfl' in route.request.url:
            time.sleep(2.5)
        route.continue_()
    page_ctx.route('**/api/upgrade/board*', hold)
    p = open_page(page_ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    before = p.evaluate("[...document.querySelectorAll('#hub-sports button')].map(b => Math.round(b.getBoundingClientRect().width))")
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && document.querySelector('#hub-sports [data-id=nfl] span:not(.h-n-wait)')", timeout=30000)
    after = p.evaluate("[...document.querySelectorAll('#hub-sports button')].map(b => Math.round(b.getBoundingClientRect().width))")
    assert before == after, (before, after)
    slow['on'] = False
    # sticky toolbar: scrolled down, the toolbar is still on screen
    scroller = "(document.querySelector('#hub-center').scrollHeight > document.querySelector('#hub-center').clientHeight ? document.querySelector('#hub-center') : document.scrollingElement)"
    p.evaluate(f"{scroller}.scrollTop = 600")
    p.wait_for_timeout(200)
    top = p.locator('#u-hub .h-toolbar').bounding_box()['y']
    assert p.evaluate(f"{scroller}.scrollTop") > 100 and -1 <= top <= 2, top
    shot(p, 'b7-sticky-toolbar')
    # a sport change starts at the top
    p.locator('#hub-sports [data-id=cs2]').click()
    p.wait_for_timeout(200)
    assert p.evaluate(f"{scroller}.scrollTop") == 0
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B3: PrizePicks line kinds


@pytest.mark.parametrize('size', SIZES)
def test_every_prizepicks_row_leg_and_card_names_its_line_kind(ctx, env, size):
    p = board(ctx, env, size)
    sport(p, 'cs2')
    p.locator('#hub-q').fill('vargo')
    p.locator('#hub-rows [data-hub=player-props]').first.click()
    rows = p.locator('#hub-rows tr[data-record]')
    seen = set()
    for i in range(rows.count()):
        r = rows.nth(i)
        k = r.get_attribute('data-record')
        kinds = r.locator('td.h-c-line .u-lt').all_inner_texts()
        if '|prizepicks|' in k:
            assert len(kinds) == 1 and kinds[0] in ('Standard', 'Goblin', 'Demon'), (k, kinds)
            seen.add(kinds[0])
        else:
            assert kinds == [], (k, kinds)
    assert {'Goblin', 'Demon'} <= seen, seen                           # the fixture's vargo has a goblin and a demon line
    shot(p, 'b3-pp-line-kinds')
    pp = p.locator('#hub-rows tr[data-record*="|prizepicks|"]').first
    pp.locator('.h-player').click()
    p.wait_for_selector('#hub-detail #hub-card-line')
    options = p.locator('#hub-detail #hub-card-line option').all_inner_texts()
    assert all(re.search(r'· (Standard|Goblin|Demon)$', o) for o in options if o.startswith('PrizePicks')), options
    p.locator('#hub-detail [data-hub=card-tab][data-id=lines]').click()
    assert all(t in ('Standard', 'Goblin', 'Demon') for t in p.locator('#hub-detail .h-line-shop td:first-child small').all_inner_texts())
    p.keyboard.press('Escape')
    book(p, 'prizepicks')
    p.locator('#hub-rows .h-add:not([disabled]):not(.is-on)').first.click()
    if p.locator('#hub-card').is_visible():
        p.locator('#hub-detail [data-hub=detail-add]:not([disabled])').click()
        p.locator('#hub-detail [data-hub=card-close]').click()
    p.goto(env.url + '/#/slips')
    p.wait_for_selector('#hub-studio-body .u-leg')
    assert re.search(r'(Standard|Goblin|Demon)', p.locator('#hub-studio-body .u-leg .u-lt').first.inner_text())
    shot(p, 'b3-pp-slip-leg')
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B2: below break-even


@pytest.mark.parametrize('size', SIZES)
def test_a_side_below_break_even_keeps_its_wording_and_says_so(ctx, env, monkeypatch, size):
    from gradecore import payouts
    real = payouts.break_even
    monkeypatch.setattr(payouts, "break_even", lambda b, t, n: 0.60 if (b, t, n) == ("underdog", "standard", 2) else real(b, t, n))
    p = board(ctx, env, size)
    rows = p.locator('#hub-rows tr[data-record]')
    below = 0
    for i in range(rows.count()):
        r = rows.nth(i)
        edge = r.locator('td.h-c-edge .h-edge').inner_text().strip() if r.locator('td.h-c-edge .h-edge').count() else '—'
        chip = r.locator('.h-below').count()
        scout = r.locator('td.h-c-scout').inner_text()
        if edge.startswith('−'):
            below += 1
            assert chip == 1 and r.locator('.h-below').text_content().replace('‑', '-') == 'Below break-even', (edge, scout)
            assert re.search(r'(Over|Under)\s+\d+\.\d%', scout)                 # the side and its chance stay on the row
            assert re.search(r'(?m)^[ABC][+-]?$|Lean (Over|Under)|Research', scout)       # and its letter or Lean wording
            assert 'No bet' not in scout
            assert 'break-even' in r.locator('.h-below').get_attribute('title')
        elif edge.startswith('+'):
            assert chip == 0, (edge, scout)
    assert below >= 1
    if size == DESK:
        h = p.evaluate(r"[...document.querySelectorAll('#hub-rows tr[data-record]')].map(r => [Math.round(r.getBoundingClientRect().height), r.innerText.replace(/\s+/g, ' ')])")
        assert all(56 <= x <= 64 for x, _ in h), [x for x in h if not 56 <= x[0] <= 64]      # the chip never grows the row
    shot(p, 'b2-below-break-even')
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- A13 / A14


@pytest.mark.parametrize('size', SIZES)
def test_filters_panel_words_fit_and_by_game_counts_read_singular(ctx, env, size):
    p = board(ctx, env, size)
    p.locator('#hub-filters-toggle').click()
    for b in p.locator('#hub-filter-pop .h-quick button').all():
        assert b.evaluate('e => e.scrollWidth <= e.clientWidth + 1 && e.getBoundingClientRect().right <= innerWidth'), b.inner_text()
    assert 'Starts within 1h' in p.locator('#hub-filter-pop').inner_text()
    shot(p, 'a13-filters-panel')
    p.keyboard.press('Escape')
    p.locator('#hub-q').fill('Sam Field')
    p.locator('#hub-board-section [data-hub=view][data-id=games]').click()
    p.wait_for_selector('#hub-games-body .h-game-card')
    text = p.locator('#hub-games-body .h-game-card p').first.inner_text()
    assert re.fullmatch(r'1 player · 1 market · \d+ lines?', text), text
    assert '1 players' not in p.locator('#hub-games-body').inner_text() and '1 markets' not in p.locator('#hub-games-body').inner_text()
    shot(p, 'a13-by-game-singular')
    assert not p.errors


def test_phone_hit_chips_are_larger(ctx, env):
    p = board(ctx, env, PHONE)
    sizes = p.evaluate("""[...document.querySelectorAll('#hub-rows tr[data-record] .h-strip .h-hit')].slice(0, 20).map(e => {
      const r = e.getBoundingClientRect(), c = e.closest('.h-strip-cell').getBoundingClientRect();   // 2026-11-06: label + value box
      return [Math.round(r.width), Math.round(c.height), parseFloat(getComputedStyle(e).fontSize)]; })""")
    assert sizes and all(w >= 32 and h >= 44 and f >= 12 for w, h, f in sizes), sizes      # were ~22px wide, 26px tall, 11px
    heights = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].slice(0,6).map(r=>Math.round(r.getBoundingClientRect().height))")
    assert all(120 <= h <= 215 for h in heights), heights      # 2026-11-06: the hit windows' own labelled line
    shot(p, 'a14-phone-chips')
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- headshots and logos


FACE = r'^https://a\.espncdn\.com/i/headshots/(nba|nfl)/players/full/\d+\.png$'
LOGO = r'^https://a\.espncdn\.com/i/teamlogos/(nba|nfl)/500/[a-z]{2,4}\.png$'


def initials_fit(p, sel, want):
    """follow-up 2026-11-06: a leg face's initials sit on one line, centred in the circle, never styled as a sport tag"""
    got = p.evaluate("""(sel) => [...document.querySelectorAll(sel)].map(f => { const i = f.querySelector(':scope > i'), a = f.getBoundingClientRect(),
      b = i.getBoundingClientRect(), cs = getComputedStyle(i);
      return {t: i.textContent, w: a.width, h: a.height, iw: b.width, ih: b.height, dx: Math.abs((b.left + b.width / 2) - (a.left + a.width / 2)),
              border: cs.borderTopWidth, ml: cs.marginLeft, pad: cs.paddingLeft}; })""", sel)
    assert sorted(x['t'] for x in got) == sorted(want), got
    for x in got:
        assert round(x['w']) == round(x['h']) and x['iw'] < x['w'] - 4 and x['ih'] < x['h'] * 0.7, x   # one line inside the circle
        assert x['dx'] < 1.5 and x['border'] in ('0px', '') and x['ml'] == '0px' and x['pad'] == '0px', x


@pytest.mark.parametrize('size', SIZES)
def test_nba_nfl_faces_on_rows_card_slip_and_picks_esports_unchanged(ctx, env, size):
    p = board(ctx, env, size)
    sport(p, 'cs2')
    assert p.locator('#hub-rows .u-face').count() == 0                       # esports rows unchanged
    sport(p, 'nba')
    rows = p.locator('#hub-rows tr[data-record]')
    assert p.locator('#hub-rows .u-face').count() == rows.count()
    face = p.locator('#hub-rows .u-face').first
    img = face.locator('img.u-face-shot')
    assert re.match(FACE, img.get_attribute('src')) and img.get_attribute('loading') == 'lazy'
    name = rows.first.locator('.h-player b').inner_text()
    assert img.get_attribute('alt') == f'{name} headshot'
    logo = face.locator('img.u-face-logo')
    assert re.match(LOGO, logo.get_attribute('src')) and re.fullmatch(r'[A-Z]{2,4} logo', logo.get_attribute('alt'))
    p.wait_for_function("document.querySelector('#hub-rows .u-face').classList.contains('u-face-fb')")   # blocked image -> initials
    assert face.locator('i').inner_text() == ''.join(w[0] for w in name.split()[:2]).upper()
    box = face.bounding_box()
    assert round(box['width']) == 32 and round(box['height']) == 32
    if size == DESK:
        h = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].map(r => Math.round(r.getBoundingClientRect().height))")
        assert all(56 <= x <= 64 for x in h), h                              # row height unchanged
    shot(p, 'faces-board-nba')
    sport(p, 'nfl')
    assert re.match(FACE.replace('(nba|nfl)', 'nfl'), p.locator('#hub-rows .u-face img.u-face-shot').first.get_attribute('src'))
    # the card: 48px
    rows.first.locator('.h-player').click()
    p.wait_for_selector('#hub-detail .h-card-head .u-face')
    b = p.locator('#hub-detail .h-card-head .u-face').bounding_box()
    assert round(b['width']) == 48
    shot(p, 'faces-card-nfl')
    p.keyboard.press('Escape')
    # the slip column / Slip tab, the Slips page and My picks
    sport(p, 'nba')
    book(p, 'underdog')
    for who in ('Karl', 'Reed'):
        p.locator('#hub-q').fill(who)
        p.locator('#hub-rows .h-add:not([disabled]):not(.is-on)').first.click()
        if p.locator('#hub-card').is_visible():
            p.locator('#hub-detail [data-hub=detail-add]:not([disabled])').click()
            p.locator('#hub-detail [data-hub=card-close]').click()
    p.locator('#hub-q').fill('')
    if size == DESK:
        legs = p.locator('#hub-slip-content .u-leg')
        assert legs.count() == 2 and p.locator('#hub-slip-content .u-leg .u-face').count() == 2
        initials_fit(p, '#hub-slip-content .u-leg .u-face', ['KA', 'RL'])
        shot(p, 'faces-slip-column')
    p.goto(env.url + '/#/slips')
    p.wait_for_selector('#hub-studio-body .u-leg .u-face')
    assert p.locator('#hub-studio-body .u-leg .u-face').count() == 2
    p.wait_for_function("[...document.querySelectorAll('#hub-studio-body .u-leg .u-face')].every(f => f.classList.contains('u-face-fb'))")
    initials_fit(p, '#hub-studio-body .u-leg .u-face', ['KA', 'RL'])
    assert re.match(FACE, p.locator('#hub-studio-body .u-leg .u-face img').first.get_attribute('src'))
    shot(p, 'faces-my-slip')
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])', timeout=20000)
    p.locator('#hub-studio-body [data-u=track]').click()
    p.wait_for_selector('#hub-studio-body .u-note.good', timeout=20000)
    p.goto(env.url + '/#/picks')
    p.wait_for_selector('#hub-picks-body .u-pick')
    assert p.locator('#hub-picks-body .u-pick .u-face').count() == p.locator('#hub-picks-body .u-pick').count() == 2
    shot(p, 'faces-my-picks')
    p.goto(env.url + '/#/slips?tab=tracked')
    p.wait_for_selector('#hub-slip-history .u-pick .u-face')
    shot(p, 'faces-tracked')
    no_h_scroll(p)
    assert not p.errors


def test_auto_build_legs_and_the_lineup_image_carry_faces(ctx, env):
    p = board(ctx, env)
    asked = []
    p.on('request', lambda r: asked.append(r.url) if 'espncdn' in r.url else None)
    p.goto(env.url + '/#/slips?tab=build')
    p.wait_for_selector('#hub-generate')
    p.locator('#hub-generation-sports input[value=lol]').uncheck()
    p.locator('#hub-generation-book').select_option('prizepicks')
    p.locator('#hub-generation-type').select_option('power')
    p.locator('#hub-generation-count').select_option('3')
    p.locator('#hub-generation-slips').select_option('1')
    p.locator('#hub-generation-research').select_option('include')
    p.locator('#hub-generate').click()
    p.wait_for_function(r"document.querySelector('#hub-generated .h-generated-card li') || /\w/.test(document.querySelector('#hub-generation-status').textContent) && !document.querySelector('#hub-generate').disabled", timeout=30000)
    assert p.locator('#hub-generated .h-generated-card li').count(), p.locator('#hub-generation-status').inner_text()
    legs = p.locator('#hub-generated .h-generated-card li')
    sports = [x.split(' · ')[0] for x in legs.locator('div > small:first-of-type').all_inner_texts()]
    faces = [legs.nth(i).locator('.u-face').count() for i in range(legs.count())]
    assert faces == [1 if x in ('NBA', 'NFL') else 0 for x in sports] and sum(faces) >= 1, (sports, faces)   # esports legs unchanged
    tags = [t.strip() for t in legs.locator('.h-line-tag').all_text_contents()]
    assert all(t in ('Standard', 'Goblin', 'Demon', 'Research estimate') for t in tags) and sum(t != 'Research estimate' for t in tags) == legs.count(), tags
    p.locator('#hub-generated .h-generated-card').first.scroll_into_view_if_needed()
    shot(p, 'faces-auto-build')
    p.locator('#hub-generated [data-hub=use-generated]').first.click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=slip]').click()
    p.wait_for_selector('#hub-studio-body .u-entry.ready', timeout=20000)
    p.wait_for_function("document.querySelector('#hub-studio-sync').textContent.startsWith('Saved to your account')", timeout=20000)
    try:
        with p.expect_download(timeout=15000) as dl:
            p.locator('#hub-studio-section [data-hub=share-card]').click()
    except Exception:
        pytest.fail('no download: ' + p.evaluate("[...document.querySelectorAll('.u-toast')].map(t => t.textContent).join(' | ')"))
    assert dl.value.suggested_filename == '1Time-Scout-lineup.png'
    assert any(re.match(FACE, u) for u in asked) and any(re.match(LOGO, u) for u in asked), asked   # the image asked for each face
    assert not p.errors


def test_server_csp_allows_espn_images_and_rows_carry_the_team_abbreviation():
    import server
    img = [d for d in server.CSP_POLICY_DEFAULT.split(';') if d.strip().startswith('img-src')][0]
    assert 'https:' in img.split() or 'https://a.espncdn.com' in img.split()
    import workspace_sports
    from tests.workspace_other_fixture import nba_board
    doc, _view = nba_board()
    m = doc["markets"][0]
    rec, _ = workspace_sports.record_of("nba", doc, m, m["q"][0], {})
    assert rec["player"]["team_abbr"] == "NYK" and rec["player"]["id"] == "nba:player:espn:301"


def test_phone_sort_menu_chart_and_auto_build(ctx, env):
    """the 390px side of the desk-only checks above: the Sort menu on the count line, the card chart, Auto-build legs"""
    p = board(ctx, env, PHONE)
    assert p.locator('#hub-sort').is_visible() and p.locator('#hub-sort-dir').bounding_box()['height'] >= 40
    p.locator('#hub-sort').select_option('l10')
    got = [x for x in rates(p, 'l10') if x is not None]
    assert got == sorted(got, reverse=True)
    shot(p, 'a6-sort-l10')
    no_h_scroll(p)
    sport(p, 'nba')
    p.locator('#hub-q').fill('Karl')
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg .median-lab', state='attached', timeout=20000)
    p.locator('#hub-detail .h-history-chart').scroll_into_view_if_needed()
    shot(p, 'b6-chart-line-and-median')
    p.locator('#hub-detail [data-hub=card-close]').click()
    p.goto(env.url + '/#/slips?tab=build')
    p.wait_for_selector('#hub-generate')
    p.locator('#hub-generation-sports input[value=lol]').uncheck()
    p.locator('#hub-generation-count').select_option('3')
    p.locator('#hub-generation-slips').select_option('1')
    p.locator('#hub-generation-research').select_option('include')
    p.locator('#hub-generate').click()
    p.wait_for_selector('#hub-generated .h-generated-card li .u-face', timeout=30000)
    p.locator('#hub-generated').scroll_into_view_if_needed()
    shot(p, 'faces-auto-build')
    no_h_scroll(p)
    assert not p.errors
