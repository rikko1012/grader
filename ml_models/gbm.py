#!/usr/bin/env python3
"""
ml_models/gbm.py - gradient boosting on the RESIDUAL of the market price (CS2 series-winner / moneyline research).

Model.  LightGBM binary objective with init_score = logit(market price at the cutoff): the trees learn only a
        correction to the market's log-odds.  Strong regularisation (num_leaves <= 15, min_data_in_leaf >= 50,
        learning rate <= 0.05, L2 on leaves, feature / row subsampling), monotone +1 constraints on every
        Elo-type difference (team Elo, roster Elo, last-lineup roster Elo, Glicko, and each rating's log-odds minus
        the market's log-odds).  Optional A/B flip augmentation: every training match is added a second time with
        the teams swapped (diffs negated, a/b columns swapped, y -> 1-y) and predictions are symmetrised
        (z = market logit + (r(x) - r(flip x)) / 2), so the correction cannot depend on which team a platform lists
        first.

Selection.  Inside EACH training window only: an expanding, time-ordered inner split (3 validation segments =
        the last 45% of the window by cutoff, each inner training set = window rows with cutoff < segment start
        - 1 day).  For every configuration of the round's grid the validation log-loss is traced per boosting
        iteration; the (configuration, number of iterations) with the lowest pooled inner log-loss is refitted on
        the whole window.  Iteration 0 = the market price itself, so "no correction" is always a candidate
        (= early stopping on the inner split).  ROI edge thresholds are chosen on the same inner predictions.
        Evaluation blocks are never used for any choice.

Protocol (pre-registered, see run_protocol): rolling origin over calendar-month blocks of the cutoff (2-week halves
        when a block-month has > 600 matches), a block's model trained only on rows with cutoff < block start
        - 1 day, first block once >= 1500 training matches exist; pooled out-of-sample log-loss vs the market with
        a match-grouped bootstrap (2000) CI; Brier; calibration slope / intercept; per-block gain sign; ROI at the
        recorded price + 1 cent for in-window thresholds.  VALIDATION PASS = pooled gain > 0 with the 95% CI
        excluding 0, gain > 0 in >= 60% of blocks, calibration slope in [0.8, 1.2].

Deployment.  fit() needs lightgbm (research).  A fitted model is exported with export_model() to plain JSON trees
        and predict_exported() scores it with numpy only (no lightgbm at serve time; checked against lightgbm's
        own predict in the CLI run and tests).

CLI:    python -m ml_models.gbm --round 1            # run round 1 (all analyses) and merge into results_gbm.json
        python -m ml_models.gbm --summary            # print the stored results
"""
import argparse
import json
import math
import os
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

RESEARCH = os.environ.get(
    "ML_RESEARCH_DIR",
    "C:/Users/tikic/AppData/Local/Temp/claude/C--Users-tikic-OneDrive-Documents-cs2-scout/"
    "6847ff60-df65-4b0c-851b-1b79f55ef3bf/scratchpad/ml_research")
RESULTS = os.path.join(RESEARCH, "results_gbm.json")

# ------------------------------------------------------------------------------------------ protocol constants (frozen)
DAY = 86400
MIN_TRAIN_MATCHES = 1500
SPLIT_MONTH_ABOVE = 600          # a month with > 600 matches is split into two halves (1st-14th, 15th-end)
EMBARGO_S = DAY                  # training rows: cutoff < block start - 1 day
BOOT = 2000
SEED = 20261012
COST = 0.01                      # ROI: recorded price + 1 cent
PASS_SHARE = 0.60
PASS_SLOPE = (0.8, 1.2)

# inner selection (inside each training window only)
INNER_VAL_FRAC = 0.45            # last 45% of the window by cutoff = 3 validation segments of 15%
INNER_FOLDS = 3
INNER_MIN_TRAIN = 300
ROI_GRID = (0.02, 0.03, 0.04, 0.05, 0.07, 0.10)
ROI_MIN_BETS = 15

# ---------------------------------------------------------------------------------------------------------- features
# flip rule per derived feature when teams A and B are swapped: "neg" (oriented to team A), "sym" (unchanged),
# or the name of the partner column it swaps with.
MKT = ["mkt_logit", "mkt_age_min", "log_npts48", "bo", "lan", "hi48", "lo48", "mkt_mv6", "mkt_mv24", "xgap",
       "k_spread", "log_k_vol48"]
RAT = ["dis_elo", "dis_relo", "dis_glicko", "elo_diff", "relo_diff", "relo_last_diff", "glicko_diff",
       "glicko_rd_a", "glicko_rd_b", "log_games_a", "log_games_b", "tier_mean"]
CTX = ["form5_adj_diff", "form10_adj_diff", "form10_diff", "mappool_vs", "lan_gap_diff", "lan_gap_x_lan",
       "stab30_diff", "stab90_diff", "standin_diff", "lastlu_lt5_diff", "rest_days_diff", "m7_diff", "mle_e_h2h",
       "home_diff"]
FLIP = {"mkt_logit": "neg", "mkt_age_min": "sym", "log_npts48": "sym", "bo": "sym", "lan": "sym",
        "hi48": "lo48", "lo48": "hi48", "mkt_mv6": "neg", "mkt_mv24": "neg", "xgap": "neg", "k_spread": "sym",
        "log_k_vol48": "sym",
        "dis_elo": "neg", "dis_relo": "neg", "dis_glicko": "neg", "elo_diff": "neg", "relo_diff": "neg",
        "relo_last_diff": "neg", "glicko_diff": "neg", "glicko_rd_a": "glicko_rd_b", "glicko_rd_b": "glicko_rd_a",
        "log_games_a": "log_games_b", "log_games_b": "log_games_a", "tier_mean": "sym"}
FLIP.update({c: "neg" for c in CTX})
MONOTONE_UP = {"dis_elo", "dis_relo", "dis_glicko", "elo_diff", "relo_diff", "relo_last_diff", "glicko_diff"}

FSETS = {
    "mkt": MKT,                                              # market structure only (longshot bias, moves, other venue)
    "rat": ["mkt_logit", "bo", "lan"] + RAT,                 # HLTV ratings vs the price
    "mkt_rat": MKT + RAT,
    "all": MKT + RAT + CTX,
}
_COMMON = dict(objective="binary", feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, max_bin=63,
               min_sum_hessian_in_leaf=1.0, verbose=-1, deterministic=True, force_row_wise=True, num_threads=4)
REGS = {
    "r4": dict(num_leaves=4, min_data_in_leaf=100, learning_rate=0.02, lambda_l2=20.0, rounds=500),
    "r8": dict(num_leaves=8, min_data_in_leaf=60, learning_rate=0.03, lambda_l2=10.0, rounds=400),
    "r15": dict(num_leaves=15, min_data_in_leaf=50, learning_rate=0.05, lambda_l2=5.0, rounds=250),
}


def _grid(fsets, regs, augs):
    return [dict(name=f"{f}|{r}|{'aug' if a else 'noaug'}", fset=f, reg=r, aug=a, extra={})
            for f in fsets for r in regs for a in augs]


# Every configuration ever tried is listed here, per round, BEFORE that round was run (no silent selection).
ROUNDS = {
    1: dict(desc="full grid: 4 feature sets x 3 regularisation levels x flip augmentation on/off; "
                 "selection = lowest pooled inner log-loss over (config, iterations incl. 0 = market)",
            configs=_grid(["mkt", "rat", "mkt_rat", "all"], ["r4", "r8", "r15"], [False, True]),
            rule="min"),
    2: dict(desc="round-1 grid, selection rule 'sig': keep the inner-best choice only if its inner gain's 90% "
                 "match-grouped bootstrap lower bound is > 0, else the market (round 1 picked noise: inner gains "
                 "+0.001..+0.008 turned into OOS losses)",
            configs=_grid(["mkt", "rat", "mkt_rat", "all"], ["r4", "r8", "r15"], [False, True]),
            rule="sig"),
    3: dict(desc="round-1 grid, selection rule 'ens': average the corrections of every config with a positive inner "
                 "gain, shrink factor alpha in {0, .25, .5, .75, 1} chosen on the inner predictions",
            configs=_grid(["mkt", "rat", "mkt_rat", "all"], ["r4", "r8", "r15"], [False, True]),
            rule="ens"),
    4: dict(desc="base = market recalibrated inside the window (y ~ a + b*logit(price), training rows only; the "
                 "price was under-confident in the round-1 output and in the earlier wf_eval bins) + residual trees; "
                 "4 feature sets x r4/r8, flip augmentation on; plus the raw-market residual configs r4 for "
                 "comparison; rule 'min' (the recalibration alone = a candidate at 0 trees)",
            configs=[dict(c, name=c["name"] + "|recal", recal=True)
                     for c in _grid(["mkt", "rat", "mkt_rat", "all"], ["r4", "r8"], [True])]
            + _grid(["mkt", "rat", "mkt_rat", "all"], ["r4"], [True]),
            rule="min"),
    5: dict(desc="as round 4 but with time-decay sample weights (half-life 120 days) on every config and rule 'ens' "
                 "(HLTV coverage and the market change over the year; rating features' inner gains did not carry)",
            configs=[dict(c, name=c["name"] + "|recal|hl120", recal=True, halflife=120)
                     for c in _grid(["mkt", "rat", "mkt_rat", "all"], ["r4", "r8"], [True])]
            + [dict(c, name=c["name"] + "|hl120", halflife=120) for c in _grid(["mkt", "rat", "mkt_rat", "all"], ["r4"], [True])],
            rule="ens"),
    6: dict(desc="EVALUATION-INFORMED (registered after seeing rounds 1-5, where market-structure corrections did "
                 "better in the late blocks than rating corrections): market-structure features only (+ mkt_rat), "
                 "recalibrated base, half-life 120 d, r4/r8/r15, flip augmentation, rule 'ens'. Its result is "
                 "biased upward by that choice and cannot count as a clean pass",
            configs=[dict(c, name=c["name"] + "|recal|hl120", recal=True, halflife=120)
                     for c in _grid(["mkt", "mkt_rat"], ["r4", "r8", "r15"], [True])],
            rule="ens"),
}


def lg(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sig(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def ll(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def derive(df):
    """Derived model inputs from dataset / live feature rows (pandas DataFrame). Missing stays NaN."""
    g = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df else pd.Series(np.nan, index=df.index)  # noqa: E731
    m = pd.Series(lg(g("mkt_p")), index=df.index)
    X = pd.DataFrame(index=df.index)
    X["mkt_logit"] = m
    X["mkt_age_min"] = g("mkt_age_min")
    X["log_npts48"] = np.log1p(g("mkt_npts48"))
    X["bo"] = g("bo")
    X["lan"] = g("lan").astype(float)
    X["hi48"] = pd.Series(lg(g("mkt_max48")), index=df.index).where(g("mkt_max48").notna()) - m
    X["lo48"] = m - pd.Series(lg(g("mkt_min48")), index=df.index).where(g("mkt_min48").notna())
    X["mkt_mv6"] = g("mkt_mv6")
    X["mkt_mv24"] = g("mkt_mv24")
    X["xgap"] = g("xgap")
    X["k_spread"] = g("k_spread")
    X["log_k_vol48"] = np.log1p(g("k_vol48"))
    for name, col in (("dis_elo", "elo_p"), ("dis_relo", "relo_p"), ("dis_glicko", "glicko_p")):
        X[name] = pd.Series(lg(g(col)), index=df.index).where(g(col).notna()) - m
    for c in ("elo_diff", "relo_diff", "relo_last_diff", "glicko_diff", "glicko_rd_a", "glicko_rd_b", "tier_mean"):
        X[c] = g(c)
    X["log_games_a"] = np.log1p(g("games_series_a"))
    X["log_games_b"] = np.log1p(g("games_series_b"))
    for c in CTX:
        X[c] = g(c)
    return X


def flip(X):
    """Teams swapped: same columns, A/B exchanged."""
    out = pd.DataFrame(index=X.index)
    for c in X.columns:
        r = FLIP[c]
        out[c] = -X[c] if r == "neg" else (X[c] if r == "sym" else X[r])
    return out


# ------------------------------------------------------------------------------------------------------- model core
def _params(cfg, feats, seed=SEED):
    p = dict(_COMMON)
    reg = dict(REGS[cfg["reg"]])
    reg.pop("rounds")
    p.update(reg)
    p.update(cfg.get("extra") or {})
    p["seed"] = seed
    p["monotone_constraints"] = [1 if f in MONOTONE_UP else 0 for f in feats]
    assert p["num_leaves"] <= 15 and p["min_data_in_leaf"] >= 50 and p["learning_rate"] <= 0.05
    return p


def _train_xy(X, y, cfg):
    feats = FSETS[cfg["fset"]]
    Xa = X[feats]
    ya = np.asarray(y, dtype=float)
    if cfg["aug"]:
        Xa = pd.concat([Xa, flip(X)[feats]], ignore_index=True)
        ya = np.concatenate([ya, 1 - ya])
    return Xa.to_numpy(dtype=float), ya, feats


def _calib_ab(m, y):
    """In-window logistic recalibration of the market: y ~ a + b * market logit (training rows only)."""
    return calib(sig(m), y)


def base_logit(model, X, flipped=False):
    """Starting log-odds: the market logit, or a + b * market logit when the config recalibrates the market inside
    the training window (a flips sign with the teams so flip-augmented rows stay consistent)."""
    m = X["mkt_logit"].to_numpy(dtype=float)
    a, b = model.get("ab") or (0.0, 1.0)
    return (-a if flipped else a) + b * m


def _weights(cut, cfg):
    hl = cfg.get("halflife")
    if not hl or cut is None:
        return None
    cut = np.asarray(cut, float)
    return 0.5 ** ((cut.max() - cut) / (hl * DAY))


def fit(X, y, cfg, rounds=None, cut=None):
    """Fit one configuration on derived inputs X (DataFrame from derive) and outcomes y (cut = cutoff_ts, used only
    for time-decay weights). Returns a model dict."""
    import lightgbm as lgb
    rounds = REGS[cfg["reg"]]["rounds"] if rounds is None else int(rounds)
    y = np.asarray(y, dtype=float)
    model = dict(cfg=cfg, feats=FSETS[cfg["fset"]], rounds=rounds, booster=None, ab=None)
    if cfg.get("recal"):
        model["ab"] = _calib_ab(X["mkt_logit"].to_numpy(dtype=float), y)
    if rounds <= 0:
        return model
    Xa, ya, feats = _train_xy(X, y, cfg)
    init = base_logit(model, X)
    w = _weights(cut, cfg)
    if cfg["aug"]:
        init = np.concatenate([init, base_logit(model, flip(X), flipped=True)])
        w = None if w is None else np.concatenate([w, w])
    ds = lgb.Dataset(Xa, ya, init_score=init, weight=w, feature_name=feats, free_raw_data=False)
    model["booster"] = lgb.train(_params(cfg, feats), ds, num_boost_round=rounds)
    return model


def _leaf_table(booster):
    """Per-tree leaf values indexed by leaf index (for cumulative per-iteration scores)."""
    dump = booster.dump_model()
    tabs = []
    for t in dump["tree_info"]:
        vals = {}

        def walk(n):
            if "leaf_index" in n:
                vals[n["leaf_index"]] = n["leaf_value"]
            elif "split_index" in n:
                walk(n["left_child"])
                walk(n["right_child"])
            else:                                    # a single-leaf tree
                vals[0] = n.get("leaf_value", 0.0)
        walk(t["tree_structure"])
        tab = np.zeros(max(vals) + 1)
        for k, v in vals.items():
            tab[k] = v
        tabs.append(tab)
    return tabs


def _raw_by_iter(booster, Xn, tabs):
    """n x T matrix of the cumulative correction after 1..T trees."""
    leaves = booster.predict(Xn, pred_leaf=True)
    if leaves.ndim == 1:
        leaves = leaves[:, None]
    contrib = np.column_stack([tabs[t][leaves[:, t]] for t in range(leaves.shape[1])])
    return np.cumsum(contrib, axis=1)


def correction(model, X, by_iter=False):
    """Correction to the market logit (symmetrised when the model was trained with flip augmentation)."""
    n = len(X)
    if model["booster"] is None:
        return np.zeros((n, 1)) if by_iter else np.zeros(n)
    feats = model["feats"]
    Xn = X[feats].to_numpy(dtype=float)
    if by_iter:
        tabs = _leaf_table(model["booster"])
        r = _raw_by_iter(model["booster"], Xn, tabs)
        if model["cfg"]["aug"]:
            r = (r - _raw_by_iter(model["booster"], flip(X)[feats].to_numpy(dtype=float), tabs)) / 2
        return np.column_stack([np.zeros(n), r])     # column k = after k trees (0 = market)
    r = model["booster"].predict(Xn, raw_score=True)
    if model["cfg"]["aug"]:
        r = (r - model["booster"].predict(flip(X)[feats].to_numpy(dtype=float), raw_score=True)) / 2
    return r


def predict(model, X):
    """Probability that team A wins: sigmoid(base log-odds + learned correction)."""
    return sig(base_logit(model, X) + correction(model, X))


def predict_members(members, alpha, X):
    """Ensemble: market logit + alpha * mean over members of (member log-odds - market logit)."""
    m = X["mkt_logit"].to_numpy(dtype=float)
    if not members or alpha == 0:
        return sig(m)
    d = np.mean([lg(predict(mm, X)) - m for mm in members], axis=0)
    return sig(m + alpha * d)


# ---------------------------------------------------------------------------- numpy-only export (deployment path)
def export_model(model):
    """Plain-JSON model: feature names, flip/symmetrise flag and the tree list (no lightgbm needed to score)."""
    out = dict(kind="gbm_residual_v1", cfg=model["cfg"], feats=model["feats"], rounds=model["rounds"], trees=[],
               ab=list(model["ab"]) if model.get("ab") else None)
    if model["booster"] is not None:
        out["trees"] = [t["tree_structure"] for t in model["booster"].dump_model()["tree_info"]]
    return out


def _tree_eval(node, Xn):
    out = np.zeros(len(Xn))
    stack = [(node, np.arange(len(Xn)))]
    while stack:
        n, idx = stack.pop()
        if not len(idx):
            continue
        if "split_index" not in n:
            out[idx] = n.get("leaf_value", 0.0)
            continue
        v = Xn[idx, n["split_feature"]]
        nan = np.isnan(v)
        mt = n.get("missing_type", "None")
        if mt == "None":
            v = np.where(nan, 0.0, v)
            miss = np.zeros(len(v), bool)
        elif mt == "Zero":
            miss = nan | (v == 0)
        else:                                        # "NaN"
            miss = nan
        go_left = np.where(miss, bool(n.get("default_left", True)), v <= n["threshold"])
        stack.append((n["left_child"], idx[go_left]))
        stack.append((n["right_child"], idx[~go_left]))
    return out


def _raw_exported(exp, Xn):
    r = np.zeros(len(Xn))
    for t in exp["trees"]:
        r += _tree_eval(t, Xn)
    return r


def predict_exported(exp, X):
    """numpy-only scoring of an export_model() dict; X = derive(rows)."""
    if exp.get("kind") == "gbm_residual_ensemble_v1":
        m = X["mkt_logit"].to_numpy(dtype=float)
        if not exp["members"] or exp["alpha"] == 0:
            return sig(m)
        d = np.mean([lg(predict_exported(e, X)) - m for e in exp["members"]], axis=0)
        return sig(m + exp["alpha"] * d)
    feats = exp["feats"]
    r = _raw_exported(exp, X[feats].to_numpy(dtype=float))
    if exp["cfg"]["aug"]:
        r = (r - _raw_exported(exp, flip(X)[feats].to_numpy(dtype=float))) / 2
    return sig(base_logit(exp, X) + r)


def export_ensemble(members, alpha):
    return dict(kind="gbm_residual_ensemble_v1", alpha=alpha, members=[export_model(m) for m in members])


# ------------------------------------------------------------------------------------------- inner (in-window) choice
def inner_segments(cut):
    """Indices (into a cutoff-sorted window) of the inner training / validation sets."""
    n = len(cut)
    bounds = [int(round(n * (1 - INNER_VAL_FRAC + INNER_VAL_FRAC * i / INNER_FOLDS))) for i in range(INNER_FOLDS + 1)]
    segs = []
    for i in range(INNER_FOLDS):
        va = np.arange(bounds[i], bounds[i + 1])
        if not len(va):
            continue
        tr = np.where(cut < cut[va[0]] - EMBARGO_S)[0]
        if len(tr) >= INNER_MIN_TRAIN:
            segs.append((tr, va))
    return segs


ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)


def inner_select(X, y, cut, configs, rule="min", groups=None):
    """Choose the model on the inner time split of ONE training window (nothing outside the window is seen).

    rule "min": the (config, iterations) with the lowest pooled inner log-loss; the market itself is always a
                candidate (used when no configuration beats it inside the window).
    rule "sig": as "min", but the choice is kept only if the lower end of a match-grouped bootstrap 90% interval of
                its inner gain over the market is > 0; otherwise the market is used.
    rule "ens": every config with a positive inner gain at its own best iteration enters an equal-weight average of
                corrections (log-odds vs the market); the shrink factor alpha in ALPHAS is chosen on the same inner
                predictions (alpha 0 = market).
    Returns members [(cfg, iters)], alpha, the inner predictions of the choice (for the ROI threshold), the table."""
    order = np.argsort(cut, kind="stable")
    X, y, cut = X.iloc[order].reset_index(drop=True), np.asarray(y, float)[order], np.asarray(cut)[order]
    groups = np.arange(len(y)) if groups is None else np.asarray(groups)[order]
    segs = inner_segments(cut)
    yv = np.concatenate([y[va] for _, va in segs])
    gv = np.concatenate([groups[va] for _, va in segs])
    mv = np.concatenate([X["mkt_logit"].to_numpy()[va] for _, va in segs])
    nval = len(yv)
    llm_rows = ll(sig(mv), yv)
    ll_mkt = float(llm_rows.mean())
    table, cand = [], []
    for cfg in configs:
        zs = []
        for tr, va in segs:
            m = fit(X.iloc[tr], y[tr], cfg, cut=cut[tr])
            Xv = X.iloc[va]
            zs.append(base_logit(m, Xv)[:, None] + correction(m, Xv, by_iter=True))
        Z = np.vstack(zs)                            # nval x (T+1); column 0 = base (market or recalibrated market)
        P = np.clip(sig(Z), 1e-6, 1 - 1e-6)
        curve = -(yv[:, None] * np.log(P) + (1 - yv[:, None]) * np.log(1 - P)).mean(0)
        k = int(np.argmin(curve))
        table.append(dict(cfg=cfg["name"], best_iters=k, inner_ll=round(float(curve[k]), 5),
                          inner_gain=round(ll_mkt - float(curve[k]), 5),
                          inner_ll_full=round(float(curve[-1]), 5)))
        cand.append(dict(cfg=cfg, iters=k, ll=float(curve[k]), z=Z[:, k]))
    out = dict(members=[], alpha=0.0, inner_ll=ll_mkt, inner_ll_market=ll_mkt, inner_n=nval, inner_p=sig(mv),
               inner_y=yv, inner_mkt=sig(mv), table=table, note="")
    pos = [c for c in cand if c["ll"] < ll_mkt - 1e-12]
    if not pos:
        out["note"] = "no configuration beat the market inside the window"
        return out
    if rule in ("min", "sig"):
        b = min(pos, key=lambda c: c["ll"])
        if rule == "sig":
            dif = llm_rows - ll(sig(b["z"]), yv)
            lo = boot_ci(dif, gv, b=1000, q=(5, 95))[0]
            out["inner_gain_lo90"] = lo
            if lo <= 0:
                out["note"] = f"best inner gain not significant (90% lower bound {lo:+.5f})"
                return out
        out.update(members=[(b["cfg"], b["iters"])], alpha=1.0, inner_ll=b["ll"], inner_p=sig(b["z"]))
        return out
    D = np.mean([c["z"] - mv for c in pos], axis=0)    # rule "ens"
    best_a, best_ll = 0.0, ll_mkt
    for a in ALPHAS[1:]:
        v = float(ll(sig(mv + a * D), yv).mean())
        if v < best_ll - 1e-12:
            best_a, best_ll = a, v
    out.update(members=[(c["cfg"], c["iters"]) for c in pos], alpha=best_a, inner_ll=best_ll,
               inner_p=sig(mv + best_a * D), note=f"{len(pos)} configs averaged")
    return out


# ------------------------------------------------------------------------------------------------- metrics / helpers
def boot_ci(vals, groups, seed=SEED, b=BOOT, q=(2.5, 97.5)):
    """Match-grouped bootstrap 95% CI of the mean of `vals` (rows of one match always resampled together)."""
    s = pd.DataFrame({"v": np.asarray(vals, float), "g": np.asarray(groups)}).groupby("g")["v"].agg(["sum", "count"])
    a, c = s["sum"].to_numpy(), s["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(a), size=(b, len(a)))
    st = a[idx].sum(1) / c[idx].sum(1)
    return [round(float(np.percentile(st, q[0])), 5), round(float(np.percentile(st, q[1])), 5)]


def calib(p, y):
    """Logistic recalibration y ~ a + b * logit(p): returns (intercept a, slope b)."""
    x = lg(p)
    y = np.asarray(y, float)
    X = np.column_stack([np.ones(len(x)), x])
    w = np.array([0.0, 1.0])
    for _ in range(50):
        q = sig(X @ w)
        g = X.T @ (q - y)
        H = (X * (q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-10:
            break
    return float(w[0]), float(w[1])


def calib_ci(p, y, groups, seed=SEED, b=BOOT):
    df = pd.DataFrame({"p": p, "y": y, "g": groups})
    keys = df.g.unique()
    pos = df.groupby("g").indices
    rng = np.random.default_rng(seed)
    a_s, b_s = [], []
    for _ in range(b):
        take = np.concatenate([pos[k] for k in rng.choice(keys, len(keys))])
        a, s = calib(df.p.to_numpy()[take], df.y.to_numpy()[take])
        a_s.append(a)
        b_s.append(s)
    return ([round(float(np.percentile(a_s, 2.5)), 4), round(float(np.percentile(a_s, 97.5)), 4)],
            [round(float(np.percentile(b_s, 2.5)), 4), round(float(np.percentile(b_s, 97.5)), 4)])


def bets(p_model, price, y, thr):
    """Back team A when p >= price + COST + thr; team B when (1-p) >= (1-price) + COST + thr. 1 unit at price + 1c.
    Returns per-row returns (NaN = no bet)."""
    p_model, price, y = (np.asarray(v, float) for v in (p_model, price, y))
    r = np.full(len(y), np.nan)
    a = p_model >= price + COST + thr
    b = (~a) & ((1 - p_model) >= (1 - price) + COST + thr)
    ca, cb = price + COST, 1 - price + COST
    r[a] = np.where(y[a] == 1, (1 - ca[a]) / ca[a], -1.0)
    r[b] = np.where(y[b] == 0, (1 - cb[b]) / cb[b], -1.0)
    return r


def choose_threshold(p_inner, price_inner, y_inner):
    """In-window ROI threshold: the grid value with the largest inner net units (>= ROI_MIN_BETS bets, net > 0);
    None = do not bet this block."""
    best, best_net = None, 0.0
    for t in ROI_GRID:
        r = bets(p_inner, price_inner, y_inner, t)
        k = ~np.isnan(r)
        if k.sum() >= ROI_MIN_BETS and r[k].sum() > best_net:
            best, best_net = t, float(r[k].sum())
    return best


def roi_summary(r, groups):
    k = ~np.isnan(r)
    if not k.any():
        return {"bets": 0}
    rr, gg = r[k], np.asarray(groups)[k]
    out = {"bets": int(k.sum()), "won": int((rr > 0).sum()), "net_units": round(float(rr.sum()), 2),
           "roi": round(float(rr.mean()), 4)}
    if k.sum() >= 20:
        out["roi_ci"] = boot_ci(rr, gg)
    return out


# ------------------------------------------------------------------------------------------------------ data / blocks
def load_dataset(path=None):
    if path:
        return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_pickle(path)
    pq, pk = os.path.join(RESEARCH, "dataset.parquet"), os.path.join(RESEARCH, "dataset.pkl")
    if os.path.exists(pq) and os.path.getmtime(pq) >= os.path.getmtime(pk):
        return pd.read_parquet(pq)
    return pd.read_pickle(pk)


def spread_at_cutoff(row):
    """Quoted spread at the cutoff. Kalshi: recorded YES ask - bid. Polymarket: the price history has no book, so its
    spread is unknown and is taken as the 1-cent minimum tick - Kalshi can therefore at best TIE it, and ties go to
    Polymarket (frozen rule)."""
    return round(float(row["k_spread"]), 4) if row["platform"] == "kalshi" else 0.01


def primary_rows(df):
    """One row per match: the platform with the smaller spread at the cutoff when both exist (ties -> Polymarket),
    else the one available."""
    d = df.copy()
    d["_spread"] = [spread_at_cutoff(r) for _, r in d[["platform", "k_spread"]].iterrows()]
    d["_pm_first"] = (d.platform != "polymarket").astype(int)
    d = d.sort_values(["match_id", "_spread", "_pm_first"]).drop_duplicates("match_id", keep="first")
    return d.drop(columns=["_spread", "_pm_first"]).sort_values(["cutoff_ts", "match_id"]).reset_index(drop=True)


def platform_rows(df, plat):
    d = df[df.platform == plat]
    assert d.match_id.is_unique
    return d.sort_values(["cutoff_ts", "match_id"]).reset_index(drop=True)


def make_blocks(d):
    """Calendar-month blocks of the cutoff (split into 1st-14th / 15th-end when the month has > 600 matches); the
    first block is the first whose training set (cutoff < block start - 1 day) holds >= 1500 matches."""
    cut = pd.to_datetime(d.cutoff_ts, unit="s", utc=True)
    months = sorted(cut.dt.strftime("%Y-%m").unique())
    blocks = []
    for mo in months:
        s = pd.Timestamp(mo + "-01", tz="UTC")
        e = s + pd.offsets.MonthBegin(1)
        n = int(((cut >= s) & (cut < e)).sum())
        if n > SPLIT_MONTH_ABOVE:
            mid = s + pd.Timedelta(days=14)
            blocks += [(mo + "a", s, mid), (mo + "b", mid, e)]
        else:
            blocks.append((mo, s, e))
    out, started = [], False
    for name, s, e in blocks:
        n_train = int(d.match_id[d.cutoff_ts < s.timestamp() - EMBARGO_S].nunique())
        started = started or n_train >= MIN_TRAIN_MATCHES
        if started:
            out.append(dict(block=name, start=int(s.timestamp()), end=int(e.timestamp()), n_train=n_train))
    return out


# -------------------------------------------------------------------------------------------------------- protocol
def run_protocol(d, configs, rule="min", log=print, label=""):
    X_all = derive(d)
    y_all = d.y.to_numpy(dtype=float)
    blocks = make_blocks(d)
    oos, per = [], []
    for b in blocks:
        t0 = time.time()
        trm = (d.cutoff_ts < b["start"] - EMBARGO_S).to_numpy()
        tem = ((d.cutoff_ts >= b["start"]) & (d.cutoff_ts < b["end"])).to_numpy()
        if not tem.any():
            continue
        Xtr, ytr, ctr = X_all[trm].reset_index(drop=True), y_all[trm], d.cutoff_ts.to_numpy()[trm]
        sel = inner_select(Xtr, ytr, ctr, configs, rule, groups=d.match_id.to_numpy()[trm])
        thr = choose_threshold(sel["inner_p"], sel["inner_mkt"], sel["inner_y"]) if sel["alpha"] > 0 else None
        members = [fit(Xtr, ytr, c, k, cut=ctr) for c, k in sel["members"]] if sel["alpha"] > 0 else []
        p = predict_members(members, sel["alpha"], X_all[tem].reset_index(drop=True))
        t = d.loc[tem, ["match_id", "platform", "y", "mkt_p", "cutoff_ts"]].copy()
        t["p_model"], t["block"] = p, b["block"]
        t["ret"] = bets(p, t.mkt_p, t.y, thr) if thr is not None else np.nan
        oos.append(t)
        llm, llx = ll(t.mkt_p, t.y), ll(t.p_model, t.y)
        g = llm - llx
        per.append(dict(block=b["block"], n_train=b["n_train"], n=int(len(t)),
                        chosen=[f"{c['name']}@{k}" for c, k in sel["members"]] if sel["alpha"] > 0
                        else "market (no correction)", alpha=sel["alpha"], note=sel["note"],
                        inner_gain_lo90=sel.get("inner_gain_lo90"), inner_n=sel["inner_n"],
                        inner_gain=round(sel["inner_ll_market"] - sel["inner_ll"], 5),
                        ll_market=round(float(llm.mean()), 5), ll_model=round(float(llx.mean()), 5),
                        gain=round(float(g.mean()), 5), gain_ci=boot_ci(g, t.match_id),
                        brier_market=round(float(((t.mkt_p - t.y) ** 2).mean()), 5),
                        brier_model=round(float(((t.p_model - t.y) ** 2).mean()), 5),
                        roi_threshold=thr, roi=roi_summary(t.ret.to_numpy(), t.match_id),
                        configs_scored=len(configs), inner_table=sel["table"], secs=round(time.time() - t0, 1)))
        log(f"  {label} {b['block']}: n={len(t)} train={b['n_train']} chosen={str(per[-1]['chosen'])[:90]} a={sel['alpha']} "
            f"inner_gain={per[-1]['inner_gain']:+.5f} gain={per[-1]['gain']:+.5f} {per[-1]['gain_ci']} "
            f"thr={thr} ({per[-1]['secs']}s)")
    o = pd.concat(oos, ignore_index=True)
    return summarize(o, per), o


def summarize(o, per):
    llm, llx = ll(o.mkt_p, o.y), ll(o.p_model, o.y)
    g = llm - llx
    a_m, s_m = calib(o.p_model.to_numpy(), o.y.to_numpy())
    a_k, s_k = calib(o.mkt_p.to_numpy(), o.y.to_numpy())
    (a_m_ci, s_m_ci), (a_k_ci, s_k_ci) = calib_ci(o.p_model.to_numpy(), o.y.to_numpy(), o.match_id.to_numpy()), \
        calib_ci(o.mkt_p.to_numpy(), o.y.to_numpy(), o.match_id.to_numpy())
    gains = [b["gain"] for b in per]
    bins = []
    for lo in np.arange(0, 1, 0.1):
        k = (o.p_model >= lo) & (o.p_model < lo + 0.1)
        if k.sum():
            bins.append(dict(bin=f"{lo:.1f}-{lo + 0.1:.1f}", n=int(k.sum()), mean_p=round(float(o.p_model[k].mean()), 3),
                             observed=round(float(o.y[k].mean()), 3),
                             market_mean_p=round(float(o.mkt_p[k].mean()), 3)))
    fixed = {}
    for t in ROI_GRID:                               # descriptive only - NOT a selection
        fixed[f"{t:.2f}"] = roi_summary(bets(o.p_model, o.mkt_p, o.y, t), o.match_id)
    res = dict(
        n=int(len(o)), n_matches=int(o.match_id.nunique()), blocks=per,
        ll_market=round(float(llm.mean()), 5), ll_market_ci=boot_ci(llm, o.match_id),
        ll_model=round(float(llx.mean()), 5), ll_model_ci=boot_ci(llx, o.match_id),
        gain=round(float(g.mean()), 5), gain_ci=boot_ci(g, o.match_id),
        brier_market=round(float(((o.mkt_p - o.y) ** 2).mean()), 5),
        brier_model=round(float(((o.p_model - o.y) ** 2).mean()), 5),
        brier_gain_ci=boot_ci((o.mkt_p - o.y) ** 2 - (o.p_model - o.y) ** 2, o.match_id),
        calibration_model=dict(intercept=round(a_m, 4), intercept_ci=a_m_ci, slope=round(s_m, 4), slope_ci=s_m_ci),
        calibration_market=dict(intercept=round(a_k, 4), intercept_ci=a_k_ci, slope=round(s_k, 4), slope_ci=s_k_ci),
        calibration_bins=bins,
        blocks_positive=int(sum(1 for x in gains if x > 0)), blocks_total=len(gains),
        blocks_zero=int(sum(1 for x in gains if x == 0)),
        mean_abs_shift=round(float(np.abs(lg(o.p_model) - lg(o.mkt_p)).mean()), 5),
        roi_in_window_thresholds=roi_summary(o.ret.to_numpy(), o.match_id),
        roi_fixed_thresholds_descriptive=fixed)
    res["pass"] = dict(
        gain_ci_excludes_0_positive=bool(res["gain_ci"][0] > 0 and res["gain"] > 0),
        blocks_positive_share=round(res["blocks_positive"] / max(1, res["blocks_total"]), 3),
        blocks_ok=bool(res["blocks_positive"] >= PASS_SHARE * res["blocks_total"]),
        slope_ok=bool(PASS_SLOPE[0] <= s_m <= PASS_SLOPE[1]))
    res["pass"]["PASS"] = bool(res["pass"]["gain_ci_excludes_0_positive"] and res["pass"]["blocks_ok"]
                               and res["pass"]["slope_ok"])
    return res


def check_export(d, cfg, log=print):
    """Fit one configuration on the first 70% of rows and confirm the numpy export reproduces lightgbm exactly."""
    X = derive(d)
    k = int(len(d) * 0.7)
    m = fit(X.iloc[:k].reset_index(drop=True), d.y.to_numpy()[:k], cfg, 60)
    Xt = X.iloc[k:].reset_index(drop=True)
    p1, p2 = predict(m, Xt), predict_exported(json.loads(json.dumps(export_model(m))), Xt)
    diff = float(np.abs(p1 - p2).max())
    log(f"  export check {cfg['name']}: max |lightgbm - numpy| = {diff:.2e} on {len(Xt)} rows")
    return diff


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int)
    ap.add_argument("--analyses", default="primary,polymarket,kalshi")
    ap.add_argument("--data")
    ap.add_argument("--out", default=RESULTS)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    res = json.load(open(a.out)) if os.path.exists(a.out) else {}
    if a.summary or not a.round:
        for rk, rv in res.get("rounds", {}).items():
            for an, r in rv.get("analyses", {}).items():
                print(rk, an, "n", r["n"], "gain", r["gain"], r["gain_ci"], "blocks+", r["blocks_positive"], "/",
                      r["blocks_total"], "slope", r["calibration_model"]["slope"], "PASS", r["pass"]["PASS"])
        return res
    spec = ROUNDS[a.round]
    df = load_dataset(a.data)
    t0 = time.time()
    out = dict(desc=spec["desc"], rule=spec["rule"], configs=[c["name"] for c in spec["configs"]],
               n_configs=len(spec["configs"]), run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               analyses={})
    for an in a.analyses.split(","):
        d = primary_rows(df) if an == "primary" else platform_rows(df, an)
        print(f"round {a.round} {an}: {len(d)} rows", flush=True)
        r, o = run_protocol(d, spec["configs"], spec["rule"], log=lambda s: print(s, flush=True), label=an)
        out["analyses"][an] = r
        o.to_pickle(os.path.join(RESEARCH, f"gbm_oos_r{a.round}_{an}.pkl"))
        print(f"  {an}: gain {r['gain']} {r['gain_ci']} blocks+ {r['blocks_positive']}/{r['blocks_total']} "
              f"slope {r['calibration_model']['slope']} PASS {r['pass']['PASS']}", flush=True)
    out["export_check_max_abs_diff"] = check_export(primary_rows(df), spec["configs"][-1])
    out["secs"] = round(time.time() - t0)
    res.setdefault("rounds", {})[f"r{a.round}"] = out
    res["round_specs"] = {f"r{k}": dict(desc=v["desc"], rule=v["rule"], configs=[c["name"] for c in v["configs"]],
                                        n_configs=len(v["configs"]))
                          for k, v in ROUNDS.items()}
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    print("wrote", a.out, f"({out['secs']}s)")
    return res


if __name__ == "__main__":
    main(sys.argv[1:])
