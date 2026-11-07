"""SCOUT_LINE_HISTORY (lh-1, workspace_linehist.py): every app's line over time and closing-line markers in the CS2 card.
Formulas on fixed inputs and the incremental store index. The /line hook and the card: tests/test_line_history_browser.py."""
import json
import sqlite3
from pathlib import Path

import pytest

import prizepicks
import workspace_linehist as lh

T0 = 1_800_000_000.0           # a fixed clock


def iso(t):
    return lh._iso(t)


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(prizepicks, "HISTORY", tmp_path / ".cache" / "pp_lines.jsonl")
    (tmp_path / ".cache").mkdir()
    lh.reset()
    yield tmp_path
    lh.reset()


def pp_row(**kw):
    r = {"id": "pp:1", "book": "prizepicks", "player": "vargo", "team": "Halcyon", "opponent": "Norrland", "start": iso(T0 + 3 * 3600),
         "prop": "kills", "scope": "12", "odds_type": "standard", "line": 39.5, "seen_at": iso(T0 - 60)}
    r.update(kw)
    return r


def ud_row(**kw):
    r = {"id": "u1", "book": "underdog", "player": "vargo", "team": "Halcyon", "opponent": "Norrland", "start": iso(T0 + 3 * 3600),
         "prop": "kills", "scope": "12", "line": 38.5, "seen_at": iso(T0 - 30)}
    r.update(kw)
    return r


def write_pp(path, events):
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def write_ud(tmp, rows):
    con = sqlite3.connect(tmp / ".cache" / "ud_moves.db")
    con.execute("CREATE TABLE IF NOT EXISTS moves (at TEXT, player TEXT, team TEXT, opponent TEXT, start TEXT, prop TEXT, line REAL, "
                "higher REAL, lower REAL, line_id TEXT, scope TEXT)")
    con.executemany("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def pp_event(r, at, event, line, **kw):
    return dict({"at": iso(at), "book": "prizepicks", "market": prizepicks.market_key(r), "id": "1", "line": line, "start": r["start"],
                 "prop": r["prop"], "scope": r["scope"], "event": event}, **kw)


# ------------------------------------------------------------------------------------------------ one app's series

def test_prizepicks_open_moves_pull_and_return_in_order(stores):
    r = pp_row(first_seen=iso(T0 - 7200))
    write_pp(prizepicks.HISTORY, [pp_event(r, T0 - 3600, "open", 38.5, open_line=37.5),
                                  pp_event(r, T0 - 1800, "move", 39.5, from_line=38.5),
                                  pp_event(r, T0 - 1700, "id_change", 39.5),
                                  pp_event(r, T0 - 1200, "removed", 39.5),
                                  pp_event(r, T0 - 600, "open", 39.5)])
    lh.refresh(T0, force=True)
    pts = lh.app_series(r, now=T0)
    assert [(p["kind"], p["line"]) for p in pts] == [("open", 37.5), ("move", 38.5), ("move", 39.5), ("pulled", None), ("move", 39.5)]
    assert pts[0]["at"] == iso(T0 - 7200)                        # the scraper's opening line at its first sighting
    assert [p["at"] for p in pts] == sorted(p["at"] for p in pts)


def test_underdog_line_and_payout_changes(stores):
    r = ud_row()
    start = r["start"]
    write_ud(stores, [(iso(T0 - 5000), "Vargo", "Halcyon", "Norrland", start, "kills", 37.5, 1.0, 1.0, "u1", "12"),
                      (iso(T0 - 4000), "vargo", "Halcyon", "Norrland", start, "kills", 37.5, 1.08, 0.92, "u1", "12"),
                      (iso(T0 - 3000), "vargo", "Halcyon", "Norrland", start, "kills", 38.5, 1.0, 1.0, "u1", "12"),
                      (iso(T0 - 2000), "vargo", "Halcyon", "Norrland", start, "kills", 99.5, 1.0, 1.0, "u1", None),    # no scope: never guessed
                      (iso(T0 - 1000), "vargo", "Halcyon", "Norrland", start, "kills", 18.5, 1.0, 1.0, "u9", "1")])    # another maps scope
    lh.refresh(T0, force=True)
    pts = lh.app_series(r, now=T0)
    assert [(p["kind"], p["line"]) for p in pts] == [("open", 37.5), ("payout", 37.5), ("move", 38.5)]
    assert pts[1]["higher"] == 1.08 and pts[1]["lower"] == 0.92


def test_no_stored_moves_uses_the_rows_own_opening_and_last_move(stores):
    r = ud_row(first_seen=iso(T0 - 9000), open_line=36.5, moved_at=iso(T0 - 400))
    lh.refresh(T0, force=True)
    assert [(p["kind"], p["line"], p["at"]) for p in lh.app_series(r, now=T0)] == [("open", 36.5, iso(T0 - 9000)), ("move", 38.5, iso(T0 - 400))]
    bare = lh.app_series(ud_row(id="u2", player="nobody"), now=T0)
    assert [(p["kind"], p["line"]) for p in bare] == [("seen", 38.5)]        # nothing stored: today's line alone, never called an opening


def test_current_line_is_appended_when_the_store_lags(stores):
    r = pp_row(line=40.5)
    write_pp(prizepicks.HISTORY, [pp_event(r, T0 - 3600, "open", 39.5)])
    lh.refresh(T0, force=True)
    assert [(p["kind"], p["line"]) for p in lh.app_series(r, now=T0)] == [("open", 39.5), ("move", 40.5)]


# ------------------------------------------------------------------------------------------------ the index

def test_index_reads_only_new_bytes_defers_half_lines_and_drops_old_offers(stores):
    r = pp_row()
    old = pp_row(start=iso(T0 - 3 * 86400), player="gone")
    write_pp(prizepicks.HISTORY, [pp_event(r, T0 - 3600, "open", 39.5), pp_event(old, T0 - 3 * 86400, "open", 20.5)])
    lh.refresh(T0, force=True)
    pos = lh._IDX["pp_pos"]
    assert pos == prizepicks.HISTORY.stat().st_size
    assert prizepicks.market_key(old) not in lh._IDX["pp"]
    with open(prizepicks.HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(pp_event(r, T0 - 60, "move", 40.5))[:30])          # a line still being written
    lh.refresh(T0 + 1, force=True)
    assert lh._IDX["pp_pos"] == pos
    lh.refresh(T0 + 2)                                                        # inside REFRESH_S: no read at all
    assert lh._IDX["at"] == T0 + 1
    with open(prizepicks.HISTORY, "w", encoding="utf-8") as f:               # replaced log: the index starts again
        f.write(json.dumps(pp_event(r, T0 - 10, "open", 41.5)) + "\n")
    lh.refresh(T0 + 3, force=True)
    assert [e["line"] for e in lh._IDX["pp"][prizepicks.market_key(r)]["ev"]] == [41.5]


def test_index_is_bounded_per_offer(stores, monkeypatch):
    monkeypatch.setattr(lh, "PER_KEY_MAX", 5)
    r = pp_row()
    write_pp(prizepicks.HISTORY, [pp_event(r, T0 - 1000 + i, "move", 30 + i) for i in range(12)])
    lh.refresh(T0, force=True)
    assert [e["line"] for e in lh._IDX["pp"][prizepicks.market_key(r)]["ev"]] == [37, 38, 39, 40, 41]


def test_missing_stores_mean_no_block(stores):
    assert lh.block(None, ud_row(), [], None, now=T0)["apps"][0]["points"][0]["line"] == 38.5
    assert lh.block(None, ud_row(line=None, seen_at=None), [], None, now=T0) is None


# ------------------------------------------------------------------------------------------------ closing markers

def test_closing_marks_follow_each_match_and_average_the_last_ten():
    ser = [{"date": f"2026-10-{28 - i:02d}", "opponent": f"T{i}", "value": 30 + i} for i in range(14)]
    mids = [f"hltv-{i}" for i in range(14)]
    rows = [{"event_id": f"hltv:{i}", "line": 35.5 + (i % 2), "lock_utc": "x", "last_seen_at": "x"} for i in range(0, 14, 1) if i != 3]
    rows.insert(0, {"event_id": "hltv:5", "line": 99.5, "stale": True})       # a stale closing for match 5 is left off
    out = lh.closing_marks(ser, mids, rows, stale=lambda c: c.get("stale"))
    got = {m["opponent"]: m["closing"] for m in out["marks"]}
    assert "T3" not in got and got["T5"] == 36.5 and got["T0"] == 35.5 and len(out["marks"]) == 13
    l10 = [35.5 + (i % 2) for i in range(10) if i != 3]
    assert out["n_l10"] == 9 and out["avg_l10"] == round(sum(l10) / 9, 2)
    assert lh.closing_marks([], [], rows) == {"marks": [], "avg_l10": None, "n_l10": 0}


def test_block_one_line_per_app_and_line_type(stores):
    r, g = pp_row(), pp_row(id="pp:2", odds_type="goblin", line=34.5)
    u = ud_row()
    b = lh.block(None, u, [r, g, u, None], None, now=T0)
    assert [(a["platform"], a["line_type"], a["label"], a["selected"]) for a in b["apps"]] == [
        ("underdog", "standard", "Underdog", True), ("prizepicks", "standard", "PrizePicks", False), ("prizepicks", "goblin", "PrizePicks Goblin", False)]
    assert b["version"] == "lh-1" and b["closing_marks"] == [] and b["avg_closing_l10"] is None


def test_nothing_here_imports_a_sports_or_grading_module():
    src = Path(lh.__file__).read_text(encoding="utf-8")
    for mod in ("sports", "grader", "gradecore.chance_cal", "picklog"):
        assert f"import {mod}" not in src and f"from {mod}" not in src
