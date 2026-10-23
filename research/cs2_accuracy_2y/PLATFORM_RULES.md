# CS2 platform rules: PrizePicks and Underdog (evidence, 2026-10-23)

Code: branch `close/rules`, commit 235e501: `platform_rules.py` (rule table + pure functions), settlement in
`picklog.py`, slips in `slip_batches.py` / `slips.py`, tests in `tests/test_platform_rules.py`.
"Verified" means the rule is stated on the platform's own public page. Anything else is unverified. An unverified
settlement stays **pending**: the pick stays open, the clock never voids it, and `result.rule_pending` records the
reason. An unverified entry check gives `eligible: null` / `status: "unverified"`, which shows only in admin and log
output. Member pages do not block on it.

## Sources (read 2026-10-23 between 15:40 and 15:45 UTC; read only, no login)

| # | Platform | URL | Page date |
|---|---|---|---|
| U1 | Underdog | https://help.underdogsports.com/en/articles/10905524-pick-em-scoring-esports | February 25, 2026 |
| U2 | Underdog | https://help.underdogsports.com/en/articles/10982174-esports-delays-postponements-when-picks-grade-vs-void | September 9, 2026 |
| U3 | Underdog | https://help.underdogsports.com/en/articles/10982402-what-happens-in-the-event-of-a-tie-voided-selection | August 26, 2026 |
| U4 | Underdog | https://app.underdogsports.com/rules/pick-em, section "Lineup restrictions" (JS page, read in the browser pane) | no date shown |
| U5 | Underdog | https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts | "Updated over a month ago" |
| U6 | Underdog | https://help.underdogsports.com/en/articles/11010091-correlated-picks-how-stacking-players-affects-your-payout-multiplier | September 9, 2026 |
| P1 | PrizePicks | https://www.prizepicks.com/help-center/player-picks | last updated October 19, 2026 |
| P2 | PrizePicks | https://www.prizepicks.com/help-center/dnps-reboots-and-ties | October 19, 2026 |
| P3 | PrizePicks | https://www.prizepicks.com/help-center/refunds-and-cancellations | October 19, 2026 |
| P4 | PrizePicks | https://www.prizepicks.com/help-center/official-scoring-providers | October 19, 2026 |

PrizePicks keeps its sport-specific rules in the in-app scoring chart. P2 says: "Please consult the in-app scoring
chart for the league in question". That chart is not public. The browser pane refuses to open app.prizepicks.com
(safety restriction), and its CS2 chart text is not on the web. So every CS2-specific PrizePicks rule is unverified.

## Exact wording used

- U1: "Esports are scored for a specified duration of the match (for example, "map 1"). Map 1/Game 1 will always
  represent the first map scheduled to be played in any situation. For all esports, if there is a forfeit during the
  time specified in the player pick, all picks for that match will result in a void."
- U1 (CS2 & Valorant): "A player must play in each map of their selection and a total of over 50% of their selection to
  be considered active. If a player plays in each map, and the total amount of rounds played is over 50% of their
  selection, they will be graded as normal, even if they are replaced in the middle of the match or substituted out."
- U1: "If 'Chronobreak' is used to return a map to an earlier state, all markets will be settled based on events after
  the point at which the map clock was reverted back to."
- U1 / U2: "If a game does not start at the officially scheduled start time of posting - or starts and is then
  suspended or delayed due to any deferral, but the newly scheduled start time is within 36 hours of the original
  scheduled start time - the picks will grade." / "If the newly scheduled start time is 36 hours after the original
  scheduled start time, all picks will void." / "If an individual map or partial series is played for individual map
  projections or partial series projections, the picks will grade regardless of whether later maps in that series are
  suspended or delayed to any time."
- U1 (overtime): "For CS and Valorant, overtime stats in the maps for the player's projections DO count toward a
  player's projection."
- U3: "If a voided player makes an entry ineligible, the entry fee would be refunded. An example of an ineligible entry
  is if a two-pick entry has a voided player that reduces to one pick, or if the remaining players after a voided
  selection are on the same team."
- U4: "All entries must include active picks from at least two different teams. All entries also require 2+ active
  picks to grade as Win or Loss." / "Ex: If you have LeBron 'Lower' than 35.5 points, you may not have LeBron 'Lower'
  than 10.5 rebounds in the same entry."
- U5: standard entries are 2 to 8 picks (payout table); "Flex entries support a minimum of 3 picks and a maximum of 8 picks."
- U6: correlated picks change the payout multiplier ("Including correlated projections in your entry can cause payout
  shifting"). This is not a refusal.
- P1: "You must have picks from AT LEAST 2 different teams in order for it to be a valid lineup." / "You can not have
  the same player in your lineup more than once, unless Stacks are available for that player." / "Pick between 2 and
  6 projections." (Colorado: minimum 3) / "Some sports also have specific rules. ... You can find those specific rules
  in the scoring chart for that particular sport."
- P2: "If a player in your lineup does not participate in a single second/snap for any reason, they will be DNPd." /
  "Depending on the sport, picks may be DNPd when a game is canceled or postponed to a new day. Please check the
  scoring chart for the league in question for more information." / "Different PrizePicks markets have specific rules
  regarding what qualifies as a DNP." / "If DNPs or Reboots make it so that all remaining players in a lineup are on
  the same team, this will make the lineup ineligible. In this case, you will be issued an automatic refund."
- P3: DNPs or Reboots that leave "only one pick remaining OR ... only players from the same team" get the lineup
  "canceled and refunded".
- P4: results use "official rulings and statistics" (SportRadar, Genius, Stats Perform, Grid). Changes after settlement
  are not applied.

## Rule table (platform x situation -> rule, source, what the code does)

| Situation | Underdog | PrizePicks | No platform on the line (member research) |
|---|---|---|---|
| Forfeit of a map inside the line's maps | void (U1), verified | not published: **pending** | void (standing rule, 2026-10-08) |
| Forfeit only outside the line's maps (e.g. map 3 on a Maps 1-2 line) | **grades** on the played in-scope maps (U1 "during the time specified" + U2 partial series), verified | not published: **pending** | void (standing rule) |
| Map awarded by default | HLTV records a default as a forfeited map, so same as the two rows above | same as the two rows above | same |
| Whole match forfeited, no map played | void (U1) | DNP = void (P2: no participation), verified | void |
| Player has no row on a played, scraped in-scope map (benched / stand-in) | void (U1 "must play in each map"), verified | played other maps: CS2 threshold in-app only, **pending**. Played no map: DNP void (P2) | unchanged (map missing, so not settled from HLTV) |
| Substituted mid-map, over / under 50% of rounds | rule verified (U1), **not applied**: HLTV map rows carry no per-player rounds | not published | n/a |
| Remake / restart | not published for CS2 (chronobreak rule only). Cannot be seen in the data: HLTV keeps the final map | not published | n/a |
| Rescheduled / postponed | grades within 36 h of the posted start, else void (U1/U2), verified, **not applied** (see "Still open") | "depending on the sport" (P2), CS2 in-app: unverified, not applied | n/a |
| Overtime | counts (U1). Already how totals are computed | not published (in-app) | n/a |
| Entry: all picks from one team | **refused** (U4) | **refused** (P1) | unverified (platform unknown) |
| Entry: number of picks | standard 2-8, flex 3-8 (U5) | 2-6, power and flex (P1). Colorado minimum 3 not checked (state unknown) | not checked |
| Entry: same player twice | same side **refused** (U4 example). Opposite sides: unverified | **refused** (P1). The Stacks exception is not seen for CS2 | not checked |
| Entry: players from both teams of one match | allowed: no restriction published, the payout shifts instead (U6) | published rules pass; CS2 scoring-chart combination rules not public, so **unverified** | not checked |
| Entry after voids: live picks under 2, or all from one team | refunded (U3/U4) | refunded (P2/P3) | n/a |

## What changed (branch close/rules, 235e501)

- `picklog.scope_result`, used by every HLTV settlement path (Maps 1-2 via `_hltv_totals`, Map 1 / Maps 1-3 via
  `_settle_scope`), and `_settle_hltv` (hltv-<id> slugs):
  - For a forfeit, the code works out which map numbers were forfeited from HLTV's `match_maps` rows plus
    `match_overview.forfeited_maps`, and checks them against the `scrape_queue` score. HLTV keeps the forfeited map's
    slot and gives it no row. A forfeit before a played map leaves a gap; any others come after the last played map. If
    the score or the gaps disagree, the result is unknown and the pick stays pending. On the last 60 forfeited matches
    in HLTV (since 2026-07-01, read-only), every case resolved. On an Underdog Maps 1-2 line, 54 would void and 6 would
    grade (map 3 forfeited).
  - The rules per platform are as in the table. For a line with no platform, the previous result (void) and its reason
    are unchanged.
  - Pending: `scope_result` returns `status: "pending"`. `_settle_one` returns 0, so there is no BO3.gg fallback and no
    void after VOID_AFTER_D. `_settle_scope` and `_settle_hltv` return None. The reason is saved once, in
    `result.rule_pending` (rule, platform, forfeited maps).
  - DNP check: a map counts as scraped when 5 or more of the player's team's rows exist on it, using the same joins as
    `hltv._series_rows`. A map that is not scraped yet is never a DNP.
  - Only open picks are affected. Settled and voided picks are not reopened (the recheck paths ignore "pending").
- `slip_batches.py` is now **batch-4**: the candidate search skips any combination Underdog refuses (one team, or the
  same player on the same side). Every slip records `entry_check`. `settle_slip` refunds when the live legs are all one
  team.
- `slips.py`: recommended and loaded slips log `entry_check` (admin `/admin/slips.json`). Loaded legs keep `team`. The
  server builder's selection is unchanged, to stay in step with web/index.html.
- Tests: 17 in tests/test_platform_rules.py. 8 of them fail without the change. Full suite: 1623 passed, 5 skipped.

## Still open / unverified (exact reasons)

1. **PrizePicks CS2 forfeits (inside and outside the line's maps), partial-match DNP, reschedules and remakes.** These
   are in the in-app scoring chart, which is not public and which the browser pane cannot open. Affected picks stay
   pending. To resolve: the owner reads the CS2 scoring chart in the app and pastes its text. Each rule is then switched
   in `platform_rules.RULES["prizepicks"]`.
2. **PrizePicks CS2 combination rules** (for example, opposing players of one match). Same blocker as item 1. Entry
   checks for PrizePicks are `unverified` unless a published rule refuses them.
3. **Underdog reschedule (36 h).** The rule is verified but not applied. matchid links a match only within 3 h of the
   listed start (matchid.WINDOW_H, not this branch's file), and the actual start of a finished match is not stored
   with the pick. Applying the rule needs the HLTV start time saved at settlement.
4. **Underdog "over 50% of rounds"** for mid-map substitutions. Not detectable: HLTV map rows carry no rounds played per
   player.
5. **Remakes.** No CS2 rule on either platform, and they cannot be seen in HLTV data.
6. **BO3.gg-only fallback** (matches HLTV never lists). Unchanged: a forfeit there still voids. BO3.gg does not say
   which map was forfeited.
7. **Underdog same player on opposite sides.** Not published, so marked unverified (not blocked).

## Hand-off for the UI / studio (not edited on this branch)

- Studio / web: call `platform_rules.entry_check(legs, platform, entry_type)`. Each leg needs `team`, `nick` and
  `side`. Block the slip only when `eligible is False`, and show `refusals[0]` as the message. Never show
  `unverified` on member pages. Suggested one-line server hook in the studio slip response:
  `slip["entry_check"] = platform_rules.entry_check(slip["legs"], book)`.
