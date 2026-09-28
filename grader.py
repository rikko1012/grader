#!/usr/bin/env python3
"""
grader.py - CS2 PrizePicks grading logic v7.2 (Maps 1-2 kills + headshots).

Pure math, no scraping. The web app sends it the scraped stats plus what the
member entered (player, prop type, line, line type, moneylines) and gets back
lambda, P(More), P(Less), grade, flags, units and market edge.

Use:  python grader.py leg.json          -> prints the graded leg
      from grader import grade_leg, grade_pair, log_pick, record_result, calibration
"""
import csv
import json
import math
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- settings
CONFIG = {
    "c": 1.10,                   # SD constant, calibrate from logged results (1.0-1.15)
    "rounds_per_map": 23,        # CS2 MR12 standard (46 over Maps 1-2)
    "stomp_rounds_per_map": 20,  # stomp (40 over Maps 1-2)
    "stomp_ml": -400,            # favorite at -400 or heavier = stomp
    "recency_trend": 0.15,       # last-5 KPR off window by 15%+ = "trending hard"
    "sd_widen": 1.10,            # per risk condition
    "sd_widen_max": 1.25,
    "poor_dpr": 0.72,            # poor DPR + poor KAST together = widen SD
    "poor_kast": 68.0,
    "underdog_hs_min": 65.0,     # underdog HS prop needs this HS% to survive
    "support_trading": 60,       # high Trading + low Firepower = support flag
    "support_firepower": 40,
    "h2h_min_maps": 2,           # H2H counts from 1 series (2 maps); was 4 - changed 2026-09-27 at owner's request
    "pp_implied": {"standard": 0.485, "goblin": 0.65, "demon": 0.35},
    "break_even": {"2-pick 3x": 0.577, "3-pick 6x": 0.550, "3-pick 5x": 0.585,
                   "4-pick 10x": 0.562, "5-pick 20x": 0.549, "6-pick 37.5x": 0.547},
}
GRADES = ["C", "B", "B+", "A-", "A", "A+"]


# ---------------------------------------------------------------- math
def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def american_to_prob(ml):
    ml = float(ml)
    return -ml / (-ml + 100) if ml < 0 else 100 / (ml + 100)


def win_prob(team_ml=None, opp_ml=None):
    """De-vigged P(win) when both sides are given."""
    if team_ml is not None and opp_ml is not None:
        a, b = american_to_prob(team_ml), american_to_prob(opp_ml)
        return a / (a + b), None
    if team_ml is not None:
        return american_to_prob(team_ml), "Only one moneyline given - P(win) includes vig"
    return 0.5, "No moneyline - P(win) set to 50%, blowout/win-loss neutral"


def grade_from_p(p):
    if p >= 0.80: return "A+"
    if p >= 0.74: return "A"
    if p >= 0.68: return "A-"
    if p >= 0.63: return "B+"
    if p >= 0.57: return "B"
    return "C"


def p_more(line, lam, sd):
    return 1 - phi((line + 0.5 - lam) / sd)


def units_for(grade, small_sample):
    u = {"A+": 2.0, "A": 1.5, "A-": 1.5, "B+": 1.0, "B": 0.5}.get(grade, 0.0)
    return min(u, 1.0) if small_sample else u


# ---------------------------------------------------------------- one leg
def grade_leg(d, cfg=CONFIG):
    """
    d keys (None/missing = unknown):
      prop "kills"|"hs", line, line_type "standard"|"goblin"|"demon"
      maps_played, kpr, hs_pct, kpr_last5, round_swing (percent, e.g. 2.1)
      per_map [{map, kpr, hs_pct, maps_played}]  (prop maps, in order)
      rating, teammate_ratings [4 numbers], is_igl
      sniping, firepower, trading, opening, dpr, kast
      team_ml, opp_ml (American odds for the player's team / opponent)
      on_listed_team, is_standin, roster_confirmed
      h2h {maps, hit_rate, clears_in_wins, clears_in_losses}
    """
    prop = d.get("prop", "kills")
    line = float(d["line"])
    line_type = d.get("line_type", "standard")
    flags, sd_reasons = [], []
    out = {"prop": prop, "line": line, "line_type": line_type, "flags": flags}

    def stop(status, why):
        out.update(status=status, grade=status, units=0.0, reason=why)
        return out

    # Gate C - live roster
    if d.get("on_listed_team") is False:
        return stop("NO GRADE", "Gate C: player is not on the listed team (transfer)")
    if d.get("is_standin"):
        return stop("NO GRADE", "Gate C: player is a stand-in")
    regional_ok = d.get("roster_confirmed") is True
    if not regional_ok:
        flags.append("Gate C: roster not confirmed - regional H2H extension disabled")
        sd_reasons.append("unconfirmed roster/role")

    # Gate D - sample size
    maps = d.get("maps_played")
    if maps is None or maps < 5:
        return stop("NO GRADE", f"Gate D: {maps} maps on deepest filter (need 5+)")
    small = maps < 10
    if small:
        flags.append(f"Gate D: only {maps} maps - variance flag, SD inflated, capped 1U")
        sd_reasons.append("sample <10 maps")

    # Gate A - AWPer on HS
    snip = d.get("sniping")
    if prop == "hs":
        if snip is None:
            flags.append("Gate A: Sniping unknown - fetch before playing an HS prop")
        elif snip > 40:
            return stop("NO PLAY", f"Gate A: Sniping {snip}/100 - AWPer on HS prop")

    # Win probability + stomp
    p_win, note = win_prob(d.get("team_ml"), d.get("opp_ml"))
    if note:
        flags.append(note)
    mls = [float(x) for x in (d.get("team_ml"), d.get("opp_ml")) if x is not None]
    stomp = any(m <= cfg["stomp_ml"] for m in mls)
    rpm = cfg["stomp_rounds_per_map"] if stomp else cfg["rounds_per_map"]
    if stomp:
        sd_reasons.append("stomp compression")

    # KPR + recency blend
    kpr = d.get("kpr")
    if not kpr:
        return stop("NO GRADE", "Missing KPR - fetch trigger")
    ratio = 1.0
    k5 = d.get("kpr_last5")
    if k5:
        trend = (k5 - kpr) / kpr
        if abs(trend) >= cfg["recency_trend"]:
            blended = 0.7 * kpr + 0.3 * k5
            ratio = blended / kpr
            flags.append(f"Last 5 trending {trend:+.0%} - 70/30 blend applied (KPR {kpr:.3f} -> {blended:.3f})")

    hs_pct = d.get("hs_pct")
    if prop == "hs" and hs_pct is None:
        return stop("NO GRADE", "Missing HS% - fetch trigger")

    # Base lambda over Maps 1-2 (map filter when maps known)
    parts = []
    for m in (d.get("per_map") or [])[:2]:
        if m.get("kpr") and (m.get("maps_played") or 0) >= 5:
            parts.append((m.get("map"), m["kpr"], m.get("hs_pct") or hs_pct))
        else:
            flags.append(f"{m.get('map')}: under 5 maps on this map - using all-maps numbers")
            parts.append((m.get("map"), kpr, hs_pct))
    while len(parts) < 2:
        parts.append(("unknown", kpr, hs_pct))
    lam = 0.0
    for _, k, h in parts:
        lam += k * ratio * rpm * ((h / 100) if prop == "hs" else 1)

    # 3a Role
    rating, mates = d.get("rating"), [t for t in (d.get("teammate_ratings") or []) if t is not None]
    if d.get("is_igl"):
        role, role_tag = 0.85, "IGL (-15%, out of hierarchy)"
    elif rating is None or len(mates) < 4:
        role, role_tag = 1.0, "unconfirmed (need player + 4 teammate ratings)"
        flags.append("Role unconfirmed - 0% role adjustment")
    else:
        rank = 1 + sum(1 for t in mates if t > rating)
        if rank == 1:
            gap = rating - max(mates)
            role, role_tag = (1.06, "primary, gap >0.10") if gap > 0.10 else (1.05, "primary")
        elif rank == 2:
            role, role_tag = 1.0, "secondary"
        else:
            role, role_tag = 0.90, f"#{rank} on team"
    tr, fp = d.get("trading"), d.get("firepower")
    if tr is not None and fp is not None and tr >= cfg["support_trading"] and fp <= cfg["support_firepower"]:
        flags.append("Support profile (high Trading, low Firepower) - treat as volume cap")

    # 3b Swing, 3c Blowout, cap
    rs = d.get("round_swing") or 0.0
    swing = 1 + clamp(rs * 3, -6, 6) / 100
    bf = 1 + 0.12 * (p_win - 0.5)
    raw_stack = role * swing * bf
    stack = clamp(raw_stack, 0.75, 1.20)
    if stack != raw_stack:
        flags.append(f"Stack capped ({raw_stack:.3f} -> {stack:.3f})")

    # 3d Win/loss split
    lam_adj = lam * stack
    lam_final = p_win * lam_adj * 1.05 + (1 - p_win) * lam_adj * 0.85

    # Confidence -> SD
    dpr, kast = d.get("dpr"), d.get("kast")
    if dpr is not None and kast is not None and dpr >= cfg["poor_dpr"] and kast <= cfg["poor_kast"]:
        sd_reasons.append("poor DPR + KAST")
    if prop == "hs" and p_win < 0.5 and (hs_pct or 0) < cfg["underdog_hs_min"]:
        sd_reasons.append("underdog HS prop")
    sd_mult = min(cfg["sd_widen"] ** len(sd_reasons), cfg["sd_widen_max"])
    c = d.get("c") or cfg["c"]
    sd = math.sqrt(lam_final) * c * sd_mult
    pm = p_more(line, lam_final, sd)
    grade = grade_from_p(pm)
    model_grade = grade

    # Phase 7 H2H
    h = d.get("h2h") or {}
    h2h_note = "No H2H"
    if h.get("maps", 0) >= cfg["h2h_min_maps"] and h.get("hit_rate") is not None:
        r = h["hit_rate"]
        if r < 0.45:
            lam_final = lam_adj * 0.85
            sd = math.sqrt(lam_final) * c * sd_mult
            pm = p_more(line, lam_final, sd)
            grade = model_grade = grade_from_p(pm)
            h2h_note = f"H2H {r:.0%} - full underdog compression applied"
        elif r >= 0.70 and h.get("clears_in_losses"):
            grade = GRADES[min(GRADES.index(grade) + 2, 5)]
            h2h_note = f"H2H {r:.0%}, clears in losses - +2 grades"
        elif r >= 0.65 and h.get("clears_in_wins"):
            grade = GRADES[min(GRADES.index(grade) + 1, 5)]
            h2h_note = f"H2H {r:.0%}, clears in wins - +1 grade"
        else:
            h2h_note = f"H2H {r:.0%} - no bump"
    elif h.get("maps"):
        h2h_note = f"H2H only {h['maps']} maps - no trigger" + ("" if regional_ok else " (regional extension disabled)")

    # Phase 9 market layer
    implied = cfg["pp_implied"].get(line_type, 0.485)
    market = {"pp_implied_more": implied, "edge": round(pm - implied, 3),
              "clears_break_even": {k: pm >= v for k, v in cfg["break_even"].items()}}
    if pm < 0.68:
        market["note"] = "Below A- (68%) - near the vig line, no real edge"
    if line_type == "demon":
        market["note_line"] = "Demon line - usually a trap; only take if the model clears comfortably"
    if line_type == "goblin":
        market["note_line"] = "Goblin line - low-variance anchor only if EV is better"
    if prop == "hs":
        flags.append("Kills props beat HS props at equal EV")

    status = "NO PLAY" if grade == "C" else "PLAY"
    out.update(
        status=status, grade=grade if status == "PLAY" else "C",
        model_grade=model_grade, h2h=h2h_note,
        p_more=round(pm, 3), p_less=round(1 - pm, 3),
        grade_less=grade_from_p(1 - pm) if line_type == "standard" else "n/a (no Less on this line)",
        units=units_for(grade, small) if status == "PLAY" else 0.0,
        lambda_base=round(lam, 2), lambda_final=round(lam_final, 2), sd=round(sd, 2),
        detail={"rounds": rpm * 2, "stomp": stomp, "p_win": round(p_win, 3),
                "role": role_tag, "role_mult": role, "swing_mult": round(swing, 3),
                "bf": round(bf, 3), "stack": round(stack, 3), "c": c,
                "sd_widen": round(sd_mult, 3), "sd_reasons": sd_reasons,
                "maps_used": [p[0] for p in parts]},
        market=market,
    )
    return out


# ---------------------------------------------------------------- pairs
def grade_pair(leg1, leg2, payout=3.0, same_match=False):
    ok = all(g.get("grade") in ("B+", "A-", "A", "A+") for g in (leg1, leg2))
    p_both = leg1["p_more"] * leg2["p_more"]
    ev = p_both * payout - 1
    if not ok:
        units, note = 0.0, "Both legs must be B+ or better"
    elif ev >= 0.60 or (leg1["grade"] == "A+" and leg2["grade"] == "A+"):
        units, note = 2.0, "2U"
    elif ev >= 0.30:
        units, note = 1.5, "1.5U"
    elif ev >= 0.15:
        units, note = 1.0, "1U"
    else:
        units, note = 0.5, "Under +15% EV - 0.5U or skip"
    if ok and leg1["detail"]["p_win"] < 0.5 and leg2["detail"]["p_win"] < 0.5:
        units, note = min(units, 0.5), note + " (double underdog - 0.5U max)"
    if ok and any("Gate D" in f for g in (leg1, leg2) for f in g["flags"]):
        units = min(units, 1.0)
    return {"p_both": round(p_both, 3), "ev": round(ev, 3), "units": units, "note": note,
            "correlation": "Same match - justify it and check the locked multiplier" if same_match
            else "Different matches - independent"}


# ---------------------------------------------------------------- Phase 10
LOG_FIELDS = ["id", "logged_at", "player", "match", "prop", "line", "line_type", "lambda",
              "p_more", "grade", "units", "ev", "flags", "actual", "hit"]


def log_pick(graded, player, match="", path="picks_log.csv"):
    """Log BEFORE the match. Pre-game fields are never edited afterward."""
    p = Path(path)
    new = not p.exists()
    row = {"id": uuid.uuid4().hex[:8], "logged_at": datetime.now().isoformat(timespec="seconds"),
           "player": player, "match": match, "prop": graded["prop"], "line": graded["line"],
           "line_type": graded["line_type"], "lambda": graded.get("lambda_final"),
           "p_more": graded.get("p_more"), "grade": graded["grade"], "units": graded["units"],
           "ev": graded.get("market", {}).get("edge"), "flags": " | ".join(graded["flags"]),
           "actual": "", "hit": ""}
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return row["id"]


def record_result(pick_id, actual, path="picks_log.csv"):
    """Fill in actual + hit only."""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        if r["id"] == pick_id:
            r["actual"] = actual
            r["hit"] = "1" if float(actual) > float(r["line"]) else "0"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)


def calibration(path="picks_log.csv", min_legs=20):
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["hit"] in ("0", "1")]
    report = {}
    for g in GRADES:
        tier = [r for r in rows if r["grade"] == g]
        if not tier:
            continue
        hit = sum(int(r["hit"]) for r in tier) / len(tier)
        pred = sum(float(r["p_more"]) for r in tier) / len(tier)
        entry = {"legs": len(tier), "hit_rate": round(hit, 3), "predicted": round(pred, 3)}
        if len(tier) < min_legs:
            entry["advice"] = f"Need {min_legs}+ legs before recalibrating"
        elif hit < pred - 0.05:
            entry["advice"] = "Overconfident - raise c (widen SD)"
        elif hit > pred + 0.05:
            entry["advice"] = "Underconfident - lower c"
        else:
            entry["advice"] = "Calibrated"
        report[g] = entry
    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python grader.py leg.json")
    print(json.dumps(grade_leg(json.load(open(sys.argv[1], encoding="utf-8"))), indent=2))
