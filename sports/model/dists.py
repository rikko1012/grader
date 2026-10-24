"""Discrete outcome distributions on an integer support, and the scores used by REGISTER.md (numpy, worker only).

A distribution for one prop is ONE probability mass function over integers lo..hi (row of a matrix for many props).
Every line and both sides are read from it:  pOver = P(X > L), pUnder = P(X < L), pPush = P(X = L)  (unconditional;
half-point lines have zero push by construction). Families (REGISTER.md 5):

  poisson(mu)                         counts
  negbin(mu, alpha)                   counts, var = mu + alpha mu^2
  normal(mu, a, b)                    yards, var = a mu + b mu^2, continuity-corrected onto integers
  ratio(mu, ratios)                   empirical: mass of round(mu * r) over pooled train ratios r
  mixtures                            sum_j w_j * component_j (opportunity quadrature; see model.py)
"""
from __future__ import annotations

import math

import numpy as np

SUPPORT = {   # (lo, hi) per market - wide enough that the tails beyond carry < 1e-6 in every fitted family
    "points": (0, 100), "rebounds": (0, 40), "assists": (0, 35), "threes": (0, 20),
    "rec": (0, 25), "rush_att": (0, 50), "rec_yds": (-20, 330), "rush_yds": (-30, 330), "pass_yds": (-20, 650),
}


def grid(market):
    lo, hi = SUPPORT[market]
    return lo, np.arange(lo, hi + 1, dtype=float)


def _norm(p):
    p = np.clip(p, 0.0, None)
    s = p.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    return p / s


def poisson(mu, lo, ks):
    mu = np.maximum(np.asarray(mu, float), 1e-6)[:, None]
    k = np.maximum(ks[None, :], 0)
    logp = k * np.log(mu) - mu - _lgamma(k + 1)
    p = np.where(ks[None, :] >= 0, np.exp(logp), 0.0)
    return _norm(p)


_LGAMMA = np.vectorize(math.lgamma, otypes=[float])
_ERF = np.vectorize(math.erf, otypes=[float])


def _lgamma(x):
    return _LGAMMA(x)            # math only: the server installs no scipy (test_production_needs_no_scipy_or_pandas)


def negbin(mu, alpha, lo, ks):
    """var = mu + alpha mu^2 (alpha -> 0 = Poisson). alpha scalar or per row."""
    mu = np.maximum(np.asarray(mu, float), 1e-6)[:, None]
    a = np.broadcast_to(np.maximum(np.asarray(alpha, float), 1e-6), mu.shape[:1])[:, None]
    r = 1.0 / a
    k = np.maximum(ks[None, :], 0)
    logp = (_lgamma(k + r) - _lgamma(r) - _lgamma(k + 1) + r * np.log(r / (r + mu)) + k * np.log(mu / (r + mu)))
    p = np.where(ks[None, :] >= 0, np.exp(logp), 0.0)
    return _norm(p)


def _phi(z):
    return 0.5 * (1.0 + _ERF(np.asarray(z, float) / math.sqrt(2.0)))


def normal(mu, var, lo, ks):
    mu = np.asarray(mu, float)[:, None]
    sd = np.sqrt(np.maximum(np.asarray(var, float), 1e-6))[:, None]
    up = _phi((ks[None, :] + 0.5 - mu) / sd)
    dn = _phi((ks[None, :] - 0.5 - mu) / sd)
    p = up - dn
    p[:, 0] += dn[:, 0]                     # left tail onto lo
    p[:, -1] += 1 - up[:, -1]               # right tail onto hi
    return _norm(p)


def ratio(mu, ratios, lo, ks):
    """Empirical: P(X = k) = share of pooled ratios r with round(mu * r) == k (ratios: 1-D array or one per row)."""
    mu = np.asarray(mu, float)
    n, K = len(mu), len(ks)
    out = np.zeros((n, K))
    for i in range(n):
        r = ratios[i] if isinstance(ratios, (list, tuple)) else ratios
        v = np.clip(np.rint(mu[i] * r).astype(int) - lo, 0, K - 1)
        out[i] = np.bincount(v, minlength=K)[:K]
    return _norm(out)


def transform(pmf, lo, ks, mu, c=1.0, s=1.0):
    """cal-1: move each support point x to c*mu + s*(x - mu) (mean factor c, spread scale s), splitting its mass between
    the two neighbouring integers (mean preserved) and clipping at the support ends."""
    if c == 1.0 and s == 1.0:
        return pmf
    n, K = pmf.shape
    mu = np.asarray(mu, float)[:, None]
    new = c * mu + s * (ks[None, :] - mu)
    pos = np.clip(new - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    c2 = np.minimum(f + 1, K - 1)
    out = np.zeros_like(pmf)
    rows = np.repeat(np.arange(n), K)
    np.add.at(out, (rows, f.ravel()), (pmf * (1 - w)).ravel())
    np.add.at(out, (rows, c2.ravel()), (pmf * w).ravel())
    return out


# --------------------------------------------------------------------------------------------------------- reading
def line_probs(pmf_row, lo, line):
    """(p_over, p_under, p_push) at `line` from one PMF row (python floats; no rounding)."""
    p = np.asarray(pmf_row, float)
    ks = np.arange(lo, lo + len(p))
    over = float(p[ks > line + 1e-9].sum())
    under = float(p[ks < line - 1e-9].sum())
    push = float(p[np.abs(ks - line) <= 1e-9].sum())
    tot = over + under + push
    return over / tot, under / tot, push / tot


def mean_of(pmf, ks):
    return pmf @ ks


def quantile(pmf, ks, q):
    c = np.cumsum(pmf, axis=1)
    idx = (c < q).sum(axis=1)
    return ks[np.minimum(idx, len(ks) - 1)]


# ---------------------------------------------------------------------------------------------------------- scores
def crps(pmf, lo, y):
    """Discrete CRPS = sum_k (F(k) - 1{y <= k})^2 over the support (y clipped into it)."""
    ks = np.arange(lo, lo + pmf.shape[1])
    F = np.cumsum(pmf, axis=1)
    ind = (np.asarray(y, float)[:, None] <= ks[None, :]).astype(float)
    return ((F - ind) ** 2).sum(axis=1)


def nll(pmf, lo, y, floor=1e-6):
    K = pmf.shape[1]
    idx = np.clip(np.rint(np.asarray(y, float)).astype(int) - lo, 0, K - 1)
    return -np.log(np.maximum(pmf[np.arange(len(idx)), idx], floor))


def over_prob(pmf, lo, line):
    """P(X > line) per row (line scalar or per row)."""
    ks = np.arange(lo, lo + pmf.shape[1])
    ln = np.broadcast_to(np.asarray(line, float), (pmf.shape[0],))[:, None]
    return (pmf * (ks[None, :] > ln + 1e-9)).sum(axis=1)


def push_prob(pmf, lo, line):
    ks = np.arange(lo, lo + pmf.shape[1])
    ln = np.broadcast_to(np.asarray(line, float), (pmf.shape[0],))[:, None]
    return (pmf * (np.abs(ks[None, :] - ln) <= 1e-9)).sum(axis=1)


def central_cover(pmf, lo, y, level):
    """1 if y lies in the central `level` interval [q_(1-level)/2, q_(1+level)/2] of the PMF."""
    ks = np.arange(lo, lo + pmf.shape[1]).astype(float)
    a = quantile(pmf, ks, (1 - level) / 2)
    b = quantile(pmf, ks, (1 + level) / 2)
    y = np.asarray(y, float)
    return ((y >= a) & (y <= b)).astype(float)


def pit_mid(pmf, lo, y):
    """Randomisation-free mid PIT: P(X < y) + 0.5 P(X = y) (diagnostic queue)."""
    ks = np.arange(lo, lo + pmf.shape[1])
    y = np.asarray(y, float)[:, None]
    return (pmf * (ks[None, :] < y)).sum(axis=1) + 0.5 * (pmf * (ks[None, :] == y)).sum(axis=1)
