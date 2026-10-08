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
    assert kinds == {"normal", "mix", "hs_bb"}


def test_maps123_chance_mixes_before_removing_push():
    """Maps 1-3 (live fix 2026-10-09): Over/Under/push are mixed over the 2-map / 3-map cases first, the push is removed
    after - the same distribution the simulation samples and settlement voids on."""
    rnd = random.Random(31); whole = 0
    for _ in range(300):
        d = _inputs(rnd, "123")
        if rnd.random() < 0.5:
            d["line"] = float(round(d["line"])); whole += 1
        g = grader.grade_leg(dict(d))
        cases = [tuple(c) for c in g["dist"]["cases"]]
        o, u, pu = grader.mix_three(g["line"], cases)
        r = g["probs_raw"]
        assert abs(r["over"] - o) < 2e-3 and abs(r["under"] - u) < 2e-3 and abs(r["push"] - pu) < 2e-3
        assert abs(g["p_more_model_raw"] - o / (o + u)) < 2e-3
        if g["line"] != int(g["line"]):
            assert pu < 1e-9
    assert whole > 50
