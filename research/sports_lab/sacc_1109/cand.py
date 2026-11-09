"""sacc-1109 candidate projection layers on top of the frozen sm-1 distribution, walk-forward.

Candidates (each one is a change to the PROJECTION DISTRIBUTION, fitted on box-score outcomes only - never on book
lines or on settled props):
  rs-1  role spread: per-player multiplicative role/minutes uncertainty. The sm-1 PMF is mixed over a mean multiplier
        r ~ lognormal(-s^2/2, s^2) with s = exp(theta . x) from pre-game role inputs (minutes volatility, bench, minutes
        level, thin history, early season, teammates out, blowout risk, long layoff). s -> 0 is sm-1 exactly.
  rm-1  role mean: the sm-1 mean times exp(gamma . z) from pre-game minutes-trend inputs (last game / last 3 vs the EW
        minutes, teammates out, starter change, back-to-back, blowout risk, early season), Poisson deviance.
  rsm-1 both (mixture centred on the rm-1 multiplier).
The remap X -> r X moves each support point to r x and splits its mass between the two neighbouring integers (a
linear map per r, the same for every row), so the mixture is PMF @ sum_j w_j M_{r_j}.

Protocol: monthly walk-forward windows (sacc_1109.wf output). The layer used for window w is fitted on the
out-of-sample sm-1 predictions of all EARLIER windows only (expanding). The final window (2026-10-24 .. 11-07, the
book-line window) uses the frozen live artifact's predictions and a layer fitted on every dev window (nothing from
the final window or from any book line).

  python -m research.sports_lab.sacc_1109.cand <wf_dir> <frame.pkl> <lines.pkl> <out_dir>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

MARKETS = ("points", "rebounds", "assists", "threes")
LOGR = np.round(np.arange(-1.2, 1.2001, 0.05), 4)       # mixture grid of log multipliers (0 included)
EVAL_FROM = "2025-01-01"                                 # first dev window scored (earlier ones = warm-up)
PNF_COLS = ("min_sd10", "season_n", "days_since_played", "team_changed", "min_last", "min_l3", "b2b", "out_min",
            "usage_share_out")


# ------------------------------------------------------------------------------------------------------------ data
def load(wf_dir, frame_pkl, mk):
    parts = []
    for p in sorted(Path(wf_dir).glob(f"sm1_{mk}__dev_*.npz")) + sorted(Path(wf_dir).glob(f"sm1frozen_{mk}__final_*.npz")):
        z = np.load(p)
        w = p.stem.split("__")[1]
        d = pd.DataFrame({k[2:]: z[k] for k in z.files if k.startswith("f_")})
        d["key"], d["y"], d["mean"], d["elig"], d["window"] = z["keys"], z["y"], z["mean"], z["elig"], w
        d["_pmf"] = list(z["pmf"])
        lo = int(z["lo"])
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    f = pd.read_pickle(frame_pkl)
    f["key"] = f["game_id"] + "|" + f["player_id"]
    f = f.drop_duplicates("key").set_index("key")[list(PNF_COLS)]
    d = d.join(f, on="key")
    P = np.stack(d.pop("_pmf").to_numpy()).astype(np.float64)
    P /= P.sum(1, keepdims=True)
    return d, P, lo


def sigma_X(d):
    ew = np.maximum(np.nan_to_num(d["ew_min"].to_numpy(float), nan=10.0), 1.0)
    cv = np.clip(np.nan_to_num(d["min_sd10"].to_numpy(float), nan=6.0) / ew, 0.05, 2.0)
    bench = 1.0 - np.nan_to_num(d["starter_lag"].to_numpy(float), nan=0.5)
    pn = np.maximum(np.nan_to_num(d["prior_n"].to_numpy(float), nan=1.0), 1.0)
    early = (np.nan_to_num(d["season_n"].to_numpy(float), nan=0.0) <= 3).astype(float)
    absent = np.clip(np.nan_to_num(d["absent_share"].to_numpy(float), nan=0.1), 0, 1)
    blow = np.nan_to_num(d["spread_abs"].to_numpy(float), nan=6.0) / 10.0
    layoff = (np.nan_to_num(d["days_since_played"].to_numpy(float), nan=2.0) > 7).astype(float)
    trend = np.abs(np.log((np.nan_to_num(d["l5_min"].to_numpy(float), nan=ew) + 1) /
                          (np.nan_to_num(d["l10_min"].to_numpy(float), nan=ew) + 1)))
    return np.column_stack([np.ones(len(d)), np.log(cv), bench, np.log(ew / 25.0), 1 / np.sqrt(pn), early, absent,
                            blow, layoff, trend])


SIGMA_NAMES = ["const", "log_min_cv", "bench", "log_ew_min", "inv_sqrt_prior_n", "early_season", "teammates_out",
               "abs_spread", "layoff_7d", "abs_min_trend"]


def mean_Z(d):
    ew = np.maximum(np.nan_to_num(d["ew_min"].to_numpy(float), nan=10.0), 1.0)
    last = np.nan_to_num(d["min_last"].to_numpy(float), nan=ew)
    l3 = np.nan_to_num(d["min_l3"].to_numpy(float), nan=ew)
    l10 = np.nan_to_num(d["l10_min"].to_numpy(float), nan=ew)
    st = np.nan_to_num(d["starter_lag"].to_numpy(float), nan=0.5)
    sr = np.nan_to_num(d["starter_rate10"].to_numpy(float), nan=0.5)
    absent = np.clip(np.nan_to_num(d["absent_share"].to_numpy(float), nan=0.1), 0, 1)
    b2b = np.nan_to_num(d["b2b"].to_numpy(float), nan=0.0)
    blow = np.nan_to_num(d["spread_abs"].to_numpy(float), nan=6.0) / 10.0
    early = (np.nan_to_num(d["season_n"].to_numpy(float), nan=0.0) <= 3).astype(float)
    pn = np.maximum(np.nan_to_num(d["prior_n"].to_numpy(float), nan=1.0), 1.0)
    return np.column_stack([np.ones(len(d)), np.log((last + 1) / (ew + 1)), np.log((l3 + 1) / (ew + 1)),
                            np.log((l10 + 1) / (ew + 1)), st - sr, absent, b2b, blow, early, 1 / np.sqrt(pn),
                            np.log(ew / 25.0)])


MEAN_NAMES = ["const", "log_last_vs_ew", "log_l3_vs_ew", "log_l10_vs_ew", "starter_change", "teammates_out", "b2b",
              "abs_spread", "early_season", "inv_sqrt_prior_n", "log_ew_min"]


def sigma_X_frame(d):
    """rs-1f: the same idea from the LIVE forward frame's own columns only (featurize.nba_frame: ew_min, l5/l10
    minutes, starter_lag, starter_rate10, prior_n, absent_share, spread_abs) - what production can compute without
    adding a column to the frame. Defined before any line score of any arm was seen."""
    ew = np.maximum(np.nan_to_num(d["ew_min"].to_numpy(float), nan=10.0), 1.0)
    l5 = np.nan_to_num(d["l5_min"].to_numpy(float), nan=ew)
    l10 = np.nan_to_num(d["l10_min"].to_numpy(float), nan=ew)
    st = np.nan_to_num(d["starter_lag"].to_numpy(float), nan=0.5)
    sr = np.nan_to_num(d["starter_rate10"].to_numpy(float), nan=0.5)
    pn = np.maximum(np.nan_to_num(d["prior_n"].to_numpy(float), nan=1.0), 1.0)
    absent = np.clip(np.nan_to_num(d["absent_share"].to_numpy(float), nan=0.1), 0, 1)
    blow = np.nan_to_num(d["spread_abs"].to_numpy(float), nan=6.0) / 10.0
    return np.column_stack([np.ones(len(d)), np.abs(np.log((l5 + 1) / (l10 + 1))), 1.0 - st, 4 * sr * (1 - sr),
                            np.log(ew / 25.0), 1 / np.sqrt(pn), absent, blow, np.abs(np.log((l10 + 1) / (ew + 1)))])


SIGMA_NAMES_FRAME = ["const", "abs_log_l5_vs_l10_min", "bench", "starter_instability", "log_ew_min",
                     "inv_sqrt_prior_n", "teammates_out", "abs_spread", "abs_log_l10_vs_ew_min"]


def mean_Z_frame(d):
    ew = np.maximum(np.nan_to_num(d["ew_min"].to_numpy(float), nan=10.0), 1.0)
    l5 = np.nan_to_num(d["l5_min"].to_numpy(float), nan=ew)
    l10 = np.nan_to_num(d["l10_min"].to_numpy(float), nan=ew)
    st = np.nan_to_num(d["starter_lag"].to_numpy(float), nan=0.5)
    sr = np.nan_to_num(d["starter_rate10"].to_numpy(float), nan=0.5)
    pn = np.maximum(np.nan_to_num(d["prior_n"].to_numpy(float), nan=1.0), 1.0)
    absent = np.clip(np.nan_to_num(d["absent_share"].to_numpy(float), nan=0.1), 0, 1)
    blow = np.nan_to_num(d["spread_abs"].to_numpy(float), nan=6.0) / 10.0
    rest = (np.nan_to_num(d["rest_days"].to_numpy(float), nan=2.0) <= 1.5).astype(float)
    return np.column_stack([np.ones(len(d)), np.log((l5 + 1) / (ew + 1)), np.log((l10 + 1) / (ew + 1)), st - sr,
                            absent, blow, 1 / np.sqrt(pn), np.log(ew / 25.0), rest])


MEAN_NAMES_FRAME = ["const", "log_l5_vs_ew_min", "log_l10_vs_ew_min", "starter_change", "teammates_out", "abs_spread",
                    "inv_sqrt_prior_n", "log_ew_min", "team_b2b"]

if __import__("os").environ.get("SACC_VARIANT") == "frame":
    sigma_X, mean_Z, SIGMA_NAMES, MEAN_NAMES = sigma_X_frame, mean_Z_frame, SIGMA_NAMES_FRAME, MEAN_NAMES_FRAME


# ----------------------------------------------------------------------------------------------------------- remap
def remap_matrix(K, lo, r):
    """M[x, k]: share of the mass at support x that lands on support k when x -> r x (clipped to the grid)."""
    ks = lo + np.arange(K, dtype=float)
    pos = np.clip(r * ks - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    M = np.zeros((K, K))
    M[np.arange(K), f] += 1 - w
    M[np.arange(K), np.minimum(f + 1, K - 1)] += w
    return M


def remap_rows(P, lo, r):
    """Each row i moved by its own multiplier r[i] (vectorised: x -> r_i x)."""
    n, K = P.shape
    ks = lo + np.arange(K, dtype=float)
    pos = np.clip(r[:, None] * ks[None, :] - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    base = (np.arange(n) * K)[:, None]
    out = np.bincount((base + f).ravel(), weights=(P * (1 - w)).ravel(), minlength=n * K)
    out += np.bincount((base + np.minimum(f + 1, K - 1)).ravel(), weights=(P * w).ravel(), minlength=n * K)
    return out.reshape(n, K)


def mix_weights(sig):
    """rows x J lognormal weights on LOGR with mean multiplier 1 (log r ~ N(-s^2/2, s^2)); s ~ 0 -> point mass at 0."""
    s = np.maximum(sig, 1e-3)[:, None]
    z = (LOGR[None, :] + s ** 2 / 2) / s
    w = np.exp(-0.5 * z ** 2)
    w[:, LOGR == 0] += 1e-12
    return w / w.sum(1, keepdims=True)


def lik_matrix(P, lo, y, shift=None):
    """L[i, j] = P(Y = y_i) after the remap by exp(LOGR_j) (times the row's mean multiplier when shift is given)."""
    n, K = P.shape
    yi = np.clip(np.rint(y).astype(int) - lo, 0, K - 1)
    L = np.zeros((n, len(LOGR)))
    for j, lr in enumerate(LOGR):
        if shift is None:
            M = remap_matrix(K, lo, float(np.exp(lr)))
            L[:, j] = (P * M[:, yi].T).sum(1)
        else:
            L[:, j] = remap_rows(P, lo, np.exp(lr) * shift)[np.arange(n), yi]
    return np.maximum(L, 1e-9)


def apply_mix(P, lo, sig, shift=None, chunk=4000):
    n, K = P.shape
    W = mix_weights(sig)
    out = np.zeros_like(P)
    if shift is None:
        for j, lr in enumerate(LOGR):
            if W[:, j].max() < 1e-7:
                continue
            out += W[:, j:j + 1] * (P @ remap_matrix(K, lo, float(np.exp(lr))))
        return out
    for j, lr in enumerate(LOGR):
        if W[:, j].max() < 1e-7:
            continue
        out += W[:, j:j + 1] * remap_rows(P, lo, np.exp(lr) * shift)
    return out


# ------------------------------------------------------------------------------------------------------------- fit
MAX_FIT_ROWS = 40000


def _sub(n, cap=MAX_FIT_ROWS, seed=1109):
    """Fixed-seed subsample of the training rows (speed only; same rows for every arm of a window)."""
    if n <= cap:
        return np.arange(n)
    return np.sort(np.random.default_rng(seed).choice(n, cap, replace=False))


def fit_sigma(L, X, ridge=1e-3):
    """Maximum likelihood of theta (s = exp(theta . x)) with the analytic gradient."""
    ii = _sub(len(L))
    L, X = L[ii], X[ii]
    lr = LOGR[None, :]

    def fg(th):
        eta = X @ th
        clipped = (eta < -8) | (eta > 1.0)
        s = np.exp(np.clip(eta, -8, 1.0))[:, None]
        s = np.maximum(s, 1e-3)
        z = (lr + s ** 2 / 2) / s
        u = np.exp(-0.5 * z ** 2)
        u[:, LOGR == 0] += 1e-12
        w = u / u.sum(1, keepdims=True)
        m = (w * L).sum(1)
        a = -z * (-lr / s ** 2 + 0.5)                       # d log u_j / ds
        dw = w * (a - (w * a).sum(1, keepdims=True))         # d w_j / ds
        dm_ds = (dw * L).sum(1)
        g_eta = np.where(clipped, 0.0, dm_ds / m * s[:, 0])  # ds/deta = s
        f = -np.log(m).mean() + ridge * (th[1:] ** 2).sum()
        g = -(X.T @ g_eta) / len(m)
        g[1:] += 2 * ridge * th[1:]
        return f, g
    th0 = np.zeros(X.shape[1])
    th0[0] = np.log(0.15)
    res = minimize(fg, th0, jac=True, method="L-BFGS-B", options={"maxiter": 300})
    return res.x


def fit_mean(y, mu, Z, ridge=1e-2):
    """Poisson deviance with offset log(mu): E[y] = mu exp(gamma . z)."""
    def f(g):
        eta = np.clip(Z @ g, -2, 2)
        m = mu * np.exp(eta)
        return (m - y * np.log(m)).mean() + ridge * (g[1:] ** 2).sum()

    def grad(g):
        eta = np.clip(Z @ g, -2, 2)
        m = mu * np.exp(eta)
        gr = Z.T @ (m - y) / len(y)
        gr[1:] += 2 * ridge * g[1:]
        return gr
    return minimize(f, np.zeros(Z.shape[1]), jac=grad, method="L-BFGS-B").x


# ----------------------------------------------------------------------------------------------------------- score
def scores(P, lo, y, line):
    n, K = P.shape
    ks = lo + np.arange(K, dtype=float)
    yi = np.clip(np.rint(y).astype(int) - lo, 0, K - 1)
    ls = np.log(np.maximum(P[np.arange(n), yi], 1e-6))
    mean = P @ ks
    cdf = np.cumsum(P, 1)
    lo_c = np.where(yi > 0, cdf[np.arange(n), np.maximum(yi - 1, 0)], 0.0)
    hi_c = cdf[np.arange(n), yi]
    pit_in = ((lo_c < 0.9) & (hi_c > 0.1)).astype(float)
    po = (P * (ks[None, :] > line[:, None])).sum(1)
    pu = (P * (ks[None, :] < line[:, None])).sum(1)
    pov = po / np.maximum(po + pu, 1e-12)
    return {"ls": ls, "ae": np.abs(mean - y), "err": mean - y, "cov80": pit_in, "po": pov, "mean": mean}


class Boot:
    def __init__(self, games, b=1000, seed=1109):
        self.ug, self.g = np.unique(np.asarray(games), return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)

    def ci(self, x, w=None):
        x = np.asarray(x, float)
        w = np.ones(len(x)) if w is None else np.asarray(w, float)
        s = self.C @ np.bincount(self.g, weights=x * w, minlength=len(self.ug))
        n = self.C @ np.bincount(self.g, weights=w, minlength=len(self.ug))
        v = s / np.maximum(n, 1e-9)
        return [round(float(np.percentile(v, 2.5)), 5), round(float(np.percentile(v, 97.5)), 5)]


# ------------------------------------------------------------------------------------------------------------- run
def walk(d, P, lo):
    """-> dict cand -> PMF for every row (baseline rows untouched), fitted params per window."""
    wins = sorted(d["window"].unique(), key=lambda w: w.split("_", 1)[1])
    out = {c: P.copy() for c in ("rs1", "rm1", "rsm1")}
    params = {}
    y, mu = d["y"].to_numpy(float), d["mean"].to_numpy(float)
    X, Z = sigma_X(d), mean_Z(d)
    L_all = lik_matrix(P, lo, y)
    for w in wins:
        if w.startswith("dev_") and w.split("_", 1)[1] < EVAL_FROM:
            continue
        if w.startswith("final_"):
            tr = d["window"].str.startswith("dev_").to_numpy()
        else:
            tr = (d["window"].str.startswith("dev_") & (d["window"].str.split("_", n=1).str[1] < w.split("_", 1)[1])).to_numpy()
        te = (d["window"] == w).to_numpy()
        # rs-1
        th = fit_sigma(L_all[tr], X[tr])
        sig = np.exp(np.clip(X[te] @ th, -8, 1.0))
        out["rs1"][te] = apply_mix(P[te], lo, sig)
        # rm-1
        g = fit_mean(y[tr], np.maximum(mu[tr], 0.05), Z[tr])
        sh = np.exp(np.clip(Z[te] @ g, -2, 2))
        out["rm1"][te] = remap_rows(P[te], lo, sh)
        # rsm-1 (sigma refit around the rm-1 shift on the training rows)
        tri = np.flatnonzero(tr)[_sub(int(tr.sum()))]
        sh_tr = np.exp(np.clip(Z[tri] @ g, -2, 2))
        th2 = fit_sigma(lik_matrix(P[tri], lo, y[tri], shift=sh_tr), X[tri])
        sig2 = np.exp(np.clip(X[te] @ th2, -8, 1.0))
        out["rsm1"][te] = apply_mix(P[te], lo, sig2, shift=sh)
        params[w] = {"rs1_theta": dict(zip(SIGMA_NAMES, np.round(th, 4).tolist())),
                     "rs1_sigma_test_median": round(float(np.median(sig)), 4),
                     "rm1_gamma": dict(zip(MEAN_NAMES, np.round(g, 4).tolist())),
                     "rsm1_theta": dict(zip(SIGMA_NAMES, np.round(th2, 4).tolist())),
                     "n_train": int(tr.sum()), "n_test": int(te.sum())}
        print(w, params[w]["n_train"], params[w]["n_test"], "sig med", params[w]["rs1_sigma_test_median"], flush=True)
    return out, params


def all_outcome_report(d, P, lo, C, mask, games):
    y = d["y"].to_numpy(float)
    line = np.floor(d["mean"].to_numpy(float)) + 0.5           # reference threshold (same for every arm)
    base = scores(P[mask], lo, y[mask], line[mask])
    bt = Boot(games[mask])
    rep = {"n": int(mask.sum()), "games": int(len(np.unique(games[mask]))),
           "base": {"logscore": round(float(base["ls"].mean()), 5), "mae": round(float(base["ae"].mean()), 4),
                    "bias": round(float(base["err"].mean()), 4), "cov80": round(float(base["cov80"].mean()), 4)}}
    for c, Q in C.items():
        s = scores(Q[mask], lo, y[mask], line[mask])
        dls = s["ls"] - base["ls"]
        rep[c] = {"logscore": round(float(s["ls"].mean()), 5), "d_logscore": round(float(dls.mean()), 5),
                  "d_logscore_ci": bt.ci(dls), "mae": round(float(s["ae"].mean()), 4),
                  "d_mae_ci": bt.ci(s["ae"] - base["ae"]), "bias": round(float(s["err"].mean()), 4),
                  "cov80": round(float(s["cov80"].mean()), 4)}
    return rep


def main(wf_dir, frame_pkl, lines_pkl, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = pd.read_pickle(lines_pkl)
    report = {"protocol": __doc__.strip().splitlines()[0], "eval_from": EVAL_FROM, "markets": {}}
    import os
    for mk in (os.environ.get("SACC_MARKETS") or ",".join(MARKETS)).split(","):
        t0 = time.time()
        d, P, lo = load(wf_dir, frame_pkl, mk)
        C, params = walk(d, P, lo)
        games = d["key"].str.split("|").str[0].to_numpy()
        dev = (d["window"].str.startswith("dev_") & (d["window"].str.split("_", n=1).str[1] >= EVAL_FROM)).to_numpy()
        fin = d["window"].str.startswith("final_").to_numpy()
        el = d["elig"].to_numpy(bool)
        rep = {"params": params,
               "dev_all": all_outcome_report(d, P, lo, C, dev, games),
               "dev_eligible": all_outcome_report(d, P, lo, C, dev & el, games),
               "dev_not_eligible": all_outcome_report(d, P, lo, C, dev & ~el, games),
               "final_all": all_outcome_report(d, P, lo, C, fin, games)}
        # per-row line probabilities for the book-line rows (final window)
        lm = lines[lines["market"] == mk]
        idx = pd.Series(np.flatnonzero(fin), index=d.loc[fin, "key"].to_numpy())
        idx = idx[~idx.index.duplicated()]
        have = lm["key"].isin(idx.index).to_numpy()
        lm = lm[have].copy()
        ii = idx.loc[lm["key"]].to_numpy()
        ks = lo + np.arange(P.shape[1], dtype=float)
        L = lm["line"].to_numpy(float)[:, None]
        for nm, Q in [("base", P)] + list(C.items()):
            q = Q[ii]
            po, pu = (q * (ks[None, :] > L)).sum(1), (q * (ks[None, :] < L)).sum(1)
            lm[f"po_{nm}"] = po / np.maximum(po + pu, 1e-12)
            lm[f"mean_{nm}"] = q @ ks
        lm.to_pickle(out_dir / f"lines_{mk}.pkl")
        rep["book_rows_matched"] = int(len(lm))
        report["markets"][mk] = rep
        (out_dir / f"report_{mk}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
        print(mk, "done", round(time.time() - t0), json.dumps({k: rep[k] for k in ("dev_all", "final_all")})[:1500],
              flush=True)
    (out_dir / "cand_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:5])
