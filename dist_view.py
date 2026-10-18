"""The player card's distribution chart (2026-10-18 release brief, section 4): the exact distribution behind a grade
(grade["dist"], written by grader.py) as whole-number probabilities, for display only - never a grading input. Shown only
when it reproduces the grade's own raw Over / Under / push at the line (grade["probs_raw"]) within CHECK_TOL, so the
chart can never disagree with the chance printed above it."""
import functools
import json
import math

CHECK_TOL = 0.002


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def pmf(dist):
    """{total: probability} or None. normal: grader.p_three's discretised normal (whole totals, cuts at k +/- 0.5);
    hs_bb: perf.hs_pmf (HS | kills beta-binomial averaged over kills)."""
    kind = (dist or {}).get("kind")
    if kind == "normal":
        lam, sd = float(dist["lam"]), float(dist["sd"])
        if sd <= 0:
            return None
        lo, hi = max(0, int(lam - 8 * sd)), int(lam + 8 * sd) + 2
        out = {k: _phi((k + 0.5 - lam) / sd) - _phi((k - 0.5 - lam) / sd) for k in range(lo, hi)}
        out[lo] = out.get(lo, 0.0) + _phi((lo - 0.5 - lam) / sd)        # the (negligible) mass below the range
        return out
    if kind == "hs_bb":
        import perf
        return perf.hs_pmf(float(dist["lam_kills"]), dist.get("scope") or "12", float(dist["hs_per_kill"]), c=dist.get("c"))
    return None


def view(grade):
    """{"pmf": [[total, p], ...], "mean", "median", "lo", "hi", "interval", "line", "over", "under", "push"} or None.
    Memoised on the distribution, line and raw chances (one computation per line however many members open it)."""
    g = grade or {}
    try:
        return _view(json.dumps({"dist": g.get("dist"), "line": g.get("line"), "probs_raw": g.get("probs_raw")}, sort_keys=True))
    except (TypeError, ValueError):
        return None


@functools.lru_cache(maxsize=2048)
def _view(key):
    g = json.loads(key)
    try:
        p, line, raw = pmf(g.get("dist")), float(g["line"]), g.get("probs_raw") or {}
    except (KeyError, TypeError, ValueError):
        return None
    if not p or raw.get("over") is None:
        return None
    over = sum(v for k, v in p.items() if k > line)
    under = sum(v for k, v in p.items() if k < line)
    push = sum(v for k, v in p.items() if k == line)
    if max(abs(over - raw["over"]), abs(under - raw["under"]), abs(push - (raw.get("push") or 0.0))) > CHECK_TOL:
        return None                                   # not the distribution behind this grade: no chart
    ks = sorted(p)
    acc, q = 0.0, {}
    for k in ks:
        acc += p[k]
        for name, at in (("lo", 0.10), ("median", 0.50), ("hi", 0.90), ("a", 0.005), ("b", 0.995)):
            if name not in q and acc >= at:
                q[name] = k
    shown = [[k, round(p[k], 4)] for k in ks if q.get("a", ks[0]) <= k <= q.get("b", ks[-1])]
    return {"pmf": shown, "mean": round(sum(k * v for k, v in p.items()), 2), "median": q.get("median"), "lo": q.get("lo"),
            "hi": q.get("hi"), "interval": 0.8, "line": line, "over": round(over, 4), "under": round(under, 4), "push": round(push, 4),
            "note": "The model's distribution for this total, before any chance cap"}
