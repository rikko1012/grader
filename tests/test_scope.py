"""Scope regression tests (2026-10-08 incident): they check the MEANING of totals, not just reproducibility."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scope import scope_total, outcome


def m(n, k, hs, won):
    return {"number": n, "kills": k, "headshots": hs, "won": won}


def test_map1_is_first_map_only():
    maps = [m(1, 12, 6, True), m(2, 24, 10, True)]
    assert scope_total(maps, "1", "kills") == (12, [1])
    assert outcome(12, 15.5, "More") == ("settled", 0)        # Under, not 36 and Over
    assert outcome(12, 15.5, "Less") == ("settled", 1)


def test_later_maps_never_change_map1_or_maps12():
    a = [m(1, 12, 6, True), m(2, 24, 10, False), m(3, 30, 15, True)]
    b = [m(1, 12, 6, True), m(2, 5, 1, False), m(3, 2, 0, True)]
    assert scope_total(a, "1", "kills")[0] == scope_total(b, "1", "kills")[0] == 12
    c = [m(1, 12, 6, True), m(2, 24, 10, False), m(3, 99, 50, True)]
    assert scope_total(a, "12", "kills")[0] == scope_total(c, "12", "kills")[0] == 36
    assert scope_total(a, "12", "hs")[0] == scope_total(c, "12", "hs")[0] == 16


def test_maps123_is_maps_1_2_and_3():
    """Maps 1-3 = maps 1, 2 and 3 only (2026-10-11). The prop's map scope, not the match format."""
    from scope import MAP3_NOT_PLAYED
    # BO3 that went 1-1: maps 1-3
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, False), m(3, 30, 9, True)], "123", "kills") == (51, [1, 2, 3])
    # BO3 that ended 2-0: no map 3 - the pick voids (Underdog: a player must play each map of the selection)
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, True)], "123", "kills") == (None, MAP3_NOT_PLAYED)
    # 1-1 but map 3 not in yet: wait, never a 2-map total
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, False)], "123", "kills")[0] is None


def test_bo5_maps123_counts_all_three_maps_and_never_maps_4_5():
    """BO5 regression (2026-10-11): maps 1-3 are always played. A 2-0 after two maps still has map 3, and it counts in
    full; maps 4 and 5 never count."""
    bo5_3_0 = [m(1, 20, 9, True), m(2, 18, 8, True), m(3, 22, 10, True)]
    assert scope_total(bo5_3_0, "123", "kills") == (60, [1, 2, 3]) and scope_total(bo5_3_0, "123", "hs") == (27, [1, 2, 3])
    bo5_3_2 = [m(1, 20, 9, True), m(2, 18, 8, True), m(3, 22, 10, False), m(4, 25, 11, False), m(5, 30, 12, True)]
    assert scope_total(bo5_3_2, "123", "kills") == (60, [1, 2, 3])          # maps 4-5 excluded
    assert scope_total(bo5_3_2, "12", "kills") == (38, [1, 2]) and scope_total(bo5_3_2, "1", "kills") == (20, [1])


def test_bo5_maps123_projection_is_not_discounted():
    """BO5 regression: the Maps 1-3 projection is three full maps - no BO3 chance of reaching map 3 (~47%) applied, with
    the performance model or the fallback, whatever the win chances."""
    import grader, perf
    for ml in (-400, -150, 110, 250):
        for bo in (5, 3):
            d = dict(prop="kills", line=42.5, line_type="standard", scope="123", bo=bo, maps_played=40, kpr=0.7, hs_pct=45,
                     team_ml=ml, opp_ml=-ml, rating=1.0)
            g = grader.grade_leg(dict(d), dict(grader.CONFIG, gap_trust_at={"kills": 1e9, "hs": 1e9}))   # fallback, no gap compression
            assert abs(g["lambda_final"] - 1.5 * g["lambda_maps12"]) < 0.02   # both rounded to 0.01
            assert (g["p_map3"] is None) == (bo == 5)                               # BO5: no "chance of map 3" at all
    hist = [(1000 - i, 20, 24, 10, True, True) for i in range(1, 40)]
    p12 = perf.project(hist, [], True, 1000, "12", "kills"); p123 = perf.project(hist, [], True, 1000, "123", "kills")
    assert abs(p123["lambda"] - 1.5 * p12["lambda"]) < 1e-9 and p123["spread_c"] == perf.SPREAD[("kills", "123")]
    pf = dict(p123, version="perf-2")
    g = grader.grade_leg(dict(prop="kills", line=42.5, line_type="standard", scope="123", bo=5, maps_played=40, kpr=0.7, hs_pct=45,
                              team_ml=-150, opp_ml=150, perf=pf))
    assert g["projection_source"].startswith("perf") and abs(g["lambda_final"] - p123["lambda"]) < 0.01
    assert abs(g["lambda_maps12"] - p12["lambda"]) < 0.02                      # the Maps 1-2 equivalent, not the 3-map total

def test_headshots_scope():
    maps = [m(1, 12, 7, True), m(2, 24, 9, True)]
    assert scope_total(maps, "1", "hs") == (7, [1])
    assert scope_total(maps, "12", "hs") == (16, [1, 2])


def test_whole_line_push():
    assert outcome(16, 16, "More") == ("push", None)
    assert outcome(16, 16, "Less") == ("push", None)


def test_missing_stats_are_unknown_not_zero():
    assert scope_total([m(1, 12, None, True), m(2, 20, 8, True)], "1", "hs")[0] is None
    assert scope_total([m(1, 12, None, True), m(2, 20, 8, True)], "1", "kills") == (12, [1])
    assert scope_total([m(2, 20, 8, True)], "1", "kills")[0] is None             # map 1 missing
    assert scope_total([m(1, 12, 5, True)], "12", "kills")[0] is None             # BO1: no Maps 1-2 total


def test_overtime_counts_in_full():
    assert scope_total([m(1, 31, 14, False), m(2, 18, 9, True)], "1", "kills") == (31, [1])


def test_out_of_order_and_duplicates():
    assert scope_total([m(2, 24, 10, True), m(1, 12, 6, True)], "1", "kills") == (12, [1])
    assert scope_total([m(1, 12, 6, True), m(1, 13, 6, True)], "1", "kills")[0] is None


def test_live_record_builder_matches_scope_rules():
    """cs2_scout.recent_form (the live last-10 builder) must agree with scope_total for Map 1 and Maps 1-2."""
    import cs2_scout
    rows = []
    series = {"a": [(1, 12, 6, 1), (2, 24, 10, 1)], "b": [(1, 30, 12, 0), (2, 9, 4, 0)], "c": [(1, 16, 8, 1)]}
    for i, (mid, ms) in enumerate(series.items()):
        for n, k, hs, w in ms:
            rows.append({"match_id": mid, "date": f"2026-10-0{i + 1}", "number": n, "map": "de_x", "kills": k,
                         "headshots": hs, "rounds": 24, "won": bool(w), "opponent": "O"})
    rf1 = cs2_scout.recent_form(rows, 0.7, 15.5, "kills", need=1)
    rf12 = cs2_scout.recent_form(rows, 0.7, 30.5, "kills", need=2)
    for mid, ms in series.items():
        mm = [m(n, k, hs, bool(w)) for n, k, hs, w in ms]
        s1 = next((s for s in rf1["series"] if s["date"] == next(r["date"] for r in rows if r["match_id"] == mid)), None)
        t1 = scope_total(mm, "1", "kills")[0]
        assert (s1["values"][0] if s1 else None) == t1
        s12 = next((s for s in rf12["series"] if s["date"] == next(r["date"] for r in rows if r["match_id"] == mid)), None)
        t12 = scope_total(mm, "12", "kills")[0]
        assert (s12["total"] if s12 else None) == t12


def _fake_hltv(monkeypatch, series):
    import hltv

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(__import__("picklog"), "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: series)


def _row(n, k, hs, won, mid=7, opp="1win"):
    return {"match_id": f"hltv-{mid}", "hltv_match": mid, "date": "2026-10-06", "number": n, "map": f"de_m{n}",
            "kills": k, "headshots": hs, "rounds": 20, "won": won, "opponent": opp, "team": "Spirit", "source": "hltv"}


def _pick(scope, line, side, prop="kills"):
    return {"id": 1, "player": "tN1R", "team": "Spirit", "opponent": "1win", "match_start": "2026-10-06T12:00:00Z",
            "prop": prop, "scope": scope, "line": line, "pick": side}


def test_settlement_map1_uses_map1_only(monkeypatch):
    import picklog
    _fake_hltv(monkeypatch, [_row(1, 11, 6, False), _row(2, 9, 6, False)])       # the tN1R incident
    res = picklog.scope_result(_pick("1", 13.5, "More"))
    assert res["status"] == "ok" and res["total"] == 11 and res["maps_used"] == [1]
    assert picklog._outcome(_pick("1", 13.5, "More"), res["total"], res["maps"])[3] == 0      # Under -> More loses
    res12 = picklog.scope_result(_pick("12", 19.5, "More"))
    assert res12["total"] == 20 and res12["maps_used"] == [1, 2]


def test_settlement_maps123_and_ambiguity(monkeypatch):
    import picklog
    _fake_hltv(monkeypatch, [_row(1, 11, 6, True), _row(2, 9, 6, False)])
    assert picklog.scope_result(_pick("123", 30.5, "More"))["status"] == "wait"              # 1-1, map 3 not in
    _fake_hltv(monkeypatch, [_row(1, 11, 6, True), _row(2, 9, 6, False), _row(3, 14, 5, True)])
    assert picklog.scope_result(_pick("123", 30.5, "More"))["total"] == 34
    _fake_hltv(monkeypatch, [_row(1, 11, 6, True), _row(1, 15, 6, True, mid=8)])             # two matches: never guess
    assert picklog.scope_result(_pick("1", 13.5, "More"))["status"] == "error"


def test_settlement_hs_missing_waits(monkeypatch):
    import picklog
    _fake_hltv(monkeypatch, [_row(1, 11, None, False), _row(2, 9, 4, False)])
    assert picklog.scope_result(_pick("1", 5.5, "More", prop="hs"))["status"] == "wait"


def _fake_hltv_ff(monkeypatch, series, forfeits):
    import hltv

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, args=None): self.q = q
        def fetchone(self): return (forfeits,)

    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(__import__("picklog"), "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: series)


def test_forfeit_voids_every_scope(monkeypatch):
    import picklog
    _fake_hltv_ff(monkeypatch, [_row(1, 11, 6, True), _row(2, 0, 0, True)], forfeits=1)
    for sc in ("1", "12", "123"):
        assert picklog.scope_result(_pick(sc, 10.5, "More"))["status"] == "void"
    assert picklog._hltv_totals(_pick("12", 19.5, "More"))[0] == "void"


def test_maps12_ambiguous_match_is_not_settled(monkeypatch):
    import picklog
    _fake_hltv_ff(monkeypatch, [_row(1, 11, 6, True), _row(2, 9, 6, True), _row(1, 20, 9, True, mid=8), _row(2, 20, 9, True, mid=8)], forfeits=0)
    assert picklog.scope_result(_pick("12", 19.5, "More"))["status"] == "error"
    assert picklog._hltv_totals(_pick("12", 19.5, "More")) is None          # left open, never the first match


def test_same_day_only_drops_earlier_meeting(monkeypatch):
    import picklog
    early = [dict(_row(1, 30, 9, True, mid=5), date="2026-10-05"), dict(_row(2, 30, 9, True, mid=5), date="2026-10-05")]
    _fake_hltv_ff(monkeypatch, early + [_row(1, 11, 6, True), _row(2, 9, 6, True)], forfeits=0)
    res = picklog.scope_result(_pick("12", 19.5, "More"), same_day_only=True)
    assert res["status"] == "ok" and res["total"] == 20


def test_stale_settlement_detail_is_refreshed_and_logged(monkeypatch, tmp_path):
    """2026-10-09 audit: the 2026-10-08 correction fixed totals but left Maps 1-2 detail on Maps 1-3 picks."""
    import json, sqlite3, picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "p.db"); monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "c.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    con = picklog._db()
    stale = {"source": "hltv", "rounds": [20, 20], "won": [True, False], "per_map": [[11, 6], [9, 6]]}     # maps 1-2 only
    con.execute("INSERT INTO picks (id, player, team, opponent, match_start, prop, scope, line, pick, status, actual, hit, result, test) "
                "VALUES (1, 'tN1R', 'Spirit', '1win', '2026-10-06T12:00:00Z', 'kills', '123', 30.5, 'More', 'settled', 34, 1, ?, 0)",
                (json.dumps(stale),))
    con.commit(); con.close()
    _fake_hltv(monkeypatch, [_row(1, 11, 6, True), _row(2, 9, 6, False), _row(3, 14, 5, True)])
    dry = picklog.fix_scope_settlements(apply=False, scopes=("123",))
    assert dry["differs"] == [] and len(dry["stale_details"]) == 1 and not (tmp_path / "c.jsonl").exists()   # dry run changes nothing
    picklog.fix_scope_settlements(apply=True, scopes=("123",))
    r = sqlite3.connect(tmp_path / "p.db").execute("SELECT actual, status, hit, result FROM picks WHERE id=1").fetchone()
    assert r[:3] == (34, "settled", 1) and json.loads(r[3])["per_map"] == [[11, 6], [9, 6], [14, 5]]      # result untouched, detail fixed
    log = [json.loads(x) for x in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[0]["old_detail"] == stale and "stale settlement detail" in log[0]["reason"]                  # old kept
    assert picklog.fix_scope_settlements(apply=False, scopes=("123",))["stale_details"] == []
