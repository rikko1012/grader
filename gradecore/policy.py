"""Grade policy gp-1, the same for every sport (brief 3.4): p_cal -> letter, EV against break-even, stake, reasons.

- Letter from p_cal, and only when ev > 0: otherwise Lean. There is always a side.
- ev = p_cal / break-even - 1. No known break-even (goblin / demon / promo with no multiplier, a book with no verified
  table) means no letter and no stake.
- Stake = quarter Kelly on p_cal against break-even, whole and half units rounded down, then gate caps. 2U or more is for
  A+ only. A gate cap keeps the letter and gives the reason; a withheld flag (gates.CONTAINED / UNVALIDATED) removes the
  letter and stake but keeps `band`, what the grade would be, for the shadow scorecard.

LETTER_CUTS, like every number in this block, is a starting value: set it again after the first refit so each tier's hit
rate lands inside its band. Nothing live reads this file.
"""
from dataclasses import dataclass

from . import gates, payouts
from .VERSION import GRADE_POLICY_VERSION

LETTER_CUTS = (("A+", 0.72), ("A", 0.66), ("A-", 0.62), ("B+", 0.585), ("B", 0.55))
LEAN = "Lean"
KELLY_FRACTION = 0.25
UNIT_BANKROLL = 0.04            # 1U = 4% of bankroll: a 72% pick at 53.45% break-even comes to 2U
UNIT_STEP = 0.5
MAX_UNITS = 3.0
TOP_ONLY_FROM = 2.0             # stakes at or above this are A+ only
TOP_LETTER = "A+"
ALT_LINE = "Alt line: payout unknown"


@dataclass(frozen=True)
class Grade:
    letter: str | None                  # None = no letter may show (see reasons)
    ev_vs_breakeven: float | None
    stake_units: float
    reasons: tuple = ()
    band: str | None = None             # the letter before any withhold, kept for scoring
    break_even: float | None = None
    policy: str = GRADE_POLICY_VERSION


def letter_for(p, cuts=None):
    for letter, lo in (LETTER_CUTS if cuts is None else cuts):
        if p >= lo:
            return letter
    return LEAN


def kelly(p, be):
    """The Kelly share of bankroll for a bet that breaks even at chance `be`."""
    return max(0.0, (p - be) / (1.0 - be))


def stake_for(p, be, letter):
    if letter == LEAN:
        return 0.0
    u = min(kelly(p, be) * KELLY_FRACTION / UNIT_BANKROLL, MAX_UNITS)
    u = int(u / UNIT_STEP + 1e-9) * UNIT_STEP
    return min(u, TOP_ONLY_FROM - UNIT_STEP) if letter != TOP_LETTER else u


def grade(p_cal, book, variant=payouts.STANDARD_VARIANT, flags=(), entry=None, n=2, multiplier=None, config=None):
    if not 0.0 <= p_cal <= 1.0:
        raise ValueError("p_cal must be a chance between 0 and 1")
    g = gates.evaluate(flags)
    be = payouts.break_even(book, entry, n, variant, multiplier, config)
    if be is None:
        why = ALT_LINE if variant != payouts.STANDARD_VARIANT else f"Break-even unknown for {book}"
        return Grade(None, None, 0.0, (why, *g.withheld, *g.reasons))
    ev = p_cal / be - 1.0
    band = letter_for(p_cal) if ev > 0 else LEAN
    reasons = (*g.withheld, *(() if ev > 0 else ("no edge against break-even",)), *g.reasons)
    if g.withheld:
        return Grade(None, ev, 0.0, reasons, band, be)
    stake = stake_for(p_cal, be, band)
    return Grade(band, ev, stake if g.cap is None else min(stake, g.cap), reasons, band, be)


def grade_offer(p_over, p_under, sides_available, book, variant=payouts.STANDARD_VARIANT, flags=(), **kw):
    """The higher offered side, graded. An unavailable side has no chance shown (sports-gp-1 rule). -> dict."""
    sides = {s: {"available": s in sides_available, "p": p if s in sides_available else None}
             for s, p in (("over", p_over), ("under", p_under))}
    offered = [(s, p) for s, p in (("over", p_over), ("under", p_under)) if s in sides_available]
    if not offered:
        return {"policy": GRADE_POLICY_VERSION, "chosen_side": None, "chosen_p": None, "label": "No side offered",
                "grade": None, "sides": sides}
    side, p = max(offered, key=lambda x: x[1])
    g = grade(p, book, variant, flags, **kw)
    return {"policy": GRADE_POLICY_VERSION, "chosen_side": side, "chosen_p": p,
            "label": f"{g.letter} {side.capitalize()}" if g.letter else g.reasons[0], "grade": g, "sides": sides}
