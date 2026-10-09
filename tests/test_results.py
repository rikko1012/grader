"""Results page summaries: cohorts kept apart, filters, predicted vs actual, pending, payout-based returns absent."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import results

def _r(i, test, status, hit, grade="B", p=0.6, units=0.5, v="V1", prop="kills", scope="12", src="hltv", m=None):
    return {"id": i, "test": test, "status": status, "hit": hit, "grade": grade, "p_pick": p, "units": units, "model_version": v,
            "prop": prop, "scope": scope, "match_slug": m or f"m{i}", "match_start": "2026-10-09T12:00:00Z", "player": f"p{i}",
            "result": '{"source": "%s"}' % src}

ROWS = [_r(1, 2, "settled", 1), _r(2, 2, "settled", 0, grade="A", p=0.75, units=2), _r(3, 2, "open", None), _r(4, 2, "push", None),
        _r(5, 0, "settled", 1, v="V0"), _r(6, 1, "settled", 1), _r(7, 2, "settled", 1, grade="LEAN", p=0.52, units=0, v="V2", prop="hs", src="bo3")]

def test_cohorts_never_mix_and_tests_hidden():
    b = results.summary("board", rows=ROWS); m = results.summary("member", rows=ROWS)
    assert b["picks"] == 5 and m["picks"] == 1                                   # owner test (test=1) in neither
    assert b["pending"] == 1 and b["push"] == 1 and b["decided"] == 3 and b["hits"] == 2

def test_filters_and_grade_table():
    s = results.summary("board", version="V1", rows=ROWS)
    assert s["decided"] == 2 and {g["grade"] for g in s["by_grade"]} == {"A", "B"}
    assert abs(s["predicted"] - 0.675) < 1e-9 and s["actual"] == 0.5
    assert results.summary("board", prop="hs", rows=ROWS)["decided"] == 1
    assert results.summary("board", grades=["LEAN"], rows=ROWS)["by_grade"][0]["grade"] == "LEAN"

def test_returns_are_labelled_and_payout_based_absent():
    s = results.summary("board", rows=ROWS)
    assert s["returns"]["payout_based"] is None and "Not available" in s["returns"]["payout_note"]
    assert abs(s["returns"]["hypothetical_units"] - (0.5 * 0.9 - 2.0)) < 1e-9 and "Hypothetical" in s["returns"]["hypothetical_note"]
    assert s["sources"] == {"hltv": 2, "bo3": 1}


def test_member_lines_get_no_hypothetical_price():
    m = results.summary("member", rows=ROWS)
    assert m["returns"]["hypothetical_units"] is None and "goblin" in m["returns"]["hypothetical_note"]
    assert m["line_types"] == {"standard": 1}
