"""Challenger prior (shadow 'prior_smooth', 2026-10-09): re-scaling the live projection must equal a full perf.project
run with prior target m(r) = 1 - (1 - m0) * T / (T + r) - for thin and established players, LAN / online / unknown."""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import perf, picklog


def _maps(n, day, kpr=0.72):
    return [(day - 3 * i - 1, round(kpr * 21), 21, 9, i % 2 == 0, True) for i in range(n)]


def test_shadow_equals_full_reprojection():
    day = 740000
    opp = [(day - 5 - i, 70, 22, None, True, True) for i in range(20)]
    for n in (0, 3, 15, 60, 200):
        for lan in (True, False, None):
            for prop in ("kills", "hs"):
                pm = _maps(n, day)
                live = perf.project(pm, opp, lan, day, "12", prop)
                sh = picklog.smooth_prior_pf(live)
                r = live["history_rounds_weighted"]
                m = 1 - (1 - picklog.PRIOR_SMOOTH["m0"]) * picklog.PRIOR_SMOOTH["T"] / (picklog.PRIOR_SMOOTH["T"] + r)
                full = perf.project(pm, opp, lan, day, "12", prop, dict(perf.PARAMS, prior_m=m))
                for k in ("lambda", "lambda_kills", "rate", "sd"):
                    assert math.isclose(sh[k], full[k], rel_tol=1e-9), (n, lan, prop, k)
                assert sh["prior_target"] == round(m, 4) and sh["version"].endswith("-prior-smooth")
                assert sh["lambda"] >= live["lambda"] - 1e-12          # target >= 0.93 always: never lower than live


def test_not_for_other_models():
    assert picklog.smooth_prior_pf({"version": "perf-1", "rate": 0.7, "history_rounds_weighted": 100}) is None
    assert picklog.smooth_prior_pf(None) is None
