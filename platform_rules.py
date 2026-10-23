"""CS2 platform rules (PrizePicks, Underdog) for settlement and entry eligibility - as PUBLISHED, with the source, the
read time and whether each rule is VERIFIED (stated on the platform's own public page) or not. Pure functions only (no
I/O, no locks): picklog's settlement, slips.py, slip_batches.py and the slip studio call them.

Never guessed: a situation no public rule covers settles PENDING (the pick stays open with the reason recorded) and an
entry check it affects reports eligible=None + "unverified" (admin / log output only - member pages simply do not block).

Read 2026-10-23 15:40-15:45 UTC (full wording and URLs: research/cs2_accuracy_2y/PLATFORM_RULES.md):
- Underdog  https://help.underdogsports.com/en/articles/10905524-pick-em-scoring-esports (dated February 25, 2026)
            https://help.underdogsports.com/en/articles/10982174-esports-delays-postponements-when-picks-grade-vs-void
            https://help.underdogsports.com/en/articles/10982402-what-happens-in-the-event-of-a-tie-voided-selection
            https://app.underdogsports.com/rules/pick-em  ("Lineup restrictions")
            https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts (2-8 standard, flex 3-8)
- PrizePicks https://www.prizepicks.com/help-center/player-picks (last updated October 19, 2026)
            https://www.prizepicks.com/help-center/dnps-reboots-and-ties (October 19, 2026)
            https://www.prizepicks.com/help-center/refunds-and-cancellations (October 19, 2026)
  PrizePicks' sport-specific rules are in the in-app scoring chart (not public; app.prizepicks.com could not be opened):
  every CS2-specific PrizePicks rule beyond the general DNP / lineup rules is unverified.
"""

READ_AT = "2026-10-23T15:45Z"
PLATFORMS = ("prizepicks", "underdog")
SCOPE_MAPS = {"1": (1,), "12": (1, 2), "123": (1, 2, 3)}

_UD_ESPORTS = "https://help.underdogsports.com/en/articles/10905524-pick-em-scoring-esports"
_UD_DELAYS = "https://help.underdogsports.com/en/articles/10982174-esports-delays-postponements-when-picks-grade-vs-void"
_UD_VOIDS = "https://help.underdogsports.com/en/articles/10982402-what-happens-in-the-event-of-a-tie-voided-selection"
_UD_LINEUP = "https://app.underdogsports.com/rules/pick-em#lineup-restrictions"
_UD_PAYOUTS = "https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts"
_PP_PICKS = "https://www.prizepicks.com/help-center/player-picks"
_PP_DNP = "https://www.prizepicks.com/help-center/dnps-reboots-and-ties"

RULES = {
    "underdog": {
        "forfeit_in_scope": {"verified": True, "action": "void", "source": _UD_ESPORTS,
                             "quote": "if there is a forfeit during the time specified in the player pick, all picks for "
                                      "that match will result in a void"},
        "forfeit_outside_scope": {"verified": True, "action": "grade", "source": _UD_DELAYS,
                                  "quote": "If an individual map or partial series is played for individual map projections "
                                           "or partial series projections, the picks will grade regardless of whether later "
                                           "maps in that series are suspended or delayed to any time.",
                                  "note": "the void rule is limited to a forfeit 'during the time specified in the player "
                                          "pick': a map-3 forfeit after Maps 1-2 were played does not void a Maps 1-2 pick"},
        "map_scope": {"verified": True, "source": _UD_ESPORTS,
                      "quote": "Map 1/Game 1 will always represent the first map scheduled to be played in any situation."},
        "player_missed_map_in_scope": {"verified": True, "action": "void", "source": _UD_ESPORTS,
                                       "quote": "A player must play in each map of their selection and a total of over 50% "
                                                "of their selection to be considered active."},
        "rounds_over_half": {"verified": True, "action": "not checked", "source": _UD_ESPORTS,
                             "note": "the >50% of rounds condition needs per-player rounds played, which HLTV's map rows do "
                                     "not carry - a mid-map substitution is not detected (limitation, not a rule gap)"},
        "remake": {"verified": False, "action": "not detectable",
                   "note": "no CS2 remake / restart rule is published (the chronobreak rule is for maps rewound in game); "
                           "HLTV keeps only the final map, so a remake is not seen in the data"},
        "reschedule": {"verified": True, "action": "grade within 36 h, else void", "source": _UD_DELAYS,
                       "quote": "If the newly scheduled start time is 36 hours after the original scheduled start time, all "
                                "picks will void."},
        "entry_min_teams": {"verified": True, "source": _UD_LINEUP,
                            "quote": "All entries must include active picks from at least two different teams."},
        "entry_size": {"verified": True, "source": _UD_PAYOUTS, "standard": (2, 8), "flex": (3, 8),
                       "quote": "Flex entries support a minimum of 3 picks and a maximum of 8 picks."},
        "entry_same_player_same_side": {"verified": True, "source": _UD_LINEUP,
                                        "quote": "If you have LeBron 'Lower' than 35.5 points, you may not have LeBron "
                                                 "'Lower' than 10.5 rebounds in the same entry."},
        "entry_same_player_other_side": {"verified": False, "note": "only the same-side example is published"},
        "entry_same_match": {"verified": True, "action": "allowed",
                             "note": "no same-match restriction is published; correlated picks shift the payout "
                                     "multiplier instead (help article 11010091)"},
        "void_refund": {"verified": True, "source": _UD_VOIDS,
                        "quote": "If the entry is reduced to players on one team, that will also result in your entry "
                                 "being voided and the entry fee being refunded."},
    },
    "prizepicks": {
        "forfeit_in_scope": {"verified": False, "action": "pending",
                             "note": "PrizePicks publishes no forfeit rule; CS2 specifics are in the in-app scoring chart"},
        "forfeit_outside_scope": {"verified": False, "action": "pending", "note": "not published (in-app scoring chart)"},
        "whole_match_forfeit": {"verified": True, "action": "void", "source": _PP_DNP,
                                "quote": "If a player in your lineup does not participate in a single second/snap for any "
                                         "reason, they will be DNPd.",
                                "note": "applied only when NO map of the match was played (the player cannot have "
                                        "participated); DNP = the pick is removed (void)"},
        "player_dnp_match": {"verified": True, "action": "void", "source": _PP_DNP,
                             "quote": "If a player in your lineup does not participate in a single second/snap for any "
                                      "reason, they will be DNPd."},
        "player_missed_map_in_scope": {"verified": False, "action": "pending", "source": _PP_DNP,
                                       "quote": "Different PrizePicks markets have specific rules regarding what qualifies "
                                                "as a DNP.",
                                       "note": "the CS2 threshold (play a map of the scope? every map?) is in-app only"},
        "remake": {"verified": False, "action": "not detectable", "note": "not published"},
        "reschedule": {"verified": False, "action": "not applied", "source": _PP_DNP,
                       "quote": "Depending on the sport, picks may be DNPd when a game is canceled or postponed to a new day.",
                       "note": "the CS2 rule is in the in-app scoring chart"},
        "entry_min_teams": {"verified": True, "source": _PP_PICKS,
                            "quote": "You must have picks from AT LEAST 2 different teams in order for it to be a valid lineup."},
        "entry_size": {"verified": True, "source": _PP_PICKS, "power": (2, 6), "flex": (2, 6),
                       "quote": "Pick between 2 and 6 projections.",
                       "note": "Colorado residents: minimum 3 (the member's state is not known here - not checked)"},
        "entry_same_player": {"verified": True, "source": _PP_PICKS,
                              "quote": "You can not have the same player in your lineup more than once, unless Stacks are "
                                       "available for that player.",
                              "note": "Stacks are not offered for CS2 on the boards we read; treated as not available"},
        "entry_sport_specific": {"verified": False, "source": _PP_PICKS,
                                 "quote": "Some sports also have specific rules.",
                                 "note": "CS2-specific combination rules (e.g. opposing players of one match) are in the "
                                         "in-app scoring chart - not public"},
        "void_refund": {"verified": True, "source": _PP_DNP,
                        "quote": "If DNPs or Reboots make it so that all remaining players in a lineup are on the same "
                                 "team, this will make the lineup ineligible."},
    },
}


def platform_of(book):
    b = str(book or "").strip().lower()
    return b if b in PLATFORMS else None


def rule(platform, name):
    return (RULES.get(platform_of(platform)) or {}).get(name) or {"verified": False, "note": "unknown platform/rule"}


def unverified(platform):
    return sorted(k for k, v in (RULES.get(platform_of(platform)) or {}).items() if not v["verified"])


# ---------------------------------------------------------------------------------------------------------------- settlement

def forfeited_numbers(n_forfeit, played, series_total=None):
    """Which map numbers HLTV's forfeited maps were. HLTV keeps the forfeited map's slot (a match with maps 2 and 3 played
    and one forfeit forfeited map 1) and lists no row for it. played: the numbers of the maps with rows. A forfeit
    before a played map leaves a gap; the rest come after the last played map. series_total (score sum, when known)
    must equal played + forfeits. Returns a set, or None when the data do not determine it (never guessed)."""
    try:
        n = int(n_forfeit or 0)
    except (TypeError, ValueError):
        return None
    played = sorted({int(x) for x in played or ()})
    if n <= 0:
        return set()
    if series_total is not None and series_total != len(played) + n:
        return None
    top = played[-1] if played else 0
    gaps = [k for k in range(1, top + 1) if k not in played]
    if len(gaps) > n:
        return None                                        # more holes than forfeits: map rows missing, not forfeits
    return set(gaps) | set(range(top + 1, top + 1 + n - len(gaps)))


def forfeit_decision(platform, scope, forfeited, any_played=True):
    """A forfeit in the pick's match. forfeited: set of forfeited map numbers (None = unknown). Returns
    {"action": "grade"|"void"|"pending", "rule", "verified", "reason"}. platform None = no platform line (member research):
    the long-standing rule (any forfeited map voids, 2026-10-08) is kept, labelled 'legacy'."""
    p = platform_of(platform)
    scope_maps = set(SCOPE_MAPS.get(str(scope or "12"), (1, 2)))
    if forfeited is not None and not forfeited:
        return {"action": "grade", "rule": None, "verified": True, "reason": "no forfeit"}
    if p is None:
        return {"action": "void", "rule": "legacy_any_forfeit", "verified": False,
                "reason": "forfeited map(s) - no platform on the line: the standing rule voids any forfeit"}
    if p == "prizepicks" and not any_played:
        return {"action": "void", "rule": "whole_match_forfeit", "verified": True,
                "reason": "match forfeited, no map played: the player did not participate (PrizePicks DNP)"}
    if forfeited is None:
        return {"action": "pending", "rule": "forfeit_in_scope", "verified": False,
                "reason": "forfeited map numbers not determined from HLTV's map rows - pending"}
    inside = sorted(forfeited & scope_maps)
    name = "forfeit_in_scope" if inside else "forfeit_outside_scope"
    r = RULES[p][name]
    if not r["verified"]:
        return {"action": "pending", "rule": name, "verified": False,
                "reason": f"{p}: no published CS2 rule for a forfeit {'inside' if inside else 'outside'} the line's maps "
                          f"(forfeited map {sorted(forfeited)}) - pending"}
    return {"action": r["action"], "rule": name, "verified": True,
            "reason": f"{p}: forfeited map {sorted(forfeited)} {'inside' if inside else 'outside'} the line's maps"}


def dnp_decision(platform, scope, player_maps, scraped_maps):
    """The player has no row on a map of the line's scope that was played and scraped (others' stats are there).
    player_maps: numbers he has rows for in this match; scraped_maps: numbers with any player's stats. Returns None
    (no DNP: he played every in-scope map, or a map is just not scraped yet) or a decision like forfeit_decision."""
    p = platform_of(platform)
    scope_maps = set(SCOPE_MAPS.get(str(scope or "12"), (1, 2)))
    have, scraped = set(player_maps or ()), set(scraped_maps or ())
    missed = sorted((scope_maps & scraped) - have)
    if not missed:
        return None
    if p == "underdog":
        return {"action": "void", "rule": "player_missed_map_in_scope", "verified": True,
                "reason": f"underdog: did not play map {missed} of the line (must play each map of the selection)"}
    if p == "prizepicks":
        if not have and scraped:
            return {"action": "void", "rule": "player_dnp_match", "verified": True,
                    "reason": "prizepicks: did not play any map of the match (DNP)"}
        return {"action": "pending", "rule": "player_missed_map_in_scope", "verified": False,
                "reason": f"prizepicks: did not play map {missed} of the line - CS2 activity rule not published - pending"}
    return None                                            # no platform: unchanged handling (missing map -> not settled)


# ---------------------------------------------------------------------------------------------------------------- entries

def _norm(s):
    return " ".join(str(s or "").lower().split())


def _leg_team(l):
    return _norm(l.get("team"))


def _leg_player(l):
    return _norm(l.get("nick") or l.get("player"))


def entry_check(legs, platform=None, entry_type=None):
    """Whether the platform accepts these legs as ONE entry. legs: [{team, nick|player, side, prop, book?}].
    platform: 'prizepicks' | 'underdog' (default: the legs' common book). entry_type: Underdog 'standard'|'flex',
    PrizePicks 'power'|'flex' (default: the platform's least restrictive).
    Returns {"platform", "eligible": True|False|None, "verified", "refusals": [member-facing messages],
    "unverified": [admin notes], "read_at"}. eligible False only on a published rule; None = could not be checked
    (member pages: do not block; record the note in admin/log output)."""
    legs = list(legs or [])
    refusals, notes = [], []
    books = {platform_of(l.get("book")) for l in legs if l.get("book")}
    p = platform_of(platform) or (next(iter(books)) if len(books) == 1 else None)
    if platform is None and len(books) > 1:
        refusals.append("These picks come from different platforms - an entry is placed on one platform.")
    if p is None:
        if not refusals:
            notes.append("platform unknown: entry rules not checked")
        return {"platform": None, "entry_type": entry_type, "eligible": False if refusals else None,
                "verified": bool(refusals), "status": "refused" if refusals else "unverified",
                "refusals": refusals, "unverified": notes, "read_at": READ_AT}
    R = RULES[p]
    label = "PrizePicks" if p == "prizepicks" else "Underdog"
    et = entry_type or ("standard" if p == "underdog" else "power")
    size = R["entry_size"].get(et)
    if size is None:
        notes.append(f"entry type {et!r} unknown for {label}: size not checked")
    elif not size[0] <= len(legs) <= size[1]:
        refusals.append(f"{label} {et} entries take {size[0]}-{size[1]} picks (this one has {len(legs)}).")
    if p == "prizepicks":
        notes.append("PrizePicks CS2 scoring-chart combination rules are not public (in-app only)")
    teams = [_leg_team(l) for l in legs]
    if any(not t for t in teams):
        notes.append("a pick has no team: the two-team rule was not checked")
    elif legs and len(set(teams)) < 2:
        refusals.append(f"All picks are from one team ({legs[0].get('team')}) - {label} requires picks from at least "
                        "2 different teams.")
    seen = {}
    for l in legs:
        k = _leg_player(l)
        if not k:
            notes.append("a pick has no player name: the same-player rule was not checked")
            continue
        seen.setdefault(k, []).append(l)
    for k, ls in seen.items():
        if len(ls) < 2:
            continue
        nick = ls[0].get("nick") or ls[0].get("player")
        if p == "prizepicks":
            refusals.append(f"{nick} is in this entry more than once - PrizePicks allows a player once per lineup.")
        else:
            sides = [str(x.get("side") or "") for x in ls]
            if len(set(sides)) < len(sides):
                refusals.append(f"{nick} is picked twice on the same side - Underdog does not allow that in one entry.")
            else:
                notes.append(f"{nick} on both sides: Underdog publishes no rule for that combination")
    # refused only on a published rule; any rule we could not check (or that is not public) leaves it unverified
    eligible = False if refusals else (None if notes else True)
    return {"platform": p, "entry_type": et, "eligible": eligible, "verified": eligible is not None,
            "status": "refused" if eligible is False else "eligible" if eligible else "unverified",
            "refusals": refusals, "unverified": notes, "read_at": READ_AT}


def live_legs_valid(live_legs):
    """After void / pushed legs drop out: an entry still grades only with 2+ live picks from 2+ teams (both platforms,
    verified). Returns True / False, or None when a live leg has no team (cannot tell - existing handling applies)."""
    if len(live_legs) < 2:
        return False
    teams = [_leg_team(l) for l in live_legs]
    if any(not t for t in teams):
        return None
    return len(set(teams)) >= 2
