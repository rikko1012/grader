# LoL grading-logic upgrade study (gu-lol-1) - definitions frozen before scoring

Owner request 2026-11-11: "full grading logic upgrade testing new factors ... on previous projections versus how it ended
to see what will improve grades". Research only. Nothing here changes a live grade; a passing item becomes a flagged
candidate (default off) plus a before/after preview for the owner.

This file is committed and pushed BEFORE any candidate number is computed. Anything seen before the freeze is listed in
section 7.

## 1. Data
- Read-only local copy `lol_1109.db` (pulled 2026-11-09 ~07:30 UTC, sha256 prefix 3ff3c741ed88cf34): 13,042 games,
  130,405 player rows, 26,728 forecasts, 2,107 settlement units. Production check 2026-11-11 23:05 UTC: no new graded
  unit since (the 17 newer settlement rows are version-2 `link_mismatch` re-settlements of old units; the next series is
  the Worlds final, Nov 14). No new production read is needed for scoring.
- Forward record (A15 rule): latest pre-match forecast per (platform, market_key, line) joined to its latest settlement.
  2,059 graded lines + 31 pushes, 42 series, 2026-10-17 .. 2026-11-08 (41 Worlds 2026 series, 1 EMEA Masters). Kills and
  assists only: no deaths line has ever been offered.
- De-duplication key: (series, player, stat, scope, platform, odds type, line). Rows sharing a key are collapsed to the
  latest forecast.
- CIs: 95% bootstrap, 2,000 resamples of whole series; differences are paired on the same lines.

## 2. Baseline
**lpf-1 for kills and assists** (the live model since 2026-11-09), as walk-forward predictions on the forward record:
variant W + live offset + decider model, production history only, refits 2026-10-01 and 2026-11-01, each fitted only on
days before the refit (the "production history" row of `esports_hit_2026-11-09.md`, 59.5%). Side = the larger of
P(over) / P(under) at the offered line. Letter = side chance >= 55% (bands of `lol/center/forecast.py`).
Deaths baseline = lol-1 (live), on history only.

## 3. Projection-layer candidates
Each candidate = lpf-1's 93 features + ONE feature group, same booster settings (600 rounds, same parameters, same seed),
same refit dates, same NB-size and win/decider procedure. Nothing is tuned; every feature uses games on days strictly
before the target's match day. The prop line is never an input.

| id | group | new features (all pre-match) |
|---|---|---|
| P1 opp-sim | opponent strength / similar-ranked opponents | opponent's and team's plain-Elo rating; the player's kills / deaths / assists per game against opponents of similar rating (kernel: time half-life 90 d x Gaussian on rating difference, sd 100 Elo, pseudo-count 3 at his 60-day mean); effective sample of that kernel; mean opponent rating in his last 10 games |
| P2 role-alloc | role-specific share, opponent deaths allowed by role, patch/meta | team's share of kills and of assists going to this ROLE (EW 60 d, any player); kills and assists per game the opponent CONCEDES to this role (EW 60 d); the role's share of all kills in the league (120 d) and on the current patch so far |
| P3 form-win | recent form windows | last-3 and last-10 means of kills / deaths / assists; last game's kills and assists; standard deviation of kills and of assists over his last 20 games |
| P4 tenure-role | time on team, role-swap lag | share of his last 10 games in today's role; games in today's role; days since his first game for this team; days since his first game in the data; number of the team's last-lineup players with fewer than 10 games for the team |
| P5 intl-exp | international vs domestic | his earlier international games (count); his international-minus-domestic kills and assists per game over 730 d (missing below 5 international games); team's and opponent's earlier international games |
| P6 stage | stage type done properly | stage class from Leaguepedia MatchSchedule Tab / Phase of the series (regular or group; play-in; swiss; bracket / playoffs; final), an elimination flag (bracket or final), swiss / round number where given; replaces nothing (the old `playoffs` flag stays) |
| P7 champ | champion-pool kill profile (pre-draft) | mean over the champions of his last 10 games of that champion's running average kills and assists per game in his role (champion needs 20 earlier games, else missing); distinct champions in last 10 |
| P8 pace-x | team kill pace x share, game length expectation | expected team kills = team kills scored (30 d) x opponent kills conceded (30 d) / league average; that x his kill share and x his assist share; expected minutes = mean of both teams' 30-d game length; that x his kills / assists / deaths per minute |
| P9 ALL | P1-P8 together | |
| P10 mkt-win | market match-winner odds (forward only) | no refit: where a Polymarket series-win chance was saved with the forecast, the lpf-1 mixture is re-weighted with the game-win chance implied by it (independent games inverse for best-of-3 / best-of-5) instead of the model win chance; other rows unchanged |
| P11 side-oracle | side (blue/red) | UPPER BOUND ONLY: the actual game-1 side is given to the booster. Side is not known pre-match in general, so this can never be promoted from this test; it only shows whether collecting announced sides is worth doing |

Already inside lpf-1, so not re-tested as new factors: player days since last game (`plast`), games with the team
(`p_team_n`, `p_team_frac10`), league switch (`p_league_frac10`), series format and game number (`bo`, `gn`, `decider`),
team kill pace and opponent kills conceded (`t_tk*`, `o_ok*`), game length (`*_dur*`), patch pace (`pa_*`), tournament pace
(`ev_*`), international / tier flags. Failed earlier and not repeated: ep-1/2/3, gn-1, xl-1, lg-1, mb-1, plain lol-1, lb-1,
line gap / bump rules R-a..R-d.

**Deaths (D1).** lpf-1's deaths booster (same harness; and with P9's features) against lol-1 on history: all-outcome log
score, MAE, and hit rate at book-style lines (nearest half-integer to the last-5 average), refits 2026-07-01 .. 2026-11-01.
No deaths line exists, so D1 CANNOT pass the owner's real-line rule; it is reported as evidence only.

## 4. Grade-layer candidates (projection and printed chance unchanged; baseline lpf-1 chances at the forward lines)
| id | rule | fitted? |
|---|---|---|
| G1 WL-1 | whole-number lines: when the chance side and the projection side disagree (e.g. Under with the mean above the line), take the projection (mean) side | none |
| G2 WL-2 | every line: side = mean above / below the line | none |
| G3 AS-1 | Over/Under asymmetry in the LETTER only: letter chance = side chance - (g(stat, side) - g(all)), where g = stated-minus-hit gap of that stat x side on EARLIER settled days (needs 100 earlier rows, else 0); zero-mean by construction (not a shrink toward 50%); bands unchanged | daily, earlier days only |
| G4 TH-1 | letter threshold per stat x side: a stat x side whose earlier-day lettered hit rate is below 50% (needs 60 earlier lettered rows) shows Lean instead of a letter | daily, earlier days only |
| G5 OS-1 | Over-only Underdog lines (and PrizePicks demon / goblin, reported apart): give a letter when the model side is the offered side | none |
| G6 SC-1 | descriptive only: hit by stat x scope x best-of and by letter band under lpf-1, with CIs | none |

Walk-forward for G3 / G4: burn-in series days Oct 17-23 (training only), test Oct 24 - Nov 8. Rules with no fitted number
are reported on all days and on the test days.

## 5. Pass bar (owner acceptance rule, fixed here)
Side rules and projection candidates, on the forward lines, paired with the baseline:
1. hit rate over all graded lines higher;
2. Over-pick hit rate not lower AND Under-pick hit rate not lower;
3. graded count (lines with a side) not reduced, and letters >= 95% of baseline;
4. Over share not farther from the data's over rate than the baseline's;
5. **PASS** = 1-4 and the paired 95% CI of the hit difference excludes 0. **NEAR-MISS** = 1-4 with a gain of at least
   +1.0 point but a CI that includes 0 -> recommended for a forward shadow, with the sample that would settle it.
   Everything else = FAIL. (Stricter than the Nov 9 lpf-1 acceptance because about 17 rules are tried here on a record
   that has been read before; with 17 tries, one "CI excludes 0" by luck is still likely, so a PASS also needs the history
   check below not to point the other way.)
6. History check (support only, cannot pass anything alone): the same candidate on refits 2026-07-01 .. 2026-09-01 at
   book-style lines for kills + assists - hit rate not lower than lpf-1's.

Letter rules (G3, G4, G5): lettered hit rate higher with the paired / added-pick CI as above, lettered count not lower,
Over and Under lettered hit not lower, Over share of letters not more lopsided. G5 adds picks, so its bar is: added picks
hit at or above the baseline lettered hit rate and their CI lower bound >= 50%.

Calibration gap, log loss, MAE and bias are reported and never a veto. No printed chance is shrunk toward 50%.

## 6. Power statement (fixed before results)
Series-level noise on this record: the baseline's CI is about +/-4 points on 42 series; a paired difference between two
projections has a CI of about +/-1.5 to +/-3.5 points. A true +1 point gain therefore needs roughly 4-10x this record
(about 170-400 series) for its CI to exclude 0; a true +2 points needs about 1.5-3x (60-130 series). Each near-miss gets its
own number from its observed paired spread.

## 7. Seen before this freeze (disclosure)
- All Nov 5 / Nov 9 reports on this same forward record (lpf-1's 59.5%, the lol-1f tables, the line-gap study).
- While checking the data layout under the OLD live model (lol-1f, not the baseline used here): whole-number lines,
  Under side with the projection above the line: 14 hits / 15 misses (+6 pushes); half lines, same disagreement: 169 / 128.
  No lpf-1 number for any rule in sections 3-4 has been computed.
