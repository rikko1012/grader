"""Deterministic lab learners (no randomness in fitting; the seed drives only the bootstrap in evaluate.py).

  prior_mean@1   baseline: mu = the player's mean over earlier games (nothing fitted but the residual spread)
  shrunk_mean@1  mu = (n * prior_mean + k * train_mean) / (n + k); k chosen on TUNE from a fixed grid
  ridge@1        linear model on the selected features, z-scored on TRAIN, missing -> train mean + indicator; the ridge
                 penalty chosen on TUNE from a fixed grid; intercept not penalised
Every learner returns a JSON model (all fitted numbers visible) and predicts (mu, sd); sd = residual standard deviation
on TRAIN (floor 0.5). Probabilities: one discretised normal per prop for all lines and sides (integer outcomes):
  half line L: p_over = 1 - Phi((L - mu)/sd), p_push = 0;   whole line L: p_push = Phi((L+.5-mu)/sd) - Phi((L-.5-mu)/sd)
Unconditional; p_over + p_under + p_push = 1; monotone in L.
"""
from __future__ import annotations

import math

import numpy as np

from . import ALGORITHMS

SHRINK_GRID = (0.0, 2.0, 5.0, 10.0, 20.0, 40.0)
RIDGE_GRID = (0.01, 0.1, 1.0, 10.0, 100.0)


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def probs(mu, sd, line):
    if abs(line - round(line)) > 1e-9:                    # half line
        po = 1.0 - _phi((line - mu) / sd)
        return po, 1.0 - po, 0.0
    hi, lo = _phi((line + 0.5 - mu) / sd), _phi((line - 0.5 - mu) / sd)
    return 1.0 - hi, lo, hi - lo


def _mae(y, p):
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(p)))) if len(y) else None


def _rows(rows, split):
    return [r for r in rows if r["split"] == split]


def _sd(rows, mus):
    res = np.asarray([r["y"] for r in rows]) - np.asarray(mus)
    return max(0.5, float(np.std(res, ddof=1))) if len(res) > 1 else 1.0


# ---------------------------------------------------------------------------------------------- prior mean
def _pm_mu(model, r):
    return r["x"]["prior_mean"]


# ------------------------------------------------------------------------------------------------- shrunk
def _sh_mu(model, r, k=None):
    k = model["k"] if k is None else k
    n = r["x"].get("prior_n") or r["prior_games"]
    return (n * r["x"]["prior_mean"] + k * model["train_mean"]) / (n + k)


# -------------------------------------------------------------------------------------------------- ridge
def _design(model, rows):
    cols = []
    for f in model["features"]:
        mean, sdv = model["mean"][f], model["scale"][f]
        cols.append([((r["x"].get(f) if r["x"].get(f) is not None else mean) - mean) / sdv for r in rows])
        if f in model["indicators"]:
            cols.append([1.0 if r["x"].get(f) is None else 0.0 for r in rows])
    return np.asarray(cols, dtype=float).T if cols else np.zeros((len(rows), 0))


def _ridge_fit(X, y, alpha):
    ym = float(np.mean(y))
    if X.shape[1] == 0:
        return ym, np.zeros(0)
    w = np.linalg.solve(X.T @ X + alpha * np.eye(X.shape[1]), X.T @ (y - ym))
    return ym, w


def fit(spec, rows):
    algo = spec["algorithm"]
    train, tune = _rows(rows, "train"), _rows(rows, "tune")
    if not train:
        raise ValueError("no training rows in the train window")
    if algo == "prior_mean":
        model = {"algorithm": ALGORITHMS[algo]}
        model["sd"] = _sd(train, [_pm_mu(model, r) for r in train])
        return model
    if algo == "shrunk_mean":
        model = {"algorithm": ALGORITHMS[algo], "train_mean": float(np.mean([r["y"] for r in train]))}
        scores = {}
        for k in SHRINK_GRID:
            scores[k] = _mae([r["y"] for r in tune], [_sh_mu(model, r, k) for r in tune]) if tune else 0.0
        model["k"] = min(SHRINK_GRID, key=lambda k: (scores[k], k))
        model["tune_mae_by_k"] = {str(k): v for k, v in scores.items()}
        model["sd"] = _sd(train, [_sh_mu(model, r) for r in train])
        return model
    if algo == "ridge":
        feats = sorted(spec["features"])
        model = {"algorithm": ALGORITHMS[algo], "features": feats, "mean": {}, "scale": {}, "indicators": []}
        for f in feats:
            vals = [r["x"].get(f) for r in train]
            present = [v for v in vals if v is not None]
            m = float(np.mean(present)) if present else 0.0
            s = float(np.std(present)) if len(present) > 1 else 1.0
            model["mean"][f], model["scale"][f] = m, (s if s > 1e-9 else 1.0)
            if len(present) < len(vals):
                model["indicators"].append(f)
        Xt, yt = _design(model, train), np.asarray([r["y"] for r in train])
        scores = {}
        for a in RIDGE_GRID:
            b0, w = _ridge_fit(Xt, yt, a)
            if tune:
                pv = b0 + _design(model, tune) @ w
                scores[a] = _mae([r["y"] for r in tune], pv)
            else:
                scores[a] = 0.0
        alpha = min(RIDGE_GRID, key=lambda a: (scores[a], a))
        b0, w = _ridge_fit(Xt, yt, alpha)
        model.update(alpha=alpha, intercept=b0, weights=[float(x) for x in w],
                     tune_mae_by_alpha={str(a): v for a, v in scores.items()})
        model["sd"] = _sd(train, list(b0 + Xt @ w))
        return model
    raise ValueError(f"unknown algorithm {algo}")


def predict(model, rows):
    algo = model["algorithm"].split("@")[0]
    if algo == "prior_mean":
        mus = [_pm_mu(model, r) for r in rows]
    elif algo == "shrunk_mean":
        mus = [_sh_mu(model, r) for r in rows]
    else:
        mus = list(model["intercept"] + _design(model, rows) @ np.asarray(model["weights"])) if rows else []
    return [(round(float(m), 9), round(float(model["sd"]), 9)) for m in mus]
