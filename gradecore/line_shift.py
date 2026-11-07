"""NBA / NFL line shift for the pick'em +EV board (SCOUT_PICKEM_EV, competitor spec 4.1 "Different line").

A copy of the sports service's measured shape (sports/fair.py at_line + sports/dispersion.json), the same way
gradecore/payouts.py copies sports/payouts.py: the CS2 server may not import the sports package
(sports/tests/test_sports_isolation.py), so the arithmetic lives here and gradecore/tests/test_ev_board.py proves it
gives sports.fair.at_line's answer to 1e-6 on every shape. dispersion.json is read as data, never imported.

  count  = negative binomial with variance mean + b*mean^2 (Poisson when b = 0)
  normal = normal with variance a*mean + b*mean^2 and a continuity correction for whole-number outcomes
The mean is solved so P(Over the book's line | no push) equals the book's no-vig chance, then read at the pick'em line.
A market missing from dispersion.json has no shape: reader() returns None and gradecore.fair drops a quote on another
line ("different line and no line-shift shape") instead of guessing.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

DISPERSION_FILE = Path(__file__).resolve().parents[1] / "sports" / "dispersion.json"
FAMILY = {"nba": "basketball", "nfl": "football"}
GAP_WARN = {"count": 1.0, "normal": 3.0}       # same as sports/fair.py: gaps beyond these read the shape far from the price


@lru_cache(maxsize=1)
def _dispersion():
    try:
        return json.loads(DISPERSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def shape_of(family, market):
    """(shape, a, b) for this market, or None when dispersion.json has no fitted shape for it."""
    d = (_dispersion().get(family) or {}).get(market)
    if not d or d.get("shape") not in ("count", "normal"):
        return None
    if d["shape"] == "count":
        return "count", 1.0, max(0.0, float(d.get("b", 0.0)))
    return "normal", max(0.05, float(d.get("a", 1.0))), max(0.0, float(d.get("b", 0.0)))


def _nb_pmf_table(mean, b, upto):
    if mean <= 0:
        return [1.0] + [0.0] * upto
    if b < 1e-9:
        p0 = math.exp(-mean)
        out = [p0]
        for k in range(1, upto + 1):
            out.append(out[-1] * mean / k)
        return out
    r = 1.0 / b
    q = r / (r + mean)
    out = [math.exp(r * math.log(q))]
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
        below = sum(pmf[:top])
        return max(0.0, 1.0 - below), below, 0.0
    sd = math.sqrt(max(1e-9, a * mean + b * mean * mean))
    if whole:
        lo, hi = _phi((line - 0.5 - mean) / sd), _phi((line + 0.5 - mean) / sd)
        return 1.0 - hi, lo, hi - lo
    lo = _phi((math.floor(line) + 0.5 - mean) / sd)
    return 1.0 - lo, lo, 0.0


def _over_no_push(shape, a, b, mean, line):
    o, u, _ = dist_probs(shape, a, b, mean, line)
    return o / (o + u) if o + u > 0 else 0.5


def solve_mean(shape, a, b, line, p_over):
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


@lru_cache(maxsize=65536)
def _at(shape, a, b, sharp_line, p_over_sharp, line):
    mean = solve_mean(shape, a, b, sharp_line, p_over_sharp)
    if sharp_line == line and abs(line - round(line)) > 1e-9:
        return p_over_sharp, 1 - p_over_sharp, 0.0, mean
    o, u, p = dist_probs(shape, a, b, mean, line)
    return o, u, p, mean


def at_line(family, market, sharp_line, p_over_sharp, line):
    """-> {"over", "under", "push", "mean", "gap", "far"} at the pick'em line, or None without a fitted shape.
    The book's chance is rounded to 1e-6 before solving (a cache key; far below any displayed digit)."""
    sh = shape_of(family, market)
    if sh is None:
        return None
    shape, a, b = sh
    o, u, p, mean = _at(shape, a, b, float(sharp_line), round(float(p_over_sharp), 6), float(line))
    gap = round(float(line) - float(sharp_line), 3)
    return {"over": o, "under": u, "push": p, "mean": mean, "gap": gap, "far": abs(gap) > GAP_WARN[shape]}


def reader(sport, market):
    """gradecore.fair's `reader` for one NBA / NFL market: reader(book_line, p_over_book, pickem_line) -> chances at the
    pick'em line. None when the sport or market has no fitted shape."""
    family = FAMILY.get(sport)
    if family is None or shape_of(family, market) is None:
        return None
    return lambda sharp_line, p, line: at_line(family, market, sharp_line, p, line)
