"""Win / loss mixture (wl-mix, lol/EVALUATION.md A10): a player's total over n games as a mixture over the number of games
his team wins. Standard library only (production does not install scipy).

  per-game mean mu1 = mu / n;  win mean mw = mu1 * R / (p R + 1 - p);  loss mean ml = mu1 / (p R + 1 - p)
  k ~ Binomial(n, p);  total | k ~ NB(k mw + (n - k) ml, size s)          -> mixture mean exactly mu
"""
import math

from . import model


def components(mu, n, p, R):
    """[(weight, mean)] for k = 0..n wins."""
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    mu1 = mu / n
    den = p * R + 1 - p
    mw, ml = mu1 * R / den, mu1 / den
    return [(math.comb(n, k) * p ** k * (1 - p) ** (n - k), k * mw + (n - k) * ml) for k in range(n + 1)]


def pmf(y, mu, n, p, R, s):
    return sum(w * model.nb_pmf(y, m, s) for w, m in components(mu, n, p, R))


def probs(mu, n, p, R, s, line):
    """(over, under, push) at a line - the same convention as model.probs (over = more than the line)."""
    over = under = push = 0.0
    for w, m in components(mu, n, p, R):
        o, u, pu = model.probs(m, s, line)
        over += w * o
        under += w * u
        push += w * pu
    t = over + under + push
    return over / t, under / t, push / t
