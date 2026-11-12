"""Phone hit-rate chips (owner 2026-11-06: "fix ui on phone the L5/10/20 h2h look clumped together"), in a real browser against the
real Handler and fixture data (no network). With the live flags on (server.DEFAULT_ON: H2H + SZN columns, "6/10" hit samples), every
board row at 360 / 390 / 414 shows its five windows as one labelled mini-grid on its own line of the card: equal widths, a clear gap,
the label above the value, the value on one line at a readable size. Same on CS2, LoL, NBA, NFL and on every book filter (Sleeper and
Dabble included). Desktop keeps its one-line row. Display only: no grade, chance, stake, letter or count moves.
PHONE_SHOTS=<folder> keeps a screenshot of every case."""
import os
import time
from pathlib import Path

import pytest

import book_providers as bp
import server
import workspace_books as wb
from tests.test_audit_board_browser import sport
from tests.test_phase4_browser import offer, lines_read, page, SHOWN_BOOKS
from tests.test_pickem_readers import event_of
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, context, DESK, no_h_scroll  # noqa: F401

SHOTS = Path(os.environ['PHONE_SHOTS']) if os.environ.get('PHONE_SHOTS') else None
PHONES = [{"width": w, "height": 844} for w in (360, 390, 414)]
WINDOWS = ['L5', 'L10', 'L20', 'H2H', 'SZN']

# the five windows on every row: one box per window, its label and its value (the on-screen text, not the tooltip)
GEOM = """() => [...document.querySelectorAll('#hub-rows tr[data-record]')].filter(r => r.offsetParent).map(r => {
  const strip = r.querySelector('td.h-c-hits .h-strip'), td = r.querySelector('td.h-c-hits');
  const cells = [...strip.querySelectorAll('.h-strip-cell')].map(c => {
    const b = c.getBoundingClientRect(), v = c.querySelector('.h-hit'), vb = v.getBoundingClientRect(), cs = getComputedStyle(v);
    const lab = getComputedStyle(c, '::before');
    const rng = document.createRange(); rng.selectNodeContents(v);
    const rs = [...rng.getClientRects()].filter(x => x.width > 0), lines = rs.length && Math.max(...rs.map(x => x.bottom)) - Math.min(...rs.map(x => x.top)) > 1.6 * parseFloat(cs.fontSize) ? 2 : 1;
    return {label: c.dataset.label, labelShown: lab.content.replace(/"/g, ''), labelPx: parseFloat(lab.fontSize), left: b.left, right: b.right,
            top: b.top, w: b.width, h: b.height, vTop: vb.top, vH: vb.height, vW: vb.width, fs: parseFloat(cs.fontSize), ls: parseFloat(cs.letterSpacing) || 0,
            text: v.textContent.trim(), lines, clip: v.scrollWidth > v.clientWidth + 1};
  });
  const rb = r.getBoundingClientRect(), tb = td.getBoundingClientRect();
  return {key: r.dataset.record, rowW: rb.width, tdW: tb.width, tdLeft: tb.left - rb.left, cells, nohits: r.classList.contains('h-nohits'),
          tdHidden: getComputedStyle(td).display === 'none', side: !!r.querySelector('td.h-c-scout .h-direction')};
})"""


@pytest.fixture
def live(env, monkeypatch):
    """the flags production runs with (server.DEFAULT_ON), the extra books fed from fixtures, never from a network"""
    for k in server.DEFAULT_ON:
        monkeypatch.setenv(k, '1')
    for k in ('SCOUT_BOOK_FANDUEL', 'SCOUT_BOOK_BETRIVERS'):
        monkeypatch.delenv(k, raising=False)
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, 'start', lambda d: False)
    bp.state('sleeper').update(at=time.time(), offers=[offer('vargo', 'Halcyon', 38.5), offer('brandr', 'Norrland', 33.5, sid='2')])
    dabble = [dict(offer('vargo', 'Halcyon', 38.5), provider='dabble', book='dabble', raw_stat='total-kills-map-one-and-map-two', source_at=None,
                   over={"offered": True, "payout": None, "price": 1.91}, under={"offered": True, "payout": None, "price": 2.1}),
              dict(offer('brandr', 'Norrland', 33.5, sid='2'), provider='dabble', book='dabble', raw_stat='total-kills-map-one-and-map-two',
                   source_at=None, over={"offered": True, "payout": None, "price": 2.05}, under={"offered": True, "payout": None, "price": 1.95})]
    real = wb.records

    def with_events(srv, sport_):
        recs, s = real(srv, sport_)
        for o in dabble:
            rec = next((r for r in recs or () if r['player']['name'] == o['player']), None)
            if rec:
                o.update(event_of(rec))
        return recs, s
    monkeypatch.setattr(wb, 'records', with_events)
    bp.state('dabble').update(at=time.time(), offers=dabble)
    yield env
    bp.reset(); wb.reset()


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.evaluate("document.querySelector('#hub-rows tr[data-record]').scrollIntoView({block: 'start'}); window.scrollBy(0, -60)")
        p.wait_for_timeout(200)
        p.screenshot(path=str(SHOTS / f"{name}_{p.viewport_size['width']}.png"))


def open_board(browser, env, size):
    c = context(browser, env, size)
    p = page(c, env)
    p.ctx = c
    lines_read(p, '/#all-sports')
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')", timeout=30000)
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    return p


def pick_book(p, b):
    p.wait_for_function(f"{SHOWN_BOOKS}.includes('{b}')", timeout=20000)
    in_sheet(p, f'#hub-platform [data-hub="book"][data-id="{b}"]').click(); close_sheet(p)
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length>0", timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)


def check_phone_grid(p, where):
    rows = p.evaluate(GEOM)
    assert rows, where
    for r in rows:
        cs = r['cells']
        if r['nohits']:          # card-and-chips 2026-11-06: no result in any window = no strip line (not five dashes)
            assert r['tdHidden'] and all(c['text'] == '—' for c in cs), (where, r['key'])
            continue
        # a row with no side shows Over-Under counts ("3-2"): its labels say so ("L5 O-U")
        want = [w + ('' if r['side'] else ' O-U') for w in WINDOWS]
        assert [c['label'] for c in cs] == want, (where, r['key'])
        assert [c['labelShown'] for c in cs] == want, (where, r['key'])                        # the label is on screen
        # the strip owns its own line of the card: (almost) the full card width, not a squeeze beside Proj / Compare
        assert r['tdW'] >= r['rowW'] - 32, (where, r['key'], r['tdW'], r['rowW'])
        widths = [c['w'] for c in cs]
        assert max(widths) - min(widths) <= 1.5, (where, r['key'], widths)                     # fixed equal widths
        assert min(widths) >= 50, (where, r['key'], widths)
        gaps = [b['left'] - a['right'] for a, b in zip(cs, cs[1:])]
        assert min(gaps) >= 6, (where, r['key'], gaps)                                         # a clear gap between windows
        assert len({round(c['top']) for c in cs}) == 1, (where, r['key'])                      # one line of five, never wrapped
        for c in cs:
            assert c['lines'] == 1 and not c['clip'], (where, r['key'], c)                     # "10/20" never "10/2 0", never cut
            assert c['fs'] >= 13 and c['ls'] >= -0.3, (where, r['key'], c)                     # readable, not squeezed letters
            assert c['labelPx'] >= 10.5, (where, r['key'], c)
            assert c['h'] >= 44 and c['vTop'] > c['top'] + 10, (where, r['key'], c)              # label above value, thumb-sized box
    return rows


@pytest.mark.parametrize('size', PHONES, ids=lambda s: str(s['width']))
def test_phone_rows_show_five_separated_windows_in_every_sport_and_book(browser, live, size):
    p = open_board(browser, live, size)
    try:
        check_phone_grid(p, 'all')
        no_h_scroll(p)
        shot(p, 'board_all')
        for s in ('cs2', 'lol', 'nba', 'nfl'):
            sport(p, s)
            p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
            check_phone_grid(p, s)
            no_h_scroll(p)
            shot(p, f'board_{s}')
        sport(p, 'cs2')
        for b in ('prizepicks', 'underdog', 'sleeper', 'dabble'):
            pick_book(p, b)
            check_phone_grid(p, b)
            no_h_scroll(p)
            shot(p, f'board_{b}')
        assert not p.errors
    finally:
        p.ctx.close()


def test_phone_values_read_the_same_as_their_tooltip_counts(browser, live):
    """the on-screen value is the sample ("4/5"), its exact counts stay in the tooltip / screen-reader label; nothing changes value"""
    p = open_board(browser, live, PHONES[1])
    try:
        sport(p, 'cs2')
        got = p.evaluate("""[...document.querySelectorAll('#hub-rows tr[data-record] .h-strip .h-hit:not(.empty)')].map(v =>
          ({t: v.textContent.trim(), a: v.getAttribute('aria-label') || ''}))""")
        assert got
        for g in got:
            if '/' in g['t']:
                k, n = g['t'].split('/')
                assert f"{k} " in g['a'] and f"last {n}" in g['a'] or f"of {n}" in g['a'], g
        assert not p.errors
    finally:
        p.ctx.close()


def test_desktop_row_keeps_its_one_line_strip(browser, live):
    p = open_board(browser, live, DESK)
    try:
        def compact(where):
            h = p.evaluate("[...document.querySelectorAll('#hub-rows tr[data-record]')].map(r => Math.round(r.getBoundingClientRect().height))")
            assert h and all(h_ <= 72 for h_ in h), (where, h)                                  # the compact desktop row
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-rows .h-strip-cell'), '::before').content") in ('none', 'normal')
            assert p.locator('#hub-table thead .h-strip-head .h-th-sort').count() == 5
        for s in ('cs2', 'lol', 'nba', 'nfl'):
            sport(p, s)
            p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
            compact(s)
            shot(p, f'board_{s}')
        sport(p, 'cs2')
        for b in ('sleeper', 'dabble'):
            pick_book(p, b)
            compact(b)
            shot(p, f'board_{b}')
        assert not p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize('size', PHONES, ids=lambda s: str(s['width']))
def test_phone_card_hit_tiles_are_one_even_grid(browser, live, size):
    """the player card's L5 / L10 / L20 / H2H / Season tiles: one even grid, numbers on one line, nothing cut"""
    p = open_board(browser, live, size)
    try:
        sport(p, 'cs2')
        p.locator('#hub-rows tr[data-record] .h-player').first.click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-history-summary .h-tile')
        p.wait_for_function("!document.querySelector('#hub-detail .h-history-summary .h-hit-wait')", timeout=20000)
        tiles = p.evaluate("""[...document.querySelectorAll('#hub-detail .h-history-summary .h-tile')].map(t => {
          const b = t.getBoundingClientRect(), v = t.querySelector('b'), r = document.createRange(); r.selectNodeContents(v);
          return {w: b.width, top: b.top, left: b.left, right: b.right, label: t.querySelector('span').textContent,
                  lines: (rs => rs.length && Math.max(...rs.map(x => x.bottom)) - Math.min(...rs.map(x => x.top)) > 1.6 * parseFloat(getComputedStyle(v).fontSize) ? 2 : 1)([...r.getClientRects()].filter(x => x.width > 0)),
                  clip: t.scrollWidth > t.clientWidth + 1};
        })""")
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            p.locator('#hub-detail .h-history-summary').scroll_into_view_if_needed()
            p.wait_for_timeout(200)
            p.screenshot(path=str(SHOTS / f"card_cs2_{size['width']}.png"))
        hit = [t for t in tiles if t['label'][:2] in ('L5', 'L1', 'L2', 'H2', 'Se')]
        assert len(hit) >= 3, tiles
        assert max(t['w'] for t in hit) - min(t['w'] for t in hit) <= 1.5, hit
        assert all(t['lines'] == 1 and not t['clip'] for t in tiles), [(t['label'], t['lines'], t['clip'], t['w']) for t in tiles]
        row1 = [t for t in hit if abs(t['top'] - hit[0]['top']) < 2]
        assert len(row1) == 3, hit                                                              # L5 L10 L20 side by side
        no_h_scroll(p)
        assert not p.errors

    finally:
        p.ctx.close()
