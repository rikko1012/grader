"""Probability semantics for saved slips. Decision scores and no-push chances are not all-win probabilities."""
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


def summary(legs):
    """All-win estimate for independent legs. Old records remain conditional when their push mass is missing."""
    raw = [outcomes(leg.get("model_outcomes")) for leg in legs]
    if legs and all(raw) and all(l.get("side") in ("More", "Less") for l in legs):
        p = math.prod(r["over" if leg["side"] == "More" else "under"] for leg, r in zip(legs, raw))
        return {"all_hit": round(p, 6), "all_hit_basis": "model_unconditional", "log_chance": round(math.log(p), 6) if p > 0 else None}
    # Historic / mixed records: keep their displayed-score product and label the condition explicitly.
    p = math.prod(float(leg["p"]) for leg in legs)
    whole = any(leg.get("market_type") != "moneyline" and leg.get("line") is not None and float(leg["line"]).is_integer() for leg in legs)
    return {"all_hit": round(p, 6), "all_hit_basis": "conditional_no_push" if whole else "display_estimate",
            "log_chance": round(math.log(p), 6) if p > 0 else None}
