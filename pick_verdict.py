"""The member verdict for one grade: side, Lean/letter grade, stake and the reason - shared by the card (server.py), the
board and the card's 'Nearby lines' ladder (cs2_scout._ladder), so every surface says the same thing for a line.
Display / wording only: the chance and the stake are the grader's (grader.grade_leg)."""
import re


# plain words for the grade flags that cap a stake (board rows / card: why a stake is below what its chance earns)
_CAP_WORDS = (("Gate D", "small sample"), ("Thin data", "small sample"), ("SOFT BOOK", "thin market"),
              ("NO MARKET", "no betting market"), ("STAND-IN", "stand-in"), ("RUST", "long break"),
              ("TAPE FIGHT", "recent record disagrees"))
_CAP_RE = re.compile(r"(?:max|capped) ([0-9.]+)U")
# the market-quality cap on a bookmaker price (Thunderpick / an OddsPapi book other than Pinnacle or Betfair Exchange)
# is not a thin market (2026-10-13 audit: every Thunderpick card said "thin market"): named by the odds source the
# grade used when the caller knows it, else by the flag's own text (the grader names the source in it)
CAP_WORD_BOOKMAKER = "bookmaker price"
_BOOKMAKER_RE = re.compile(r"thunderpick|bookmaker|oddspapi", re.I)


def _cap_word(f, source=None):
    if f.startswith("SOFT BOOK"):
        if source in ("thunderpick", "oddspapi") or (source is None and _BOOKMAKER_RE.search(f)):
            return CAP_WORD_BOOKMAKER
        return "thin market"
    return next((w for k, w in _CAP_WORDS if f.startswith(k)), None)


def stake_cap_reasons(g, v, source=None):
    """The grade-flag cap that actually cut this stake, in plain words (display only - the stake is the grader's).
    A cap counts only when the stake is below what the chance alone earns: the grader's own uncapped stake
    (units_free, from the unrounded chance - the 3-decimal chance can sit across a units boundary), else
    grader.units_for_p(p) for grades made before it existed. Only the smallest cap binds (the stake equals it), so only
    that one is named - a looser cap that also applied did not cut anything. 'Roster not confirmed' is a warning, not a
    cap; a 0-unit Lean has no cap; the recent-record cap only counts on the side it applies to.
    source: the odds source the grade used ('thunderpick' / 'polymarket' / 'oddspapi'), when the caller knows it."""
    from grader import units_for_p
    v, g = v or {}, g or {}
    u, p, side = v.get("units") or 0, v.get("p"), v.get("side")
    if not u or p is None:
        return []
    free = g.get("units_free_less" if side == "Less" else "units_free")
    full = free if free is not None else units_for_p(p)
    if u >= full:
        return []
    found = []
    for f in g.get("flags") or []:
        m = _CAP_RE.search(f)
        if not m or float(m.group(1)) >= full:
            continue
        if f.startswith("TAPE FIGHT") and side and g.get("tape_fight") not in (None, side):
            continue
        w = _cap_word(f, source)
        if w:
            found.append((float(m.group(1)), w))
    if not found:
        return []
    low = min(c for c, _ in found)
    out = []
    for c, w in found:
        if c == low and w not in out:
            out.append(w)
    return out


def verdict(g):
    """Always a side: the one the numbers favour, with its grade. Units follow P: B = 0.25U
    (small edge), C = 0U (loses in testing) but is still a pick."""
    if not g or "p_more" not in g:
        return {"side": None, "grade": (g or {}).get("grade", "NO GRADE"), "units": 0.0,
                "reason": (g or {}).get("reason")}
    if g["p_more"] != g["p_less"]:
        side = "More" if g["p_more"] > g["p_less"] else "Less"
    else:
        # a tie at 3 decimals (or exactly 50% - the old pipeline's Over margin stops there): the model's unrounded chance,
        # else the line's real cut. 2026-10-13 audit: was the TYPED line - a 30.7 line is cut at 30.5, so a 30.69
        # projection with the model's Over at 50.65% read as Lean Under
        raw = g.get("p_more_model_raw", (g.get("probs_raw") or {}).get("p_more_no_push"))
        if raw is not None and raw != 0.5:
            side = "More" if raw > 0.5 else "Less"
        else:
            from grader import _cuts
            up, down = _cuts(float(g["line"]))
            side = "More" if g["lambda_final"] > (up + down) / 2 else "Less"
    # a line whose feed does not offer / confirm Less (goblin / demon without a feed statement, or an Over-only line).
    # If the model expects UNDER, say so (Lean
    # Under, 0 units - skip it) instead of headlining a More it thinks loses (azukay 17.5 demon:
    # printed "More 42%", he went under).
    no_less_side = side == "Less" and not g.get("less_available", g["line_type"] == "standard")
    p = g["p_more"] if side == "More" else g["p_less"]
    grade = g["grade"] if side == "More" else (g["grade_less"] if not no_less_side else "C")
    units = g.get("units", 0) if side == "More" else g.get("units_less", 0)
    if no_less_side:
        units = 0.0
    stat = "headshots" if g.get("prop") == "hs" else "kills"
    need = g["line"]                        # the cut the grade uses: projection above the line leans More
    gap = g["lambda_final"] - need          # > 0 More, < 0 Less
    # member wording (2026-10-10 audit): Over / Under everywhere (never More / Less), chances to 0.1% like every other
    # chance on the site, projection and gap to 0.1 like the card's Edge
    ou = "Over" if side == "More" else "Under"
    if g.get("scout"):
        return {"side": side, "state": "SCOUT", "grade": "SCOUT", "p": p, "units": 0.0,
                "confidence": "scout",
                "reason": f"No upcoming match found for his team - scouting view only. Leans {ou} "
                          f"({p:.1%}): projection {g['lambda_final']:.1f} vs line {g['line']}"}
    if no_less_side:
        why = (f"{g['line_type'].title()} line - only Over is offered here, but the model expects "
               f"Under ({p:.1%}); Over is only {g['p_more']:.1%}. Skip it.")
    elif g.get("form_fight"):
        why = f"FORM FIGHT - his last 10 matches at this line disagree with the projection - 0 units"
    elif g.get("tape_fight"):
        why = (f"TAPE FIGHT - model {ou} {p:.1%}, but his recent record at this line disagrees - max 0.5U")
    elif units > 0:
        why = f"Projection {g['lambda_final']:.1f} vs line {need:g} ({gap:+.1f} {stat})"
    else:
        why = (f"Projection {g['lambda_final']:.1f} vs line {need:g} ({gap:+.1f} {stat}) is small next "
               f"to how much his totals swing from match to match - leans {ou} {p:.1%}, 0 units")
    if g.get("zero_reason") and not no_less_side:
        # a gate (AWPer on HS, stand-in, transfer, tiny sample): still a side, never "no play"
        grade, units = "LEAN", 0.0
        why = (f"Leans {ou} {p:.1%} (projection {g['lambda_final']:.1f} vs line {need:g}) - 0 units: "
               f"{g['zero_reason']}")
    if units and "max" not in why:      # a cap cut the stake: name the one that bound, in plain words
        words = stake_cap_reasons(g, {"units": units, "p": p, "side": side})
        if words:
            why += " · stake capped: " + "; ".join(words)
    if grade == "C" or (not units and grade not in ("LEAN", "C")):
        # owner 2026-09-29: under the bar the card says "Lean Over" / "Lean Under" instead of a C; a
        # letter grade with 0 units (FORM FIGHT, gates) reads as a Lean too - never "B · 0U"
        grade = "LEAN"
    return {"side": side, "state": "GRADE", "grade": grade, "p": p, "units": units, "reason": why,
            "market": g.get("market_quality"),
            "confidence": "high" if p >= 0.68 else "medium" if p >= 0.63 else "small" if p >= 0.57 else "low"}
