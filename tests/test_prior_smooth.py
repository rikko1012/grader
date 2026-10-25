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


def _real_inputs():
    """One production forecast's saved grader inputs (perf-2 + its G7 copy) in every stat / scope / history source."""
    import json
    from pathlib import Path
    gi = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))["inputs"]
    for prop, line in (("kills", 29.0), ("hs", 10.5)):
        for scope in ("12", "1", "123"):
            for bo3 in (False, True):
                pf = dict(gi["perf"])
                if bo3:                               # BO3.gg history: perf-2-bo3 live, perf-1-bo3 G7 copy
                    pf.update(version="perf-2-bo3", g7=dict(pf["g7"], version="perf-1-bo3"))
                yield dict(gi, prop=prop, line=line, scope=scope, perf=pf)


def test_perf_shadows_graded_by_their_own_model():
    """2026-10-13 bug: 'perf-2-prior-smooth' was not in CONFIG perf_versions, so grade_leg graded the prior_smooth shadow
    on the OLD pipeline (lambda = shadow.legacy). Every shadow that swaps in a performance-model projection must be graded
    by that projection: projection_source = the challenger's version, never 'legacy ...'. The live CONFIG never changes."""
    import grader
    live = tuple(grader.CONFIG["perf_versions"])
    n = 0
    for gi in _real_inputs():
        swapped = {k: v for k, v in picklog.shadow_alts(gi).items() if isinstance(v, tuple)}
        assert set(swapped) == {"perf1_g7", "prior_smooth"}
        # perf-1 / perf-1-bo3: already live versions -> the live CONFIG itself, minus lw-1 (2026-10-25: frozen comparisons)
        assert swapped["perf1_g7"][1] == dict(grader.CONFIG, lw1=None)
        for name, (gin, cfg) in swapped.items():
            src = grader.grade_leg(gin, cfg)["projection_source"]
            assert src == gin["perf"]["version"] and not src.startswith("legacy"), (name, gi["prop"], gi["scope"], src)
        sh = picklog._factors({"grader_inputs": gi, "grade": grader.grade_leg(gi)})["shadow"]   # what the pick log saves
        for name, (gin, _) in swapped.items():
            assert sh[name]["source"] == gin["perf"]["version"], (name, sh[name])
        assert sh["prior_smooth"]["source"].endswith("-prior-smooth") and sh["legacy"]["source"] == "legacy"
        if gi["prop"] == "kills":                                               # the challenger's own projection
            assert abs(sh["prior_smooth"]["lambda"] - swapped["prior_smooth"][0]["perf"]["lambda"]) <= 0.005   # logged to 2 dp
            assert abs(sh["prior_smooth"]["lambda"] - sh["legacy"]["lambda"]) > 0.01
        n += 1
    assert n == 12
    assert tuple(grader.CONFIG["perf_versions"]) == live == ("perf-2", "perf-2-bo3", "perf-1", "perf-1-bo3", "perf-cm2")
