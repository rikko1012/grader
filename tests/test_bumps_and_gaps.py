"""Bumps and line discrepancies (owner 2026-10-19), display only. Underdog openings are tracked per player / stat / maps /
start; the page marks a line bumped from its opening and a line that differs from the other platform's standard line.
Every player and number is a TEST FIXTURE."""
import json
import sqlite3

import pytest

import underdog
from tests.test_audit13_site_ui import needs_node, run_page

START = "2099-10-20T12:00:00Z"


def row(line, scope="12", prop="kills", alt=False, player="Tester One"):
    return {"id": f"{player}-{prop}-{scope}-{line}", "player": player, "prop": prop, "scope": scope, "start": START, "line": line,
            "alt": alt, "higher": 1.0, "lower": 1.0}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(underdog, "_OPEN", {"by": None})
    (tmp_path / ".cache").mkdir()
    return tmp_path / ".cache"


def test_underdog_opening_is_kept_and_a_bump_is_timed(cache):
    a = [row(30.5), row(15.5, scope="1"), row(28.5, alt=True)]
    underdog._opening(a, now="2026-10-19T10:00:00Z")
    assert a[0]["open_line"] == 30.5 and a[0]["moved_at"] is None and "open_line" not in a[2]       # alt lines: never
    b = [row(31.5), row(15.5, scope="1")]
    underdog._opening(b, now="2026-10-19T11:00:00Z")
    assert (b[0]["open_line"], b[0]["moved_at"], b[0]["first_seen"]) == (30.5, "2026-10-19T11:00:00Z", "2026-10-19T10:00:00Z")
    assert b[1]["open_line"] == 15.5 and b[1]["moved_at"] is None                                  # Map 1 kept apart
    underdog._OPEN["by"] = None                                                                     # a restart reads the file
    c = [row(31.5)]
    underdog._opening(c, now="2026-10-19T12:00:00Z")
    assert c[0]["open_line"] == 30.5 and c[0]["moved_at"] == "2026-10-19T11:00:00Z"


def test_seed_from_the_move_log_only_where_the_maps_are_unambiguous(cache):
    con = sqlite3.connect(cache / "ud_moves.db")
    con.execute("CREATE TABLE moves (at TEXT, player TEXT, team TEXT, opponent TEXT, start TEXT, prop TEXT, line REAL, higher REAL, lower REAL, line_id TEXT)")
    for at, pl, prop, line in (("2026-10-19T08:00:00Z", "Tester One", "kills", 29.5), ("2026-10-19T09:00:00Z", "Tester One", "kills", 30.5),
                               ("2026-10-19T08:00:00Z", "Tester Two", "kills", 20.5)):
        con.execute("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?)", (at, pl, "", "", START, prop, line, 1, 1, "x"))
    con.commit(); con.close()
    rows = [row(30.5), row(21.5, player="Tester Two"), row(10.5, scope="1", player="Tester Two")]
    underdog._opening(rows, now="2026-10-19T10:00:00Z")
    assert rows[0]["open_line"] == 29.5 and rows[0]["first_seen"] == "2026-10-19T08:00:00Z"      # one line for that key: seeded
    assert rows[1]["open_line"] == 21.5 and rows[2]["open_line"] == 10.5     # two maps share the logged key: not guessed


def test_opening_never_breaks_the_feed(cache, monkeypatch):
    monkeypatch.setattr(underdog, "_open_path", lambda: (_ for _ in ()).throw(OSError("disk")))
    rows = [row(30.5)]
    underdog._opening(rows)
    assert "open_line" not in rows[0]


@needs_node
def test_board_rows_show_bumps_and_discrepancies(tmp_path):
    out = run_page(tmp_path, """(() => {
      const base = {nick: "p1", player: "p1", prop: "kills", scope: "12", side: "More", p: 0.6, grade: "B", model_grade: "B", units: 1,
                    state: "GRADE", start: "2099-10-20T12:00:00Z", graded_at: new Date().toISOString(), team: "A", opponent: "B",
                    match_id: "hltv:1", odds_type: "standard", alt: false};
      BK.underdog = {rows: [Object.assign({}, base, {id: "u1", book: "underdog", line: 30.5, open_line: 29.5, moved_at: "2026-10-19T11:00:00Z"})], loaded: true, at: 1, total: 1};
      BK.prizepicks = {rows: [Object.assign({}, base, {id: "pp:1", book: "prizepicks", line: 31.5, open_line: 31.5}),
                              Object.assign({}, base, {id: "pp:2", book: "prizepicks", line: 18.5, prop: "hs", open_line: 19.5})], loaded: true, at: 1, total: 2};
      bbook = "all"; bprop = "both"; bside = "both"; bscope = "12"; bview = "all";
      boardRows = viewRows("all"); drawBoard();
      return $("#boardout").innerHTML.split('<div class="brow2 crow"').slice(1);
    })()""")
    u = next(h for h in out if 'data-bl="p1|30.5' in h)
    assert "▲ bumped from 29.5" in u and "≠ PrizePicks 31.5" in u and "For the Over, the lower line is the easier one" in u
    pp = next(h for h in out if 'data-bl="p1|31.5' in h)
    assert "≠ Underdog 30.5" in pp and "bumped" not in pp                                          # opened where it is
    hs = next(h for h in out if 'data-bl="p1|18.5' in h)
    assert "▼ bumped from 19.5" in hs and "≠" not in hs                                             # no Underdog HS line
