"""awp-hs-1 (CANDIDATE, 2026-11-11, default OFF): lift the "AWPer on a headshot prop" 0-unit gate.

What it is. Gate A in grader.grade_leg gives 0 units to every headshot prop of a player the AWP rule marks as a sniper
(sniping > 40). The side, the chance, the letter and the projection are untouched by that gate - it only removes the
stake. This candidate returns the same grade with the stake the chance earns (every other cap and every other 0-unit
rule still applies: stand-in, sample, FORM FIGHT, the BO3.gg-route gate, TAPE FIGHT / SOFT / NO MARKET / rust caps).

Why. cs2_scout_ops/logs/grade_upgrade_cs2_2026-11-11.md (rule G6, frozen before scoring in
research/grade_upgrade_2026_11_11/FROZEN_DEFINITIONS.md; named on 2026-10-28 as sr-gates-1's forward re-test).

Status. NOT WIRED INTO THE LIVE PATH. The live grade is made by cs2_scout.py -> grader.grade_leg; both files are part
of picklog.MODEL_VERSION and are not edited here, so the model tag and every live stake are unchanged whatever the
environment says. Switching it on is an owner decision and a new model tag: route the grade through `grade()` below
(one call site), set CS2_AWP_HS_LIFT=1, add the MODEL_VERSIONS.md entry. CS2_AWP_HS_LIFT=0 / unset = today's grades.

Never changes a side, a chance, a letter or a projection: if the re-grade differs in any of them the original grade is
returned unchanged.
"""
import os

VERSION = "awp-hs-1"
REASON = "AWPer on a headshot prop"                 # grader's Gate A reason text
NOTE = "AWPer on a headshot prop: stake allowed (awp-hs-1)"
SNIPING_GATE = 40                                    # grader Gate A fires on sniping > 40
_SAME = ("p_more", "p_less", "grade", "grade_less", "model_grade", "lambda_final", "sd", "probs_raw",
         "projection_source", "status_side", "line", "prop", "line_type")


def enabled(cfg=None):
    """Off unless CS2_AWP_HS_LIFT=1 (read at grade time). A cfg key "awp_hs_lift" overrides for tests / previews."""
    if cfg is not None and cfg.get("awp_hs_lift") is not None:
        return bool(cfg["awp_hs_lift"])
    return os.environ.get("CS2_AWP_HS_LIFT", "0").strip() == "1"


def gated(d):
    """True when Gate A (and nothing else decided here) would zero this leg's stake."""
    snip = (d or {}).get("sniping")
    return (d or {}).get("prop") == "hs" and snip is not None and snip > SNIPING_GATE


def lifted_inputs(d):
    """A copy of the grader inputs with the sniping value set just below the gate; None when Gate A does not apply.
    `sniping` is read by grade_leg for Gate A only (checked by tests/test_awp_hs_lift.py)."""
    if not gated(d):
        return None
    d2 = dict(d)
    d2["sniping"] = SNIPING_GATE
    return d2


def grade(d, cfg=None, grade_leg=None):
    """grader.grade_leg(d), with Gate A's 0 units lifted when the candidate is enabled."""
    if grade_leg is None:
        import grader
        grade_leg = grader.grade_leg
    g = grade_leg(d, cfg) if cfg is not None else grade_leg(d)
    if not enabled(cfg):
        return g
    d2 = lifted_inputs(d)
    if d2 is None or g.get("status") in ("NO GRADE",):
        return g
    g2 = grade_leg(d2, cfg) if cfg is not None else grade_leg(d2)
    for k in _SAME:
        if g2.get(k) != g.get(k):
            return g                                  # never anything but the stake
    g2["awp_hs_lift"] = {"version": VERSION, "units_before": g.get("units"), "units_less_before": g.get("units_less"),
                         "units": g2.get("units"), "units_less": g2.get("units_less")}
    g2.setdefault("flags", []).append(NOTE)
    return g2


# ---------------------------------------------------------------- preview from logged rows (no grader inputs needed)
def units_for_p(p):
    return 3.0 if p >= 0.80 else 2.0 if p >= 0.74 else 1.5 if p >= 0.68 else 1.0 if p >= 0.63 else 0.5 if p >= 0.55 else 0.0


def preview_units(p_side, flags, side):
    """The stake a logged board row would carry with Gate A lifted, from its logged chance and flag texts (the same caps
    grade_leg applied when it made the row). side = "More" | "Less". Returns (units, why)."""
    import re
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
    u = units_for_p(p_side)
    return (min(u, cap) if cap is not None else u), ("cap %gU" % cap if cap is not None and cap < u else "")
