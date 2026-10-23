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


# ---- AMENDMENT 2026-10-23 (research/cs2_accuracy_2y/AMENDMENT_2026-10-23.md): decision level + th-1 look counter
def _th1_con(tmp_path, n_cell, n_cohort, gain=True):
    """n_cell forward matches with a kills Map 1 perf-2 unit outside th-1's cohort (th-1 entry not eligible, history >= 150);
    the first n_cohort of them also carry a th-1 cohort unit."""
    con = learning.connect(tmp_path / "learning.db")
    us, fid = [], 0
    for i in range(n_cell):
        start = f"2026-10-{24 + i // 200:02d}T{(i % 200) // 10:02d}:{i % 10}0:00+00:00"
        fid += 1
        con.execute("INSERT INTO forecast_copy (forecast_id, cands) VALUES (?, ?)", (fid, json.dumps(
            {"th1": {"eligible": False, "reason": "history >= 150 weighted rounds (not in the th-1 cohort)", "live": {"p_more": 0.6}}})))
        u = _unit(fid, f"m{i:04d}", 0.6, 1)
        u["start"] = start
        us.append(u)
        if i < n_cohort:
            fid += 1
            con.execute("INSERT INTO forecast_copy (forecast_id, cands) VALUES (?, ?)", (fid, json.dumps(
                {"th1": {"eligible": True, "p_more": 0.45 if gain else 0.7, "live_replay": 0.6, "live": {"p_more": 0.6}}})))
            u = _unit(fid, f"m{i:04d}", 0.6, i % 3 == 0)
            u["start"] = start
            us.append(u)
    return con, us


def test_th1_look_counts_its_cell_not_only_the_cohort(tmp_path):
    """REGISTER 6d9bb32: looks at 300 distinct forward matches with an eligible logged unit, 'th-1 ADDITIONALLY needs >= 60
    distinct cohort matches' - the look counter is th-1's cell (kills Map 1 on perf-2), the 60 counts cohort matches."""
    con, us = _th1_con(tmp_path, 300, 60)
    rep = learning.cands_report(con, us, "th1", B=50)
    assert rep["eligible_matches"] == 60 and rep["look_matches"] == 300 and rep["look_reached"] == 300
    (lk,) = rep["looks"]
    assert lk["matches"] == 300 and lk["cohort_matches"] == 60 and lk["cohort_ok"] is True and lk["units"] == 60
    assert lk["decision"]["cohort_ok"] is True and lk["decision"]["units"] == 60      # statistics on cohort units only


def test_th1_look_skipped_below_60_cohort_matches(tmp_path):
    con, us = _th1_con(tmp_path, 300, 59)
    (lk,) = learning.cands_report(con, us, "th1", B=50)["looks"]
    assert lk["cohort_ok"] is False and lk["decision"]["rule_met"] is False and "skipped" in lk["decision"]
    (tmp_path / "b").mkdir()
    con2, us2 = _th1_con(tmp_path / "b", 299, 0)
    rep = learning.cands_report(con2, us2, "th1", B=50)
    assert rep["look_matches"] == 299 and rep["look_reached"] is None and rep["next_look"] == 300


def test_amended_decision_level_is_bonferroni_over_2_candidates_x_3_looks():
    cfg = learning.CANDS_AMENDMENT
    assert cfg["tests"] == 6 and abs(cfg["alpha_one_sided_per_test"] - 0.025 / 6) < 1e-15
    assert learning.CANDS_REVIEW["rm1"]["looks"] == cfg["looks"] == [300, 600, 1000]
    assert "95% match-grouped interval is above 0" in learning.CANDS_RULE          # registered text kept verbatim
    import numpy as np
    lc = []
    for m in range(40):
        for k in range(3):
            y = int((m * 3 + k) % 4 != 0)
            lc.append({"match": f"m{m:02d}", "over": y, "p_logged": 0.55, "p_ch": 0.6 if m % 5 else 0.5})
    d = learning.cands_decision(lc)
    # independent recomputation of the decision bound: the floor(alpha B)-th order statistic of the clustered bootstrap
    ms = sorted({u["match"] for u in lc})
    s = np.array([sum(learning._ll(u["p_logged"], u["over"]) - learning._ll(u["p_ch"], u["over"]) for u in lc if u["match"] == m) for m in ms])
    c = np.array([sum(1 for u in lc if u["match"] == m) for m in ms])
    rat = np.sort(np.concatenate([(W @ s) / (W @ c) for W in learning._boot_weights(len(ms), cfg["B"], cfg["seed"])]))
    assert d["primary_lower_bound"] == round(float(rat[int(math.floor(cfg["alpha_one_sided_per_test"] * cfg["B"]))]), 5)
    assert d["two_sided_equivalent"] == round(1 - 2 * 0.025 / 6, 5) and d["promotion"].startswith("never automatic")
    assert d["primary_met"] == (d["primary_lower_bound"] > 0)


def test_amended_decision_guards_block_a_worse_calibrated_candidate():
    lc = [{"match": f"m{i // 4}", "over": int(i % 10 < 7), "p_logged": 0.7, "p_ch": 0.95} for i in range(400)]
    d = learning.cands_decision(lc)
    assert d["calibration"]["Over"]["met"] is False and d["calibration"]["Over"]["diff"] > 0.2 and d["rule_met"] is False
    good = [{"match": f"m{i // 4}", "over": int(i % 10 < 7), "p_logged": 0.5 + 0.001, "p_ch": 0.7} for i in range(4000)]
    g = learning.cands_decision(good)
    assert g["primary_met"] and g["brier_met"] and g["calibration"]["Over"]["met"] and g["rule_met"] is True
    assert learning.cands_decision(good, coverage_gaps=1)["rule_met"] is False


def test_rm1_look_reports_the_amended_decision(tmp_path):
    con = learning.connect(tmp_path / "learning.db")
    us = []
    for i in range(1, 301):
        con.execute("INSERT INTO forecast_copy (forecast_id, cands) VALUES (?, ?)",
                    (i, json.dumps({"rm1": {"eligible": True, "p_more": 0.5, "live_replay": 0.5, "live": {"p_more": 0.5}}})))
        u = _unit(i, f"m{i:03d}", 0.5, 0)
        u["start"] = f"2026-10-{24 + i // 100:02d}T{(i % 100) // 10:02d}:{i % 10}0:00+00:00"
        us.append(u)
    rep = learning.cands_report(con, us, "rm1", B=50)
    (lk,) = rep["looks"]
    assert rep["review"]["rule"] == learning.CANDS_RULE_AMENDED and rep["review"]["rule_registered"] == learning.CANDS_RULE
    assert lk["decision"]["primary_lower_bound"] == 0 and lk["decision"]["primary_met"] is False and lk["decision"]["rule_met"] is False
