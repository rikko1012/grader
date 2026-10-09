"""Diagnostic dashboard cohorts: first published vs evaluation snapshot (latest 30+ min before start), exclusions,
owner tests hidden, groups never mixed."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import diagnostics

def _p(i, test=2, status="settled", actual=31.0, line=30.5, pick="More", hit=1, version="V1", prop="kills", logged="2026-10-09T10:00:00+00:00"):
    return {"id": i, "test": test, "status": status, "actual": actual, "line": line, "pick": pick, "hit": hit, "model_version": version,
            "prop": prop, "scope": "12", "match_slug": f"m{i}", "match_start": "2026-10-09T12:00:00.000+00:00", "logged_at": logged,
            "p_pick": 0.6, "lambda": 31.0, "grade": "B", "units": 0.5, "result": '{"source": "hltv"}'}

def _f(pid, logged, line=30.5, pick="More", version="V1"):
    return {"pick_id": pid, "logged_at": logged, "line": line, "pick": pick, "p_pick": 0.6, "lambda": 31.0, "grade": "B", "units": 0.5,
            "model_version": version, "inputs": None}

def test_cohorts_and_exclusions():
    P = [_p(1), _p(2, test=1), _p(3, status="open", hit=None, actual=None), _p(4, line=1675.0), _p(5, logged="2026-10-09T12:05:00+00:00")]
    F = [_f(1, "2026-10-09T09:00:00+00:00", line=29.5), _f(1, "2026-10-09T11:20:00+00:00", line=31.5, pick="Less"),
         _f(1, "2026-10-09T11:50:00+00:00", line=33.5)]                  # 10 min before the start: not eligible
    d = diagnostics.build(P, F, [])
    first = d["cohorts"]["first"]["summary"]; ev = d["cohorts"]["eval"]["summary"]
    assert sum(s["n"] for s in first) == 1                                     # owner test, open, typo line, late forecast out
    assert d["exclusions"]["implausible line (typo)"] == 1 and d["exclusions"]["pending"] == 1
    assert d["exclusions"]["first forecast after the start"] == 1
    assert len(ev) == 1 and ev[0]["n"] == 1 and ev[0]["hit_rate"] == 1.0       # latest 30+ min before: Less 31.5, actual 31 -> hit

def test_groups_never_mixed():
    P = [_p(1, version="V1"), _p(2, version="V2"), _p(3, prop="hs", actual=15.0, line=14.5), _p(4, test=0)]
    d = diagnostics.build(P, [], [])
    keys = {(s["source"], s["prop"], s["version"]) for s in d["cohorts"]["first"]["summary"]}
    assert keys == {("board", "kills", "V1"), ("board", "kills", "V2"), ("board", "hs", "V1"), ("member", "kills", "V1")}


def test_forward_hs_bands():
    import json
    st = "2026-10-11T12:00:00.000+00:00"
    P = [dict(_p(i, prop="hs", actual=a, line=14.5), match_start=st, logged_at="2026-10-11T09:00:00+00:00") for i, a in ((1, 16.0), (2, 12.0), (3, 13.0))]
    P.append(dict(_p(4, prop="hs", actual=16.0, line=14.5), match_start="2026-10-09T12:00:00.000+00:00"))     # before FORWARD_FROM
    F = [dict(_f(i, "2026-10-11T10:00:00+00:00", line=14.5, pick="Less"), p_pick=0.6, p_more=0.4,
              shadow=json.dumps({"perf1_g7": {"p_more": 0.5}, "prior_smooth": {"p_more": 0.42}})) for i in (1, 2, 3)]
    F.append(dict(_f(4, "2026-10-09T10:00:00+00:00", line=14.5), p_more=0.6))
    fw = diagnostics.build(P, F, [])["forward_hs"]
    assert fw["lines"] == 3 and fw["matches"] == 3                              # the pre-registration forecast is out
    cur, prev, sm = fw["models"]
    assert cur["model"] == "current" and [b["n"] for b in cur["bands"]] == [0, 3, 0, 0, 0]
    assert cur["bands"][1]["predicted"] == 0.4 and abs(cur["bands"][1]["actual"] - 1 / 3) < 1e-3
    assert [b["n"] for b in prev["bands"]] == [0, 0, 3, 0, 0] and abs(prev["gap"] - (1 / 3 - 0.5)) < 1e-3
    assert sm["bands"][1]["predicted"] == 0.42
