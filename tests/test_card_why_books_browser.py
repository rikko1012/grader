"""card-why (owner 2026-11-06: "why are dabble and other books not showing L5/10/20 but other books have them"): every extra book's
rows show L5 / L10 / L20 on the board and open the full card with "Why Scout leans" - Sleeper and Dabble on CS2 AND on NBA, at the
board's own line and at a different line (NBA: counted from the player's saved game log, the same file the card reads).
Real Handler + fixtures, 1440 and 390. AUDIT_SHOTS=<folder> keeps the screenshots."""
import os
import time
from pathlib import Path

import pytest

import book_providers as bp
import server
import workspace_books as wb
from tests.test_audit_esports_1105 import _cs2_row
from tests.test_phase4_browser import lines_read, page, SHOWN_BOOKS
from tests.test_pickem_readers import event_of
from tests.test_upgrade_browser import in_sheet, close_sheet, browser, env, context, no_h_scroll, DESK, PHONE  # noqa: F401

SHOTS = Path(os.environ['AUDIT_SHOTS']) if os.environ.get('AUDIT_SHOTS') else None


def shot(p, name, size):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{size['width']}.png"))


def offer(book, sport, name, team, line, stat, period, sid, price=False):
    now = time.time()
    side = (lambda v: {"offered": True, "payout": None, "price": v}) if price else (lambda v: {"offered": True, "payout": v, "price": None})
    return {"provider": book, "book": book, "sport": sport, "raw_stat": stat, "stat": stat, "period": period, "subject_id": sid,
            "player": name, "player_norm": bp.norm(name), "team": team, "team_norm": bp.norm(team), "game_id": "g" + sid, "line": line,
            "over": side(1.85), "under": side(1.95), "source_at": now - 60, "observed_at": now}


@pytest.fixture
def books(env, monkeypatch):
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, 'start', lambda d: False)
    for k in ('SCOUT_BOOK_SLEEPER', 'SCOUT_BOOK_DABBLE', 'SCOUT_BOARD_H2H', 'SCOUT_WORKSPACE_V2'):
        monkeypatch.setenv(k, '1')
    row = _cs2_row(41.0, 6.0, 38.5)
    for bk in ('underdog', 'prizepicks'):
        x = env.by('vargo', bk)
        x.update(p=row['p'], side=row['side'], model_outcomes=row['model_outcomes'])
        server.BOARD[x['id']] = {'dist': row['dist']}
    sl = [offer('sleeper', 'cs2', 'vargo', 'Halcyon', 37.5, 'kills', 'maps12', '1'), offer('sleeper', 'cs2', 'brandr', 'Norrland', 34.5, 'kills', 'maps12', '2'),
          offer('sleeper', 'nba', 'Reed Lean', 'CHI', 7.5, 'assists', 'full_game', '3'), offer('sleeper', 'nba', 'Karl Anders', 'NYK', 20.5, 'points', 'full_game', '4')]
    db = [offer('dabble', 'cs2', 'vargo', 'Halcyon', 38.5, 'kills', 'maps12', '5', price=True), offer('dabble', 'cs2', 'brandr', 'Norrland', 33.5, 'kills', 'maps12', '6', price=True),
          offer('dabble', 'nba', 'Reed Lean', 'CHI', 5.5, 'assists', 'full_game', '7', price=True), offer('dabble', 'nba', 'Karl Anders', 'NYK', 20.5, 'points', 'full_game', '8', price=True)]
    real = wb.records

    def with_events(srv, sport):                    # Dabble lines carry their fixture's teams and start: copy the board match's
        recs, s = real(srv, sport)
        for o in db:
            rec = next((r for r in recs or () if r['player']['name'] == o['player'] and r['sport'] == o['sport']), None)
            if rec:
                o.update(event_of(rec), team=rec['player']['team'], team_norm=bp.norm(rec['player']['team']))
        return recs, s
    monkeypatch.setattr(wb, 'records', with_events)
    bp.state('sleeper').update(at=time.time(), offers=sl)
    bp.state('dabble').update(at=time.time(), offers=db)
    yield env
    bp.reset(); wb.reset()


def cells(p):
    return p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr[data-record]')].map(tr => ({name: tr.querySelector('.h-player b').innerText,
        line: tr.querySelector('td.h-c-line b').firstChild.textContent.trim(),
        hits: Object.fromEntries(['l5','l10','l20','h2h','season'].map(w => [w, tr.querySelector('.h-s-' + w + ' .h-hit')?.innerText.trim() ?? null]))}))""")


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
@pytest.mark.parametrize('sp,book', [('cs2', 'sleeper'), ('cs2', 'dabble'), ('nba', 'sleeper'), ('nba', 'dabble')])
def test_every_extra_book_row_has_hit_rates_and_the_why_card(browser, books, sp, book, size):
    c = context(browser, books, size)
    try:
        p = page(c, books)
        lines_read(p, '/#all-sports')
        p.locator(f'#hub-sports [data-id={sp}]').click()
        p.wait_for_function(f"{SHOWN_BOOKS}.includes('{book}')", timeout=20000)
        in_sheet(p, f'#hub-platform [data-hub="book"][data-id="{book}"]').click(); close_sheet(p)
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length===2", timeout=20000)
        p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
        got = cells(p)
        for r in got:                                 # L5 / L10 / L20 on every row, at the book's own line
            for w in ('l5', 'l10', 'l20'):
                assert r['hits'][w] not in (None, '—'), (book, sp, r)
        no_h_scroll(p)
        shot(p, f'why-{sp}-{book}-board', size)
        p.locator('#hub-rows tr[data-record] .h-add').first.click()           # Info -> the full research card, view only
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-why', timeout=20000)
        p.wait_for_selector('#hub-detail .h-history-summary', timeout=20000)
        d = p.locator('#hub-detail')
        assert d.locator('.h-why .h-why-list li').count() >= 1 and d.locator('.h-extra-detail').count() == 1
        assert p.locator('#hub-decision [data-hub="detail-add"]').count() == 0 and 'View only' in p.locator('#hub-decision').inner_text()
        # the Why section is first, then this book's own line, then the tiles counted at that line
        order = p.evaluate("[...document.querySelector('#hub-card-body').children].map(e => e.className.split(' ')[0])")
        assert order[:3] == ['h-why', 'h-extra-detail', 'h-history-summary'], order
        tiles = d.locator('.h-history-summary .h-tile b').all_inner_texts()
        assert all(t.strip() != '—' for t in tiles[:3]), tiles
        no_h_scroll(p)
        shot(p, f'why-{sp}-{book}-card', size)
        assert p.locator('#hub-slip-count').inner_text() == '0'
        assert not p.errors, p.errors
    finally:
        c.close()
