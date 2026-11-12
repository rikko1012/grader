"""Ported to the redesign (html[data-ui=next], the default since 2026-11-11): the member functions and figures of the 2026-11-05 audit
suites that failed when their legacy tests ran on the new design (tests/test_audit_board_browser.py, test_audit_classic_browser.py,
test_audit_esports_1105_browser.py, test_lol_one_sided_browser.py). The legacy tests stay as they are and keep covering the rollback
design (UI_NEXT=0); here every context carries the member's scout_ui=next cookie. Real Handler + fixture data, no network.
Display only: no grade, side, chance, stake or projection is touched."""
import json
import re
import time

import pytest

import server
from tests.test_audit_board_browser import board, sport, flags, initials_fit, FACE, LOGO  # noqa: F401  (flags: autouse, same feature flags)
from tests.test_audit_esports_1105_browser import setup  # noqa: F401
from tests.test_hub_shell_browser import market_doc, mt_page
from tests.test_lol_one_sided_browser import one, hub_lol, harky  # noqa: F401
from tests.test_phase4_browser import lines_read, page, SHOWN_BOOKS
from tests.test_research_hub_browser import book
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, context, open_page, no_h_scroll, DESK, PHONE, OWNER  # noqa: F401

SIZES = [DESK, PHONE]


def nx(browser, env, size=DESK, user=OWNER):
    """A member on the redesign: their own cookie wins over the legacy fixture's UI_NEXT=0."""
    c = context(browser, env, size, user=user)
    c.add_cookies([{"name": server.UI_NEXT_COOKIE, "value": "next", "url": env.url}])
    return c


@pytest.fixture
def ctx(browser, env):
    c = nx(browser, env)
    yield c
    c.close()


def is_next(p):
    assert p.locator('html').get_attribute('data-ui') == 'next'


def open_ladders(p):
    """Open every '+N lines' ladder on screen (a repaint keeps open ladders open)."""
    for _ in range(40):
        b = p.locator('#hub-rows [data-hub=ladder][aria-expanded=false]')
        if not b.count():
            return
        b.first.click()
    raise AssertionError('ladders kept closing')


# ---------------------------------------------------------------------------------------------------------- B5: NBA / NFL rows


@pytest.mark.parametrize('size', SIZES, ids=['1440', '390'])
def test_sport_tag_stays_whole_under_the_edge_sort(ctx, env, size):
    """legacy: test_audit_board_browser.py::test_sport_tag_stays_whole_under_the_edge_sort. With 'Team vs Opp' under the name only the
    matchup text is cut; the sport tag keeps its full text inside its cell."""
    if size["width"] < 600:
        pytest.skip("phone simplification (owner 2026-11-12): the phone row no longer carries the sport tag; the card has it")
    p = board(ctx, env, size)
    is_next(p)
    assert p.locator('#hub-sort').input_value() == 'edge'
    bad = p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr[data-record] .h-player small')].map(s => {
      const i = s.querySelector(':scope > i'), td = s.closest('td'), v = s.querySelector(':scope > .h-vs');
      if (!i || getComputedStyle(i).display === 'none') return null;
      const a = i.getBoundingClientRect(), c = td.getBoundingClientRect();
      return (a.right <= c.right + 0.5 && i.scrollWidth <= i.clientWidth + 1 && i.textContent.trim() && a.width > 8 && v) ? null
        : {row: s.closest('tr').dataset.record, tag: i.textContent, right: a.right, cell: c.right, w: a.width, sw: i.scrollWidth, cw: i.clientWidth, v: !!v}; }).filter(Boolean)""")
    assert bad == [], bad
    shown = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record] .h-player small>i')].filter(i => getComputedStyle(i).display !== 'none').length")
    assert shown > 0
    assert p.locator('#hub-rows .h-player small .h-vs').first.text_content().count(' vs ') == 1
    no_h_scroll(p)
    assert not p.errors


@pytest.mark.parametrize('size', SIZES, ids=['1440', '390'])
def test_sports_rows_name_the_game_once_and_never_cut_the_letter(ctx, env, size):
    """legacy: ...::test_sports_rows_name_the_game_once_and_never_cut_research_grade (its 56-64px row height is the previous layout's)."""
    p = board(ctx, env, size)
    is_next(p)
    sport(p, 'nba')
    in_sheet(p, '#hub-sort').select_option('start')
    close_sheet(p)
    heads = p.locator('#hub-rows .h-game-head > span:not(.h-countdown)').all_text_contents() if size == DESK else []
    assert all(not t.startswith('NBA · NBA') for t in heads) and (size != DESK or (heads and all(t.startswith('NBA · ') for t in heads))), heads
    letters = p.locator('#hub-rows tr.h-row .h-grade')
    assert letters.count() >= 1
    assert 'research grade' not in p.locator('#hub-rows').inner_text().lower()
    for i in range(letters.count()):
        assert letters.nth(i).evaluate('e => e.scrollWidth <= e.clientWidth + 1 && getComputedStyle(e).textOverflow !== "ellipsis"')
    if size == DESK:   # one row height for every prop row with one line of text under the name (a letter or chip never grows a row)
        h = p.evaluate("[...document.querySelectorAll('#hub-rows tr.h-row')].map(r => Math.round(r.getBoundingClientRect().height))")
        assert h and max(h) <= 72, h
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-card-head')
    assert 'NBA · NBA' not in p.locator('#hub-detail .h-card-head').text_content()
    p.keyboard.press('Escape')
    p.locator('#hub-board-section [data-hub=view][data-id=games]').click()
    p.wait_for_selector('#hub-games-body .h-game-card')
    assert all(t.strip() == 'NBA' for t in p.locator('#hub-games-body .h-eyebrow').all_text_contents())
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B7: tabs, sticky bar, scroll


@pytest.mark.parametrize('size', SIZES, ids=['1440', '390'])
def test_sport_tabs_keep_their_width_toolbar_sticks_and_a_sport_change_scrolls_up(ctx, env, size):
    """legacy: ...::test_sport_tabs_keep_their_width_toolbar_sticks_and_a_sport_change_scrolls_up."""
    slow = {'on': True}

    def hold(route):
        if slow['on'] and 'sport=nfl' in route.request.url:
            time.sleep(2.5)
        route.continue_()
    ctx.route('**/api/upgrade/board*', hold)
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    is_next(p)
    before = p.evaluate("[...document.querySelectorAll('#hub-sports button')].map(b => Math.round(b.getBoundingClientRect().width))")
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && document.querySelector('#hub-sports [data-id=nfl] span:not(.h-n-wait)')", timeout=30000)
    after = p.evaluate("[...document.querySelectorAll('#hub-sports button')].map(b => Math.round(b.getBoundingClientRect().width))")
    assert before == after, (before, after)
    slow['on'] = False
    scroller = "(document.querySelector('#hub-center').scrollHeight > document.querySelector('#hub-center').clientHeight ? document.querySelector('#hub-center') : document.scrollingElement)"
    p.evaluate(f"{scroller}.scrollTop = 600")
    p.wait_for_timeout(200)
    if size['width'] < 600:                 # phones (2026-11-12): the header slides away on the way down and is back on the way up
        assert p.locator('#u-hub .h-toolbar.h-bar-away').count() == 1
        p.evaluate(f"{scroller}.scrollTop = 500")
        p.wait_for_timeout(300)
        assert p.locator('#u-hub .h-toolbar.h-bar-away').count() == 0
    bar = p.locator('#u-hub .h-toolbar').bounding_box()
    assert p.evaluate(f"{scroller}.scrollTop") > 100
    # the toolbar stays on screen with its search and filters reachable
    assert bar['y'] + bar['height'] > 40 and bar['y'] <= 2, bar
    for sel in ('#hub-q', '#hub-filters-toggle'):
        b = p.locator(sel).bounding_box()
        assert b and b['y'] >= -1 and b['y'] + b['height'] <= size['height'], (sel, b)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.wait_for_timeout(200)
    assert p.evaluate(f"{scroller}.scrollTop") == 0
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B3: PrizePicks line kinds


@pytest.mark.parametrize('size', SIZES, ids=['1440', '390'])
def test_every_prizepicks_row_ladder_line_leg_and_card_names_its_line_kind(ctx, env, size):
    """legacy: ...::test_every_prizepicks_row_leg_and_card_names_its_line_kind. The '+N props' toggle is gone: a prop's other lines are
    in its ladder, so the kind is checked on the row's own line and on every ladder line."""
    p = board(ctx, env, size)
    is_next(p)
    sport(p, 'cs2')
    p.locator('#hub-q').fill('vargo')
    p.wait_for_function("document.querySelectorAll('#hub-rows tr.h-player-row').length === 1")   # a search on one player lists their props under their name
    assert 'vargo' in p.locator('#hub-rows tr.h-player-row').text_content()
    open_ladders(p)
    rows = p.locator('#hub-rows tr.h-row[data-record]')
    seen = set()
    for i in range(rows.count()):
        r = rows.nth(i)
        k = r.get_attribute('data-record')
        kinds = [t.strip() for t in r.locator('td.h-c-line .u-lt').all_text_contents()]
        if '|prizepicks|' in k:
            assert len(kinds) == 1 and kinds[0] in ('Standard', 'Goblin', 'Demon'), (k, kinds)
            seen.add(kinds[0])
        else:
            assert kinds == [], (k, kinds)
    lines = p.locator('#hub-rows tr.h-ladder-row tr[data-line]')
    assert lines.count() >= 3
    for i in range(lines.count()):
        ln = lines.nth(i)
        k = ln.get_attribute('data-line')
        kinds = [t.strip() for t in ln.locator('th .u-lt').all_text_contents()]
        if '|prizepicks|' in k:
            assert len(kinds) == 1 and kinds[0] in ('Standard', 'Goblin', 'Demon'), (k, kinds)
            seen.add(kinds[0])
        else:
            assert kinds == [], (k, kinds)
    assert {'Goblin', 'Demon'} <= seen, seen                           # the fixture's vargo has a goblin and a demon line
    book(p, 'prizepicks')
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr.h-row[data-record]')].every(r => r.dataset.record.includes('|prizepicks|'))")
    p.locator('#hub-rows tr.h-row[data-record*="|prizepicks|"] .h-player').first.click()
    p.wait_for_selector('#hub-detail #hub-card-line')
    options = p.locator('#hub-detail #hub-card-line option').all_text_contents()
    assert any(o.startswith('PrizePicks') for o in options)
    assert all(re.search(r'· (Standard|Goblin|Demon)$', o) for o in options if o.startswith('PrizePicks')), options
    p.locator('#hub-detail [data-hub=card-tab][data-id=lines]').click()
    small = [t.strip() for t in p.locator('#hub-detail .h-line-shop td:first-child small').all_text_contents()]
    assert small and all(t in ('Standard', 'Goblin', 'Demon') for t in small), small
    p.keyboard.press('Escape')
    p.locator('#hub-rows tr.h-row .h-add:not([disabled]):not(.is-on):not(.h-add-info)').first.click()
    if p.locator('#hub-card').is_visible():
        p.locator('#hub-detail [data-hub=detail-add]:not([disabled])').click()
        p.locator('#hub-detail [data-hub=card-close]').click()
    p.goto(env.url + '/#/slips')
    p.wait_for_selector('#hub-studio-body .u-leg')
    assert re.search(r'(Standard|Goblin|Demon)', p.locator('#hub-studio-body .u-leg .u-lt').first.text_content())
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- B2: below break-even


@pytest.mark.parametrize('size', SIZES, ids=['1440', '390'])
def test_a_side_below_break_even_keeps_its_wording_and_says_so(ctx, env, monkeypatch, size):
    """legacy: ...::test_a_side_below_break_even_keeps_its_wording_and_says_so (the direction label is upper-cased by CSS on the new
    design, so the row's own text is read)."""
    from gradecore import payouts
    real = payouts.break_even
    monkeypatch.setattr(payouts, "break_even", lambda b, t, n: 0.60 if (b, t, n) == ("underdog", "standard", 2) else real(b, t, n))
    p = board(ctx, env, size)
    is_next(p)
    rows = p.locator('#hub-rows tr.h-row[data-record]')
    below = 0
    for i in range(rows.count()):
        r = rows.nth(i)
        edge = r.locator('td.h-c-edge .h-edge').text_content().strip() if r.locator('td.h-c-edge .h-edge').count() else '—'
        chip = r.locator('.h-below:visible').count()      # the Edge cell's chip, or the Scout cell's where the Edge column is hidden
        scout = r.locator('td.h-c-scout').inner_text()
        if edge.startswith('−'):
            below += 1
            assert chip == 1 and r.locator('.h-below:visible').text_content().replace('‑', '-') == 'Below break-even', (edge, scout)
            assert r.locator('.h-below:visible').evaluate('e => { const b = e.getBoundingClientRect(), t = e.closest("tr").getBoundingClientRect(); return b.width > 20 && b.height > 8 && b.left >= t.left && b.right <= t.right + 0.5; }')
            assert (r.locator('td.h-c-edge').is_visible()) == (r.locator('td.h-c-edge .h-below:visible').count() == 1)
            assert re.search(r'(?i)(Over|Under)\s+\d+\.\d%', scout), scout      # the side and its chance stay on the row
            assert re.search(r'(?im)^[ABC][+-]?$|Lean (Over|Under)|Research', scout), scout   # and its letter or Lean wording
            assert 'no bet' not in scout.lower()
            assert 'break-even' in r.locator('.h-below:visible').get_attribute('title')
        elif edge.startswith('+'):
            assert chip == 0, (edge, scout)
    assert below >= 1
    no_h_scroll(p)
    if size == DESK:      # a card open beside the board hides the Edge column: the chip stays on the row, once
        r = p.locator('#hub-rows tr.h-row[data-record]', has=p.locator('.h-below')).first
        r.locator('.h-player').click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-card-head')
        assert not r.locator('td.h-c-edge').is_visible() and r.locator('.h-below:visible').count() == 1
        assert r.locator('.h-below:visible').evaluate('e => !!e.closest("td.h-c-scout")')
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- A14: phone hit chips


def test_phone_hit_chips_are_readable(ctx, env):
    """legacy: ...::test_phone_hit_chips_are_larger (its 44px label + value box is the previous layout's; the windows must still be
    readable on the phone: each rate at least 12px type in a chip at least 32px wide that is not clipped)."""
    if True:
        pytest.skip("phone simplification (owner 2026-11-12): the phone row no longer carries the L5-SZN windows; the card has it")
    p = board(ctx, env, PHONE)
    is_next(p)
    sizes = p.evaluate("""[...document.querySelectorAll('#hub-rows tr.h-row[data-record]:not(.h-nohits) .h-strip .h-hit')].slice(0, 20).map(e => {
      const r = e.getBoundingClientRect(), c = e.closest('.h-strip-cell').getBoundingClientRect();
      return [Math.round(r.width), Math.round(r.height), parseFloat(getComputedStyle(e).fontSize), e.scrollWidth <= e.clientWidth + 1, r.right <= innerWidth && r.left >= 0, Math.round(c.height)]; })""")
    assert sizes and all(w >= 32 and h >= 16 and f >= 12 and fit and on for w, h, f, fit, on, _ in sizes), sizes
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- headshots and logos


WIDE = {'width': 1600, 'height': 900}


@pytest.mark.parametrize('size', [WIDE, DESK, PHONE], ids=['1600', '1440', '390'])
def test_nba_nfl_faces_on_rows_card_slip_and_picks_esports_unchanged(ctx, env, size):
    """legacy: ...::test_nba_nfl_faces_on_rows_card_slip_and_picks_esports_unchanged. On the redesign a desktop under 1500px gives the
    row's face to the name (the face stays on the card, the slip and My picks); at 1500px and up and on the phone the row shows it."""
    p = board(ctx, env, size)
    is_next(p)
    sport(p, 'cs2')
    assert p.locator('#hub-rows .u-face').count() == 0                       # esports rows unchanged
    sport(p, 'nba')
    rows = p.locator('#hub-rows tr.h-row[data-record]')
    assert p.locator('#hub-rows tr.h-row .u-face').count() == rows.count()
    face = p.locator('#hub-rows tr.h-row .u-face').first
    img = face.locator('img.u-face-shot')
    assert re.match(FACE, img.get_attribute('src')) and img.get_attribute('loading') == 'lazy'
    name = rows.first.locator('.h-player b').first.text_content().strip()
    assert img.get_attribute('alt') == f'{name} headshot'
    logo = face.locator('img.u-face-logo')
    assert re.match(LOGO, logo.get_attribute('src')) and re.fullmatch(r'[A-Z]{2,4} logo', logo.get_attribute('alt'))
    if size == DESK:
        assert face.is_visible() and rows.first.locator('.h-player b').first.evaluate('e => e.scrollWidth <= e.clientWidth + 1')   # owner 2026-11-12: photos on the rows at every width; the name is whole
    else:
        p.wait_for_function("document.querySelector('#hub-rows tr.h-row .u-face').classList.contains('u-face-fb')")   # blocked image -> initials
        assert face.locator('i').text_content() == ''.join(w[0] for w in name.split()[:2]).upper()
        box = face.bounding_box()
        assert round(box['width']) == round(box['height']) and 24 <= round(box['width']) <= 40, box
    sport(p, 'nfl')
    assert re.match(FACE.replace('(nba|nfl)', 'nfl'), p.locator('#hub-rows .u-face img.u-face-shot').first.get_attribute('src'))
    rows.first.locator('.h-player').click()
    p.wait_for_selector('#hub-detail .h-card-head .u-face')
    b = p.locator('#hub-detail .h-card-head .u-face').bounding_box()
    assert round(b['width']) == round(b['height']) and b['width'] >= 40, b
    p.keyboard.press('Escape')
    sport(p, 'nba')
    book(p, 'underdog')
    for who in ('Karl', 'Reed'):
        p.locator('#hub-q').fill(who)
        p.wait_for_function("(w) => { const h = [...document.querySelectorAll('#hub-rows tr.h-player-row')]; return h.length === 1 && h[0].textContent.includes(w); }", arg=who)
        p.locator('#hub-rows tr.h-row .h-add:not([disabled]):not(.is-on)').first.click()
        if p.locator('#hub-card').is_visible():
            p.locator('#hub-detail [data-hub=detail-add]:not([disabled])').click()
            p.locator('#hub-detail [data-hub=card-close]').click()
    p.locator('#hub-q').fill('')
    if size != PHONE:
        legs = p.locator('#hub-slip-content .u-leg')
        assert legs.count() == 2 and p.locator('#hub-slip-content .u-leg .u-face').count() == 2
        initials_fit(p, '#hub-slip-content .u-leg .u-face', ['KA', 'RL'])
    p.goto(env.url + '/#/slips')
    p.wait_for_selector('#hub-studio-body .u-leg .u-face')
    assert p.locator('#hub-studio-body .u-leg .u-face').count() == 2
    p.wait_for_function("[...document.querySelectorAll('#hub-studio-body .u-leg .u-face')].every(f => f.classList.contains('u-face-fb'))")
    initials_fit(p, '#hub-studio-body .u-leg .u-face', ['KA', 'RL'])
    assert re.match(FACE, p.locator('#hub-studio-body .u-leg .u-face img').first.get_attribute('src'))
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])', timeout=20000)
    p.locator('#hub-studio-body [data-u=track]').click()
    p.wait_for_selector('#hub-studio-body .u-note.good', timeout=20000)
    p.goto(env.url + '/#/picks')
    p.wait_for_selector('#hub-picks-body .u-pick')
    assert p.locator('#hub-picks-body .u-pick .u-face').count() == p.locator('#hub-picks-body .u-pick').count() == 2
    p.goto(env.url + '/#/slips?tab=tracked')
    p.wait_for_selector('#hub-slip-history .u-pick .u-face')
    no_h_scroll(p)
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------- Tools: owner-only parts


@pytest.mark.parametrize("size", [PHONE, DESK], ids=['390', '1440'])
def test_owner_tools_are_invisible_to_a_member(browser, env, monkeypatch, size):
    """legacy: test_audit_classic_browser.py::test_owner_tools_are_invisible_to_a_member."""
    import workspace_markets
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    member = nx(browser, env, size, user="member-2")
    owner = nx(browser, env, size)
    try:
        p = member.new_page()
        p.goto(env.url + "/")
        me = p.evaluate("async () => (await (await fetch('/api/upgrade/me')).json())")
        assert me["owner"] is False
        cut = workspace_markets.for_member(json.dumps(market_doc(owner=True)), {"cs2": True, "lol": True, "nba": True, "nfl": True}, owner=False)
        assert cut["owner"] is False and not any(d.get("flagged") for d in cut["sports"].values())
        p.close()
        for path in ('/#/tools', '/#/ev', '/#/arb'):                                   # Tools, and the redesign's EV and Arbitrage screens
            m = mt_page(member, env, cut, path=path)
            is_next(m)
            m.wait_for_selector("#hub-market-results", state='attached')
            text = m.locator("#u-hub").text_content().lower()
            assert "owner check" not in text and "grading comparison" not in text and "owner review" not in text and "formulas (owner)" not in text, path
            assert m.locator('[data-hub="grade-review"]').count() == 0 and m.locator(".h-mt-owner").count() == 0
            assert not m.errors
            m.close()
        o = mt_page(owner, env, market_doc(owner=True))
        is_next(o)
        o.wait_for_selector("#hub-market-results")
        assert o.locator('#hub-tools-body [data-hub="grade-review"]').count() == 4 and o.locator('#hub-tools-body [data-hub="grade-review"]').first.is_visible()
        assert "grading comparison" in o.locator("#hub-tools-body").inner_text().lower()
        shown = [path for path in ('/#/tools', '/#/ev', '/#/arb') if owner_check_visible(owner, env, path)]
        assert shown, 'the owner check is on no screen'                                 # the owner keeps it
        assert not o.errors
    finally:
        member.close()
        owner.close()


def owner_check_visible(owner, env, path):
    o = mt_page(owner, env, market_doc(owner=True), path=path)
    try:
        o.wait_for_selector("#hub-market-results", state='attached')
        o.wait_for_timeout(300)
        d = o.locator('.h-mt-owner')
        return d.count() >= 1 and d.first.is_visible() and 'owner check' in d.first.locator(':scope > summary').text_content().lower()
    finally:
        o.close()


# ---------------------------------------------------------------------------------------------------------- A2 / B4: another book's line


@pytest.mark.parametrize('size', SIZES, ids=['1440', '390'])
def test_sleeper_line_has_chance_hits_and_the_full_card(browser, setup, size):
    """legacy: test_audit_esports_1105_browser.py::test_sleeper_line_has_chance_hits_and_the_full_card."""
    c = nx(browser, setup, size)
    try:
        p = page(c, setup)
        lines_read(p, '/')
        is_next(p)
        p.wait_for_function(f"{SHOWN_BOOKS}.includes('sleeper')", timeout=20000)
        in_sheet(p, '#hub-platform [data-hub="book"][data-id="sleeper"]').click()
        close_sheet(p)
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr.h-row[data-record]')].length===2", timeout=20000)
        v = p.locator('#hub-rows tr.h-row[data-record]', has_text='vargo').first
        b = p.locator('#hub-rows tr.h-row[data-record]', has_text='brandr').first
        assert '|sleeper|' in v.get_attribute('data-record') and '|sleeper|' in b.get_attribute('data-record')   # the row shows the filtered app's line
        p.wait_for_function("""()=>{const r=[...document.querySelectorAll('#hub-rows tr.h-row[data-record]')].find(t=>t.innerText.includes('vargo'));
                                    return r&&!r.querySelector('.h-s-l5 .h-hit.empty')&&!r.querySelector('.h-s-l5 .h-hit-wait');}""", timeout=20000)
        vt = v.text_content()
        assert 'Chance at this line' in vt and 'Lean' not in vt                # Scout's chance at 37.5, no letter
        assert re.search(r'(?i)over\s*\d+\.\d%', v.locator('.h-c-scout').inner_text())
        lab = v.locator('.h-line-chance')
        assert lab.is_visible() and lab.evaluate('e => e.scrollWidth <= e.clientWidth + 1 && getComputedStyle(e).textOverflow !== "ellipsis"')
        assert lab.get_attribute('title') == 'Scout’s chance at this line'
        assert p.locator('#hub-sort').input_value() == 'start' and p.locator('#hub-sort-note').inner_text().strip() == ''
        assert v.locator('.h-s-l5 .h-hit').inner_text() != '—' and v.locator('.h-s-l20 .h-hit').inner_text() != '—'
        assert b.locator('.h-s-l5 .h-hit').inner_text() != '—'
        no_h_scroll(p)
        v.locator('.h-c-add .h-add').click()                                    # Info -> the full research card
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
        d = p.locator('#hub-detail')
        assert 'Sleeper · 37.5' in d.text_content() and 'chance at this line' in d.text_content().lower()
        assert p.locator('#hub-detail [role="tab"]').count() >= 4 and d.locator('.h-history-chart').count() == 1
        assert 'view only' in p.locator('#hub-decision').inner_text().lower() and p.locator('#hub-decision [data-hub="detail-add"]').count() == 0
        no_h_scroll(p)
        p.locator('#hub-detail [data-hub="card-tab"][data-id="lines"]').click()
        assert 'Sleeper' in d.text_content() and 'Underdog' in d.text_content()
        p.locator('#hub-detail [data-hub="card-tab"][data-id="stats"]').click()
        p.locator('#hub-detail [data-hub="card-tab"][data-id="ask"]').click()
        assert d.locator('form, details, p').count() > 0
        assert p.locator('#hub-slip-count').text_content().strip() == '0'
        assert not p.errors, p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------- os-1: PrizePicks goblin


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_prizepicks_goblin_keeps_its_letter_and_add(ctx, one, size):
    """legacy: test_lol_one_sided_browser.py::test_prizepicks_goblin_keeps_its_letter_and_add (the kind chip is upper-cased by CSS)."""
    p = hub_lol(ctx, one, size, "prizepicks")
    is_next(p)
    r = p.locator("#hub-rows tr.h-row[data-record]", has_text="Harky").first
    assert '|prizepicks|' in r.get_attribute('data-record')
    scout = r.locator(".h-c-scout").inner_text()
    assert "A-" in scout and re.search(r'\d+\.\d%', scout) and "over only" not in scout.lower(), scout
    kind = r.locator(".h-c-line .u-lt")
    assert kind.count() == 1 and kind.text_content().strip() == "Goblin" and kind.is_visible()
    add = r.locator(".h-c-add button")
    assert add.text_content().strip() != "Info" and add.is_enabled()
    add.click()                                                     # a one-option goblin still goes on the slip (or asks for the side)
    if p.locator('#hub-card').is_visible():
        p.locator('#hub-detail [data-hub=detail-add]:not([disabled])').click()
    p.wait_for_function("document.querySelector('#hub-slip-count').textContent.trim()==='1'")
    no_h_scroll(p)
    assert not p.errors, p.errors


# ---------------------------------------------------------------------------------------------------------- phone row grid (real bug)


@pytest.mark.parametrize('width', [360, 390, 414])
def test_phone_row_keeps_its_own_grid_with_the_edge_and_h2h_columns_on(ctx, env, width):
    """Found while porting test_sport_tag_stays_whole_under_the_edge_sort[390]: with SCOUT_BOARD_H2H on (production) the previous
    design's five-column phone grid outranked the redesign's, so the line sat on Scout's side, names were cut to two letters and the
    Lines button ran off the row. Every cell of a phone row is inside the row and none covers another; By player too."""
    p = board(ctx, env, {'width': width, 'height': 844})
    is_next(p)
    assert p.locator('#u-hub.h-cols').count() == 1 and p.locator('#hub-table.h-has-edge').count() == 1     # the columns that broke it
    check = """() => [...document.querySelectorAll('#hub-rows tr.h-row[data-record]')].map(tr => {
      const t = tr.getBoundingClientRect(), cells = [...tr.children].filter(c => getComputedStyle(c).display !== 'none' && c.getBoundingClientRect().width > 0)
        .map(c => [c.className, c.getBoundingClientRect()]);
      const out = cells.filter(([, b]) => b.left < t.left - 0.5 || b.right > t.right + 0.5 || b.top < t.top - 0.5 || b.bottom > t.bottom + 0.5).map(x => 'outside ' + x[0]);
      for (let i = 0; i < cells.length; i++) for (let j = i + 1; j < cells.length; j++) { const a = cells[i][1], b = cells[j][1];
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) out.push(cells[i][0] + ' over ' + cells[j][0]); }
      const btn = tr.querySelector('.h-lines-btn, .h-lines-one'), add = tr.querySelector('.h-c-add button');
      for (const e of [btn, add]) if (e && e.getBoundingClientRect().right > innerWidth + 0.5) out.push('off screen ' + e.className);
      const pl = tr.querySelector('.h-c-player');
      if (pl.getBoundingClientRect().width < t.width * (innerWidth < 600 ? 0.3 : 0.5)) out.push('player cell ' + Math.round(pl.getBoundingClientRect().width) + 'px');
      return out.length ? [tr.dataset.record, out] : null; }).filter(Boolean)"""
    assert p.locator('#hub-rows tr.h-row[data-record]').count() >= 10
    assert p.evaluate(check) == []
    p.locator('#hub-board-section [data-hub=group-by][data-id=player]').click()
    p.wait_for_selector('#hub-table.h-by-player tr.h-player-row')
    assert p.evaluate(check.replace("if (pl.getBoundingClientRect().width < t.width * (innerWidth < 600 ? 0.3 : 0.5)) out.push('player cell ' + Math.round(pl.getBoundingClientRect().width) + 'px');", '')) == []
    no_h_scroll(p)
    assert not p.errors



# ---------------------------------------------------------------------------------------------------------- legacy bodies, unchanged
# In the listed run (2026-11-12 01:29-02:00, started before c0c5b06 was committed) these legacy tests could not press a desktop row's
# name. That did not happen again on c0c5b06: their bodies pass on the redesign as written, with and without this branch's fixes
# (tests/UI_NEXT_TRIAGE.md). They run here unchanged on a member's redesign context, so the redesign keeps what they check.

import tests.test_audit_board_browser as ab
import tests.test_audit_member_browser as am
from tests.test_audit_member_browser import flags as member_flags  # noqa: E402,F401  (fixture)


def test_chart_dashed_line_is_the_offered_line_and_the_median_is_separate(ctx, env):
    """legacy: test_audit_board_browser.py::test_chart_dashed_line_is_the_offered_line_and_the_median_is_separate."""
    ab.test_chart_dashed_line_is_the_offered_line_and_the_median_is_separate(ctx, env)
    is_next(ctx.pages[0])


@pytest.mark.parametrize("prod", [False, True], ids=["test-flags", "production-flags"])
@pytest.mark.parametrize("who", [OWNER, am.MEMBER])
def test_audit_surfaces_as_owner_and_member_on_the_desktop(browser, env, member_flags, monkeypatch, who, prod):
    """legacy: test_audit_member_browser.py::test_audit_surfaces_as_owner_and_member[*-size1-*] (1440px; the phone runs passed)."""
    seen = []

    def board_next(*a, **k):
        p = ab.board(*a, **k)
        is_next(p)
        seen.append(p)
        return p
    monkeypatch.setattr(am, "context", nx)
    monkeypatch.setattr(am, "board", board_next)
    am.test_audit_surfaces_as_owner_and_member(browser, env, DESK, who, prod, monkeypatch)
    assert seen
