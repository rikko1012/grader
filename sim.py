#!/usr/bin/env python3
"""
sim.py - 20,000-run match simulation built ON the grade (2026-10-06, owner: "make it make sense").

The old version ran its own Poisson model and could disagree with the grade (63% More next to a B+
Less). This one is anchored to the grader: same projection and the same spread, so on average it
says what the grade says - what it adds is HOW the prop plays out: his team winning 2-0, a 1-1
split (and the map 3 it brings on Maps 1-3 lines), losing 0-2, overtime.

Each run: map winners (Polymarket map prices / series price -> per-map chance from the grade) ->
rounds per map from the empirical 13-x / overtime table -> his kills (or headshots) on that map =
his per-map projection x rounds/22 x won-map / lost-map factor (scaled so the average is the grade's
projection) + the rest of the grade's spread as noise.
"""
import numpy as np

RUNS = 20000
# rounds per map, 7,627 earlier player-maps (13-x and overtime as played)
ROUNDS = {13: 0.0042, 14: 0.0168, 15: 0.035, 16: 0.0459, 17: 0.0698, 18: 0.0762, 19: 0.0783, 20: 0.0924,
          21: 0.093, 22: 0.1135, 23: 0.1053, 24: 0.1411, 28: 0.0163, 29: 0.0341, 30: 0.0385, 34: 0.0054,
          35: 0.0097, 36: 0.0097, 40: 0.002, 41: 0.0022, 42: 0.0047, 46: 0.001, 47: 0.001, 48: 0.0014}
F_WIN, F_LOSS = 1.083, 0.880       # kills per round in won / lost maps vs overall (same data)
_R = np.array(list(ROUNDS))
_P = np.array(list(ROUNDS.values())) / sum(ROUNDS.values())


def simulate_grade(g, seed=7):
    """g = the grade (grade_leg output). Returns the overall chance over the line, the spread of
    outcomes and each match script's chance / average / chance over the line."""
    if not g or g.get("lambda_final") is None or not g.get("sd"):
        return None
    det = g.get("detail") or {}
    scope = g.get("scope") or "12"
    line = float(g["line"])
    lam12 = float(g.get("lambda_maps12") or g["lambda_final"])
    sd12 = float(g["sd"]) * (lam12 / float(g["lambda_final"])) ** 0.5 if g["lambda_final"] else float(g["sd"])
    q1, q2 = float(det.get("p_map1") or det.get("p_map") or 0.5), float(det.get("p_map2") or det.get("p_map") or 0.5)
    rng = np.random.default_rng(seed)
    n_maps = 1 if scope == "1" else 3 if scope == "123" else 2
    qs = [q1, q2, (q1 + q2) / 2][:n_maps]
    won = np.stack([rng.random(RUNS) < q for q in qs])                    # maps x runs
    rounds = rng.choice(_R, (n_maps, RUNS), p=_P)
    shape = rounds / 22.0 * np.where(won, F_WIN, F_LOSS)
    played = np.ones((n_maps, RUNS), dtype=bool)
    if n_maps == 3:                                                       # map 3 only after a 1-1
        played[2] = won[0] != won[1]
    m = lam12 / 2                                                         # his per-map projection
    shape12 = shape[:2] if n_maps >= 2 else shape[:1]
    scale = m / shape12.mean()                                            # average = the grade's projection
    base = shape * scale
    # the rest of the grade's spread (beyond what map results and rounds explain) as noise
    var_map = max(sd12 ** 2 / 2 - base[: min(n_maps, 2)].var(axis=1).mean(), m * 0.5)
    vals = np.maximum(base + rng.normal(0, var_map ** 0.5, (n_maps, RUNS)), 0) * played
    total = vals.sum(axis=0)
    up = line if abs(line - round(line)) > 0.25 else line + 0.5     # same cuts as the grade (grader._cuts)
    down = line if abs(line - round(line)) > 0.25 else line - 0.5
    over, under = total > up, total < down
    over = np.where(over | under, over, False)
    decided = over | under
    # scenarios
    w12 = won[:2].sum(axis=0) if n_maps >= 2 else won[0].astype(int)
    ot = (np.where(played, rounds, 0) >= 28).any(axis=0)

    def row(mask, label):
        if not mask.any():
            return None
        return {"label": label, "chance": round(float(mask.mean()), 3), "avg": round(float(total[mask].mean()), 1),
                "p_over": round(float(over[mask].sum() / max(decided[mask].sum(), 1)), 3)}
    if n_maps == 1:
        scen = [row(won[0], "win"), row(~won[0], "loss")]
    else:
        scen = [row(w12 == 2, "win 2-0"), row(w12 == 1, "split 1-1" + (" (map 3 played)" if n_maps == 3 else "")),
                row(w12 == 0, "lose 0-2")]
    scen.append(row(ot, "any overtime"))
    lo, mid, hi = np.percentile(total, [10, 50, 90])
    return {"p_more": round(float(over.sum() / max(decided.sum(), 1)), 3), "mean": round(float(total.mean()), 1),
            "p10": round(float(lo)), "p50": round(float(mid)), "p90": round(float(hi)),
            "scenarios": [s for s in scen if s], "runs": RUNS, "anchored": True, "weight": 0}


def simulate(d, seed=None):
    """Old entry point (kept for scripts): no grade to anchor to -> nothing."""
    return None
