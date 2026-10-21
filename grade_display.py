"""What members see for one verdict, as three separate fields (owner 2026-10-15, display version gd-1):

  model grade   the letter the model's chance earns - the grader's own letter for that side (grader.grade_from_p on the
                UNROUNDED published chance, existing thresholds: B- 55%, B 57%, B+ 63%, A- 68%, A 74%, A+ 80%); under
                55% it is a Lean. A stake rule never turns a graded pick into a Lean.
  stake         the recommended units - unchanged: the grader's stake rules and the board's same-match exposure cap.
                stake_reason says why a graded pick has 0 units (AWPer on a headshot prop, FORM FIGHT, the same-match
                cap, a small sample, ...).
  availability  whether the platform offers that exact side now (a side not offered stays unavailable: never copied,
                never on a slip).

DISPLAY ONLY. pick_verdict.verdict (in the model hash) and every logged forecast keep their grade exactly as before
(a 0-unit pick is logged 'LEAN'), so evaluation by model version and grade cohort is unchanged - a relabelled grade is
never a better model. Eligibility everywhere stays on units > 0 and availability, never on this letter.
Not in the model hash (MODEL_VERSIONS.md); versioned by DISPLAY_VERSION."""
import re

DISPLAY_VERSION = "gd-1"
LETTERS = ("A+", "A", "A-", "B+", "B", "B-")


def _letter_from_p(p):
    from grader import grade_from_p
    return grade_from_p(p) if p is not None else "C"


def model_grade(g, v):
    """The probability-earned grade of the verdict's side ('LEAN' under 55%); SCOUT / no grade pass through."""
    v, g = v or {}, g or {}
    if not v.get("side") or v.get("state") == "SCOUT" or v.get("grade") == "SCOUT":
        return v.get("grade")
    letter = g.get("grade") if v["side"] == "More" else g.get("grade_less")
    if letter not in LETTERS + ("C",):
        # the grader has no letter for this side (a side the feed does not offer: grade_less = 'n/a ...'): the letter of
        # the verdict's own (3-decimal) chance - the side stays unavailable either way
        letter = _letter_from_p(v.get("p"))
    return letter if letter in LETTERS else "LEAN"


def _zero_reason(g, v):
    if g.get("zero_reason"):
        return str(g["zero_reason"])
    if g.get("form_fight"):
        return "FORM FIGHT - his last 10 matches at this line disagree with the projection"
    return None


def annotate(g, v, row=None):
    """The display fields for one verdict (a dict to merge into the verdict / board row). row: the board row, when the
    board changed the stake (same-match cap) or knows the side is no longer offered."""
    v, g, row = v or {}, g or {}, row or {}
    mg = model_grade(g, v)
    side = v.get("side")
    no_less = side == "Less" and not g.get("less_available", g.get("line_type", "standard") == "standard")
    gone = bool(row.get("unavailable"))
    available = not (no_less or gone)
    if no_less:
        why_na = f"{str(g.get('line_type') or 'this').title()} line - Under is not offered"
    elif gone:
        why_na = "this side is no longer offered" if row.get("unavailable") is True else str(row.get("unavailable"))
    else:
        why_na = None
    units = row.get("units", v.get("units")) or 0
    stake_reason = None
    if mg in LETTERS and not units and available:
        if row.get("cap_n") and row.get("units_full"):
            stake_reason = f"same-match exposure cap ({row['cap_n']} bets on this team)"
        else:
            stake_reason = _zero_reason(g, v) or (v.get("reason") or "a stake rule held it at 0 units")
    return {"model_grade": mg, "stake_reason": stake_reason, "available": available, "availability_reason": why_na,
            "display_version": DISPLAY_VERSION}


def annotate_item(x):
    """A 'Nearby lines' chip (cs2_scout._ladder: side, p, grade, units, exact_grade): the card's own model_grade for that
    line when the chip carries it (2026-10-21 audit no 29: the 3-decimal chance turned an unrounded 54.97% Lean into 'B-');
    otherwise (older saved chips / rows) its letter from its own 3-decimal chance."""
    x = x or {}
    if not x.get("side") or x.get("state") == "SCOUT":
        return {"model_grade": x.get("grade"), "display_version": DISPLAY_VERSION}
    if x.get("exact_grade"):
        return {"model_grade": x["exact_grade"], "display_version": DISPLAY_VERSION}
    if x.get("grade") in LETTERS:
        return {"model_grade": x["grade"], "display_version": DISPLAY_VERSION}
    letter = _letter_from_p(x.get("p"))
    return {"model_grade": letter if letter in LETTERS else "LEAN", "display_version": DISPLAY_VERSION}


_ZERO_RE = re.compile(r"0 units:\s*(.+?)(?:\s+-\s+|$)")


def annotate_saved(row):
    """A saved board row graded before gd-1 (no grader letters kept): its letter from its own 3-decimal chance and the
    restriction from its own reason text; replaced by the exact fields at the row's next regrade (<= 15 min)."""
    d = annotate_item(row)
    units = row.get("units") or 0
    sr = None
    if d["model_grade"] in LETTERS and not units and not row.get("unavailable"):
        m = _ZERO_RE.search(str(row.get("reason") or ""))
        sr = m.group(1).strip() if m else ("FORM FIGHT - his last 10 matches at this line disagree with the projection"
                                           if "FORM FIGHT" in str(row.get("reason") or "") else "a stake rule held it at 0 units")
        if sr.startswith("same-match cap"):
            sr = sr.replace("same-match cap", "same-match exposure cap", 1)
    return dict(d, stake_reason=sr, available=not row.get("unavailable"), display_version=DISPLAY_VERSION + " (saved row)")
