"""Predictive distributions as pmf matrices (rows x support 0..Y) and the scores used on every historical outcome.

Live families (reproduced from perf.py / grader.py):
  kills: discretised normal, mean lam, sd = c sqrt(max(lam, 1)), conditioned on >= 0 (perf.kills_pmf);
  HS:    HS | kills ~ BetaBinomial(kills, hs_per_kill * 100, (1 - hs_per_kill) * 100) averaged over the kills pmf
         (perf.hs_pmf, grader perf_hs_dist).
Candidate family: negative binomial with mean mu and variance v * mu (v > 1), or the same discretised normal."""
from __future__ import annotations

import numpy as np
from scipy.special import gammaln, ndtr
from scipy.stats import nbinom

YMAX = {"1": 70, "12": 120, "123": 170}


def norm_pmf(mu, sd, ymax):
    """Discretised normal on 0..ymax, renormalised on >= 0 (perf.kills_pmf; its +8 sd cap removes < 1e-9)."""
    mu = np.asarray(mu, float)[:, None]
    sd = np.maximum(np.asarray(sd, float), 1e-6)[:, None]
    y = np.arange(ymax + 1)[None, :]
    cdf_hi = ndtr((y + 0.5 - mu) / sd)
    cdf_lo = ndtr((y - 0.5 - mu) / sd)
    P = np.maximum(cdf_hi - cdf_lo, 0.0)
    tot = P.sum(1, keepdims=True)
    return P / np.maximum(tot, 1e-300)


def live_kills_pmf(lam, c, ymax):
    lam = np.asarray(lam, float)
    return norm_pmf(lam, np.asarray(c, float) * np.sqrt(np.maximum(lam, 1.0)), ymax)


def betabinom_mix(kpmf, hsr, kappa=100.0, chunk=2000):
    """HS pmf: sum_k P(k) BetaBinomial(y | k, a, b) - perf.hs_pmf vectorised (each k's terms renormalised)."""
    n, K = kpmf.shape
    hsr = np.clip(np.asarray(hsr, float), 1e-6, 1 - 1e-6)
    out = np.zeros_like(kpmf)
    k = np.arange(K)[:, None]
    yv = np.arange(K)[None, :]
    valid = yv <= k
    lc = gammaln(k + 1) - gammaln(yv + 1) - gammaln(np.maximum(k - yv, 0) + 1)
    for s in range(0, n, chunk):
        a = (hsr[s:s + chunk] * kappa)[:, None, None]
        b = ((1 - hsr[s:s + chunk]) * kappa)[:, None, None]
        base = gammaln(a + b) - gammaln(a) - gammaln(b) - gammaln(k[None] + a + b)
        lt = lc[None] + gammaln(yv[None] + a) + gammaln(np.maximum(k - yv, 0)[None] + b) + base
        T = np.where(valid[None], np.exp(np.where(valid[None], lt, -np.inf)), 0.0)
        T = T / np.maximum(T.sum(2, keepdims=True), 1e-300)
        out[s:s + chunk] = np.einsum("nk,nky->ny", kpmf[s:s + chunk], T)
    return out


def negbin_pmf(mu, v, ymax):
    mu = np.maximum(np.asarray(mu, float), 0.05)[:, None]
    v = np.maximum(np.asarray(v, float), 1.02)[:, None]
    nn = mu / (v - 1.0)
    p = 1.0 / v
    y = np.arange(ymax + 1)[None, :]
    P = np.exp(nbinom.logpmf(y, nn, p))
    return P / np.maximum(P.sum(1, keepdims=True), 1e-300)


def pmf_mean(P):
    return P @ np.arange(P.shape[1])


def logscore(P, y):
    y = np.asarray(y, int)
    return np.log(np.maximum(P[np.arange(len(y)), np.minimum(y, P.shape[1] - 1)], 1e-12))


def p_over(P, t):
    """P(X > t) for t a half-integer (vector per row)."""
    t = np.asarray(t, float)
    cut = np.floor(t).astype(int) + 1                 # first integer above t
    C = np.cumsum(P, 1)
    below = np.where(cut > 0, C[np.arange(len(t)), np.clip(cut - 1, 0, P.shape[1] - 1)], 0.0)
    return np.clip(1.0 - below, 0.0, 1.0)


def p_over_line(P, line):
    """(over, under) for a book line: x.5 = at the line; whole number = push excluded (live p_more convention)."""
    line = np.asarray(line, float)
    whole = line == np.floor(line)
    # over: X > line (whole: X >= line + 1); under: X < line (whole: X <= line - 1)
    o = p_over(P, np.where(whole, line + 0.5, line))
    u = 1.0 - p_over(P, np.where(whole, line - 0.5, line))
    return o, u


def pit(P, y, rng):
    y = np.asarray(y, int)
    C = np.cumsum(P, 1)
    i = np.arange(len(y))
    hi = C[i, np.minimum(y, P.shape[1] - 1)]
    lo = np.where(y > 0, C[i, np.clip(y - 1, 0, P.shape[1] - 1)], 0.0)
    return lo + rng.random(len(y)) * (hi - lo)
