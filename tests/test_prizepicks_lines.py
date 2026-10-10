"""PrizePicks receiver (prizepicks.py): rows from the shared database, feed state, line history, line checks,
the card's comparison (same strict joins as Underdog), one log row per platform, one projection across books."""
import copy, os, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comparison as cmp
import prizepicks as pp
from tests.test_comparison import NOW, START, _res, _row

T0 = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def _rec(lid="p1", player="cairne", maps="1-2", market="kills", line=31.5, ot="standard", team="Fnatic", opp="K27", start=T0):
    return {"line_id": lid, "player": player, "team": team, "opponent": opp, "start_time": start, "market": market, "maps": maps,
            "line": line, "odds_type": ot, "open_line": 30.5, "prev_line": 30.5, "move": 1.0, "moves": 1,
            "moved_at": T0 - timedelta(hours=2), "first_seen": T0 - timedelta(hours=5), "other_line": 30.5,
            "higher": 1.0, "lower": 1.0, "stat": "MAPS 1-2 Kills", "updated_at": T0 - timedelta(minutes=5)}


def test_rows_from_the_database():
    rows = pp.rows_from([_rec(), _rec("p2", market="headshots", maps="1"), _rec("p3", maps="2"),
                         _rec("p4", ot="goblin", line=27.5), _rec("p5", market="assists")])
    assert [r["id"] for r in rows] == ["pp:p1", "pp:p2", "pp:p4"]          # Map 2 and other markets are not graded
    r = rows[0]
    assert (r["book"], r["market_id"], r["prop"], r["scope"], r["line"], r["alt"], r["open_line"], r["other_line"], r["mod_higher"], r["higher"]) == (
        "prizepicks", "p1", "kills", "12", 31.5, False, 30.5, 30.5, 1.0, None)
    assert rows[1]["prop"] == "hs" and rows[1]["scope"] == "1"
    assert rows[2]["alt"] and rows[2]["odds_type"] == "goblin" and rows[2]["market"] != r["market"]   # goblin = its own market
    assert cmp._ts(r["start"]) == T0


def test_panel_shows_both_books_with_strict_joins():
    res = _res(line=30.5)
    before = copy.deepcopy(res["grade"])
    rows = [dict(x, stale=False) for x in pp.rows_from([
        _rec("p1", line=31.5), _rec("p4", ot="goblin", line=27.5),
        _rec("p6", team="Vitality"),                                         # namesake on another team
        _rec("p7", start=T0 + timedelta(hours=5)),                           # another match
        _rec("p8", maps="1")])]                                              # other maps: never Map 1 vs Maps 1-2
    p = cmp.panel(res, [_row()] + rows, board_at=NOW.timestamp() - 60, now=NOW)
    assert res["grade"] == before                                            # context only: the grade is untouched
    got = sorted((o["source"], o["line"], o["alt"]) for o in p["offers"])
    assert got == [("prizepicks", 27.5, True), ("prizepicks", 31.5, False), ("underdog", 30.5, False)]
    assert len(p["rejected"]) == 3
    assert p["best_line"] == {}                                              # no per-line payout: never ranked as value
    o = next(o for o in p["offers"] if o["id"] == "pp:p1")
    assert (o["open_line"], o["moves"], o["odds_type"]) == (30.5, 1, "standard")
    assert abs(o["p_over"] + o["p_under"] + o["p_push"] - 1) < 1e-6


def test_prizepicks_staleness_is_its_own_feed_age():
    res = _res(line=30.5)
    fresh = [dict(x, stale=False) for x in pp.rows_from([_rec()])]
    old = [dict(x, stale=True) for x in pp.rows_from([_rec()])]
    p = cmp.panel(res, [_row()] + fresh, board_at=NOW.timestamp() - 40 * 60, now=NOW)
    assert {o["source"]: o["stale"] for o in p["offers"]} == {"underdog": True, "prizepicks": False}
    p = cmp.panel(res, [_row()] + old, board_at=NOW.timestamp() - 60, now=NOW)
    assert {o["source"]: o["stale"] for o in p["offers"]} == {"underdog": False, "prizepicks": True}


def test_board_without_database_is_harmless(monkeypatch):
    import hltv
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    b = pp.board()
    assert b["rows"] == [] and b["state"] == "unconfigured" and "not configured" in b["error"]
    assert next(s for s in cmp.SOURCES if s["id"] == "prizepicks")["status"] == "connected"


def _feed(monkeypatch, rows, status, error=None, at=None):
    import hltv
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://x")
    pp._S.update(at=at if at is not None else time.time(), tried=time.time(), rows=rows, status=status, error=error, busy=True)


def test_feed_states(monkeypatch):
    now = time.time()
    up = [dict(r, start=(datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()) for r in pp.rows_from([_rec()])]
    _feed(monkeypatch, up, {"ok": True, "checked_at": now - 60, "last_ok": now - 60})
    b = pp.board(now)
    assert b["state"] == "ok" and not b["rows"][0]["stale"]
    _feed(monkeypatch, up, {"ok": True, "checked_at": now - 3 * 3600, "last_ok": now - 3 * 3600})
    assert pp.board(now)["state"] == "stale" and pp.board(now)["rows"][0]["stale"]            # never shown as verified
    _feed(monkeypatch, up, {"ok": False, "checked_at": now - 60, "last_ok": now - 1800, "error": "403"})
    assert pp.board(now)["state"] == "failed"                                                 # the scraper's read failed
    _feed(monkeypatch, up, {"ok": True, "checked_at": now - 60, "last_ok": now - 60}, error="OperationalError: down")
    assert pp.board(now)["state"] == "failed"                                                 # Scout's read failed
    _feed(monkeypatch, [], {"ok": True, "checked_at": now - 60, "last_ok": now - 60, "lines": 0})
    assert pp.board(now)["state"] == "empty"                                                  # empty is not failed
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)


def test_line_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "HISTORY", tmp_path / "h.jsonl"); monkeypatch.setattr(pp, "SNAPSHOT", tmp_path / "s.json")
    later = datetime.now(timezone.utc) + timedelta(days=1)
    t = time.time()
    e1 = pp._history(pp.rows_from([_rec("a", start=later), _rec("b", market="headshots", line=14.5, start=later)]), t)
    assert sorted(e["event"] for e in e1) == ["open", "open"]
    e2 = pp._history(pp.rows_from([_rec("a", line=32.5, start=later)]), t + 60)               # moved; b pulled
    assert sorted((e["event"], e.get("from_line")) for e in e2) == [("move", 31.5), ("removed", None)]
    e3 = pp._history(pp.rows_from([_rec("a2", line=32.5, start=later)]), t + 120)             # re-posted under a new id
    assert [(e["event"], e["old_id"], e["id"]) for e in e3] == [("id_change", "a", "a2")]
    assert len({e["market"] for e in pp.history() if e["prop"] == "kills"}) == 1              # one market, every event
    assert len(pp.history()) == 5


def _ppboard(rows, state="ok"):
    return {"state": state, "at": time.time(), "rows": rows, "error": None}


def test_line_check_prizepicks():
    st = T0.isoformat()
    rows = pp.rows_from([_rec("a", line=31.5), _rec("g", ot="goblin", line=27.5)])
    ok = cmp.line_check(_ppboard(rows), "cairne", "kills", "12", 31.5, "Fnatic", st, book="prizepicks")
    assert ok["status"] == "verified" and ok["book"] == "prizepicks"
    gob = cmp.line_check(_ppboard(rows), "cairne", "kills", "12", 27.5, "Fnatic", st, book="prizepicks", odds_type="goblin")
    assert gob["status"] == "verified"
    wrong_kind = cmp.line_check(_ppboard(rows), "cairne", "kills", "12", 27.5, "Fnatic", st, book="prizepicks")
    assert wrong_kind["status"] == "changed" and wrong_kind["new_line"] == 31.5 and "PrizePicks now shows" in wrong_kind["note"]
    moved = cmp.line_check(_ppboard(rows), "cairne", "kills", "12", 30.5, "Fnatic", st, book="prizepicks")
    assert moved["status"] == "changed" and moved["new_line"] == 31.5
    stale = cmp.line_check(_ppboard(rows, "stale"), "cairne", "kills", "12", 31.5, "Fnatic", st, book="prizepicks")
    assert stale["status"] == "failed" and "stale" in stale["note"]                         # stale is never "verified"
    none = cmp.line_check(_ppboard(rows), "cairne", "hs", "12", 14.5, "Fnatic", st, book="prizepicks")
    assert none["status"] == "unavailable" and "PrizePicks" in none["note"]


def test_one_log_row_per_platform():
    import picklog
    base = {"grade": {"prop": "kills", "line": 30.5, "line_type": "standard", "scope": "12", "lambda_final": 29.0, "p_more": 0.4,
                      "p_less": 0.6, "flags": []},
            "player_block": {"player": {"slug": "cairne", "nick": "cairne", "id": 7}, "current_team": "Fnatic"},
            "next_match": {"slug": "fnatic-vs-k27", "start": START, "opponent": "K27"},
            "verdict": {"state": "GRADE", "side": "Less", "p": 0.6, "grade": "B", "units": 0.5}}
    ud = picklog.pick_row(dict(base, book={"book": "underdog", "market_id": "u1"}), base["verdict"], test=2)
    p2 = picklog.pick_row(dict(base, book={"book": "prizepicks", "market_id": "1560", "market": "m"}), base["verdict"], test=2)
    assert ud["key"] != p2["key"] and p2["key"].endswith("|pp")
    assert (ud["book"], ud["market_id"]) == ("underdog", "u1") and (p2["book"], p2["market_id"], p2["market_key"]) == ("prizepicks", "1560", "m")
    mem = picklog.pick_row(base, base["verdict"], test=0)
    assert mem["book"] is None and mem["key"] == ud["key"]                              # Underdog keys unchanged


def test_market_type_never_moves_the_projection():
    import grader, random
    from tests.test_perf import _inputs
    rnd = random.Random(5)
    for _ in range(200):
        d = _inputs(rnd, rnd.choice(["12", "1"]))
        a = grader.grade_leg(dict(d, line_type="standard")); b = grader.grade_leg(dict(d, line_type="goblin"))
        c = grader.grade_leg(dict(d, line_type="demon"))
        assert a["lambda_final"] == b["lambda_final"] == c["lambda_final"] and a["p_more"] == b["p_more"] == c["p_more"]
