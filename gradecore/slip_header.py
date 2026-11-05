"""Competitor spec 4.3: the live slip header (SCOUT_SLIP_HEADER_V2, default off). Composes the section-3 math only:
payouts.tiers / entry_break_even (verified tables), slip.slip_ev (Poisson-binomial, pushes removed), slip.simulate (Gaussian
copula, 20k seeded draws, 95% interval) where a validated same-match correlation exists, slip.stake (quarter-Kelly, display
only) and fair.leg_ev. Every entry type of the book is computed in one call so the Power / Flex toggle needs no new request.

Each leg's chance is the chance the board prints (the spec's p_cal while calibration is off). A leg without one is unrated
and the header says how many; a same-match pair without a validated correlation cannot be rated. Nothing here places a slip,
changes a grade or sizes a grader stake.
"""
from . import fair, payouts
from . import slip as gslip

NAME = "sh-2"
SPEC_CHECK = {"probs": (0.60, 0.60), "book": "prizepicks", "entry": "power", "win": 0.36, "ev": 0.08}


def no_correlation(a, b):
    """gradecore's default: legs in different matches are independent; a same-match pair has no validated estimate."""
    return 0.0 if a.get("match_key") != b.get("match_key") else None


def _chance(leg):
    p = leg.get("p")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 < p < 1:
        return None
    return float(p)


def _r(x, d=4):
    return None if x is None else round(float(x), d)


def entry_header(legs, book, entry, correlation=no_correlation, kelly=gslip.KELLY_FRACTION, config=None):
    """One entry type. legs: [{"p" (printed chance on the chosen side or None), "match_key", "unpriced" (reason the book
    sets this pick's payout elsewhere, or None)}].
    -> {"entry", "n", "payout", "break_even", "break_even_american", "rated", "reason"?, "win", "ev", "ev_low"/"ev_high",
    "kelly_units", "legs": [{"index", "p", "need", "below", "ev"}], "basis"}"""
    n = len(legs)
    out = {"entry": entry, "n": n, "rated": False}
    t = payouts.tiers(book, entry, n, config)
    if t is None:
        return dict(out, reason=f"No published {entry} payout for {n} picks.")
    unpriced = next((l["unpriced"] for l in legs if l.get("unpriced")), None)
    if unpriced:
        return dict(out, reason=unpriced)
    be = payouts.entry_break_even(book, entry, n, config)
    out.update(payout=t.get(n), tiers={str(k): v for k, v in sorted(t.items())}, break_even=_r(be),
               break_even_american=None if be is None else round(payouts.american_of(be)))
    probs = [_chance(l) for l in legs]
    out["legs"] = [{"index": i, "p": _r(p), "need": _r(be), "below": p is not None and be is not None and p < be,
                    "ev": _r(fair.leg_ev(p, be)) if p is not None and be is not None else None} for i, p in enumerate(probs)]
    unrated = sum(p is None for p in probs)
    if unrated:
        return dict(out, unrated=unrated, reason=f"Can't rate: {unrated} unrated {'leg' if unrated == 1 else 'legs'}.")
    keys = [l.get("match_key") for l in legs]
    same = len(set(keys)) < n or any(k is None for k in keys)
    if same:
        m = gslip.correlation_matrix(legs, correlation)
        if m is None:
            return dict(out, reason="Can't rate: same-match picks have no tested correlation.")
        sim = gslip.simulate(probs, t, m)
        if not sim.get("available"):
            return dict(out, reason="Can't rate: same-match picks have no tested correlation.")
        dist, ev = sim["hit_dist"], sim["ev"]
        out.update(rated=True, correlated=True, win=_r(sim["p_all_hit"]), win_low=_r(sim["p_all_hit_low"]),
                   win_high=_r(sim["p_all_hit_high"]), ev=_r(ev), ev_low=_r(sim["ev_low"]), ev_high=_r(sim["ev_high"]),
                   basis=sim["basis"])
    else:
        s = gslip.slip_ev(probs, t)
        dist, ev = s["hit_dist"], s["ev"]
        out.update(rated=True, correlated=False, win=_r(s["p_all_hit"]), ev=_r(ev), basis=s["basis"])
    out["paid"] = _r(sum(p for k, p in enumerate(dist) if t.get(k, 0) > 0))
    st = gslip.stake(gslip.returns_of(dist, t), "kelly", kelly)
    out.update(kelly_fraction=kelly, kelly_units=round(st["units"], 2))
    return out


def header(legs, book, entries=None, correlation=no_correlation, kelly=gslip.KELLY_FRACTION, config=None):
    """Every entry type of `book` (or `entries`) for one slip -> {"version", "book", "n", "unrated", "types": {entry: ...}}.
    None for an empty slip or a book with no verified table."""
    cfg = config if config is not None else payouts.pickem_config()
    legs = list(legs or ())
    if not legs or book not in cfg:
        return None
    names = list(entries or (cfg[book].get("entries") or {}))
    types = {e: entry_header(legs, book, e, correlation, kelly, cfg) for e in names}
    return {"version": NAME, "book": book, "n": len(legs), "unrated": sum(_chance(l) is None for l in legs),
            "source": cfg[book].get("source"), "types": types}
