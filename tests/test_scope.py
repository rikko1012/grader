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


def test_maps123_follows_series_result():
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, True)], "123", "kills") == (21, [1, 2])          # 2-0
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, False), m(3, 30, 9, True)], "123", "kills") == (51, [1, 2, 3])
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, False)], "123", "kills")[0] is None              # 1-1, map 3 missing
    assert scope_total([m(1, 10, 5, True), m(2, 11, 5, True), m(3, 9, 2, True)], "123", "kills")[0] is None  # bad data


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
