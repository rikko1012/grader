"""Shared-match challenger M1 (shadow): structure and logging; never part of the live grade."""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import match_model, grader
from tests.test_perf import _inputs

def test_structure_and_means():
    k, h = match_model.simulate_totals(0.7, 0.5, [0.5, 0.5, 0.5], "12", runs=20000)
    assert abs(k.mean() - 0.7 * 2 * (match_model.R_VALS * match_model.R_P).sum()) < 0.4      # mean = rate x expected rounds
    assert (h <= k).all() and (h >= 0).all()
    k1, _ = match_model.simulate_totals(0.7, 0.5, [0.5], "1", runs=20000)
    assert abs(k1.mean() * 2 - k.mean()) < 0.6
    kw, _ = match_model.simulate_totals(0.7, 0.5, [0.9, 0.9], "12", runs=20000); kl, _ = match_model.simulate_totals(0.7, 0.5, [0.1, 0.1], "12", runs=20000)
    assert kw.mean() > kl.mean()                                                          # winners get more kills

def test_shadow_logged_not_live():
    import picklog
    d = _inputs(random.Random(3), "12")
    d["perf"] = dict(d["perf"], lambda_kills=d["perf"]["lambda"], maps=2, hs_per_kill=0.5)     # fields real perf outputs carry
    g = grader.grade_leg(dict(d))
    out = picklog._factors({"grade": g, "grader_inputs": dict(d), "player_block": {}})
    m1 = out["shadow"]["match_m1"]
    assert m1["version"] == match_model.VERSION and 0 <= m1["p_more"] <= 1
    assert abs(m1["raw"]["over"] + m1["raw"]["under"] + m1["raw"]["push"] - 1) < 1e-6
    assert grader.grade_leg(dict(d)) == g                                                  # live grade unchanged
