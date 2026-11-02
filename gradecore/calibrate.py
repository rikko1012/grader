"""Stated chance -> honest chance, per (sport, market, side), from settled forecasts.

Identity under MIN_N settled legs. Platt (logistic on the logit, slope never below 0) from MIN_N to ISOTONIC_MIN_N, where
isotonic would mostly fit noise. Isotonic (pool adjacent violators) from there on, falling back to Platt when the fit has
under MIN_STEPS steps. Output never falls as the input rises.

Each fit is a plain dict (kind, n, fit_date, data_hash, + knots or a/b), and a sport's fits live in one JSON document,
artifacts/calibration_<sport>.json. fit_date is supplied by the caller. Only wins and losses are fitted: a push or void
is dropped by build() and raises in fit(), so it can never count as a win. Chances are given no push.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np

VERSION = "cal-1"
MIN_N = 300
ISOTONIC_MIN_N = 1000
MIN_BLOCK_N = 30        # a step resting on fewer legs is merged into its neighbour: no 100% from a handful of picks
MIN_STEPS = 3
CLIP = (0.01, 0.99)
EPS = 1e-6
_NEWTON = 50


def _clean(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) != len(y):
        raise ValueError("p and y must be the same length")
    if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError("p must be chances between 0 and 1")
    if not np.all((y == 0) | (y == 1)):
        raise ValueError("y must be 0 or 1: drop pushes and voids before fitting")
    return p, y


def data_hash(p, y):
    h = hashlib.sha256()
    h.update(np.round(np.asarray(p, float), 9).tobytes())
    h.update(np.asarray(y, float).tobytes())
    return h.hexdigest()[:16]


def _merge(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def fit_isotonic(p, y, min_block=MIN_BLOCK_N):
    """-> (knot x, knot y). Blocks are [sum p, sum y, n]; equal chances start in one block."""
    p, y = _clean(p, y)
    xs, inv = np.unique(p, return_inverse=True)
    sy, n = np.bincount(inv, weights=y), np.bincount(inv)
    blocks = []
    for sx, s, k in zip((xs * n).tolist(), sy.tolist(), n.tolist()):
        blocks.append([sx, s, k])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] >= blocks[-1][1] / blocks[-1][2]:
            top = blocks.pop()
            blocks[-1] = _merge(blocks[-1], top)
    while len(blocks) > 1:
        i = min(range(len(blocks)), key=lambda j: blocks[j][2])
        if blocks[i][2] >= min_block:
            break
        rate = blocks[i][1] / blocks[i][2]
        j = min(((abs(blocks[j][1] / blocks[j][2] - rate), j) for j in (i - 1, i + 1) if 0 <= j < len(blocks)))[1]
        lo, hi = min(i, j), max(i, j)
        blocks[lo:hi + 1] = [_merge(blocks[lo], blocks[hi])]
    return [b[0] / b[2] for b in blocks], [b[1] / b[2] for b in blocks]


def _loss(a, b, x, y):
    q = np.clip(1.0 / (1.0 + np.exp(-(a * x + b))), 1e-12, 1 - 1e-12)
    return float(-(y * np.log(q) + (1 - y) * np.log(1 - q)).mean())


def fit_platt(p, y):
    """-> (a, b) of logit(p_cal) = a*logit(p) + b, by damped Newton from the identity. None when y has one class."""
    p, y = _clean(p, y)
    if y.min() == y.max():
        return None
    c = np.clip(p, EPS, 1 - EPS)
    x = np.log(c / (1 - c))
    a, b = 1.0, 0.0
    for _ in range(_NEWTON):
        q = 1.0 / (1.0 + np.exp(-(a * x + b)))
        w = np.maximum(q * (1 - q), 1e-9)
        g = np.array([np.sum((q - y) * x), np.sum(q - y)])
        h = np.array([[np.sum(w * x * x) + 1e-6, np.sum(w * x)], [np.sum(w * x), np.sum(w) + 1e-6]])
        try:
            step = np.linalg.solve(h, g)
        except np.linalg.LinAlgError:
            break
        t, now = 1.0, _loss(a, b, x, y)
        while t > 1e-4 and _loss(a - t * step[0], b - t * step[1], x, y) > now:
            t /= 2
        a, b = a - t * step[0], b - t * step[1]
        if np.abs(t * step).max() < 1e-9:
            break
    if a < 0:                       # a falling line would rank worse picks higher: flat at the hit rate instead
        m = float(y.mean())
        a, b = 0.0, float(np.log(m / (1 - m)))
    return float(a), float(b)


def fit(p, y, key="", fit_date=None, min_n=MIN_N):
    p, y = _clean(p, y)
    out = {"version": VERSION, "key": key, "n": int(len(y)), "fit_date": fit_date, "data_hash": data_hash(p, y)}
    if len(y) < min_n or y.min() == y.max():
        return {**out, "kind": "identity"}
    if len(y) >= ISOTONIC_MIN_N:
        kx, ky = fit_isotonic(p, y)
        if len(kx) >= MIN_STEPS:
            return {**out, "kind": "isotonic", "x": kx, "y": [min(max(v, CLIP[0]), CLIP[1]) for v in ky]}
    ab = fit_platt(p, y)
    if ab is None:
        return {**out, "kind": "identity"}
    return {**out, "kind": "platt", "a": ab[0], "b": ab[1]}


def apply(art, p):
    """The calibrated chance. Flat beyond the first and last isotonic knots."""
    kind = (art or {}).get("kind", "identity")
    p = float(p)
    if kind == "identity":
        return p
    if kind == "isotonic":
        return float(np.interp(p, art["x"], art["y"]))
    if kind == "platt":
        c = min(max(p, EPS), 1 - EPS)
        v = 1.0 / (1.0 + np.exp(-(art["a"] * np.log(c / (1 - c)) + art["b"])))
        return float(min(max(v, CLIP[0]), CLIP[1]))
    raise ValueError(f"unknown calibration kind {kind!r}")


def table_key(market, side):
    return f"{market}|{side}"


def build(rows, sport, fit_date=None, min_n=MIN_N):
    """rows: {"market", "side", "p", "status"}. -> the sport's document, one fit per (market, side). Only win / loss rows
    enter; "dropped" counts the rest."""
    groups, dropped = {}, 0
    for r in rows:
        if r.get("status") not in ("win", "loss"):
            dropped += 1
            continue
        g = groups.setdefault(table_key(r["market"], r["side"]), ([], []))
        g[0].append(r["p"])
        g[1].append(1.0 if r["status"] == "win" else 0.0)
    return {"version": VERSION, "sport": sport, "fit_date": fit_date, "dropped": dropped,
            "tables": {k: fit(p, y, f"{sport}|{k}", fit_date, min_n) for k, (p, y) in sorted(groups.items())}}


def lookup(doc, market, side):
    """The fit for (market, side); identity when there is none."""
    return ((doc or {}).get("tables") or {}).get(table_key(market, side)) or {"kind": "identity"}


def artifact_path(folder, sport):
    return Path(folder) / f"calibration_{sport}.json"


def save(doc, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
