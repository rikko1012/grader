#!/usr/bin/env python3
"""
glm-all.py - REGULARIZED GLM ON EVERYTHING for the CS2 series-winner (moneyline) research model.

Research code: nothing here is imported by server.py, the prop grader, moneyline.py or any member output.

Model
-----
    logit P(team A wins) = market_logit  +  X @ beta           (no intercept; see "antisymmetry")

  * market_logit = logit of the recorded market price at the cutoff (the OFFSET). In "free" mode the market logit is
    also a penalised column, so its effective coefficient is 1 + beta_m (shrinkage pulls it back to exactly 1, i.e.
    to the market); in "offset" mode the coefficient is fixed at 1.
  * X = point-in-time features (ml_features.py / the research dataset), standardised INSIDE the training window
    (modifiers centred with training means, every column divided by its training RMS), missing -> 0.
  * Penalty = elastic net  lam * (alpha * |beta|_1 + (1 - alpha) / 2 * |beta|_2^2)  on the (weighted) mean log-loss;
    with all beta = 0 the model IS the market price, so heavy regularisation means "trust the market".
  * Antisymmetry: team A is an arbitrary label (Polymarket outcome 0 / Kalshi first ticker). Every column is built so
    that swapping A and B flips its sign (differences, logits, logit gaps, or symmetric modifier x antisymmetric
    quantity) and there is no intercept, so the model gives P(B) = 1 - P(A) for the swapped row.

Selection (only inside each training window)
--------------------------------------------
  Candidates = the market itself + every (data variant, feature set, mode, alpha, lambda). Data variant = training
  pool ("own" = the analysed rows, "both" = both platforms' rows, each with its own price) x time-decay weighting.
  They are scored by an inner rolling-origin split of the training window: the last `folds` time chunks (`frac` of the
  window each; boundaries are cutoff TIMES so both platform rows of a match stay together) are each predicted by a fit
  on rows with cutoff < chunk start - 1 day, and scored ONLY on analysed rows (identical validation rows for every
  candidate). Selectors: "min" (lowest pooled inner log-loss), "1se" (simplest candidate within one standard error),
  "ens10" (average logit of the 10 best inner candidates). The edge threshold for the hypothetical ROI is chosen on the
  inner out-of-fold predictions of the selected candidate (never on the evaluation block).

Rounds (every configuration of every round is counted and reported in the results JSON):
  r1  pre-declared grid: 25 configs x 15 lambdas, own pool, no decay, 3 inner chunks of 12%; primary selector "min".
  r2  written AFTER r1's evaluation-block results were seen (so it is not a clean test): adds 4 parsimonious configs,
      the "both" training pool and a 180-day half-life decay (4 data variants), 5 inner chunks of 8%; primary
      selector "ens10" (r1 diagnosis: selection noise among many near-equal candidates).

Deployment: fit_enet / design / predict_model / predict_rows need numpy only (numpy is already in requirements.txt).
pandas is used only by the research CLI (dataset loading and the protocol run).

CLI:  python ml_models/glm-all.py [--dataset PATH] [--out PATH] [--boot 2000] [--rounds r1,r2]
"""
import argparse
import json
import math
import os
import time

import numpy as np

SCRATCH = ("C:/Users/tikic/AppData/Local/Temp/claude/C--Users-tikic-OneDrive-Documents-cs2-scout/"
           "6847ff60-df65-4b0c-851b-1b79f55ef3bf/scratchpad/ml_research")
DEFAULT_OUT = os.path.join(SCRATCH, "results_glm-all.json")

# ---------------------------------------------------------------------------------------------- protocol constants
MIN_TRAIN = 1500          # first evaluation block starts once >= 1500 training matches exist
GAP_DAYS = 1              # a block is predicted from rows with cutoff < block start - 1 day
MONTH_SPLIT = 600         # a month with more rows than this is split into two-week blocks (1-14, 15-end)
COST = 0.01               # +1 cent on the recorded price for the hypothetical ROI
EDGE_GRID = (0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.10)
MIN_INNER_BETS = 30       # an edge threshold needs >= 30 inner bets to be eligible
FIXED_EDGE = 0.03         # secondary ROI with a fixed (not tuned) 3-point edge
LAMBDAS = tuple(float(x) for x in np.logspace(-0.5, -4.0, 15))
ALPHAS = (1.0, 0.5, 0.0)
ENS_K = 10
SEED = 20261012
EPS = 1e-6

# ---------------------------------------------------------------------------------------------- features
# Symmetric modifiers (unchanged when A and B swap) - used only multiplied by an antisymmetric quantity.
MODIFIERS = ("liq", "age", "bo1", "bo5", "lan", "kal", "ksp", "kvol", "oliq", "osp", "exp", "tier", "rd")

MKT_BASE = ["m3", "mv6", "mv24", "mid48", "xgap"]
MKT_INTER = ["m*liq", "m*age", "m*bo1", "m*bo5", "m*lan", "m*kal", "m*ksp", "m*kvol", "m*exp", "m*tier",
             "xgap*age", "xgap*oliq", "xgap*osp", "xgap*kal", "mv6*liq"]
HLTV_CORE = ["r_elo", "r_relo", "r_glicko", "relo_last_diff", "form10_adj_diff", "mappool_vs", "lan_gap_x_lan",
             "standin_diff", "stab30_diff", "mle_e_h2h", "lastlu_lt5_diff", "rest_days_diff"]
HLTV_EXTRA = ["elo_diff_k10", "elo_diff_k16", "elo_diff_k24", "elo_diff_k32", "elo_diff_k48",
              "relo_diff_k10", "relo_diff_k16", "relo_diff_k24", "relo_diff_k32", "relo_diff_k48",
              "glicko_diff", "form5_diff", "form10_diff", "form5_adj_diff", "lan_gap_diff", "stab90_diff", "m7_diff",
              "games_series_ldiff", "home_diff", "mle_a_rating", "mle_b_mappool", "mle_c_form", "mle_d_lan_x_rating",
              "h2h_w", "maps60_ldiff", "series365_ldiff", "lastlu_overlap_diff", "starters_lt5_diff",
              "glicko_rd_diff", "n_starters_diff", "elo_known_diff"]
HLTV_INTER = ["r_elo*exp", "r_relo*exp", "r_glicko*rd", "r_relo*bo1", "r_relo*lan", "r_elo*liq", "r_relo*liq",
              "r_elo*tier", "r_relo*age"]

FEATURE_SETS = {
    # round 1
    "S0_recal": ["m3"],                                              # market recalibration (free market coef + FLB)
    "H_hltv": list(HLTV_CORE),                                       # HLTV core only
    "S1_market": MKT_BASE + MKT_INTER,                               # market structure (moves, x-platform, liquidity)
    "S2_core": MKT_BASE + MKT_INTER + HLTV_CORE,                     # market structure + HLTV core
    "S3_all": MKT_BASE + MKT_INTER + HLTV_CORE + HLTV_EXTRA + HLTV_INTER,   # everything
    # round 2 (parsimonious)
    "P1_blend": ["r_avg"],                                           # market blended with the mean HLTV rating logit
    "P2_small": ["m3", "r_avg", "xgap", "mv6"],                      # four strongest-prior signals
}
SIMPLICITY = {"market": 0, "P1_blend": 1, "S0_recal": 1, "P2_small": 2, "H_hltv": 3, "S1_market": 3, "S2_core": 4,
              "S3_all": 5}


def _configs(fsets, alphas=ALPHAS):
    out = []
    for fs in fsets:
        for free in (False, True):
            for a in alphas:
                tag = {1.0: "lasso", 0.5: "enet", 0.0: "ridge"}[a]
                out.append({"name": f"{fs}|{'free' if free else 'offset'}|{tag}", "fset": fs, "free": free,
                            "alpha": a})
    return out


CONFIGS_R1 = [{"name": "S0_recal|free|ridge", "fset": "S0_recal", "free": True, "alpha": 0.0}] + _configs(
    ("H_hltv", "S1_market", "S2_core", "S3_all"))
CONFIGS_R2_NEW = _configs(("P1_blend", "P2_small"), alphas=(0.0,))
ROUNDS = {
    "r1": {"configs": CONFIGS_R1, "variants": [("own", None)], "folds": 3, "frac": 0.12, "primary": "min",
           "runs": ["primary", "polymarket", "kalshi", "polymarket_pooled", "kalshi_pooled"]},
    "r2": {"configs": CONFIGS_R1 + CONFIGS_R2_NEW, "variants": [("own", None), ("own", 180), ("both", None),
                                                               ("both", 180)],
           "folds": 5, "frac": 0.08, "primary": "ens10",
           "runs": ["primary", "polymarket", "kalshi", "kalshi_pooled"]},
}
ALL_CONFIGS = {c["name"]: c for c in CONFIGS_R1 + CONFIGS_R2_NEW}


def _lg(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _f(cols, name, n):
    v = cols.get(name)
    if v is None:
        return np.full(n, np.nan)
    return np.asarray(v, dtype=float)


def raw_arrays(cols):
    """cols: {dataset column -> 1-D array} (a DataFrame's columns, or live rows). Returns (base, mods): antisymmetric
    base features (NaN = missing) and symmetric modifiers. Row-wise only - no statistic of other rows is used."""
    n = len(np.asarray(cols["mkt_p"]))
    g = lambda c: _f(cols, c, n)                                      # noqa: E731
    m = _lg(g("mkt_p"))
    base = {"m": m, "m3": m * np.abs(m) / 4.0, "mv6": g("mkt_mv6"), "mv24": g("mkt_mv24")}
    mx, mn = g("mkt_max48"), g("mkt_min48")
    base["mid48"] = np.where(np.isfinite(mx) & np.isfinite(mn), (_lg(mx) + _lg(mn)) / 2 - m, np.nan)
    base["xgap"] = g("xgap")
    rs = []
    for k in ("elo", "relo", "glicko"):
        p = g(f"{k}_p")
        base[f"r_{k}"] = np.where(np.isfinite(p), _lg(p) - m, np.nan)
        rs.append(base[f"r_{k}"])
    rs = np.vstack(rs)
    cnt = np.isfinite(rs).sum(0)
    base["r_avg"] = np.where(cnt > 0, np.nansum(rs, 0) / np.maximum(cnt, 1), np.nan)
    for c in ("relo_last_diff", "form10_adj_diff", "mappool_vs", "lan_gap_x_lan", "standin_diff", "stab30_diff",
              "mle_e_h2h", "lastlu_lt5_diff", "rest_days_diff",
              "elo_diff_k10", "elo_diff_k16", "elo_diff_k24", "elo_diff_k32", "elo_diff_k48",
              "relo_diff_k10", "relo_diff_k16", "relo_diff_k24", "relo_diff_k32", "relo_diff_k48",
              "glicko_diff", "form5_diff", "form10_diff", "form5_adj_diff", "lan_gap_diff", "stab90_diff", "m7_diff",
              "home_diff", "mle_a_rating", "mle_b_mappool", "mle_c_form", "mle_d_lan_x_rating"):
        base[c] = g(c)
    l1 = lambda a: np.log1p(np.maximum(a, 0))                          # noqa: E731
    base["games_series_ldiff"] = l1(g("games_series_a")) - l1(g("games_series_b"))
    base["maps60_ldiff"] = l1(g("maps60_a")) - l1(g("maps60_b"))
    base["series365_ldiff"] = l1(g("series365_a")) - l1(g("series365_b"))
    base["h2h_w"] = g("mle_e_h2h") * l1(g("mle_e_h2h_n"))
    for c in ("lastlu_overlap", "starters_lt5", "glicko_rd", "n_starters"):
        base[f"{c}_diff"] = g(f"{c}_a") - g(f"{c}_b")
    base["elo_known_diff"] = g("elo_known_a").astype(float) - g("elo_known_b").astype(float)

    plat = np.asarray(cols.get("platform", np.array(["polymarket"] * n)), dtype=object)
    kal = (plat == "kalshi").astype(float)
    bo = g("bo")
    mods = {"liq": l1(g("mkt_npts48")), "age": l1(g("mkt_age_min")), "bo1": (bo == 1).astype(float),
            "bo5": (bo == 5).astype(float), "lan": g("lan"), "kal": kal,
            "ksp": np.where(kal == 1, g("k_spread"), np.nan), "kvol": np.where(kal == 1, l1(g("k_vol48")), np.nan),
            "oliq": l1(g("o_npts48")), "osp": g("o_spread"),
            "exp": l1(np.fmin(g("games_series_a"), g("games_series_b"))), "tier": g("tier_mean"),
            "rd": (g("glicko_rd_a") + g("glicko_rd_b")) / 2}
    return base, mods


def design(base, mods, names, stats=None):
    """Matrix for feature `names` ('x' or 'x*mod'); stats = None -> computed here (TRAINING rows) and returned.
    Modifiers are centred with the training mean, every column divided by its training RMS (no centring of the
    antisymmetric columns - it would break the A/B symmetry), missing -> 0."""
    n = len(base["m"])
    fit = stats is None
    if fit:
        stats = {"mod_mean": {}, "rms": {}}
    cols = []
    for nm in names:
        if "*" in nm:
            b, md = nm.split("*")
            x = np.nan_to_num(base[b], nan=0.0)
            mv = mods[md]
            if fit and md not in stats["mod_mean"]:
                stats["mod_mean"][md] = float(np.nanmean(mv)) if np.isfinite(mv).any() else 0.0
            x = x * np.nan_to_num(mv - stats["mod_mean"][md], nan=0.0)
        else:
            x = np.nan_to_num(base[nm], nan=0.0)
        if fit:
            r = float(np.sqrt(np.mean(x ** 2))) if n else 0.0
            stats["rms"][nm] = r if r > 1e-9 else 1.0
        cols.append(x / stats["rms"][nm])
    X = np.column_stack(cols) if cols else np.zeros((n, 0))
    return X, stats


def config_names(cfg):
    return (["m"] if cfg["free"] else []) + FEATURE_SETS[cfg["fset"]]


# ---------------------------------------------------------------------------------------------- solver (numpy only)
def _sig(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_enet(X, y, off, lam, alpha, beta0=None, w=None, maxit=4000, tol=1e-7):
    """argmin_b  sum_i w_i logloss_i / n  +  lam*(alpha*|b|_1 + (1-alpha)/2*|b|^2),  logit_i = off_i + X_i b.
    No intercept. w = row weights (mean 1; None = equal). alpha == 0: damped Newton; else FISTA + adaptive restart."""
    n, p = X.shape
    if p == 0:
        return np.zeros(0)
    w = np.ones(n) if w is None else np.asarray(w, float)
    b = np.zeros(p) if beta0 is None else np.array(beta0, dtype=float)
    l2 = lam * (1 - alpha)
    if alpha == 0:
        for _ in range(100):
            pr = _sig(off + X @ b)
            gr = X.T @ (w * (pr - y)) / n + l2 * b
            H = (X * (w * pr * (1 - pr))[:, None]).T @ X / n + l2 * np.eye(p)
            step = np.linalg.solve(H + 1e-10 * np.eye(p), gr)
            b = b - step
            if np.max(np.abs(step)) < 1e-9:
                break
        return b
    L = 0.25 * np.linalg.norm(X * np.sqrt(w)[:, None], 2) ** 2 / n + l2
    l1 = lam * alpha / L
    z, t = b.copy(), 1.0
    for _ in range(maxit):
        pr = _sig(off + X @ z)
        gr = X.T @ (w * (pr - y)) / n + l2 * z
        u = z - gr / L
        bn = np.sign(u) * np.maximum(np.abs(u) - l1, 0.0)
        if np.max(np.abs(bn - b)) < tol:
            b = bn
            break
        if np.dot(z - bn, bn - b) > 0:          # adaptive restart (O'Donoghue & Candes)
            t = 1.0
        tn = (1 + math.sqrt(1 + 4 * t * t)) / 2
        z = bn + ((t - 1) / tn) * (bn - b)
        b, t = bn, tn
    return b


def fit_path(X, y, off, alpha, lambdas=LAMBDAS, w=None):
    """Warm-started path, lambdas in DESCENDING order. Returns [beta per lambda]."""
    out, b = [], None
    for lam in lambdas:
        b = fit_enet(X, y, off, lam, alpha, beta0=b, w=w)
        out.append(b.copy())
    return out


def predict_logit(beta, X, off):
    return off + (X @ beta if len(beta) else 0.0)


def logloss(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def decay_weights(cut, t_ref, half_life_days):
    if not half_life_days:
        return None
    w = 0.5 ** ((t_ref - np.asarray(cut, float)) / (half_life_days * 86400.0))
    return w / w.mean()


# ---------------------------------------------------------------------------------------------- deployable model
def fit_model(base, mods, y, cfg, lam, w=None):
    """Final fit of one candidate on a training window -> JSON-serialisable model."""
    names = config_names(cfg)
    X, stats = design(base, mods, names)
    lams = [x for x in LAMBDAS if x > lam * (1 + 1e-9)] + [lam]      # warm-started down to lam (as in the inner path)
    beta = fit_path(X, y, base["m"], cfg["alpha"], lams, w=w)[-1]
    return {"config": cfg, "lambda": lam, "names": names, "stats": stats, "beta": [float(v) for v in beta]}


def predict_model(model, base, mods):
    """P(team A) for rows given as raw_arrays() output. model None = the market price; {"ensemble": [models]} = the
    average logit of its members."""
    if model is None:
        return _sig(base["m"])
    if "ensemble" in model:
        zs = [_lg(predict_model(mm, base, mods)) for mm in model["ensemble"]]
        return _sig(np.mean(zs, 0))
    X, _ = design(base, mods, model["names"], model["stats"])
    return _sig(predict_logit(np.asarray(model["beta"]), X, base["m"]))


def predict_rows(model, rows):
    """Live entry point: rows = list of dicts with the dataset's column names (ml_features.live_features output plus
    mkt_p etc., platform, o_npts48 / o_spread when the other platform quotes). numpy only."""
    keys = set().union(*[r.keys() for r in rows]) if rows else set()
    cols = {k: np.array([r.get(k) if r.get(k) is not None else np.nan for r in rows], dtype=float)
            for k in keys if k not in ("platform",) and not isinstance(rows[0].get(k), str)}
    cols["platform"] = np.array([r.get("platform", "polymarket") for r in rows], dtype=object)
    base, mods = raw_arrays(cols)
    return predict_model(model, base, mods)


# ---------------------------------------------------------------------------------------------- inner selection
def _subset(d, idx):
    return {k: v[idx] for k, v in d.items()}


def chunk_bounds(cut_eval_train, folds, frac):
    """Time boundaries [(t_lo, t_hi)] of the inner validation chunks, from the analysed rows' sorted cutoffs."""
    cut = np.sort(np.asarray(cut_eval_train, float))
    n = len(cut)
    out = []
    for j in range(folds):
        t_lo = cut[min(n - 1, int(n * (1 - frac * (folds - j))))]
        t_hi = cut[-1] + 1 if j == folds - 1 else cut[min(n - 1, int(n * (1 - frac * (folds - j - 1))))]
        out.append((float(t_lo), float(t_hi)))
    return out


def inner_select(base, mods, y, cut, is_eval, mid, bounds, configs, hl=None, prefix=()):
    """Inner rolling-origin scores of every (config, lambda) on one training pool. Validation rows = analysed rows
    (is_eval) inside each chunk; training = pool rows with cutoff < chunk start - GAP_DAYS. Returns
    {"vidx_mid": validation match ids, "y", "price", "oof": {key: probs}}."""
    chunks = []
    for t_lo, t_hi in bounds:
        va = np.where(is_eval & (cut >= t_lo) & (cut < t_hi))[0]
        tr = np.where(cut < t_lo - GAP_DAYS * 86400)[0]
        if len(va) >= 20 and len(tr) >= 200:
            chunks.append((tr, va, t_lo))
    vidx = np.concatenate([v for _, v, _ in chunks])
    out = {"chunks": [(int(len(t)), int(len(v))) for t, v, _ in chunks], "mid": mid[vidx], "y": y[vidx],
           "price": _sig(base["m"][vidx]), "oof": {}}
    for cfg in configs:
        names = config_names(cfg)
        oof = np.zeros((len(LAMBDAS), len(vidx)))
        pos = 0
        for tr, va, t_lo in chunks:
            btr, mtr = _subset(base, tr), _subset(mods, tr)
            X, st = design(btr, mtr, names)
            path = fit_path(X, y[tr], btr["m"], cfg["alpha"], w=decay_weights(cut[tr], t_lo, hl))
            Xv, _ = design(_subset(base, va), _subset(mods, va), names, st)
            for li, beta in enumerate(path):
                oof[li, pos:pos + len(va)] = _sig(predict_logit(beta, Xv, base["m"][va]))
            pos += len(va)
        for li, lam in enumerate(LAMBDAS):
            out["oof"][prefix + (cfg["name"], lam)] = oof[li]
    return out


def _simplicity(key):
    if key[0] == "market":
        return (0, 0, 0.0)
    pool, hl, name, lam = key
    return (SIMPLICITY[name.split("|")[0]], (pool == "both") + (hl is not None), -lam)


def select(mean_loss, row_loss, rule="min", among=None):
    keys = [k for k in mean_loss if among is None or k[0] == "market" or among(k)]
    best = min(keys, key=lambda k: (mean_loss[k], _simplicity(k)))
    if rule == "min":
        return [best]
    if rule.startswith("ens"):
        kk = int(rule[3:])
        return sorted(keys, key=lambda k: (mean_loss[k], _simplicity(k)))[:kk]
    n = len(row_loss[best])
    ok = []
    for k in keys:
        dlt = row_loss[k] - row_loss[best]
        se = float(dlt.std(ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        if mean_loss[k] <= mean_loss[best] + se:
            ok.append(k)
    return [min(ok, key=_simplicity)]


def ens_prob(probs):
    return _sig(np.mean([_lg(p) for p in probs], 0))


def bet_returns(p_model, price, y, edge, groups=None, return_groups=False):
    """1 unit on A when p - (price + COST) >= edge, on B when (1-p) - (1-price+COST) >= edge; paid at price + COST."""
    p_model, price, y = np.asarray(p_model), np.asarray(price), np.asarray(y)
    ca, cb = price + COST, 1 - price + COST
    ba = (p_model - ca) >= edge
    bb = (~ba) & (((1 - p_model) - cb) >= edge)
    r = np.where(ba, np.where(y == 1, (1 - ca) / ca, -1.0), np.where(y == 0, (1 - cb) / cb, -1.0))
    sel = ba | bb
    if return_groups:
        return r[sel], np.asarray(groups)[sel]
    return r[sel]


def choose_edge(p_model, price, y):
    """Edge threshold with the best inner net units (>= MIN_INNER_BETS bets); None = no bets (nothing positive)."""
    best, best_net = None, 0.0
    for e in EDGE_GRID:
        r = bet_returns(p_model, price, y, e)
        if len(r) >= MIN_INNER_BETS and r.sum() > best_net:
            best, best_net = e, float(r.sum())
    return best


# ---------------------------------------------------------------------------------------------- data / blocks
def load_dataset(path=None):
    import pandas as pd
    if path:
        return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_pickle(path)
    pq, pk = os.path.join(SCRATCH, "dataset.parquet"), os.path.join(SCRATCH, "dataset.pkl")
    if os.path.exists(pq):
        return pd.read_parquet(pq)
    return pd.read_pickle(pk)


def attach_other(df):
    """Add the OTHER platform's point-in-time liquidity to each row (same match, same cutoff): o_npts48 (its point
    count in the 48 h before the cutoff), o_spread (its bid/ask spread when the other platform is Kalshi)."""
    o = df[["match_id", "platform", "mkt_npts48", "k_spread"]].rename(
        columns={"mkt_npts48": "o_npts48", "k_spread": "o_spread"})
    o["platform"] = o["platform"].map({"polymarket": "kalshi", "kalshi": "polymarket"})
    return df.merge(o, on=["match_id", "platform"], how="left")


def primary_rows(df):
    """Frozen baseline rule: the platform with the smaller spread at the cutoff when both exist, else the one
    available; ties -> Polymarket. The research dataset records no Polymarket spread (its price history is a
    mid/last-trade series), so Polymarket's spread is taken as its 1-cent minimum tick: Kalshi would need a spread
    < 1 cent, which never occurs (Kalshi's minimum is 1 cent) -> Polymarket when available, else Kalshi."""
    import pandas as pd
    df = attach_other(df)
    pm = df[df.platform == "polymarket"]
    k = df[(df.platform == "kalshi") & ~df.match_id.isin(pm.match_id)]
    return pd.concat([pm, k]).sort_values(["cutoff_ts", "match_id"]).reset_index(drop=True)


def make_blocks(cut_eval, cut_train_matches, min_train=MIN_TRAIN):
    """Evaluation blocks on cutoff (UTC): calendar months, a month with > MONTH_SPLIT rows split into days 1-14 and
    15-end; the first block is the first whose training window (cutoff < start - GAP_DAYS) holds >= min_train
    distinct matches (cut_train_matches = one cutoff per training match). Returns [(label, start_ts, end_ts)]."""
    import pandas as pd
    t = pd.to_datetime(pd.Series(cut_eval), unit="s", utc=True)
    blocks = []
    for mth in sorted(t.dt.strftime("%Y-%m").unique()):
        s = pd.Timestamp(mth + "-01", tz="UTC")
        e = s + pd.offsets.MonthBegin(1)
        if int(((t >= s) & (t < e)).sum()) > MONTH_SPLIT:
            mid = s + pd.Timedelta(days=14)
            blocks += [(mth + "a", s, mid), (mth + "b", mid, e)]
        else:
            blocks.append((mth, s, e))
    ctm = np.asarray(cut_train_matches, dtype=float)
    out, started = [], False
    for lab, s, e in blocks:
        if not started and int((ctm < s.timestamp() - GAP_DAYS * 86400).sum()) >= min_train:
            started = True
        if started:
            out.append((lab, s.timestamp(), e.timestamp()))
    return out


def _arrays(d):
    cols = {}
    for c in d.columns:
        if c == "platform":
            continue
        try:
            cols[c] = d[c].to_numpy().astype(float)
        except (TypeError, ValueError):
            pass
    cols["platform"] = d["platform"].to_numpy(dtype=object)
    return raw_arrays(cols)


class Pool:
    """A training pool (sorted by cutoff) with its feature arrays and the analysed-row mask."""

    def __init__(self, d, eval_keys):
        self.d = d.sort_values(["cutoff_ts", "match_id", "platform"]).reset_index(drop=True)
        self.base, self.mods = _arrays(self.d)
        self.y = self.d.y.to_numpy().astype(float)
        self.cut = self.d.cutoff_ts.to_numpy().astype(float)
        self.mid = self.d.match_id.to_numpy()
        keys = list(zip(self.d.match_id, self.d.platform))
        self.is_eval = np.array([k in eval_keys for k in keys])


# ---------------------------------------------------------------------------------------------- protocol
def run_protocol(pools, eval_df, blocks, spec, label, log=print):
    """pools: {"own": Pool, "both": Pool (optional)}; eval_df: analysed rows; blocks: [(label, s, e)];
    spec: a ROUNDS entry. Returns (out-of-sample frame, per-block details)."""
    import pandas as pd
    ev = eval_df.sort_values(["cutoff_ts", "match_id"]).reset_index(drop=True)
    base_ev, mods_ev = _arrays(ev)
    variants = [v for v in spec["variants"] if v[0] in pools]
    configs = spec["configs"]
    preds, per_block = [], []
    for lab, s, e in blocks:
        t0 = time.time()
        evi = np.where((ev.cutoff_ts.to_numpy() >= s) & (ev.cutoff_ts.to_numpy() < e))[0]
        if not len(evi):
            continue
        t_end = s - GAP_DAYS * 86400
        own = pools["own"]
        own_tr = own.cut < t_end
        bounds = chunk_bounds(own.cut[own_tr & own.is_eval], spec["folds"], spec["frac"])
        oof, ref = {}, None
        trsets = {}
        for pool_name, hl in variants:
            P = pools[pool_name]
            tri = np.where(P.cut < t_end)[0]
            trsets[(pool_name, hl)] = tri
            inn = inner_select(_subset(P.base, tri), _subset(P.mods, tri), P.y[tri], P.cut[tri], P.is_eval[tri],
                               P.mid[tri], bounds, configs, hl=hl, prefix=(pool_name, hl))
            if ref is None:
                ref = inn
                oof[("market",)] = inn["price"]
            else:                                   # identical validation rows for every candidate
                assert (inn["mid"] == ref["mid"]).all() and (inn["y"] == ref["y"]).all()
            oof.update(inn["oof"])
        yv = ref["y"]
        row_loss = {k: logloss(p, yv) for k, p in oof.items()}
        mean_loss = {k: float(v.mean()) for k, v in row_loss.items()}
        be, me = _subset(base_ev, evi), _subset(mods_ev, evi)
        out = ev.iloc[evi][["match_id", "platform", "y", "mkt_p", "cutoff_ts"]].copy()
        out["block"] = lab
        cache = {}

        def fit_pred(key):
            if key not in cache:
                if key[0] == "market":
                    cache[key] = (None, _sig(be["m"]))
                else:
                    pool_name, hl, name, lam = key
                    P, tri = pools[pool_name], trsets[(pool_name, hl)]
                    mdl = fit_model(_subset(P.base, tri), _subset(P.mods, tri), P.y[tri], ALL_CONFIGS[name], lam,
                                    w=decay_weights(P.cut[tri], s, hl))
                    mdl["pool"], mdl["half_life_days"] = pool_name, hl
                    cache[key] = (mdl, predict_model(mdl, be, me))
            return cache[key]

        def sel_pred(keys):
            ps = [fit_pred(k)[1] for k in keys]
            return ps[0] if len(ps) == 1 else ens_prob(ps)

        def sel_oof(keys):
            return oof[keys[0]] if len(keys) == 1 else ens_prob([oof[k] for k in keys])

        per_cfg = {}
        for pool_name, hl in variants:
            for cfg in configs:
                ks = select(mean_loss, row_loss, "min",
                            among=lambda k, c=cfg["name"], pn=pool_name, h=hl: k[:3] == (pn, h, c))
                col = f"{pool_name}|{hl or 'nodecay'}|{cfg['name']}"
                out["p__" + col] = sel_pred(ks)
                per_cfg[col] = [str(ks[0][-1]), round(mean_loss[ks[0]], 5)]
        sels = {}
        for rule in ("min", "1se", f"ens{ENS_K}"):
            ks = select(mean_loss, row_loss, rule)
            out["p_" + rule] = sel_pred(ks)
            ed = choose_edge(sel_oof(ks), ref["price"], yv)
            out["edge_" + rule] = ed if ed is not None else np.nan
            sels[rule] = {"keys": [[str(x) for x in k] for k in ks], "edge_threshold": ed,
                          "inner_ll": round(float(logloss(sel_oof(ks), yv).mean()), 5)}
        kmin = select(mean_loss, row_loss, "min")[0]
        mdl_min = fit_pred(kmin)[0]
        top = []
        if mdl_min:
            bb = np.asarray(mdl_min["beta"])
            top = [[mdl_min["names"][i], round(float(bb[i]), 4)] for i in np.argsort(-np.abs(bb))[:8]
                   if abs(bb[i]) > 1e-8]
        preds.append(out)
        y_b, lm = out.y.to_numpy(), logloss(out.mkt_p.to_numpy(), out.y.to_numpy())
        lp = logloss(out["p_" + spec["primary"]].to_numpy(), y_b)
        P0 = pools["own"]
        pb = {"block": lab, "start": pd.Timestamp(s, unit="s", tz="UTC").strftime("%Y-%m-%d"),
              "end": pd.Timestamp(e, unit="s", tz="UTC").strftime("%Y-%m-%d"),
              "n_train_rows_own": int(own_tr.sum()), "n_train_matches_own": int(len(set(P0.mid[own_tr]))),
              "n": int(len(evi)), "inner_chunks": ref["chunks"], "inner_n_val": int(len(yv)),
              "n_candidates": len(oof), "inner_ll_market": round(mean_loss[("market",)], 5),
              "selectors": sels, "min_nonzero": int(np.sum(np.abs(mdl_min["beta"]) > 1e-8)) if mdl_min else 0,
              "min_top_coefs": top, "ll_market": round(float(lm.mean()), 5),
              "ll_primary": round(float(lp.mean()), 5), "gain_primary": round(float((lm - lp).mean()), 5),
              "per_config_inner": per_cfg, "secs": round(time.time() - t0, 1)}
        per_block.append(pb)
        log(f"[{label}] {lab}: train={pb['n_train_rows_own']} n={len(evi)} cands={len(oof)} "
            f"min={kmin} inner {mean_loss[kmin]:.5f} vs mkt {mean_loss[('market',)]:.5f}  "
            f"OOS gain({spec['primary']}) {pb['gain_primary']:+.5f} ({pb['secs']}s)", flush=True)
    return pd.concat(preds).reset_index(drop=True), per_block


# ---------------------------------------------------------------------------------------------- metrics
def boot_mean(v, groups, B, seed=SEED):
    import pandas as pd
    s = pd.Series(np.asarray(v, float)).groupby(np.asarray(groups)).agg(["sum", "count"])
    a, c = s["sum"].to_numpy(), s["count"].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(a), size=(B, len(a)))
    st = a[idx].sum(1) / c[idx].sum(1)
    return [round(float(np.percentile(st, 2.5)), 5), round(float(np.percentile(st, 97.5)), 5)]


def calib_fit(p, y):
    """y ~ sigmoid(a + b * logit(p)) by Newton -> (intercept a, slope b)."""
    z = _lg(p)
    X = np.column_stack([np.ones_like(z), z])
    w = np.array([0.0, 1.0])
    for _ in range(50):
        pr = _sig(X @ w)
        H = (X * (pr * (1 - pr))[:, None]).T @ X + 1e-9 * np.eye(2)
        st = np.linalg.solve(H, X.T @ (pr - y))
        w -= st
        if np.max(np.abs(st)) < 1e-10:
            break
    return float(w[0]), float(w[1])


def calib_boot(p, y, groups, B, seed=SEED):
    import pandas as pd
    g = pd.Series(np.arange(len(y))).groupby(np.asarray(groups)).apply(list).to_list()
    rng = np.random.default_rng(seed + 1)
    a_s, b_s = [], []
    for _ in range(B):
        ix = np.concatenate([g[i] for i in rng.integers(0, len(g), len(g))])
        a, b = calib_fit(p[ix], y[ix])
        a_s.append(a)
        b_s.append(b)
    q = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]  # noqa: E731
    return q(a_s), q(b_s)


def roi_summary(r, g, B):
    if not len(r):
        return {"bets": 0, "won": 0, "net_units": 0.0, "roi": None}
    out = {"bets": int(len(r)), "won": int((r > 0).sum()), "net_units": round(float(r.sum()), 2),
           "roi": round(float(r.mean()), 4)}
    if len(r) >= 20:
        out["roi_ci"] = [round(x, 4) for x in boot_mean(r, g, B)]
    return out


def evaluate(o, B, pcol, ecol):
    y, pm, p = o.y.to_numpy().astype(float), o.mkt_p.to_numpy(), o[pcol].to_numpy()
    g = o.match_id.to_numpy()
    lm, lp = logloss(pm, y), logloss(p, y)
    res = {"n": int(len(o)), "n_matches": int(o.match_id.nunique()), "n_blocks": int(o.block.nunique()),
           "ll_market": round(float(lm.mean()), 5), "ll_market_ci": boot_mean(lm, g, B),
           "ll_model": round(float(lp.mean()), 5), "ll_model_ci": boot_mean(lp, g, B),
           "gain": round(float((lm - lp).mean()), 5), "gain_ci": boot_mean(lm - lp, g, B),
           "brier_market": round(float(((pm - y) ** 2).mean()), 5),
           "brier_model": round(float(((p - y) ** 2).mean()), 5),
           "brier_gain_ci": boot_mean((pm - y) ** 2 - (p - y) ** 2, g, B)}
    a, b = calib_fit(p, y)
    ac, bc = calib_boot(p, y, g, min(B, 1000))
    am, bm = calib_fit(pm, y)
    amc, bmc = calib_boot(pm, y, g, min(B, 1000))
    res.update(calib_intercept=round(a, 4), calib_intercept_ci=ac, calib_slope=round(b, 4), calib_slope_ci=bc,
               market_calib_intercept=round(am, 4), market_calib_intercept_ci=amc,
               market_calib_slope=round(bm, 4), market_calib_slope_ci=bmc)
    blocks = []
    for lab in o.block.unique():
        m = (o.block == lab).to_numpy()
        d = lm[m] - lp[m]
        blocks.append({"block": lab, "n": int(m.sum()), "ll_market": round(float(lm[m].mean()), 5),
                       "ll_model": round(float(lp[m].mean()), 5), "gain": round(float(d.mean()), 5),
                       "gain_ci": boot_mean(d, g[m], B)})
    res["blocks"] = blocks
    pos = sum(1 for b_ in blocks if b_["gain"] > 0)
    res["blocks_positive"] = f"{pos}/{len(blocks)}"
    res["share_blocks_positive"] = round(pos / len(blocks), 3) if blocks else None
    cal = []
    for lo in np.arange(0, 1, 0.1):
        mm = (p >= lo) & (p < lo + 0.1)
        if mm.sum():
            cal.append({"bin": f"{lo:.1f}-{lo + 0.1:.1f}", "n": int(mm.sum()), "mean_p": round(float(p[mm].mean()), 3),
                        "observed": round(float(y[mm].mean()), 3), "mean_market": round(float(pm[mm].mean()), 3)})
    res["calibration_bins"] = cal
    # ROI: per-block edge threshold chosen inside the training window (NaN -> no bets in that block)
    rs, gs, thr = [], [], {}
    for lab in o.block.unique():
        m = (o.block == lab).to_numpy()
        e = float(o[ecol].to_numpy()[m][0])
        thr[lab] = e if np.isfinite(e) else None
        if np.isfinite(e):
            r, gg = bet_returns(p[m], pm[m], y[m], e, g[m], return_groups=True)
            rs.append(r)
            gs.append(gg)
    r = np.concatenate(rs) if rs else np.zeros(0)
    gg = np.concatenate(gs) if gs else np.zeros(0)
    res["roi_inner_threshold"] = dict(roi_summary(r, gg, B), thresholds_by_block=thr)
    r, gg = bet_returns(p, pm, y, FIXED_EDGE, g, return_groups=True)
    res["roi_fixed_3pt"] = roi_summary(r, gg, B)
    res["pass_checks"] = {"gain_gt_0": bool(res["gain"] > 0), "ci_excludes_0": bool(res["gain_ci"][0] > 0),
                          "blocks_positive_ge_60pct": bool(blocks and pos / len(blocks) >= 0.6),
                          "calib_slope_in_0.8_1.2": bool(0.8 <= res["calib_slope"] <= 1.2)}
    res["passes"] = all(res["pass_checks"].values())
    return res


def config_table(o, B):
    y, pm, g = o.y.to_numpy().astype(float), o.mkt_p.to_numpy(), o.match_id.to_numpy()
    lm = logloss(pm, y)
    rows = []
    for c in [c for c in o.columns if c.startswith("p__")]:
        lp = logloss(o[c].to_numpy(), y)
        d = lm - lp
        pos = sum(1 for b in o.block.unique() if d[(o.block == b).to_numpy()].mean() > 0)
        rows.append({"config": c[3:], "ll_model": round(float(lp.mean()), 5), "gain": round(float(d.mean()), 5),
                     "gain_ci": boot_mean(d, g, B), "blocks_positive": f"{pos}/{o.block.nunique()}"})
    return rows


def _json_default(x):
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    return str(x)


def build_runs(df):
    """Run definitions: (eval rows, pools, blocks). Blocks follow the protocol on each run's own analysed rows;
    *_pooled runs train on both platforms' rows and use the primary analysis' blocks."""
    allr = attach_other(df)
    prim = primary_rows(df)
    key = lambda d: set(zip(d.match_id, d.platform))                   # noqa: E731
    pm, k = allr[allr.platform == "polymarket"], allr[allr.platform == "kalshi"]

    def blocks_for(ev, train_rows):
        return make_blocks(ev.cutoff_ts.to_numpy(), train_rows.groupby("match_id").cutoff_ts.min().to_numpy())

    pblocks = blocks_for(prim, prim)
    return {
        "primary": (prim, {"own": Pool(prim, key(prim)), "both": Pool(allr, key(prim))}, pblocks),
        "polymarket": (pm, {"own": Pool(pm, key(pm)), "both": Pool(allr, key(pm))}, blocks_for(pm, pm)),
        "kalshi": (k, {"own": Pool(k, key(k)), "both": Pool(allr, key(k))}, blocks_for(k, k)),
        "polymarket_pooled": (pm, {"own": Pool(allr, key(pm))}, pblocks),
        "kalshi_pooled": (k, {"own": Pool(allr, key(k))}, pblocks),
    }


def main(argv=None):
    import pandas as pd
    ap = argparse.ArgumentParser(description="glm-all rolling-origin protocol")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--rounds", default="r1,r2")
    ap.add_argument("--runs", default=None, help="comma list to restrict the runs of every round")
    a = ap.parse_args(argv)
    df = load_dataset(a.dataset).sort_values(["cutoff_ts", "match_id"]).reset_index(drop=True)
    runs = build_runs(df)
    t0 = time.time()
    res = {"approach": "glm-all", "generated_unix": int(time.time()),
           "dataset": a.dataset or "dataset.parquet if present else dataset.pkl (scratchpad ml_research)",
           "lambdas": [round(x, 6) for x in LAMBDAS], "feature_sets": FEATURE_SETS, "modifiers": list(MODIFIERS),
           "roi_rules": {"cost": COST, "edge_grid": EDGE_GRID, "min_inner_bets": MIN_INNER_BETS,
                         "fixed_edge": FIXED_EDGE},
           "primary_rule": primary_rows.__doc__.strip(), "rounds": {}}
    for rnd in a.rounds.split(","):
        spec = ROUNDS[rnd]
        nv = len(spec["variants"])
        R = {"spec": {"configs": spec["configs"], "n_configs": len(spec["configs"]), "variants": spec["variants"],
                      "n_lambdas": len(LAMBDAS),
                      "n_candidates_per_block_primary": len(spec["configs"]) * len(LAMBDAS) * nv + 1,
                      "inner_folds": spec["folds"], "inner_chunk_frac": spec["frac"],
                      "primary_selector": spec["primary"]}, "runs": {}}
        for name in spec["runs"]:
            if a.runs and name not in a.runs.split(","):
                continue
            ev, pools, blocks = runs[name]
            o, pb = run_protocol(pools, ev, blocks, spec, f"{rnd}/{name}")
            sel = {}
            for rule in ("min", "1se", f"ens{ENS_K}"):
                sel[rule] = evaluate(o, a.boot, "p_" + rule, "edge_" + rule)
            r = {"blocks": [(b[0], pd.Timestamp(b[1], unit="s", tz="UTC").strftime("%Y-%m-%d")) for b in blocks],
                 "blocks_detail": pb, "selectors": sel, "primary": sel[spec["primary"]],
                 "per_config": config_table(o, a.boot),
                 "selected_min_counts": pd.Series([str(b["selectors"]["min"]["keys"][0]) for b in pb])
                 .value_counts().to_dict()}
            R["runs"][name] = r
            o.to_pickle(os.path.join(os.path.dirname(a.out), f"oos_glm-all_{rnd}_{name}.pkl"))
            g = r["primary"]
            print(f"== {rnd}/{name} [{spec['primary']}]: n={g['n']} blocks={g['n_blocks']} market {g['ll_market']} "
                  f"model {g['ll_model']} gain {g['gain']:+.5f} {g['gain_ci']} pos {g['blocks_positive']} "
                  f"slope {g['calib_slope']} PASS {g['passes']}", flush=True)
            for rule, gg in sel.items():
                print(f"     {rule}: gain {gg['gain']:+.5f} {gg['gain_ci']} pos {gg['blocks_positive']} "
                      f"slope {gg['calib_slope']} roi {gg['roi_inner_threshold'].get('roi')} "
                      f"bets {gg['roi_inner_threshold']['bets']}", flush=True)
        res["rounds"][rnd] = R
        with open(a.out, "w") as fh:
            json.dump(res, fh, indent=1, default=_json_default)
    res["secs"] = round(time.time() - t0, 1)
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1, default=_json_default)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
