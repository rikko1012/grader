"""Simulation = the grade's own distribution: frequencies match calculated chances within Monte Carlo error; the
exclusive scripts reconcile with the overall numbers; overtime is marked as overlapping."""
import math, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader, sim
from tests.test_perf import _inputs


def _check(g):
    s = sim.simulate_grade(g)
    r = g["probs_raw"]; se = 4 * math.sqrt(0.25 / sim.RUNS)
    assert abs(s["p_over_raw"] - r["over"]) < se and abs(s["p_under_raw"] - r["under"]) < se and abs(s["p_push"] - r["push"]) < se + 1e-3
    ex = [x for x in s["scenarios"] if x["exclusive"]]
    assert abs(sum(x["chance"] for x in ex) - 1) < 0.003                                         # partition
    assert abs(sum(x["chance"] * x["avg"] for x in ex) - s["mean"]) < 0.2                       # reconcile
    assert [x for x in s["scenarios"] if not x["exclusive"]] and all("overlaps" in x["label"] for x in s["scenarios"] if not x["exclusive"])
    return s


def test_sim_matches_each_distribution_kind():
    rnd = random.Random(21); kinds = set()
    for _ in range(40):
        sc = rnd.choice(["12", "1", "123"]); d = _inputs(rnd, sc)
        if sc == "12" and rnd.random() < 0.5:
            d["prop"] = "hs"; lk = rnd.uniform(18, 40); ph = rnd.uniform(0.3, 0.6)
            d["perf"] = dict(d["perf"], lambda_kills=lk, hs_per_kill=ph, **{"lambda": lk * ph}); d["line"] = round(lk * ph) + rnd.choice([0.5, 0])
        g = grader.grade_leg(dict(d)); kinds.add(g["dist"]["kind"]); _check(g)
    assert kinds == {"normal", "hs_bb"}                 # Maps 1-3: three full maps, no 2-map / 3-map mix (2026-10-11)


def test_maps123_is_three_full_maps():
    """Maps 1-3 (2026-10-11): one distribution over three full maps - the chance is never mixed with a 2-map case."""
    rnd = random.Random(31)
    for _ in range(200):
        d = _inputs(rnd, "123"); d.pop("perf")
        g = grader.grade_leg(dict(d), dict(grader.CONFIG, gap_trust_at={"kills": 1e9, "hs": 1e9}))
        assert g["dist"]["kind"] == "normal" and abs(g["lambda_final"] - 1.5 * g["lambda_maps12"]) < 0.02   # both rounded to 0.01


def test_maps123_simulation_plays_three_maps():
    """BO5 regression: the Maps 1-3 simulation scripts are by maps won of three (3-0 / 2-1 / 1-2 / 0-3) - no "map 3 played"
    conditional, and the scripts still partition the runs and reconcile with the grade's own distribution."""
    rnd = random.Random(5)
    d = _inputs(rnd, "123"); d.pop("perf")
    g = grader.grade_leg(dict(d))
    s = _check(g)
    labels = [x["label"] for x in s["scenarios"] if x["exclusive"]]
    assert labels == ["win 3-0", "win 2-1", "lose 1-2", "lose 0-3"] and not any("map 3" in x["label"] for x in s["scenarios"])
