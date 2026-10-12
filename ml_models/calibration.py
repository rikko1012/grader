"""Market-calibration model for CS2 series-winner (moneyline) prices - research only.

Idea: the price at the cutoff already contains (almost) everything; the only thing this model may learn is a CORRECTION
of the market's own log-odds L = logit(price of team A):

    logit p = w_L * L  +  sum_k g_k * L * z_k  +  sum_j h_j * m_j  (+ b0)

  * w_L      - the favourite-longshot slope (1 = market is calibrated, > 1 = favourites under-priced);
  * L * z_k  - the slope varying with a SYMMETRIC covariate z_k (best-of, platform, tier proxy, market activity,
               staleness, volatility, time of day ...): thin / low-tier markets may be mis-scaled;
  * m_j      - an ANTISYMMETRIC main effect (the market's own move over the last 6 h / 24 h: momentum or reversal);
  * b0       - an intercept (team-A orientation bias; only as a candidate).

Without b0 the model is odd in (L, m): swapping team A and B flips the forecast exactly. Every weight is shrunk by an
L2 penalty toward the MARKET (w_L = 1, everything else 0), so lambda -> infinity returns the market price unchanged.

Inputs are only fields known at the cutoff (scheduled start - 30 min): the price at the cutoff, its 6 h / 24 h / 48 h
history, the platform, best-of, Kalshi 48 h volume, HLTV tier proxy (`tier_mean`, from HLTV rows dated before the
match day) and the scheduled start's UTC hour / weekday. `volume_final_posthoc` (final volume incl. in-play) and every
outcome field are refused (FORBIDDEN).

Deployment: `fit`, `predict`, `raw_from_rows` and the selection code use numpy only (requirements.txt already has
numpy; pandas is NOT in requirements.txt and is imported only by the research CLI). A fitted model is a plain
JSON-serialisable dict.

PRE-DECLARED evaluation (written before any run; the protocol is the owner's pre-registered one):
  * primary analysis = one row per match: the platform with the smaller bid/ask spread at the cutoff when both exist,
    else the one available, ties -> Polymarket. Polymarket's historical spread is NOT recorded (prices-history is a
    mid/last-trade series), so it is taken as one tick (0.01, the minimum possible) - Kalshi then wins only with a
    spread < 0.01, which no Kalshi row has, so the rule reduces to "Polymarket when listed, else Kalshi". This
    operationalisation uses no outcome.
  * blocks = calendar months of the cutoff (two blocks, days 1-14 and 15-end, when a month has > 600 matches); a block
    is predicted by a model trained only on rows with cutoff < block start - 1 day; the first block is the first one
    with >= 1500 distinct training matches.
  * inside every training window: inner expanding-window CV (4 validation chunks covering the last 50 % of the
    training rows by cutoff, each inner fit trained only on rows with cutoff < chunk start - 1 day) scores every
    candidate (config x lambda, plus the unchanged market); the selection rule picks from those inner scores only.
  * selection rules (all reported, PRIMARY = R1):
      R1_min_P1   pool P1 (configs c01-c10 x 10 lambdas + market) - lowest inner log-loss         <- PRIMARY
      R2_1se_P1   pool P1 - simplest candidate within one standard error of the best (market first)
      R3_top5_P1  pool P1 - mean logit of the 5 best inner candidates
      R4_min_P2   pool P2 (P1 + c11-c13) - lowest inner log-loss
      R5_1se_P2   pool P2 - one-standard-error rule
    plus every single config c01-c13 with only its lambda chosen inside the window (13 more models).
    Nothing is selected on an evaluation block; every one of these numbers is reported.
  * ROUND 2 (declared AFTER the round-1 results had been seen - disclosed, and none of it can replace R1 as the
    pre-registered primary): only the machinery inside the training window changes -
      decay  candidates also fitted with time-decay sample weights (half-life 180 d / 90 d before the window's last
             cutoff), chosen or not by the same inner CV;
      wide   inner CV with 6 validation chunks covering the last 75 % of the window (more inner validation rows);
      R6_min_P2_decay, R7_1se_P2_decay, R8_min_P2_wide, R9_1se_P2_wide, R10_min_P2_decay_wide,
      R11_top5_P2_decay_wide;
      R12_min_P0_decay_wide / R13_min_P0  pool P0 = market + c01-c03 (pure favourite-longshot terms). P0 was picked
             because c01 looked best in round 1, i.e. WITH knowledge of the evaluation blocks: reported, never eligible.
  * ROI: back a side when model p >= price + 1 cent + t; t from EDGE_GRID chosen per block on the inner out-of-fold
    predictions of the same rule (max inner net units with >= MIN_INNER_BETS bets and net > 0, else no bets).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

COST = 0.01                       # one cent added to the recorded price for every hypothetical bet
LAMS = (10000.0, 3000.0, 1000.0, 300.0, 100.0, 30.0, 10.0, 3.0, 1.0, 0.3)   # strongest first (ties -> stronger)
EDGE_GRID = (0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
MIN_INNER_BETS = 25
INNER_FOLDS = 4
GAP_S = 86400                     # 1 day between the last training cutoff and the first predicted cutoff
PM_SPREAD_ASSUMED = 0.01          # Polymarket's historical spread is not recorded -> its minimum (one tick)
B_BOOT = 2000
SEED = 7

FORBIDDEN = frozenset({"volume_final_posthoc", "platform_resolved", "y", "map_a", "map_b", "hltv_bo"})
RAW_COLS = ("mkt_p", "platform", "bo", "bo_source", "tier_mean", "lan", "mkt_npts48", "mkt_age_min", "k_vol48",
            "mkt_max48", "mkt_min48", "mkt_mv6", "mkt_mv24", "start_ts")
BO_KNOWN_SOURCES = ("market title", "polymarket title (same match)")   # 'hltv format / maps played' can be post-hoc

# ---- terms ---------------------------------------------------------------------------------------------------------
# kind: 'L' market log-odds (prior weight 1), 'icpt', 'odd' (odd main effect, scaled by its training sd, missing -> 0),
# 'raw' (odd term used as is), 'x' (L * standardised symmetric covariate; missing -> the training mean)
TERMS = {
    "L": ("L", None), "icpt": ("icpt", None),
    "flb2": ("odd", "LabsL"),
    "sgn": ("raw", "sgnL"), "kink1": ("raw", "Lk1"), "kink2": ("raw", "Lk2"),
    "x_bo1": ("x", "bo1"), "x_bo5": ("x", "bo5"), "x_kalshi": ("x", "is_k"),
    "x_tier": ("x", "tier"), "x_lan": ("x", "lan"),
    "x_lnpts": ("xp", "lnpts"), "x_lage": ("xp", "lage"), "x_kvol": ("x", "kvol"),
    "x_volat": ("x", "volat"), "x_absmv6": ("x", "absmv6"),
    "m_mv6": ("odd", "mv6"), "m_mv24": ("odd", "mv24"),
    "x_night": ("x", "h_night"), "x_day": ("x", "h_day"), "x_wkend": ("x", "wkend"),
}
GROUPS = {
    "icpt": ["icpt"], "flb": ["flb2"], "bo": ["x_bo1", "x_bo5"], "plat": ["x_kalshi"], "tier": ["x_tier", "x_lan"],
    "act": ["x_lnpts", "x_lage", "x_kvol"], "vol": ["x_volat", "x_absmv6"], "move": ["m_mv6", "m_mv24"],
    "hour": ["x_night", "x_day", "x_wkend"], "kink": ["sgn", "kink1", "kink2"],
}


def _cfg(*groups):
    return ["L"] + [t for g in groups for t in GROUPS[g]]


CONFIGS = {
    "c01_slope": _cfg(),
    "c02_slope_icpt": _cfg("icpt"),
    "c03_flb_curve": _cfg("flb"),
    "c04_bo_platform": _cfg("bo", "plat"),
    "c05_tier": _cfg("tier"),
    "c06_activity": _cfg("act"),
    "c07_volatility": _cfg("vol"),
    "c08_move": _cfg("move"),
    "c09_full_icpt": _cfg("icpt", "flb", "bo", "plat", "tier", "act", "vol", "move"),
    "c10_full": _cfg("flb", "bo", "plat", "tier", "act", "vol", "move"),
    # pool P2 additions (declared with P1, before any run)
    "c11_kink": _cfg("kink"),
    "c12_time": _cfg("hour"),
    "c13_full_kink_time": _cfg("flb", "bo", "plat", "tier", "act", "vol", "move", "kink", "hour"),
}
P0 = ["c01_slope", "c02_slope_icpt", "c03_flb_curve"]
P1 = [c for c in CONFIGS if c <= "c10"]
P2 = list(CONFIGS)
HALF_LIVES = (None, 180.0, 90.0)                  # days; None = unweighted (always first: ties -> no decay)
SCHEMES = {"std": {"folds": 4, "frac": 0.5}, "wide": {"folds": 6, "frac": 0.25}}
ND = (None,)
# name: (scheme, pool, half-lives, how, round, eligible for the pre-registered claim)
RULES = {"R1_min_P1": ("std", P1, ND, "min", 1, True), "R2_1se_P1": ("std", P1, ND, "1se", 1, True),
         "R3_top5_P1": ("std", P1, ND, "top5", 1, True), "R4_min_P2": ("std", P2, ND, "min", 1, True),
         "R5_1se_P2": ("std", P2, ND, "1se", 1, True),
         "R6_min_P2_decay": ("std", P2, HALF_LIVES, "min", 2, False),
         "R7_1se_P2_decay": ("std", P2, HALF_LIVES, "1se", 2, False),
         "R8_min_P2_wide": ("wide", P2, ND, "min", 2, False), "R9_1se_P2_wide": ("wide", P2, ND, "1se", 2, False),
         "R10_min_P2_decay_wide": ("wide", P2, HALF_LIVES, "min", 2, False),
         "R11_top5_P2_decay_wide": ("wide", P2, HALF_LIVES, "top5", 2, False),
         "R12_min_P0_decay_wide": ("wide", P0, HALF_LIVES, "min", 2, False),
         "R13_min_P0": ("std", P0, ND, "min", 2, False)}
PRIMARY_RULE = "R1_min_P1"
MARKET = ("market", None, None)


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


# ---- raw inputs (numpy only) ---------------------------------------------------------------------------------------
def raw_from_rows(rows):
    """rows: a mapping column -> sequence (a dict of lists, a pandas DataFrame, ...) or a list of dicts.
    Returns a dict of float numpy arrays: everything the terms need, computed from cutoff-time fields only."""
    if isinstance(rows, (list, tuple)):
        rows = {c: [r.get(c) for r in rows] for c in RAW_COLS}
    bad = FORBIDDEN.intersection(set(RAW_COLS))
    assert not bad, bad

    def col(c, default=np.nan):
        try:
            v = rows[c]
        except (KeyError, IndexError):
            return None
        a = np.array([default if x is None else x for x in list(v)], dtype=object)
        return a

    def f(c):
        a = col(c)
        if a is None:
            return None
        out = np.full(len(a), np.nan)
        for i, x in enumerate(a):
            try:
                xf = float(x)
                out[i] = xf if math.isfinite(xf) else np.nan
            except (TypeError, ValueError):
                pass
        return out

    p = f("mkt_p")
    n = len(p)
    nanv = np.full(n, np.nan)
    L = logit(p)
    plat = col("platform")
    is_k = np.array([str(x).lower() == "kalshi" for x in plat], float) if plat is not None else np.zeros(n)
    bo = f("bo") if f("bo") is not None else nanv
    src = col("bo_source")
    known = np.array([str(s) in BO_KNOWN_SOURCES for s in src], bool) if src is not None else np.ones(n, bool)
    mx, mn = f("mkt_max48"), f("mkt_min48")
    mv6 = f("mkt_mv6") if f("mkt_mv6") is not None else nanv
    mv24 = f("mkt_mv24") if f("mkt_mv24") is not None else nanv
    st = f("start_ts")
    hour = np.full(n, np.nan)
    wkend = np.full(n, np.nan)
    if st is not None:
        for i, s in enumerate(st):
            if np.isfinite(s):
                d = datetime.fromtimestamp(s, tz=timezone.utc)
                hour[i], wkend[i] = d.hour, float(d.weekday() >= 5)
    kv = f("k_vol48")
    kvol = np.where(is_k > 0, np.log1p(np.where(np.isfinite(kv), kv, np.nan)), np.nan) if kv is not None else nanv
    absL = np.abs(L)
    return {
        "L": L, "is_k": is_k, "LabsL": L * absL, "sgnL": np.sign(L),
        "Lk1": L * (absL > 1.0), "Lk2": L * (absL > 2.0),
        "bo1": np.where(known, (bo == 1).astype(float), np.nan), "bo5": np.where(known, (bo == 5).astype(float), np.nan),
        "tier": f("tier_mean") if f("tier_mean") is not None else nanv,
        "lan": f("lan") if f("lan") is not None else nanv,
        "lnpts": np.log1p(f("mkt_npts48")) if f("mkt_npts48") is not None else nanv,
        "lage": np.log1p(f("mkt_age_min")) if f("mkt_age_min") is not None else nanv,
        "kvol": kvol,
        "volat": (logit(mx) - logit(mn)) if mx is not None and mn is not None else nanv,
        "mv6": mv6, "mv24": mv24, "absmv6": np.abs(mv6),
        "h_night": np.where(np.isfinite(hour), (hour < 8).astype(float), np.nan),
        "h_day": np.where(np.isfinite(hour), ((hour >= 8) & (hour < 16)).astype(float), np.nan),
        "wkend": wkend,
    }


def subset(raw, idx):
    return {k: v[idx] for k, v in raw.items()}


# ---- design --------------------------------------------------------------------------------------------------------
def _mean_sd(v):
    v = v[np.isfinite(v)]
    if len(v) < 2:
        return 0.0, 0.0
    return float(v.mean()), float(v.std())


def term_stats(raw, terms):
    """Standardisation stats for the terms, from TRAINING rows only."""
    st = {}
    for t in terms:
        kind, src = TERMS[t]
        if kind == "x":
            m, s = _mean_sd(raw[src])
            st[t] = {"mean": m, "sd": s}
        elif kind == "odd":                           # odd terms are scaled, never centred (centring = an intercept)
            v = raw[src][np.isfinite(raw[src])]
            st[t] = {"mean": 0.0, "sd": float(np.sqrt(np.mean(v ** 2))) if len(v) > 1 else 0.0}
        elif kind == "xp":                            # standardised within platform (counts differ by platform)
            st[t] = {}
            for pk, pm in (("k", raw["is_k"] > 0), ("p", raw["is_k"] <= 0)):
                m, s = _mean_sd(raw[src][pm])
                if np.isfinite(raw[src][pm]).sum() < 30:
                    m, s = _mean_sd(raw[src])
                st[t][pk] = {"mean": m, "sd": s}
    return st


def design(raw, terms, st):
    L = raw["L"]
    cols = []
    for t in terms:
        kind, src = TERMS[t]
        if kind == "L":
            c = L
        elif kind == "icpt":
            c = np.ones_like(L)
        elif kind == "raw":
            c = raw[src]
        elif kind == "odd":
            s = st[t]["sd"]
            c = np.where(np.isfinite(raw[src]), raw[src] / s, 0.0) if s > 1e-12 else np.zeros_like(L)
        elif kind == "x":
            m, s = st[t]["mean"], st[t]["sd"]
            z = np.where(np.isfinite(raw[src]), (raw[src] - m) / s, 0.0) if s > 1e-12 else np.zeros_like(L)
            c = L * z
        else:                                         # 'xp'
            z = np.zeros_like(L)
            for pk, pm in (("k", raw["is_k"] > 0), ("p", raw["is_k"] <= 0)):
                m, s = st[t][pk]["mean"], st[t][pk]["sd"]
                if s > 1e-12:
                    v = raw[src]
                    z = np.where(pm & np.isfinite(v), (v - m) / s, z)
            c = L * z
        cols.append(np.asarray(c, float))
    return np.column_stack(cols)


def _newton(X, y, w0, lam, iters=100, sw=None):
    w = w0.copy()
    P = np.full(len(w0), float(lam))
    sw = np.ones(len(y)) if sw is None else np.asarray(sw, float)
    for _ in range(iters):
        p = sigmoid(X @ w)
        g = X.T @ (sw * (p - y)) + P * (w - w0)
        H = (X * (sw * p * (1 - p))[:, None]).T @ X + np.diag(P) + 1e-9 * np.eye(len(w0))
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    return w


def decay_weights(cut, half_life_days):
    """Sample weights 0.5 ** (age / half-life), age measured from the LAST training cutoff (training rows only)."""
    if half_life_days is None:
        return None
    cut = np.asarray(cut, float)
    return 0.5 ** ((cut.max() - cut) / (half_life_days * 86400.0))


def fit(raw, y, config, lam, sw=None):
    """L2 logistic correction of the market log-odds; every weight shrunk toward the market (w_L = 1, others 0).
    sw: optional sample weights (time decay). Returns a JSON-serialisable model dict."""
    if config == "market":
        return {"config": "market", "terms": ["L"], "w": [1.0], "stats": {}, "lam": None}
    terms = CONFIGS[config]
    st = term_stats(raw, terms)
    X = design(raw, terms, st)
    w0 = np.array([1.0 if t == "L" else 0.0 for t in terms])
    w = _newton(X, np.asarray(y, float), w0, lam, sw=sw)
    return {"config": config, "terms": terms, "w": [float(x) for x in w], "stats": st, "lam": float(lam)}


def predict_logit(model, raw):
    if model["config"] == "market":
        return raw["L"].copy()
    return design(raw, model["terms"], model["stats"]) @ np.asarray(model["w"])


def predict(model, raw):
    return sigmoid(predict_logit(model, raw))


def predict_rows(model, rows):
    """Deployment entry point: rows -> probability that team A wins (numpy only)."""
    return predict(model, raw_from_rows(rows))


# ---- selection inside a training window ------------------------------------------------------------------------------
def candidates(pool, hls=ND):
    return [MARKET] + [(c, lam, h) for h in hls for c in pool for lam in LAMS]


def cand_name(c):
    if c[0] == "market":
        return "market"
    return f"{c[0]}@{c[1]:g}" + ("" if c[2] is None else f"/hl{c[2]:g}d")


def n_terms(c):
    return 0 if c[0] == "market" else len(CONFIGS[c[0]])


def inner_cv(raw, y, cut, pool, folds=INNER_FOLDS, gap=GAP_S, frac=0.5, hls=ND):
    """Expanding-window CV inside the training rows (sorted by cutoff): `folds` validation chunks covering the last
    (1 - frac) of the rows; each inner fit uses only rows with cutoff < chunk start - gap.
    Returns (val_idx, {candidate: logit preds})."""
    order = np.argsort(cut, kind="stable")
    n = len(order)
    edges = [int(n * (frac + (1 - frac) * i / folds)) for i in range(folds + 1)]
    val_idx, preds = [], {c: [] for c in candidates(pool, hls)}
    for i in range(folds):
        vi = order[edges[i]:edges[i + 1]]
        if len(vi) < 20:
            continue
        ti = np.where(cut < cut[vi].min() - gap)[0]
        if len(ti) < 100:
            continue
        rt, rv = subset(raw, ti), subset(raw, vi)
        val_idx.append(vi)
        sws = {h: decay_weights(cut[ti], h) for h in hls}
        for c in preds:
            m = fit(rt, y[ti], c[0], c[1], sw=sws[c[2]])
            preds[c].append(predict_logit(m, rv))
    vi = np.concatenate(val_idx)
    return vi, {c: np.concatenate(v) for c, v in preds.items()}


def select(rule, pool, vi, zs, y, hls=ND):
    """Returns a list of chosen candidates (averaged in logit space) from inner scores only."""
    cands = candidates(pool, hls)
    yv = y[vi]
    loss = {c: logloss(sigmoid(zs[c]), yv) for c in cands}
    mean = {c: float(loss[c].mean()) for c in cands}
    best = min(cands, key=lambda c: mean[c])           # stable: market first, then config order, lambda strongest
    if rule == "min":
        return [best]
    if rule == "top5":
        return sorted(cands, key=lambda c: mean[c])[:5]
    if rule == "1se":
        ok = []
        for c in cands:
            d = loss[c] - loss[best]
            se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else 0.0
            if d.mean() <= se + 1e-12:
                ok.append(c)
        simp = sorted(ok, key=lambda c: (n_terms(c), -(c[1] or 1e18), c[2] is not None))
        return [simp[0]]
    raise ValueError(rule)


def choose_edge(p_inner, price, y):
    """Edge threshold for the hypothetical-ROI check, from inner out-of-fold predictions only."""
    best = (None, 0.0, 0)
    for t in EDGE_GRID:
        r = bet_returns(p_inner, price, y, t)
        nb, net = len(r), float(np.sum(r)) if len(r) else 0.0
        if nb >= MIN_INNER_BETS and net > best[1]:
            best = (t, net, nb)
    return best[0]


def bet_returns(p, price, y, t, groups=None):
    p, price, y = np.asarray(p, float), np.asarray(price, float), np.asarray(y, float)
    a = p >= price + COST + t
    b = (~a) & ((1 - p) >= (1 - price) + COST + t)
    ca, cb = price + COST, 1 - price + COST
    r = np.where(a, np.where(y == 1, (1 - ca) / ca, -1.0), np.where(y == 0, (1 - cb) / cb, -1.0))
    m = a | b
    if groups is not None:
        return r[m], np.asarray(groups)[m]
    return r[m]


# ---- metrics -------------------------------------------------------------------------------------------------------
def _group_index(groups):
    _, inv = np.unique(np.asarray(groups).astype(str), return_inverse=True)
    return inv


def boot_ratio(vals, groups, B=B_BOOT, seed=SEED):
    """Match-grouped bootstrap 95 % CI of a mean (sum over resampled matches / their row count)."""
    vals = np.asarray(vals, float)
    inv = _group_index(groups)
    g = inv.max() + 1
    s = np.bincount(inv, weights=vals, minlength=g)
    c = np.bincount(inv, minlength=g).astype(float)
    idx = np.random.default_rng(seed).integers(0, g, size=(B, g))
    st = s[idx].sum(1) / c[idx].sum(1)
    return [float(np.percentile(st, 2.5)), float(np.percentile(st, 97.5))]


def calib_fit(p, y):
    """Calibration intercept/slope: logistic regression of y on logit(p)."""
    X = np.column_stack([np.ones(len(p)), logit(p)])
    w = np.array([0.0, 1.0])
    for _ in range(50):
        q = sigmoid(X @ w)
        H = (X * (q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, X.T @ (q - y))
        w -= step
        if np.abs(step).max() < 1e-10:
            break
    return float(w[0]), float(w[1])


def calib_boot(p, y, groups, B=B_BOOT, seed=SEED):
    inv = _group_index(groups)
    g = inv.max() + 1
    members = [np.where(inv == k)[0] for k in range(g)]
    rng = np.random.default_rng(seed + 1)
    a, b = [], []
    for _ in range(B):
        pick = rng.integers(0, g, size=g)
        ix = np.concatenate([members[k] for k in pick])
        i0, s0 = calib_fit(p[ix], y[ix])
        a.append(i0)
        b.append(s0)
    return ([float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))],
            [float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))])


def r4(x):
    return None if x is None else round(float(x), 4)


def summarize(p, y, mkt, groups, blocks_of_rows, edge_by_row=None, calib=True):
    p, y, mkt = np.asarray(p, float), np.asarray(y, float), np.asarray(mkt, float)
    llm, llp = logloss(mkt, y), logloss(p, y)
    out = {"n": int(len(y)), "n_matches": int(len(np.unique(np.asarray(groups).astype(str)))),
           "ll_market": r4(llm.mean()), "ll_market_ci": [r4(x) for x in boot_ratio(llm, groups)],
           "ll_model": r4(llp.mean()), "ll_model_ci": [r4(x) for x in boot_ratio(llp, groups)],
           "gain": round(float((llm - llp).mean()), 5), "gain_ci": [round(x, 5) for x in boot_ratio(llm - llp, groups)],
           "brier_market": r4(((mkt - y) ** 2).mean()), "brier_model": r4(((p - y) ** 2).mean()),
           "brier_gain": round(float(((mkt - y) ** 2 - (p - y) ** 2).mean()), 5),
           "brier_gain_ci": [round(x, 5) for x in boot_ratio((mkt - y) ** 2 - (p - y) ** 2, groups)]}
    bl = np.asarray(blocks_of_rows)
    gains = {}
    for b in sorted(set(bl)):
        m = bl == b
        gains[b] = float((llm[m] - llp[m]).mean())
    out["blocks_positive"] = int(sum(g > 0 for g in gains.values()))
    out["blocks_total"] = len(gains)
    out["blocks_positive_share"] = round(out["blocks_positive"] / max(1, len(gains)), 3)
    if calib:
        i0, s0 = calib_fit(p, y)
        ic, sc = calib_boot(p, y, groups)
        out["calibration"] = {"intercept": r4(i0), "intercept_ci": [r4(x) for x in ic], "slope": r4(s0),
                              "slope_ci": [r4(x) for x in sc]}
        i1, s1 = calib_fit(mkt, y)
        out["calibration_market"] = {"intercept": r4(i1), "slope": r4(s1)}
        bins = []
        for lo in np.arange(0, 1, 0.1):
            m = (p >= lo) & (p < lo + 0.1)
            if m.sum():
                bins.append({"bin": f"{lo:.1f}-{lo + 0.1:.1f}", "n": int(m.sum()), "mean_p": r4(p[m].mean()),
                             "mean_market": r4(mkt[m].mean()), "observed": r4(y[m].mean())})
        out["calibration_bins"] = bins
    if edge_by_row is not None:
        out["roi_inner_threshold"] = roi_summary(p, mkt, y, groups, edge_by_row)
        out["roi_fixed_3pt_reference"] = roi_summary(p, mkt, y, groups, np.full(len(y), 0.03))
    return out


def roi_summary(p, price, y, groups, edges):
    rs, gs = [], []
    for t in sorted(set(x for x in edges if x is not None and np.isfinite(x))):
        m = edges == t
        r, g = bet_returns(p[m], price[m], y[m], t, groups=np.asarray(groups)[m])
        rs.append(r)
        gs.append(g)
    if not rs or not sum(len(r) for r in rs):
        return {"bets": 0}
    r, g = np.concatenate(rs), np.concatenate(gs)
    out = {"bets": int(len(r)), "won": int((r > 0).sum()), "net_units": round(float(r.sum()), 2),
           "roi": round(float(r.mean()), 4)}
    if len(r) >= 20:
        out["roi_ci"] = [round(x, 4) for x in boot_ratio(r, g)]
    return out


# ---- walk-forward protocol (research harness; pandas only here) ------------------------------------------------------
def primary_rows(df):
    """Frozen baseline rule: smaller spread at the cutoff when both platforms exist, else the one available, ties ->
    Polymarket. Polymarket's spread at the cutoff is not recorded -> PM_SPREAD_ASSUMED (one tick)."""
    import pandas as pd
    pm = df[df.platform == "polymarket"].set_index("match_id")
    k = df[df.platform == "kalshi"].set_index("match_id")
    keep = []
    for mid in sorted(set(pm.index) | set(k.index)):
        if mid in pm.index and mid in k.index:
            ks = float(k.loc[mid, "k_spread"]) if pd.notna(k.loc[mid, "k_spread"]) else 1.0
            keep.append(("kalshi" if ks < PM_SPREAD_ASSUMED - 1e-9 else "polymarket", mid))
        else:
            keep.append(("polymarket" if mid in pm.index else "kalshi", mid))
    key = pd.MultiIndex.from_tuples(keep, names=["platform", "match_id"])
    out = df.set_index(["platform", "match_id"]).loc[key].reset_index()
    return out


def make_blocks(cut_s, match_ids, min_train=1500, big=600):
    """[(label, start_s, end_s)] by the cutoff (unix s): calendar months, halves (1-14 / 15-end) for months with > big
    matches; first block = first with >= min_train distinct training matches (cutoff < start - 1 day)."""
    import pandas as pd
    c = pd.to_datetime(pd.Series(cut_s), unit="s", utc=True)
    mids = np.asarray(match_ids).astype(str)
    months = sorted(c.dt.strftime("%Y-%m").unique())
    blocks = []
    for mth in months:
        ms = pd.Timestamp(mth + "-01", tz="UTC")
        me = ms + pd.offsets.MonthBegin(1)
        inm = (c >= ms) & (c < me)
        if len(np.unique(mids[inm.to_numpy()])) > big:
            mid_ = ms + pd.Timedelta(days=14)
            parts = [(mth + "a(1-14)", ms, mid_), (mth + "b(15-end)", mid_, me)]
        else:
            parts = [(mth, ms, me)]
        for lab, s, e in parts:
            ntr = len(np.unique(mids[(np.asarray(cut_s) < s.timestamp() - GAP_S)]))
            if ntr >= min_train or blocks:
                blocks.append((lab, int(s.timestamp()), int(e.timestamp())))
    return blocks


def walk_forward(train_df, eval_df, blocks, log=print):
    """train_df: rows usable for training (y known); eval_df: rows to predict. Returns (oos rows, per-block info)."""
    import pandas as pd
    raw_tr_all = raw_from_rows(train_df)
    y_tr_all = train_df.y.to_numpy().astype(float)
    cut_tr_all = train_df.cutoff_ts.to_numpy().astype(float)
    raw_ev_all = raw_from_rows(eval_df)
    cut_ev = eval_df.cutoff_ts.to_numpy().astype(float)
    oos, per = [], []
    for lab, s, e in blocks:
        tri = np.where(cut_tr_all < s - GAP_S)[0]
        tei = np.where((cut_ev >= s) & (cut_ev < e))[0]
        if not len(tei):
            continue
        t0 = time.time()
        rtr, ytr, ctr = subset(raw_tr_all, tri), y_tr_all[tri], cut_tr_all[tri]
        rte = subset(raw_ev_all, tei)
        assert ctr.max() < s - GAP_S <= cut_ev[tei].min() - GAP_S, "training row too late"
        assert not set(train_df.match_id.to_numpy()[tri]) & set(eval_df.match_id.to_numpy()[tei]), "match in both"
        cv = {}
        for sch, kw in SCHEMES.items():
            hls_needed = tuple(sorted({h for r in RULES.values() if r[0] == sch for h in r[2]},
                                      key=lambda h: (h is not None, -(h or 0))))
            pool = sorted({c for r in RULES.values() if r[0] == sch for c in r[1]} | (set(P2) if sch == "std"
                                                                                       else set()), key=P2.index)
            cv[sch] = inner_cv(rtr, ytr, ctr, pool, folds=kw["folds"], frac=kw["frac"], hls=hls_needed)
        fitted = {}

        def zte(c):
            if c not in fitted:
                fitted[c] = fit(rtr, ytr, c[0], c[1], sw=decay_weights(ctr, c[2]))
            return predict_logit(fitted[c], rte)

        rec = te_rows = eval_df.iloc[tei][["match_id", "platform", "y", "mkt_p", "cutoff_ts"]].copy()
        rec["block"] = lab
        info = {"block": lab, "start": datetime.fromtimestamp(s, tz=timezone.utc).date().isoformat(),
                "end": datetime.fromtimestamp(e, tz=timezone.utc).date().isoformat(),
                "n_train": int(len(tri)), "n_train_matches": int(train_df.match_id.iloc[tri].nunique()),
                "n": int(len(tei)), "rules": {}, "configs": {}}
        for rule, (sch, pool, hls, how, _rnd, _elig) in RULES.items():
            vi, zs = cv[sch]
            price_in, y_in = sigmoid(rtr["L"][vi]), ytr[vi]
            chosen = select(how, pool, vi, zs, ytr, hls)
            z = np.mean([zte(c) for c in chosen], axis=0)
            zin = np.mean([zs[c] for c in chosen], axis=0)
            edge = choose_edge(sigmoid(zin), price_in, y_in)
            rec["p_" + rule] = sigmoid(z)
            rec["edge_" + rule] = np.nan if edge is None else edge
            info["rules"][rule] = {"chosen": [cand_name(c) for c in chosen], "edge_threshold": edge}
            if len(chosen) == 1 and chosen[0][0] != "market":
                m = fitted[chosen[0]]
                info["rules"][rule]["weights"] = dict(zip(m["terms"], [round(x, 4) for x in m["w"]]))
        vi, zs = cv["std"]
        for cfg in P2:                                # single configs: only lambda chosen inside the window
            yv = ytr[vi]
            lam = min(LAMS, key=lambda l: float(logloss(sigmoid(zs[(cfg, l, None)]), yv).mean()))
            rec["p_cfg_" + cfg] = sigmoid(zte((cfg, lam, None)))
            info["configs"][cfg] = {"lam": lam, "w_L": round(fitted[(cfg, lam, None)]["w"][0], 4)}
        info["n_inner_val"] = {k: int(len(v[0])) for k, v in cv.items()}
        info["inner_ll_market"] = r4(logloss(sigmoid(rtr["L"][cv["std"][0]]), ytr[cv["std"][0]]).mean())
        llm = logloss(rec.mkt_p.to_numpy(), rec.y.to_numpy())
        llp = logloss(rec["p_" + PRIMARY_RULE].to_numpy(), rec.y.to_numpy())
        info["ll_market"], info["ll_primary"] = r4(llm.mean()), r4(llp.mean())
        info["gain_primary"] = round(float((llm - llp).mean()), 5)
        info["gain_primary_ci"] = [round(x, 5) for x in boot_ratio(llm - llp, rec.match_id.to_numpy())]
        info["secs"] = round(time.time() - t0, 1)
        log(f"  {lab}: n_train {info['n_train']} n {info['n']} R1 chose {info['rules'][PRIMARY_RULE]['chosen']} "
            f"gain {info['gain_primary']:+.4f} {info['gain_primary_ci']} ({info['secs']} s)")
        oos.append(rec)
        per.append(info)
    return pd.concat(oos, ignore_index=True), per


def evaluate_run(name, train_df, eval_df, blocks, outdir, log=print):
    log(f"[{name}] train rows {len(train_df)}, eval rows {len(eval_df)}, blocks {[b[0] for b in blocks]}")
    if not blocks:
        return {"name": name, "blocks": [], "note": "no block reaches the training minimum"}
    o, per = walk_forward(train_df, eval_df, blocks, log=log)
    y, mkt, g, bl = o.y.to_numpy().astype(float), o.mkt_p.to_numpy(), o.match_id.to_numpy(), o.block.to_numpy()
    models = {}
    for rule in RULES:
        models[rule] = summarize(o["p_" + rule].to_numpy(), y, mkt, g, bl, o["edge_" + rule].to_numpy())
    for cfg in P2:
        models["cfg_" + cfg] = summarize(o["p_cfg_" + cfg].to_numpy(), y, mkt, g, bl, calib=True)
    for b in per:                                     # per-block gain + CI for every rule (CI only for rules)
        m = bl == b["block"]
        llm = logloss(mkt[m], y[m])
        b["brier_market"] = r4(((mkt[m] - y[m]) ** 2).mean())
        for rule in RULES:
            p = o["p_" + rule].to_numpy()[m]
            d = llm - logloss(p, y[m])
            b["rules"][rule]["gain"] = round(float(d.mean()), 5)
            b["rules"][rule]["gain_ci"] = [round(x, 5) for x in boot_ratio(d, g[m])]
            b["rules"][rule]["brier_model"] = r4(((p - y[m]) ** 2).mean())
        for cfg in P2:
            d = llm - logloss(o["p_cfg_" + cfg].to_numpy()[m], y[m])
            b["configs"][cfg]["gain"] = round(float(d.mean()), 5)
    for k, v in models.items():
        v["pass_checks"] = pass_checks(v)
        if k in RULES:
            v["round"], v["eligible_for_preregistered_claim"] = RULES[k][4], RULES[k][5] and k == PRIMARY_RULE
    o.to_pickle(os.path.join(outdir, f"oos_calibration_{name}.pkl"))
    return {"name": name, "n_train_rows_total": int(len(train_df)), "n_eval_rows": int(len(o)),
            "blocks": per, "models": models, "validation_pass_primary_rule": models[PRIMARY_RULE]["pass_checks"]}


def pass_checks(v):
    """The pre-registered VALIDATION PASS applied to one model's pooled out-of-sample summary."""
    vp = {"gain_positive_ci_excludes_0": bool(v["gain"] > 0 and v["gain_ci"][0] > 0),
          "blocks_positive_share_ge_0.6": bool(v["blocks_positive_share"] >= 0.6),
          "calibration_slope_in_0.8_1.2": bool(0.8 <= v["calibration"]["slope"] <= 1.2)}
    vp["PASS"] = all(vp.values())
    return vp


def load_dataset(path):
    import pandas as pd
    if path.endswith(".parquet") and os.path.exists(path):
        return pd.read_parquet(path), path
    alt = os.path.splitext(path)[0] + ".pkl"
    if os.path.exists(alt):
        return pd.read_pickle(alt), alt + " (parquet missing: no parquet engine installed; same rows)"
    raise FileNotFoundError(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    default_dir = os.path.join(os.environ.get("TEMP", "/tmp"), "claude",
                               "C--Users-tikic-OneDrive-Documents-cs2-scout", "6847ff60-df65-4b0c-851b-1b79f55ef3bf",
                               "scratchpad", "ml_research")
    ap.add_argument("--dataset", default=os.path.join(default_dir, "dataset.parquet"))
    ap.add_argument("--out", default=os.path.join(default_dir, "results_calibration.json"))
    ap.add_argument("--runs", default="primary,polymarket,kalshi,kalshi_pooled_train")
    a = ap.parse_args(argv)
    import pandas as pd
    df, used = load_dataset(a.dataset)
    assert df.groupby(["platform", "match_id"]).size().max() == 1
    outdir = os.path.dirname(os.path.abspath(a.out))
    t0 = time.time()
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    prim = primary_rows(df)
    prim_blocks = make_blocks(prim.cutoff_ts.to_numpy(), prim.match_id.to_numpy())
    runs = {}
    want = a.runs.split(",")
    if "primary" in want:
        runs["primary"] = evaluate_run("primary", prim, prim, prim_blocks, outdir, log)
    for plat in ("polymarket", "kalshi"):
        if plat in want:
            d = df[df.platform == plat].reset_index(drop=True)
            runs[plat] = evaluate_run(plat, d, d, make_blocks(d.cutoff_ts.to_numpy(), d.match_id.to_numpy()),
                                      outdir, log)
    if "kalshi_pooled_train" in want:                  # Kalshi rows, primary block schedule, trained on both platforms
        k = df[df.platform == "kalshi"].reset_index(drop=True)
        runs["kalshi_pooled_train"] = evaluate_run("kalshi_pooled_train", df.reset_index(drop=True), k,
                                                   prim_blocks, outdir, log)
    n_cand_p1, n_cand_p2 = len(candidates(P1)), len(candidates(P2))
    n_cand_all = len(candidates(P2, HALF_LIVES))
    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dataset": used,
        "rows": int(len(df)), "primary_rows": int(len(prim)),
        "primary_platform_counts": prim.platform.value_counts().to_dict(),
        "primary_rule_note": "Polymarket's spread at the cutoff is not recorded; taken as one tick (0.01). Kalshi "
                             "spreads are >= 0.01, so the frozen rule keeps Polymarket whenever it is listed.",
        "blocks_primary": [b[0] for b in prim_blocks], "gap_s": GAP_S, "inner_folds": INNER_FOLDS,
        "lambdas": list(LAMS), "configs": CONFIGS, "rules": {k: {"scheme": v[0], "inner_cv": SCHEMES[v[0]], "pool": v[1], "half_lives_days": v[2],
                                                        "how": v[3], "round": v[4],
                                                        "eligible_for_preregistered_claim": v[5] and k == PRIMARY_RULE}
                                                    for k, v in RULES.items()},
        "primary_rule": PRIMARY_RULE,
        "counted_configurations": {
            "candidate_settings_P1": n_cand_p1, "candidate_settings_P2": n_cand_p2,
            "candidate_settings_P2_with_decay": n_cand_all,
            "distinct_candidate_settings_scored_inside_windows": n_cand_all,
            "inner_cv_schemes": len(SCHEMES),
            "selection_rules_reported": len(RULES),
            "selection_rules_round1": sum(1 for v in RULES.values() if v[4] == 1),
            "selection_rules_round2": sum(1 for v in RULES.values() if v[4] == 2),
            "single_config_models_reported": len(P2),
            "total_models_reported_per_run": len(RULES) + len(P2),
            "note": "round 1 (R1-R5, c01-c13) was declared before the first run; round 2 (R6-R13: time decay, wider "
                    "inner CV, pool P0) was declared after round-1 results were seen and is reported in full, but "
                    "only R1 can support the pre-registered claim. R12/R13 use pool P0, chosen with knowledge of "
                    "round-1 evaluation results. No configuration was dropped."},
        "edge_grid": list(EDGE_GRID), "min_inner_bets": MIN_INNER_BETS, "cost": COST, "bootstrap": B_BOOT,
        "runtime_s": None}
    res = {"meta": meta, "runs": runs}
    if "primary" in runs:
        res["VALIDATION_PASS"] = runs["primary"]["validation_pass_primary_rule"]
    meta["runtime_s"] = round(time.time() - t0, 1)
    meta["log"] = lines
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    print("wrote", a.out)
    return res


if __name__ == "__main__":
    main()
