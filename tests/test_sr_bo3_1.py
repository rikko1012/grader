"""sr-bo3-1 (2026-10-28): 0 units on PROVISIONAL BO3.gg-route projections - a stake gate only. Letter, chance, side and
projection unchanged; HLTV-route rows untouched; reason shown like the other gd-1 0U gates; SR_BO3_1=0 rolls it back."""
import copy
import grader
import grade_display
from pick_verdict import verdict


def _leg(version):
    return {"prop": "kills", "line": 24.5, "line_type": "standard", "scope": "12", "maps_played": 30, "sample_maps": 60,
            "kpr": 0.70, "hs_pct": 48.0, "rating": 1.05, "teammate_ratings": [1.0, 0.98, 1.02, 0.95], "sniping": 5,
            "roster_confirmed": True, "p_team_market": 0.55, "market_quality": "HARD", "bo": 3,
            "recent_avg": 28.0, "recent_median": 28.0, "recent": {"n": 10, "overs": 7},
            "perf": {"version": version, "lambda": 28.0, "sd": 6.0, "rate": 0.65, "opp_factor": 1.0, "rounds_per_map": 21.5,
                     "data_quality": "ok"}}


def _cfg(on):
    cfg = copy.deepcopy(grader.CONFIG)
    cfg["sr_bo3_1"] = on
    return cfg


def test_gate_applies_only_to_the_provisional_route():
    bo3 = grader.grade_leg(_leg("perf-2-bo3"), _cfg(True))
    hltv = grader.grade_leg(_leg("perf-2"), _cfg(True))
    assert bo3["units"] == 0.0 and bo3["units_less"] == 0.0
    assert bo3["zero_reason"] == grader.SR_BO3_1_REASON
    assert any(f == "0 units: " + grader.SR_BO3_1_REASON for f in bo3["flags"])
    # HLTV route: same inputs, the stake is what the chance earns
    assert hltv["units"] > 0 and hltv["zero_reason"] is None
    assert not any(grader.SR_BO3_1_REASON in f for f in hltv["flags"])


def test_letter_chance_projection_and_side_unchanged():
    on = grader.grade_leg(_leg("perf-2-bo3"), _cfg(True))
    off = grader.grade_leg(_leg("perf-2-bo3"), _cfg(False))
    for k in ("p_more", "p_less", "grade", "grade_less", "model_grade", "lambda_final", "sd", "probs_raw", "projection_source"):
        assert on[k] == off[k], k
    assert on["grade"] in grader.GRADES and on["grade"] != "C"   # a real letter, kept
    assert off["units"] > 0 and on["units"] == 0.0               # only the stake moved
    assert on["units_free"] == off["units_free"]                  # what the chance alone earns is still reported


def test_verdict_and_display_show_the_letter_with_the_reason():
    g = grader.grade_leg(_leg("perf-2-bo3"), _cfg(True))
    v = verdict(g)
    assert v["side"] == "More" and v["units"] == 0.0 and v["grade"] == "LEAN"       # logged like every other 0U gate
    assert "Leans Over" in v["reason"] and grader.SR_BO3_1_REASON in v["reason"]
    d = grade_display.annotate(g, v)
    assert d["model_grade"] == g["grade"]                                            # gd-1: the model letter stays
    assert d["stake_reason"] == grader.SR_BO3_1_REASON
    assert d["available"] is True


def test_env_switch_rolls_back_without_a_deploy(monkeypatch):
    monkeypatch.setenv("SR_BO3_1", "0")
    assert grader.sr_bo3_1_enabled() is False
    g = grader.grade_leg(_leg("perf-2-bo3"))
    assert g["units"] > 0 and g["zero_reason"] is None
    monkeypatch.setenv("SR_BO3_1", "1")
    assert grader.sr_bo3_1_enabled() is True
    assert grader.grade_leg(_leg("perf-2-bo3"))["units"] == 0.0
    monkeypatch.delenv("SR_BO3_1")
    assert grader.sr_bo3_1_enabled() is True                                         # default: on


def test_other_gates_and_hltv_route_unchanged_by_the_switch():
    leg = _leg("perf-2"); leg["is_standin"] = True
    for on in (True, False):
        g = grader.grade_leg(leg, _cfg(on))
        assert g["units"] == 0.0 and g["zero_reason"] == "stand-in"
