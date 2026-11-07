"""Audit 2026-11-05 esports group in a real browser at 1440 and 390 (real Handler + isolated fixtures; no network).
B1: an Underdog row's age is the time since Scout's read. A2/B4: a Sleeper line at a different line shows Scout's chance at that line
(from the saved distribution, no letter), hit rates at its own line, and opens the full research card (Overview, Lines, Stats, Ask).
Set AUDIT_SHOTS=<folder> to keep screenshots."""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import book_providers as bp
import server
import underdog
import workspace_books as wb
from tests.test_audit_esports_1105 import _cs2_row
from tests.test_phase4_browser import offer, lines_read, page, SHOWN_BOOKS
from tests.test_upgrade_browser import browser, env, context, no_h_scroll, DESK, PHONE  # noqa: F401

SHOTS = Path(os.environ['AUDIT_SHOTS']) if os.environ.get('AUDIT_SHOTS') else None


def shot(p, name, size):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.screenshot(path=str(SHOTS / f"{name}_{size['width']}.png"))


@pytest.fixture
def setup(env, monkeypatch):
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, 'start', lambda d: False)
    monkeypatch.setenv('SCOUT_BOOK_SLEEPER', '1')
    monkeypatch.setenv('SCOUT_BOARD_H2H', '1')
    row = _cs2_row(41.0, 6.0, 38.5)
    for bk in ('underdog', 'prizepicks'):
        x = env.by('vargo', bk)
        x.update(p=row['p'], side=row['side'], model_outcomes=row['model_outcomes'])
        server.BOARD[x['id']] = {'dist': row['dist'], 'perf_on': True}
    old = (datetime.now(timezone.utc) - timedelta(hours=32)).isoformat(timespec='seconds')
    for r in env.rows:                       # B1: Underdog changed these lines 32 h ago; Scout read them just now
        if r['book'] == 'underdog':
            r['updated_at'] = old
            r['seen_at'] = underdog.stamp()
    bp.state('sleeper').update(at=time.time(), offers=[offer('vargo', 'Halcyon', 37.5), offer('brandr', 'Norrland', 34.5, sid='2')])
    yield env
    bp.reset(); wb.reset()


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_underdog_row_age_is_since_scouts_read(browser, setup, size):
    c = context(browser, setup, size)
    try:
        p = page(c, setup)
        lines_read(p, '/')
        p.locator('#hub-platform [data-hub="book"][data-id="underdog"]').click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length>0", timeout=20000)
        chips = p.locator('#hub-rows .h-c-line small').all_inner_texts()
        assert chips and all(t.startswith('UD') for t in chips)
        assert not any('32h' in t for t in chips), chips                      # the read time, not Underdog's change time
        no_h_scroll(p)
        shot(p, 'b1_underdog_board', size)
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_sleeper_line_has_chance_hits_and_the_full_card(browser, setup, size):
    c = context(browser, setup, size)
    try:
        p = page(c, setup)
        lines_read(p, '/')
        p.wait_for_function(f"{SHOWN_BOOKS}.includes('sleeper')", timeout=20000)
        p.locator('#hub-platform [data-hub="book"][data-id="sleeper"]').click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length===2", timeout=20000)
        v = p.locator('#hub-rows tr[data-record]', has_text='vargo').first
        b = p.locator('#hub-rows tr[data-record]', has_text='brandr').first
        p.wait_for_function("""()=>{const r=[...document.querySelectorAll('#hub-rows tr[data-record]')].find(t=>t.innerText.includes('vargo'));
                                    return r&&!r.querySelector('.h-s-l5 .h-hit.empty')&&!r.querySelector('.h-s-l5 .h-hit-wait');}""", timeout=20000)
        vt = v.inner_text()
        assert 'Chance at this line' in vt and 'Lean' not in vt                # Scout's chance at 37.5, no letter
        # follow-up 2026-11-06: the row label is whole (never cut) and the sort menu names the order on screen (no edges here)
        lab = v.locator('.h-line-chance')
        assert lab.evaluate('e => e.scrollWidth <= e.clientWidth + 1 && getComputedStyle(e).textOverflow !== "ellipsis"')
        assert lab.get_attribute('title') == 'Scout’s chance at this line'
        assert p.locator('#hub-sort').input_value() == 'start' and p.locator('#hub-sort-note').inner_text().strip() == ''
        assert v.locator('.h-s-l5 .h-hit').inner_text() != '—' and v.locator('.h-s-l20 .h-hit').inner_text() != '—'
        assert b.locator('.h-s-l5 .h-hit').inner_text() != '—'                 # same-line row: L5 / L20 too, not only the saved L10
        no_h_scroll(p)
        shot(p, 'a2_sleeper_board', size)
        v.locator('.h-add').click()                                             # Info -> the full research card
        p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-extra-detail')
        d = p.locator('#hub-detail')
        assert 'Sleeper · 37.5' in d.inner_text() and 'chance at this line' in d.inner_text()
        assert p.locator('#hub-detail [role="tab"]').all_inner_texts()[:1] and d.locator('.h-history-chart').count() == 1
        assert 'View only' in p.locator('#hub-decision').inner_text() and p.locator('#hub-decision [data-hub="detail-add"]').count() == 0
        no_h_scroll(p)
        shot(p, 'a2_sleeper_card', size)
        p.locator('#hub-detail [data-hub="card-tab"][data-id="lines"]').click()
        assert 'Sleeper' in d.inner_text() and 'Underdog' in d.inner_text()
        p.locator('#hub-detail [data-hub="card-tab"][data-id="stats"]').click()
        p.locator('#hub-detail [data-hub="card-tab"][data-id="ask"]').click()
        assert d.locator('form, details, p').count() > 0
        shot(p, 'a2_sleeper_card_ask', size)
        assert p.locator('#hub-slip-count').inner_text() == '0'
        assert not p.errors, p.errors
    finally:
        c.close()
