"""SCOUT_BOARD_H2H (default off, owner on): H2H + Season columns, 7-tone cells, start countdown, Edge vs the Underdog standard
break-even and the "Current lines only" empty state. Flag off: the board, /me and the sheets carry none of it."""
import json

import re

import pytest

import hltv
import workspace_api
import workspace_h2h
import workspace_lol
import workspace_sports
from gradecore import payouts
from tests.test_upgrade_gate import get, series
from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE  # noqa: F401 - the real Handler, four sports

SER = [{"date": "2026-10-28", "opponent": "Norrland", "value": 40}, {"date": "2026-10-20", "opponent": "Norrland Academy", "value": 50},
       {"date": "2026-09-01", "opponent": "norrland", "value": 30}, {"date": "2025-12-30", "opponent": "Norrland", "value": 38.5},
       {"date": "2026-08-01", "opponent": "Other", "value": 20}]


# ---------------------------------------------------------------------------------------------------------------- counts
def test_h2h_counts_exact_opponent_only_and_season_is_the_calendar_year():
    h = workspace_h2h.board_h2h(SER, "Norrland", 38.5)
    assert h == {"over": 1, "under": 1, "push": 1, "n": 3, "opponent": "Norrland"}      # academy never counts as the main team
    assert workspace_h2h.board_h2h(SER, "Nobody", 38.5) is None and workspace_h2h.board_h2h(SER, "", 38.5) is None
    assert workspace_h2h.board_h2h(SER, "Norrland", None) is None
    s = workspace_h2h.board_season(SER, 38.5, year=2026)
    assert s == {"over": 2, "under": 2, "push": 0, "n": 4, "season": "2026"}
    assert workspace_h2h.board_season(SER, 38.5, year=2024) is None


def test_flag_reads_the_environment(monkeypatch):
    monkeypatch.delenv("SCOUT_BOARD_H2H", raising=False)
    assert workspace_h2h.board_enabled() is False
    monkeypatch.setenv("SCOUT_BOARD_H2H", "0")
    assert workspace_h2h.board_enabled() is False
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    assert workspace_h2h.board_enabled() is True


# ---------------------------------------------------------------------------------------------------------------- CS2 server
def _me(base):
    return json.loads(get(base, "/api/upgrade/me", "good-u1")[1])


def _hits(base):
    return json.loads(get(base, "/api/upgrade/hits", "good-u1")[1])["rows"]


def test_cs2_flag_off_keeps_me_and_hits_as_before(env, monkeypatch):
    monkeypatch.delenv("SCOUT_BOARD_H2H", raising=False)
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: series([24] * 20))
    assert "board_h2h" not in _me(env.url)["features"]
    rows = _hits(env.url)
    assert rows and all(set(r) == {"line", "l5", "l20"} for r in rows.values())


def test_cs2_flag_on_adds_h2h_season_and_the_ud_break_even(env, monkeypatch):
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    vals = series([24] * 20)
    for r in vals:                                                  # every third match against the fixture's opponents
        if int(r["match_id"][2:]) % 3 == 0:
            r["opponent"] = "Norrland"
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: vals)
    f = _me(env.url)["features"]["board_h2h"]
    assert f["ud_standard_break_even"] == pytest.approx(payouts.break_even("underdog", "standard", 2)) == pytest.approx(3.5 ** -0.5)
    assert f["source"] == payouts.UD_SOURCE and f["read_at"] == payouts.UD_READ_AT       # the break-even names its published source
    rows = _hits(env.url)
    assert rows and all({"h2h", "season"} <= set(r) for r in rows.values())
    with_h2h = [r for r in rows.values() if r["h2h"]]
    assert with_h2h and all(r["h2h"]["n"] == 7 and r["h2h"]["opponent"] == "Norrland" for r in with_h2h)
    assert all(r["season"]["season"] == "2026" and r["season"]["n"] == 20 for r in rows.values() if r["season"])


# ---------------------------------------------------------------------------------------------------------------- LoL record
def _lol_x():
    return {"scope": "Maps 1-2", "pick_side": "Over", "player": "Ana", "team": "A", "opponent": "Norrland", "stat": "kills", "line": 38.5,
            "platform": "underdog", "key": "k1", "sides": [{"side": "Over", "offered": True, "payout": 1.0}], "p_over": .6, "p_under": .4}


def test_lol_history_gains_h2h_and_season_only_with_the_flag(monkeypatch):
    monkeypatch.delenv("SCOUT_BOARD_H2H", raising=False)
    off = workspace_lol.record_of(_lol_x(), {}, SER)
    assert "h2h" not in off["history"] and "season" not in off["history"]
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    on = workspace_lol.record_of(_lol_x(), {}, SER)
    assert on["history"]["h2h"]["n"] == 3 and on["history"]["season"]["season"]
    assert {k: v for k, v in on.items() if k != "history"} == {k: v for k, v in off.items() if k != "history"}   # nothing else moves
    assert {k: v for k, v in on["history"].items() if k not in ("h2h", "season")} == off["history"]


# ---------------------------------------------------------------------------------------------------------------- NBA / NFL record
def test_sports_record_reads_the_sheet_counts_only_with_the_flag():
    # the sheet side (sports/board_view.h2h_season_lines, sheets.py) is tested in sports/tests/test_board_h2h_sheets.py
    view = {"linesH2h": {"20.5": [1, 1, 1, 3]}, "linesSeason": {"20.5": [1, 1, 0, 2]}, "season": 2026}
    cols = workspace_sports._board_columns(view, 20.5, "BOS")
    assert cols["h2h"] == {"over": 1, "under": 1, "push": 1, "n": 3, "opponent": "BOS"}
    assert cols["season"] == {"over": 1, "under": 1, "push": 0, "n": 2, "season": "2026"}
    assert workspace_sports._board_columns({}, 20.5, "BOS") == {"h2h": None, "season": None}
    assert workspace_sports._board_columns(view, None, "BOS") == {"h2h": None, "season": None}


# ---------------------------------------------------------------------------------------------------------------- browser
def _hub(ctx, env, size=None):
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    return p


def test_board_flag_off_has_no_new_columns(ctx, env, monkeypatch):
    monkeypatch.delenv("SCOUT_BOARD_H2H", raising=False)
    p = _hub(ctx, env)
    assert p.locator('#hub-table thead th').count() == 9
    assert p.locator('#hub-table thead .h-strip-head span').all_inner_texts() == ['L5', 'L10', 'L20']
    assert p.locator('#u-hub.h-cols').count() == 0
    assert p.locator('.h-s-h2h, .h-s-season, .h-c-edge').count() == 0
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
    assert p.locator('#hub-rows .h-detail-row').count() == 0                            # the card is a panel, never a row inside the table
    p.locator('#hub-detail [data-hub=card-close]').click()
    p.locator('#hub-q').fill('zzzz-no-such-player')
    assert 'lines hidden by filters' in p.locator('#hub-empty').inner_text()
    p.locator('#hub-empty [data-hub=clear-filters]').click()
    assert p.locator('#hub-q').input_value() == '' and p.locator('#hub-rows tr[data-record]').count() > 0
    assert p.locator('#hub-empty .h-empty-reasons').count() == 0
    assert p.errors == []


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_board_flag_on_columns_countdown_edge_and_tones(ctx, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    maps = [{"match_id": f"hltv-{i}", "number": n, "date": f"2026-10-{28 - i:02d}", "kills": 24 if i % 5 < 3 else 15, "headshots": 8,
             "opponent": "Norrland" if i % 2 == 0 else f"Team{i}", "map": "Mirage"} for i in range(20) for n in (1, 2)]
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: maps)

    def passing_gate(srv):            # every fixture CS2 cell passes the slip's settled-results check (review 2026-11-04)
        import time as _t
        workspace_api.snapshot(srv)
        keys = {f"{x.get('graded_model')}|{x.get('prop')}|{x.get('scope') or '12'}" for x in (workspace_api._SNAP["index"] or {}).values()}
        band = {"lo": 0.0, "hi": 1.0, "printed": 0.6, "observed": 0.6, "ci": [0.5, 0.7], "picks": 400}
        return {"at_ts": _t.time() - 60, "cells": {k: {"matches": 400, "bands": [band]} for k in keys}}
    monkeypatch.setattr(workspace_api, "_gate", passing_gate)
    workspace_api.reset(); workspace_lol.reset(); workspace_sports.reset()
    p = _hub(ctx, env, size)
    if size is DESK:
        heads = [h.strip() for h in p.locator('#hub-table thead th').all_text_contents()]       # raw text; CSS uppercases it
        assert len(heads) == 10 and 'Edge vs break-even' in heads                                    # a visible row has an edge, so the column shows
        assert p.locator('#hub-table thead .h-strip-head span').all_inner_texts() == ['L5', 'L10', 'L20', 'H2H', 'SZN']
    assert p.locator('#u-hub.h-cols').count() == 1
    rows = p.locator('#hub-rows tr[data-record]')
    assert rows.count() and p.locator('#hub-rows tr[data-record] .h-s-h2h').count() == rows.count()
    assert p.locator('#hub-rows tr[data-record] .h-s-season').count() == rows.count()
    # the shared CS2 pass fills H2H against the fixture opponent (Norrland: 10 of the 20 matches, newest 10 meetings)
    p.locator('#hub-q').fill('vargo')
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record^=cs2] .h-s-h2h .h-hit')].some(e=>/last 10 meetings/.test(e.title))", timeout=15000)
    h2h = p.locator('#hub-rows tr[data-record^=cs2] .h-s-h2h .h-hit').first
    assert 'meetings with Norrland' in h2h.get_attribute('title') and 'h-tone-' in h2h.get_attribute('class')
    assert '2026 season' in p.locator('#hub-rows tr[data-record^=cs2] .h-s-season .h-hit').first.get_attribute('title')
    p.locator('#hub-sort').select_option('start')                 # countdowns sit on the start-time sort's game header rows
    cd = p.locator('#hub-rows .h-countdown').first
    assert cd.inner_text().startswith('in ') and 'h' in cd.inner_text()             # the fixture starts in about three hours
    p.locator('#hub-q').fill('')
    # Edge (owner 2026-11-05): the row's chance against Underdog's published per-pick break-even on every sport's Underdog standard
    # line; with the parity flags off a PrizePicks row carries no break-even, so its cell stays empty
    for i in range(rows.count()):
        r = rows.nth(i)
        rid = r.get_attribute('data-record')
        if '|prizepicks|' in rid:
            assert r.locator('td.h-c-edge .h-edge').inner_text() == '—', rid
    ud = p.locator('#hub-rows tr[data-record*="|underdog|"] td.h-c-edge .h-edge:not(.empty)')
    assert ud.count() >= 1 and 'break-even' in ud.first.get_attribute('title') and ud.first.inner_text().endswith('pts')
    assert all(re.fullmatch(r'[+−±]\d+\.\d', t.replace('pts', '').strip()) for t in ud.all_inner_texts())
    assert p.locator('#hub-rows .h-edge.negative').count() == 0                     # coloured only when above zero
    if size is DESK:
        assert "Goblin, demon and boosted lines publish no fixed per-pick payout" in p.locator('#hub-table thead .h-c-edge').get_attribute('title')
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-sort').select_option('edge')
        first = [e for e in p.locator('#hub-rows tr[data-record] td.h-c-edge .h-edge').all_inner_texts()][:3]
        assert first and all(x != '—' for x in first) and p.locator('#hub-sort-note').inner_text() == ''
        p.locator('#hub-q').fill('vargo')
        book = p.locator('#hub-platform [data-id=prizepicks]')
        book.click()                                                            # no line on screen has an edge: start-time order, said so
        assert 'sorted by start time' in p.locator('#hub-sort-note').text_content()
        assert p.locator('#hub-rows tr.h-game-row').count() >= 1
        p.locator('#hub-platform [data-id=all]').click(); p.locator('#hub-q').fill('')
        p.locator('#hub-filters-toggle').click(); p.locator('#hub-sort').select_option('start'); p.keyboard.press('Escape')
    assert p.locator('#hub-rows .h-hit:not(.empty):not(.h-hit-wait):not(.h-hit-both)').count() == \
        p.locator('#hub-rows .h-hit[class*="h-tone-"]').count()                              # every sided cell takes one of the 7 tones
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision')
    no_h_scroll(p)
    assert p.errors == []


def test_current_lines_only_empty_state_counts_each_reason(ctx, env, monkeypatch):
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    workspace_lol.reset(); workspace_sports.reset()
    p = _hub(ctx, env)
    # every CS2 match starts now: no current CS2 line is left, and the empty state says how many were left off and why
    p.evaluate("""() => { const b=document.querySelector('[data-hub=sport][data-id=cs2]'); b.click(); }""")
    p.wait_for_selector('#hub-rows tr[data-record^=cs2]')
    n = p.locator('#hub-rows tr[data-record^=cs2]').count()
    p.evaluate("""() => { const real=Date.now; Date.now=()=>real()+86400000*3; }""")      # three days on: every fixture match has started
    p.locator('#hub-q').fill('a')
    p.locator('#hub-q').fill('')
    # 2026-11-05 structure review: no current line left means the plain fact and a Refresh, not a list of statuses
    p.wait_for_selector('#hub-empty [data-hub=refresh]')
    text = p.locator('#hub-empty').inner_text()
    assert 'No current lines' in text and 'hidden by filters' not in text and n > 0
    assert p.locator('#hub-empty .h-empty-reasons').count() == 0
    no_h_scroll(p)
    assert p.errors == []



# ---------------------------------------------------------------------------------------------------- review 2026-11-04 fixes
def test_board_edge_cells_come_from_the_slip_gate_and_never_break_me(env, monkeypatch):
    """The Edge column reads the bands the slip's EV check passes (rules.edge_cells), keyed like the hub row (tag|stat|period)."""
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    import time as _t
    gate = {"at_ts": _t.time() - 600, "cells": {"T|kills|12": {"matches": 320, "bands": [
        {"lo": 0.6, "hi": 0.65, "printed": 0.62, "observed": 0.61, "ci": [0.58, 0.66], "picks": 320},
        {"lo": 0.65, "hi": 0.7, "printed": 0.67, "observed": 0.6, "ci": [0.55, 0.65], "picks": 320}]},
        "T|kills|1": {"matches": 100, "bands": [{"lo": 0.6, "hi": 0.65, "printed": 0.62, "observed": 0.62, "ci": None, "picks": 10}]}}}
    monkeypatch.setattr(workspace_api, "_gate", lambda srv: gate)
    f = _me(env.url)["features"]["board_h2h"]
    assert f["edge_cells"] == {"T|kills|maps12": [[0.6, 0.65]]} and f["min_chance"] == 0.57
    monkeypatch.setattr(workspace_api, "_gate", lambda srv: 1 / 0)
    assert _me(env.url)["features"]["board_h2h"]["edge_cells"] == {}            # a failed read: no edge anywhere


def test_hub_edge_cell_requires_the_slip_gate_and_the_empty_state_names_no_status():
    from pathlib import Path
    js = (Path(workspace_api.__file__).resolve().parent / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    body = js[js.index("function edgeValue(r)"):js.index("/* start countdown")]
    # owner 2026-11-05: chance vs the published per-pick break-even; Underdog standard at 1.0x only; letters (hold) do not enter it
    assert "r.offer.platform!=='underdog'" in body and "r.market.line_type!=='standard'" in body and "Math.abs(mult-1)>1e-9" in body
    assert "s.hold" not in body and "edgeOk" not in js
    empty = js[js.index("function emptyReasons()"):js.index("function installCols()")]
    for banned in ("Board refresh failed", "Source not updated", "'Held'", "unavailableReason"):
        assert banned not in empty
