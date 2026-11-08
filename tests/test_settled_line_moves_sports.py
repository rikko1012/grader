"""SCOUT_LINE_MOVE_NOTES on NBA / NFL receipts (review 1107 #1): a settled receipt whose offer was bumped between its opening and
closing line says so, from the sports API's stored offer revisions (GET /api/sports/offers/moves, sports/line_moves.py) - never the
workspace's in-memory first sightings. Wording only: status, final and the record stay exactly as settled at the member's own
line. Fixture sports service only (tests/workspace_other_fixture.Sports)."""
import pytest

import closing_lines as cl
import workspace_api
import workspace_sports
from tests.test_workspace_all_sports import env, mine, past, tracked_nba  # noqa: F401  (env = the all-sports fixture)


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.setenv(cl.MOVE_FLAG, "1")
    with workspace_sports._SETTLED_LOCK:
        workspace_sports._SETTLED_MOVES.clear()
    yield
    with workspace_sports._SETTLED_LOCK:
        workspace_sports._SETTLED_MOVES.clear()


def settled(env):
    tracked_nba(env)
    past(env)
    env.sports.settled[("ud:1", "under")] = {"status": "win", "value": 18.0, "settlement_id": "st-1", "revision": 1}
    env.sports.settled[("ud:4", "over")] = {"status": "push", "value": 6.5, "settlement_id": "st-2", "revision": 1}
    workspace_api.store().settle_once()


def test_a_bumped_nba_receipt_says_so_and_keeps_its_result(env, flags):
    env.sports.moves[("underdog", "ud:4")] = (6.0, 6.5)          # the B1eany shape: Over won at the opening line, push at the close
    env.sports.moves[("underdog", "ud:1")] = (21.5, 20.5)        # Under won at both lines: the board's chip only
    settled(env)
    m = mine(env)
    st = {x["player"]: x for x in m["receipts"]}
    reed, karl = st["Reed Lean"], st["Karl Anders"]
    assert reed["status"] == "push" and reed["final"] == 6.5
    assert reed["line_move"]["text"] == "Opened 6 (Over won) · closed 6.5 (push)" and reed["line_move"]["chip"] == "▲ bumped"
    assert reed["line_move"]["tip"] == "Bumped up: Underdog opened this line at 6; it closed at 6.5."
    assert karl["status"] == "hit" and karl["line_move"]["kind"] == "bump" and karl["line_move"]["chip"] == "▼ bumped from 21.5"
    assert m["record"]["hit"] == 1 and m["record"]["push"] == 1 and m["record"]["decided"] == 1          # counts unchanged
    assert all("_settle_key" not in x and "_match_key" not in x for x in m["receipts"])
    assert env.sports.calls.get("/api/sports/offers/moves") == 1


def test_no_stored_opening_line_means_no_note(env, flags):
    settled(env)
    m = mine(env)
    assert all("line_move" not in x for x in m["receipts"])
    assert {x["player"]: x["status"] for x in m["receipts"]} == {"Karl Anders": "hit", "Reed Lean": "push"}


def test_flag_off_asks_nothing_and_adds_nothing(env, monkeypatch):
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.delenv(cl.MOVE_FLAG, raising=False)
    env.sports.moves[("underdog", "ud:4")] = (6.0, 6.5)
    settled(env)
    m = mine(env)
    assert all("line_move" not in x for x in m["receipts"]) and "/api/sports/offers/moves" not in env.sports.calls


def test_the_sports_api_down_never_breaks_my_picks(env, flags):
    env.sports.moves[("underdog", "ud:4")] = (6.0, 6.5)
    settled(env)
    env.sports.down = True
    m = mine(env)
    assert {x["player"]: x["status"] for x in m["receipts"]} == {"Karl Anders": "hit", "Reed Lean": "push"}
    assert all("line_move" not in x for x in m["receipts"])


def test_line_moves_cache_and_caps(monkeypatch):
    workspace_sports.reset()
    calls = []

    def fake(srv, path, query=None, etag=None, timeout=None):
        import json
        keys = json.loads(query["keys"])
        calls.append(len(keys))
        return {"moves": [[p, o, 15.5, 16.0] for p, o in keys]}, None
    monkeypatch.setattr(workspace_sports, "call", fake)
    monkeypatch.setattr(workspace_sports, "MOVE_KEYS", 2)
    keys = [("prizepicks", f"pp:{i}", "2026-01-01T00:00:00Z") for i in range(6)] + [("prizepicks", "pp:0", "2026-01-01T00:00:00Z")]
    out = workspace_sports.line_moves(None, keys, now=1.9e9)
    assert len(out) == 4 and calls == [2, 2]                     # MOVE_CALLS reads of MOVE_KEYS each, duplicates asked once
    out = workspace_sports.line_moves(None, keys, now=1.9e9 + 10 * workspace_sports.MOVES_TTL)
    assert len(out) == 6 and calls == [2, 2, 2]                  # an old game's answer is kept: only the two left are read
    workspace_sports.reset()


def test_settle_key_offer():
    assert workspace_sports.settle_key_offer("nba|underdog|ud:1|under|20.5") == ("nba", "underdog", "ud:1")
    assert workspace_sports.settle_key_offer("nfl|prizepicks|pp|x|over|1.5") == ("nfl", "prizepicks", "pp|x")
    assert workspace_sports.settle_key_offer("lol|underdog|ud:1|over|1") is None and workspace_sports.settle_key_offer(None) is None
