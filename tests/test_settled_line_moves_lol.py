"""SCOUT_LINE_MOVE_NOTES on LoL receipts (workspace_lol.line_moves): the opening line is the market's first event only when that event
is its open; the closing line is the last line observed before the market's OWN start (its latest listed start_time, so a delayed
series closes at its real start). Wording only - the receipt's result never changes. Fixture
worker folder only (tests/workspace_other_fixture.Lol)."""
import types
from datetime import datetime, timedelta, timezone

import pytest

import closing_lines as cl
import workspace_api
import workspace_lol
from tests.test_workspace_all_sports import env, get, lol_legs, track  # noqa: F401  (env = the all-sports fixture)


def iso(t):
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def harky(env):
    return next(x for x in env.lol.rows if x["player"] == "Harky" and x["platform"] == "underdog")


def events(env, row, items):
    """(event, line, observed at[, listed start]) - the start defaults to the fixture's own start."""
    for it in items:
        ev, line, at = it[:3]
        st = it[3] if len(it) > 3 else env.lol.start
        env.lol.con.execute("INSERT INTO market_events (sport, platform, market_key, event, observed_at, line, start_time) "
                            "VALUES ('lol', ?, ?, ?, ?, ?, ?)", (row["platform"], row["market_key"], ev, iso(at), line, iso(st)))
    env.lol.con.commit()


def mine_after_start(env, monkeypatch):
    """My picks read once the series has started (a settled receipt always is): the line-move read only trusts a passed start."""
    later = env.lol.start.timestamp() + 3 * 3600
    real = workspace_lol.time
    monkeypatch.setattr(workspace_lol, "time", types.SimpleNamespace(time=lambda: later, monotonic=real.monotonic))
    workspace_api._PRIVATE.clear()
    try:
        return get(env, "mine")[1]
    finally:
        monkeypatch.setattr(workspace_lol, "time", real)


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.setenv(cl.MOVE_FLAG, "1")


def settled(env, actual, status="push", over_hit=None):
    r, legs = lol_legs(env)
    assert track(env, legs, "underdog", "standard")[0] == 200
    env.lol.settle(harky(env), status, actual, over_hit)
    workspace_api.store().settle_once()


def test_a_lol_receipt_shows_the_move_and_keeps_its_result(env, flags, monkeypatch):
    row, s = harky(env), env.lol.start
    events(env, row, [("open", row["line"] - 1, s - timedelta(hours=6)), ("change", row["line"], s - timedelta(hours=2)),
                      ("change", row["line"] + 2, s + timedelta(minutes=30))])        # after the start: never the closing line
    settled(env, row["line"])
    x = next(p for p in mine_after_start(env, monkeypatch)["receipts"] if p["player"] == "Harky")
    o, c = cl.fmt_line(row["line"] - 1), cl.fmt_line(row["line"])
    assert x["status"] == "push" and x["line_move"]["text"] == f"Opened {o} (Over won) · closed {c} (push)"
    assert x["line_move"]["tip"] == f"Bumped up: Underdog opened this line at {o}; it closed at {c}."
    assert "_settle_key" not in x


def test_no_open_event_first_means_no_opening_line(env, flags, monkeypatch):
    row, s = harky(env), env.lol.start
    events(env, row, [("change", row["line"] - 1, s - timedelta(hours=6)), ("change", row["line"], s - timedelta(hours=2))])
    settled(env, row["line"])
    x = next(p for p in mine_after_start(env, monkeypatch)["receipts"] if p["player"] == "Harky")
    assert x["status"] == "push" and "line_move" not in x


def test_flag_off_adds_nothing(env, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.delenv(cl.MOVE_FLAG, raising=False)
    row, s = harky(env), env.lol.start
    events(env, row, [("open", row["line"] - 1, s - timedelta(hours=6)), ("change", row["line"], s - timedelta(hours=2))])
    settled(env, row["line"])
    x = next(p for p in mine_after_start(env, monkeypatch)["receipts"] if p["player"] == "Harky")
    assert x["status"] == "push" and "line_move" not in x and "_settle_key" not in x


def test_moves_of_rules():
    s = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    ev = lambda e, line, h, st=s: {"event": e, "line": line, "observed_at": iso(s + timedelta(hours=h)), "start_time": iso(st)}
    assert workspace_lol._moves_of([ev("open", 9.5, -5), ev("change", 10.5, -1)]) == (9.5, 10.5)
    assert workspace_lol._moves_of([ev("open", 9.5, -5), ev("removed", 9.5, -1)]) == (9.5, 9.5)       # not moved: no note later
    assert workspace_lol._moves_of([ev("open", 9.5, -5), ev("change", 10.5, 1)]) == (9.5, 9.5)        # after the start: not the close
    assert workspace_lol._moves_of([ev("open", 9.5, 1)]) is None                                        # opened after the start
    assert workspace_lol._moves_of([ev("reopen", 9.5, -5)]) is None
    assert workspace_lol._moves_of([]) is None
    assert workspace_lol._moves_of([{"event": "open", "line": 9.5, "observed_at": iso(s), "start_time": None}]) is None   # no start


def test_a_delayed_series_closes_at_its_own_start_not_the_receipts():
    """Reviewer case: open 9.5, 10.5 at 11:00, the series moved from 12:00 to 13:00, back to 9.5 at 12:20, removed at 13:00. The
    market closed at 9.5 unmoved - never "Opened 9.5 - closed 10.5" from the start the receipt saved."""
    d = datetime(2026, 11, 7, tzinfo=timezone.utc)
    at = lambda h, m=0: d + timedelta(hours=h, minutes=m)
    ev = lambda e, line, t, st: {"event": e, "line": line, "observed_at": iso(t), "start_time": iso(st)}
    rows = [ev("open", 9.5, at(8), at(12)), ev("change", 10.5, at(11), at(12)), ev("change", 10.5, at(11, 30), at(13)),
            ev("change", 9.5, at(12, 20), at(13)), ev("removed", 9.5, at(13), at(13))]
    assert workspace_lol._moves_of(rows) == (9.5, 9.5)
    assert cl.row_note({"open_line": 9.5, "line": 9.5}, 9.5, 10, "underdog") is None


def test_a_delayed_series_on_a_receipt_reads_the_real_close(env, flags, monkeypatch):
    row, s = harky(env), env.lol.start
    events(env, row, [("open", row["line"] - 1, s - timedelta(hours=6)), ("change", row["line"], s - timedelta(hours=2)),
                      ("change", row["line"], s - timedelta(minutes=30), s + timedelta(hours=1)),     # delayed an hour
                      ("change", row["line"] - 1, s + timedelta(minutes=20), s + timedelta(hours=1)),
                      ("removed", row["line"] - 1, s + timedelta(hours=1), s + timedelta(hours=1))])
    settled(env, row["line"])
    x = next(p for p in mine_after_start(env, monkeypatch)["receipts"] if p["player"] == "Harky")
    assert x["status"] == "push" and "line_move" not in x                  # closed where it opened: nothing to say


def test_line_moves_cache_keeps_final_answers_and_rereads_recent_ones(tmp_path, monkeypatch):
    import sqlite3
    d = tmp_path / "lol"
    d.mkdir()
    con = sqlite3.connect(d / "lol.db")
    con.execute("CREATE TABLE market_events (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, market_key TEXT, event TEXT, "
                "observed_at TEXT, line REAL, start_time TEXT)")
    s = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    for e, line, h in (("open", 9.5, -5), ("change", 10.5, -1)):
        con.execute("INSERT INTO market_events (platform, market_key, event, observed_at, line, start_time) VALUES (?,?,?,?,?,?)",
                    ("underdog", "ud:1|", e, iso(s + timedelta(hours=h)), line, iso(s)))
    con.commit()
    con.close()
    srv = types.SimpleNamespace(LOL_CENTER=str(d))
    workspace_lol.reset()
    opens = []
    real = workspace_lol._open
    monkeypatch.setattr(workspace_lol, "_open", lambda srv_: opens.append(1) or real(srv_))
    k, t = ("underdog", "ud:1|"), s.timestamp()
    assert workspace_lol.line_moves(srv, [k], now=t - 60) == {}                              # before its close: not known yet
    assert workspace_lol.line_moves(srv, [k], now=t - 30) == {} and len(opens) == 1          # that answer held for the TTL
    assert workspace_lol.line_moves(srv, [k], now=t + 3600) == {k: (9.5, 10.5)} and len(opens) == 2
    assert workspace_lol.line_moves(srv, [k], now=t + 3700) == {k: (9.5, 10.5)} and len(opens) == 2
    assert workspace_lol.line_moves(srv, [k], now=t + 3600 + workspace_lol.MOVES_MISS_TTL + 1) == {k: (9.5, 10.5)}
    assert len(opens) == 3                                                                   # a recent close: read again after the TTL
    late = t + workspace_lol.MOVES_FINAL_S + 10
    workspace_lol.line_moves(srv, [k], now=late)
    assert workspace_lol.line_moves(srv, [k], now=late + 10 * workspace_lol.MOVES_MISS_TTL) == {k: (9.5, 10.5)} and len(opens) == 4
    gone = ("underdog", "ud:2|")
    con = sqlite3.connect(d / "lol.db")
    con.execute("INSERT INTO market_events (platform, market_key, event, observed_at, line, start_time) VALUES (?,?,?,?,?,?)",
                ("underdog", "ud:2|", "change", iso(s - timedelta(hours=3)), 4.5, iso(s)))   # no open event: never an opening line
    con.commit()
    con.close()
    assert workspace_lol.line_moves(srv, [gone], now=late) == {} and len(opens) == 5
    assert workspace_lol.line_moves(srv, [gone], now=late + 10 * workspace_lol.MOVES_MISS_TTL) == {} and len(opens) == 5   # final miss kept
    workspace_lol.reset()


def test_line_moves_without_lol_db_is_empty(tmp_path):
    workspace_lol.reset()
    srv = types.SimpleNamespace(LOL_CENTER=str(tmp_path / "nowhere"))
    assert workspace_lol.line_moves(srv, [("underdog", "ud:1|")]) == {}


def test_lol_unit_parses_a_settle_key():
    assert workspace_api._lol_unit("lol|prizepicks|pp:1|kills|standard|g9|4.5|over") == ("prizepicks", "pp:1|kills|standard|g9")
    assert workspace_api._lol_unit("lol|underdog|ud:abc||10.5|under") == ("underdog", "ud:abc|")
    assert workspace_api._lol_unit(None) is None and workspace_api._lol_unit("nba|x|y|1|over") is None
