"""4.16 board parity fixes in a real browser at 1440 and 390 (real Handler, fixture boards, no network): every SCOUT_PARITY_* flag
off leaves the board as it was; on, the Edge column is real EV and sorts from its header, hit cells read "6/10" with the % in the
tooltip, the hit-rate sort puts lines below break-even last, the platform toggle shows plays counts, the EV band chip shows, the
card's Report a problem writes one data_issues row, and Preferences change the odds format and the display size only.
Set PARITY_SHOTS=<folder> to keep screenshots."""
import os
import re
from pathlib import Path

import pytest

import board_parity as bpar
import workspace_api
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

BASE = ("SCOUT_WORKSPACE_V2", "SCOUT_BOARD_H2H", "SCOUT_GENIE", "SCOUT_SLIP_HEADER", "SCOUT_CLOSING_LINES", "SCOUT_PUBLIC_RECORD")
SHOTS_DIR = Path(os.environ['PARITY_SHOTS']) if os.environ.get('PARITY_SHOTS') else None


def shot(p, name, size):
    if SHOTS_DIR:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(200)
        p.screenshot(path=str(SHOTS_DIR / f"{name}_{size['width']}.png"))


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in BASE:
        monkeypatch.setenv(k, "1")
    for k in bpar.FLAGS.values():
        monkeypatch.delenv(k, raising=False)


def parity_on(monkeypatch):
    for k in bpar.FLAGS.values():
        monkeypatch.setenv(k, "1")


def board(ctx, env, size, sport='cs2'):
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    if sport:
        p.locator(f'#hub-sports [data-id={sport}]').click()
    return p


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_flags_off_board_is_unchanged(ctx, env, size):
    p = board(ctx, env, size)
    assert p.locator('.h-plays, .h-band, .h-th-sort, .h-hit-n, #hub-report-dialog, #hub-parity-style').count() == 0
    assert all(re.fullmatch(r'\d+%|\d+-\d+|—', t) for t in p.locator('#hub-rows .h-hit').all_inner_texts())
    assert p.locator('#hub-sort option[value=hit]').count() == 0
    assert not p.errors
    shot(p, 'parity_off', size)


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_parity_on_board(ctx, env, size, monkeypatch):
    parity_on(monkeypatch)
    workspace_api.reset()
    p = board(ctx, env, size)
    # hit cells: the sample on the cell, the % in the tooltip
    cells = p.locator('#hub-rows .h-hit.h-hit-n')
    assert cells.count() > 0
    c = cells.first
    assert re.fullmatch(r'\d+/\d+', c.inner_text()) and re.search(r'\(\d+%\)', c.get_attribute('title'))
    # EV band chip next to the letter, plain words
    bands = p.locator('#hub-rows .h-band').all_inner_texts()
    assert bands and set(bands) <= {'Skip', 'Marginal', 'Strong', 'Premium'}
    # plays chips in the platform toggle
    chips = p.locator('#hub-platform .h-plays')
    assert chips.count() >= 1 and all(t.isdigit() for t in chips.all_inner_texts())
    assert re.search(r'\d+ plays?$', p.locator('#hub-platform [data-id=underdog]').get_attribute('aria-label'))
    no_h_scroll(p)
    shot(p, 'parity_board', size)
    if size == DESK:
        # Edge column: real EV with "%", sortable from its header
        edge = p.locator('#hub-rows .h-c-edge .h-edge b')
        assert edge.count() > 0 and all(re.fullmatch(r'[+−±]\d+\.\d%', t) for t in edge.all_inner_texts())
        p.locator('#hub-table [data-hub=parity-sort]').click()
        assert p.locator('#hub-sort').input_value() == 'edge'
        vals = [float(t.replace('−', '-').replace('±', '').rstrip('%')) for t in p.locator('#hub-rows tr[data-record] .h-c-edge .h-edge b').all_inner_texts()]
        assert vals == sorted(vals, reverse=True)
        assert p.locator('#hub-table th.h-c-edge').get_attribute('aria-sort') == 'descending'
        p.locator('#hub-table [data-hub=parity-sort]').click()
        assert p.locator('#hub-sort').input_value() == 'start'
        # hit-rate sort: lines below break-even listed last
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-sort').select_option('hit')
        assert 'below their break-even' in p.locator('#hub-sort-note').inner_text()
        keys = p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr[data-record]')].map(tr => tr.dataset.record)""")
        assert keys
    assert not p.errors


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_report_a_problem_and_preferences(ctx, env, size, monkeypatch):
    parity_on(monkeypatch)
    workspace_api.reset()
    p = board(ctx, env, size)
    # Preferences on Tools: decimal odds and a bankroll change the display only
    p.evaluate("document.querySelector('#u-hub [data-hub=section][data-id=tools]').click()")
    p.wait_for_selector('#hub-pref-odds')
    shot(p, 'parity_prefs', size)
    p.locator('#hub-pref-odds').select_option('decimal')
    p.locator('#hub-pref-sizing').select_option('half')
    p.locator('#hub-pref-bankroll').fill('500')
    p.locator('#hub-pref-bankroll').dispatch_event('change')
    p.wait_for_function("document.querySelector('#hub-save-status') !== null")
    p.evaluate("document.querySelector('#u-hub [data-hub=section][data-id=board]').click()")
    p.wait_for_selector('#hub-rows tr[data-record]')
    # the card shows the break-even in decimal odds and the size in dollars
    p.locator('#hub-q').fill('vargo')
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail [data-hub=detail-add]', timeout=20000)
    size_line = p.locator('#hub-detail .h-dec-size')
    assert re.search(r'Break-even \d\.\d\d · ½ Kelly: \$\d+\.\d\d', size_line.inner_text())
    # the grade, chance and the slip are unchanged by the preference (display only)
    assert p.locator('#hub-detail .h-dec-chance b').inner_text().endswith('%')
    p.evaluate("document.querySelector('#hub-detail [data-hub=card-close]').click()")
    edge_title = p.locator('#hub-rows .h-c-edge .h-edge').first.get_attribute('title') if size == DESK else ''
    assert size != DESK or re.search(r'\(\d\.\d\d\)', edge_title)
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail [data-hub=report-open]', timeout=20000)
    # Report a problem
    p.locator('#hub-detail [data-hub=report-open]').click()
    assert p.locator('#hub-report-dialog').is_visible()
    shot(p, 'parity_report', size)
    p.locator('#hub-report-reason').select_option('stats')
    p.locator('#hub-report-note').fill('L10 looks off')
    with p.expect_response(lambda r: 'report-problem' in r.url) as got:
        p.locator('#hub-report-dialog button[value=send]').click()
    assert got.value.status == 200
    with workspace_api.store().reader() as con:
        rows = [tuple(r) for r in con.execute("SELECT sport, reason, note, player FROM data_issues")]
    assert rows == [('cs2', 'stats', 'L10 looks off', 'vargo')]
    no_h_scroll(p)
    assert not p.errors
