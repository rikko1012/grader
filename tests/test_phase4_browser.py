"""Phase 4 in a real browser at 1440 and 390: the extra-book toggle (SCOUT_BOOK_SLEEPER off / on), Live highlight wording
(SCOUT_BACKGROUND_ALERTS off / on) and the Genie persona (SCOUT_GENIE_PERSONA off / on). Real Handler + isolated fixtures; no network.
Set PHASE4_SHOTS=<folder> to keep screenshots."""
import os
import time
from pathlib import Path

import pytest

import book_providers as bp
import workspace_alerts
import workspace_books as wb
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

SHOTS_DIR = Path(os.environ['PHASE4_SHOTS']) if os.environ.get('PHASE4_SHOTS') else None


def shot(p, name, size, locator=None):
    if SHOTS_DIR:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        (p.locator(locator) if locator else p).screenshot(path=str(SHOTS_DIR / f"{name}_{size['width']}.png"), **({} if locator else {'full_page': False}))


def offer(name, team, line, over=1.8, under=1.76, stat='kills', period='maps12', sid='1'):
    now = time.time()
    return {"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": stat, "period": period, "subject_id": sid,
            "player": name, "player_norm": bp.norm(name), "team": team, "team_norm": bp.norm(team), "game_id": "g", "line": line,
            "over": {"offered": True, "payout": over, "price": None}, "under": {"offered": True, "payout": under, "price": None},
            "source_at": now - 120, "observed_at": now}


@pytest.fixture
def sleeper(monkeypatch):
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, 'start', lambda d: False)
    monkeypatch.setenv('SCOUT_BOOK_SLEEPER', '1')
    st = bp.state('sleeper')
    st.update(at=time.time(), offers=[offer('vargo', 'Halcyon', 38.5), offer('brandr', 'Norrland', 33.5, sid='2')])
    yield st
    bp.reset()


def hub(ctx, env, size=None, path='/#all-sports'):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector('#hub-rows tr', timeout=30000)
    return p


def test_book_toggle_flag_off_has_only_the_pickem_books(ctx, env, monkeypatch):
    monkeypatch.delenv('SCOUT_BOOK_SLEEPER', raising=False)
    p = hub(ctx, env)
    assert p.locator('#hub-platform [data-hub="book"]').evaluate_all('bs=>bs.map(b=>b.dataset.id)') == ['all', 'prizepicks', 'underdog']
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_book_toggle_flag_on_shows_view_only_sleeper_lines(browser, env, sleeper, size):
    c = context(browser, env, size)
    try:
        p = hub(c, env, size)
        assert 'sleeper' in p.locator('#hub-platform [data-hub="book"]').evaluate_all('bs=>bs.map(b=>b.dataset.id)')
        p.locator('#hub-platform [data-hub="book"][data-id="sleeper"]').click()
        assert p.locator('#hub-platform [data-id="sleeper"]').get_attribute('aria-pressed') == 'true'
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length===2", timeout=20000)
        text = p.locator('#hub-rows').inner_text()
        assert 'SL' in text and 'vargo' in text and 'brandr' in text
        v = p.locator('#hub-rows tr[data-record]', has_text='vargo').first
        b = p.locator('#hub-rows tr[data-record]', has_text='brandr').first
        assert '61' in v.inner_text()                                       # the saved Underdog chance at the same line, unchanged
        assert 'Lean' not in v.inner_text() or 'Lean' in p.locator('#hub-rows').inner_text()
        assert v.locator('.h-add').inner_text() == 'Info'
        v.locator('.h-add').click()
        p.wait_for_function("document.body.innerText.includes('Sleeper lines are view only')")
        assert p.locator('#hub-slip-count').inner_text() == '0'            # nothing reached the slip (one book per slip)
        assert 'view only' in p.locator('#hub-health').inner_text().lower()
        b.locator('.h-player').click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
        assert 'No saved chance at this line' in b.inner_text()                # the one Scout cell says so too
        detail = p.locator('#hub-detail').inner_text()
        assert '1.80x pick multiplier' in detail and 'No saved chance at this line' in detail
        no_h_scroll(p)
        shot(p, 'board_sleeper', size)
        p.locator('#hub-detail [data-hub="card-close"]').click()
        assert p.locator('#hub-card').is_hidden()
        assert not p.errors
    finally:
        c.close()


def test_book_toggle_stale_feed_hides_lines_and_says_so(ctx, env, sleeper):
    sleeper['at'] = time.time() - 3600
    p = hub(ctx, env)
    p.locator('#hub-platform [data-hub="book"][data-id="sleeper"]').click()
    p.wait_for_function("document.getElementById('hub-health').innerText.includes('older than their freshness limit')", timeout=20000)
    assert p.locator('#hub-rows tr[data-record]').count() == 0
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_live_highlight_wording_without_background_delivery(browser, env, monkeypatch, size):
    monkeypatch.setattr(workspace_alerts, 'configured', lambda: False)
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    c = context(browser, env, size)
    try:
        p = open_page(c, env, '/#/alerts', size)
        p.wait_for_selector('#hub-alerts-body h2')
        body = p.locator('#u-hub').inner_text()
        assert 'Live highlight' in body and 'Nothing is sent when it is closed' in body
        assert 'Line alerts' not in body and 'Alert me' not in p.locator('#u-hub').evaluate('e=>e.innerHTML')
        assert p.locator('[data-hub="save-alert"]').first.text_content() == 'Live highlight these filters'
        shot(p, 'live_highlight', size)
        assert not p.errors
    finally:
        c.close()


def test_alert_me_with_background_delivery(ctx, env, monkeypatch):
    monkeypatch.setattr(workspace_alerts, 'configured', lambda: True)
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    p = open_page(ctx, env, '/#/alerts')
    p.wait_for_selector('#hub-alerts-body h2')
    assert p.locator('[data-hub="save-alert"]').first.text_content() == 'Alert me on these filters'
    assert 'Whop notifications' in p.locator('#hub-alerts-body').inner_text()
    assert 'Live highlight · this visit' in p.locator('#hub-alerts-body').inner_text()
    assert 'Line alerts' not in p.locator('#u-hub').inner_text()
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
@pytest.mark.parametrize('persona', [False, True])
def test_genie_persona_intro(browser, env, monkeypatch, size, persona):
    monkeypatch.setenv('SCOUT_GENIE', '1')
    if persona:
        monkeypatch.setenv('SCOUT_GENIE_PERSONA', '1')
    else:
        monkeypatch.delenv('SCOUT_GENIE_PERSONA', raising=False)
    c = context(browser, env, size)
    try:
        p = hub(c, env, size)
        p.locator('#hub-rows tr[data-record]').first.locator('.h-player').click()
        p.wait_for_selector('#hub-card:not([hidden]) [data-hub="card-tab"][data-id="ask"]', timeout=20000)
        p.locator('[data-hub="card-tab"][data-id="ask"]').click()
        p.wait_for_selector('#hub-ask')
        intro = p.locator('#hub-card .h-genie-intro').filter(has_text='Ask the Genie')
        assert intro.count() == (1 if persona else 0)
        if persona:
            assert intro.inner_text().startswith('🧞') and 'never a made-up stat' in intro.inner_text()
        p.locator('#hub-ask-q').fill('What is the projection?')
        p.locator('#hub-ask button[type=submit]').click()
        p.wait_for_function("document.querySelector('#hub-ask-out') && document.querySelector('#hub-ask-out').innerText.includes('projection')", timeout=20000)
        out = p.locator('#hub-ask-out').inner_text()
        assert ('🧞' in out) is persona
        shot(p, 'genie_persona' if persona else 'genie_plain', size)
        assert not p.errors
    finally:
        c.close()
