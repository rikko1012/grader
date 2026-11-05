"""CS2 projection candidates (2026-11-05). Research / shadow only - nothing here is imported by the live grader.

Every candidate changes the PROJECTION (the predictive mean, or the predictive spread around a changed mean).
None multiplies or shrinks a printed chance after the fact, none adds a global Over/Under intercept, none touches
stakes. The chance is always P(Over) of the candidate's own predictive distribution at the offered line.

Inputs are pre-match only: the live lam, the offered board line (the P1 row's line, logged >= 30 min before start),
and the frozen challenger lams logged with the same pre-match forecast (fh-1 full-history, cm-1, cm-2). Fits use
settled rows of EARLIER matches only (the caller passes the training rows; see evaluate.walk_forward).

Candidates
  st-1  stacked no-line mean: actual - lam ~ ridge on (fh-1 - lam), (cm-1 - lam), (cm-2 - lam), with the
        fh-1 term split by history agreement (|fh-1 - N0| < 0.3), per (stat, scope group). Repairs the
        N0-pinned history with the full-history / complete-model projections where they carry information.
  mi-2  segment market-informed mean: lam' = line + w_seg (lam - line), w per (stat, scope group, history
        agreement), 0 <= w <= 1, least squares on past rows (no intercept: the side can only follow lam).
  mx-1  combination: mi-2 applied to the st-1 mean (w fitted on st-1's distance to the line).
  sd-1  per-segment spread of the candidate mean: sd scaled by the realized z-sd of past rows (only shipped on
        top of a mean change; HS was ~6% too wide).
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
FORMULA_C = {('kills', '1'): 1.442, ('kills', '12'): 1.4757, ('kills', '123'): 1.457,
             ('hs', '1'): 1.30, ('hs', '12'): 1.33, ('hs', '123'): 1.30}
AGREE_TOL = 0.3
MIN_SEG = 80


def pover(lam: float, sd: float, line: float) -> float:
    """P(Over) of a normal predictive distribution with continuity correction; pushes (integer line) excluded."""
    sd = max(sd, 1e-6)
    if line != int(line):
        up = dn = math.floor(line) + 0.5
    else:
        up, dn = line + 0.5, line - 0.5
    po = 1 - _N.cdf((up - lam) / sd)
    pu = _N.cdf((dn - lam) / sd)
    s = po + pu
    return 0.5 if s <= 0 else min(max(po / s, 1e-4), 1 - 1e-4)


def implied_sd(lam: float, line: float, p: float, prop: str, scope: str) -> float:
    """Spread the LIVE model used for this row, recovered from its logged chance (so a candidate changes only the
    mean). Falls back to the live spread formula C x sqrt(lam) when the logged chance cannot pin it."""
    f = FORMULA_C.get((prop, scope), 1.4) * math.sqrt(max(lam, 1.0))
    if abs(lam - line) < 0.3 or abs(p - 0.5) < 0.02 or p <= 0.001 or p >= 0.999:
        return f
    if (p > 0.5) != (lam > line):
        return f
    lo, hi = 0.3 * f, 3.0 * f
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        pm = pover(lam, mid, line)
        # larger sd -> chance closer to 0.5
        if abs(pm - 0.5) > abs(p - 0.5):
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def scope_group(scope: str) -> str:
    return '1' if scope == '1' else '12'


def _ch(x: dict, key: str):
    c = (x.get('ch') or {}).get(key) or {}
    return c.get('lam')


def agree(x: dict):
    c = (x.get('ch') or {}).get('fh-001') or {}
    if c.get('lam') is None or c.get('lam_base') is None:
        return None
    return abs(c['lam'] - c['lam_base']) < AGREE_TOL


def seg_cell(x):
    return (x['prop'], scope_group(x['scope']))


def seg_agree(x):
    return (x['prop'], scope_group(x['scope']), agree(x))


# ---------- st-1: stacked no-line mean ----------
def st_features(x: dict) -> list:
    lam = x['lam']
    fh = _ch(x, 'fh-001')
    cm1 = _ch(x, 'cm-001')
    cm2 = _ch(x, 'cm-002')
    a = agree(x)
    dfh = (fh - lam) if fh is not None else 0.0
    return [dfh if a else 0.0,
            dfh if a is False else 0.0,
            (cm1 - lam) if cm1 is not None else 0.0,
            (cm2 - lam) if cm2 is not None else 0.0]


def fit_st(train: list, ridge: float = 50.0) -> dict:
    out = {}
    for c in {seg_cell(x) for x in train}:
        g = [x for x in train if seg_cell(x) == c]
        if len(g) < MIN_SEG:
            continue
        X = np.array([st_features(x) for x in g], float)
        y = np.array([x['actual'] - x['lam'] for x in g], float)
        A = X.T @ X + ridge * np.eye(X.shape[1])
        out[c] = np.linalg.solve(A, X.T @ y)
    return out


def lam_st(x: dict, F: dict) -> float:
    b = F.get(seg_cell(x))
    if b is None:
        return x['lam']
    return max(0.5, x['lam'] + float(np.dot(b, st_features(x))))


# ---------- mi-2: segment market-informed mean ----------
def fit_w(train: list, mean_fn=lambda x: x['lam'], seg=seg_agree) -> dict:
    """w per segment from least squares of (actual - line) on (mean - line), no intercept, clipped to [0, 1]."""
    out = {}
    for k in {seg(x) for x in train}:
        g = [x for x in train if seg(x) == k]
        if len(g) < MIN_SEG:
            continue
        d = np.array([mean_fn(x) - x['line'] for x in g], float)
        y = np.array([x['actual'] - x['line'] for x in g], float)
        den = float(d @ d)
        out[k] = 0.0 if den <= 0 else min(max(float(d @ y) / den, 0.0), 1.0)
    return out


def lam_mi(x: dict, W: dict, Wcell: dict, mean: float) -> float:
    w = W.get(seg_agree(x))
    if w is None:
        w = Wcell.get(seg_cell(x), 1.0)
    return x['line'] + w * (mean - x['line'])


# ---------- sd-1: per-segment spread of a candidate mean ----------
def fit_sd_scale(train: list, mean_fn, sd_fn) -> dict:
    out = {}
    for c in {seg_cell(x) for x in train}:
        g = [x for x in train if seg_cell(x) == c]
        if len(g) < MIN_SEG:
            continue
        z = np.array([(x['actual'] - mean_fn(x)) / sd_fn(x) for x in g], float)
        out[c] = float(min(max(np.sqrt(np.mean(z * z)), 0.7), 1.3))
    return out
