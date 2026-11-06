"""Published Underdog / PrizePicks pick'em rules for NBA / NFL entries: entry sizes, lineup restrictions, void / tie
reduction, payout schedules. Every rule carries its source, the page's own date and our read time; a rule we could not
read on an official page is UNVERIFIED and never refuses an entry or produces a payout figure.

Sources (read 2026-10-23 ~22:07 UTC by plain HTTPS GET of the public help pages, no login; WebFetch was refused with
403 by help.underdogsports.com, a browser user agent was served the page):
  U-PAY   https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts   "Updated over a month ago"
  U-FLEX  https://help.underdogsports.com/en/articles/13161362-pick-em-flex-flexed-payouts          "Updated over 2 weeks ago"
  U-TIE   https://help.underdogsports.com/en/articles/10982402-what-happens-in-the-event-of-a-tie-voided-selection  August 26, 2026
  U-TIE2  https://help.underdogsports.com/en/articles/8974260-tied-or-voided-picks                   "Updated over a week ago"
  U-DNP   https://help.underdogsports.com/en/articles/8923389-what-happens-if-my-player-doesn-t-play August 26, 2026
  U-SHIFT https://help.underdogsports.com/en/articles/8974208-shifted-payouts                       September 9, 2026
  U-RESC  https://help.underdogsports.com/en/articles/16910268-rescues                              "Updated this week"
  U-LINE  https://app.underdogsports.com/rules/pick-em ("Lineup restrictions"; a JavaScript page - its text was read in
          a browser by the esports rules session 2026-10-23 15:40-15:45 UTC, research/cs2_accuracy_2y/PLATFORM_RULES.md
          U4; our plain GET gets only the script shell). The rules there are sport-agnostic and U-TIE repeats them.
  P-PICKS https://www.prizepicks.com/help-center/player-picks          last updated October 19, 2026
  P-DNP   https://www.prizepicks.com/help-center/dnps-reboots-and-ties last updated October 19, 2026
  P-OUT   https://www.prizepicks.com/help-center/potential-outcomes    last updated October 19, 2026
  P-RB    https://www.prizepicks.com/reboots                           (NFL / NBA reboot policy)

PrizePicks payouts (owner 2026-11-06: "update the Sports Slip Studio so it uses the verified PrizePicks payout table"):
the help-center table https://www.prizepicks.com/help-center/payouts (page dated 2026-10-19) is VERIFIED by owner
sign-off 2026-11-05 in gradecore/pickem_payouts.json, the same file the main site reads; payouts.PP_TABLE reads it.
Power 2-6 and Flex 3-6 are offered (Flex 2-pick is on the help center but not in the app, so it is never offered here).
Standard lines only: a goblin / demon / promo pick pays differently and its lineup shows no payout figure.
DNPs and ties drop the lineup one tier each along P-DNP's chain (6 -> 5 -> 4 -> 3 Flex, 3-pick Flex -> 2-pick Power,
n-pick Power -> (n-1)-pick Power, 2-pick -> refund for a DNP; a tie never makes a lineup ineligible).

Underdog payouts: the base tables (payouts.UD_STANDARD / UD_FLEX, version ud-payouts-2026-10-23) assume every pick
at 1.0x; a pick with another payout multiplier changes the entry payout by an UNPUBLISHED combination rule
(payouts.MODIFIER_RULE) - so a member-facing figure is shown only when every leg's multiplier is exactly 1.0.

Not detectable from box scores (listed with every settlement that could be affected): PrizePicks Reboots and Underdog
Rescues (a Higher/More pick on a player who left in the first half and did not return).
"""
from __future__ import annotations

from .. import payouts

READ_AT = "2026-10-23T22:07:00Z"
RULES_VERSION = "slip-rules-2026-10-23"
PLATFORMS = ("underdog", "prizepicks")
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
SIDE_WORD = {"underdog": {"over": "Higher", "under": "Lower"}, "prizepicks": {"over": "More", "under": "Less"}}

SOURCES = {
    "U-PAY": ("https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts",
              "Updated over a month ago"),
    "U-FLEX": ("https://help.underdogsports.com/en/articles/13161362-pick-em-flex-flexed-payouts", "Updated over 2 weeks ago"),
    "U-TIE": ("https://help.underdogsports.com/en/articles/10982402-what-happens-in-the-event-of-a-tie-voided-selection",
              "August 26, 2026"),
    "U-TIE2": ("https://help.underdogsports.com/en/articles/8974260-tied-or-voided-picks", "Updated over a week ago"),
    "U-DNP": ("https://help.underdogsports.com/en/articles/8923389-what-happens-if-my-player-doesn-t-play",
              "August 26, 2026"),
    "U-SHIFT": ("https://help.underdogsports.com/en/articles/8974208-shifted-payouts", "September 9, 2026"),
    "U-RESC": ("https://help.underdogsports.com/en/articles/16910268-rescues", "Updated this week"),
    "U-LINE": ("https://app.underdogsports.com/rules/pick-em#lineup-restrictions",
               "no date shown; read 2026-10-23 15:4x UTC in a browser (esports PLATFORM_RULES.md U4)"),
    "P-PICKS": ("https://www.prizepicks.com/help-center/player-picks", "October 19, 2026"),
    "P-DNP": ("https://www.prizepicks.com/help-center/dnps-reboots-and-ties", "October 19, 2026"),
    "P-OUT": ("https://www.prizepicks.com/help-center/potential-outcomes", "October 19, 2026"),
    "P-RB": ("https://www.prizepicks.com/reboots", "no date shown"),
}

RULES = {
    "underdog": {
        "entry_size": {"verified": True, "src": "U-PAY", "standard": (2, 8), "flex": (3, 8),
                       "quote": "Flex entries support a minimum of 3 picks and a maximum of 8 picks."},
        "two_teams": {"verified": True, "src": "U-LINE",
                      "quote": "All entries must include active picks from at least two different teams."},
        "same_player_same_side": {"verified": True, "src": "U-LINE",
                                  "quote": "If you have LeBron 'Lower' than 35.5 points, you may not have LeBron "
                                           "'Lower' than 10.5 rebounds in the same entry."},
        "same_player_other_side": {"verified": False, "note": "only the same-side example is published"},
        "void_reduces": {"verified": True, "src": "U-TIE",
                         "quote": "the entry would win, but the payout would be calculated as if the entry were a "
                                  "pick 2 instead of 3."},
        "tie_is_void": {"verified": True, "src": "U-TIE2",
                        "quote": "In the event of a tied projection, that projection will be voided."},
        "refund_one_pick_or_team": {"verified": True, "src": "U-TIE",
                                    "quote": "If the entry is reduced to players on one team, that will also result "
                                             "in your entry being voided and the entry fee being refunded."},
        "flex_revert": {"verified": True, "src": "U-FLEX",
                        "quote": "If a 3-pick flex entry has a voided pick, the entry will revert to a 2-pick "
                                 "standard entry.",
                        "note": "U-FLEX lists '4-pick Flex Entry (6x) -> 3-pick Flex Entry (3x)' while U-PAY / U-FLEX "
                                "list the 3-pick flex at 3.25x: the all-correct payout of a 4-pick flex reduced by "
                                "one void is CONFLICTING on Underdog's own pages and is reported as unknown"},
        "dnp_basketball": {"verified": True, "src": "U-DNP",
                           "quote": "Whether officially active or inactive, if the player does not play, they will be "
                                    "treated as inactive, and all picks for that player will be considered Void."},
        "dnp_football": {"verified": True, "src": "U-DNP",
                         "quote": "If a player is active but does not play a snap or on special teams ... this will be "
                                  "graded as a Void."},
        "early_exit_counts": {"verified": True, "src": "U-DNP",
                              "quote": "All picks will be valid if a player plays in a game but exits early due to "
                                       "injury or otherwise."},
        "modifier_shift_kept": {"verified": True, "src": "U-SHIFT",
                                "quote": "In the event of any voided selection for both standard and flexed entries, "
                                         "the magnitude of the payout shift will hold true, no matter which selection "
                                         "voids."},
        "modifier_combination": {"verified": False, "note": payouts.MODIFIER_RULE},
        "rescue": {"verified": True, "src": "U-RESC", "detectable": False,
                   "quote": "If a player plays in the first half of a game but does not return in the second half for "
                            "any reason (such as injury, ejection, or rest), your entry may qualify for a refund "
                            "through our Rescue policy.",
                   "note": "Higher picks only; NBA rescues 'at Underdog's discretion'; not detectable from ESPN box "
                           "scores - flagged on affected settlements"},
        "correlated_payout": {"verified": True, "src": "U-PAY",
                              "quote": "Correlated projections can also modify your projected payout in your entry."},
    },
    "prizepicks": {
        "entry_size": {"verified": True, "src": "P-PICKS", "power": (2, 6), "flex": (3, 6),
                       "quote": "Pick between 2 and 6 projections.",
                       "note": "Flex 2-pick is listed on the payout page but not offered in the app (in-app view "
                               "2026-11-05, gradecore/pickem_payouts.json not_in_app), so Flex takes 3-6 here. "
                               "Colorado residents: minimum 3 (the member's state is unknown here - not checked)"},
        "two_teams": {"verified": True, "src": "P-PICKS",
                      "quote": "You must have picks from AT LEAST 2 different teams in order for it to be a valid "
                               "lineup."},
        "same_player": {"verified": True, "src": "P-PICKS",
                        "quote": "You can not have the same player in your lineup more than once, unless Stacks are "
                                 "available for that player."},
        "sport_specific": {"verified": False, "src": "P-PICKS",
                           "quote": "You can find those specific rules in the scoring chart for that particular sport.",
                           "note": "NBA / NFL scoring-chart combination rules are in-app only"},
        "dnp": {"verified": True, "src": "P-DNP",
                "quote": "If a player in your lineup does not participate in a single second/snap for any reason, "
                         "they will be DNPd."},
        "dnp_reverts": {"verified": True, "src": "P-DNP",
                        "quote": "if you select a 3-pick power play and one of your selections is marked DNP, your "
                                 "lineup will revert to a 2-pick power play."},
        "tie_reverts": {"verified": True, "src": "P-DNP",
                        "quote": "Tied Projections will not cause your lineup to become ineligible due to the same "
                                 "team or minimum pick requirement.",
                        "note": "a tie lowers the payout like a DNP but never makes the lineup ineligible; a lineup "
                                "left with ONE pick by ties has no published payout (reported unknown)"},
        "refund_same_team": {"verified": True, "src": "P-DNP",
                             "quote": "If DNPs or Reboots make it so that all remaining players in a lineup are on "
                                      "the same team, this will make the lineup ineligible."},
        "refund_two_pick": {"verified": True, "src": "P-DNP", "quote": "2-Pick Power → Refund, not eligible for payout"},
        "flex_reverts": {"verified": True, "src": "P-DNP", "quote": "3-Pick Flex → 2-Pick Power",
                         "note": "6 -> 5 -> 4 -> 3-pick Flex, then 3-pick Flex -> 2-pick Power (read 2026-11-06)"},
        "reboot": {"verified": True, "src": "P-RB", "detectable": False,
                   "quote": "Reboots are only applied to MORE selections",
                   "note": "NBA / NFL: player leaves in the 1st half and does not return in the 2nd half; not "
                           "detectable from ESPN box scores - flagged on affected settlements"},
        "payouts": {"verified": True, "src": "P-PAY",
                    "quote": "Mark the PrizePicks help-center payout table as verified so PrizePicks gets EV and "
                             "break-even.",
                    "note": "owner sign-off 2026-11-05 (quoted) on the help-center payout table "
                            "(gradecore/pickem_payouts.json); standard lines only - goblin / demon / promo lines pay "
                            "differently and show no figure"},
    },
}
SOURCES["P-PAY"] = ("https://www.prizepicks.com/help-center/payouts", "October 19, 2026")

# PrizePicks payouts, from the verified table (payouts.PP_TABLE = gradecore/pickem_payouts.json): picks -> multiple /
# picks -> {correct: multiple}. Flex 2 stays in the table (the help center lists it) but is never offered here.
PP_STANDARD_POWER = dict(payouts.PP_POWER)
PP_STANDARD_FLEX = {n: payouts.pp_tiers("flex", n) for n in sorted(payouts.PP_FLEX)}
# P-DNP's tier drops: (entry, picks) -> (entry, picks) one DNP / tie lower; None = refund (DNP) / no payout (tie)
PP_REVERT = {("flex", 6): ("flex", 5), ("flex", 5): ("flex", 4), ("flex", 4): ("flex", 3), ("flex", 3): ("power", 2),
             ("flex", 2): None, ("power", 6): ("power", 5), ("power", 5): ("power", 4), ("power", 4): ("power", 3),
             ("power", 3): ("power", 2), ("power", 2): None}
# Underdog flex "revert down" targets as printed on U-FLEX: from n picks -> (entry, picks, printed multiple)
UD_FLEX_REVERT = {8: ("flex", 7, 40.0), 7: ("flex", 6, 25.0), 6: ("flex", 5, 10.0), 5: ("flex", 4, 6.0),
                  4: ("flex", 3, 3.0), 3: ("standard", 2, 3.5)}

ENTRY_TYPES = {"underdog": ("standard", "flex"), "prizepicks": ("power", "flex")}
DEFAULT_ENTRY = {"underdog": "standard", "prizepicks": "power"}


def entry_type_ok(platform, entry_type):
    return entry_type in ENTRY_TYPES.get(platform, ())


def size_range(platform, entry_type):
    return RULES[platform]["entry_size"].get(entry_type)


def entry_check(legs, platform, entry_type=None):
    """Would the platform accept these legs as ONE entry (published rules only)?
    legs: [{"platform", "player_id", "player", "team", "side"}]. -> {"eligible": True|False|None, "refusals" (member
    text), "unverified" (admin notes), "rules_version"}. False only on a published rule; None = a rule could not be
    checked (never shown to members, never blocks)."""
    refusals, notes = [], []
    plats = {l.get("platform") for l in legs}
    if len(plats - {platform}) or len(plats) > 1:
        refusals.append("One platform per entry - these picks are on " +
                        " and ".join(sorted(LABEL.get(p, str(p)) for p in plats | {platform})) + ".")
    if platform not in PLATFORMS:
        return {"eligible": False, "refusals": refusals or [f"{platform} is not a pick'em platform."],
                "unverified": [], "rules_version": RULES_VERSION}
    et = entry_type or DEFAULT_ENTRY[platform]
    if not entry_type_ok(platform, et):
        refusals.append(f"{LABEL[platform]} has no {et} entry.")
    else:
        lo, hi = size_range(platform, et)
        if not lo <= len(legs) <= hi:
            refusals.append(f"{LABEL[platform]} {et} entries take {lo}-{hi} picks (this one has {len(legs)}).")
    teams = [l.get("team") for l in legs]
    if any(not t for t in teams):
        notes.append("a pick has no team: the two-team rule was not checked")
    elif legs and len(set(teams)) < 2:
        refusals.append(f"All picks are from one team ({teams[0]}) - {LABEL[platform]} needs picks from at least 2 "
                        "different teams.")
    by_player = {}
    for l in legs:
        k = l.get("player_id")
        if not k:
            notes.append("a pick has no canonical player: the same-player rule was not checked")
            continue
        by_player.setdefault(k, []).append(l)
    for ls in by_player.values():
        if len(ls) < 2:
            continue
        nm = ls[0].get("player") or ls[0].get("player_id")
        if platform == "prizepicks":
            refusals.append(f"{nm} is in this lineup more than once - PrizePicks allows a player once per lineup.")
        else:
            sides = [x.get("side") for x in ls]
            if len(set(sides)) < len(sides):
                refusals.append(f"{nm} is picked twice on the same side - Underdog does not allow that in one entry.")
            else:
                notes.append(f"{nm} on both sides: Underdog publishes no rule for that combination")
    if platform == "prizepicks":
        notes.append("PrizePicks NBA / NFL scoring-chart combination rules are in-app only")
    eligible = False if refusals else (None if notes else True)
    return {"eligible": eligible, "refusals": refusals, "unverified": notes, "rules_version": RULES_VERSION}


def pp_standard(legs):
    """Every pick is a standard PrizePicks line (not goblin / demon / promo): the only lineups the table covers."""
    return all(l.get("variant") in payouts.PP_STANDARD_VARIANTS and not l.get("promotional") for l in legs)


def _ud_base(entry_type, n):
    if entry_type == "standard":
        return payouts.UD_STANDARD.get(n)
    return (payouts.UD_FLEX.get(n) or {}).get(0)


def schedule(platform, entry_type, legs):
    """The payout schedule of this entry as the platform publishes it.
    -> {"member": [{"outcome", "multiple"|None, "text"}], "figures_shown": bool, "why_no_figures", "basis",
        "internal": {...}}. Member rows carry a multiple only when the platform publishes it for THIS entry."""
    n = len(legs)
    out = {"platform": platform, "entry_type": entry_type, "picks": n, "rules_version": RULES_VERSION, "member": [],
           "figures_shown": False, "why_no_figures": None, "basis": None, "internal": {}}
    if platform == "underdog":
        mults = [l.get("multiplier") for l in legs]
        all_one = all(m is not None and abs(float(m) - 1.0) < 1e-9 for m in mults)
        base = _ud_base(entry_type, n)
        out["internal"] = {"base": base, "modifiers": mults, "modifier_rule": payouts.MODIFIER_RULE,
                           "table_version": payouts.UD_TABLE_VERSION}
        if base is None:
            out["why_no_figures"] = f"Underdog has no {n}-pick {entry_type} payout"
            return out
        if not all_one:
            out["why_no_figures"] = ("Underdog adjusts this payout for the picks' multipliers; the app shows the "
                                     "exact figure")
            out["member"].append({"outcome": "Payout", "multiple": None, "text": out["why_no_figures"]})
            return out
        out["figures_shown"] = True
        out["basis"] = f"Underdog published payouts ({payouts.UD_TABLE_VERSION}), every pick at 1.0x"
        if entry_type == "standard":
            out["member"].append({"outcome": f"All {n} correct", "multiple": base})
            out["member"].append({"outcome": "Any pick wrong", "multiple": 0.0})
            if n > 2:
                out["member"].append({"outcome": "A pick ties or is void", "multiple": payouts.UD_STANDARD[n - 1],
                                      "text": f"pays as a {n - 1}-pick entry"})
        else:
            for k, m in sorted(payouts.UD_FLEX[n].items()):
                out["member"].append({"outcome": f"{n - k} of {n} correct", "multiple": m})
            out["member"].append({"outcome": "More wrong", "multiple": 0.0})
            tgt = UD_FLEX_REVERT.get(n)
            if tgt:
                out["member"].append({"outcome": "A pick ties or is void", "multiple": None,
                                      "text": f"reverts to a {tgt[1]}-pick {tgt[0]} entry"})
        out["member"].append({"outcome": "Left with 1 pick or one team", "multiple": None, "text": "refunded"})
        return out
    if platform == "prizepicks":
        tiers = payouts.pp_tiers(entry_type, n) if payouts.pp_in_app(entry_type, n) else None
        out["internal"] = {"tiers": tiers, "source": "P-PAY", "table_version": payouts.PP_VERSION}
        if tiers is None:
            out["why_no_figures"] = f"PrizePicks has no {n}-pick {entry_type} entry"
            return out
        if not pp_standard(legs):
            out["why_no_figures"] = "PrizePicks pays goblin, demon and promo picks differently; the app shows the figure"
            out["member"].append({"outcome": "Payout", "multiple": None, "text": out["why_no_figures"]})
            return out
        out["figures_shown"] = True
        out["basis"] = f"PrizePicks published payouts ({payouts.PP_VERSION}), standard lines"
        if entry_type == "power":
            out["member"].append({"outcome": f"All {n} correct", "multiple": tiers[n]})
            out["member"].append({"outcome": "Any pick wrong", "multiple": 0.0})
        else:
            for k, m in sorted(tiers.items(), reverse=True):
                out["member"].append({"outcome": f"{k} of {n} correct", "multiple": m})
            out["member"].append({"outcome": "More wrong", "multiple": 0.0})
        tgt = PP_REVERT.get((entry_type, n))
        out["member"].append({"outcome": "A pick is DNP or ties", "multiple": None,
                              "text": (f"pays as a {tgt[1]}-pick {tgt[0].title()}" if tgt else
                                       "a DNP refunds the lineup")})
        out["member"].append({"outcome": "DNPs leave 1 pick or one team", "multiple": None, "text": "refunded"})
        return out
    out["why_no_figures"] = "not a pick'em platform"
    return out
