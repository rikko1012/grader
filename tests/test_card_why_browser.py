"""card-why (owner 2026-11-06) in a real browser at 1440 and 390, real Handler + fixture data (no network):
- every sport's player card opens with "Why Scout leans Over / Under": 1-6 plain facts with their numbers, the forecast first and
  marked as used in the grade, the rest marked context only; "What could go wrong" only from definite facts; the rest of the card
  (hit tiles, chart, H2H, Lines, Stats, Ask) is still there, after it;
- phone: the L5 / L10 / L20 / H2H / SZN chips get a full-width row of their own, evenly spaced (they were squeezed beside Proj/Edge).
AUDIT_SHOTS=<folder> keeps a 390px and a 1440px screenshot of every card."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

FLAGS = ("SCOUT_WORKSPACE_V2", "SCOUT_BOARD_H2H", "SCOUT_GENIE", "SCOUT_CLOSING_LINES", "SCOUT_LINE_HISTORY")
SHOTS = Path(os.environ.get("AUDIT_SHOTS") or "")


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


def board(ctx, env, size):
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    return p


def sport(p, s):
    p.locator(f'#hub-sports [data-id={s}]').click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{s}|'))")


def open_first_card(p):
    p.locator('#hub-rows tr[data-record] .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-why', timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-detail .h-detail-loading') || document.querySelector('#hub-detail .h-history-summary')", timeout=20000)
    p.wait_for_timeout(300)


def why(p):
    return p.evaluate("""() => { const w = document.querySelector('#hub-detail .h-why');
      const items = l => [...w.querySelectorAll(l + ' > li')].map(li => ({text: li.querySelector(':scope > span:last-child').innerText.trim(), sr: li.querySelector('.u-sr')?.textContent || '', used: !!li.querySelector('.h-why-dot.is-used'),
        dot: !!li.querySelector('.h-why-dot'), cls: li.className}));
      const body = document.querySelector('#hub-card-body');
      return {title: w.querySelector('h4').innerText, why: items('.h-why-list:not(.h-why-risk)'), risk: w.querySelector('.h-why-risk') ? items('.h-why-risk') : [],
              first: body.firstElementChild === w || body.firstElementChild.nextElementSibling === w && body.firstElementChild.classList.contains('h-warning'),
              key: w.querySelector('.h-why-key').innerText, after: [...body.children].map(e => e.className).join(' ')}; }""")


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
@pytest.mark.parametrize('sp', ['cs2', 'lol', 'nba', 'nfl'])
def test_every_sport_card_opens_with_why_scout_leans(ctx, env, sp, size):
    p = board(ctx, env, size)
    sport(p, sp)
    open_first_card(p)
    w = why(p)
    assert w['first'], w['after']                                    # the reason comes first on the card
    assert w['title'].startswith(('Why Scout leans Over', 'Why Scout leans Under', 'The numbers for')), w['title']
    assert 1 <= len(w['why']) <= 6 and len(w['risk']) <= 4, w
    assert all(x['dot'] for x in w['why'] + w['risk'])               # every fact says whether it feeds the grade
    assert w['why'][0]['used'] and ('Scout projects' in w['why'][0]['text'] or 'Scout gives' in w['why'][0]['text']), w['why'][0]
    ctx_only = [x for x in w['why'] + w['risk'] if not x['used']]
    assert all(not t['text'].startswith('Scout projects') for t in ctx_only)
    assert 'Used in the grade' in w['key'] and 'Context only' in w['key']
    text = ' '.join(x['text'] for x in w['why'] + w['risk']).lower()
    for word in ('unconfirmed', 'uncertain', 'questionable', 'day_to_day', 'nan', 'undefined', 'null'):
        assert word not in text.split() and word not in text.replace('.', ' ').split(), (word, text)
    # the rest of the card is still there, after the reason
    for cls in ('h-history-summary', 'h-history-panel'):
        assert cls in w['after'], w['after']
    for tab in ('lines', 'stats', 'ask'):
        assert p.locator(f'#hub-detail [data-hub=card-tab][data-id={tab}]').count() == 1
    no_h_scroll(p)
    shot(p, f'why-{sp}-card')
    assert not p.errors, p.errors


def test_hit_rates_in_why_match_the_tiles(ctx, env):
    """the "went Over in k of the last n" fact is the card's own L10 tile, not a second count"""
    p = board(ctx, env, DESK)
    sport(p, 'nba')
    p.locator('#hub-q').fill('Karl')
    open_first_card(p)
    side = p.locator('#hub-detail .h-why h4').inner_text().split()[3] if p.locator('#hub-detail .h-why h4').inner_text().startswith('Why') else None
    if side:
        p.locator(f'#hub-decision [data-hub=history-side][data-id={side.lower()}]').click()
    tile = p.locator('#hub-detail .h-history-summary .h-tile').nth(1).locator('b').inner_text().replace('\n', '')
    k, n = tile.split('/')
    went = [x['text'] for x in why(p)['why'] + why(p)['risk'] if x['text'].startswith('Went ')]
    assert went and f'{k} of the last {n}' in went[0], (tile, went)
    assert not p.errors


def test_phone_hit_strip_has_its_own_full_width_row(ctx, env):
    p = board(ctx, env, PHONE)
    sport(p, 'cs2')
    m = p.evaluate("""() => { const tr = document.querySelector('#hub-rows tr[data-record]'), hits = tr.querySelector('td.h-c-hits'),
      proj = tr.querySelector('td.h-c-proj'), cells = [...hits.querySelectorAll('.h-strip-cell')].map(c => c.getBoundingClientRect());
      return {row: tr.getBoundingClientRect().width, hits: hits.getBoundingClientRect().width, top: hits.getBoundingClientRect().top,
              proj: proj.getBoundingClientRect().top, cells: cells.map(c => [c.left, c.width]), labels: [...hits.querySelectorAll('.h-strip-cell')].map(c => c.dataset.label)}; }""")
    assert m['labels'] == ['L5', 'L10', 'L20', 'H2H', 'SZN'], m
    assert m['hits'] >= m['row'] - 30, m                              # the strip spans the row
    widths = [w for _, w in m['cells']]
    assert min(widths) >= 52 and max(widths) - min(widths) < 2, m     # five even chips, wide enough to read
    gaps = [m['cells'][i + 1][0] - (m['cells'][i][0] + m['cells'][i][1]) for i in range(4)]
    assert min(gaps) >= 6, gaps                                       # visibly apart, not clumped
    assert m['proj'] > m['top'], m                                    # Proj moved under the strip, beside Compare
    no_h_scroll(p)
    shot(p, 'why-phone-board-strip')
    assert not p.errors
