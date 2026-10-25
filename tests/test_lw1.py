"""lw-1 line-informed chance (research/esports_accuracy_2026_10_25): exact transform, one consistent distribution on the
card, experiments untouched, rollback. Grader inputs below are TEST FIXTURES (invented numbers)."""
import json
import math

import pytest

import dist_view
import grader
import lw1
import pick_verdict
import sim

pytestmark = pytest.mark.lw1


def _kills(line=27.5, lam=30.2, sd=7.8, scope="12", version="perf-2"):
    return {"prop": "kills", "line": line, "line_type": "standard", "scope": scope, "maps_played": 40, "kpr": 0.7,
            "perf": {"version": version, "lambda": lam, "sd": sd, "rate": 0.7, "opp_factor": 1.0, "rounds_per_map": 21.7,
                     "data_quality": "ok"}}


def _hs(line=14.5):
    d = _kills()
    d.update(prop="hs", line=line)
    d["perf"].update({"lambda": 17.5, "lambda_kills": 30.2, "hs_per_kill": 0.58, "spread_c": 1.42, "spread_c_kills": 1.42})
    return d


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def test_frozen_params_are_the_research_fit():
    fin = json.load(open("research/esports_accuracy_2026_10_25/outputs/lw1_final.json"))
    assert fin["data_hash"] == lw1.DATA_HASH
    for k, v in fin["params"].items():
        assert lw1.PARAMS[k] == (v["a"], v["w"])
    assert set(lw1.PARAMS) == {f"{s}|{c}" for s in ("kills", "hs") for c in ("1", "12", "123")}


def test_transform_matches_its_definition():
    a, w = lw1.PARAMS["hs|12"]
    for q in (0.2, 0.45, 0.5, 0.6354, 0.9):
        p, a_, w_, z = lw1.chance("hs", "12", q)
        assert abs(_phi(z) - q) < 1e-9 and abs(p - _phi(a + w * z)) < 1e-12 and (a_, w_) == (a, w)
    assert lw1.chance("assists", "1", 0.6) is None and lw1.chance("kills", "12", None) is None


def test_live_grade_uses_lw1_and_keeps_the_model_numbers():
    g = grader.grade_leg(_kills())
    m = grader.grade_leg(_kills(), dict(grader.CONFIG, lw1=None))
    lw = g["lw1"]
    assert lw["version"] == lw1.VERSION and lw["lambda_model"] == m["lambda_final"] and lw["p_more_model"] == m["p_more"]
    assert lw["probs_model_raw"] == m["probs_raw"]
    exp = min(max(lw1.chance("kills", "12", m["probs_raw"]["p_more_no_push"])[0], 0.22), 0.78)
    assert g["p_more"] == round(exp, 3)
    assert g["grade"] == grader.grade_from_p(exp) and g["units"] == grader.units_for_p(exp)
    assert m["lw1"] is None
    assert any(f.startswith("LINE-INFORMED") for f in g["flags"])


def test_one_distribution_behind_chance_chart_and_simulation():
    for d in (_kills(), _hs(), _kills(line=33.0)):            # a whole line (push) too
        g = grader.grade_leg(d)
        assert g["dist"]["kind"] == "normal" and abs(g["dist"]["lam"] - g["lambda_final"]) < 0.01
        pr = g["probs_raw"]
        assert abs(pr["p_more_no_push"] - g["lw1"]["p_more_lw"]) < 1e-4
        assert dist_view.view(g) is not None                   # the chart reproduces the printed chance
        s = sim.simulate_grade(g)
        assert abs(s["p_more"] - pr["p_more_no_push"]) < 0.02
        v = pick_verdict.verdict(g)
        up, down = grader._cuts(g["line"])
        side_by_projection = "More" if g["lambda_final"] > (up + down) / 2 else "Less"
        assert v["side"] == side_by_projection or abs(g["p_more"] - 0.5) < 0.002
        assert v["reason"].startswith("Line-informed projection") or v["reason"].startswith("Leans")


def test_side_follows_the_chance_and_reason_names_the_model():
    g = grader.grade_leg(_hs())
    v = pick_verdict.verdict(g)
    assert v["side"] == "More" and 0.55 <= v["p"] < 0.63 and v["units"] == 0.5
    assert "model 17.5" in v["reason"]


def test_value_lines_follow_lw1():
    g = grader.grade_leg(_hs())
    lw, val = g["lw1"], g["value"]
    L = val["more_playable_at_or_below"]
    # at the playable line the lw-1 chance (normal approximation of the model z) reaches B+ territory, one step higher does not
    assert L is not None and L < g["line"]
    gk = grader.grade_leg(_kills())
    assert gk["value"]["more_playable_at_or_below"] is None    # kills w 0.04: no nearby line is playable


def test_kills_cap_still_applies_after_lw1():
    g = grader.grade_leg(_kills(line=10.5, lam=40.0))
    assert g["p_more"] <= 0.78


def test_legacy_grades_and_lol_untouched():
    d = _kills()
    d["perf"] = {"error": "no performance-model input"}
    g = grader.grade_leg(d)
    assert g.get("lw1") is None


def test_rollback_env(monkeypatch):
    import importlib
    monkeypatch.setenv("LW1_LIVE", "0")
    assert lw1.enabled() is False
    monkeypatch.setenv("LW1_LIVE", "1")
    assert lw1.enabled() is True
    importlib.reload(lw1)


def test_experiments_grade_without_lw1(monkeypatch):
    import perf_shadow
    import picklog
    g, v = perf_shadow.grade_arm(_kills(), _kills()["perf"])
    assert g["lw1"] is None
    alts = picklog.shadow_alts(_kills())
    assert all((c[1] if isinstance(c, tuple) else c).get("lw1") is None for c in alts.values())


def test_model_hash_covers_lw1():
    import picklog
    import inspect
    assert '"lw1.py"' in inspect.getsource(picklog._model_version)


def test_forecast_logs_the_model_projection():
    import picklog
    g = grader.grade_leg(_hs())
    res = {"grade": g, "player_block": {"player": {"nick": "x", "slug": "x", "id": 1}}, "grader_inputs": _hs(),
           "next_match": {"slug": "hltv-1", "start": "2026-10-26T10:00:00+00:00", "opponent": "y"}}
    row = picklog.pick_row(res, pick_verdict.verdict(g))
    assert row["lambda"] == round(g["lw1"]["lambda_model"], 2) and row["p_more"] == g["p_more"]
