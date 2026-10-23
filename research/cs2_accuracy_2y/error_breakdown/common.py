"""ED-1 shared helpers: live-model columns, oracle factors, distributions, match-grouped bootstrap."""
import itertools
import math

import numpy as np
import pandas as pd
from scipy.special import ndtr
from scipy.stats import betabinom

BR = 21.74132969862184
B = 2000
SEED = 20261022
M = {"1": 1, "12": 2, "123": 3}
C_K = {"1": 1.442, "12": 1.457, "123": 1.457}       # perf-2 kills spread
C_CM2 = 1.4756515691194962                           # cm-2 kills spread
KAPPA = 100.0
KILL_F = ["Rreg", "Rot", "Tw", "Tr", "S"]
NAMES = {"Rreg": "(1) regulation rounds (map length / scoreline closeness)", "Rot": "(1) overtime rounds",
         "Tw": "(2) team kills per round explained by realized round-win share (team strength / scoreline)",
         "Tr": "(3a) team kills per round, rest", "S": "(3b) player share of team kills", "H": "(4) HS per kill"}


def cm2_round_partial(df):
    """cm-2 has no explicit rounds term: its implied rounds = 21.7413 x maps x exp(partial effect of its two rounds features)."""
    import cm2
    F = cm2.FIT
    nm = cm2.ARTIFACT["feature_names"]
    i, j = nm.index("log_own_historical_rounds_per_map"), nm.index("log_opponent_historical_rounds_per_map")
    return np.exp(F["coef"][i] * (df[f"x{i}"] - F["mean"][i]) / F["std"][i] + F["coef"][j] * (df[f"x{j}"] - F["mean"][j]) / F["std"][j])


def live_frame(ser, scope):
    """Rows of population (a) that played the scope, with the live model's projection, its rounds assumption, team sum, share."""
    d = ser[ser[f"K_{scope}"].notna()].copy()
    m = M[scope]
    if scope == "12":
        use = d.cm2_ok == 1
        d["lam"] = np.where(use, d.lam_cm2, 2 * d.lam_p2_1)
        d["model"] = np.where(use, "cm-2", "perf-2")
        d["c"] = np.where(use, C_CM2, C_K["12"])
        d["Rhat"] = BR * m * np.where(use, cm2_round_partial(d), 1.0)
    else:
        d["lam"] = m * d.lam_p2_1
        d["model"] = "perf-2"
        d["c"] = C_K[scope]
        d["Rhat"] = BR * m
    g = d.groupby(["match", "team"])
    d["n_team"] = g.lam.transform("size")
    d = d[d.n_team == 5].copy()
    d["Tsum"] = d.groupby(["match", "team"]).lam.transform("sum")
    d["s_hat"] = d.lam / d.Tsum
    d["tau_hat"] = d.Tsum / d.Rhat
    for c_ in ("K", "HS", "R", "Rreg", "W", "T", "O", "ot"):
        d[c_] = d[f"{c_}_{scope}"]
    d["lam_hs"] = d.lam * d.hsr
    d["month"] = pd.to_datetime(d.day.map(lambda x: pd.Timestamp.fromordinal(int(x)))).dt.strftime("%Y-%m")
    return d


def factors(d, g_w):
    """Realized/model ratios. g_w(w) = fitted log team-kpr ratio as a function of realized round-win share."""
    f = pd.DataFrame(index=d.index)
    f["Rreg"] = d.Rreg / d.Rhat
    f["Rot"] = d.R / d.Rreg
    fT = (d["T"] / d.R) / d.tau_hat
    f["Tw"] = np.exp(g_w(d.W / d.R))
    f["Tr"] = fT / f.Tw
    f["S"] = (d.K / d["T"]) / d.s_hat
    hk = d.HS / d.K
    f["H"] = np.where(d.K > 0, hk / d.hsr, 1.0)
    return f


def fit_gw(d, deg=3):
    """log team-kpr ratio ~ poly(realized round-win share - 0.5) by OLS (an oracle mapping; realized information)."""
    w = (d.W / d.R - 0.5).values
    y = np.log((d["T"] / d.R) / d.tau_hat).values
    X = np.vstack([w ** k for k in range(deg + 1)]).T
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    return (lambda ww: np.vstack([(np.asarray(ww) - 0.5) ** k for k in range(deg + 1)]).T @ b), b


def subsets(names):
    for r in range(len(names) + 1):
        for s in itertools.combinations(names, r):
            yield frozenset(s)


def pred_subset(base, f, S):
    p = base.copy()
    for k in S:
        p = p * f[k].values
    return p


def shapley_rows(base, actual, f, names):
    """Row-level Shapley contributions of each factor to sq error, abs error and signed error (actual - pred).
    Returns dict metric -> DataFrame (rows x factors) of contributions = v(S) - v(S + f) averaged (positive = error removed)."""
    n = len(names)
    preds = {S: pred_subset(base, f, S) for S in subsets(names)}
    vals = {S: {"se": (actual - p) ** 2, "ae": np.abs(actual - p), "bias": actual - p} for S, p in preds.items()}
    out = {k: pd.DataFrame(0.0, index=f.index, columns=names) for k in ("se", "ae", "bias")}
    for S in preds:
        for x in names:
            if x in S:
                continue
            wgt = math.factorial(len(S)) * math.factorial(n - len(S) - 1) / math.factorial(n)
            T_ = S | {x}
            for k in out:
                out[k][x] += wgt * (vals[S][k] - vals[T_][k])
    return out, preds


# ---------------------------------------------------------------- distributions
def disc_normal_logpmf(y, lam, c):
    """log P(Y=y) for the live kills distribution: normal(lam, c sqrt(max(lam,1))) discretised to integers, conditioned >= 0."""
    sd = c * np.sqrt(np.maximum(lam, 1.0))
    p = ndtr((y + 0.5 - lam) / sd) - ndtr((y - 0.5 - lam) / sd)
    z0 = 1 - ndtr((-0.5 - lam) / sd)
    return np.log(np.maximum(p, 1e-300)) - np.log(z0)


def disc_normal_cdf(y, lam, c):
    sd = c * np.sqrt(np.maximum(lam, 1.0))
    z0 = 1 - ndtr((-0.5 - lam) / sd)
    return (ndtr((y + 0.5 - lam) / sd) - ndtr((-0.5 - lam) / sd)) / z0


def fit_c(y, lam, lo=0.3, hi=3.0):
    """ML spread multiplier for the discretised normal (golden section on mean NLL)."""
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    f = lambda c: -disc_normal_logpmf(y, lam, c).mean()
    x1, x2 = b - g * (b - a), a + g * (b - a)
    f1, f2 = f(x1), f(x2)
    for _ in range(40):
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = b - g * (b - a); f1 = f(x1)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + g * (b - a); f2 = f(x2)
    return (a + b) / 2


def hs_mix(lam_k, ck, hsr, y, kmax_sd=8):
    """HS live distribution (beta-binomial kappa 100 on HS|kills, mixed over the discretised-normal kills pmf):
    returns (P(HS = y), P(HS <= y), P(HS <= y - 1)) per row."""
    n = len(lam_k)
    sd = ck * np.sqrt(np.maximum(lam_k, 1.0))
    kmax = int(np.max(lam_k + kmax_sd * sd)) + 2
    ks = np.arange(kmax + 1)
    pk = ndtr((ks[None, :] + 0.5 - lam_k[:, None]) / sd[:, None]) - ndtr((ks[None, :] - 0.5 - lam_k[:, None]) / sd[:, None])
    pk = pk / pk.sum(1, keepdims=True)
    h = np.clip(hsr, 1e-6, 1 - 1e-6)
    a, b = (h * KAPPA)[:, None], ((1 - h) * KAPPA)[:, None]
    yy = np.asarray(y, float)[:, None]
    K = np.broadcast_to(ks[None, :], (n, kmax + 1))
    pm = np.where(yy <= K, betabinom.pmf(yy, K, a, b), 0.0)
    cd = np.where(yy <= K, betabinom.cdf(yy, K, a, b), 1.0)
    cd1 = np.where(yy - 1 <= K, betabinom.cdf(yy - 1, K, a, b), 1.0)
    cd1 = np.where(yy - 1 < 0, 0.0, cd1)
    return (pk * pm).sum(1), (pk * cd).sum(1), (pk * cd1).sum(1)


def p_over_line(lam, sd, line):
    """Chance of Over given no push, discretised normal (live cut rule)."""
    lam, sd, line = map(np.asarray, (lam, sd, line))
    whole = line == np.floor(line)
    up = np.where(whole, line + 0.5, np.floor(line) + 0.5)
    dn = np.where(whole, line - 0.5, up)
    po = 1 - ndtr((up - lam) / sd)
    pu = ndtr((dn - lam) / sd)
    return po / np.maximum(po + pu, 1e-12)


def logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


# ---------------------------------------------------------------- match-grouped bootstrap
def boot(cols, groups, ratio=None, b=B, seed=SEED):
    """cols: dict name -> per-row values (means are bootstrapped). groups: match id per row. ratio: dict name -> (num, den)
    names of two means whose ratio is also reported. Returns {name: (point, lo, hi)} and the number of distinct matches."""
    g, gi = np.unique(np.asarray(groups), return_inverse=True)
    G = len(g)
    names = list(cols)
    S = np.vstack([np.bincount(gi, weights=np.asarray(cols[k], float), minlength=G) for k in names]).T
    n = np.bincount(gi, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    reps = []
    for start in range(0, b, 250):
        bb = min(250, b - start)
        W = np.vstack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(bb)]).astype(float)
        reps.append((W @ S) / (W @ n)[:, None])
    reps = np.vstack(reps)
    point = S.sum(0) / n.sum()
    out = {k: (point[i], *np.percentile(reps[:, i], [2.5, 97.5])) for i, k in enumerate(names)}
    for k, (a, c) in (ratio or {}).items():
        ia, ic = names.index(a), names.index(c)
        r = reps[:, ia] / reps[:, ic]
        out[k] = (point[ia] / point[ic], *np.percentile(r, [2.5, 97.5]))
    return out, G


def fmt(t, d=3, pct=False):
    if pct:
        return f"{100 * t[0]:.{d}f}% [{100 * t[1]:.{d}f}, {100 * t[2]:.{d}f}]"
    return f"{t[0]:+.{d}f} [{t[1]:+.{d}f}, {t[2]:+.{d}f}]"
