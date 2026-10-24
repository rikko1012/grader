"""sm-1: opportunity x rate with shrinkage, fitted feature effects, fitted distribution families, cal-1 (numpy, worker).

Every number below that shapes a forecast is FITTED (train: priors, shrinkage k, GLM beta, dispersion, residual
distributions; tune: half-lives, feature set, family, calibration) - nothing is hand-set except the registered grids.
REGISTER.md sections 3-5 are the specification; this file is the implementation.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np

from . import dists

SPEC = {
    # sport, kind, opportunity column (frame), efficiency stat (per opportunity), mixture design
    "points": {"sport": "nba", "kind": "count", "stat": "pts", "mix": "minutes"},
    "rebounds": {"sport": "nba", "kind": "count", "stat": "reb", "mix": "minutes"},
    "assists": {"sport": "nba", "kind": "count", "stat": "ast", "mix": "minutes"},
    "threes": {"sport": "nba", "kind": "count", "stat": "fg3m", "mix": "threes"},
    "rec": {"sport": "nfl", "kind": "count", "opp": "targets", "eff": "rec", "mix": "binom"},
    "rush_att": {"sport": "nfl", "kind": "count", "opp": "carries", "eff": None, "mix": None},
    "rec_yds": {"sport": "nfl", "kind": "yards", "opp": "targets", "eff": "rec_yds", "mix": "rec_yards"},
    "rush_yds": {"sport": "nfl", "kind": "yards", "opp": "carries", "eff": "rush_yds", "mix": "unit_yards"},
    "pass_yds": {"sport": "nfl", "kind": "yards", "opp": "pass_att", "eff": "pass_yds", "mix": "pass_yards"},
}
FAMILIES = {"count": ("poisson", "negbin", "mixture", "ratio"), "yards": ("normal", "ratio", "mixture")}
K_GRID = (0.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0)


# ========================================================================================================= eligibility
def eligible(df, market):
    sp = SPEC[market]
    if sp["sport"] == "nba":
        m = (df["prior_n"] >= 5) & (df["l10_min"] >= 15)
    else:
        m = df["prior_n"] >= 3
        if market == "pass_yds":
            m &= (df["position"] == "QB") & (df["l6_pass_att"] >= 15)
        elif market in ("rush_yds", "rush_att"):
            m &= df["l6_carries"] >= 6
        else:
            m &= df["l6_targets"] >= 3
    return m.fillna(False).to_numpy()


def partition(df, sport):
    """'train' | 'tune' | 'test' | 'test2' | 'refit_only' per row (REGISTER.md 4)."""
    s = df["start"]
    if sport == "nba":
        out = np.where(s < "2025-10-01", "train", np.where(s < "2026-02-01", "tune",
                       np.where(s < "2026-07-01", "test", "refit_only")))
    else:
        se, wk = df["season"].to_numpy(), df["week"].to_numpy()
        out = np.where(se <= 2024, "train", np.where((se == 2025) & (wk <= 9), "tune",
                       np.where(se == 2025, "test", "test2")))
    return np.asarray(out)


# ============================================================================================================ base mean
def base_inputs(df, market, prior, k):
    """-> (mu0, opportunity expectation, efficiency) for the base model with shrinkage strength k."""
    sp = SPEC[market]
    if sp["sport"] == "nba":
        st = sp["stat"]
        r_pos = df["pos_g"].map(prior["rate_pos"]).to_numpy(float)
        rate = (np.nan_to_num(df[f"S_{st}"].to_numpy(float)) + k * r_pos) / \
            (np.nan_to_num(df["S_min"].to_numpy(float)) + k + 1e-9)
        e_opp = df["ew_min"].to_numpy(float)
        return e_opp * rate, e_opp, rate
    o, e = sp["opp"], sp["eff"]
    e_opp = df[f"ew_{o}"].to_numpy(float)
    if e is None:
        return e_opp, e_opp, np.ones(len(df))
    e_pos = df["position"].map(prior["eff_pos"]).to_numpy(float)
    eff = (np.nan_to_num(df[f"S_{e}"].to_numpy(float)) + k * e_pos) / \
        (np.nan_to_num(df[f"S_{o}"].to_numpy(float)) + k + 1e-9)
    return e_opp * eff, e_opp, eff


def fit_priors(tr, market):
    sp = SPEC[market]
    if sp["sport"] == "nba":
        g = tr.groupby("pos_g")
        return {"rate_pos": {p: float(v[sp["stat"]].sum() / max(v["min"].sum(), 1)) for p, v in g}}
    if sp["eff"] is None:
        return {"eff_pos": {}}
    g = tr.groupby("position")
    return {"eff_pos": {p: float(v[sp["eff"]].sum() / max(v[sp["opp"]].sum(), 1)) for p, v in g}}


def poisson_dev(y, mu):
    mu = np.maximum(mu, 1e-6)
    y = np.maximum(y, 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(np.mean(2 * (t - (y - mu))))


def fit_k(tr, market, prior):
    y = tr[f"y_{market}"].to_numpy(float)
    best = None
    for k in K_GRID:
        mu, _, _ = base_inputs(tr, market, prior, k)
        ok = np.isfinite(mu)
        d = poisson_dev(y[ok], mu[ok]) if SPEC[market]["kind"] == "count" else float(np.mean((y[ok] - mu[ok]) ** 2))
        if best is None or d < best[1]:
            best = (k, d)
    return best[0]


# ================================================================================================================= GLM
def design(df, feats, stats=None):
    """Standardised feature matrix; missing -> 0 (the train mean) plus a missing indicator when missing on train > 1%.
    stats (train means / sds / indicator flags) are fitted on train only and reused on tune / test."""
    cols, fit = [], stats is None
    stats = stats or {}
    for f in feats:
        x = df[f].to_numpy(float)
        if fit:
            ok = np.isfinite(x)
            stats[f] = {"mean": float(np.mean(x[ok])) if ok.any() else 0.0,
                        "sd": float(np.std(x[ok])) if ok.sum() > 1 and np.std(x[ok]) > 0 else 1.0,
                        "ind": bool((~ok).mean() > 0.01)}
        s = stats[f]
        z = (x - s["mean"]) / s["sd"]
        miss = ~np.isfinite(z)
        cols.append(np.where(miss, 0.0, z))
        if s["ind"]:
            cols.append(miss.astype(float))
    X = np.column_stack(cols) if cols else np.zeros((len(df), 0))
    return X, stats


def fit_glm(X, y, offset, ridge=1e-4, iters=50):
    """Poisson / quasi-Poisson log-link IRLS with the base mean as offset: mu = exp(offset + X beta)."""
    n, p = X.shape
    if p == 0:
        return np.zeros(0)
    beta = np.zeros(p)
    for _ in range(iters):
        eta = np.clip(offset + X @ beta, -20, 20)
        mu = np.exp(eta)
        z = (eta - offset) + (y - mu) / mu
        W = mu
        A = X.T @ (X * W[:, None]) + ridge * np.eye(p) * max(1.0, W.mean())
        b = X.T @ (W * z)
        new = np.linalg.solve(A, b)
        if np.max(np.abs(new - beta)) < 1e-7:
            beta = new
            break
        beta = new
    return beta


# =========================================================================================================== families
def _nb_ll(y, mu, a):
    r = 1.0 / a
    lg = np.vectorize(math.lgamma, otypes=[float]) if not _HAS_SCIPY else _gammaln
    return float(np.sum(lg(y + r) - lg(r) - lg(y + 1) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))))


try:
    from scipy.special import gammaln as _gammaln
    _HAS_SCIPY = True
except ImportError:                                     # pragma: no cover - production image has no scipy
    _HAS_SCIPY = False
    _gammaln = None


def fit_alpha(y, mu):
    """NB alpha by maximum likelihood (log grid, then golden-section refine), var = mu + alpha mu^2."""
    y = np.maximum(np.rint(np.asarray(y, float)), 0)
    mu = np.maximum(np.asarray(mu, float), 1e-4)
    grid = np.exp(np.linspace(np.log(1e-4), np.log(3.0), 60))
    lls = [_nb_ll(y, mu, a) for a in grid]
    i = int(np.argmax(lls))
    lo, hi = grid[max(0, i - 1)], grid[min(len(grid) - 1, i + 1)]
    for _ in range(30):                                  # golden-section refine
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if _nb_ll(y, mu, m1) > _nb_ll(y, mu, m2):
            hi = m2
        else:
            lo = m1
    return float((lo + hi) / 2)


def fit_normal_var(y, mu):
    """(a, b) of var = a mu + b mu^2 by least squares of (y - mu)^2 (b < 0 -> refit with b = 0)."""
    r2 = (y - mu) ** 2
    M = np.column_stack([mu, mu ** 2])
    a, b = np.linalg.lstsq(M, r2, rcond=None)[0]
    if b < 0 or a <= 0:
        a, b = float(np.sum(r2 * mu) / np.sum(mu * mu)), 0.0
    return float(max(a, 0.05)), float(max(b, 0.0))


def fit_ratio(y, mu):
    edges = np.quantile(mu, [0.2, 0.4, 0.6, 0.8])
    q = np.digitize(mu, edges)
    ratios = [np.asarray((y[q == j] / np.maximum(mu[q == j], 1e-6)), float) for j in range(5)]
    return {"edges": [float(e) for e in edges], "ratios": [np.quantile(r, np.linspace(0.0025, 0.9975, 200)).tolist()
                                                           for r in ratios]}


def ratio_pmf(mu, par, lo, ks):
    q = np.digitize(mu, np.asarray(par["edges"]))
    rs = [np.asarray(par["ratios"][j]) for j in q]
    return dists.ratio(mu, rs, lo, ks)


def _quantile_points(x, n):
    """n equal-probability bin midpoints of the empirical distribution of x."""
    return np.quantile(x, (np.arange(n) + 0.5) / n)


def _binom_pmf_matrix(nmax, p, rmax):
    """P(R = r | N = n) for n = 0..nmax, r = 0..rmax, per row p -> (rows, nmax+1, rmax+1)."""
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)[:, None, None]
    n = np.arange(nmax + 1)[None, :, None]
    r = np.arange(rmax + 1)[None, None, :]
    lc = (_lg(n + 1) - _lg(r + 1) - _lg(np.maximum(n - r, 0) + 1))
    out = np.where(r <= n, np.exp(lc + r * np.log(p) + (n - r) * np.log(1 - p)), 0.0)
    return out


def _lg(x):
    return _gammaln(x) if _HAS_SCIPY else np.vectorize(math.lgamma, otypes=[float])(x)


def _nb_counts(mu, alpha, nmax):
    ks = np.arange(nmax + 1, dtype=float)
    return dists.negbin(mu, alpha, 0, ks)


def _yards_given_units(units_pmf, unit_mean, sigma, e_sorted, lo, ks, chunk=256):
    """Yards PMF = sum_n P(N=n) * P(yards | n), yards | n = n*unit_mean + sqrt(n)*sigma*e, e ~ empirical (train)."""
    rows, nmax1 = units_pmf.shape
    out = np.zeros((rows, len(ks)))
    cdf_x = np.asarray(e_sorted, float)
    cdf_y = (np.arange(len(cdf_x)) + 0.5) / len(cdf_x)
    zero = int(0 - lo)
    for a in range(0, rows, chunk):
        b = min(rows, a + chunk)
        up = ks[None, None, :] + 0.5
        n = np.arange(1, nmax1)[None, :, None].astype(float)
        m = (n * unit_mean[a:b, None, None])
        sd = np.sqrt(n) * sigma
        F_up = np.interp(((up - m) / sd).ravel(), cdf_x, cdf_y, left=0.0, right=1.0).reshape(b - a, nmax1 - 1, len(ks))
        P = np.diff(np.concatenate([np.zeros((b - a, nmax1 - 1, 1)), F_up], axis=2), axis=2)
        P[:, :, -1] += 1 - F_up[:, :, -1]
        out[a:b] = np.einsum("rn,rnk->rk", units_pmf[a:b, 1:], P)
        out[a:b, zero] += units_pmf[a:b, 0]
    return out / np.maximum(out.sum(axis=1, keepdims=True), 1e-12)


class MixFit:
    """Fitted pieces of the opportunity-mixture family (train only)."""

    @staticmethod
    def fit(tr, market, mu_tr, ctx_tr):
        sp = SPEC[market]
        y = tr[f"y_{market}"].to_numpy(float)
        if sp["mix"] in ("minutes",):
            rho = (tr["min"].to_numpy(float) / np.maximum(tr["ew_min"].to_numpy(float), 1))
            q = _quantile_points(rho, 40)
            q = q / q.mean()
            a_c = fit_alpha(y, mu_tr * rho / np.mean(rho))
            return {"rho": q.tolist(), "alpha_c": a_c}
        if sp["mix"] == "threes":
            rho = (tr["min"].to_numpy(float) / np.maximum(tr["ew_min"].to_numpy(float), 1))
            q = _quantile_points(rho, 40)
            q = q / q.mean()
            p3 = ctx_tr["p3"]
            a3 = (mu_tr / np.maximum(p3, 1e-3)) * rho / np.mean(rho)
            alpha_3a = fit_alpha(tr["fg3a"].to_numpy(float), np.maximum(a3, 1e-3))
            return {"rho": q.tolist(), "alpha_3a": alpha_3a}
        opp = tr[sp["opp"]].to_numpy(float)
        e_opp = np.maximum(ctx_tr["e_opp"], 1e-3)
        alpha_o = fit_alpha(opp, e_opp)
        out = {"alpha_opp": alpha_o}
        if sp["mix"] == "binom":
            return out
        units = {"rec_yards": "rec", "pass_yards": "pass_cmp", "unit_yards": sp["opp"]}[sp["mix"]]
        n = tr[units].to_numpy(float)
        upm = ctx_tr["unit_mean"]
        ok = n > 0
        r = (y[ok] - n[ok] * upm[ok]) / np.sqrt(n[ok])
        sigma = float(np.std(r))
        out.update({"sigma": sigma, "e": np.sort(_quantile_points(r / sigma, 200)).tolist()})
        return out


def mix_context(df, market, mu, e_opp, eff, prior):
    """Per-row quantities the mixture needs, all pre-game (EB conversion rates)."""
    sp = SPEC[market]
    if sp["mix"] == "threes":
        p_pos = df["pos_g"].map(prior.get("p3_pos", {})).fillna(0.35).to_numpy(float)
        k3 = prior.get("k3", 100.0)
        p3 = (np.nan_to_num(df["S_fg3m"].to_numpy(float)) + k3 * p_pos) / \
            (np.nan_to_num(df["S_fg3a"].to_numpy(float)) + k3)
        return {"p3": p3}
    if sp["sport"] == "nba" or sp["mix"] is None:
        return {}
    ctx = {"e_opp": e_opp}
    if sp["mix"] == "binom":
        ctx["p"] = np.clip(mu / np.maximum(e_opp, 1e-3), 0.01, 0.99)
    elif sp["mix"] == "rec_yards":
        c_pos = df["position"].map(prior.get("catch_pos", {})).fillna(0.65).to_numpy(float)
        kc = prior.get("kc", 20.0)
        p = (np.nan_to_num(df["S_rec"].to_numpy(float)) + kc * c_pos) / \
            (np.nan_to_num(df["S_targets"].to_numpy(float)) + kc)
        ctx["p"] = np.clip(p, 0.05, 0.98)
        ctx["unit_mean"] = mu / np.maximum(e_opp * ctx["p"], 1e-3)
    elif sp["mix"] == "pass_yards":
        c_pos = prior.get("cmp_all", 0.64)
        kc = prior.get("kc", 60.0)
        p = (np.nan_to_num(df["S_pass_cmp"].to_numpy(float)) + kc * c_pos) / \
            (np.nan_to_num(df["S_pass_att"].to_numpy(float)) + kc)
        ctx["p"] = np.clip(p, 0.3, 0.9)
        ctx["unit_mean"] = mu / np.maximum(e_opp * ctx["p"], 1e-3)
    else:
        ctx["unit_mean"] = mu / np.maximum(e_opp, 1e-3)
    return ctx


def mixture_pmf(market, mu, ctx, mix, lo, ks):
    sp = SPEC[market]
    n = len(mu)
    if sp["mix"] == "minutes":
        rho = np.asarray(mix["rho"])
        out = np.zeros((n, len(ks)))
        for r in rho:
            out += dists.negbin(mu * r, mix["alpha_c"], lo, ks)
        return out / len(rho)
    if sp["mix"] == "threes":
        rho = np.asarray(mix["rho"])
        p3 = ctx["p3"]
        a_mean = mu / np.maximum(p3, 1e-3)
        nmax = 30
        att = np.zeros((n, nmax + 1))
        for r in rho:
            att += _nb_counts(a_mean * r, mix["alpha_3a"], nmax)
        att /= len(rho)
        B = _binom_pmf_matrix(nmax, p3, len(ks) - 1)
        return np.einsum("rn,rnk->rk", att, B)
    nmax = {"targets": 25, "carries": 45, "pass_att": 70}[sp["opp"]]
    T = _nb_counts(ctx["e_opp"], mix["alpha_opp"], nmax)
    if sp["mix"] == "binom":
        B = _binom_pmf_matrix(nmax, ctx["p"], len(ks) - 1)
        return np.einsum("rn,rnk->rk", T, B)
    if sp["mix"] in ("rec_yards", "pass_yards"):
        units = np.einsum("rn,rnk->rk", T, _binom_pmf_matrix(nmax, ctx["p"], nmax))
    else:
        units = T
    return _yards_given_units(units, ctx["unit_mean"], mix["sigma"], mix["e"], lo, ks)


def family_pmf(family, market, mu, ctx, par, lo, ks):
    mu = np.maximum(np.asarray(mu, float), 0.05)
    if family == "poisson":
        return dists.poisson(mu, lo, ks)
    if family == "negbin":
        return dists.negbin(mu, par["alpha"], lo, ks)
    if family == "normal":
        return dists.normal(mu, par["a"] * mu + par["b"] * mu * mu, lo, ks)
    if family == "ratio":
        return ratio_pmf(mu, par["ratio"], lo, ks)
    if family == "mixture":
        return mixture_pmf(market, mu, ctx, par["mix"], lo, ks)
    raise KeyError(family)


def fit_family_params(tr, market, mu_tr, ctx_tr):
    y = tr[f"y_{market}"].to_numpy(float)
    par = {"ratio": fit_ratio(y, np.maximum(mu_tr, 0.05))}
    if SPEC[market]["kind"] == "count":
        par["alpha"] = fit_alpha(y, mu_tr)
    else:
        par["a"], par["b"] = fit_normal_var(y, mu_tr)
    if SPEC[market]["mix"]:
        par["mix"] = MixFit.fit(tr, market, mu_tr, ctx_tr)
    return par


def fit_mix_priors(tr, market, prior):
    """EB priors the mixture's conversion rates shrink to (train)."""
    sp = SPEC[market]
    if sp["mix"] == "threes":
        g = tr.groupby("pos_g")
        prior["p3_pos"] = {p: float(v["fg3m"].sum() / max(v["fg3a"].sum(), 1)) for p, v in g}
        prior["k3"] = 100.0 if "k3" not in prior else prior["k3"]
    if sp["mix"] == "rec_yards":
        g = tr.groupby("position")
        prior["catch_pos"] = {p: float(v["rec"].sum() / max(v["targets"].sum(), 1)) for p, v in g}
        prior["kc"] = 20.0
    if sp["mix"] == "pass_yards":
        prior["cmp_all"] = float(tr["pass_cmp"].sum() / max(tr["pass_att"].sum(), 1))
        prior["kc"] = 60.0
    return prior


def artifact_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()[:16]
