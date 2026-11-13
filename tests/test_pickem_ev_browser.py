"""SCOUT_PICKEM_EV in a real browser at 1440 and 390: flag off = no Best prices route, card or link; flag on = the #/ev board with
the app picker (verified payouts: PrizePicks since the owner's 2026-11-05 sign-off, and Underdog), entry picker (need X%), market-priced legs with
expandable books, the hidden-plays banner, the filters drawer and the no-book-price list. Every control at least 40px tall, plain
words only. Real Handler + the isolated fixtures of test_upgrade_browser (no network). Set PICKEM_EV_SHOTS=<folder> to keep
screenshots."""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import workspace_books as wb
import workspace_ev as we
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

SHOTS_DIR = Path(os.environ['PICKEM_EV_SHOTS']) if os.environ.get('PICKEM_EV_SHOTS') else None


def shot(p, name, size):
    if SHOTS_DIR:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        p.screenshot(path=str(SHOTS_DIR / f"{name}_{size['width']}.png"), full_page=True)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def nba(pkey, name, stat, label, line, app, p_over, books, mult=None):
    start = iso(time.time() + 3 * 3600)
    r = {"sport": "nba", "key": f"nba.e1.{pkey}.{stat}.full_game.{line}",
         "event": {"id": "e1", "label": "Lakers at Celtics", "start_utc": start, "lock_utc": start, "status": "scheduled"},
         "player": {"key": pkey, "name": name, "team": "LAL"},
         "market": {"stat": stat, "stat_label": label, "period": "full_game", "period_label": "Full game", "line_type": "standard"},
         "offer": {"platform": app, "platform_label": "PrizePicks" if app == "prizepicks" else "Underdog", "offer_id": f"{app[:2]}-{pkey}", "revision": "r1",
                   "line": line, "over": {"offered": True, "payout": mult, "price": None}, "under": {"offered": True, "payout": mult, "price": None}},
         "scout": {"side": None, "p_side": None, "p_over": p_over, "p_under": None if p_over is None else round(1 - p_over, 4), "p_push": 0.0},
         "state": {"kind": "ok"}}
    rows = [{"platform": b, "platform_label": b, "line": ln, "kind": k, "over": o, "under": u, "seen_at": iso(time.time() - age), "_age": age} for b, ln, k, o, u, age in books]
    return r, rows


UD = "underdog"
ITEMS = [
    nba("a", "Austin Reaves", "points", "Points", 20.5, UD, 0.58, [("fanduel", 20.5, "devig", 1.45, 2.85, 40), ("betrivers", 19.5, "devig", 1.40, 3.00, 70),
                                                                             ("kalshi", 20.5, "mid", "0.64 / 0.66", None, 30)]),
    nba("b", "Jaylen Brown", "rebounds", "Rebounds", 6.5, UD, 0.47, [("fanduel", 6.5, "devig", 2.20, 1.68, 50), ("oddsapi:draftkings", 6.5, "devig", 2.15, 1.72, 80)]),
    nba("c", "Derrick White", "assists", "Assists", 4.5, UD, 0.51, [("fanduel", 4.5, "devig", 1.91, 1.91, 60)]),
    nba("d", "Jayson Tatum", "points", "Points", 27.5, UD, None, [("fanduel", 27.5, "devig", 1.5, 2.8, 5000), ("betrivers", 27.5, "devig", 1.5, 2.8, 6000)]),
    nba("e", "LeBron James", "assists", "Assists", 7.5, UD, 0.66, []),
    nba("f", "Austin Reaves", "points", "Points", 20.5, "prizepicks", 0.58, [("fanduel", 20.5, "devig", 1.62, 2.40, 40)]),
]
for _r, _ in ITEMS:                                   # plain 1.0x Underdog picks (the "f" PrizePicks row stays unpriced)
    if _r["offer"]["platform"] == UD:
        _r["offer"]["over"]["payout"] = _r["offer"]["under"]["payout"] = 1.0

SMALL = """() => [...document.querySelectorAll('#hub-ev-body button, #hub-ev-body select, #hub-ev-body input, #hub-ev-body summary, #hub-ev-body .ev-req label, .h-ev-link')]
  .filter(e => e.offsetParent !== null && e.type !== 'checkbox').map(e => [e.id || e.className || e.textContent.trim().slice(0, 30), e.getBoundingClientRect().height])
  .filter(([, h]) => h < 39.5)"""
JARGON = ('+ev', 'kelly', 'shin', 'multiplicative', 'devig', 'vig', 'liquidity', 'spread', 'sharp', 'ev on model', 'model only')


@pytest.fixture
def ev_on(monkeypatch):
    monkeypatch.setenv('SCOUT_PICKEM_EV', '1')
    we._FAIR.clear(); we._LEGS.clear()
    for _r, rows in ITEMS:                             # ages count from now, not from when the module was imported: late in a long
        for b in rows:                                 # run the 30 s Kalshi price was past its 4-minute limit (two books needed)
            b["seen_at"] = iso(time.time() - b["_age"])
    body = json.dumps({"records": [r for r, _ in ITEMS]}).encode()
    s = {"body": body, "index": {(r["offer"]["platform"], r["offer"]["offer_id"]): {"books": (rows, []), "st": r["market"]["stat"]} for r, rows in ITEMS}}
    real = wb.records
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r for r, _ in ITEMS], s) if sport == "nba" else real(srv, sport))
    yield
    we._FAIR.clear(); we._LEGS.clear()


def ev_page(ctx, env, size=None):
    p = open_page(ctx, env, '/#scout/ev', size)
    p.wait_for_selector('#hub-ev-body #ev-entry', timeout=30000)      # the default view may list no leg (two independent books needed)
    return p


def test_flag_off_has_no_ev_route_card_or_link(ctx, env, monkeypatch):
    monkeypatch.delenv('SCOUT_PICKEM_EV', raising=False)
    p = open_page(ctx, env, '/#scout/tools')
    p.wait_for_selector('#hub-tools-body .h-tool-grid', timeout=30000)
    assert p.locator('#hub-ev-section').count() == 0 and p.locator('.h-ev-link').count() == 0
    assert 'Best prices' not in p.locator('#hub-tools-body').inner_text()
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_ev_board(browser, env, ev_on, size):
    c = context(browser, env, size)
    try:
        p = ev_page(c, env, size)
        # both verified apps are offered (owner sign-off 2026-11-05 for the PrizePicks help-center table). PrizePicks opens on the
        # entry that needs the lowest chance per leg (6-pick Flex), with the needed chance shown (owner 2026-11-12)
        assert p.locator('[data-ev="app"]').count() == 2 and p.locator('[data-ev="app"][data-id="prizepicks"]').count() == 1
        assert 'each leg needs 54.2%' in p.locator('#ev-entry').inner_text()
        assert p.evaluate("document.querySelector('#ev-entry').value") == 'flex-6'
        assert 'PrizePicks Flex 6-pick pays 25x all correct' in p.locator('#hub-ev-body').inner_text()
        assert p.locator('#hub-ev-body .ev-row').count() == 0                # its one leg rests on a single book: not in the default view
        assert 'Flex 2-pick' not in p.locator('#ev-entry').inner_text()          # not an entry the PrizePicks app offers
        shot(p, 'ev_board_prizepicks', size)
        p.locator('[data-ev="app"][data-id="underdog"]').click()
        p.wait_for_function("document.querySelector('#hub-ev-body').innerHTML.includes('need 53.5%')", timeout=30000)
        p.wait_for_selector('#hub-ev-body .ev-row', timeout=30000)
        txt = p.locator('#hub-ev-body').inner_text()
        assert 'Best prices' in txt and 'need 53.5%' in p.locator('#hub-ev-body').inner_html()
        assert '6 plays hidden: 4 with fewer independent books than the minimum, 2 with only old prices.' in txt
        assert 'Underdog Standard 2-pick pays 3.5x, read from its help center on 2026-10-23.' in txt
        assert p.evaluate("document.querySelector('#ev-entry').value").startswith('standard-')
        # Underdog standard 2: Reaves Over beats the entry against the market (shown beside Scout's chance); the toggle hides the rest
        rows = p.locator('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row')
        assert rows.count() >= 1 and 'Austin Reaves' in rows.first.inner_text()
        assert not any(w in txt.lower() for w in ('research estimate', 'provisional', 'not validated', 'lock of'))
        shot(p, 'ev_board', size)
        rows.first.locator('[data-ev="books"]').click()
        p.wait_for_selector('#hub-ev-body .ev-src table')
        src = p.locator('#hub-ev-body .ev-src').first.inner_text()
        priced, _, left = src.partition('Left out')
        assert 'FanDuel' in priced and 'Kalshi' in priced and 'read at' not in src and 'BetRivers' not in priced   # exact line, two books
        # BetRivers' rules were read on 2026-11-12, so its quote on another line is listed as left out (never priced)
        assert 'BetRivers 19.5: on another line' in left
        assert '2 books' in rows.first.inner_text()
        shot(p, 'ev_board_books', size)
        p.locator('#hub-ev-body .ev-drawer summary').click()
        assert p.locator('#ev-preset').count() == 1 and p.locator('#ev-method').count() == 0 and p.locator('#ev-min_books').count() == 1
        assert p.locator('[data-ev-req]').count() >= 2 and p.locator('#ev-lo').count() == 1
        shot(p, 'ev_board_filters', size)
        assert p.evaluate(SMALL) == []                                     # every control at least 40px tall
        low = p.locator('#hub-ev-body').inner_text().lower()
        assert not [w for w in JARGON if w in low], [w for w in JARGON if w in low]
        p.locator('[data-ev="evonly"]').first.click()
        p.wait_for_function("document.querySelectorAll('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row').length >= 2")
        # the member lowers the filter to one book: those legs appear, each marked "1 book"
        p.select_option('#ev-min_books', '1')
        p.wait_for_function("document.querySelectorAll('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row').length >= 6")
        one = p.locator('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row', has_text='Derrick White').first
        assert '1 book' in one.inner_text() and '1 book is enough' in p.locator('#hub-ev-body').inner_text()
        p.locator('[data-ev="model"]').click()
        p.wait_for_selector('#hub-ev-body .ev-list-model .ev-row')
        assert 'No book price' in p.locator('#hub-ev-body .ev-list-model').inner_text()
        assert 'LeBron James' in p.locator('#hub-ev-body .ev-list-model').inner_text()
        shot(p, 'ev_board_model_only', size)
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_tools_card_and_board_link_open_the_ev_route(ctx, env, ev_on):
    p = open_page(ctx, env, '/#scout/tools')
    p.wait_for_selector('#hub-tools-body .h-tool-grid', timeout=30000)
    p.locator('#hub-tools-body [data-hub="section"][data-id="ev"]').click()
    p.wait_for_selector('#hub-ev-body #ev-entry', timeout=30000)
    assert p.evaluate('location.hash').endswith('ev')
    assert p.locator('.h-ev-link').count() == 1
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_best_prices_link_is_styled_sized_and_fits(browser, env, ev_on, size):
    c = context(browser, env, size)
    try:
        p = open_page(c, env, '/#scout/board?sport=cs2', size)
        p.wait_for_selector('.h-ev-link', timeout=30000)
        box = p.evaluate("""() => {const e=document.querySelector('.h-ev-link'), r=e.getBoundingClientRect(), s=getComputedStyle(e);
          return {h: r.height, w: r.width, right: r.right, left: r.left, vw: innerWidth, text: e.textContent.trim(), border: s.borderTopStyle}}""")
        assert box['h'] >= 40 and box['w'] >= 40 and box['left'] >= 0 and box['right'] <= box['vw'], box
        assert box['text'] == 'EV' and box['border'] == 'solid', box
        no_h_scroll(p)
        shot(p, 'ev_link', size)
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_paging_searches_all_rows_not_just_the_first_page(browser, env, ev_on, monkeypatch, size):
    original = we.build
    def many(*a, **kw):
        d = original(*a, **kw)
        seed = original(a[0], we.params({"app": "underdog", "min_books": "1"}, []), a[2])["rows"][0]
        d["rows"] = [dict(seed, key=f"coverage-{i}", player=f"Coverage Player {i:03}", ev=.05) for i in range(451)]
        d["rows_total"] = 451
        return d
    monkeypatch.setattr(we, "build", many)
    c = context(browser, env, size)
    try:
        p = ev_page(c, env, size)
        rows = p.locator('section[aria-label="Market priced legs"] .ev-row')
        assert rows.count() == 50
        p.locator('[data-ev="page"][data-kind="market"][data-step="1"]').click()
        assert 'Coverage Player 050' in rows.first.inner_text()
        p.locator('#ev-q').fill('Coverage Player 450')
        assert rows.count() == 1 and 'Coverage Player 450' in rows.first.inner_text()
        p.locator('#ev-q').fill('')
        assert rows.count() == 50 and 'Coverage Player 000' in rows.first.inner_text()
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()
