"""rm-1 / th-1 (REGISTER.md "Frozen candidate definitions rm-1 / th-1") - shared distribution code.

One generic construction covers live, rm-1 and th-1: kills | R ~ normal(mu_R, sd_R) discretised + conditioned >= 0 (integer
pmf) or the continuous-normal line cuts (kills chances, as grader p_three), mixed over a pmf of scope rounds R.
- live: R degenerate (one point, weight 1), mu = lam, sd = c sqrt(max(lam, 1))
- rm-1: R ~ pi convolved m times, mu_R = lam R / E[R], sd_R = rho c sqrt(max(mu_R, 1))
- th-1: live with lam' = lam e^a and c' = c e^s
HS = beta-binomial (kappa 100, row HS per kill) over the integer kills pmf (perf.hs_pmf)."""
import math

import numpy as np
from scipy.special import betaln, gammaln, ndtr

KAPPA = 100.0
CAP = 0.78
SEED = 20261023


# ----------------------------------------------------------------- rounds pmf
def pi_fit(rounds):
    """Per-map rounds pmf from an array of map round totals: dict r -> p, plus q / P_reg / P_ot pieces."""
    r = np.asarray(rounds, int)
    r = r[r >= 13]
    n = len(r)
    ot = r > 24
    q = ot.mean()
    reg_v, reg_c = np.unique(r[~ot], return_counts=True)
    ot_v, ot_c = np.unique(r[ot], return_counts=True)
    pmf = {}
    for v, c in zip(reg_v, reg_c):
        pmf[int(v)] = (1 - q) * c / (~ot).sum()
    for v, c in zip(ot_v, ot_c):
        pmf[int(v)] = pmf.get(int(v), 0.0) + q * c / ot.sum()
    return {"n_maps": int(n), "q": float(q), "P_reg": {int(v): float(c / (~ot).sum()) for v, c in zip(reg_v, reg_c)},
            "P_ot": {int(v): float(c / ot.sum()) for v, c in zip(ot_v, ot_c)}, "pmf": pmf}


def pi_scope(pmf, m, tol=1e-9):
    """pi convolved m times -> (support array, prob array), tiny tail mass dropped and renormalised."""
    v = np.array(sorted(pmf)); p = np.array([pmf[k] for k in v])
    lo = v.min()
    base = np.zeros(v.max() - lo + 1); base[v - lo] = p
    out, off = base.copy(), lo
    for _ in range(m - 1):
        out = np.convolve(out, base); off += lo
    sup = np.arange(len(out)) + off
    keep = out > tol
    sup, out = sup[keep], out[keep]
    return sup.astype(float), out / out.sum()


# ----------------------------------------------------------------- kills
def _mix(lam, c, rho, sup, w):
    """mu (rows x R), sd (rows x R), weights (R). sup None = live (degenerate)."""
    lam = np.asarray(lam, float)[:, None]; c = np.asarray(c, float)[:, None]
    if sup is None:
        return lam, c * np.sqrt(np.maximum(lam, 1.0)), np.ones(1)
    ER = float((sup * w).sum())
    mu = lam * (sup[None, :] / ER)
    sd = np.asarray(rho, float).reshape(-1, 1) * c * np.sqrt(np.maximum(mu, 1.0))
    return mu, sd, w


def kills_logpmf_at(y, lam, c, rho=1.0, sup=None, w=None):
    """log P(K = y) under the (mixture of) discretised conditioned normals."""
    mu, sd, ww = _mix(lam, c, np.broadcast_to(rho, np.shape(lam)), sup, w)
    yy = np.asarray(y, float)[:, None]
    z0 = 1 - ndtr((-0.5 - mu) / sd)
    p = (ndtr((yy + 0.5 - mu) / sd) - ndtr((yy - 0.5 - mu) / sd)) / z0
    return np.log(np.maximum((p * ww[None, :]).sum(1), 1e-300))


def kills_cdf_at(y, lam, c, rho=1.0, sup=None, w=None):
    """P(K <= y) (integer pmf), y may be -1."""
    mu, sd, ww = _mix(lam, c, np.broadcast_to(rho, np.shape(lam)), sup, w)
    yy = np.asarray(y, float)[:, None]
    z0 = 1 - ndtr((-0.5 - mu) / sd)
    F = (ndtr((yy + 0.5 - mu) / sd) - ndtr((-0.5 - mu) / sd)) / z0
    F = np.where(yy < 0, 0.0, F)
    return (F * ww[None, :]).sum(1)


def kills_pmf_grid(lam, c, rho=1.0, sup=None, w=None, kmax=None):
    """rows x (kmax+1) integer kills pmf (each row sums to 1)."""
    mu, sd, ww = _mix(lam, c, np.broadcast_to(rho, np.shape(lam)), sup, w)
    if kmax is None:
        kmax = int(np.max(mu + 8 * sd)) + 2
    ks = np.arange(kmax + 1, dtype=float)
    z0 = 1 - ndtr((-0.5 - mu) / sd)                                   # rows x R
    hi = ndtr((ks[None, None, :] + 0.5 - mu[:, :, None]) / sd[:, :, None])
    lo = ndtr((ks[None, None, :] - 0.5 - mu[:, :, None]) / sd[:, :, None])
    pk = ((hi - lo) / z0[:, :, None] * ww[None, :, None]).sum(1)
    return pk / pk.sum(1, keepdims=True)


def p_over_kills(line, lam, c, rho=1.0, sup=None, w=None, cap=True):
    """P(Over | no push) for a kills line: continuous normal at the live cuts, mixed over R, then the live cap."""
    mu, sd, ww = _mix(lam, c, np.broadcast_to(rho, np.shape(lam)), sup, w)
    line = np.asarray(line, float)
    whole = line == np.floor(line)
    up = np.where(whole, line + 0.5, np.floor(line) + 0.5)[:, None]
    dn = np.where(whole, line - 0.5, np.floor(line) + 0.5)[:, None]
    po = ((1 - ndtr((up - mu) / sd)) * ww[None, :]).sum(1)
    pu = (ndtr((dn - mu) / sd) * ww[None, :]).sum(1)
    p = np.where(po + pu > 0, po / np.maximum(po + pu, 1e-300), 0.5)
    return np.clip(p, 1 - CAP, CAP) if cap else p


# ----------------------------------------------------------------- HS
def _bb_logpmf(h, k, a, b):
    return (gammaln(k + 1) - gammaln(h + 1) - gammaln(k - h + 1) + betaln(h + a, k - h + b) - betaln(a, b))


def hs_stats(pk, hsr, y, line=None):
    """From an integer kills pmf grid pk (rows x K) and HS per kill: P(HS = y), P(HS <= y - 1) (for mid-PIT) and, if line
    is given, P(Over | no push) at that HS line (Over = HS > line, Under = HS < line)."""
    n, K1 = pk.shape
    ks = np.arange(K1, dtype=float)[None, :]
    h = np.clip(np.asarray(hsr, float), 1e-6, 1 - 1e-6)[:, None]
    a, b = h * KAPPA, (1 - h) * KAPPA
    H = int(max(np.nanmax(y), np.nanmax(line) if line is not None else 0)) + 2
    G = np.zeros((n, H + 1))                      # P(HS = j), j = 0..H
    for j in range(H + 1):
        G[:, j] = (pk * np.where(j <= ks, np.exp(_bb_logpmf(np.minimum(j, ks), ks, a, b)), 0.0)).sum(1)
    cdf = np.cumsum(G, 1)
    yi = np.asarray(y, int)
    p_y = G[np.arange(n), yi]
    out_cdf_y1 = np.where(yi - 1 >= 0, cdf[np.arange(n), np.clip(yi - 1, 0, H)], 0.0)
    res = {"p_y": p_y, "cdf_y1": out_cdf_y1}
    if line is not None:
        L = np.asarray(line, float)
        tot = G.sum(1)          # mass up to H; mass above H counts as Over
        below = np.where(L == np.floor(L), np.floor(L) - 1, np.floor(L)).astype(int)   # Under = HS < line
        atop = np.floor(L).astype(int)                                                 # Over = HS > line
        pu = np.where(below >= 0, cdf[np.arange(n), np.clip(below, 0, H)], 0.0)
        po = 1 - cdf[np.arange(n), np.clip(atop, 0, H)]
        res["p_over"] = np.where(po + pu > 0, po / np.maximum(po + pu, 1e-300), 0.5)
    return res


# ----------------------------------------------------------------- fitting
def golden(f, lo, hi, it=40):
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    x1, x2 = b - g * (b - a), a + g * (b - a)
    f1, f2 = f(x1), f(x2)
    for _ in range(it):
        if f1 < f2:
            b, x2, f2 = x2, x1, f1
            x1 = b - g * (b - a); f1 = f(x1)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + g * (b - a); f2 = f(x2)
    return (a + b) / 2


def fit_rho(y, lam, c, sup, w):
    return golden(lambda r: -kills_logpmf_at(y, lam, c, r, sup, w).mean(), 0.3, 1.5)


def fit_th(y, lam, c):
    """(a, s) by ML of the integer kills actual (Nelder-Mead from 0, then clipped)."""
    from scipy.optimize import minimize
    f = lambda v: -kills_logpmf_at(y, lam * math.exp(v[0]), c * math.exp(v[1])).mean()
    r = minimize(f, np.zeros(2), method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-8, "maxiter": 400})
    return float(np.clip(r.x[0], -0.5, 0.5)), float(np.clip(r.x[1], -0.5, 0.7))


# ----------------------------------------------------------------- bootstrap with arbitrary quantiles
def boot(cols, groups, qs=(2.5, 97.5), b=2000, seed=SEED):
    """Match-grouped bootstrap of row means. Returns {name: (point, q...)}, n_groups."""
    g, gi = np.unique(np.asarray(groups).astype(str), return_inverse=True)
    G = len(g)
    names = list(cols)
    S = np.vstack([np.bincount(gi, weights=np.asarray(cols[k], float), minlength=G) for k in names]).T
    n = np.bincount(gi, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    reps = []
    for st in range(0, b, 250):
        bb = min(250, b - st)
        W = np.vstack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(bb)]).astype(float)
        reps.append((W @ S) / (W @ n)[:, None])
    reps = np.vstack(reps)
    pt = S.sum(0) / n.sum()
    return {k: (float(pt[i]), *[float(x) for x in np.percentile(reps[:, i], qs)]) for i, k in enumerate(names)}, G


def boot_absgap(p_a, p_b, fav_hit_a, fav_hit_b, pfav_a, pfav_b, side_a, side_b, groups, b=1000, seed=SEED):
    """Calibration by favoured side: for side s in (Over, Under): |mean pfav - hit| for model a and b, difference b - a,
    match-grouped bootstrap. Returns dict side -> (gap_a, gap_b, diff point, lo, hi, n_a, n_b)."""
    g, gi = np.unique(np.asarray(groups).astype(str), return_inverse=True)
    G = len(g)
    rng = np.random.default_rng(seed)
    W = np.vstack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)
    out = {}
    for s in ("Over", "Under"):
        sums = []
        for side, pf, hit in ((side_a, pfav_a, fav_hit_a), (side_b, pfav_b, fav_hit_b)):
            m = (np.asarray(side) == s).astype(float)
            sums.append([np.bincount(gi, weights=m * np.asarray(pf), minlength=G), np.bincount(gi, weights=m * np.asarray(hit), minlength=G),
                         np.bincount(gi, weights=m, minlength=G)])
        def gaps(Wv):
            r = []
            for sp, sh, sn in sums:
                nn = Wv @ sn
                r.append(np.abs((Wv @ sp) - (Wv @ sh)) / np.maximum(nn, 1e-9))
            return r
        ga, gb = gaps(np.ones((1, G)))
        ra, rb = gaps(W)
        d = rb - ra
        out[s] = {"gap_live": float(ga[0]), "gap_cand": float(gb[0]), "diff": float(gb[0] - ga[0]),
                  "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5)),
                  "n_live": int(sums[0][2].sum()), "n_cand": int(sums[1][2].sum())}
    return out
