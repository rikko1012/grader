"""Hub shell flags in a real browser: SCOUT_UI_TOKENS (classic tokens, skip link, account control, 40px targets, three breakpoints),
SCOUT_MARKET_TOOLS (Price check: plain sections, filters and density) and the SCOUT_BACKGROUND_ALERTS deep link to a prop. Each flag off and on.
Real Handler + isolated fixtures; no network, no production writes."""
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

import workspace_alerts
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE

SHOTS_DIR = Path(os.environ['PHASE4_SHOTS']) if os.environ.get('PHASE4_SHOTS') else None
TABLET = {"width": 800, "height": 1024}
SMALL_DESK = {"width": 1060, "height": 800}


def hub(ctx, env, size=None, path='/#all-sports'):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector('#hub-rows tr', timeout=30000)
    return p


SMALL_TARGETS = """() => [...document.querySelectorAll('#u-hub button,#u-hub select,#u-hub input,#u-hub summary,#u-hub .h-sidebar nav a')]
  .filter(e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('[hidden]'); })
  .filter(e => e.getBoundingClientRect().height < 39.5)
  .map(e => e.tagName + '.' + e.className + '#' + e.id + ' ' + Math.round(e.getBoundingClientRect().height) + 'px ' + (e.textContent || '').trim().slice(0, 20))"""


# ---------------------------------------------------------------------------------------------------------------- SCOUT_UI_TOKENS

def test_ui_tokens_off_keeps_the_hub_as_it_was(ctx, env, monkeypatch):
    monkeypatch.delenv('SCOUT_UI_TOKENS', raising=False)
    p = hub(ctx, env)
    assert p.evaluate("document.documentElement.dataset.uiTokens") is None
    assert p.locator('link[href="/upgrade/tokens.css"]').count() == 0
    assert p.locator('#u-hub .h-skip').count() == 0 and p.locator('#u-hub .h-account').count() == 0
    assert p.evaluate("getComputedStyle(document.getElementById('u-hub')).backgroundColor") != 'rgb(8, 8, 8)'
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, SMALL_DESK, TABLET, PHONE])
def test_ui_tokens_on_classic_palette_skip_link_account_and_targets(browser, env, monkeypatch, size):
    monkeypatch.setenv('SCOUT_UI_TOKENS', '1')
    c = context(browser, env, size)
    try:
        p = hub(c, env, size)
        p.wait_for_function("getComputedStyle(document.getElementById('u-hub')).backgroundColor==='rgb(8, 8, 8)'")
        assert p.evaluate("document.documentElement.dataset.uiTokens") == '1'
        style = p.evaluate("""() => { const h = document.getElementById('u-hub'), t = document.getElementById('hub-title');
          return {font: getComputedStyle(h).fontFamily, title: getComputedStyle(t).fontFamily,
                  gold: getComputedStyle(h).getPropertyValue('--u-gold').trim(), eyebrow: getComputedStyle(document.querySelector('#u-hub .h-eyebrow')).color}; }""")
        assert style['font'].startswith('Outfit') and 'Bebas Neue' in style['title'] and 'Inter' not in style['font']
        assert style['gold'] == '#f5c842' and style['eyebrow'] == 'rgb(245, 200, 66)'
        # the skip link is the hub's first stop and lands on the page heading
        assert p.evaluate("document.querySelector('#u-hub').firstElementChild.className") == 'h-skip'
        assert p.locator('#u-hub .h-skip').bounding_box()['y'] < 0                    # off screen until focused
        p.locator('#u-hub .h-skip').focus()
        assert p.locator('#u-hub .h-skip').bounding_box()['y'] >= 0
        p.keyboard.press('Enter')
        assert p.evaluate("document.activeElement.id") == 'hub-title'
        # one named account control with a real sign-out
        summary = p.locator('#u-hub .h-account > summary')
        assert summary.get_attribute('aria-label') == 'Account: Tester'
        summary.click()
        assert p.locator('#u-hub .h-account nav a[href="/auth/logout"]').is_visible()
        p.locator('#u-hub .h-account nav [data-id="picks"]').click()
        assert p.locator('#u-hub .h-account').evaluate("d => d.open") is False
        assert not p.locator('#hub-picks-section').is_hidden()
        p.locator('#u-hub .h-tabbar [data-id="board"], #u-hub .h-sidebar [data-id="board"]').locator('visible=true').first.click()
        p.wait_for_selector('#hub-rows tr')
        small = p.evaluate(SMALL_TARGETS)
        assert small == [], small
        sidebar = p.locator('#u-hub .h-sidebar').is_visible()
        assert sidebar is (size['width'] >= 1101)                                   # the rail on desktop; the bottom tab bar below 1101px
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


DETAIL_SMALL = SMALL_TARGETS.replace("#u-hub button,#u-hub select,#u-hub input,#u-hub summary,#u-hub .h-sidebar nav a",
                                     "#hub-detail button,#hub-detail select,#hub-detail input,#hub-detail summary")


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_ui_tokens_on_open_prop_card_targets_colours_and_account_outside_click(browser, env, monkeypatch, size):
    """Review 2026-11-04: with the flag on the open card kept 26-34px controls, navy rows / tiles and blue selections, and the
    account menu stayed open after a click outside it."""
    monkeypatch.setenv('SCOUT_UI_TOKENS', '1')
    c = context(browser, env, size)
    try:
        p = hub(c, env, size)
        p.wait_for_function("getComputedStyle(document.getElementById('u-hub')).backgroundColor==='rgb(8, 8, 8)'")
        p.locator('#hub-rows .h-player').first.click()
        p.wait_for_selector('#hub-detail .h-history-controls', timeout=20000)
        small = p.evaluate(DETAIL_SMALL)
        assert small == [], small
        col = p.evaluate("""() => { const td = document.querySelector('#hub-rows tr.is-expanded>td'), sel = document.querySelector('#hub-detail .h-segment button[aria-pressed=true]'),
              tile = document.querySelector('#hub-detail .h-tile, #hub-detail .h-mini-tiles>div');
          return {row: getComputedStyle(td).backgroundColor, sel: sel && getComputedStyle(sel).backgroundColor, tile: tile && getComputedStyle(tile).backgroundColor}; }""")
        navy = {'rgb(24, 34, 47)', 'rgb(41, 67, 98)', 'rgb(22, 29, 39)'}
        assert not navy & set(col.values()), col
        assert col['sel'] == 'rgb(245, 200, 66)'
        no_h_scroll(p)
        p.keyboard.press('Escape')                          # 2026-11-05: the card is a panel / full-screen sheet; Escape returns to the board
        assert p.locator('#hub-card').is_hidden()
        summary = p.locator('#u-hub .h-account > summary')
        summary.click()
        assert p.locator('#u-hub .h-account').evaluate("d => d.open") is True
        p.mouse.click(5, (size['height'] if isinstance(size, dict) else 800) - 5)
        assert p.locator('#u-hub .h-account').evaluate("d => d.open") is False
        summary.click()
        p.locator('#u-hub .h-account nav a[href="/auth/logout"]').focus()
        assert p.locator('#u-hub .h-account').evaluate("d => d.open") is True            # focus inside keeps it open
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize('path', ['/sports?view=classic#/board', '/lol?view=classic', '/?view=classic#board'])
def test_ui_tokens_on_classic_entry_chip_is_one_colour(browser, env, monkeypatch, path):
    monkeypatch.setenv('SCOUT_UI_TOKENS', '1')
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, path, DESK)
        p.wait_for_selector('.h-entry', timeout=30000)
        st = p.evaluate("""() => { const e = document.querySelector('.h-entry'), s = getComputedStyle(e), g = getComputedStyle(e).getPropertyValue('--u-gold').trim();
          return {color: s.color, border: s.borderTopColor, bg: s.backgroundColor}; }""")
        def rgb(s):                      # 'rgb(r, g, b)' / 'rgba(...)' or color-mix's 'color(srgb r g b / a)'
            inner = s[s.index('(') + 1:s.rindex(')')].split('/')[0]
            if s.startswith('color('):
                return tuple(round(float(x) * 255) for x in inner.split()[1:4])
            return tuple(round(float(x)) for x in inner.replace(',', ' ').split()[:3])
        assert rgb(st['color']) == rgb(st['border']) == rgb(st['bg']), st         # one hue: text, border and tint agree
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- SCOUT_MARKET_TOOLS

def market_doc(age_built=0, owner=False, empty=False):
    """The Price check sheet (workspace_markets.build, cut by for_member) as the server sends it. owner=True keeps the owner's
    extras (flagged pairs, pick'em rows, exclusions, formulas); a member's copy has none of them."""
    now = time.time()
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    am = lambda d: f"+{round((d - 1) * 100)}" if d >= 2 else f"-{round(100 / (d - 1))}"
    def ev(book, label, player, side, odds, p, e, age=30, flags=(), wide=False, sport='nba', kind='sportsbook'):
        return {'sport': sport, 'event': 'Away at Home', 'start_utc': iso(now + 7200), 'player': player, 'team': 'T', 'stat': 'points', 'stat_label': 'Points',
                'period_label': 'Full game', 'line': 24.5, 'side': side, 'book': book, 'book_label': label, 'kind': kind, 'odds': odds, 'p_cal': p, 'p_push': 0.0,
                'ev': e, 'width': {'overround': .09 if wide else .04, 'width_cents': 30 if wide else 15, 'wide': wide}, 'age_s': age, 'seen_at': iso(now - age),
                'source': 'sports service · ' + label, 'flags': list(flags), 'needs': round(1 / odds, 4), 'american': am(odds), 'per_100': round(e * 100, 2),
                'chance_from': 'Other books (Beta, Gamma)', **({'p_source': 'No-vig consensus · Beta, Gamma (worst case of 4 devig methods)'} if owner else {})}
    def leg(book, label, side, odds, share, age=20, flags=()):
        return {'side': side, 'book': book, 'book_label': label, 'kind': 'sportsbook', 'odds': odds, 'implied': 1 / odds, 'stake_share': share,
                'stake_per_100': round(share * 100, 2), 'american': am(odds), 'age_s': age, 'seen_at': iso(now - age), 'source': 'sports service · ' + label,
                'flags': list(flags)}
    arb = {'sport': 'nba', 'event': 'Away at Home', 'start_utc': iso(now + 7200), 'player': 'Nba Center', 'team': 'T', 'stat': 'points', 'stat_label': 'Points',
           'period_label': 'Full game', 'line': 24.5, 'margin_pct': 1.244, 'total_implied': .98771, 'return_per_100': 1.24, 'oldest_leg_s': 20,
           'legs': [leg('alpha', 'Alpha', 'over', 2.08, .4867), leg('beta', 'Beta', 'under', 2.05, .5133)]}
    flagged = dict(arb, player='Nba Stale', margin_pct=3.1, flags=['Stale leg'],
                   legs=[leg('alpha', 'Alpha', 'over', 2.2, .48, age=900, flags=['Stale leg']), leg('kalshi', 'Kalshi', 'under', 2.0, .52)])
    nba = {'state': 'ready', 'as_of': iso(now), 'newest_quote_at': iso(now - 20), 'quotes_checked': 9, 'markets_checked': 3,
           'ev': [ev('alpha', 'Alpha', 'Nba Guard', 'over', 2.1, .5, .05), ev('beta', 'Beta', 'Nba Wing', 'under', 1.95, .52, .014, age=300, wide=True, flags=['Wide market'])],
           'arbitrage': [arb], 'sections_possible': {'ev': True, 'arb': True}}
    nfl = {'state': 'ready', 'as_of': iso(now), 'newest_quote_at': iso(now - 40), 'quotes_checked': 2, 'markets_checked': 1,
           'ev': [dict(ev('gamma', 'Gamma', 'Nfl Back', 'over', 1.9, .56, .064, age=40), sport='nfl', stat_label='Rush yards')], 'arbitrage': [],
           'sections_possible': {'ev': True, 'arb': False}}
    cs2 = {'state': 'ready', 'as_of': iso(now), 'quotes_checked': 39, 'markets_checked': 39, 'ev': [], 'arbitrage': [], 'sections_possible': {'ev': False, 'arb': False}}
    if empty:
        nba = dict(nba, ev=[], arbitrage=[], quotes_checked=671, markets_checked=325)
        nfl = dict(nfl, ev=[], arbitrage=[], quotes_checked=1765, markets_checked=968)
    if owner:
        nba.update(flagged=[flagged], excluded={'Whole-number line: push chance not priced': 3}, excluded_lines={'Whole-number line: push chance not priced': 1})
        nba['ev'] = nba['ev'] + [ev('sleeper', 'Sleeper', 'Nba Pickem', 'over', 2.6, .5, .3, kind='pickem', flags=["Pick'em multiplier: needs 2+ picks"])]
        for d in (nfl, cs2):
            d.update(flagged=[], excluded={}, excluded_lines={})
    books = sorted({(r['book'], r['book_label']) for d in (cs2, nba, nfl) for r in d['ev']}
                   | {(l['book'], l['book_label']) for d in (cs2, nba, nfl) for r in d['arbitrage'] + d.get('flagged', []) for l in r['legs']})
    method = {'wide_overround': .08, 'arb_max_age_s': 300, 'ev_stale_s': 1800, 'fresh_filters': [[120, '2 min'], [600, '10 min'], [1800, '30 min']]}
    if owner:
        method.update(ev='EV = p_cal x decimal odds - 1, push-adjusted (a push returns the stake)', p_cal='Scout chance or consensus.',
                      arbitrage='stake share A = impA / (impA + impB); margin % = (1 / (impA + impB) - 1) x 100')
    return {'as_of': iso(now - age_built), 'owner': owner, 'sports': {'cs2': cs2, 'nba': nba, 'nfl': nfl},
            'books': [{'id': b, 'label': l} for b, l in books], 'method': method}


JARGON = ('p_cal', 'devig', 'no-vig', 'overround', 'impA', 'Margin %', 'Stake share', 'Flagged and left out', 'Prices not rated', 'Kalshi', 'Polymarket', 'Stale')


def market_rows(p):
    return p.locator('#hub-market-results .h-pc-list > .h-pc-row:not(.h-pc-head)').count()


def mt_page(c, env, doc, path='/#/tools'):
    p = c.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(json=doc))
    p.goto(env.url + path)
    p.wait_for_selector('#hub-market-filters .h-market-filters')            # loads on its own: no click needed
    return p


def test_market_tools_off_has_no_price_section(ctx, env, monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.delenv('SCOUT_MARKET_TOOLS', raising=False)
    p = open_page(ctx, env, '/#/tools')
    p.wait_for_selector('#hub-tools-body article')
    assert p.locator('[data-hub="refresh-markets"]').count() == 0 and p.locator('#hub-market-filters').count() == 0
    assert p.locator('#hub-mt-style').count() == 0
    assert 'Price check' not in p.locator('#hub-tools-body').inner_text()
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_market_tools_ev_and_arbitrage_sheet(browser, env, monkeypatch, size):
    """Price check (owner 2026-11-05): two plain sections, money per $100, a stake box, no jargon and no status chips for members."""
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    c = context(browser, env, size)
    try:
        p = mt_page(c, env, market_doc())
        if SHOTS_DIR:                                                           # as a member first sees it, before any filter
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            p.locator('#hub-market-tools').screenshot(path=str(SHOTS_DIR / f"tools_price_check_{size['width']}.png"))
        assert 'Price check' in p.locator('#hub-tools-body').inner_text()
        assert p.locator('#hub-market-tools h2').inner_text() == 'Price check'
        assert market_rows(p) == 4                                              # 3 single bets (NBA 2, NFL 1) + 1 both-sides pair
        titles = p.locator('#hub-market-results .h-pc-section h3').all_text_contents()
        assert titles == ['Prices better than the fair chance', 'Bet both sides at two books']
        heads = p.locator('#hub-market-results .h-pc-ev .h-pc-head .h-pc-cell').all_text_contents()
        assert heads == ['Pick', 'Book & price', 'Fair chance vs needed', 'Expected per $100', 'Checked']
        arb_heads = p.locator('#hub-market-results .h-pc-arbs .h-pc-head .h-pc-cell').all_text_contents()
        assert arb_heads == ['Pick', 'Bet 1', 'Bet 2', 'Either way you get', 'Checked']
        text = p.locator('#hub-market-results').inner_text()
        assert '+$6.40' in text and '+$5.00' in text and '+$1.40' in text            # expected per $100, best first
        assert 'Alpha +110' in text and '(2.10)' not in text                          # American price only: no unexplained decimal
        assert '50.0%' in text and '47.6%' in text                                    # fair chance vs the chance the price needs
        assert '$48.67' in text and '$51.33' in text and '$101.24 back' in text and '(+$1.24)' in text
        assert 'big cut' not in text and 'not a sure win' not in text and 'Prices move fast' not in text   # no hedge lines
        assert ('Gamma pays -111 on Over 24.5, which needs a 52.6% chance to break even. The fair chance is 56.0%, so over many bets '
                'like this you would average about +$6.40 per $100.') in text                  # the worked example uses the best row
        assert 'Beta Under 24.5 +105' in text and '(2.05)' not in text
        assert ('Put $48.67 on Over at Alpha +108 and $51.33 on Under at Beta +105: either result pays about $101.24 for $100.00 staked.') in text
        for word in JARGON:
            assert word not in text, word
        assert p.locator('#hub-market-results .h-flag, details.h-mt-flagged, details.h-mt-owner').count() == 0
        assert 'guaranteed' not in text.lower() and 'not bet placement' not in text.lower()
        # the stake box only scales the server's numbers
        p.locator('#hub-mt-stake').fill('250')
        p.wait_for_function("document.querySelector('#hub-market-results').innerText.includes('$253.10 back')")
        t250 = p.locator('#hub-market-results').inner_text()
        assert '$121.6' in t250 and p.evaluate("document.activeElement.id") == 'hub-mt-stake'
        assert 'for $250.00 staked' in t250 and 'pays about $253.10' in t250 and 'for $100.00 staked' not in t250   # the example follows the box
        assert t250.count('$121.67') == 2 and t250.count('$128.32') == 2
        # filters: Search, Sport and Book on the bar; the rest under More filters
        p.locator('#hub-mt-book').select_option('gamma')
        assert 'Nfl Back' in p.locator('#hub-market-results').inner_text() and 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-book').select_option('all')
        assert p.locator('#hub-mt-minEv').is_hidden()
        p.locator('details.h-mt-more > summary').click()
        p.locator('#hub-mt-minEv').select_option('0.02')
        assert 'Nba Wing' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-minEv').select_option('0')
        p.locator('#hub-mt-fresh').select_option('120')
        assert 'Nba Wing' not in p.locator('#hub-market-results').inner_text() and 'Nba Guard' in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-fresh').select_option('all')
        assert p.locator('#hub-mt-sort option').all_text_contents() == ['Best value', 'Highest fair chance', 'Newest check']
        p.locator('#hub-mt-sort').select_option('chance')
        names = p.locator('#hub-market-results .h-pc-ev .h-pc-row:not(.h-pc-head) .h-pc-pick b').all_text_contents()
        assert names == ['Nfl Back', 'Nba Wing', 'Nba Guard']                    # 56%, 52%, 50%
        p.locator('#hub-mt-sort').select_option('ev')
        p.locator('#hub-mt-league').select_option('nfl')
        assert 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        assert p.locator('#hub-mt-book option').evaluate_all('os=>os.map(o=>o.value)') == ['all', 'gamma']   # books follow the sport
        p.locator('#hub-mt-league').select_option('all')
        p.locator('#hub-mt-q').fill('center')
        assert p.evaluate("document.activeElement.id") == 'hub-mt-q'
        assert 'Nba Center' in p.locator('#hub-market-results').inner_text() and 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-q').fill('')
        dense = p.locator('#hub-mt-dense')
        dense.click()
        assert dense.get_attribute('aria-pressed') == 'true' and p.locator('.h-pc-list.is-compact').count() == 2
        assert p.locator('#hub-market-filters :is(select,input,button,summary)').evaluate_all(
            "es=>es.filter(e=>e.getBoundingClientRect().height>0).every(e=>e.getBoundingClientRect().height>=40)")
        assert p.locator('.h-market-table').count() == 0                         # no 720px table: rows are cards on a phone
        if size['width'] < 640:
            assert p.locator('#hub-market-results .h-pc-head').first.is_hidden()
            chip = p.locator('#hub-market-results .h-pc-ev .h-pc-row:not(.h-pc-head) .h-pc-chip').first.bounding_box()
            assert chip and chip['x'] + chip['width'] <= size['width']
            assert 'Fair 50.0% · needs 47.6%' in p.locator('#hub-market-results').inner_text()
        # 'More filters' and 'How these numbers are worked out' show a chevron, so they read as things to tap
        marks = p.locator('#hub-market-tools summary').evaluate_all(
            "ss=>ss.filter(s=>s.getBoundingClientRect().height>0).map(s=>[s.textContent.trim(),getComputedStyle(s,'::after').borderRightStyle,s.getBoundingClientRect().height])")
        assert any(t.startswith('More filters') for t, _, _ in marks) and any(t.startswith('How these numbers') for t, _, _ in marks)
        assert all(b == 'solid' and h >= 40 for _, b, h in marks), marks
        css = p.evaluate("document.getElementById('hub-mt-style').textContent")
        assert '#edb968' not in css and '#8a704b' not in css and 'min-width:720px' not in css
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def _aged(doc, ev_age=None, arb_age=None):
    from datetime import datetime, timezone
    iso = lambda a: datetime.fromtimestamp(time.time() - a, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for d in doc['sports'].values():
        for r in d['ev']:
            if ev_age is not None:
                r.update(age_s=ev_age, seen_at=iso(ev_age))
        for a in d['arbitrage']:
            for l in a['legs']:
                if arb_age is not None:
                    l.update(age_s=arb_age, seen_at=iso(arb_age))
    return doc


def test_market_tools_drop_rows_past_the_servers_age_limits(ctx, env, monkeypatch):
    """Review 2026-11-05: a sheet left open kept showing a both-sides pair 25 min old as 'win either way'. The page applies the
    server's own limits on every draw (both prices within arb_max_age_s, a price within ev_stale_s)."""
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    p = mt_page(ctx, env, _aged(market_doc(age_built=1500), arb_age=1500))
    text = p.locator('#hub-market-results').inner_text()
    assert 'Bet both sides at two books' not in text and 'Nba Center' not in text and 'either result pays' not in text
    assert 'Prices better than the fair chance' in text and market_rows(p) == 3          # the single bets are 25 min old: still in
    p.unroute('**/api/upgrade/market-tools')
    p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(json=_aged(market_doc(age_built=2000), ev_age=2000, arb_age=2000)))
    p.locator('[data-hub="refresh-markets"]').click()
    p.wait_for_function("!document.querySelector('#hub-market-results').innerText.includes('Prices better than the fair chance')")
    assert market_rows(p) == 0 and 'Nothing worth acting on right now' in p.locator('#hub-market-results').inner_text()
    # a fresh pair that ages on screen drops off on the next redraw (the page redraws once a minute), with no new request
    p.unroute('**/api/upgrade/market-tools')
    p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(json=market_doc()))
    p.locator('[data-hub="refresh-markets"]').click()
    p.wait_for_function("document.querySelector('#hub-market-results').innerText.includes('Bet both sides at two books')")
    p.evaluate("""() => { const real = Date.now; Date.now = () => real() + 400000; }""")         # 6 min 40 s later
    p.evaluate("""() => { document.getElementById('hub-market-filters').querySelector('#hub-mt-league').dispatchEvent(new Event('change', {bubbles: true})); }""")
    p.wait_for_function("!document.querySelector('#hub-market-results').innerText.includes('Bet both sides at two books')")
    assert not p.errors


def test_market_tools_member_sees_prediction_market_rows(ctx, env, monkeypatch):
    """Review 2026-11-05: Kalshi / Polymarket price rows stay in the member view (plainly labelled); pick'em rows do not."""
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    doc = market_doc()
    k = dict(doc['sports']['nba']['ev'][0], book='kalshi', book_label='Kalshi', kind='prediction', player='Nba Forward')
    doc['sports']['nba']['ev'].append(k)
    doc['books'].append({'id': 'kalshi', 'label': 'Kalshi'})
    p = mt_page(ctx, env, doc)
    row = p.locator('#hub-market-results .h-pc-ev .h-pc-row', has_text='Nba Forward')
    assert row.count() == 1 and 'Kalshi' in row.inner_text() and 'Prediction market' in row.inner_text()
    assert 'kalshi' in p.locator('#hub-mt-book option').evaluate_all('os=>os.map(o=>o.value)')
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_market_tools_nothing_to_act_on_is_one_line(browser, env, monkeypatch, size):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    c = context(browser, env, size)
    try:
        p = mt_page(c, env, market_doc(empty=True))
        text = p.locator('#hub-market-results').inner_text()
        assert text.count('Nothing worth acting on right now. Checked 2,475 prices on 1,332 lines at ') == 1
        assert p.locator('#hub-market-results .h-pc-section').count() == 0 and p.locator('#hub-market-results .h-mt-state').count() == 1
        assert 'No +EV' not in text and 'No two-book' not in text and 'No book prices are held' not in text
        no_h_scroll(p)
        if SHOTS_DIR:
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            p.locator('#hub-market-tools').screenshot(path=str(SHOTS_DIR / f"tools_price_check_empty_{size['width']}.png"))
        assert not p.errors
    finally:
        c.close()


def test_market_tools_follow_the_cs2_tab(ctx, env, monkeypatch):
    """Audit 2026-11-05: CS2 has no sportsbook prices, so from the CS2 tab the sheet opens on NBA. Choosing CS2 in its Sport filter
    still shows CS2 only: with no sportsbook prices for CS2 it says so in one line and lists no other book."""
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    p = mt_page(ctx, env, market_doc(), path='/#/tools?sport=cs2')
    assert p.locator('#hub-mt-league').input_value() == 'nba'
    p.locator('#hub-mt-league').select_option('cs2')
    p.wait_for_function("document.getElementById('hub-market-results').innerText.includes('No sportsbook offers CS2')")
    assert p.locator('#hub-mt-book option').evaluate_all('os=>os.map(o=>o.value)') == ['all']
    text = p.locator('#hub-market-results').inner_text()
    assert 'No sportsbook offers CS2 prices right now' in text and 'Nba' not in text and 'Alpha' not in text
    assert not p.errors


def test_market_tools_owner_keeps_every_detail(ctx, env, monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    p = mt_page(ctx, env, market_doc(owner=True))
    assert market_rows(p) == 4                                                  # the pick'em row stays out of the single-bet list
    owner = p.locator('details.h-mt-owner')
    assert owner.count() == 1
    owner.locator('> summary').click()
    t = owner.inner_text()
    assert 'Flagged and left out (1)' in t and "Pick'em, not a single bet (1)" in t and 'Prices not rated' in t
    p.locator('details.h-mt-method > summary').click()
    p.locator('details.h-mt-formulas > summary').click()
    assert 'impA' in p.locator('details.h-mt-formulas').inner_text()
    assert not p.errors


def test_market_tools_loading_stale_and_error_states(ctx, env, monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    p = ctx.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    hold = []
    p.route('**/api/upgrade/market-tools', lambda r: hold.append(r))
    p.goto(env.url + '/#/tools')
    p.wait_for_selector('#hub-market-results .h-mt-state')
    assert 'Checking current prices' in p.locator('#hub-market-results').inner_text()
    hold[0].fulfill(json=market_doc(age_built=600))
    p.wait_for_selector('#hub-market-filters .h-market-filters')
    assert 'These prices were built 10m ago' in p.locator('#hub-market-results').inner_text()
    p.unroute('**/api/upgrade/market-tools')
    p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(status=503, json={'error': 'market_tools_unavailable'}))
    p.locator('[data-hub="refresh-markets"]').click()
    p.wait_for_function("document.querySelector('#hub-market-results').innerText.includes('Refresh prices to try again')")
    assert not p.errors


# ---------------------------------------------------------------------------------------------------------------- SCOUT_BACKGROUND_ALERTS

def deep_link(key):
    sport, book, offer = key.split('|', 2)
    return workspace_alerts.prop_path({'sport': sport, 'offer': {'platform': book, 'offer_id': offer}})[1:]


def last_key(ctx, env):
    p = hub(ctx, env)
    key = p.locator('#hub-rows tr[data-record]').last.get_attribute('data-record')
    p.close()
    return key


def test_alert_deep_link_off_leaves_the_board_unopened(ctx, env, monkeypatch):
    monkeypatch.setattr(workspace_alerts, 'configured', lambda: False)
    key = last_key(ctx, env)
    p = hub(ctx, env, path=deep_link(key))
    p.wait_for_timeout(1200)
    assert p.locator('#hub-rows tr.is-expanded').count() == 0
    assert 'open=' in p.evaluate("location.hash")
    assert not p.errors


def test_alert_deep_link_on_opens_the_prop_and_reports_a_gone_one(ctx, env, monkeypatch):
    monkeypatch.setattr(workspace_alerts, 'configured', lambda: True)
    key = last_key(ctx, env)
    p = hub(ctx, env, path=deep_link(key))
    p.wait_for_selector(f'#hub-rows tr.is-expanded[data-record="{key}"]', timeout=20000)
    assert 'open=' not in p.evaluate("location.hash") and p.evaluate("location.hash").startswith('#scout/board')
    assert p.locator('#hub-section, #hub-board-section').first.is_visible()
    gone = hub(ctx, env, path=deep_link(key.split('|')[0] + '|prizepicks|no-such-offer'))
    gone.wait_for_function("document.body.innerText.includes('This offer is no longer on the board')", timeout=20000)
    assert gone.locator('#hub-rows tr.is-expanded').count() == 0
    assert not p.errors and not gone.errors
