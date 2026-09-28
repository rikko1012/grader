#!/usr/bin/env python3
"""
sim.py - 20,000-run Maps 1-2 simulation. DISPLAY ONLY (weight 0 in the grade).

Each run: map (actual, or drawn from the veto prediction) -> map winner (Polymarket map price, or
derived from the series price) -> rounds from the empirical 13-x / overtime table -> kills
~ Poisson(KPR x rounds x won/lost-map factor) -> headshots ~ Binomial(kills, HS%).

Tested 2026-09-28 on the 900-case walk-forward against the grader's curve + c: miss kills 6.015 vs
6.007, HS 3.711 vs 3.695, and kills A+ overconfident (printed 81.7%, hit 75.2%) - pure Poisson is
too thin-tailed (it has no "good day / bad day" swing). So the grade stays on the curve; this
number is shown next to it.
"""
import numpy as np

from grader import CONFIG, win_prob

RUNS = 20000
# rounds per map, 7,627 earlier player-maps (13-x and overtime as played)
ROUNDS = {13: 0.0042, 14: 0.0168, 15: 0.035, 16: 0.0459, 17: 0.0698, 18: 0.0762, 19: 0.0783, 20: 0.0924,
          21: 0.093, 22: 0.1135, 23: 0.1053, 24: 0.1411, 28: 0.0163, 29: 0.0341, 30: 0.0385, 34: 0.0054,
          35: 0.0097, 36: 0.0097, 40: 0.002, 41: 0.0022, 42: 0.0047, 46: 0.001, 47: 0.001, 48: 0.0014}
F_WIN, F_LOSS = 1.083, 0.880       # kills per round in won / lost maps vs overall (same data)
_R = np.array(list(ROUNDS))
_P = np.array(list(ROUNDS.values())) / sum(ROUNDS.values())


def _blend(kpr, hs, ms):
    n = (ms or {}).get("maps_played") or 0
    if not n or not (ms or {}).get("kpr"):
        return kpr, hs
    w = n / (n + CONFIG["map_blend_k"])
    return w * ms["kpr"] + (1 - w) * kpr, w * (ms.get("hs_pct") or hs) + (1 - w) * hs


def simulate(d, seed=None):
    """d = the grader inputs. Returns P(More), the simulated mean and the run count."""
    rng = np.random.default_rng(seed)
    kpr, hs, prop, line = d.get("kpr"), d.get("hs_pct") or 0, d.get("prop", "kills"), float(d["line"])
    if not kpr:
        return None
    p_win, _ = win_prob(d.get("team_ml"), d.get("opp_ml"))
    lo, hi = 0.0, 1.0
    for _ in range(40):
        q = (lo + hi) / 2
        lo, hi = (q, hi) if q * q * (3 - 2 * q) < p_win else (lo, q)
    mw = d.get("map_win_probs") or []
    qs = [mw[i] if len(mw) > i and mw[i] is not None else q for i in (0, 1)]
    known = (d.get("per_map") or [])[:2]
    probs = d.get("map_probs") or []
    stats = d.get("map_stats") or {}
    total = np.zeros(RUNS)
    for slot in (0, 1):
        if slot < len(known):
            k, h = _blend(kpr, hs, known[slot])
            k_run, h_run = np.full(RUNS, k), np.full(RUNS, h)
        elif slot < len(probs) and probs[slot]:
            names = list(probs[slot])
            p = np.array([probs[slot][n] for n in names], dtype=float)
            pick = rng.choice(len(names), RUNS, p=p / p.sum())
            table = [_blend(kpr, hs, stats.get(n)) for n in names]
            k_run = np.array([t[0] for t in table])[pick]
            h_run = np.array([t[1] for t in table])[pick]
        else:
            k_run, h_run = np.full(RUNS, kpr), np.full(RUNS, hs)
        won = rng.random(RUNS) < qs[slot]
        rounds = rng.choice(_R, RUNS, p=_P)
        kills = rng.poisson(k_run * rounds * np.where(won, F_WIN, F_LOSS))
        total += rng.binomial(kills, np.clip(h_run / 100, 0, 1)) if prop == "hs" else kills
    return {"p_more": round(float((total > line).mean()), 3), "mean": round(float(total.mean()), 2),
            "runs": RUNS, "weight": 0}
