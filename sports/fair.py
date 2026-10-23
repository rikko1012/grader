"""From a sharp two-sided price to the chance of Over / Under / push at a pick'em line.

1. no_vig: proportional margin removal at the sharp line (the documented baseline in lol/center/prices.py and the
   method dkori/odds-derived-ff-projections uses): p_over = (1/d_over) / (1/d_over + 1/d_under). At a whole-number
   line the result is the chance of Over given no push (the book refunds a push).
2. at_line: when the pick'em line equals the sharp line (half-point), that chance is used as it is. When it differs,
   a distribution is fitted to the sharp price - its mean is solved so P(Over the sharp line) equals the no-vig chance,
   its spread comes from the market's variance function (dispersion.json, measured on last season's box scores by
   fit_dispersion.py) - and read at the pick'em line. The further apart the lines, the more the answer depends on the
   shape, so every result carries the gap and a flag beyond GAP_WARN.

Shapes: "count" = negative binomial with variance mean + b*mean^2 (Poisson when b = 0); "normal" = normal with variance
a*mean + b*mean^2 and a continuity correction for whole-number outcomes. numpy / scipy are not needed (production does
not install scipy - the LoL hotfix d78bc1d).
"""
import json
import math
from functools import lru_cache

from . import config

GAP_WARN = {"count": 1.0, "normal": 3.0}     # line gaps beyond these read the fitted shape far from the sharp line


def american_to_decimal(a):
    a = float(a)
    if a == 0:
        raise ValueError("american odds 0")
    return 1 + (a / 100.0 if a > 0 else 100.0 / -a)


def no_vig(d_over, d_under):
    """Two-sided decimal prices at the same line -> (p_over, overround) or None."""
    try:
        a, b = 1.0 / float(d_over), 1.0 / float(d_under)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if a <= 0 or b <= 0:
        return None
    return a / (a + b), a + b


@lru_cache(maxsize=1)
def _dispersion():
    try:
        return json.loads(config.DISPERSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def shape_params(family, market, shape):
    """(a, b) of the variance function var = a*mean + b*mean^2 for this market (dispersion.json), else Poisson-like."""
    d = (_dispersion().get(family) or {}).get(market) or {}
    if shape == "count":
        return 1.0, max(0.0, float(d.get("b", 0.0)))
    return max(0.05, float(d.get("a", 1.0))), max(0.0, float(d.get("b", 0.0)))


def _nb_pmf_table(mean, b, upto):
    """P(X = k) for k = 0..upto, negative binomial with variance mean + b*mean^2 (Poisson when b is ~0)."""
    if mean <= 0:
        return [1.0] + [0.0] * upto
    if b < 1e-9:
        p0 = math.exp(-mean)
        out = [p0]
        for k in range(1, upto + 1):
            out.append(out[-1] * mean / k)
        return out
    r = 1.0 / b
    q = r / (r + mean)                      # success probability
    log0 = r * math.log(q)
    out = [math.exp(log0)]
    for k in range(1, upto + 1):
        out.append(out[-1] * (k - 1 + r) / k * (1 - q))
    return out


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def dist_probs(shape, a, b, mean, line):
    """(P(X > line), P(X < line), P(X == line)) for whole-number outcomes."""
    whole = abs(line - round(line)) < 1e-9
    if shape == "count":
        top = int(math.floor(line)) + 1
        pmf = _nb_pmf_table(mean, b, top)
        if whole:
            k = int(round(line))
            below = sum(pmf[:k])
            push = pmf[k]
            return max(0.0, 1.0 - below - push), below, push
        below = sum(pmf[:top])                 # P(X <= floor(line))
        return max(0.0, 1.0 - below), below, 0.0
    sd = math.sqrt(max(1e-9, a * mean + b * mean * mean))
    if whole:
        lo, hi = _phi((line - 0.5 - mean) / sd), _phi((line + 0.5 - mean) / sd)
        return 1.0 - hi, lo, hi - lo
    lo = _phi((math.floor(line) + 0.5 - mean) / sd)   # continuity: X <= floor(line)
    return 1.0 - lo, lo, 0.0


def _over_no_push(shape, a, b, mean, line):
    o, u, _ = dist_probs(shape, a, b, mean, line)
    return o / (o + u) if o + u > 0 else 0.5


def solve_mean(shape, a, b, line, p_over):
    """The mean whose P(Over line | no push) equals p_over (bisection; the chance rises with the mean)."""
    p_over = min(max(p_over, 1e-4), 1 - 1e-4)
    lo, hi = 1e-3, max(5.0, line * 4 + 10)
    for _ in range(200):
        mid = (lo + hi) / 2
        if _over_no_push(shape, a, b, mid, line) < p_over:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


def at_line(family, market, shape, sharp_line, p_over_sharp, line):
    """-> {"over", "under", "push", "mean", "gap", "method", "far"}: chances at the pick'em `line` given the no-vig
    chance of Over at `sharp_line` (conditional on no push when sharp_line is whole)."""
    gap = round(float(line) - float(sharp_line), 3)
    a, b = shape_params(family, market, shape)
    mean = solve_mean(shape, a, b, float(sharp_line), p_over_sharp)
    half = abs(line - round(line)) > 1e-9
    if gap == 0 and half:
        return {"over": p_over_sharp, "under": 1 - p_over_sharp, "push": 0.0, "mean": round(mean, 2), "gap": 0.0,
                "method": "same line", "far": False}
    o, u, p = dist_probs(shape, a, b, mean, float(line))
    return {"over": o, "under": u, "push": p, "mean": round(mean, 2), "gap": gap,
            "method": f"{shape} fit, line moved {gap:+g}", "far": abs(gap) > GAP_WARN.get(shape, 1.0)}
