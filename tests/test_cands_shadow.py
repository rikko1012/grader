"""rm-1 / th-1 picklog shadows (CHALLENGERS.md, frozen 2026-10-23; research/cs2-accuracy-2y CANDIDATES.md). Live grades must be
byte-identical; only the registered cells are eligible; values equal the research implementation (candidates/lib.py) on the
frozen constants. Inputs = the labelled fixture tests/fixtures/grader_inputs_sample.json (one production forecast's inputs)."""
import copy
import json
import math
from pathlib import Path

import cands_shadow as cs
import grader
import learning
import picklog

GI = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))["inputs"]


def map1(prop="kills", line=14.5, hist=None, version="perf-2"):
    """The fixture's projection as a one-map (Map 1) perf-2 input: half the Maps 1-2 lambda, the Map 1 spreads."""
    gi = copy.deepcopy(GI)
    pf = gi["perf"]
    lam_k = pf["lambda_kills"] / 2
    c = 1.442 if prop == "kills" else 1.241
    lam = lam_k if prop == "kills" else lam_k * pf["hs_per_kill"]
    pf.update(version=version, lambda_kills=lam_k, maps=1, spread_c=c, spread_c_kills=1.442, sd=c * math.sqrt(max(lam, 1.0)),
              **{"lambda": lam})
    pf.pop("g7", None)
    if hist is not None:
        pf["history_rounds_weighted"] = hist
    return dict(gi, prop=prop, scope="1", line=line)


def dump(x):
    return json.dumps(x, sort_keys=True, default=str)


def test_frozen_constants():
    assert cs.CONSTANTS_SHA == "3d00b8c801fb160f96368e8e5b743f4d699fbac2fd920a610febef0db0744b33"
    assert abs(cs.PI_Q - 0.11354947239639089) < 1e-15
    assert abs(sum(cs.PI_REG.values()) - 1) < 1e-9 and abs(sum(cs.PI_OT.values()) - 1) < 1e-9
    assert min(cs.PI_OT) > 24 and max(cs.PI_REG) == 24 and min(cs.PI_REG) == 13
    assert cs.RM1["cells"] == [["kills", "1"], ["kills", "123"], ["hs", "1"]] and cs.TH1["cells"] == [["kills", "1"]]


def test_live_grades_byte_identical_and_shadow_logged():
    cases = [map1("kills", 14.5), map1("hs", 7.5), map1("kills", 14.5, hist=30.0), dict(copy.deepcopy(GI), prop="kills", line=29.0),
             dict(copy.deepcopy(GI), perf=None, prop="kills", line=24.5)]
    cfg_before = dump(grader.CONFIG)
    for gi in cases:
        before = dump(grader.grade_leg(copy.deepcopy(gi)))
        g = grader.grade_leg(gi)
        f = picklog._factors({"grader_inputs": gi, "grade": g})
        assert dump(g) == before and dump(grader.grade_leg(copy.deepcopy(gi))) == before
        assert dump(grader.CONFIG) == cfg_before
        assert set(f["shadow"]["cands"]) == {"rm1", "th1"}
        assert f["shadow"]["cands"]["rm1"]["version"] == cs.RM1["version"]


def test_rm1_eligible_cells_and_live_replay():
    for gi in (map1("kills", 14.5), map1("hs", 7.5), dict(map1("kills", 43.5), scope="123")):
        if gi["scope"] == "123":
            pf = gi["perf"]
            pf.update(lambda_kills=pf["lambda_kills"] * 3, maps=3, spread_c=1.457, spread_c_kills=1.457)
            pf["lambda"] = pf["lambda_kills"]
            pf["sd"] = 1.457 * math.sqrt(pf["lambda"])
        g = grader.grade_leg(copy.deepcopy(gi))
        e = cs.rm1_entry(gi, g)
        assert e["eligible"] is True and e["live"]["p_more"] == g["p_more"]
        assert abs(e["live_replay"] - g["p_more"]) < 6e-4                # the same live chance, rebuilt (logged rounding)
        assert set(e["raw"]) == {"over", "under", "push"} and set(e["rec"]) == {"side"}
        assert 0.22 <= e["p_more"] <= 0.78 or gi["prop"] == "hs"


def test_rm1_keeps_the_mean_and_map1_is_the_one_map_case():
    comps = cs._components(15.2, 1.442, "1", cs.RHO["1_p2"])
    assert abs(sum(w * mu for w, mu, _ in comps) - 15.2) < 1e-9          # mean exactly the live lambda
    one = dict(cs.pi_scope(1))
    two = {}
    for a, pa in one.items():
        for b, pb in one.items():
            two[a + b] = two.get(a + b, 0.0) + pa * pb
    for r, p in cs.pi_scope(2):
        assert abs(p - two[r] / sum(v for v in two.values() if v > 1e-9)) < 1e-9   # Maps 1-2 rounds = Map 1 + Map 2


def test_values_equal_research_implementation():
    # reference numbers from research/cs2_accuracy_2y/candidates/lib.py on params_FINAL.json
    po, pu, _ = cs.kills_probs(14.5, cs._components(15.2, 1.442, "1", cs.RHO["1_p2"]))
    assert abs(po / (po + pu) - 0.5215502919003747) < 1e-9
    po, pu, _ = cs.kills_probs(43.5, cs._components(44.0, 1.457, "123", cs.RHO["123_p2"]))
    assert abs(po / (po + pu) - 0.4998831480555309) < 1e-9
    o, u, _ = cs.hs_probs(cs.kills_pmf(cs._components(15.2, 1.442, "1", cs.RHO["1_p2"])), 0.5, 7.5)
    assert abs(o / (o + u) - 0.4714549951493403) < 1e-7
    a, s = cs.TH["p2_1_D0"]["a"], cs.TH["p2_1_D0"]["s"]
    lam = 15.2 * math.exp(a)
    po, pu, _ = cs.kills_probs(14.5, [(1.0, lam, 1.442 * math.exp(s) * math.sqrt(lam))])
    assert abs(po / (po + pu) - 0.43669041181247914) < 1e-9


def test_not_eligible_outside_cells_and_off_perf():
    gi = dict(copy.deepcopy(GI), prop="kills", line=29.0)                 # Maps 1-2: not an rm-1 / th-1 cell
    g = grader.grade_leg(copy.deepcopy(gi))
    for e in cs.entries(gi, g).values():
        assert e["eligible"] is False and "p_more" not in e and "raw" not in e
    gi = dict(copy.deepcopy(GI), perf=None, prop="kills", scope="1", line=14.5)   # old pipeline
    g = grader.grade_leg(copy.deepcopy(gi))
    for e in cs.entries(gi, g).values():
        assert e["eligible"] is False and "p_more" not in e


def test_th1_cohort_and_bands():
    gi = map1("kills", 14.5, hist=800.0)
    assert cs.th1_entry(gi, grader.grade_leg(copy.deepcopy(gi)))["eligible"] is False
    for hist, band in ((30.0, "D0"), (120.0, "D1")):
        gi = map1("kills", 14.5, hist=hist)
        g = grader.grade_leg(copy.deepcopy(gi))
        e = cs.th1_entry(gi, g)
        assert e["eligible"] is True and e["band"] == band
        assert abs(e["lambda"] - gi["perf"]["lambda"] * math.exp(cs.TH[f"p2_1_{band}"]["a"])) < 1e-9
        assert abs(e["live_replay"] - g["p_more"]) < 6e-4
    gi = map1("kills", 14.5, hist=30.0, version="perf-2-bo3")            # BO3.gg route: the perf-2 band of its depth
    g = grader.grade_leg(copy.deepcopy(gi))
    if g.get("projection_source") == "perf-2-bo3":
        assert cs.th1_entry(gi, g)["eligible"] is True
    assert cs.th1_entry(map1("hs", 7.5, hist=30.0), {"projection_source": "perf-2", "p_more": 0.5})["eligible"] is False


def _unit(fid, match, p, over, logged="2026-10-24T10:00:00+00:00"):
    return {"forecast_id": fid, "match": match, "p": p, "over": over, "prop": "kills", "scope": "1", "version": "v1",
            "logged": logged, "start": "2026-10-24T12:00:00+00:00", "units": 0.5, "lam": 15.0, "actual": 16, "line": 14.5}


def test_learning_copies_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    c = picklog._db()
    cd = {"rm1": {"id": "rm-1", "eligible": True, "p_more": 0.55}, "th1": {"id": "th-1", "eligible": False, "reason": "x"}}
    c.execute("INSERT INTO forecasts (pick_id, kind, logged_at, line, pick, p_pick, p_more, grade, units, shadow) "
              "VALUES (1, 'first', '2026-10-24T10:00:00+00:00', 14.5, 'More', 0.58, 0.58, 'B', 0.5, ?)",
              (json.dumps({"lm0": {"id": "lm-0"}, "cands": cd}),))
    c.commit()
    c.close()
    _, fr = learning.read_picklog(tmp_path / "picks.db")
    assert json.loads(learning._cands_of(fr[0])) == cd and "shadow" not in fr[0]
    con = learning.connect(tmp_path / "learning.db")
    rows = {1: {"rm1": {"eligible": True, "p_more": 0.52, "live_replay": 0.58, "live": {"p_more": 0.58}}},
            2: {"rm1": {"eligible": True, "p_more": 0.40, "live_replay": 0.45, "live": {"p_more": 0.45}}},
            3: {"rm1": {"eligible": False, "reason": "not a rm-1 cell (kills 12)"}},
            5: {"rm1": {"eligible": True, "p_more": 0.6, "live": {"p_more": 0.51}}}}
    for fid in (1, 2, 3, 4, 5):
        con.execute("INSERT INTO forecast_copy (forecast_id, cands) VALUES (?, ?)", (fid, json.dumps(rows[fid]) if fid in rows else None))
    us = [_unit(1, "m1", 0.58, 0), _unit(2, "m2", 0.45, 0), _unit(3, "m3", 0.6, 1), _unit(4, "m4", 0.6, 1), _unit(5, "m5", 0.55, 1)]
    rep = learning.cands_report(con, us, "rm1")
    assert rep["eligible_units"] == 2 and rep["eligible_matches"] == 2 and rep["look_reached"] is None and rep["next_look"] == 300
    assert rep["counts"] == {"not eligible (not a rm-1 cell (kills 12))": 1, "not logged (forecast before rm-1)": 1,
                             "rejected (stored live chance differs from the P1 chance)": 1}
    assert rep["paired"]["gain_vs_logged"] > 0 and rep["brier"]["gain"] > 0       # nearer on both Unders that hit
    assert rep["favoured_side"]["rm-1"]["Under"]["picks"] == 1 and rep["favoured_side"]["live"]["Over"]["picks"] == 1
    assert learning.cands_report(con, us, "th1")["eligible_units"] == 0


def test_cands_look_uses_the_frozen_first_w_matches(tmp_path):
    """REVIEW_RULE cohort: the look figure is computed on the first W distinct forward matches by start, never on the running
    set that keeps growing past the window (closeout 2026-10-23)."""
    con = learning.connect(tmp_path / "learning.db")
    us = []
    for i in range(1, 303):                       # 302 matches; the last two start latest and lose badly for rm-1
        late = i > 300
        con.execute("INSERT INTO forecast_copy (forecast_id, cands) VALUES (?, ?)",
                    (i, json.dumps({"rm1": {"eligible": True, "p_more": 0.9 if late else 0.5, "live_replay": 0.5, "live": {"p_more": 0.5}}})))
        u = _unit(i, f"m{i:03d}", 0.5, 0)
        u["start"] = f"2026-10-{24 + i // 100:02d}T{(i % 100) // 10:02d}:{i % 10}0:00+00:00"
        us.append(u)
    rep = learning.cands_report(con, us, "rm1", B=50)
    assert rep["eligible_matches"] == 302 and rep["look_reached"] == 300 and rep["next_look"] == 600
    (lk,) = rep["looks"]
    assert lk["window"] == 300 and lk["matches"] == 300 and lk["units"] == 300 and lk["level"] == 0.95
    assert lk["log_loss_gain_vs_logged"] == 0 and rep["paired"]["gain_vs_logged"] < 0     # the 2 late matches are outside the look
    assert learning.cands_report(con, us[:299], "rm1", B=50)["looks"] == []
