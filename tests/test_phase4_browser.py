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
from tests.test_pickem_readers import event_of  # noqa: E402
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

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
        in_sheet(p, '#hub-platform [data-hub="book"][data-id="sleeper"]').click(); close_sheet(p)
        assert p.locator('#hub-platform [data-id="sleeper"]').get_attribute('aria-pressed') == 'true'
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length===2", timeout=20000)
        text = p.locator('#hub-rows').inner_text()
        assert 'SL' in text and 'vargo' in text and 'brandr' in text
        v = p.locator('#hub-rows tr[data-record]', has_text='vargo').first
        b = p.locator('#hub-rows tr[data-record]', has_text='brandr').first
        assert '61' in v.inner_text()                                       # the saved Underdog chance at the same line, unchanged
        assert 'Lean' not in v.inner_text() or 'Lean' in p.locator('#hub-rows').inner_text()
        assert v.locator('.h-add').inner_text() == 'Info'
        v.locator('.h-add').click()                                         # audit 2026-11-05: Info opens the research card (it only toasted)
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
        assert p.locator('#hub-detail [role="tab"]').count() >= 3 and 'L5' in p.locator('#hub-detail').inner_text()
        assert 'View only' in p.locator('#hub-decision').inner_text() and p.locator('#hub-decision [data-hub="detail-add"]').count() == 0
        assert p.locator('#hub-slip-count').inner_text() == '0'            # nothing reached the slip (one book per slip)
        p.locator('#hub-detail [data-hub="card-close"]').click()
        assert 'view only' in p.locator('#hub-health').inner_text().lower()
        b.locator('.h-player').click()
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
        # card-and-chips 2026-11-06 (no status labels): no saved chance = an empty Grader cell and no "Grader's read" row, no sentence
        assert b.locator('td.h-c-scout').inner_text().strip() == '' and 'saved chance' not in b.inner_text()
        detail = p.locator('#hub-detail').inner_text()
        assert '1.80x pick multiplier' in detail and 'No saved chance' not in detail and "Grader's read" not in p.locator('#hub-detail .h-extra-detail').inner_text()
        no_h_scroll(p)
        shot(p, 'board_sleeper', size)
        p.locator('#hub-detail [data-hub="card-close"]').click()
        assert p.locator('#hub-card').is_hidden()
        assert not p.errors
    finally:
        c.close()


SHOWN_BOOKS = "[...document.querySelectorAll('#hub-platform [data-hub=book]')].filter(b=>!b.hidden).map(b=>b.dataset.id)"


def lines_read(p, path):
    """Open the hub and wait for its first /book-lines answer."""
    with p.expect_response(lambda r: '/api/upgrade/book-lines' in r.url, timeout=30000):
        p.goto(p.base + path)
    p.wait_for_selector('#hub-rows tr', timeout=30000)


def page(ctx, env):
    p = ctx.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    p.base = env.url
    return p


def test_book_toggle_stale_feed_hides_the_book_until_lines_return(ctx, env, sleeper):
    """owner 2026-11-05: a book with no lines on the tab is left out of the toggle (no banner, no status label); it returns with its lines."""
    import server
    sleeper['at'] = time.time() - 3600
    p = page(ctx, env)
    lines_read(p, '/#all-sports')
    assert p.evaluate(SHOWN_BOOKS) == ['all', 'prizepicks', 'underdog']
    assert 'older than their freshness limit' not in p.locator('#hub-health').inner_text()
    sleeper['at'] = time.time()
    server._RESULTS.clear()
    with p.expect_response(lambda r: '/api/upgrade/book-lines' in r.url, timeout=30000):
        p.locator('[data-hub="refresh"]').first.click()
    p.wait_for_function(f"{SHOWN_BOOKS}.includes('sleeper')", timeout=20000)
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_book_toggle_follows_the_sport_tab(browser, env, sleeper, monkeypatch, size):
    """CS2 tab: All, PP, UD, SL. LoL tab (Sleeper lists no LoL): no SL. FanDuel / BetRivers (NBA / NFL only) never show on CS2 or LoL,
    and not on All sports for a member without NBA / NFL. A chosen book with no lines on the new tab falls back to All."""
    monkeypatch.setenv('SCOUT_BOOK_FANDUEL', '1')
    monkeypatch.setenv('SCOUT_BOOK_BETRIVERS', '1')
    c = context(browser, env, size)
    try:
        p = page(c, env)
        lines_read(p, '/')
        assert p.evaluate(SHOWN_BOOKS) == ['all', 'prizepicks', 'underdog', 'sleeper']
        shot(p, 'toggle_cs2', size, '#u-hub .h-toolbar')
        in_sheet(p, '#hub-platform [data-id="sleeper"]').click(); close_sheet(p)
        assert p.locator('#hub-platform [data-id="sleeper"]').get_attribute('aria-pressed') == 'true'
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length===2", timeout=20000)
        p.locator('#hub-sports [data-id="lol"]').click()
        p.wait_for_function("document.querySelector('#hub-platform [data-id=\"all\"]').getAttribute('aria-pressed')==='true'", timeout=20000)
        shown = p.evaluate(SHOWN_BOOKS)
        assert 'sleeper' not in shown and 'fanduel' not in shown and 'betrivers' not in shown and shown[0] == 'all'
        assert 'Sleeper lists no lines' not in p.locator('#hub-health').inner_text()
        assert 'could not be loaded' not in p.locator('#hub-health').inner_text()
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=20000)          # the LoL board, not an empty Sleeper board
        shot(p, 'toggle_lol', size, '#u-hub .h-toolbar')
        p.locator('#hub-sports [data-id="all"]').click()
        p.wait_for_function(f"{SHOWN_BOOKS}.includes('sleeper')", timeout=20000)
        tabs = set(p.locator('#hub-sports [data-hub="sport"]').evaluate_all('bs=>bs.map(b=>b.dataset.id)'))
        lines = p.evaluate("fetch('/api/upgrade/book-lines').then(r=>r.json())")['books']
        shown = set(p.evaluate(SHOWN_BOOKS))
        for b in ('fanduel', 'betrivers'):                                      # on All: only with lines in a sport this member has
            assert (b in shown) == any(s in tabs and n > 0 for s, n in lines.get(b, {}).items()), (b, lines, tabs)   # 0 = counted empty
            assert not {'cs2', 'lol'} & set(lines.get(b, {}))
        if not {'nba', 'nfl'} & tabs:
            assert not {'fanduel', 'betrivers'} & shown
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_member_without_nba_nfl_never_sees_fanduel_or_betrivers(browser, env, sleeper, monkeypatch):
    monkeypatch.setenv('SCOUT_BOOK_FANDUEL', '1')
    monkeypatch.setenv('SCOUT_BOOK_BETRIVERS', '1')
    import workspace_sports
    monkeypatch.setattr(workspace_sports, 'on', lambda h, q, owner: False)          # a CS2 + LoL member
    c = context(browser, env, DESK, user='u1')
    try:
        p = page(c, env)
        lines_read(p, '/#all-sports')
        tabs = set(p.locator('#hub-sports [data-hub="sport"]').evaluate_all('bs=>bs.map(b=>b.dataset.id)'))
        assert not {'nba', 'nfl'} & tabs, tabs
        assert p.evaluate(SHOWN_BOOKS) == ['all', 'prizepicks', 'underdog', 'sleeper']
        for path in ('/', '/lol'):
            lines_read(p, path)
            assert not {'fanduel', 'betrivers'} & set(p.evaluate(SHOWN_BOOKS))
        assert not p.errors
    finally:
        c.close()


def test_book_toggle_refuses_a_book_with_no_lines_here(ctx, env, sleeper, monkeypatch):
    """A saved view or an old link with FanDuel on the CS2 tab restores with All books; the action itself is refused."""
    monkeypatch.setenv('SCOUT_BOOK_FANDUEL', '1')
    p = page(ctx, env)
    lines_read(p, '/')
    p.evaluate("document.querySelector('#hub-platform [data-id=\"fanduel\"]').click()")          # hidden, so only a script can press it
    assert p.locator('#hub-platform [data-id="all"]').get_attribute('aria-pressed') == 'true'
    assert 'could not be loaded' not in p.locator('#hub-health').inner_text()
    assert not p.errors


def test_book_toggle_shows_dabble_beside_sleeper_with_its_price(ctx, env, sleeper, monkeypatch):
    """Readers added 2026-11-05: Dabble comes through the same generic toggle, short code DB, view only, its per-side price shown as its own unit (never decimal odds)."""
    monkeypatch.setenv('SCOUT_BOOK_DABBLE', '1')
    rows = [dict(offer('vargo', 'Halcyon', 38.5), provider='dabble', book='dabble', raw_stat='total-kills-map-one-and-map-two', source_at=None,
                 over={"offered": True, "payout": None, "price": 1.91}, under={"offered": True, "payout": None, "price": 2.1}),
            dict(offer('brandr', 'Norrland', 33.5, sid='2'), provider='dabble', book='dabble', raw_stat='total-kills-map-one-and-map-two', source_at=None,
                 over={"offered": True, "payout": None, "price": 2.05}, under={"offered": True, "payout": None, "price": 1.95})]
    real = wb.records

    def with_events(srv, sport):                    # Dabble lines carry their fixture's teams and start: copy the board match's
        recs, s = real(srv, sport)
        for o in rows:
            rec = next((r for r in recs or () if r['player']['name'] == o['player']), None)
            if rec:
                o.update(event_of(rec))
        return recs, s
    monkeypatch.setattr(wb, 'records', with_events)
    bp.state('dabble').update(at=time.time(), offers=rows)
    p = page(ctx, env)
    lines_read(p, '/')
    p.wait_for_function(f"{SHOWN_BOOKS}.includes('dabble')", timeout=20000)
    assert p.evaluate(SHOWN_BOOKS) == ['all', 'prizepicks', 'underdog', 'sleeper', 'dabble']
    in_sheet(p, '#hub-platform [data-hub="book"][data-id="dabble"]').click(); close_sheet(p)
    assert p.locator('#hub-platform [data-id="dabble"]').get_attribute('aria-pressed') == 'true'
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length===2", timeout=20000)
    text = p.locator('#hub-rows').inner_text()
    assert 'DB' in text and 'vargo' in text and 'brandr' in text
    v = p.locator('#hub-rows tr[data-record]', has_text='vargo').first
    assert '61' in v.inner_text() and v.locator('.h-add').inner_text() == 'Info'
    v.locator('.h-add').click()                                             # Info opens the research card, never the slip
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
    assert p.locator('#hub-slip-count').inner_text() == '0'
    p.locator('#hub-detail [data-hub="card-close"]').click()
    b = p.locator('#hub-rows tr[data-record]', has_text='brandr').first
    b.locator('.h-player').click()
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
    detail = p.locator('#hub-detail').inner_text()
    assert 'Dabble price 2.05' in detail and 'not what an entry pays' in detail and '2.05 decimal' not in detail
    assert 'No saved chance' not in detail and "Grader's read" not in p.locator('#hub-detail .h-extra-detail').inner_text()
    no_h_scroll(p)
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
        # the question box spans the card (a classic-page form rule had squeezed it to a few characters at 390)
        q_w, card_w = p.evaluate("[document.getElementById('hub-ask-q').getBoundingClientRect().width, document.getElementById('hub-card-body').getBoundingClientRect().width]")
        assert q_w >= 0.55 * card_w, (q_w, card_w)
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
