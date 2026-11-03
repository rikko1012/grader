"""Model + market in log-odds: logit(p) = w*logit(p_model) + (1-w)*logit(p_mkt). w = the model's share.

No market -> the model as it is (w = 1). Width: brief 3.1 says to shrink toward the market as the market gets wider, and
WIDTH_TOWARD = "market" does exactly that. Brief section 2 says the opposite of a wide market ("wide market = less
trust"); WIDTH_TOWARD = "model" is that reading. The direction is this one constant, for the owner to settle.
Width = the overround (devig.width).

Weights come from a table {"<sport>|<market>": w} built by fit_weight on settled forecasts. A pair with no fitted weight
gets DEFAULT_WEIGHT = 1: the model alone, the same "change nothing until fitted" as calibrate's identity.
"""
import math

import numpy as np

EPS = 1e-6
MIN_N = 300
DEFAULT_WEIGHT = 1.0
WIDTH_TOWARD = "market"
WIDTH_CALM, WIDTH_FULL = 0.05, 0.15     # starting values: no shift up to a 5% margin, the whole shift at 15%


def logit(p):
    p = min(max(float(p), EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def expit(x):
    return 1.0 / (1.0 + math.exp(-x))


def effective_weight(w, width=None):
    """The model's share after the width shift. No width known = no shift."""
    if width is None:
        return w
    t = min(max((float(width) - WIDTH_CALM) / (WIDTH_FULL - WIDTH_CALM), 0.0), 1.0)
    if WIDTH_TOWARD == "market":
        return w * (1.0 - t)
    if WIDTH_TOWARD == "model":
        return w + (1.0 - w) * t
    raise ValueError(f"WIDTH_TOWARD must be market or model, not {WIDTH_TOWARD!r}")


def blend(p_model, p_mkt=None, w=DEFAULT_WEIGHT, width=None):
    if not 0.0 <= w <= 1.0:
        raise ValueError("w must be between 0 and 1")
    if p_mkt is None:
        return float(p_model)
    e = effective_weight(w, width)
    return expit(e * logit(p_model) + (1.0 - e) * logit(p_mkt))


def weight(table, sport, market):
    return float((table or {}).get(f"{sport}|{market}", DEFAULT_WEIGHT))


def _logits(p):
    c = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(c / (1 - c))


def fit_weight(p_model, p_mkt, y, grid=101, min_n=MIN_N):
    """The w on an even grid with the lowest log loss, or None under min_n legs. y is 1 win / 0 loss: drop pushes and
    voids first, any other value raises."""
    lm, lk, y = _logits(p_model), _logits(p_mkt), np.asarray(y, float)
    if not (len(lm) == len(lk) == len(y)):
        raise ValueError("p_model, p_mkt and y must be the same length")
    if not np.all((y == 0) | (y == 1)):
        raise ValueError("y must be 0 or 1: drop pushes and voids before fitting")
    if len(y) < min_n:
        return None
    ws = np.linspace(0.0, 1.0, grid)
    p = np.clip(1.0 / (1.0 + np.exp(-(ws[:, None] * lm + (1 - ws[:, None]) * lk))), EPS, 1 - EPS)
    loss = -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean(axis=1)
    return float(ws[int(np.argmin(loss))])
