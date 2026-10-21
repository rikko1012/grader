"""Assists shape mixture (sh-1, lol/EVALUATION.md A13). Standard library only (production does not install scipy).

  P(total = k) = (1 - w) NB(k; mean mu, size s) + w N(k)
  N(k) = discretised normal, mean mu, variance mu + mu^2 / s (the NB's), renormalised over k >= 0:
         [Phi((k + 0.5 - mu) / sd) - Phi((k - 0.5 - mu) / sd)] / [1 - Phi((-0.5 - mu) / sd)]

w = 0 is exactly lol-1's negative binomial (model.probs / model.interval). The NB part has mean mu; the renormalised normal
part has mean slightly above mu when mu is small (its negative tail is cut off), so the mixture mean equals mu only up to
w x that truncation shift - recorded in the A13 result.
"""
import math

from . import model


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_pmf(k, mu, s):
    """The renormalised discretised normal at k >= 0 (mean mu, variance mu + mu^2 / s)."""
    if k < 0:
        return 0.0
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    sd = math.sqrt(mu + mu * mu / s)
    z = 1.0 - _phi((-0.5 - mu) / sd)
    return (_phi((k + 0.5 - mu) / sd) - _phi((k - 0.5 - mu) / sd)) / z


def pmf(k, mu, s, w):
    nb = model.nb_pmf(k, mu, s)
    return nb if not w else (1.0 - w) * nb + w * normal_pmf(k, mu, s)


def logpmf(k, mu, s, w):
    p = pmf(k, mu, s, w)
    return math.log(p) if p > 0 else -math.inf


def _top(mu, s, line=0):
    return max(int(mu + 12 * math.sqrt(mu + mu * mu / s)) + 10, int(line) + 2)


def probs(mu, s, w, line):
    """(over, under, push) at a line - model.probs's convention (over = more than the line; push = exactly a whole line)."""
    over = under = push = 0.0
    for k in range(0, _top(mu, s, line)):
        pk = pmf(k, mu, s, w)
        if k > line:
            over += pk
        elif k < line:
            under += pk
        else:
            push += pk
    tot = over + under + push
    return over / tot, under / tot, push / tot


def mean(mu, s, w):
    ks = range(0, _top(mu, s))
    ps = [pmf(k, mu, s, w) for k in ks]
    return sum(k * p for k, p in zip(ks, ps)) / sum(ps)


def interval(mu, s, w, cover=0.8):
    """Central interval [lo, hi] holding at least `cover` of the mass (model.interval's rule)."""
    lo_q, hi_q = (1 - cover) / 2, 1 - (1 - cover) / 2
    c, k, lo = 0.0, 0, None
    while True:
        c += pmf(k, mu, s, w)
        if lo is None and c >= lo_q:
            lo = k
        if c >= hi_q or k > 10000:
            return lo, k
        k += 1


def median(mu, s, w):
    c, k = 0.0, 0
    while True:
        c += pmf(k, mu, s, w)
        if c >= 0.5 or k > 10000:
            return k
        k += 1
