"""awp-hs-1 (LIVE from 2026-11-12, owner-approved 2026-11-11): the "AWPer on a headshot prop" 0-unit gate is lifted.

The rule itself lives in grader.grade_leg (Gate A; grader.awp_hs_1_enabled): an AWPer's headshot prop is staked on its
chance like any other pick - every other 0-unit rule and cap still applies; side, chance, letter and projection are
untouched. Rollback without a deploy: CS2_AWP_HS_LIFT=0 (restores the exact old 0U gate).
Evidence and caveats: FACTOR_AUDIT.md round 8 (G6), MODEL_VERSIONS.md, cs2_scout_ops/logs/grade_upgrade_cs2_2026-11-11.md.

This module holds the helpers around it: the before/after stake rebuild from logged rows (previews, audits). It is not
imported by the grading path.
"""
import re

import grader

VERSION = grader.AWP_HS_1_VERSION
REASON = grader.AWP_HS_GATE_REASON                   # the old Gate A reason ("0 units: ..." flag while rolled back)
NOTE = grader.AWP_HS_1_NOTE
enabled = grader.awp_hs_1_enabled


# ---------------------------------------------------------------- stake rebuild from logged rows (no grader inputs needed)
def preview_units(p_side, flags, side):
    """The stake a logged board row carries with Gate A lifted, from its logged chance and flag texts (the same caps
    grade_leg applied when it made the row). side = "More" | "Less". Returns (units, why)."""
    flags = flags or []
    zero = [f[len("0 units: "):] for f in flags if f.startswith("0 units: ")]
    other = [z for z in zero if z != REASON]
    if other:
        return 0.0, "still 0U: " + "; ".join(other)
    fight = next((f for f in flags if f.startswith("FORM FIGHT")), None)
    if fight and (("says Less" in fight and side == "Less") or ("says More" in fight and side == "More")):
        return 0.0, "still 0U: FORM FIGHT"
    cap = None
    for f in flags:
        tape = f.startswith("TAPE FIGHT")
        if tape and not (("model Less" in f and side == "Less") or ("model More" in f and side == "More")):
            continue
        m = re.search(r"(?:max|capped) (\d+(?:\.\d+)?)U", f)
        if m:
            cap = float(m.group(1)) if cap is None else min(cap, float(m.group(1)))
    u = grader.units_for_p(p_side)
    return (min(u, cap) if cap is not None else u), ("cap %gU" % cap if cap is not None and cap < u else "")
