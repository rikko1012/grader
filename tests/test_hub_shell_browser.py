"""Hub shell flags in a real browser: SCOUT_UI_TOKENS (classic tokens, skip link, account control, 40px targets, three breakpoints),
SCOUT_MARKET_TOOLS (Board-style price filters and density) and the SCOUT_BACKGROUND_ALERTS deep link to a prop. Each flag off and on.
Real Handler + isolated fixtures; no network, no production writes."""
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

import workspace_alerts
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE

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
        p.locator('#u-hub .h-nav-mobile [data-id="board"], #u-hub .h-sidebar [data-id="board"]').locator('visible=true').first.click()
        p.wait_for_selector('#hub-rows tr')
        small = p.evaluate(SMALL_TARGETS)
        assert small == [], small
        sidebar = p.locator('#u-hub .h-sidebar').is_visible()
        assert sidebar is (size['width'] >= 1025)                                   # three breakpoints: the shell keeps its rail to 1025px
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- SCOUT_MARKET_TOOLS

def market_doc():
    now = time.time()
    iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    def q(book, player, side, odds, age=5, line=24.5, stat='points'):
        return {'sport': 'nba', 'event_id': 'e1', 'player_id': player, 'stat': stat, 'period': 'full', 'line': line, 'settlement_rule': 'r1',
                'book': book, 'jurisdiction': 'NJ', 'side': side, 'decimal_odds': odds, 'source': book, 'observed_at': now - age,
                'source_at': now - age, 'available': True, 'liquid': True, 'commission': 0.0}
    nba = {'state': 'ready', 'as_of': iso, 'excluded': {'Quote older than two minutes': 3},
           'ev': [{'quote': q('alpha', 'Nba Guard', 'over', 2.1), 'p': .5, 'ev': .05},
                  {'quote': q('beta', 'Nba Wing', 'under', 1.95, age=300), 'p': .52, 'ev': .014, 'wide_market': True}],
           'arbitrage': [{'legs': [q('alpha', 'Nba Center', 'over', 2.08), q('beta', 'Nba Center', 'under', 2.05)],
                          'stake_shares': [.4964, .5036], 'quoted_return': .0324}]}
    nfl = {'state': 'ready', 'as_of': iso, 'excluded': {},
           'ev': [{'quote': dict(q('gamma', 'Nfl Back', 'over', 1.9, stat='rushing yards'), sport='nfl'), 'p': .56, 'ev': .064}], 'arbitrage': []}
    return {'leagues': {'nba': nba, 'nfl': nfl}, 'supported_sports': ['nba', 'nfl']}


def market_rows(p):
    return p.locator('#hub-market-results .h-market-table tbody tr').count()


def test_market_tools_off_has_no_price_section(ctx, env, monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.delenv('SCOUT_MARKET_TOOLS', raising=False)
    p = open_page(ctx, env, '/#/tools')
    p.wait_for_selector('#hub-tools-body article')
    assert p.locator('[data-hub="refresh-markets"]').count() == 0 and p.locator('#hub-market-filters').count() == 0
    assert p.locator('#hub-mt-style').count() == 0
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_market_tools_board_style_filters_flags_and_density(browser, env, monkeypatch, size):
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
        p.locator('[data-hub="refresh-markets"]').click()
        p.wait_for_selector('#hub-market-filters .h-market-filters')
        assert market_rows(p) == 5                                  # 3 EV rows + one two-leg comparison
        text = p.locator('#hub-market-results').text_content()
        assert 'Wide market' in text and 'Stale quote' in text and 'Quoted return' in text and '3.2%' in text
        assert 'guaranteed' not in text.lower()
        p.locator('#hub-mt-book').select_option('alpha')
        assert market_rows(p) == 3                                  # alpha's EV row + the comparison it is a leg of
        p.locator('#hub-mt-book').select_option('all')
        p.locator('#hub-mt-minEv').select_option('0.02')
        assert market_rows(p) == 4 and 'Nba Wing' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-minEv').select_option('0')
        p.locator('#hub-mt-league').select_option('nfl')
        assert market_rows(p) == 1 and 'Nfl Back' in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-league').select_option('all')
        p.locator('#hub-mt-q').fill('center')
        assert market_rows(p) == 2
        assert p.evaluate("document.activeElement.id") == 'hub-mt-q'          # typing never loses the field
        p.locator('#hub-mt-q').fill('')
        p.locator('#hub-mt-side').select_option('under')
        assert market_rows(p) == 3                                  # beta's EV row + the comparison with an Under leg
        p.locator('#hub-mt-side').select_option('all')
        p.locator('#hub-mt-sort').select_option('chance')
        assert p.locator('#hub-market-results article').first.locator('tbody tr').first.inner_text().startswith('Nba Wing')
        dense = p.locator('#hub-mt-dense')
        assert dense.get_attribute('aria-pressed') == 'false'
        dense.click()
        assert dense.get_attribute('aria-pressed') == 'true' and p.locator('.h-market-table.is-compact').count() == 3
        assert p.locator('#hub-market-filters :is(select,input,button)').evaluate_all("es=>es.every(e=>e.getBoundingClientRect().height>=40)")
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


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
