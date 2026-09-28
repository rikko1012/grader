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
    # --- calibration 2026-09-28 (owner-approved) - measured on 373 real Maps 1-2 series ---
    # c tuned OUT-OF-SAMPLE on 450 player-series x 13 lines (stats from before each match only):
    # kills 1.55 -> printed 65%/71%/76% hit 64%/71%/77%; headshots 1.30 -> gap 0.8%
    "c": 1.55,                   # SD constant for kills (in-sample 1.26 was still too tight)
    "c_hs": 1.30,                # headshots
    "p_cap_kills": 0.78,         # printed 81% kills hit only 69% (fat tails) - no A+ on kills
    "rounds_per_map": 22,        # real CS2 maps average ~21.8 (44 over Maps 1-2; was 23/46)
    "stomp_rounds_per_map": 20,  # stomp (40 over Maps 1-2)
    "stomp_ml": -400,            # either side at -400 or heavier (P(win) >= 0.80) = stomp
    "thin_maps": 20,             # under 20 Maps 1-2 = thin data: wider SD, 1U cap
    "map_blend_k": 12,           # map KPR weight = n / (n + 12): 8 maps -> 40%, 12 -> 50%
    "h2h_nudge": 0.02,           # 2-3 H2H maps: tiny lambda nudge only, never a letter bump
    "h2h_bump1_maps": 4,         # +1 grade needs 4+ Maps 1-2 of H2H
    "h2h_bump2_maps": 8,         # +2 grades needs 8+ and the floor clears in losses
    "form_fight_gap": 0.5,       # last-10 smoothed hit rate on the other side of 50% = FORM FIGHT
    "poor_adr": 65.0,
    "recency_trend": 0.15,       # last-5 KPR off window by 15%+ = "trending hard"
    "sd_widen": 1.10,            # per risk condition
    "sd_widen_max": 1.25,
    "poor_dpr": 0.72,            # poor DPR + poor KAST together = widen SD
    "poor_kast": 68.0,
    "underdog_hs_min": 65.0,     # underdog HS prop needs this HS% to survive
    "support_trading": 60,       # high Trading + low Firepower = support flag
    "support_firepower": 40,
    # role multipliers - 1.0: tested twice (in- and out-of-sample). +5%/-10% on top of KPR made
    # projections 1.9 kills too low and broke calibration. Rank is still reported.
    "role_primary": 1.0, "role_primary_gap": 1.0, "role_secondary": 1.0, "role_other": 1.0,
    "role_igl": 0.85,
    "h2h_min_maps": 2,          # H2H counts from 1 series (2 maps); was 4 - changed 2026-09-27 at owner's request
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


def units_for_p(p, cap=None):
    """Units follow the probability, never a letter after an H2H bump.
    B (57-62%) is display-only: break-even on a 2-pick is 57.7%."""
    u = 2.0 if p >= 0.80 else 1.5 if p >= 0.74 else 1.0 if p >= 0.68 else 0.5 if p >= 0.63 else 0.0
    return min(u, cap) if cap is not None else u


def units_for(grade, small_sample):
    """Legacy letter-based sizing (kept for grade_pair / old callers)."""
    u = {"A+": 2.0, "A": 1.5, "A-": 1.5, "B+": 1.0, "B": 0.5}.get(grade, 0.0)
    return min(u, 1.0) if small_sample else u


# ---------------------------------------------------------------- one leg
def grade_leg(d, cfg=CONFIG):
    """
    d keys (None/missing = unknown):
      prop "kills"|"hs", line, line_type "standard"|"goblin"|"demon"
      maps_played (Maps 1-2 in window), kpr, hs_pct, kpr_last5 (recent KPR), round_swing
      per_map [{map, kpr, hs_pct, maps_played}]  (prop maps, in order)
      rating, teammate_ratings [4 numbers], is_igl
      sniping, firepower, trading, opening, dpr, kast, adr
      team_ml, opp_ml (American odds for the player's team / opponent)
      on_listed_team, is_standin, roster_confirmed
      h2h {maps, hit_rate, clears_in_wins, clears_in_losses}
      recent {n, overs}  (last-10 matches: Maps 1-2 totals over THIS line)
    """
    prop = d.get("prop", "kills")
    line = float(d["line"])
    line_type = d.get("line_type", "standard")
    flags, sd_reasons = [], []
    out = {"prop": prop, "line": line, "line_type": line_type, "flags": flags}
    unit_cap, zero_units = None, []

    def stop(status, why):
        out.update(status=status, grade=status, units=0.0, units_less=0.0, reason=why)
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

    # Gate D - sample size (Maps 1-2)
    maps = d.get("maps_played")
    if maps is None or maps < 5:
        return stop("NO GRADE", f"Gate D: {maps} Maps 1-2 in the window (need 5+)")
    small = maps < cfg["thin_maps"]
    if maps < 10:
        flags.append(f"Gate D: only {maps} Maps 1-2 - variance flag, SD inflated, capped 1U")
        sd_reasons.append("sample <10 maps")
    elif small:
        flags.append(f"Thin data: {maps} Maps 1-2 on BO3.gg (<{cfg['thin_maps']}) - SD inflated, capped 1U")
        sd_reasons.append(f"thin data <{cfg['thin_maps']} maps")
    if small:
        unit_cap = 1.0

    # Gate A - AWPer on HS
    snip = d.get("sniping")
    if prop == "hs":
        if snip is None:
            flags.append("Gate A: Sniping unknown - fetch before playing an HS prop")
        elif snip > 40:
            return stop("NO PLAY", f"Gate A: Sniping {snip}/100 - AWPer on HS prop")

    # Win probability + stomp (either side at -400 or heavier = P(win) >= 0.80 or <= 0.20)
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
            flags.append(f"Last 10 trending {trend:+.0%} - 70/30 blend applied (KPR {kpr:.3f} -> {blended:.3f})")

    hs_pct = d.get("hs_pct")
    if prop == "hs" and hs_pct is None:
        return stop("NO GRADE", "Missing HS% - fetch trigger")

    # Base lambda over Maps 1-2. Map numbers are blended with the overall ones by sample size
    # (weight n/(n+12): 8 maps -> 40%) - one hot series on a map never becomes the projection.
    def blended(name, mstats):
        """His KPR/HS% on a map, pulled toward his overall numbers by sample size."""
        n = (mstats or {}).get("maps_played") or 0
        if not n or not (mstats or {}).get("kpr"):
            return kpr, hs_pct, 0
        w = n / (n + cfg["map_blend_k"])
        return (w * mstats["kpr"] + (1 - w) * kpr,
                w * (mstats.get("hs_pct") or hs_pct or 0) + (1 - w) * (hs_pct or 0), n)

    parts = []
    for m in (d.get("per_map") or [])[:2]:            # maps known (veto done)
        mk, mh, n = blended(m.get("map"), m)
        if not n:
            flags.append(f"{m.get('map')}: no Maps 1-2 on this map in the window - using all-maps numbers")
        parts.append((m.get("map"), mk, mh))
    if not parts and d.get("map_probs"):              # veto not done: expected value over predicted maps
        ms = d.get("map_stats") or {}
        for slot, probs in enumerate(d["map_probs"][:2], 1):
            tot = sum(probs.values()) or 1
            ek = eh = 0.0
            for name, pr in probs.items():
                mk, mh, _ = blended(name, ms.get(name))
                ek += pr / tot * mk
                eh += pr / tot * mk * (mh or 0)       # E[kills x HS%] for the HS projection
            likely = ", ".join(f"{n.replace('de_', '').title()} {p:.0%}"
                               for n, p in list(probs.items())[:3])
            parts.append((f"Map {slot} predicted ({likely})", ek, eh / ek if ek else hs_pct))
    while len(parts) < 2:
        parts.append(("unknown", kpr, hs_pct))
    lam = 0.0
    for _, k, h in parts:
        lam += k * ratio * rpm * ((h / 100) if prop == "hs" else 1)

    # 3a Role (rank by HLTV-style rating vs the 4 teammates)
    rating, mates = d.get("rating"), [t for t in (d.get("teammate_ratings") or []) if t is not None]
    rank = None
    if d.get("is_igl"):
        role, role_tag = cfg["role_igl"], f"IGL ({cfg['role_igl'] - 1:+.0%}, out of hierarchy)"
    elif rating is None or len(mates) < 4:
        role, role_tag = 1.0, "unconfirmed (need player + 4 teammate ratings)"
        flags.append("Role unconfirmed - 0% role adjustment")
    else:
        rank = 1 + sum(1 for t in mates if t > rating)
        if rank == 1:
            gap = rating - max(mates)
            role, role_tag = ((cfg["role_primary_gap"], "primary, gap >0.10") if gap > 0.10
                              else (cfg["role_primary"], "primary"))
        elif rank == 2:
            role, role_tag = cfg["role_secondary"], "secondary"
        else:
            role, role_tag = cfg["role_other"], f"#{rank} on team"
    tr, fp = d.get("trading"), d.get("firepower")
    if tr is not None and fp is not None and tr >= cfg["support_trading"] and fp <= cfg["support_firepower"]:
        flags.append("Support profile (high Trading, low Firepower) - treat as volume cap")
    if prop == "hs" and rank is not None and rank >= 3:
        zero_units.append("HS prop on a #3+ player")

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

    # Phase 7 H2H - adjusts lambda; a letter bump needs a real sample
    h = d.get("h2h") or {}
    h2h_maps, r = h.get("maps", 0), h.get("hit_rate")
    h2h_note, bump = "No H2H", 0
    if h2h_maps >= cfg["h2h_min_maps"] and r is not None:
        if r < 0.45:
            if h2h_maps >= cfg["h2h_bump1_maps"]:
                lam_final = lam_adj * 0.85
                h2h_note = f"H2H {r:.0%} over {h2h_maps} maps - full underdog compression applied"
            else:
                lam_final *= 1 - cfg["h2h_nudge"]
                h2h_note = f"H2H {r:.0%} over {h2h_maps} maps - small nudge down (-{cfg['h2h_nudge']:.0%})"
        elif r >= 0.65:
            if r >= 0.70 and h.get("clears_in_losses") and h2h_maps >= cfg["h2h_bump2_maps"]:
                bump, h2h_note = 2, f"H2H {r:.0%} over {h2h_maps} maps, clears in losses - +2 grades"
            elif h.get("clears_in_wins") and h2h_maps >= cfg["h2h_bump1_maps"]:
                bump, h2h_note = 1, f"H2H {r:.0%} over {h2h_maps} maps, clears in wins - +1 grade"
            else:
                lam_final *= 1 + cfg["h2h_nudge"]
                h2h_note = f"H2H {r:.0%} over {h2h_maps} maps - small nudge up (+{cfg['h2h_nudge']:.0%}), no letter bump"
        else:
            h2h_note = f"H2H {r:.0%} over {h2h_maps} maps - no adjustment"
    elif h2h_maps:
        h2h_note = f"H2H only {h2h_maps} maps - no trigger" + ("" if regional_ok else " (regional extension disabled)")

    # Confidence -> SD
    dpr, kast, adr = d.get("dpr"), d.get("kast"), d.get("adr")
    if dpr is not None and dpr >= cfg["poor_dpr"] and (
            (kast is not None and kast <= cfg["poor_kast"]) or (adr is not None and adr <= cfg["poor_adr"])):
        sd_reasons.append("poor DPR + KAST/ADR")
    if prop == "hs" and p_win < 0.5 and (hs_pct or 0) < cfg["underdog_hs_min"]:
        sd_reasons.append("underdog HS prop")
    sd_mult = min(cfg["sd_widen"] ** len(sd_reasons), cfg["sd_widen_max"])
    c = d.get("c") or (cfg["c_hs"] if prop == "hs" else cfg["c"])
    sd = math.sqrt(lam_final) * c * sd_mult
    pm = p_more(line, lam_final, sd)
    if prop == "kills":                                   # fat tails: no side above the cap
        cap = cfg["p_cap_kills"]
        pm = min(max(pm, 1 - cap), cap)
    pm_model = pm

    # Last-10 veto: the model can't out-vote what actually happened at this line
    rec = d.get("recent") or {}
    fight = None
    if rec.get("n"):
        sm = (rec["overs"] + 2) / (rec["n"] + 4)          # smoothed last-10 hit rate over the line
        if pm >= 0.57 and sm < cfg["form_fight_gap"]:
            fight, pm = "More", min(pm, sm)
        elif (1 - pm) >= 0.57 and (1 - sm) < cfg["form_fight_gap"]:
            fight, pm = "Less", max(pm, sm)
        if fight:
            flags.append(f"FORM FIGHT: model says {fight} but the last {rec['n']} matches went over "
                         f"{rec['overs']}/{rec['n']} - probability capped at the smoothed record, 0 units")

    grade = grade_from_p(pm)
    model_grade = grade
    if bump and not fight:
        grade = GRADES[min(GRADES.index(grade) + bump, 5)] if grade != "C" else grade

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

    # units follow P (not the letter); B is display-only
    u_more = 0.0 if (fight == "More" or zero_units) else units_for_p(pm, unit_cap)
    less_ok = line_type == "standard"
    u_less = 0.0 if (not less_ok or fight == "Less" or zero_units) else units_for_p(1 - pm, unit_cap)
    for z in zero_units:
        flags.append(f"0 units: {z}")
    status = "PLAY" if u_more > 0 else "NO PLAY"

    # value lines: where each side becomes playable (B+ = 63%) for THIS projection and spread
    def line_where(target_more):
        """Highest .5 line with P(More) >= target (More side) - solved on the same curve."""
        z = {0.63: 0.3319, 0.68: 0.4677}[target_more]
        return math.floor(lam_final - 0.5 - z * sd) + 0.5
    more_at, more_a = line_where(0.63), line_where(0.68)
    less_at = math.ceil(lam_final - 0.5 + 0.3319 * sd) - 0.5
    less_a = math.ceil(lam_final - 0.5 + 0.4677 * sd) - 0.5
    if prop == "kills" and cfg["p_cap_kills"] < 0.68:
        more_a = less_a = None
    value = {"fair_line": round(lam_final - 0.5, 1),
             "more_playable_at_or_below": more_at, "more_a_minus_at_or_below": more_a,
             "less_playable_at_or_above": less_at if less_ok else None,
             "less_a_minus_at_or_above": less_a if less_ok else None,
             "edge_kills": round(lam_final - line - 0.5, 1)}
    out.update(value=value)
    out.update(
        status=status, grade=grade, model_grade=model_grade, h2h=h2h_note,
        p_more=round(pm, 3), p_less=round(1 - pm, 3), p_more_model=round(pm_model, 3),
        grade_less=grade_from_p(1 - pm) if less_ok else "n/a (no Less on this line)",
        units=u_more, units_less=u_less, form_fight=fight,
        lambda_base=round(lam, 2), lambda_final=round(lam_final, 2), sd=round(sd, 2),
        detail={"rounds": rpm * 2, "stomp": stomp, "p_win": round(p_win, 3),
                "role": role_tag, "role_mult": role, "rank": rank, "swing_mult": round(swing, 3),
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
