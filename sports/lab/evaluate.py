"""Experiment evaluation: projection error per split, the prior_mean baseline on the SAME rows, a game-block bootstrap
interval for the test MAE difference (seeded), 80% interval coverage, and probability calibration at offered lines and
(separately, labelled) at reference lines. Pushes (whole lines hit exactly) are counted and left out of Brier / log
loss for p_over, which is evaluated as P(over) vs over-or-not among non-push outcomes using the conditional value
p_over / (1 - p_push) - named so.
"""
from __future__ import annotations

import math

import numpy as np

from . import algorithms

Z80 = 1.2815515655446004
BOOT = 500


def _err(rows, preds):
    if not rows:
        return {"rows": 0}
    y = np.asarray([r["y"] for r in rows])
    mu = np.asarray([p[0] for p in preds])
    sd = np.asarray([p[1] for p in preds])
    e = mu - y
    return {"rows": len(rows), "games": len({r["game_id"] for r in rows}), "mae": round(float(np.mean(np.abs(e))), 4),
            "rmse": round(float(np.sqrt(np.mean(e ** 2))), 4), "bias": round(float(np.mean(e)), 4),
            "coverage_80": round(float(np.mean(np.abs(e) <= Z80 * sd)), 4)}


def _boot_delta(rows, cand, base, seed):
    games = sorted({r["game_id"] for r in rows})
    if len(games) < 2:
        return None
    gi = {g: i for i, g in enumerate(games)}
    d = np.zeros(len(games))
    n = np.zeros(len(games))
    for r, c, b in zip(rows, cand, base):
        k = gi[r["game_id"]]
        d[k] += abs(c[0] - r["y"]) - abs(b[0] - r["y"])
        n[k] += 1
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(BOOT):
        idx = rng.integers(0, len(games), len(games))
        stats.append(d[idx].sum() / n[idx].sum())
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return {"delta_mae": round(float(d.sum() / n.sum()), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "bootstrap": f"{BOOT} game-block resamples, seed {seed}", "games": len(games)}


def _calib(rows, preds, source):
    sel = [(r, p) for r, p in zip(rows, preds) if r["line_source"] == source and r["line"] is not None]
    if not sel:
        return {"line_source": source, "rows": 0}
    ps, ys, pushes = [], [], 0
    for r, (mu, sd) in sel:
        po, pu, pp = algorithms.probs(mu, sd, r["line"])
        if r["y"] == r["line"]:
            pushes += 1
            continue
        ps.append(po / (1 - pp) if pp < 1 else 0.5)
        ys.append(1.0 if r["y"] > r["line"] else 0.0)
    if not ps:
        return {"line_source": source, "rows": len(sel), "pushes": pushes}
    p, y = np.clip(np.asarray(ps), 1e-6, 1 - 1e-6), np.asarray(ys)
    bins = []
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        m = (p >= lo) & ((p < hi) if b < 9 else (p <= hi))
        if m.any():
            bins.append({"band": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "mean_p": round(float(p[m].mean()), 4),
                         "observed": round(float(y[m].mean()), 4)})
    return {"line_source": source, "rows": len(sel), "pushes": pushes, "scored": len(ps),
            "metric_on": "p_over_given_no_push", "brier": round(float(np.mean((p - y) ** 2)), 5),
            "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
            "base_rate_over": round(float(y.mean()), 4), "bins": bins}


def evaluate(spec, model, rows, preds):
    base_model = algorithms.fit({**spec, "algorithm": "prior_mean"}, rows)
    base_preds = algorithms.predict(base_model, rows)
    out = {"splits": {}, "baseline": {"algorithm": base_model["algorithm"], "splits": {}}}
    for sp in ("train", "tune", "test"):
        idx = [i for i, r in enumerate(rows) if r["split"] == sp]
        rr = [rows[i] for i in idx]
        out["splits"][sp] = _err(rr, [preds[i] for i in idx])
        out["baseline"]["splits"][sp] = _err(rr, [base_preds[i] for i in idx])
    test = [i for i, r in enumerate(rows) if r["split"] == "test"]
    tr = [rows[i] for i in test]
    out["test_vs_baseline"] = _boot_delta(tr, [preds[i] for i in test], [base_preds[i] for i in test], spec["seed"])
    out["calibration"] = {s: _calib(tr, [preds[i] for i in test], s) for s in ("offered", "reference")}
    out["note"] = ("test split inspected = development evidence for the lab; registered promotion runs only through the "
                   "model agent's REGISTER.md gate")
    return out


def finite(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))
