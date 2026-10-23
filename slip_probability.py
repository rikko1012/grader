"""Probability semantics for saved slips. Decision scores and no-push chances are not all-win probabilities.

closeout slipsem 2026-10-23 - ONE probability per leg, the forecast's own: a slip multiplies each leg's SHOWN chance (the
verdict's p: the card / board / Studio number, after the grader's caps, margins and - old pipeline only - the FORM FIGHT
halving) times that leg's chance of not pushing (the same grade's raw push mass; 0 on x.5 lines). Basis "shown_chance".
Before, a leg with saved outcomes multiplied the RAW over / under mass (before those adjustments), so a capped or halved
leg entered the slip at another chance than the one the member saw on it. Records saved before this keep their own
numbers and basis ("model_unconditional" / "conditional_no_push" / "display_estimate")."""
import math


def outcomes(value):
    """Validate a forecast's rounded raw distribution; never synthesize it from a capped decision score."""
    if not isinstance(value, dict):
        return None
    try:
        vals = [float(value[k]) for k in ("over", "under", "push")]
    except (KeyError, TypeError, ValueError):
        return None
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in vals):
        return None
    total = sum(vals)
    if abs(total - 1) > 0.0002:
        return None
    return dict(zip(("over", "under", "push"), (v / total for v in vals)))


def _shown(leg):
    try:
        p = float(leg.get("p"))
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and 0 <= p <= 1 else None


def leg_chance(leg):
    """The leg's all-win factor: its shown chance x (1 - its push chance), or None without a valid shown chance / outcomes."""
    p, raw = _shown(leg), outcomes(leg.get("model_outcomes"))
    if p is None or raw is None or leg.get("side") not in ("More", "Less"):
        return None
    return p * (1 - raw["push"])


def summary(legs):
    """All-win estimate for independent legs: the shown chances, each times its no-push chance. Legs without saved
    outcomes (older records) stay conditional on no push and are labelled so."""
    f = [leg_chance(leg) for leg in legs]
    if legs and all(x is not None for x in f):
        p = math.prod(f)
        return {"all_hit": round(p, 6), "all_hit_basis": "shown_chance", "log_chance": round(math.log(p), 6) if p > 0 else None}
    # Historic / mixed records: keep their displayed-score product and label the condition explicitly.
    p = math.prod(float(leg["p"]) for leg in legs)
    whole = any(leg.get("market_type") != "moneyline" and leg.get("line") is not None and float(leg["line"]).is_integer() for leg in legs)
    return {"all_hit": round(p, 6), "all_hit_basis": "conditional_no_push" if whole else "display_estimate",
            "log_chance": round(math.log(p), 6) if p > 0 else None}
