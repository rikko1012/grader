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
    # x1.2 on 2026-10-07 (owner, after two outside reviews): on days the settings never saw a wider spread
    # was chosen every time (B printed ~60%, hit ~55%). FROZEN for a week - judged only on new matches,
    # with the old spread and "no rest bonus" logged alongside as comparisons (picklog shadows).
    "c": 1.86,                   # SD constant for kills (1.55 before 2026-10-07)
    "c_hs": 1.56,                # headshots (1.30 before 2026-10-07)
    "p_cap_kills": 0.78,         # printed 81% kills hit only 69% (fat tails) - no A+ on kills
    "rounds_per_map": 22,        # real CS2 maps average ~21.8 (44 over Maps 1-2; was 23/46)
    "stomp_rounds_per_map": 22,  # was 20 (40): the measured map-outcome factors below already
                                 # include short lopsided maps - a separate cut double-counted it
    # Measured on 450 real series (2026-09-28): actual / base projection by Maps 1-2 outcome.
    # Replaces the untested 1.05/0.85 win/loss split and the 0.12 blowout factor, which penalised
    # underdogs ~3x too much (33.5% dog: -5.4% vs measured -1.9%).
    "map_outcome": {"kills": (1.043, 0.969, 0.871), "hs": (1.022, 0.956, 0.893)},   # (won both, split, lost both)
    "outcome_clamp": (0.90, 1.08),
    "tape_gap": {"kills": 2.0, "hs": 1.0},     # projection this far from the last-10 average = TAPE note
    # --- v7.5 step 1 (2026-09-28, owner-specified) ---
    # Weights MEASURED on 900 walk-forward cases (694 with prior H2H): blending either into the
    # projection made it slightly LESS accurate (kills miss 6.07 -> 6.10 each; the 3-month window
    # already contains the last 10). Both are computed and shown on every card at weight 0.
    # Last 10 = 0.5 since 2026-10-01: on members' real PrizePicks lines (209 props, 3 days) the
    # last-10 average was the strongest signal - its side alone hit 126/205; held out day by day,
    # w 0.05 -> 0.5 took bets from 47% to 62% (Sep 29 5/12 -> 7/11, Sep 30 11/28 -> 11/24,
    # Oct 1 24/45 -> 34/49) and log-loss 0.705 -> 0.683. (The walk-forward had rejected it because
    # its lines were set at the recent average itself.) H2H: owner's decision 2026-09-30.
    # 0.5 -> 0.7 on 2026-10-02 with Oct 1-2 settled (235 props, 4 days, each day held out):
    # log-loss better on 3 of 4 days (0.6859 -> 0.6809), bets 57/96 -> 66/110. Rounds-by-closeness
    # and a wider spread were also tried and not chosen.
    # 0.3 was tried 2026-10-05 and reverted the same day: it cut playable board lines by more than
    # half (HS bets 30 -> 13). Owner: more accurate grades, never fewer playable ones.
    # 0.7 -> 0.3 on 2026-10-07 together with the star / rust / hot-form fixes: last 10 at 70%+ over
    # the line went over only 47% (we said Over on 79%). With the fixes the board has MORE playable
    # lines than before (483 -> 582 on Oct 2-6), so this no longer thins the board.
    "last10_weight": 0.3,
    # H2H: same-roster meetings only (opponent kept 4+ of today's 5), weight 0.25, 1-year half-life.
    # HLTV year test (6,122 later results with earlier meetings): roster-matched H2H cut the miss
    # 6.1362 -> 6.1317 (twice the gain of counting every meeting); weight 0.25 / half-life 365 fitted.
    "h2h_weight": 0.25,
    # Predicted maps (veto not out) are off since 2026-10-01: the top Map 1 pick was right 17/106
    # on members' props - chance level - so they only added noise. All-maps numbers instead.
    "use_predicted_maps": False,
    # Unders need a bigger edge (2026-10-02): on members' real lines, Under bets that cleared the
    # bar by less than 1 kill (0.6 HS) hit 25/45 (56%) - below PrizePicks' 57.7% break-even
    # (overtime and players popping off hit Unders hardest). Chosen on 3 of 4 held-out days;
    # Under bets 44/70 -> 19/25, Overs unchanged.
    # 2026-10-06: margin moved from Unders to Overs. On 4 days of real lines (Oct 2-5) Overs bets went
    # 73/174 (-9.0U) and Unders 93/170 (+8.75U) - books shade lines up (the public bets Overs). Overs
    # needing +1 kill / +0.6 HS and Unders none: bets 367 -> 423, hit 49% -> 54%, +8.0U -> +25.5U,
    # every day positive (Oct 2-5: +0.5 / +2.25 / +10.75 / +12.0).
    "under_margin": {"kills": 0.0, "hs": 0.0},
    # 0.5/0.3 -> 1.0/0.6 on 2026-10-07 together with the continuity fix: forward-only (fit on earlier days,
    # scored on the next) log-loss 0.6892 -> 0.6895 (same), Over bets 83 -> 110, Under bets 319 -> 228
    "over_margin": {"kills": 1.0, "hs": 0.6},   # 1.0 / 0.6 until 2026-10-07 (projection fixes made it less needed)
    "env_k": 20,
    "rest_days": (4, 14), "rest_bonus": 0.04,
    "online_shift": 0.06,
    "online_guard": "full",
    # 2026-10-08 performance model perf-1 (perf.py): drives the projection + spread for these scopes when its input
    # (d["perf"]) is present. Maps 1-2 only - on Map 1 it did not beat the old projection. () = the old pipeline.
    "perf_scopes": ("12",),   # 2026-10-08: see grade_leg - no +6% on fully online stats (comparisons: "none", "taper")     # LAN / online: maps in that environment for half the difference to count
    "star_rating": 1.15, "star_k": 1.0, "star_cap": 0.06, "rust_drop": 0.08, "hot_k": 0.6,
    "last10_min": 5,             # need 5+ recent two-map matches to blend
    # 4c (2026-09-28, owner-approved for KILLS props only): trade kills per round. Cross-validated
    # by team on 900 cases: kills miss 6.007 -> 5.976 (same sign in both halves); HS 3.695 -> 3.708
    # (no signal) -> weight 0 on headshots. Heavy traders score slightly fewer kills than KPR says.
    "trade_beta_kills": -0.129, "trade_mean": 0.1283,
    # owner 2026-10-06: same-region series count (players play the same vs teams from one scene).
    # Real lines Oct 2-5 (1,040 props, 604 with regional series): weight 0.1 log-loss 0.7036 -> 0.7031,
    # playable bets 423 -> 424, every day positive; 0.25+ brought losing days back.
    # 0.1 -> 0.4 on 2026-10-07: with the over/under fixes in, more weight kept helping (real lines Oct 2-6:
    # log-loss 0.6887 -> 0.6877, Oct 6 -5.5U -> -3.0U); held-out days chose 0.25-0.6.
    "regional_weight": 0.4,       # (2026-09-28 HLTV team-split test had chosen 0 - superseded)
    "h2h_half": 365,             # H2H series weight = exp(-days_old / 365): an e-folding time (halves every ~253 days), as tested
    "h2h_full_maps": 8,          # size weight = min(maps / 8, 1)
    "h2h_cap": {"kills": 3.0, "hs": 2.0},
    "stomp_ml": -400,            # either side at -400 or heavier (P(win) >= 0.80) = stomp
    "thin_maps": 20,             # under 20 Maps 1-2 = thin data: wider SD, 1U cap
    "map_blend_k": 12,
    "underdog_k": 1.15,
    # 0.3 (2026-10-05) -> 0 (2026-10-06): fitted on days the favourites won; with Oct 6 (1win swept
    # Spirit at 13%) it piled correlated Unders on underdog players. Real lines Oct 2-6: removing it
    # log-loss 0.7112 -> 0.7083, +9.5U -> +15.75U, Oct 6 -12.5U -> -6.8U, Over bets 71 -> 102 (all of Oct 6 settled).
    "dog_drop": 0.0,
    "gap_trust_at": {"kills": 2.5, "hs": 1.5},
    "gap_trust_keep": 0.5,
    "matchup_slope": 0.15,       # extra matchup shift (see grade_leg)
    "elite_k": 0.5,
    "rank_band_w": 0.5, "rank_band_k": 20,   # vs similar-rank teams (see grade_leg)              # extra vs a top-10 opponent (VRS rank from HLTV), same data          # heavy-underdog extra (see grade_leg), fitted on HLTV player-maps
    "shrink_k": {"kills": 10, "hs": 30},   # thin-sample pull toward the pro average (tested 2026-09-30)
    "pop_kpr": 0.672, "pop_hs": 51.5,
    # owner's map HS% adjustments - tested 2026-09-29 on 900 cases (fixed numbers, nothing fitted):
    # HS miss 3.695 -> 3.685, Brier 0.2120 -> 0.2103, grades still calibrated; kills unaffected
    "map_hs_mod": {"de_mirage": .06, "de_inferno": .04, "de_ancient": .03, "de_vertigo": .05,
                   "de_nuke": -.03, "de_dust2": -.05},           # map KPR weight = n / (n + 12): 8 maps -> 40%, 12 -> 50%
    "form_fight_margin": 0.14,   # veto only when the record clearly disagrees: smoothed hit rate
                                 # <=36% vs a More lean (3/10 or worse) or >=64% vs a Less lean
    "poor_adr": 65.0,
    # --- v7.4 (2026-09-28): market selection, not new multipliers ---
    "tape_more_max": 0.50,       # TAPE FIGHT: More lean but last-10 over-rate <= 50% -> 0 units
    "tape_less_min": 0.60,       #             Less lean but last-10 over-rate >= 60% -> 0 units
    "tape_fight_cap": 0.5,       # 2026-09-28: TAPE FIGHT picks hit 59-66% when tested -> max 0.25U, not 0U
    "soft_market_cap": 1.5,      # no usable Polymarket price (SOFT/NONE) -> max 1.5U (1U on the old 0.25-2U scale)
    "veto_mass_min": 0.45,       # predicted maps: top-2 mass under 45% in a slot = veto uncertainty
    "rust_note_days": 8, "rust_flag_days": 15, "rust_cap_days": 29,   # days since last official map
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
GRADES = ["C", "B-", "B", "B+", "A-", "A", "A+"]


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
    # B- (55-57%) added 2026-10-07: on 1,193 real lines Oct 2-6 that band hit 55.9% - right at what it
    # says - while 50-55% hit 49%. A real (small) pick, not a Lean.
    if p >= 0.55: return "B-"
    return "C"


def _cuts(line):
    """Continuity cuts for a count: a 30.5 line is Over at 31+ (cut 30.5) and Under at 30 or less (cut
    30.5); a whole line 26 is Over at 27+ (cut 26.5), Under at 25 or less (cut 25.5), 26 = push.
    (Until 2026-10-07 every line used line + 0.5 for both sides - half a kill against Overs on x.5
    lines and the push counted as an Under on whole lines; reviewed by outside AIs, fixed here.)"""
    if abs(line - round(line)) > 0.25:
        return line, line
    return line + 0.5, line - 0.5


def p_three(line, lam, sd):
    """Unconditional (P(Over), P(Under), P(push)) - the push only exists on whole lines."""
    up, down = _cuts(line)
    po, pu = 1 - phi((up - lam) / sd), phi((down - lam) / sd)
    return po, pu, max(0.0, 1.0 - po - pu)


def mix_p_more_live(line, cases):
    """LIVE (frozen) Maps 1-3 chance: average each case's push-removed P(More), weighted by the case chance.
    cases = [(weight, lam, sd), ...]"""
    return sum(w * p_more(line, lam, sd) for w, lam, sd in cases)


def mix_three(line, cases):
    """fix_v2: mix the raw (over, under, push) of each case first; condition on no push afterwards."""
    tot = [0.0, 0.0, 0.0]
    for w, lam, sd in cases:
        for i, v in enumerate(p_three(line, lam, sd)):
            tot[i] += w * v
    return tuple(tot)


def p_more(line, lam, sd):
    """P(More), push removed (pushes settle as no action): P(Over) / (P(Over) + P(Under))."""
    up, down = _cuts(line)
    pm, pl = 1 - phi((up - lam) / sd), phi((down - lam) / sd)
    return pm / (pm + pl) if pm + pl > 0 else 0.5


def units_for_p(p, cap=None):
    """Units follow the probability.
    C (<57%) is 0U: it loses in every walk-forward slice."""
    # B (57-63%) = 0.25U since 2026-09-28: after the measured win/loss fix, B hits 59.6% in the
    # walk-forward (kills and HS) vs PrizePicks' 57.7% 2-pick break-even - small edge, small stake.
    # owner 2026-10-07: stakes members actually bet - whole and half units, nothing under 0.5U
    u = (3.0 if p >= 0.80 else 2.0 if p >= 0.74 else 1.5 if p >= 0.68 else 1.0 if p >= 0.63
         else 0.5 if p >= 0.55 else 0.0)
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
    fix_v2 = bool(cfg.get("fix_v2"))   # comparison only (2026-10-08 review); the live CONFIG never sets it
    prop = d.get("prop", "kills")
    line = float(d["line"])
    line_type = d.get("line_type", "standard")
    flags, sd_reasons = [], []
    out = {"prop": prop, "line": line, "line_type": line_type, "flags": flags}
    unit_cap, zero_units = None, []

    def stop(status, why):
        out.update(status=status, grade=status, units=0.0, units_less=0.0, reason=why)
        return out

    # Gates never remove the side (owner: always Lean Over / Lean Under) - they set 0 units + a reason.
    # Gate C - live roster
    if d.get("on_listed_team") is False:
        zero_units.append("not on the listed team (transfer)")
    if d.get("is_standin"):
        zero_units.append("stand-in")
    regional_ok = d.get("roster_confirmed") is True
    if not regional_ok:
        flags.append("Gate C: roster not confirmed - regional H2H extension disabled")
        sd_reasons.append("unconfirmed roster/role")

    # Gate D - sample size (Maps 1-2)
    maps = d.get("maps_played")
    if not maps:
        return stop("NO GRADE", "Gate D: no Maps 1-2 in the window")
    if maps < 5:
        zero_units.append(f"only {maps} recent Maps 1-2")
        sd_reasons.append("sample <5 maps")
    small = maps < cfg["thin_maps"]
    if maps < 10:
        flags.append(f"Gate D: only {maps} Maps 1-2 - variance flag, SD inflated, capped 1.5U")
        sd_reasons.append("sample <10 maps")
    elif small:
        flags.append(f"Thin data: {maps} Maps 1-2 on BO3.gg (<{cfg['thin_maps']}) - SD inflated, capped 1.5U")
        sd_reasons.append(f"thin data <{cfg['thin_maps']} maps")
    if small:
        unit_cap = 1.5

    # Gate A - AWPer on HS
    snip = d.get("sniping")
    if prop == "hs":
        if snip is None:
            flags.append("Gate A: Sniping unknown - fetch before playing an HS prop")
        elif snip > 40:
            zero_units.append("AWPer on a headshot prop")

    # Win probability + stomp (either side at -400 or heavier = P(win) >= 0.80 or <= 0.20)
    p_win, note = win_prob(d.get("team_ml"), d.get("opp_ml"))
    if note:
        flags.append(note)
    mls = [float(x) for x in (d.get("team_ml"), d.get("opp_ml")) if x is not None]
    stomp = any(m <= cfg["stomp_ml"] for m in mls)
    rpm = cfg["stomp_rounds_per_map"] if stomp else cfg["rounds_per_map"]
    if stomp:   # no SD widening: measured 2026-09-28, spread when his team won both maps (HS x1.11,
                # kills x1.32) is no wider than on split maps (x1.19 / x1.28) - shown as a note only
        flags.append("Heavy favourite (-400 or shorter): note only - measured, spread is not wider")

    # Market quality (v7.4): HARD = liquid, tight Polymarket book; SOFT = thin/wide/none
    mq = d.get("market_quality") or ("HARD" if d.get("team_ml") is not None else "NONE")
    if mq != "HARD":
        unit_cap = min(unit_cap or cfg["soft_market_cap"], cfg["soft_market_cap"])
        flags.append(f"{'SOFT BOOK' if mq == 'SOFT' else 'NO MARKET'}: win chance "
                     f"{'from a thin/wide Polymarket book' if mq == 'SOFT' else 'set to 50/50'} - max "
                     f"{cfg['soft_market_cap']:g}U")

    # Stand-in (v7.4.4): playing for another team than his own - graded, with a risk flag + 0.5U cap
    if d.get("standin_for"):
        sd_reasons.append("stand-in")
        unit_cap = min(unit_cap or 1.0, 1.0)
        flags.append(f"STAND-IN for {d['standin_for']}: different team and role than his usual - "
                     f"SD inflated, max 1U")

    # Rust (v7.4): time since his last official map
    rust = d.get("days_since_last_map")
    if rust is not None:
        if rust >= cfg["rust_cap_days"]:
            sd_reasons.append("rust >28d")
            unit_cap = min(unit_cap or 1.0, 1.0)
            flags.append(f"RUST: last official map {rust} days ago - SD inflated, max 1U")
        elif rust >= cfg["rust_flag_days"]:
            sd_reasons.append("rust 15-28d")
            flags.append(f"RUST: last official map {rust} days ago - SD inflated")
        elif rust >= cfg["rust_note_days"]:
            flags.append(f"Note: last official map {rust} days ago")

    # KPR + recency blend
    kpr = d.get("kpr")
    if not kpr:
        return stop("NO GRADE", "Missing KPR - fetch trigger")
    ratio = 1.0   # (the old 15%-trigger KPR blend is replaced by the always-on last-10 blend below)

    hs_pct = d.get("hs_pct")
    if prop == "hs" and hs_pct is None:
        return stop("NO GRADE", "Missing HS% - fetch trigger")

    # Thin samples are pulled toward the pro average by n/(n+k), n = Maps 1-2 in his widest window.
    # Walk-forward 2026-09-30 (2,300 cases, top + lower tier, fitted on half the teams, scored on
    # the rest): kills miss 5.908 -> 5.902, HS 3.732 -> 3.711, log-loss better on both tiers.
    n_s = d.get("sample_maps")
    k_s = cfg["shrink_k"].get(prop, 0)
    if n_s and k_s:
        a = n_s / (n_s + k_s)
        kpr = a * kpr + (1 - a) * cfg["pop_kpr"]
        if hs_pct is not None:
            hs_pct = a * hs_pct + (1 - a) * cfg["pop_hs"]

    # Base lambda over Maps 1-2. Map numbers are blended with the overall ones by sample size
    # (weight n/(n+12): 8 maps -> 40%) - one hot series on a map never becomes the projection.
    def blended(name, mstats):
        """His KPR/HS% on a map, pulled toward his overall numbers by sample size, then the
        owner's map HS% adjustment (Mirage +6% ... Dust2 -5%)."""
        hmod = 1 + cfg["map_hs_mod"].get(name or "", 0.0)
        n = (mstats or {}).get("maps_played") or 0
        if not n or not (mstats or {}).get("kpr"):
            return kpr, (hs_pct or 0) * hmod, 0
        w = n / (n + cfg["map_blend_k"])
        return (w * mstats["kpr"] + (1 - w) * kpr,
                (w * (mstats.get("hs_pct") or hs_pct or 0) + (1 - w) * (hs_pct or 0)) * hmod, n)

    parts = []
    for m in (d.get("per_map") or [])[:2]:            # maps known (veto done)
        mk, mh, n = blended(m.get("map"), m)
        if not n:
            flags.append(f"{m.get('map')}: no Maps 1-2 on this map in the window - using all-maps numbers")
        parts.append((m.get("map"), mk, mh))
    if not parts and d.get("map_probs") and cfg.get("use_predicted_maps", True):   # veto not done
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
            top2 = sum(sorted((p / tot for p in probs.values()), reverse=True)[:2])
            if top2 < cfg["veto_mass_min"] and "veto uncertainty" not in sd_reasons:
                sd_reasons.append("veto uncertainty")
                flags.append(f"VETO UNCERTAINTY: Map {slot} top-2 maps hold only {top2:.0%} - SD inflated")
    while len(parts) < 2:
        parts.append(("unknown", kpr, hs_pct))
    lam = 0.0
    for _, k, h in parts:
        lam += k * ratio * rpm * ((h / 100) if prop == "hs" else 1)
    trade_mult = 1.0
    if prop == "kills" and d.get("trade_pr"):
        trade_mult = 1 + cfg["trade_beta_kills"] * (d["trade_pr"] / cfg["trade_mean"] - 1)
        lam *= trade_mult

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

    # 3b Swing, cap
    rs = d.get("round_swing") or 0.0
    swing = 1 + clamp(rs * 3, -6, 6) / 100
    bf = 1.0                       # blowout now lives inside the measured map-outcome factors
    raw_stack = role * swing
    stack = clamp(raw_stack, 0.75, 1.20)
    if stack != raw_stack:
        flags.append(f"Stack capped ({raw_stack:.3f} -> {stack:.3f})")

    # 3d Win/loss: expected factor over Maps 1-2 outcomes (won both / split / lost both), from the
    # series win chance -> per-map win chance q (BO3: P(series) = q^2 (3 - 2q))
    lo, hi = 0.0, 1.0
    for _ in range(40):
        q = (lo + hi) / 2
        lo, hi = (q, hi) if q * q * (3 - 2 * q) < p_win else (lo, q)
    mw = d.get("map_win_probs") or []           # Polymarket Map 1 / Map 2 winner prices when listed
    q1 = mw[0] if len(mw) > 0 and mw[0] is not None else q
    q2 = mw[1] if len(mw) > 1 and mw[1] is not None else q
    w_both, w_split, w_lost = cfg["map_outcome"]["hs" if prop == "hs" else "kills"]
    p20, p02 = q1 * q2, (1 - q1) * (1 - q2)
    outcome = clamp(p20 * w_both + (1 - p20 - p02) * w_split + p02 * w_lost, *cfg["outcome_clamp"])
    # Heavy underdogs lose more than the win/loss factor alone says: better teams also hold them to
    # fewer kills per round. Measured 2026-10-02 on 41,702 HLTV player-maps (kills vs the player's own
    # rate elsewhere): opponent 100+ ranks better -10% (factor gave -6%), 50-99 better -7% (-4%),
    # top-10 opponent -14% (-5%); favourites already matched. Extra = 1 - k * (0.40 - q)^2 below q 0.40.
    qm = (q1 + q2) / 2
    # bigger matchup shift: (1 + 0.15 x (map win chance - 0.5)). Chosen on all 5 held-out days of members'
    # real lines (827 props, log-loss 0.6969 -> 0.6947, bets 167/305 -> 172/315) and on HLTV history.
    dog = 1 + cfg["matchup_slope"] * (qm - 0.5)
    dog *= 1 - cfg["underdog_k"] * max(0.0, 0.40 - qm) ** 2
    # underdog teams' players finish below projection on real lines: 2026-10-05, 833 real Underdog/
    # PrizePicks lines Oct 2-4, each day held out - Overs on underdogs hit 46%, Unders 76%; this drop
    # (0.3 x (0.45 - match win chance)) chosen on held-out days, log-loss 0.7106 -> 0.7083, bets
    # 153/317 -> 157/319 (same number of playable grades)
    dog *= 1 - cfg["dog_drop"] * max(0.0, 0.45 - p_win)
    # vs a top-10 team the drop is bigger still (same HLTV data: team ranked 11-30 vs top 10 -7.6%
    # real vs -3.3% modelled at q~0.32; ranked 31-60 -14% vs -7% at q~0.24): 0.5 x (0.40 - q) more
    if (d.get("opp_rank") or 999) <= 10:
        dog *= 1 - cfg["elite_k"] * max(0.0, 0.40 - qm)
    if dog < 1:
        flags.append(f"Heavy underdog (map win chance {qm:.0%}) - projection lowered {1 - dog:.1%}")
    lam_adj = lam * stack
    lam_final = lam_adj * outcome * dog

    # vs teams ranked within 10 of tonight's opponent (HLTV, last 12 months): his kills per round there
    # vs his overall rate, weight 0.5 x maps / (maps + 20). Tested 2026-10-02 on 27,466 HLTV player-maps
    # (only earlier matches, fitted on half the players): held-out miss 4.214 -> 4.208 and 4.293 -> 4.291.
    rb = d.get("rank_band") or {}
    band_mult = 1.0
    if rb.get("maps") and rb.get("ratio"):
        a = cfg["rank_band_w"] * rb["maps"] / (rb["maps"] + cfg["rank_band_k"])
        band_mult = 1 + a * (clamp(rb["ratio"], 0.7, 1.3) - 1)
        lam_final *= band_mult

    lam_model = lam_final

    # Last 10 (v7.5): 70% model + 30% his last-10 Maps 1-2 average for this stat, always
    ra, rn = d.get("recent_avg"), (d.get("recent") or {}).get("n") or d.get("recent_n") or 0
    last10_mult = 1.0
    if ra is not None and rn >= cfg["last10_min"] and cfg["last10_weight"] > 0:
        w10 = cfg["last10_weight"]
        # his last-10 level = half average, half median (2026-10-02: chosen on all 4 held-out days of
        # members' real lines, log-loss 0.6782 -> 0.6747) - one blow-up game moves it less
        rmed = d.get("recent_median")
        level = (ra + rmed) / 2 if rmed is not None else ra
        # his last 10 came against his usual opponents: scale it to THIS matchup the same way the model
        # part is (win/loss factor x underdog correction, relative to an even match)
        even = 0.25 * w_both + 0.5 * w_split + 0.25 * w_lost
        level *= (outcome * dog) / even * band_mult       # ...and to how he does vs teams of this level
        blended = (1 - w10) * lam_final + w10 * level
        last10_mult = blended / lam_final if lam_final else 1.0
        lam_final = blended

    # H2H (v7.5): moves the projection, weighted by age and sample - never a letter bump
    series = [s for s in (d.get("h2h_series") or []) if s.get("total") is not None]
    h2h_note, h2h_edge, h2h_avg = "No H2H", 0.0, None
    if series:
        ws = [math.exp(-max(s["age_days"], 0) / cfg["h2h_half"]) for s in series]
        h2h_avg = sum(w * s["total"] for w, s in zip(ws, series)) / sum(ws)
        age_w = sum(ws) / len(ws)
        size_w = min(2 * len(series) / cfg["h2h_full_maps"], 1.0)
        cap = cfg["h2h_cap"]["hs" if prop == "hs" else "kills"]
        h2h_edge = clamp((h2h_avg - lam_final) * age_w * size_w, -cap, cap)
        lam_final += h2h_edge * cfg["h2h_weight"]
        h2h_note = (f"H2H avg {h2h_avg:.1f} over {len(series)} series ({2 * len(series)} maps) - "
                    f"age weight {age_w:.2f}, size weight {size_w:.2f} -> would add {h2h_edge:+.2f} "
                    f"(applied x{cfg['h2h_weight']:g})")

    # Regional (always, next to exact H2H): his series vs OTHER teams from the opponent's
    # country/region, same age x size formula, its own tested weight
    rseries = [s for s in (d.get("regional_series") or []) if s.get("total") is not None]
    reg_note, reg_edge, reg_avg = "No regional series", 0.0, None
    if rseries:
        ws = [math.exp(-max(s["age_days"], 0) / cfg["h2h_half"]) for s in rseries]
        reg_avg = sum(w * s["total"] for w, s in zip(ws, rseries)) / sum(ws)
        age_w = sum(ws) / len(ws)
        size_w = min(2 * len(rseries) / cfg["h2h_full_maps"], 1.0)
        cap = cfg["h2h_cap"]["hs" if prop == "hs" else "kills"]
        reg_edge = clamp((reg_avg - lam_final) * age_w * size_w, -cap, cap)
        lam_final += reg_edge * cfg["regional_weight"]
        reg_note = (f"Regional avg {reg_avg:.1f} over {len(rseries)} series vs same-region teams - age "
                    f"weight {age_w:.2f}, size weight {size_w:.2f} -> would add {reg_edge:+.2f} (applied "
                    f"x{cfg['regional_weight']:g}: tested on 847 cases, made projections less accurate)")

    # Confidence -> SD
    dpr, kast, adr = d.get("dpr"), d.get("kast"), d.get("adr")
    if dpr is not None and dpr >= cfg["poor_dpr"] and (
            (kast is not None and kast <= cfg["poor_kast"]) or (adr is not None and adr <= cfg["poor_adr"])):
        sd_reasons.append("poor DPR + KAST/ADR")
    if prop == "hs" and p_win < 0.5 and (hs_pct or 0) < cfg["underdog_hs_min"]:
        # no SD widening: measured 2026-09-28, HS spread when his team lost both maps is x1.14 vs
        # x1.19 on split maps - not wider. Kept as a note (the -outcome factor already lowers lambda).
        flags.append("Underdog HS prop: note only - measured, spread is not wider")
    sd_mult = min(cfg["sd_widen"] ** len(sd_reasons), cfg["sd_widen_max"])
    c = d.get("c") or (cfg["c_hs"] if prop == "hs" else cfg["c"])
    # Which maps the line covers (owner 2026-10-06): "12" Maps 1-2 (everything above is in Maps 1-2
    # units), "123" Maps 1-3 (map 3 is only played when Maps 1-2 split: expected total = Maps 1-2 +
    # P(1-1) x one more map, and the chance over the line mixes the 2-map and 3-map outcomes),
    # "1" Map 1 only (BO1 lines): one map = half the Maps 1-2 projection.
    # What drove real props over / under (2026-10-07, 1,193 real lines Oct 2-6, each fix held out by
    # day; together: log-loss 0.7031 -> 0.6938, playable bets 483 -> 582, +15.5U -> +32.2U, Oct 6
    # -4.5U -> -0.5U): stars (rating 1.15+) finished 2.6 under projection, rusty players (15+ days
    # off) 2.9 under (went over 31%), hot 3-month form 1.9 under - the line already prices a streak.
    # LAN or online (owner 2026-10-07: grade a LAN match with his LAN numbers, an online match with
    # his online numbers): his kills per round in tonight's environment vs overall (12 months, HLTV),
    # counted by sample - n maps / (n + env_k): 20 maps in that environment = half the difference.
    env = d.get("environment") or {}
    if env.get("stats_from"):
        where = "LAN" if env["stats_from"] == "lan" else "online"
        flags.append(f"{where} match: graded on his {where} stats ({env.get('stats_maps')} {where} maps, "
                     f"{'only' if (env.get('stats_weight') or 0) >= 1 else str(round(100 * env['stats_weight'])) + '% weight'})"
                     + (f"; last 10 = his last {where} matches" if env.get("form_from") else ""))
    elif env.get("tonight") and env.get("ratio") and env.get("ratio_maps"):
        n = env["ratio_maps"]
        mult = 1 + (env["ratio"] - 1) * n / (n + cfg["env_k"])
        lam_final *= mult
        where = "LAN" if env["tonight"] == "lan" else "online"
        flags.append(f"{where} match: his {where} kills per round are {env['ratio'] - 1:+.0%} vs his overall "
                     f"({n} {where} maps) - projection {mult - 1:+.1%}")
    # Online matches overall (2026-10-07, 953 real lines matched to HLTV LAN/online): online props went
    # over 55% and finished 1.75 above projection, LAN 41% / -1.0. +6% online was chosen on every
    # held-out day (log-loss 0.6944 -> 0.6915); a LAN shift was not.
    online_bonus = 0.0
    if env.get("tonight") == "online" and cfg["online_shift"]:
        # Environment-adjustment revision UNDER EVALUATION (2026-10-08, owner-approved, versioned): the +6% was
        # fitted on Oct 2-6 projections built from ALL-MAPS stats (added Oct 7 01:19Z); online-only stats came an
        # hour later (02:28Z) and the +6% stayed on top. Modes (cfg online_guard), all logged on identical inputs:
        #   "full"  (LIVE)  no +6% when the base rate is fully online (stats_weight 1); otherwise the full +6%
        #   "taper" (comparison, PROVISIONAL assumption) +6% x (1 - online weight) for partial blends
        #   "none"  (comparison) the old behaviour: +6% on every online match
        # The online-vs-overall ratio adjustment never overlaps: it is off whenever online stats are blended in.
        mode = cfg.get("online_guard", "full")
        w_on = min(max(env.get("stats_weight") or 0.0, 0.0), 1.0) if env.get("stats_from") == "online" else 0.0
        if mode == "none":
            shift = cfg["online_shift"]
        elif mode == "taper":
            shift = cfg["online_shift"] * (1 - w_on)
        else:
            shift = 0.0 if w_on >= 1 else cfg["online_shift"]
        w = w_on
        online_bonus = shift
        if shift > 0:
            lam_final *= 1 + shift
            flags.append(f"Online match: players beat their lines online on real lines - projection +{shift:.1%}"
                         + (f" (all-maps share {1 - w:.0%} of the base rate)" if w > 0 else ""))
    rt = d.get("rating")
    if rt and rt > cfg["star_rating"]:
        # capped at 6% (owner: don't underestimate stars - most of the 13% came from one day, Oct 5;
        # the other days stars missed by 1-3 kills, mostly because their LINES are set high)
        cut = min(cfg["star_cap"], cfg["star_k"] * (rt - cfg["star_rating"]))
        lam_final *= 1 - cut
        flags.append(f"Star (rating {rt:.2f}): projection {cut:.1%} lower - books set star lines high")
    if rust is not None and cfg["rest_days"][0] <= rust <= cfg["rest_days"][1]:
        # 4-14 days off: players finished +2.1 above projection (went over 59%); forward-only test +4%:
        # log-loss 0.6901 -> 0.6892 (2026-10-07, suggested by an outside review)
        lam_final *= 1 + cfg["rest_bonus"]
        flags.append(f"Rested ({rust} days since his last map): projection +{cfg['rest_bonus']:.0%}")
    if rust is not None and rust >= cfg["rust_flag_days"]:
        lam_final *= 1 - cfg["rust_drop"]
        flags.append(f"Rust ({rust} days since his last map): projection {cfg['rust_drop']:.0%} lower")
    tr = d.get("form_trend")
    if tr is not None and tr > 0:
        lam_final *= 1 - cfg["hot_k"] * tr
        flags.append(f"Hot form (+{tr:.0%} vs 12 months): only part of the streak counted")
    scope = d.get("scope") or "12"
    lam12, p3, lam3 = lam_final, None, lam_final / 2
    if scope == "1":
        lam_final = lam12 / 2
    elif scope == "123":
        p3 = q1 * (1 - q2) + (1 - q1) * q2
        lam_final = lam12 + p3 * lam3
    scale = lam_final / lam12 if lam12 else 1.0
    sd = math.sqrt(lam_final) * c * sd_mult
    pf = d.get("perf") if isinstance(d.get("perf"), dict) else None
    perf_on = (scope in cfg.get("perf_scopes", ()) and bool(pf) and pf.get("lambda") is not None and pf.get("sd")
               and pf.get("version") in cfg.get("perf_versions", ("perf-1",)))   # candidates (e.g. perf-1-bo3) stay comparison-only
    perf_missing = None
    if scope in cfg.get("perf_scopes", ()) and not perf_on:
        perf_missing = (pf or {}).get("error") or (f"{pf.get('version')} is a comparison-only candidate" if pf and pf.get("version") else "no performance-model input")
        flags.append(f"OLD PROJECTION: the performance model is unavailable here ({perf_missing}) - graded on the old pipeline")
    if perf_on:
        # performance model (perf.py): its projection and spread replace the old pipeline's; the old pipeline's
        # corrections for its own biases (gap compression, Over margin, confidence halving) do not apply to it
        flags.append(f"Projection {pf['lambda']:.1f} from the performance model ({pf.get('version')}: "
                     f"{pf.get('rate', 0):.3f} kills/round x opponent {pf.get('opp_factor', 1):.2f} x "
                     f"{pf.get('rounds_per_map', 0):.1f} rounds/map; data: {pf.get('data_quality')}) - old pipeline {lam_final:.1f}")
        lam_final = lam12 = float(pf["lambda"])
        sd = float(pf["sd"])
        scale = 1.0

    def three(shift=0.0):
        """Unconditional (over, under, push) for this scope; Maps 1-3 mixes the raw outcomes of the 2-map and
        3-map cases BEFORE the push is removed."""
        if scope == "123":
            return mix_three(line, cases123(shift))
        return p_three(line, lam_final + shift, sd)

    def cases123(shift=0.0):
        a, b = lam12 + shift, lam12 + lam3 + shift
        return [(1 - p3, a, math.sqrt(max(a, 1)) * c * sd_mult), (p3, b, math.sqrt(max(b, 1)) * c * sd_mult)]

    def pmore(shift=0.0):
        if fix_v2:                                   # corrected comparison (2026-10-08 review): condition after mixing
            o, u, _ = three(shift)
            return o / (o + u) if o + u > 0 else 0.5
        if scope == "123":
            return mix_p_more_live(line, cases123(shift))
        return p_more(line, lam_final + shift, sd)
    # Big disagreements with the line: past gap_trust_at kills/HS only gap_trust_keep of the extra counts
    # (2026-10-07: on 1,193 real lines Oct 2-6 the books were right more often than not when we
    # disagreed by 2.5+ kills - A- printed 70% but hit 26/62. Chosen on every held-out day; with half:
    # log-loss 0.7083 -> 0.7031, playable bets 489 -> 483, A- 20/29).
    gt = cfg["gap_trust_at"]["hs" if prop == "hs" else "kills"] * min(scale, 1.2)
    # live: the pre-2026-10-07 reference line + 0.5 (frozen); fix_v2 comparison: the line itself (the cut the
    # chance uses - symmetric for Overs and Unders on x.5 lines)
    gap = lam_final - (line if fix_v2 else line + 0.5)
    if abs(gap) > gt and not perf_on:
        shift = math.copysign(gt + (abs(gap) - gt) * cfg["gap_trust_keep"], gap) - gap
        flags.append(f"Projection {lam_final:.1f} is {abs(gap):.1f} from the line - past {gt:.1f} only half counts "
                     f"(books are usually closer on big disagreements): {lam_final + shift:.1f}")
        lam_final += shift
        lam12 += shift
    pm = pmore()
    raw = three()                                         # logged: the model's own outcome chances, before caps / margins
    probs_raw = {"over": round(raw[0], 4), "under": round(raw[1], 4), "push": round(raw[2], 4),
                 "p_more_no_push": round(raw[0] / (raw[0] + raw[1]), 4) if raw[0] + raw[1] > 0 else 0.5}
    if prop == "kills":                                   # fat tails: no side above the cap
        cap = cfg["p_cap_kills"]
        if pm > cap or pm < 1 - cap:
            flags.append(f"CAP: model chance {max(pm, 1 - pm):.1%} shown as {cap:.0%} (kills cap - fat tails)")
        pm = min(max(pm, 1 - cap), cap)
    m_o = cfg.get("over_margin", {}).get(prop, 0.0) * min(scale, 1.2)
    if pm > 0.5 and m_o and not perf_on:   # an Over is scored as if he projected m_o lower (never flips side)
        po = pmore(-m_o)
        if prop == "kills":
            po = min(max(po, 1 - cfg["p_cap_kills"]), cfg["p_cap_kills"])
        pm = max(po, 0.5)
    m_u = cfg.get("under_margin", {}).get(prop, 0.0) * min(scale, 1.2)
    if pm < 0.5 and m_u and not perf_on:   # an Under is scored as if he projected m_u higher (never flips side)
        pu = pmore(m_u)
        if prop == "kills":
            pu = min(max(pu, 1 - cfg["p_cap_kills"]), cfg["p_cap_kills"])
        pm = min(pu, 0.5)
    pm_model = pm

    # TAPE note (display only): projection far from what he has actually been doing
    ra = d.get("recent_avg") if scope == "12" else None   # his record is Maps 1-2 totals
    tape_gap = None
    if ra is not None:
        tape_gap = round(lam_final - ra, 1)
        if abs(tape_gap) >= cfg["tape_gap"]["hs" if prop == "hs" else "kills"]:
            flags.append(f"TAPE: projection {lam_final:.1f} vs last-10 average {ra} ({tape_gap:+.1f}) - display only")

    # Last-10 veto: the model can't out-vote what actually happened at this line
    rec = (d.get("recent") or {}) if scope == "12" else {}   # Maps 1-2 record vs a Maps 1-2 line only
    fight = None
    if rec.get("n"):
        sm = (rec["overs"] + 2) / (rec["n"] + 4)          # smoothed last-10 hit rate over the line
        m = cfg["form_fight_margin"]
        if pm >= 0.57 and sm <= 0.5 - m:
            fight = "More"
        elif (1 - pm) >= 0.57 and sm >= 0.5 + m:
            fight = "Less"
        if fight and not perf_on:
            # the record disagrees: keep the model's side but halve its confidence (never flip it)
            pm = 0.5 + (pm - 0.5) / 2
            flags.append(f"FORM FIGHT: model says {fight} but the last {rec['n']} matches went over "
                         f"{rec['overs']}/{rec['n']} - confidence halved, 0 units")
        elif fight:          # performance model: the probability stays the model's; the 0-unit gate is unchanged
            flags.append(f"FORM FIGHT: model says {fight} but the last {rec['n']} matches went over "
                         f"{rec['overs']}/{rec['n']} - 0 units")

    # TAPE FIGHT (v7.4): softer disagreement than FORM FIGHT - keeps the side and %, 0 units
    tape = None
    if rec.get("n") and not fight:
        rate = rec["overs"] / rec["n"]
        if pm >= 0.57 and rate <= cfg["tape_more_max"]:
            tape = "More"
        elif (1 - pm) >= 0.57 and rate >= cfg["tape_less_min"]:
            tape = "Less"
        if tape:
            flags.append(f"TAPE FIGHT: model {tape} {max(pm, 1 - pm):.0%} but last {rec['n']} went over "
                         f"{rec['overs']}/{rec['n']} - max {cfg['tape_fight_cap']:g}U")

    grade = grade_from_p(pm)
    model_grade = grade

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

    scout = bool(d.get("scout"))
    if scout:
        zero_units.append("SCOUT - no upcoming match found")
    # TAPE FIGHT tested 2026-09-28: its picks hit 59.4% kills / 65.7% HS - above the 57.7%
    # break-even, so a small stake (B size) instead of 0U.
    tcap = cfg["tape_fight_cap"]
    u_more = 0.0 if (fight == "More" or zero_units) else units_for_p(
        pm, min(unit_cap if unit_cap is not None else 9, tcap) if tape == "More" else unit_cap)
    less_ok = line_type == "standard"
    u_less = 0.0 if (not less_ok or fight == "Less" or zero_units) else units_for_p(
        1 - pm, min(unit_cap if unit_cap is not None else 9, tcap) if tape == "Less" else unit_cap)
    for z in zero_units:
        flags.append(f"0 units: {z}")
    out["zero_reason"] = "; ".join(z for z in zero_units if not z.startswith("SCOUT")) or None
    status = "SCOUT" if scout else "PLAY" if u_more > 0 else "NO PLAY"

    # value lines: where each side becomes playable (B+ = 63%) for THIS projection and spread
    # P(More at line L) = 1 - PHI((L + 0.5 - lam)/sd): More reaches p at L <= lam - 0.5 - z*sd,
    # Less reaches p at L >= lam - 0.5 + z*sd. Lines are x.5, so round to the right x.5.
    def more_line(z):   # highest x.5 line at or below the threshold (Over at L+0.5+: cut L)
        return math.floor(lam_final - m_o - z * sd - 0.5) + 0.5
    def less_line(z):   # lowest x.5 line at or above the threshold
        return math.ceil(lam_final + m_u + z * sd - 0.5) + 0.5
    more_at, more_a = more_line(0.3319), more_line(0.4677)
    less_at, less_a = less_line(0.3319), less_line(0.4677)
    if prop == "kills" and cfg["p_cap_kills"] < 0.68:
        more_a = less_a = None
    value = {"fair_line": round(lam_final - 0.5, 1),
             "more_playable_at_or_below": more_at, "more_a_minus_at_or_below": more_a,
             "less_playable_at_or_above": less_at if less_ok else None,
             "less_a_minus_at_or_above": less_a if less_ok else None,
             "edge_kills": round(lam_final - line - 0.5, 1)}
    out.update(value=value)
    out.update(
        status=status, grade=grade, model_grade=model_grade, h2h=h2h_note, regional=reg_note,
        p_more=round(pm, 3), p_less=round(1 - pm, 3), p_more_model=round(pm_model, 3), probs_raw=probs_raw,
        projection_source=(pf.get("version") if perf_on else "legacy" + (f" (performance model unavailable: {perf_missing})" if perf_missing else "")),
        p_more_model_raw=probs_raw["p_more_no_push"],      # the model's own chance (push removed), before the kills cap
        online_adj={"tonight": env.get("tonight"), "stats_from": env.get("stats_from"),
                    "online_weight": env.get("stats_weight") if env.get("stats_from") == "online" else None,
                    "mode": cfg.get("online_guard", "full"), "bonus_applied": round(online_bonus, 4)},
        grade_less=grade_from_p(1 - pm) if less_ok else "n/a (no Less on this line)",
        units=u_more, units_less=u_less, form_fight=fight, tape_fight=tape, scout=scout,
        market_quality=mq,
        lambda_base=round(lam, 2), lambda_final=round(lam_final, 2), sd=round(sd, 2),
        scope=scope, lambda_maps12=round(lam12, 2), p_map3=None if p3 is None else round(p3, 3),
        detail={"rounds": rpm * 2, "stomp": stomp, "p_win": round(p_win, 3), "p_map": round(q, 3),
                "outcome_mult": round(outcome, 3), "underdog_mult": round(dog, 3), "rank_band_mult": round(band_mult, 3), "p_map1": round(q1, 3), "p_map2": round(q2, 3),
                "tape_gap": tape_gap, "lambda_model": round(lam_model, 2), "trade_mult": round(trade_mult, 3), "last10_mult": round(last10_mult, 3),
                "h2h_edge": round(h2h_edge, 2), "regional_edge": round(reg_edge, 2),
                "regional_avg": None if reg_avg is None else round(reg_avg, 1), "h2h_avg": None if h2h_avg is None else round(h2h_avg, 1),
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
