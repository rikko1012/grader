"""
Each platform's LoL settlement rules, as published - with the source, the date read, and whether the rule is VERIFIED
(stated on the platform's own public rules page) or NOT (not stated; the handling below is then provisional and every
affected settlement is marked rule_unverified, kept out of accuracy scoring until confirmed). No CS2 assumption is
imported: CS2's "Maps 1-2 are always played" does not hold for a LoL best-of-3's Maps 1+2+3.

Read 2026-10-17 (UTC):
- Underdog: https://app.underdogsports.com/rules/sports/misc (section "Esports") and https://app.underdogsports.com/rules/pick-em
- PrizePicks: https://www.prizepicks.com/help-center/dnps-reboots-and-ties (last updated Aug 11, 2026),
  https://www.prizepicks.com/help-center/official-scoring-providers (May 16, 2026),
  https://www.prizepicks.com/help-center/player-picks (Sept 9, 2026). Sport-specific rules are in the in-app
  scoring chart for each league, which is not public - so every LoL-specific PrizePicks rule is unverified.
"""

UNDERDOG = {
    "source": ["https://app.underdogsports.com/rules/sports/misc#Esports", "https://app.underdogsports.com/rules/pick-em"],
    "read_at": "2026-10-17",
    "rules": {
        "scope": {"verified": True, "handling": "only the listed games count (Maps 1+2+3 = games 1, 2 and 3 of the series); "
                  "game 1 is the first game scheduled to be played"},
        "player_active": {"verified": True, "handling": "LoL: the player must play in ALL games stated in the projection to be "
                          "active; otherwise the pick is void"},
        "unplayed_game_in_scope": {"verified": False, "handling": "not stated for Maps 1+2+3 when a best-of-3 ends 2-0 (game 3 "
                                   "never played). Read literally with the activity rule the pick would void; not confirmed. "
                                   "Settled as rule_unverified with both readings recorded (sum of games played; void)."},
        "forfeit": {"verified": True, "handling": "a forfeit during the stated games voids every pick on that match"},
        "reschedule": {"verified": True, "handling": "new start within 36 h of the posted start: picks grade; later: void"},
        "partial_series": {"verified": True, "handling": "games already played for a game-range projection grade even if "
                           "later games are suspended or delayed"},
        "chronobreak": {"verified": True, "handling": "after a chronobreak only events after the revert point count"},
        "remake": {"verified": False, "handling": "not stated; provisional: a remade (restarted) game is not a played game "
                   "and the replay is that game number. Series with a remake are marked rule_unverified."},
        "push": {"verified": True, "handling": "stat equal to the line is a tie = void for that pick (the entry pays as one "
                 "pick fewer; an entry reduced to one active pick is refunded)"},
        "wrong_team": {"verified": True, "handling": "a player posted on the wrong team: void"},
        "stat_source": {"verified": True, "handling": "official league statistics from partners (Sportradar); corrections after "
                        "settlement normally do not change settled contests"},
        "fantasy_points": {"verified": True, "handling": "LoL fantasy points = 3 x kills + 2 x assists - 1 x deaths + 0.02 x creep score"},
        "combined": {"verified": True, "handling": "duos/combos: every player must play or the projection voids (no LoL combos "
                     "seen on Underdog so far)"},
    },
}

PRIZEPICKS = {
    "source": ["https://www.prizepicks.com/help-center/dnps-reboots-and-ties", "https://www.prizepicks.com/help-center/official-scoring-providers",
               "https://www.prizepicks.com/help-center/player-picks"],
    "read_at": "2026-10-17",
    "rules": {
        "scope": {"verified": False, "handling": "'MAPS 1-3' read as games 1, 2 and 3 of the series (the LoL scoring chart is "
                  "in-app only)"},
        "player_active": {"verified": True, "handling": "a player who does not participate at all is DNP: the pick is removed "
                          "and the lineup drops one tier (each league may set its own activity threshold - LoL's is not public)"},
        "unplayed_game_in_scope": {"verified": False, "handling": "not stated; settled as rule_unverified with both readings "
                                   "(sum of games played; DNP/void) recorded"},
        "forfeit": {"verified": False, "handling": "not stated; provisional void, marked rule_unverified"},
        "reschedule": {"verified": True, "handling": "depending on the sport a cancelled / postponed game may be DNP'd (LoL "
                       "specifics in-app); a projection left up after start is a board error and is DNP'd"},
        "remake": {"verified": False, "handling": "not stated; as Underdog's provisional handling, marked rule_unverified"},
        "push": {"verified": True, "handling": "an exact tie is a 'Tied Projection': neither correct nor incorrect, the payout "
                 "drops as for a DNP"},
        "stat_source": {"verified": True, "handling": "official partners (SportRadar, Genius, Stats Perform, Grid); stats as "
                        "posted when the event ends; later changes are not applied (obvious errors may be corrected)"},
        "combined": {"verified": False, "handling": "LoL Combo projections (2-3 players summed): handling when a player does not "
                     "play is not stated; combos are archived only, never graded"},
        "goblin_demon": {"verified": True, "handling": "goblins are easier lines at a lower multiplier, demons harder at a higher "
                         "one; no separate settlement rule"},
    },
}

RULES = {"underdog": UNDERDOG, "prizepicks": PRIZEPICKS}


def rule(platform, name):
    return (RULES.get(platform) or {}).get("rules", {}).get(name) or {"verified": False, "handling": "unknown platform/rule"}


def unverified(platform):
    return sorted(k for k, v in (RULES.get(platform) or {}).get("rules", {}).items() if not v["verified"])
