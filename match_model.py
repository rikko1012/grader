#!/usr/bin/env python3
"""
match_model.py - shared-match challenger M1 (SHADOW ONLY, 2026-10-09). Never changes a grade, chance or stake.

One simulated match drives every prop of it:
  map winners (the grade's map-win chances) -> series length (BO3: map 3 only after 1-1; Maps 1-2 / Map 1 never count
  map 3) -> rounds per map (empirical incl. overtime) -> each player's kills per map ~ NegBin(mean = perf rate x rounds x
  won / lost factor) -> headshots | kills ~ BetaBinomial(HS per kill, kappa 100).
Constants fitted on TRAIN (HLTV history before 2026-07-01) only.

Development result (TEST 2026-08-13 - 2026-10-08, 13,255 lines / 1,343 matches): marginal props tie or slightly worse
than G8.1 (kills MAE +0.016, 95% +0.004 to +0.029; log-likelihood +0.004, -0.003 to +0.012); teammates' joint
"both Over" 27.7% vs observed 27.9% (G8.1's independence: 25.0%) - better; opponents 24.8% vs observed 26.2% (G8.1 25.0%)
- worse. Kept in shadow (logged as "match_m1") for the forward test; not promoted.
"""
import math

import numpy as np

VERSION = "m1-2026-10-09"
F_WIN, F_LOSS = 1.1055052404791503, 0.8944947595208498
NB_R = 78.56749740231288                       # Var = mu + mu^2 / NB_R
HS_KAPPA = 100.0
R_VALS = np.array([13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 28, 29, 30, 34, 35, 36, 40, 41, 42, 46, 47, 48, 52, 54, 60])
R_P = np.array([0.00318, 0.014993, 0.031955, 0.047251, 0.063305, 0.077389, 0.085264, 0.101318, 0.109496, 0.110253, 0.106921,
                0.125246, 0.018325, 0.028926, 0.035893, 0.006209, 0.010298, 0.01257, 0.001212, 0.001969, 0.003786, 0.000606,
                0.001212, 0.001363, 0.000151, 0.000757, 0.000151])
R_P = R_P / R_P.sum()


def simulate_totals(rate, hs_per_kill, q_maps, scope="12", runs=4000, seed=7):
    """Draws of (kills, headshots) totals for one player. rate = kills per round (the perf projection / its rounds),
    q_maps = this player's team's chance of winning each map [map1, map2, map3]."""
    rng = np.random.default_rng(seed)
    n = 1 if scope == "1" else 3 if scope == "123" else 2
    q = (list(q_maps) + [0.5, 0.5, 0.5])[:n]
    won = np.stack([rng.random(runs) < qq for qq in q], axis=1)
    rounds = rng.choice(R_VALS, size=(runs, n), p=R_P)
    played = np.ones((runs, n), dtype=bool)
    if n == 3:
        played[:, 2] = won[:, 0] != won[:, 1]
    mu = rate * rounds * np.where(won, F_WIN, F_LOSS) * played
    p = NB_R / (NB_R + np.maximum(mu, 1e-9))
    kills = np.where(played, rng.negative_binomial(NB_R, p), 0).sum(axis=1)
    phs = rng.beta(hs_per_kill * HS_KAPPA, (1 - hs_per_kill) * HS_KAPPA, size=runs)
    hs = rng.binomial(kills, phs)
    return kills, hs


def shadow(pf, detail, scope, prop, line):
    """{"p_more", "raw": {over, under, push}, "lambda"} for the logged comparison; None when inputs are missing."""
    if not pf or pf.get("lambda_kills") is None or not pf.get("rounds_per_map") or not pf.get("maps"):
        return None
    rate = pf["lambda_kills"] / (pf["rounds_per_map"] * pf["maps"])
    q1 = (detail or {}).get("p_map1") or (detail or {}).get("p_map") or 0.5
    q2 = (detail or {}).get("p_map2") or (detail or {}).get("p_map") or 0.5
    k, h = simulate_totals(rate, float(pf.get("hs_per_kill") or 0.5), [q1, q2, (q1 + q2) / 2], scope)
    v = h if prop == "hs" else k
    over, under = float((v > line).mean()), float((v < line).mean())
    return {"version": VERSION, "p_more": round(over / (over + under), 4) if over + under else 0.5, "lambda": round(float(v.mean()), 2),
            "raw": {"over": round(over, 4), "under": round(under, 4), "push": round(1 - over - under, 4)}}
