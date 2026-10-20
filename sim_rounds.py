"""sim_rounds.py - the round view of the card's match simulation (owner 2026-10-20: "round simulations under the model
distribution"). DISPLAY ONLY: never a grade input, not part of the model tag (sim.py is; this file only reads it).

It replays sim.simulate_grade's 20,000 runs draw for draw (same seed, same order of random draws), so every run has the
same rounds, map results and total as the match simulation on the card, and groups those runs by how many rounds the
maps lasted. The totals are the grade's own distribution; which runs get the long maps is sim.py's heuristic script
model (empirical round counts, won-map / lost-map factors), so the rows are an illustration, not a tested forecast.
tests/test_sim_rounds.py checks the replay against simulate_grade on every scope.
"""
import numpy as np

import sim

# average rounds per map in the scope (regulation is 13-x: 13 to 24 rounds; 28+ = overtime)
BANDS = (("quick", "Quick", "under 19 a map"), ("standard", "Standard", "19–22 a map"),
         ("close", "Close", "23–24, no OT"), ("overtime", "Overtime", "a map to OT"))


def _runs(g, seed=7):
    """(total, rounds[n_maps, RUNS], n_maps) - exactly sim.simulate_grade's runs."""
    det = g.get("detail") or {}
    scope = g.get("scope") or "12"
    rng = np.random.default_rng(seed)
    q1, q2 = float(det.get("p_map1") or det.get("p_map") or 0.5), float(det.get("p_map2") or det.get("p_map") or 0.5)
    n_maps = 1 if scope == "1" else 3 if scope == "123" else 2
    qs = [q1, q2, (q1 + q2) / 2][:n_maps]
    us = [rng.random(sim.RUNS) for _ in qs]
    won = np.stack([u < q for u, q in zip(us, qs)])
    mb = det.get("map_prob_basis")
    if n_maps == 3 and g.get("p_map3") is not None and isinstance(mb, dict) and mb.get("format") == "bo3":
        p11 = q1 * (1 - q2) + (1 - q1) * q2
        won[0] = us[0] < (q1 * (1 - q2) / p11 if p11 > 0 else 0.5)
        won[1] = ~won[0]
    rounds = rng.choice(sim._R, (n_maps, sim.RUNS), p=sim._P)
    script = (rounds / 22.0 * np.where(won, sim.F_WIN, sim.F_LOSS)).sum(axis=0) + rng.normal(0, 0.15, sim.RUNS)
    total = np.empty(sim.RUNS)
    total[np.argsort(script, kind="stable")] = sim.sample_dist(g["dist"], sim.RUNS, rng)
    return total, rounds, n_maps


def round_view(g, seed=7):
    """The runs by map length, or None when the grade has no distribution. Rows partition the runs (they add to 100%)."""
    if not g or not g.get("dist"):
        return None
    total, rounds, n_maps = _runs(g, seed)
    up, down = sim.cuts(float(g["line"]))
    over, decided = total > up, (total > up) | (total < down)
    per_map = rounds.sum(axis=0) / n_maps
    ot = (rounds >= 28).any(axis=0)
    masks = {"overtime": ot, "quick": ~ot & (per_map < 19), "standard": ~ot & (per_map >= 19) & (per_map < 23),
             "close": ~ot & (per_map >= 23)}
    rows = []
    for key, label, note in BANDS:
        m = masks[key]
        if not m.any():
            continue
        rows.append({"key": key, "label": label, "note": note, "chance": round(float(m.mean()), 3),
                     "rounds": round(float(rounds[:, m].sum(axis=0).mean()), 1), "avg": round(float(total[m].mean()), 1),
                     "p_over": round(float(over[m].sum() / max(decided[m].sum(), 1)), 3)})
    tr = rounds.sum(axis=0)
    return {"maps": n_maps, "rounds_mean": round(float(tr.mean()), 1), "rounds_p10": int(np.percentile(tr, 10)),
            "rounds_p90": int(np.percentile(tr, 90)), "per_round": round(float(total.mean() / tr.mean()), 3),
            "rows": rows, "runs": sim.RUNS, "weight": 0}
