"""Hub shell flags in a real browser: SCOUT_UI_TOKENS (classic tokens, skip link, account control, 40px targets, three breakpoints),
SCOUT_MARKET_TOOLS (Board-style price filters and density) and the SCOUT_BACKGROUND_ALERTS deep link to a prop. Each flag off and on.
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

def market_doc(age_built=0):
    """The Phase 4 sheet (workspace_markets.build) as the server sends it."""
    now = time.time()
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    def ev(book, label, player, side, odds, p, e, age=30, flags=(), wide=False, sport='nba'):
        return {'sport': sport, 'event': 'Away at Home', 'player': player, 'team': 'T', 'stat': 'points', 'stat_label': 'Points', 'period_label': 'Full game',
                'line': 24.5, 'side': side, 'book': book, 'book_label': label, 'kind': 'sportsbook', 'odds': odds, 'p_cal': p, 'p_push': 0.0,
                'p_source': 'No-vig consensus · FanDuel, BetRivers (worst case of 4 devig methods)', 'ev': e,
                'width': {'overround': .09 if wide else .04, 'width_cents': 30 if wide else 15, 'wide': wide}, 'age_s': age, 'seen_at': iso(now - age),
                'source': 'sports service · ' + label, 'flags': list(flags)}
    def leg(book, label, side, odds, share, age=20, flags=()):
        return {'side': side, 'book': book, 'book_label': label, 'kind': 'sportsbook', 'odds': odds, 'implied': 1 / odds, 'stake_share': share,
                'age_s': age, 'seen_at': iso(now - age), 'source': 'sports service · ' + label, 'flags': list(flags)}
    arb = {'sport': 'nba', 'event': 'Away at Home', 'player': 'Nba Center', 'team': 'T', 'stat': 'points', 'stat_label': 'Points', 'period_label': 'Full game',
           'line': 24.5, 'margin_pct': 1.244, 'total_implied': .98771,
           'legs': [leg('alpha', 'Alpha', 'over', 2.08, .4867), leg('beta', 'Beta', 'under', 2.05, .5133)]}
    flagged = dict(arb, player='Nba Stale', margin_pct=3.1, flags=['Stale leg'],
                   legs=[leg('alpha', 'Alpha', 'over', 2.2, .48, age=900, flags=['Stale leg']), leg('beta', 'Beta', 'under', 2.0, .52)])
    nba = {'state': 'ready', 'as_of': iso(now), 'newest_quote_at': iso(now - 20), 'quotes_checked': 9, 'markets_checked': 3,
           'ev': [ev('alpha', 'Alpha', 'Nba Guard', 'over', 2.1, .5, .05), ev('beta', 'Beta', 'Nba Wing', 'under', 1.95, .52, .014, age=300, wide=True, flags=['Wide market'])],
           'arbitrage': [arb], 'flagged': [flagged], 'excluded': {'Whole-number line: push chance not priced': 3}}
    nfl = {'state': 'ready', 'as_of': iso(now), 'newest_quote_at': iso(now - 40), 'quotes_checked': 2, 'markets_checked': 1,
           'ev': [dict(ev('gamma', 'Gamma', 'Nfl Back', 'over', 1.9, .56, .064, age=40), sport='nfl', stat_label='Rush yards')], 'arbitrage': [], 'flagged': [], 'excluded': {}}
    cs2 = {'state': 'empty', 'as_of': iso(now), 'ev': [], 'arbitrage': [], 'flagged': [], 'excluded': {}}
    return {'as_of': iso(now - age_built), 'sports': {'cs2': cs2, 'nba': nba, 'nfl': nfl},
            'books': [{'id': 'alpha', 'label': 'Alpha'}, {'id': 'beta', 'label': 'Beta'}, {'id': 'gamma', 'label': 'Gamma'}],
            'method': {'ev': 'EV = p_cal x decimal odds - 1, push-adjusted (a push returns the stake)', 'p_cal': 'Scout chance or consensus.',
                       'arbitrage': 'stake share A = impA / (impA + impB); margin % = (1 / (impA + impB) - 1) x 100',
                       'wide_overround': .08, 'arb_max_age_s': 300, 'ev_stale_s': 1800, 'fresh_filters': [[120, '2 min'], [600, '10 min'], [1800, '30 min']]}}


def market_rows(p):
    return p.locator('#hub-market-results .h-market-table:not(.h-flagged) tbody tr').count() - p.locator('#hub-market-results details.h-mt-flagged tbody tr').count()


def test_market_tools_off_has_no_price_section(ctx, env, monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.delenv('SCOUT_MARKET_TOOLS', raising=False)
    p = open_page(ctx, env, '/#/tools')
    p.wait_for_selector('#hub-tools-body article')
    assert p.locator('[data-hub="refresh-markets"]').count() == 0 and p.locator('#hub-market-filters').count() == 0
    assert p.locator('#hub-mt-style').count() == 0
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_market_tools_ev_and_arbitrage_sheet(browser, env, monkeypatch, size):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    c = context(browser, env, size)
    try:
        p = c.new_page()
        p.errors = []
        p.on('pageerror', lambda e: p.errors.append(str(e)))
        doc = market_doc()
        p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(json=doc))
        p.goto(env.url + '/#/tools')
        p.wait_for_selector('#hub-market-filters .h-market-filters')            # loads on its own: no click needed
        assert market_rows(p) == 5                                              # NBA: 2 EV rows + a two-leg arbitrage; NFL: 1 EV row
        heads = p.locator('#hub-market-results article').nth(1).locator('table').first.locator('th').all_text_contents()
        assert heads == ['Player / market', 'Book', 'Side / line', 'Odds', 'p_cal', 'EV %', 'Freshness', 'Source']
        arb_heads = p.locator('#hub-market-results article').nth(1).locator('table').nth(1).locator('th').all_text_contents()
        assert arb_heads == ['Player / market', 'Book', 'Side / line', 'Odds', 'Stake share', 'Margin %', 'Freshness', 'Source']
        text = p.locator('#hub-market-results').inner_text()
        assert '+5.0%' in text and '48.7%' in text and '+1.24%' in text and 'Wide market' in text
        assert 'No book prices are held for CS2 right now.' in text
        assert 'guaranteed' not in text.lower() and 'not bet placement' not in text.lower()
        flagged = p.locator('details.h-mt-flagged')
        assert flagged.count() == 1 and 'Flagged and left out (1)' in flagged.inner_text()
        p.locator('#hub-mt-book').select_option('gamma')
        assert 'Nfl Back' in p.locator('#hub-market-results').inner_text() and 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-book').select_option('all')
        p.locator('#hub-mt-minEv').select_option('0.02')
        assert 'Nba Wing' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-minEv').select_option('0')
        p.locator('#hub-mt-fresh').select_option('120')
        assert 'Nba Wing' not in p.locator('#hub-market-results').inner_text() and 'Nba Guard' in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-fresh').select_option('all')
        p.locator('#hub-mt-league').select_option('nfl')
        assert 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-league').select_option('all')
        p.locator('#hub-mt-q').fill('center')
        assert p.evaluate("document.activeElement.id") == 'hub-mt-q'
        assert 'Nba Center' in p.locator('#hub-market-results').inner_text() and 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-q').fill('')
        dense = p.locator('#hub-mt-dense')
        dense.click()
        assert dense.get_attribute('aria-pressed') == 'true' and p.locator('.h-market-table.is-compact').count() >= 3
        assert p.locator('#hub-market-filters :is(select,input,button)').evaluate_all("es=>es.every(e=>e.getBoundingClientRect().height>=40)")
        no_h_scroll(p)
        if SHOTS_DIR:
            p.locator('#hub-market-tools').screenshot(path=str(SHOTS_DIR / f"tools_ev_arb_{size['width']}.png"))
        assert not p.errors
    finally:
        c.close()


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
