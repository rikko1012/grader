"""card-why (owner 2026-11-06) in a real browser at 1440 and 390, real Handler + fixture data (no network):
- every sport's player card opens with "Why Grader leans Over / Under" (or "The numbers for ..." without a side): Grader's own number
  first, then the facts for the side, "What could go wrong" (definite facts only) and "Also good to know"; the rest of the card
  (hit tiles, chart, H2H, Lines, Stats, Ask) is still there, after it. Dots (used / not used in the grade) only where a grade exists.
  The phone strip layout is phone-hit-chips' (tests/test_phone_hit_chips_browser.py); card-and-chips 2026-11-06 dropped the second one.
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
      return {title: w.querySelector('h4').innerText, why: [...items('.h-why-scout'), ...items('.h-why-for')], risk: items('.h-why-risk'), also: items('.h-why-also'),
              first: body.firstElementChild === w || body.firstElementChild.nextElementSibling === w && body.firstElementChild.classList.contains('h-warning'),
              key: w.querySelector('.h-why-key')?.innerText || '', after: [...body.children].map(e => e.className).join(' ')}; }""")


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
@pytest.mark.parametrize('sp', ['cs2', 'lol', 'nba', 'nfl'])
def test_every_sport_card_opens_with_why_scout_leans(ctx, env, sp, size):
    p = board(ctx, env, size)
    sport(p, sp)
    open_first_card(p)
    w = why(p)
    assert w['first'], w['after']                                    # the reason comes first on the card
    assert w['title'].startswith(('Why Grader leans Over', 'Why Grader leans Under', 'The numbers for')), w['title']
    assert 1 <= len(w['why']) <= 9 and len(w['risk']) <= 4 and len(w['also']) <= 4, w
    assert 'Grader projects' in w['why'][0]['text'] or 'Grader gives' in w['why'][0]['text'], w['why'][0]
    facts = w['why'] + w['risk'] + w['also']
    if w['key']:                                                     # a grade exists: every fact says whether it feeds it
        assert all(x['dot'] for x in facts) and w['why'][0]['used']
        assert 'Used in the grade' in w['key'] and 'Not used in the grade' in w['key']
    else:                                                            # no grade (NBA / NFL held letters): no "used in the grade" dot at all
        assert not any(x['dot'] for x in facts), facts
    assert all(not x['used'] for x in facts if not x['text'].startswith(('Grader projects', 'Grader gives', 'His base rate', 'Likely maps')) and 'kills a game than' not in x['text'] and 'win chance moves' not in x['text'])
    text = ' '.join(x['text'] for x in facts).lower()
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
    w = why(p)
    went = [x['text'] for x in w['why'] + w['risk'] + w['also'] if x['text'].startswith('Went ')]
    assert went and f'{k} of the last {n}' in went[0], (tile, went)
    assert not p.errors

@pytest.mark.parametrize('sp', ['cs2', 'lol', 'nba', 'nfl'])
@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
def test_key_insights_current_line_counts(ctx, env, sp, size):
    p = board(ctx, env, size)
    sport(p, sp)
    open_first_card(p)
    assert p.locator('.h-why .h-eyebrow').inner_text() == 'KEY INSIGHTS' or p.locator('.h-why .h-eyebrow').inner_text() == 'Key insights'
    stats = p.locator('.h-insight-stats')
    if stats.count():
        cards = stats.locator(':scope > div')
        assert cards.count() == 4
        o, n = map(int, cards.nth(0).locator('b').inner_text().split('/'))
        u, un = map(int, cards.nth(1).locator('b').inner_text().split('/'))
        pushes = int(cards.nth(1).locator('small').inner_text().split()[0])
        assert n == un and o + u + pushes == n and n <= 10
        p.locator('.h-insight-more summary').click()
        assert 'not a predicted chance' in p.locator('.h-insight-more').inner_text()
        # Changing the reviewed side must not change objective history counts.
        before = stats.inner_text()
        p.locator('[data-hub=history-side][data-id=under]').click()
        assert p.locator('.h-insight-stats').inner_text() == before
    else:
        assert p.locator('.h-insight-empty').is_visible()
    no_h_scroll(p)
    assert not p.errors
