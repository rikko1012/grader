#!/usr/bin/env python3
"""
sim.py - 20,000-run match simulation of the GRADE'S OWN DISTRIBUTION (rebuilt 2026-10-08).

Every run's total is a draw from exactly the distribution the grade used (g["dist"]):
  normal  - the grade's projection and spread, with the line's real cuts (x.5: at the line; whole: +-0.5, push between)
  mix     - Maps 1-3: the 2-map and 3-map cases with their weights (map 3 only after a 1-1)
  hs_bb   - headshots out of kills: kills from the conditioned kills distribution, HS | kills beta-binomial (perf.py)
so the simulated Over / Under / push frequencies match the grade's calculated chances within Monte Carlo error.

The match-script breakdown (his team 2-0 / 1-1 / 0-2, overtime) is HEURISTIC: a separate script model (map winners
from the market, rounds from the empirical 13-x / overtime table, won-map / lost-map factors) only ORDERS the runs -
each run gets the deployed total of the same rank (rank-preserving transform). So the totals' distribution is exactly
the grade's, the exclusive scripts (2-0 / 1-1 / 0-2) partition the runs and reconcile with the overall numbers, and
"any overtime" overlaps the other scripts (it is not another exclusive outcome).
"""
import math
import numpy as np

RUNS = 20000
ROUNDS = {13: 0.0042, 14: 0.0168, 15: 0.035, 16: 0.0459, 17: 0.0698, 18: 0.0762, 19: 0.0783, 20: 0.0924,
          21: 0.093, 22: 0.1135, 23: 0.1053, 24: 0.1411, 28: 0.0163, 29: 0.0341, 30: 0.0385, 34: 0.0054,
          35: 0.0097, 36: 0.0097, 40: 0.002, 41: 0.0022, 42: 0.0047, 46: 0.001, 47: 0.001, 48: 0.0014}
F_WIN, F_LOSS = 1.083, 0.880
_R = np.array(list(ROUNDS))
_P = np.array(list(ROUNDS.values())) / sum(ROUNDS.values())


def cuts(line):
    """(up, down): Over if total > up, Under if total < down (same as grader._cuts / perf.probs)."""
    return (math.floor(line) + 0.5,) * 2 if line != int(line) else (line + 0.5, line - 0.5)   # see grader._cuts


def sample_dist(dist, n, rng):
    """n draws from the grade's distribution (sorted ascending)."""
    k = dist.get("kind")
    if k == "normal":
        x = rng.normal(dist["lam"], dist["sd"], n)
    elif k == "mix":
        cases = dist["cases"]; w = np.array([c[0] for c in cases], float); w = w / w.sum()
        which = rng.choice(len(cases), n, p=w)
        lam = np.array([cases[i][1] for i in which]); sd = np.array([cases[i][2] for i in which])
        x = rng.normal(lam, sd)
    elif k == "hs_bb":
        import perf
        pmf = perf.hs_pmf(dist["lam_kills"], dist["scope"], dist["hs_per_kill"], c=dist.get("c"))
        ys = np.array(sorted(pmf)); ps = np.array([pmf[y] for y in ys]); ps = ps / ps.sum()
        x = rng.choice(ys, n, p=ps).astype(float)
    else:
        raise ValueError(f"unknown distribution {k!r}")
    return np.sort(x)


def simulate_grade(g, seed=7):
    """g = the grade (grade_leg output). Returns the simulated Over / Under / push frequencies of the grade's own
    distribution, the spread of outcomes, and the heuristic match-script breakdown (reconciled, labelled)."""
    if not g or not g.get("dist"):
        return None
    det = g.get("detail") or {}
    scope = g.get("scope") or "12"
    line = float(g["line"])
    rng = np.random.default_rng(seed)
    q1, q2 = float(det.get("p_map1") or det.get("p_map") or 0.5), float(det.get("p_map2") or det.get("p_map") or 0.5)
    n_maps = 1 if scope == "1" else 3 if scope == "123" else 2
    qs = [q1, q2, (q1 + q2) / 2][:n_maps]
    won = np.stack([rng.random(RUNS) < q for q in qs])
    rounds = rng.choice(_R, (n_maps, RUNS), p=_P)
    played = np.ones((n_maps, RUNS), dtype=bool)
    if n_maps == 3:
        played[2] = won[0] != won[1]
    script = (rounds / 22.0 * np.where(won, F_WIN, F_LOSS) * played).sum(axis=0) + rng.normal(0, 0.15, RUNS)
    total = np.empty(RUNS)
    total[np.argsort(script, kind="stable")] = sample_dist(g["dist"], RUNS, rng)
    up, down = cuts(line)
    over, under = total > up, total < down
    push = ~(over | under)
    decided = over | under
    w12 = won[:2].sum(axis=0) if n_maps >= 2 else won[0].astype(int)
    ot = (np.where(played, rounds, 0) >= 28).any(axis=0)

    def row(mask, label, exclusive=True):
        if not mask.any():
            return None
        return {"label": label, "chance": round(float(mask.mean()), 3), "avg": round(float(total[mask].mean()), 1),
                "p_over": round(float(over[mask].sum() / max(decided[mask].sum(), 1)), 3), "exclusive": exclusive}
    if n_maps == 1:
        scen = [row(won[0], "win"), row(~won[0], "loss")]
    else:
        scen = [row(w12 == 2, "win 2-0"), row(w12 == 1, "split 1-1" + (" (map 3 played)" if n_maps == 3 else "")),
                row(w12 == 0, "lose 0-2")]
    scen.append(row(ot, "any overtime (overlaps the scripts above)", exclusive=False))
    lo, mid, hi = np.percentile(total, [10, 50, 90])
    return {"p_more": round(float(over.sum() / max(decided.sum(), 1)), 3), "p_over_raw": round(float(over.mean()), 4),
            "p_under_raw": round(float(under.mean()), 4), "p_push": round(float(push.mean()), 4),
            "mc_se": round(math.sqrt(0.25 / RUNS), 4), "mean": round(float(total.mean()), 1),
            "p10": round(float(lo)), "p50": round(float(mid)), "p90": round(float(hi)),
            "scenarios": [s for s in scen if s], "scenarios_note": "heuristic match scripts ordering the grade's own distribution",
            "runs": RUNS, "anchored": True, "dist": g["dist"].get("kind"), "weight": 0}


def simulate(d, seed=None):
    """Old entry point (kept for scripts): no grade to anchor to -> nothing."""
    return None
