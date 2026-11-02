"""From a book's prices to fair chances: multiplicative, additive, power and Shin, plus worst_case().

Every method takes decimal prices for one market's outcomes (over, under) and returns fair chances that sum to 1, or
None when the prices are unusable for that method. worst_case() = the lowest fair chance any method gives the chosen
side, so a pick is never priced on the method that flatters it.

multiplicative matches sports/fair.no_vig (the documented baseline). Width here is always the overround: sum(1/d) - 1.
"""
import math

_ITERS = 200


def american_to_decimal(a):
    a = float(a)
    if a == 0:
        raise ValueError("american odds 0")
    return 1 + (a / 100.0 if a > 0 else 100.0 / -a)


def _implied(prices):
    """Decimal prices -> implied chances, or None. A price of 1 or less cannot be devigged."""
    try:
        d = [float(x) for x in prices]
    except (TypeError, ValueError):
        return None
    if len(d) < 2 or not all(math.isfinite(x) and x > 1.0 for x in d):
        return None
    return [1.0 / x for x in d]


def width(prices):
    """The market's overround (0.05 = a 5% margin), or None."""
    q = _implied(prices)
    return None if q is None else sum(q) - 1.0


def multiplicative(prices):
    q = _implied(prices)
    if q is None:
        return None
    s = sum(q)
    return [x / s for x in q]


def additive(prices):
    """Takes the margin equally off each outcome. A long shot smaller than its share has no valid answer: None."""
    q = _implied(prices)
    if q is None:
        return None
    cut = (sum(q) - 1.0) / len(q)
    p = [x - cut for x in q]
    return p if min(p) > 0 else None


def power(prices):
    """Fair p_i = q_i ** k, with k solved so they sum to 1."""
    q = _implied(prices)
    if q is None:
        return None
    lo, hi = 1e-9, 1.0
    while sum(x ** hi for x in q) > 1.0:
        lo, hi = hi, hi * 2.0
    for _ in range(_ITERS):
        mid = (lo + hi) / 2
        if sum(x ** mid for x in q) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    s = sum(x ** k for x in q)
    return [x ** k / s for x in q]


def _shin_p(q, s, z):
    return [(math.sqrt(z * z + 4.0 * (1.0 - z) * x * x / s) - z) / (2.0 * (1.0 - z)) for x in q]


def shin(prices):
    """Shin's insider-trading model. It explains a margin, so a market priced under 100% has no answer: None. An exactly
    fair market is returned as it is."""
    q = _implied(prices)
    if q is None:
        return None
    s = sum(q)
    if abs(s - 1.0) < 1e-12:
        return q
    if s < 1.0:
        return None
    lo, hi = 0.0, 1.0 - 1e-9
    if sum(_shin_p(q, s, hi)) > 1.0:
        return None
    for _ in range(_ITERS):
        mid = (lo + hi) / 2
        if sum(_shin_p(q, s, mid)) > 1.0:
            lo = mid
        else:
            hi = mid
    p = _shin_p(q, s, (lo + hi) / 2)
    t = sum(p)
    return [x / t for x in p]


METHODS = {"multiplicative": multiplicative, "additive": additive, "power": power, "shin": shin}


def worst_case(prices, side=0):
    """-> (lowest fair chance of outcome `side` across the methods, method name), or None. A method with no valid answer
    is skipped; ties go to the first method in METHODS."""
    best = None
    for name, fn in METHODS.items():
        p = fn(prices)
        if p is not None and (best is None or p[side] < best[0]):
            best = (p[side], name)
    return best
