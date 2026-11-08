"""sports/line_moves.py (SCOUT_LINE_MOVE_NOTES, review 1107 #1): an NBA / NFL settled pick whose offer was bumped between its
opening and closing line says so - "Opened 15.5 (Over won) · closed 16 (push)" - from the offer's stored revisions (hot and
archived) and the game's REAL start. Wording only: the pick's result and every count stay exactly as settled at its own line."""
import json
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from sports import archive, line_moves, store, web
from sports.tests import helpers as h

START = h.START                                          # 2026-10-23T23:00:00Z
AFTER = datetime(2026, 10, 24, 3, tzinfo=timezone.utc).timestamp()


def _t(hours):
    return (datetime.fromisoformat(START.replace("Z", "+00:00")) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def fresh():
    line_moves.reset()
    yield
    line_moves.reset()


def _opener():
    @contextmanager
    def go():
        con = store.connect(read_only=True)
        try:
            yield con
        finally:
            con.close()
    return go


def _put(oid, lines, event_start=START, platform="prizepicks"):
    """lines: [(line, hours from the start)] stored as revisions of one offer; the event starts at event_start."""
    con = store.connect()
    for line, hrs in lines:
        store.put(con, h.offer(oid=oid, platform=platform, line=line, observed_at=_t(hrs)))
    store.put(con, h.event(start=event_start))
    con.commit()
    con.close()


def test_b1eany_shape_opened_15_5_closed_16_final_16():
    _put("pp:b1", [(15.5, -6), (16.0, -1)])
    got = line_moves.offer_moves([("prizepicks", "pp:b1")], _opener(), now=AFTER)
    assert got == {("prizepicks", "pp:b1"): (15.5, 16.0)}
    mv = line_moves.note(got[("prizepicks", "pp:b1")], 15.5, 16, "prizepicks")
    assert mv["text"] == "Opened 15.5 (Over won) · closed 16 (push)" and mv["chip"] == "▲ bumped"
    assert mv["tip"] == "Bumped up: PrizePicks opened this line at 15.5; it closed at 16."


def test_a_delayed_game_closes_at_its_real_start():
    """The event moved to two hours later: a move between the listed start and the real start is the closing line."""
    _put("pp:d", [(15.5, -6), (16.0, 1)], event_start=_t(2))
    assert line_moves.offer_moves([("prizepicks", "pp:d")], _opener(), now=AFTER) == {("prizepicks", "pp:d"): (15.5, 16.0)}


def test_a_different_line_after_the_start_means_the_close_is_not_known():
    _put("pp:l", [(15.5, -6), (16.0, -1), (18.5, 1)])
    assert line_moves.offer_moves([("prizepicks", "pp:l")], _opener(), now=AFTER) == {}


def test_first_stored_after_the_start_has_no_opening_line():
    _put("pp:a", [(15.5, 1)])
    assert line_moves.offer_moves([("prizepicks", "pp:a")], _opener(), now=AFTER) == {}


def test_before_the_start_nothing_is_said_and_it_is_asked_again():
    _put("pp:e", [(15.5, -6), (16.0, -1)])
    early = datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() - 60
    assert line_moves.offer_moves([("prizepicks", "pp:e")], _opener(), now=early) == {}
    assert line_moves.offer_moves([("prizepicks", "pp:e")], _opener(), now=early + line_moves.MISS_TTL + 120) == {
        ("prizepicks", "pp:e"): (15.5, 16.0)}


def test_answers_are_cached_and_the_store_is_opened_only_on_a_miss():
    _put("pp:c", [(15.5, -6), (16.0, -1)])
    opened = []
    real = _opener()

    @contextmanager
    def spy():
        opened.append(1)
        with real() as con:
            yield con
    k = ("prizepicks", "pp:c")
    line_moves.offer_moves([k], spy, now=AFTER)
    assert line_moves.offer_moves([k], spy, now=AFTER + 60) == {k: (15.5, 16.0)} and len(opened) == 1
    final = AFTER + line_moves.FINAL_S + 3600
    line_moves.offer_moves([k], spy, now=final)
    assert line_moves.offer_moves([k], spy, now=final + 50 * line_moves.MISS_TTL) == {k: (15.5, 16.0)} and len(opened) == 2


def test_reads_per_call_are_capped(monkeypatch):
    monkeypatch.setattr(line_moves, "KEYS_MAX", 2)
    for i in range(4):
        _put(f"pp:{i}", [(15.5, -6), (16.0, -1)])
    keys = [("prizepicks", f"pp:{i}") for i in range(4)]
    assert len(line_moves.offer_moves(keys, _opener(), now=AFTER)) == 2
    assert len(line_moves.offer_moves(keys, _opener(), now=AFTER)) == 4         # the cached two answer free, two more read


def test_a_store_that_does_not_exist_answers_nothing():
    assert line_moves.offer_moves([("prizepicks", "pp:x")], _opener(), now=AFTER) == {}


def test_archived_offers_read_the_same():
    _put("pp:z", [(15.5, -6), (16.0, -1)])
    con = store.connect()
    out = archive.run_pass(con, now=datetime(2026, 11, 7, tzinfo=timezone.utc))
    con.close()
    assert out["error"] is None and out["rows"] >= 2
    c = store.connect(read_only=True)
    assert c.execute("SELECT COUNT(*) FROM records WHERE kind='offer'").fetchone()[0] == 0     # every offer row archived
    c.close()
    assert line_moves.offer_moves([("prizepicks", "pp:z")], _opener(), now=AFTER) == {("prizepicks", "pp:z"): (15.5, 16.0)}


def test_annotate_only_settled_picks_on_the_band_and_never_their_result():
    _put("pp:b1", [(15.5, -6), (16.0, -1)])
    views = [{"line": 15.5, "result": {"status": "win", "value": 16.0}},          # the B1eany case
             {"line": 15.5, "result": {"status": "unresolved", "value": None}},
             {"line": 20.5, "result": {"status": "loss", "value": 16.0}},         # a line off the move: no note
             {"line": 15.5, "result": None}]
    before = json.dumps(views)
    line_moves.annotate(views, [("prizepicks", "pp:b1")] * 4, _opener(), now=AFTER)
    assert views[0]["line_move"]["text"] == "Opened 15.5 (Over won) · closed 16 (push)"
    assert all("line_move" not in v for v in views[1:])
    assert json.dumps([{k: v for k, v in x.items() if k != "line_move"} for x in views]) == before


def test_of_rows_rules():
    r = lambda rev, line, hrs, late=0: {"revision": rev, "line": line, "late": late, "known_at": _t(hrs)}
    assert line_moves.of_rows([r(1, 15.5, -5), r(2, 16.0, -1)], START) == (15.5, 16.0)
    assert line_moves.of_rows([r(1, 15.5, -5), r(2, 17.0, -1, late=1)], START) == (15.5, 15.5)     # a late row is history only
    assert line_moves.of_rows([r(2, 15.5, -5)], START) is None                                       # revision 1 not read
    assert line_moves.of_rows([r(1, 15.5, -5, late=1)], START) is None
    assert line_moves.of_rows([r(1, 15.5, -5)], None) is None and line_moves.of_rows([], START) is None


def test_the_route_answers_members_with_a_bounded_list(monkeypatch):
    _put("pp:b1", [(15.5, -6), (16.0, -1)])
    monkeypatch.setattr(line_moves, "time", types.SimpleNamespace(time=lambda: AFTER))
    web._POOL.reset()
    req = lambda keys: web.Request(path="/api/sports/offers/moves", method="GET", role="member", member="m_1",
                                   query={"keys": json.dumps(keys)})
    code, body, _ = web.dispatch(req([["prizepicks", "pp:b1"], ["underdog", "nope"]]))
    assert code == 200 and json.loads(body) == {"moves": [["prizepicks", "pp:b1", 15.5, 16.0]]}
    code, body, _ = web.dispatch(req([["prizepicks", "x"]] * (line_moves.KEYS_MAX + 1)))
    assert code == 400
    code, body, _ = web.dispatch(req([["kalshi", "x"]]))
    assert code == 400
    code, _, _ = web.dispatch(web.Request(path="/api/sports/offers/moves", method="GET", role=None, query={}))
    assert code == 401


def test_the_wording_is_the_esports_servers_word_for_word():
    """sports never imports an esports module (test_sports_isolation), so it keeps its own copy of closing_lines.move_note /
    row_note: the copy must read exactly the same on every case (the esports file is loaded here by path, in the test only)."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("_cl_parity", Path(__file__).resolve().parents[2] / "closing_lines.py")
    cl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cl)
    grid = [x / 2 for x in range(26, 40)]
    n = 0
    for o in grid:
        for c in grid:
            for a in (13, 15.5, 16, 16.5, 17, 19.5, 20):
                for ln in (o, c, (o + c) / 2, 21.5):
                    for book in ("underdog", "prizepicks", None):
                        want = cl.row_note({"open_line": o, "line": c}, ln, a, book)
                        assert line_moves.note((o, c), ln, a, book) == want, (o, c, a, ln, book)
                        n += want is not None
    assert n > 1000


def test_the_flag_is_on_unless_set_to_0(monkeypatch):
    monkeypatch.delenv(line_moves.FLAG, raising=False)
    assert line_moves.enabled()
    monkeypatch.setenv(line_moves.FLAG, "0")
    assert not line_moves.enabled()


# ---------------------------------------------------------------------------------------------------- the sports app's own pages
def _picks_world(monkeypatch):
    from dataclasses import replace
    from sports import picks, settlement as settlement_mod
    from sports.tests import slip_world as w
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(picks, "clock", lambda: w.NOW)
    monkeypatch.setattr(line_moves, "time", types.SimpleNamespace(time=lambda: datetime(2026, 10, 24, 6, tzinfo=timezone.utc).timestamp()))
    web._POOL.reset()
    con = store.connect()
    legs = w.world(con, [("h1", "T1", 1, "over", 0.61)])
    rid, f = legs["h1"]
    item = {"platform": "underdog", "record_id": rid, "side": "over", "forecast_id": f.forecast_id,
            "shown": {"p_over": f.p_over, "p_under": f.p_under, "p_push": f.p_push}, "surface": "board"}
    code, body, _ = web.dispatch(web.Request(path="/api/sports/picks/track", method="POST", query={},
                                             body=json.dumps({"picks": [item]}).encode(), member="m_owner", role="member"))
    assert code == 200, body
    picks.drain()
    o = store.get(con, rid)                                           # Over 20.5 tracked; bumped to 21 before the start
    moved = replace(o, line=21.0, observed_at="2026-10-23T22:00:00Z", known_at="2026-10-23T22:00:00Z")
    store.put(con, moved)
    pc = picks.connect()
    picks.ingest(pc)
    settlement_mod.record(con, [w.settlement(moved, "over", "push", 21.0)])
    picks.settle_all(con, pc)
    pc.close()
    con.commit()
    return con, o


def test_sports_my_picks_says_the_line_moved_and_keeps_the_win(monkeypatch):
    con, o = _picks_world(monkeypatch)
    con.close()
    code, body, _ = web.dispatch(web.Request(path="/api/sports/picks/mine", method="GET", query={}, member="m_owner", role="member"))
    doc = json.loads(body)
    p = doc["picks"][0]
    assert code == 200 and p["result"]["status"] == "win" and p["line"] == 20.5            # settled at the pick's own line
    assert p["line_move"]["text"] == "Opened 20.5 (Over won) · closed 21 (push)"
    assert doc["record"]["nba"]["win"] == 1 and doc["record"]["nba"]["push"] == 0           # the record is unchanged
    monkeypatch.setenv(line_moves.FLAG, "0")
    p = json.loads(web.dispatch(web.Request(path="/api/sports/picks/mine", method="GET", query={}, member="m_owner",
                                            role="member"))[1])["picks"][0]
    assert "line_move" not in p and p["result"]["status"] == "win"


def test_sports_slip_settle_says_the_line_moved(monkeypatch):
    con, o = _picks_world(monkeypatch)
    con.close()
    slip = {"league": "nba", "platform": "underdog", "legs": [{"platform": "underdog", "offer_id": o.offer_id, "side": "over",
                                                                "line": 20.5, "team": o.team}]}
    code, body, _ = web.dispatch(web.Request(path="/api/sports/slips/settle", method="GET", query={"slip": json.dumps(slip)},
                                             member="m_owner", role="member"))
    lr = json.loads(body)["leg_results"][0]
    assert code == 200 and lr["status"] == "win" and lr["line_move"]["text"] == "Opened 20.5 (Over won) · closed 21 (push)"
