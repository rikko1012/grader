"""Grade policy sports-gp-1 (REGISTER.md 8) - sports bands, separate from every esports band. Pure python (the API
uses it too).

Input: the UNROUNDED, calibrated, unconditional probability of each OFFERED side. Output fields are separate:
validation_state, band, grade, stake_units, stake_reason, data_quality. While a (model version, market) is not
forward-validated the grade is None and members see the concise validation text - the band is kept for the shadow
scorecard (it is what the grade WOULD be), never shown as confidence.
"""
from __future__ import annotations

VERSION = "sports-gp-1"
STAKE_POLICY = "sports-sp-1"
# Underdog published per-pick break-evens (sports/AUDIT.md 1): lowest 53.45% (standard 2), highest 55.59% (flex 4)
BE_LOW, BE_HIGH = 0.5345, 0.5559
BANDS = (("A", 0.60), ("B", BE_HIGH), ("C", BE_LOW))
TEXT_UNVALIDATED = "Projection only - model not yet validated"
TEXT_HISTORY = "Projection - validated on past games, not yet on live lines"
STATES = ("unvalidated", "history-validated", "forward-validated")


def band(p):
    for letter, lo in BANDS:
        if p >= lo:
            return letter
    return "Lean"


def _choose(offered, mean=None, line=None, p_all=None):
    """The graded side among the offered (side, p) pairs -> (side, p) or None.
    SPORTS_GP_SIDE_FIX (default OFF = the old max(), which returned Over on an exact tie and the only offered side
    whatever its chance; =1 turns on, not walk-forward passed): (d) an exact tie between two offered sides goes to the side of the projection mean (mean above
    the line Over, below Under), else no side; (b) a single offered side the model rates under 50% (an offer-forced side,
    audit_1121 hit 34.9%) is not chosen - no letter, no Lean. "Disfavours" compares the two sides' UNCONDITIONAL chances
    (p_all = {"over": p, "under": p}): on an integer line the push mass can leave the favoured side below 0.5."""
    from ..grade_rules import side_fix_on
    if not side_fix_on():
        return max(offered, key=lambda x: x[1])
    if len(offered) == 2 and offered[0][1] == offered[1][1]:
        if mean is None or line is None or mean == line:
            return None
        want = "over" if mean > line else "under"
        return next(x for x in offered if x[0] == want)
    side, p = max(offered, key=lambda x: x[1])
    if len(offered) == 1 and p is not None:
        other = (p_all or {}).get("under" if side == "over" else "over")
        if (p < other) if isinstance(other, (int, float)) else (p < 0.5):
            return None
    return side, p


def apply(p_over, p_under, sides_available, validation_state="unvalidated", data_quality=(), stake_gate_passed=False,
          mean=None, line=None):
    """-> dict. Unavailable sides are 'unavailable' (never a probability presented as tradable). mean / line (optional)
    break an exact tie (_choose)."""
    sides = {}
    for s, p in (("over", p_over), ("under", p_under)):
        sides[s] = {"available": s in sides_available, "p": p if s in sides_available else None}
    offered = [(s, p) for s, p in (("over", p_over), ("under", p_under)) if s in sides_available]
    if not offered:
        return {"policy": VERSION, "validation_state": validation_state, "chosen_side": None, "band": None,
                "grade": None, "label": "No side offered", "stake_units": 0.0, "stake_reason": "no side offered",
                "data_quality": list(data_quality), "sides": sides}
    pick = _choose(offered, mean, line, {"over": p_over, "under": p_under})
    if pick is None:
        tie = len(offered) == 2
        return {"policy": VERSION, "validation_state": validation_state, "chosen_side": None, "band": None,
                "grade": None, "label": "No side: even chance" if tie else "No grade: Scout favours the side not offered",
                "stake_units": 0.0, "stake_reason": "even chance" if tie else "model side not offered",
                "data_quality": list(data_quality), "sides": sides}
    side, p = pick
    b = band(p)
    label = (f"Lean {side.capitalize()}" if b == "Lean" else f"{b} {side.capitalize()}")
    validated = validation_state == "forward-validated"
    if not validated:
        reason = "model not forward-validated (registered gate F not passed)"
    elif not stake_gate_passed:
        reason = "stake gate not passed"
    elif data_quality:
        reason = "data quality: " + ", ".join(data_quality)
    else:
        reason = None
    stake = 0.0 if reason else _stake(b)
    return {"policy": VERSION, "validation_state": validation_state, "chosen_side": side, "chosen_p": p, "band": b,
            "grade": b if validated else None,
            "label": label if validated else (TEXT_HISTORY if validation_state == "history-validated" else TEXT_UNVALIDATED),
            "stake_units": stake, "stake_reason": reason, "stake_policy": STAKE_POLICY,
            "data_quality": list(data_quality), "sides": sides}


def _stake(b):
    """sports-sp-1 (only reachable after gate F): A 1.0U, B 0.5U, C / Lean 0U. Never used before validation."""
    return {"A": 1.0, "B": 0.5}.get(b, 0.0)
