#!/usr/bin/env python3
"""
ml_models/ratings.py - "ratings stack" research models for the CS2 series-winner (moneyline) price.

Research only: nothing here is imported by the server, the prop grader, moneyline.py or any member output.

Model family (every model predicts P(team A wins the series); team A = Polymarket outcome 0 / Kalshi first market):

  market       p = recorded market price at the cutoff (the baseline).
  stack        logit(p) = a*logit(p_mkt) + sum_j b_j*r_j + sum_k d_k*(r_k or m)*modifier_k + c
               r_j = rating logits converted to a SERIES probability for the match's best-of (team Elo, roster Elo,
               last-lineup roster Elo, Glicko, Bradley-Terry with time decay); modifiers = LAN flag, tier (mean
               team Elo), experience (games played), price age. L2 penalty lam*||w - w0||^2/2 with w0 = the market
               (a = 1, everything else 0), so a large lam IS the market. lam chosen by inner time-ordered CV.
  rating       ratings only (no market): logit(p) = sum_j b_j*r_j + c, L2 toward 0 (Elo / Bradley-Terry only model).
  raw          a rating's own probability with nothing fitted (e.g. pure team Elo).
  blend_p      p = (1-w)*p_mkt + w*p_rating   (convex blend in probability; w by inner CV)
  blend_logit  logit(p) = (1-w)*logit(p_mkt) + w*logit(p_rating)
  select_*     per training window, the configuration with the lowest pooled inner-CV log-loss (select_min), or that
               configuration only when it beats the market inside the window by more than one standard error of the
               paired difference, else the market (select_1se).

Bradley-Terry ratings (bt_ratings_asof) are fitted from HLTV MAP results dated STRICTLY BEFORE the feature day
(the same day convention as ml_features.py), exponentially time-decayed (half-life in days), ridge prior toward 0.

Deployability: the fit / predict / Bradley-Terry functions use numpy only and accept any mapping of column name ->
array-like (a pandas DataFrame or a plain dict of lists). pandas is used only by the research protocol (run_protocol,
the CLI) - it is NOT in requirements.txt, so a deployed caller must stay on the numpy-only functions.

CLI (runs the pre-registered rolling-origin protocol on the primary one-row-per-match view and on each platform):
  python -m ml_models.ratings --dataset <dataset.parquet|.pkl> --snapshot <snap_full.pkl> --out results_ratings.json
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import date, timedelta

import numpy as np

# ------------------------------------------------------------------------------------------------ fixed constants
CUTOFF_GAP_DAYS = 1          # a block is predicted by a model trained on rows with cutoff < block start - 1 day
MIN_TRAIN = 1500             # the first block starts once >= 1500 training matches exist
BLOCK_SPLIT_N = 600          # a month with more matches than this is split into two half-month blocks (1-14, 15-end)
N_BOOT = 2000
SEED = 20261012
COST = 0.01                  # ROI: price + 1 cent
EDGE_GRID = (0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
MIN_INNER_BETS = 30          # a threshold needs >= 30 inner bets to be eligible
INNER_FOLDS = 4              # expanding-window inner CV over the last half of the training rows
INNER_START = 0.5
LAMS = (0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000, 3000, 10000)   # 0.1 / 0.3 added in round 2
RATING_LAMS = (0.1, 1, 10, 100)
BLEND_W = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
BLEND_RATING_LAM = 1.0       # the rating sub-model inside a blend (fixed a priori)
BT_HALF_LIVES = (45, 90, 180)
BT_RIDGE = 1.0
BT_WINDOW_HL = 4             # games older than 4 half-lives (weight < 1/16) are dropped
EPS = 1e-6


# ------------------------------------------------------------------------------------------------ small helpers
def logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def sigmoid(z):
    z = np.asarray(z, float)
    return 0.5 * (1 + np.tanh(0.5 * z))


def logloss(p, y):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def p_series(p, bo):
    """Series win probability from map probability p for best-of bo (vectorised; bo not in {3, 5} -> p)."""
    p = np.asarray(p, float)
    bo = np.broadcast_to(np.asarray(bo, float), p.shape)
    q = 1 - p
    s3 = p ** 2 * (1 + 2 * q)
    s5 = p ** 3 * (1 + 3 * q + 6 * q ** 2)
    return np.where(bo == 3, s3, np.where(bo == 5, s5, p))


def elo_map_p(diff):
    return 1.0 / (1.0 + 10.0 ** (-np.asarray(diff, float) / 400.0))


def _col(rows, name, n=None):
    """Column of a DataFrame / dict as a float array (None -> nan)."""
    v = rows[name]
    try:
        return np.asarray(v, float)
    except (TypeError, ValueError):
        return np.array([np.nan if x is None else float(x) for x in v], float)


def _n(rows):
    return len(rows["mkt_p"])


# ------------------------------------------------------------------------------------------------ Bradley-Terry
def games_from_snapshot(snap):
    """Map-level games [(date ordinal, team a, team b, a won)] from an ml_features.Snapshot (forfeits and maps without
    two scores skipped, ties skipped), sorted by date."""
    out = []
    for m in snap.matches.values():
        if len(m["teams"]) != 2 or m.get("forfeited") or not m["maps"]:
            continue
        a, b = m["teams"]
        for _, _, sc in m["maps"]:
            if sc[a] == sc[b]:
                continue
            out.append((m["date"].toordinal(), a, b, 1.0 if sc[a] > sc[b] else 0.0))
    out.sort(key=lambda r: r[0])
    return out


def _bt_fit(ia, ib, y, w, nt, ridge, theta0=None, iters=30):
    """Weighted ridge Bradley-Terry by Newton steps: maximise sum w*loglik(sigmoid(th_a - th_b)) - ridge*||th||^2."""
    th = np.zeros(nt) if theta0 is None else theta0.copy()
    for _ in range(iters):
        p = sigmoid(th[ia] - th[ib])
        r = w * (p - y)
        g = np.bincount(ia, r, nt) - np.bincount(ib, r, nt) + 2 * ridge * th
        h = w * p * (1 - p)
        H = np.zeros((nt, nt))
        np.add.at(H, (ia, ia), h)
        np.add.at(H, (ib, ib), h)
        np.add.at(H, (ia, ib), -h)
        np.add.at(H, (ib, ia), -h)
        H[np.diag_indices(nt)] += 2 * ridge
        step = np.linalg.solve(H, g)
        th -= step
        if np.abs(step).max() < 1e-7:
            break
    return th


class BTRatings:
    """Time-decayed ridge Bradley-Terry team ratings as of a day: only games dated < day are used."""

    def __init__(self, games, half_life, ridge=BT_RIDGE, window_hl=BT_WINDOW_HL):
        self.d = np.array([g[0] for g in games], np.int64)
        self.a = np.array([g[1] for g in games], np.int64)
        self.b = np.array([g[2] for g in games], np.int64)
        self.y = np.array([g[3] for g in games], float)
        self.hl, self.ridge, self.win = float(half_life), float(ridge), int(window_hl * half_life)
        self._cache = {}
        self._last = {}

    def asof(self, day):
        """{team id: theta} from games with date in [day - window, day)."""
        o = day.toordinal() if isinstance(day, date) else int(day)
        if o in self._cache:
            return self._cache[o]
        lo, hi = np.searchsorted(self.d, o - self.win, "left"), np.searchsorted(self.d, o, "left")
        if hi <= lo:
            self._cache[o] = {}
            return {}
        a, b, y = self.a[lo:hi], self.b[lo:hi], self.y[lo:hi]
        w = 0.5 ** ((o - self.d[lo:hi]) / self.hl)
        teams, inv = np.unique(np.concatenate([a, b]), return_inverse=True)
        ia, ib = inv[:len(a)], inv[len(a):]
        th0 = np.array([self._last.get(int(t), 0.0) for t in teams])
        th = _bt_fit(ia, ib, y, w, len(teams), self.ridge, th0)
        res = {int(t): float(v) for t, v in zip(teams, th)}
        self._last.update(res)
        self._cache[o] = res
        return res

    def diff(self, ta, tb, day):
        r = self.asof(day)
        return r.get(int(ta), 0.0) - r.get(int(tb), 0.0)


def add_bt_columns(df, snap, half_lives=BT_HALF_LIVES, ridge=BT_RIDGE):
    """Adds bt_diff_hl{H} (map-logit difference, team A - team B) for each half-life, from the snapshot's games dated
    < feature_day. Research helper (pandas DataFrame in, same frame out)."""
    games = games_from_snapshot(snap)
    days = [date.fromisoformat(str(x)) for x in df["feature_day"]]
    for hl in half_lives:
        bt = BTRatings(games, hl, ridge)
        for d in sorted(set(days)):
            bt.asof(d)
        df[f"bt_diff_hl{hl}"] = [bt.diff(a, b, d) for a, b, d in zip(df["team_a_id"], df["team_b_id"], days)]
    return df


# ------------------------------------------------------------------------------------------------ model inputs
def model_inputs(rows):
    """Rating logits (series level, best-of aware) and modifiers from dataset / live feature rows. numpy only."""
    n = _n(rows)
    bo = _col(rows, "bo")
    bo = np.where(np.isin(bo, (1, 3, 5)), bo, 3)
    out = {"m": logit(_col(rows, "mkt_p")), "bo": bo}
    out["elo"] = logit(_col(rows, "elo_p"))
    relo = _col(rows, "relo_p")
    out["relo"] = np.where(np.isnan(relo), out["elo"], logit(np.where(np.isnan(relo), 0.5, relo)))
    rl = _col(rows, "relo_last_diff")
    rl_l = logit(p_series(elo_map_p(np.nan_to_num(rl)), bo))
    out["relo_last"] = np.where(np.isnan(rl), out["relo"], rl_l)
    out["glicko"] = logit(_col(rows, "glicko_p"))
    for k in (10, 16, 24, 32, 48):
        c = f"elo_diff_k{k}"
        if c in rows:
            out[f"elo_k{k}"] = logit(p_series(elo_map_p(_col(rows, c)), bo))
    for hl in BT_HALF_LIVES:
        c = f"bt_diff_hl{hl}"
        if c in rows:
            out[f"bt{hl}"] = logit(p_series(sigmoid(_col(rows, c)), bo))
    out["lan"] = np.nan_to_num(_col(rows, "lan"))
    out["tier"] = _col(rows, "tier_mean")
    ga, gb = _col(rows, "games_series_a"), _col(rows, "games_series_b")
    out["exp"] = np.log1p(np.nan_to_num(np.minimum(ga, gb)))
    out["age"] = np.log1p(np.clip(np.nan_to_num(_col(rows, "mkt_age_min")), 0, None))
    out["bo1"] = (bo == 1).astype(float)
    out["elo_p_raw"] = _col(rows, "elo_p")
    parts = [out[k] for k in ("elo", "relo", "relo_last", "glicko", "bt45", "bt90", "bt180") if k in out]
    out["ravg"] = np.mean(parts, axis=0)                      # composite rating logit (round 2)
    out["cut"] = _col(rows, "cutoff_ts") if "cutoff_ts" in rows else np.zeros(n)
    for k, v in out.items():
        if len(v) != n:
            raise ValueError(k)
    return out


MODIFIERS = ("lan", "tier", "exp", "age", "bo1")


def _mod_stats(inp):
    return {k: [float(np.nanmean(inp[k])), float(np.nanstd(inp[k]) or 1.0)] for k in MODIFIERS}


def _mod(inp, k, stats):
    mu, sd = stats[k]
    if k in ("lan", "bo1"):
        return np.nan_to_num(inp[k] - mu)
    return np.nan_to_num((inp[k] - mu) / (sd if sd > 0 else 1.0))


# ------------------------------------------------------------------------------------------------ configurations
def _cfg(kind, **kw):
    kw["kind"] = kind
    return kw


# Round 1 (written before any result): the approach as specified.
CONFIGS_R1 = {
    "market": _cfg("market"),
    "recal": _cfg("stack", terms=[], inter=[]),                                    # a*m + c only
    "stack_E": _cfg("stack", terms=["elo"], inter=[]),
    "stack_ER": _cfg("stack", terms=["elo", "relo"], inter=[]),
    "stack_ER_int": _cfg("stack", terms=["elo", "relo"],
                         inter=[("elo", "lan"), ("relo", "lan"), ("elo", "tier"), ("relo", "tier"),
                                ("m", "lan"), ("m", "tier")]),
    "stack_all": _cfg("stack", terms=["elo", "relo", "relo_last", "glicko", "bt90"], inter=[]),
    "elo_raw": _cfg("raw", col="elo_p_raw"),
    "rating_ER": _cfg("rating", terms=["elo", "relo"]),
    "rating_BT": _cfg("rating", terms=["bt90"]),
    "blend_p_ER": _cfg("blend_p", terms=["elo", "relo"]),
    "blend_logit_ER": _cfg("blend_logit", terms=["elo", "relo"]),
}


def config_space(rounds=("r1",)):
    out = dict(CONFIGS_R1)
    if "r2" in rounds:
        out.update(CONFIGS_R2)
    return out


# Round 2 - written after looking ONLY at round-1 inner-CV results inside the FIRST primary training window
# (cutoff < 2026-05-31; no evaluation-block row read): stack_all was best and chose the smallest lam (grid extended
# down to 0.1), interactions helped stack_ER, BT with a 180-day half-life had the best raw log-loss, rating-only models
# were far behind the market. Round 2 adds: interactions on the full stack, a one-coefficient composite rating,
# BT variants, recency-weighted training rows (the market may have become more efficient over time), and
# experience / price-age / best-of-1 interactions.
INT_LT = [("elo", "lan"), ("relo", "lan"), ("elo", "tier"), ("relo", "tier"), ("m", "lan"), ("m", "tier")]
CONFIGS_R2 = {
    "stack_all_int": _cfg("stack", terms=["elo", "relo", "relo_last", "glicko", "bt90"], inter=INT_LT),
    "stack_avg": _cfg("stack", terms=["ravg"], inter=[]),
    "stack_avg_int": _cfg("stack", terms=["ravg"], inter=[("ravg", "lan"), ("ravg", "tier"), ("m", "lan"), ("m", "tier")]),
    "stack_all_w120": _cfg("stack", terms=["elo", "relo", "relo_last", "glicko", "bt90"], inter=[], row_hl=120),
    "stack_avg_w120": _cfg("stack", terms=["ravg"], inter=[], row_hl=120),
    "stack_ERB180": _cfg("stack", terms=["elo", "relo", "bt180"], inter=[]),
    "stack_BT180": _cfg("stack", terms=["bt180"], inter=[]),
    "stack_allBT": _cfg("stack", terms=["elo", "relo", "relo_last", "glicko", "bt45", "bt90", "bt180"], inter=[]),
    "stack_ER_exp": _cfg("stack", terms=["elo", "relo"], inter=[("elo", "exp"), ("relo", "exp"), ("m", "exp")]),
    "stack_ER_age": _cfg("stack", terms=["elo", "relo"], inter=[("elo", "age"), ("relo", "age"), ("m", "age")]),
    "stack_ER_bo1": _cfg("stack", terms=["elo", "relo"], inter=[("elo", "bo1"), ("relo", "bo1"), ("m", "bo1")]),
    "stack_Kgrid": _cfg("stack", terms=["elo_k10", "elo_k16", "elo_k24", "elo_k32", "elo_k48"], inter=[]),
    "rating_all": _cfg("rating", terms=["elo", "relo", "relo_last", "glicko", "bt45", "bt90", "bt180"]),
    "blend_p_all": _cfg("blend_p", terms=["elo", "relo", "relo_last", "glicko", "bt45", "bt90", "bt180"]),
    "blend_logit_all": _cfg("blend_logit", terms=["elo", "relo", "relo_last", "glicko", "bt45", "bt90", "bt180"]),
}


# derived predictions: the two per-window selectors (pre-registered; select_min = PRIMARY) and, added in round 3 AFTER
# the round-1/2 out-of-sample results were seen (post hoc, never primary), the equal-weight average of every stack.
DERIVED = ["select_min", "select_1se", "ens_stack"]


def param_grid(cfg):
    k = cfg["kind"]
    if k == "stack":
        return LAMS
    if k == "rating":
        return RATING_LAMS
    if k in ("blend_p", "blend_logit"):
        return BLEND_W
    return (None,)


# ------------------------------------------------------------------------------------------------ fitting (numpy)
def _design(inp, cfg, stats):
    """Design matrix, prior vector w0 and column names. Last column = intercept."""
    if cfg["kind"] == "stack":
        cols, names = [inp["m"]], ["m"]
    else:
        cols, names = [], []
    for t in cfg["terms"]:
        cols.append(inp[t])
        names.append(t)
    for t, mod in cfg.get("inter", []):
        cols.append(inp[t] * _mod(inp, mod, stats))
        names.append(f"{t}*{mod}")
    n = len(inp["m"])
    cols.append(np.ones(n))
    names.append("1")
    X = np.column_stack(cols)
    w0 = np.zeros(X.shape[1])
    if cfg["kind"] == "stack":
        w0[0] = 1.0
    return X, w0, names


def _newton(X, y, w0, lam, iters=100, sw=None):
    w = w0.copy()
    P = np.full(len(w0), float(lam))
    sw = np.ones(len(y)) if sw is None else sw
    for _ in range(iters):
        p = sigmoid(X @ w)
        g = X.T @ (sw * (p - y)) + P * (w - w0)
        H = (X * (sw * p * (1 - p))[:, None]).T @ X + np.diag(P) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    return w


def fit(rows, y, cfg, param, inp=None):
    """Fit one configuration with one parameter value on training rows. Returns a JSON-serialisable model dict."""
    inp = model_inputs(rows) if inp is None else inp
    y = np.asarray(y, float)
    stats = _mod_stats(inp)
    k = cfg["kind"]
    model = {"cfg": cfg, "param": param, "stats": stats}
    if k in ("market", "raw"):
        return model
    if k in ("stack", "rating"):
        X, w0, names = _design(inp, cfg, stats)
        sw = None
        if cfg.get("row_hl"):                                 # recency weights relative to the newest training row
            age = (inp["cut"].max() - inp["cut"]) / 86400.0
            sw = 0.5 ** (age / float(cfg["row_hl"]))
            sw = sw * len(sw) / sw.sum()
        w = _newton(X, y, w0, param, sw=sw)
        model.update(w=w.tolist(), names=names)
        return model
    if k in ("blend_p", "blend_logit"):
        sub = _cfg("rating", terms=cfg["terms"])
        X, w0, names = _design(inp, sub, stats)
        w = _newton(X, y, w0, BLEND_RATING_LAM)
        model.update(w=w.tolist(), names=names, sub=sub)
        return model
    raise ValueError(k)


def predict(model, rows, inp=None):
    """P(team A wins) for rows (numpy only)."""
    inp = model_inputs(rows) if inp is None else inp
    cfg, k = model["cfg"], model["cfg"]["kind"]
    pm = sigmoid(inp["m"])
    if k == "market":
        return pm
    if k == "raw":
        return np.clip(inp[cfg["col"]], EPS, 1 - EPS)
    if k in ("stack", "rating"):
        X, _, _ = _design(inp, cfg, model["stats"])
        return sigmoid(X @ np.asarray(model["w"]))
    X, _, _ = _design(inp, model["sub"], model["stats"])
    pr = sigmoid(X @ np.asarray(model["w"]))
    w = float(model["param"])
    if k == "blend_p":
        return (1 - w) * pm + w * pr
    return sigmoid((1 - w) * inp["m"] + w * logit(pr))


def _subset(inp, idx):
    return {k: v[idx] for k, v in inp.items()}


# ------------------------------------------------------------------------------------------------ inner CV
def inner_folds(cut_ts, start=INNER_START, k=INNER_FOLDS, gap_days=CUTOFF_GAP_DAYS):
    """Expanding-window folds inside a training window (rows sorted by cutoff): validation = k consecutive slices of
    the last (1 - start) share of rows; each fold trains on rows with cutoff < validation start - gap."""
    order = np.argsort(cut_ts, kind="stable")
    n = len(order)
    edges = [int(n * (start + (1 - start) * i / k)) for i in range(k + 1)]
    out = []
    for i in range(k):
        val = order[edges[i]:edges[i + 1]]
        if len(val) < 20:
            continue
        t0 = cut_ts[val].min()
        tr = np.where(cut_ts < t0 - gap_days * 86400)[0]
        if len(tr) < 100:
            continue
        out.append((tr, val))
    return out


def inner_cv(inp, y, cut_ts, configs, folds=None):
    """For every configuration and parameter value, out-of-fold predictions on the inner validation rows.
    Returns {name: {"best": param, "loss": {param: mean loss}, "oof": preds at best param}}, the validation index."""
    folds = inner_folds(cut_ts) if folds is None else folds
    vidx = np.concatenate([v for _, v in folds])
    res = {}
    for name, cfg in configs.items():
        preds = {pp: [] for pp in param_grid(cfg)}
        for tr, val in folds:
            itr, iva = _subset(inp, tr), _subset(inp, val)
            if cfg["kind"] in ("blend_p", "blend_logit"):
                base = fit(None, y[tr], dict(cfg, kind="blend_p"), 0.0, inp=itr)   # one rating fit per fold
                for pp in preds:
                    m = dict(base, cfg=cfg, param=pp)
                    preds[pp].append(predict(m, None, inp=iva))
            else:
                for pp in preds:
                    preds[pp].append(predict(fit(None, y[tr], cfg, pp, inp=itr), None, inp=iva))
        preds = {pp: np.concatenate(v) for pp, v in preds.items()}
        loss = {pp: float(logloss(v, y[vidx]).mean()) for pp, v in preds.items()}
        # ties -> the parameter closest to the market (largest lam / smallest blend weight)
        order = list(preds)
        if cfg["kind"] in ("stack", "rating"):
            order = order[::-1]
        best = min(order, key=lambda pp: loss[pp])
        res[name] = {"best": best, "loss": loss, "oof": preds[best], "best_loss": loss[best]}
    return res, vidx


def select(res, y_val, market="market"):
    """select_min = lowest inner loss; select_1se = that config only if its paired inner gain over the market
    exceeds one standard error, else the market."""
    best = min(res, key=lambda k: (res[k]["best_loss"], k != market))
    d = logloss(res[market]["oof"], y_val) - logloss(res[best]["oof"], y_val)
    se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("inf")
    return {"select_min": best, "select_1se": best if (best != market and d.mean() > se) else market,
            "inner_gain_best": float(d.mean()), "inner_se_best": se}


def choose_edge(p, price, y):
    """Edge threshold with the best ROI on inner out-of-fold predictions (>= MIN_INNER_BETS bets, ROI > 0) or None."""
    best, best_roi = None, 0.0
    table = {}
    for e in EDGE_GRID:
        r = bet_returns(p, price, y, e)
        if len(r) >= MIN_INNER_BETS:
            table[e] = (len(r), float(np.mean(r)))
            if np.mean(r) > best_roi:
                best, best_roi = e, float(np.mean(r))
        else:
            table[e] = (len(r), None)
    return best, table


def bet_returns(p, price, y, edge, cost=COST, idx=False):
    """1 unit on A when p >= price + cost + edge; on B when (1-p) >= (1-price) + cost + edge; paid at price + cost."""
    p, price, y = np.asarray(p, float), np.asarray(price, float), np.asarray(y, float)
    ca, cb = price + cost, 1 - price + cost
    ba = (p - ca >= edge - 1e-12) & (ca < 1)
    bb = ((1 - p) - cb >= edge - 1e-12) & (cb < 1) & ~ba
    r = np.where(ba, np.where(y == 1, (1 - ca) / ca, -1.0), np.where(bb, np.where(y == 0, (1 - cb) / cb, -1.0), np.nan))
    m = ~np.isnan(r)
    return (r[m], np.where(m)[0]) if idx else r[m]


# ------------------------------------------------------------------------------------------------ window model
def fit_window(rows, y, cut_ts, configs, inp=None):
    """Everything a training window decides: per-config parameter (inner CV), the selections, the edge thresholds,
    and the final per-config models refitted on the whole window."""
    inp = model_inputs(rows) if inp is None else inp
    y = np.asarray(y, float)
    cut_ts = np.asarray(cut_ts, float)
    res, vidx = inner_cv(inp, y, cut_ts, configs)
    sel = select(res, y[vidx])
    price_v = sigmoid(inp["m"][vidx])
    models, edges = {}, {}
    for name, cfg in configs.items():
        models[name] = fit(None, y, cfg, res[name]["best"], inp=inp)
        edges[name] = choose_edge(res[name]["oof"], price_v, y[vidx])
    stacks = [k for k, c in configs.items() if c["kind"] == "stack"]
    if stacks:                                               # round 3 (post hoc): equal-weight logit average
        edges["ens_stack"] = choose_edge(sigmoid(np.mean([logit(res[k]["oof"]) for k in stacks], 0)), price_v, y[vidx])
    return {"models": models, "inner": {k: {"best": v["best"], "best_loss": v["best_loss"], "loss": v["loss"]}
                                        for k, v in res.items()},
            "select": sel, "edges": edges, "n_inner_val": int(len(vidx)),
            "inner_market_loss": res["market"]["best_loss"]}


def predict_window(win, rows, inp=None):
    inp = model_inputs(rows) if inp is None else inp
    out = {name: predict(m, None, inp=inp) for name, m in win["models"].items()}
    stacks = [k for k, m in win["models"].items() if m["cfg"]["kind"] == "stack"]
    if stacks:
        out["ens_stack"] = sigmoid(np.mean([logit(out[k]) for k in stacks], 0))
    for s in ("select_min", "select_1se"):
        out[s] = out[win["select"][s]]
    return out


# ------------------------------------------------------------------------------------------------ evaluation helpers
def boot_mean_ci(x, groups, n=N_BOOT, seed=SEED):
    """Match-grouped bootstrap 95% CI of a mean (ratio of sums over resampled groups)."""
    import pandas as pd
    s = pd.Series(np.asarray(x, float)).groupby(np.asarray(groups)).agg(["sum", "count"])
    a, c = s["sum"].to_numpy(), s["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(a), size=(n, len(a)))
    st = a[idx].sum(1) / c[idx].sum(1)
    return [float(np.percentile(st, 2.5)), float(np.percentile(st, 97.5))]


def calib_fit(p, y):
    """Calibration slope / intercept: logistic regression of y on logit(p)."""
    X = np.column_stack([logit(p), np.ones(len(p))])
    w = _newton(X, np.asarray(y, float), np.array([1.0, 0.0]), 1e-9)
    return float(w[0]), float(w[1])


def calib_ci(p, y, groups, n=N_BOOT, seed=SEED):
    g = np.asarray(groups)
    ug, inv = np.unique(g, return_inverse=True)
    members = [np.where(inv == i)[0] for i in range(len(ug))]
    rng = np.random.default_rng(seed + 1)
    sl, ic = [], []
    p, y = np.asarray(p, float), np.asarray(y, float)
    for _ in range(n):
        pick = rng.integers(0, len(ug), len(ug))
        idx = np.concatenate([members[i] for i in pick])
        s, c = calib_fit(p[idx], y[idx])
        sl.append(s)
        ic.append(c)
    return [float(np.percentile(sl, 2.5)), float(np.percentile(sl, 97.5))], \
        [float(np.percentile(ic, 2.5)), float(np.percentile(ic, 97.5))]


def calib_table(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    out = []
    for lo in np.arange(0, 1, 0.1):
        m = (p >= lo) & (p < lo + 0.1 + (1e-9 if lo >= 0.9 else 0))
        if m.sum():
            out.append({"bin": f"{lo:.1f}-{lo + 0.1:.1f}", "n": int(m.sum()), "mean_p": round(float(p[m].mean()), 4),
                        "observed": round(float(y[m].mean()), 4)})
    return out


# ------------------------------------------------------------------------------------------------ protocol (pandas)
def primary_view(df):
    """One row per match (frozen baseline rule): the platform with the smaller spread at the cutoff when both exist,
    else the one available; ties -> Polymarket. Polymarket's spread at the cutoff is NOT recorded in the dataset
    (its price history is a mid/last-trade series), so it is taken at its minimum tick (1 cent) - Kalshi's quoted
    spread is never below 1 cent, so every two-platform match is a tie -> Polymarket."""
    pm = df[df.platform == "polymarket"]
    k = df[(df.platform == "kalshi") & ~df.match_id.isin(pm.match_id)]
    import pandas as pd
    return pd.concat([pm, k]).reset_index(drop=True)


def make_blocks(d):
    """Evaluation blocks over the rows' cutoffs: calendar months; a month with > BLOCK_SPLIT_N matches -> two blocks
    (days 1-14, 15-end). Keeps the blocks from the first one with >= MIN_TRAIN training matches."""
    import pandas as pd
    cut = pd.to_datetime(d.cutoff_ts, unit="s", utc=True)
    months = sorted(cut.dt.strftime("%Y-%m").unique())
    blocks = []
    for mo in months:
        s = pd.Timestamp(mo + "-01", tz="UTC")
        e = s + pd.offsets.MonthBegin(1)
        nm = d.match_id[(cut >= s) & (cut < e)].nunique()
        if nm > BLOCK_SPLIT_N:
            mid = s + pd.Timedelta(days=14)
            blocks += [(f"{mo}a", s, mid), (f"{mo}b", mid, e)]
        else:
            blocks.append((mo, s, e))
    out = []
    for name, s, e in blocks:
        ntr = d.match_id[cut < s - pd.Timedelta(days=CUTOFF_GAP_DAYS)].nunique()
        if ntr >= MIN_TRAIN or out:
            out.append((name, s, e))
    return out


def run_protocol(df, configs, label, log=print, eval_df=None):
    """Rolling-origin protocol. eval_df (optional): score THESE rows in each block instead (e.g. Kalshi rows with the
    models fitted on the primary view's training window); blocks and training rows always come from df."""
    import pandas as pd
    d = df.sort_values("cutoff_ts").reset_index(drop=True)
    cut = pd.to_datetime(d.cutoff_ts, unit="s", utc=True)
    inp_all = model_inputs(d)
    y_all = d.y.to_numpy(float)
    blocks = make_blocks(d)
    ev = d if eval_df is None else eval_df.sort_values("cutoff_ts").reset_index(drop=True)
    ecut = pd.to_datetime(ev.cutoff_ts, unit="s", utc=True)
    inp_ev = model_inputs(ev)
    y_ev = ev.y.to_numpy(float)
    oos, per, windows = [], [], []
    for name, s, e in blocks:
        tr = np.where(cut < s - pd.Timedelta(days=CUTOFF_GAP_DAYS))[0]
        te = np.where((ecut >= s) & (ecut < e))[0]
        if not len(te):
            continue
        t0 = time.time()
        win = fit_window(None, y_all[tr], d.cutoff_ts.to_numpy(float)[tr], configs, inp=_subset(inp_all, tr))
        assert not set(ev.match_id.to_numpy()[te]) & set(d.match_id.to_numpy()[tr])   # no match in train and block
        preds = predict_window(win, None, inp=_subset(inp_ev, te))
        o = pd.DataFrame({"match_id": ev.match_id.to_numpy()[te], "y": y_ev[te], "mkt_p": ev.mkt_p.to_numpy()[te],
                          "platform": ev.platform.to_numpy()[te], "block": name})
        for k, v in preds.items():
            o["p_" + k] = v
        # edge thresholds (chosen inside the window) -> bets in the block
        for k in list(configs) + DERIVED:
            src = win["select"][k] if k.startswith("select_") else k
            o["edge_" + k] = win["edges"][src][0] if win["edges"][src][0] is not None else np.nan
        oos.append(o)
        lm = logloss(o.mkt_p, o.y)
        row = {"block": name, "start": str(s.date()), "end_excl": str(e.date()), "n_train": int(len(tr)),
               "n": int(len(te)), "ll_market": round(float(lm.mean()), 5),
               "select_min": win["select"]["select_min"], "select_1se": win["select"]["select_1se"],
               "inner_gain_best": round(win["select"]["inner_gain_best"], 5),
               "inner_se_best": round(win["select"]["inner_se_best"], 5),
               "params": {k: v["best"] for k, v in win["inner"].items()},
               "inner_loss": {k: round(v["best_loss"], 5) for k, v in win["inner"].items()},
               "edge": {k: win["edges"][k][0] for k in configs},
               "coef": {k: dict(zip(m.get("names", []), [round(x, 4) for x in m.get("w", [])]))
                        for k, m in win["models"].items() if "w" in m}}
        for k in list(configs) + DERIVED:
            lk = logloss(o["p_" + k], o.y)
            row[f"gain_{k}"] = round(float((lm - lk).mean()), 5)
        row["gain_select_min_ci"] = [round(x, 5) for x in boot_mean_ci(lm - logloss(o.p_select_min, o.y), o.match_id)]
        per.append(row)
        windows.append(win)
        log(f"[{label}] {name} n_train={len(tr)} n={len(te)} sel={row['select_min']}/{row['select_1se']} "
            f"gain_sel={row['gain_select_min']:+.4f} {row['gain_select_min_ci']} ({time.time() - t0:.0f}s)")
    o = pd.concat(oos).reset_index(drop=True)
    return o, per, windows


def summarize(o, per, configs, primary="select_min"):
    lm = logloss(o.mkt_p, o.y)
    g = o.match_id.to_numpy()
    out = {"n": int(len(o)), "n_matches": int(o.match_id.nunique()), "n_blocks": len(per),
           "ll_market": round(float(lm.mean()), 5), "ll_market_ci": [round(x, 5) for x in boot_mean_ci(lm, g)],
           "brier_market": round(float(((o.mkt_p - o.y) ** 2).mean()), 5)}
    s, c = calib_fit(o.mkt_p.to_numpy(), o.y.to_numpy())
    out["calib_market"] = {"slope": round(s, 4), "intercept": round(c, 4)}
    models = {}
    for k in list(configs) + DERIVED:
        p = o["p_" + k].to_numpy()
        lk = logloss(p, o.y)
        d = lm - lk
        pos = sum(1 for r in per if r[f"gain_{k}"] > 0)
        sl, ic = calib_fit(p, o.y.to_numpy())
        m = {"ll": round(float(lk.mean()), 5), "gain": round(float(d.mean()), 5),
             "gain_ci": [round(x, 5) for x in boot_mean_ci(d, g)],
             "brier": round(float(((p - o.y) ** 2).mean()), 5),
             "brier_gain": round(float(((o.mkt_p - o.y) ** 2 - (p - o.y) ** 2).mean()), 5),
             "blocks_positive": pos, "blocks_positive_share": round(pos / len(per), 3),
             "calib_slope": round(sl, 4), "calib_intercept": round(ic, 4),
             "roi_inner_threshold": roi_summary(o, k)}
        if k == primary or k == "select_1se":
            sci, ici = calib_ci(p, o.y.to_numpy(), g)
            m["calib_slope_ci"] = [round(x, 4) for x in sci]
            m["calib_intercept_ci"] = [round(x, 4) for x in ici]
            m["calibration_table"] = calib_table(p, o.y.to_numpy())
            m["gain_ci_ll_model"] = [round(x, 5) for x in boot_mean_ci(lk, g)]
        m["validation_pass"] = bool(m["gain"] > 0 and m["gain_ci"][0] > 0 and pos / len(per) >= 0.6
                                    and 0.8 <= sl <= 1.2)
        models[k] = m
    out["models"] = models
    out["primary_model"] = primary
    out["validation_pass"] = models[primary]["validation_pass"]
    out["calibration_table_market"] = calib_table(o.mkt_p.to_numpy(), o.y.to_numpy())
    return out


def roi_summary(o, k):
    """Bets in each block at the edge threshold chosen inside that block's training window (none if no threshold)."""
    rs, gs = [], []
    for b, ob in o.groupby("block", sort=False):
        e = ob["edge_" + k].iloc[0]
        if not np.isfinite(e):
            continue
        r, ix = bet_returns(ob["p_" + k].to_numpy(), ob.mkt_p.to_numpy(), ob.y.to_numpy(), e, idx=True)
        rs.append(r)
        gs.append(ob.match_id.to_numpy()[ix])
    if not rs or not sum(len(r) for r in rs):
        return {"bets": 0}
    r, g = np.concatenate(rs), np.concatenate(gs)
    out = {"bets": int(len(r)), "won": int((r > 0).sum()), "net_units": round(float(r.sum()), 2),
           "roi": round(float(r.mean()), 4)}
    if len(r) >= 20:
        out["roi_ci"] = [round(x, 4) for x in boot_mean_ci(r, g)]
    return out


# ------------------------------------------------------------------------------------------------ CLI
def load_dataset(path):
    import pandas as pd
    if path.endswith(".parquet"):
        if os.path.exists(path):
            try:
                return pd.read_parquet(path), path
            except ImportError:
                pass
        alt = path[:-len(".parquet")] + ".pkl"
        return pd.read_pickle(alt), alt
    return pd.read_pickle(path), path


def attach_bt(df, snapshot_path, cache_path):
    import pickle
    import pandas as pd
    cols = [f"bt_diff_hl{h}" for h in BT_HALF_LIVES]
    key = df[["match_id", "platform", "feature_day", "team_a_id", "team_b_id"]].astype(str)
    if cache_path and os.path.exists(cache_path):
        c = pd.read_pickle(cache_path)
        if len(c) == len(df) and (c[key.columns].astype(str).to_numpy() == key.to_numpy()).all():
            for col in cols:
                df[col] = c[col].to_numpy()
            return df
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    snap = pickle.load(open(snapshot_path, "rb"))
    t = time.time()
    add_bt_columns(df, snap)
    print(f"bradley-terry columns computed in {time.time() - t:.0f}s")
    if cache_path:
        df[list(key.columns) + cols].to_pickle(cache_path)
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--snapshot", required=True, help="pickled ml_features.Snapshot of the HLTV database")
    ap.add_argument("--bt-cache", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", default="r1,r2")
    ap.add_argument("--views", default="primary,polymarket,kalshi,kalshi_by_primary_models")
    ap.add_argument("--dev-json", nargs="*", default=[], help="inner-CV development records to embed (rounds 1-2)")
    a = ap.parse_args(argv)
    df, used = load_dataset(a.dataset)
    df = attach_bt(df, a.snapshot, a.bt_cache)
    configs = config_space(tuple(a.rounds.split(",")))
    out = {"dataset": used, "n_rows": int(len(df)), "configs": configs, "n_configs": len(configs) + 2,
           "constants": {"LAMS": LAMS, "RATING_LAMS": RATING_LAMS, "BLEND_W": BLEND_W, "EDGE_GRID": EDGE_GRID,
                         "INNER_FOLDS": INNER_FOLDS, "INNER_START": INNER_START, "BT_HALF_LIVES": BT_HALF_LIVES,
                         "BT_RIDGE": BT_RIDGE, "MIN_TRAIN": MIN_TRAIN, "BLOCK_SPLIT_N": BLOCK_SPLIT_N,
                         "COST": COST, "N_BOOT": N_BOOT, "SEED": SEED},
           "views": {},
           "configurations_counted": {"round1": list(CONFIGS_R1), "round2": list(CONFIGS_R2),
                                      "round3_post_hoc": ["ens_stack"], "selectors": ["select_min", "select_1se"],
                                      "total": len(configs) + len(DERIVED),
                                      "parameter_values_per_window": {k: len(param_grid(c)) for k, c in configs.items()}},
           "primary_model": "select_min (per-window inner-CV selection over every round-1/2 configuration)",
           "development": [json.load(open(p)) for p in a.dev_json]}
    views = {"primary": primary_view(df), "polymarket": df[df.platform == "polymarket"],
             "kalshi": df[df.platform == "kalshi"]}
    for v in a.views.split(","):
        if v == "kalshi_by_primary_models":
            o, per, _ = run_protocol(views["primary"], configs, v, eval_df=views["kalshi"])
        else:
            o, per, _ = run_protocol(views[v], configs, v)
        s = summarize(o, per, configs)
        s["blocks"] = per
        hb = max(s["models"], key=lambda k: s["models"][k]["gain"] if k != "market" else -9)
        s["hindsight_best_config"] = {"name": hb, "gain": s["models"][hb]["gain"], "gain_ci": s["models"][hb]["gain_ci"],
                                      "note": "picked AFTER seeing the blocks - descriptive only, not a valid test"}
        out["views"][v] = s
        o.to_pickle(os.path.splitext(a.out)[0] + f"_oos_{v}.pkl")
        pm = s["models"]["select_min"]
        print(f"== {v}: n={s['n']} blocks={s['n_blocks']} market={s['ll_market']} model={pm['ll']} "
              f"gain={pm['gain']:+.5f} {pm['gain_ci']} pos={pm['blocks_positive']}/{s['n_blocks']} "
              f"slope={pm['calib_slope']} PASS={s['validation_pass']}")
    json.dump(out, open(a.out, "w"), indent=1, default=str)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
