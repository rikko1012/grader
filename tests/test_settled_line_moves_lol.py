"""SCOUT_LINE_MOVE_NOTES on LoL receipts (workspace_lol.line_moves): the opening line is the market's first event only when that event
is its open; the closing line is the last line observed before the start. Wording only - the receipt's result never changes. Fixture
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
    for ev, line, at in items:
        env.lol.con.execute("INSERT INTO market_events (sport, platform, market_key, event, observed_at, line) VALUES ('lol', ?, ?, ?, ?, ?)",
                            (row["platform"], row["market_key"], ev, iso(at), line))
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
    ev = lambda e, line, h: {"event": e, "line": line, "observed_at": iso(s + timedelta(hours=h))}
    start = iso(s)
    assert workspace_lol._moves_of([ev("open", 9.5, -5), ev("change", 10.5, -1)], start) == (9.5, 10.5)
    assert workspace_lol._moves_of([ev("open", 9.5, -5), ev("removed", 9.5, -1)], start) == (9.5, 9.5)       # not moved: no note later
    assert workspace_lol._moves_of([ev("open", 9.5, 1)], start) is None                                        # opened after the start
    assert workspace_lol._moves_of([ev("reopen", 9.5, -5)], start) is None
    assert workspace_lol._moves_of([], start) is None and workspace_lol._moves_of([ev("open", 9.5, -5)], None) is None


def test_line_moves_without_lol_db_is_empty(tmp_path):
    srv = types.SimpleNamespace(LOL_CENTER=str(tmp_path / "nowhere"))
    assert workspace_lol.line_moves(srv, [("underdog", "ud:1|", "2026-11-07T12:00:00Z")]) == {}


def test_lol_unit_parses_a_settle_key():
    assert workspace_api._lol_unit("lol|prizepicks|pp:1|kills|standard|g9|4.5|over") == ("prizepicks", "pp:1|kills|standard|g9")
    assert workspace_api._lol_unit("lol|underdog|ud:abc||10.5|under") == ("underdog", "ud:abc|")
    assert workspace_api._lol_unit(None) is None and workspace_api._lol_unit("nba|x|y|1|over") is None
