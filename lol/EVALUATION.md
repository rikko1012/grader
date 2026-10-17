# LoL projection (lol-1): chronological evaluation

## Protocol (written 2026-10-17 before the TEST numbers were seen)

- **Data:** the LoL Esports feed history in the default leagues (`lolesports.MAJOR`: Worlds, MSI, First Stand, LCK, LPL,
  LEC, LCS, LCP, CBLOL, LTA N/S, EWC, Demacia Cup, LCK CL, EMEA Masters, LFL, Prime League, PCS, VCS, LJL), every
  completed match from 2025-01-01 to 2026-10-16. Leaguepedia was not configured, so the LPL is effectively absent
  (the feed carries 7 LPL matches).
- **Unit:** one (match, player). Totals: Map 1 = game 1; Maps 1-2 = games 1 + 2; Maps 1-3 = games 1-3 (only series where
  the player played every game of the scope; Maps 1-3 in a best-of-3 therefore means "map 3 was played").
- **No look-ahead:** a projection uses only games on days before the match day (the same rule live); Elo uses only
  earlier results.
- **TRAIN:** matches 2025-03-01 to 2026-03-31 (January-February 2025 is history only). Everything is chosen here: role
  priors, the stat-in-wins / stat-in-losses ratio, the form (rate / share), HL, K0, KO, KT and the match-strength
  weight EW (grid, lowest per-game squared error), then the negative-binomial size per (stat, scope) by maximum
  likelihood.
- **TEST:** matches 2026-04-01 to 2026-10-16, never used by the fit, reported once.
- **Reported:** MAE and bias against two baselines (the role average; the player's last 10 games), the same model
  without match strength, log-likelihood against Poisson, how often the 80% range holds the result, and calibration
  of P(over) at the x.5 line nearest the projection. Kills Maps 1-2 is also broken down by role and league group.
- **Pass criteria for showing the projection as a "model estimate" (kills, Maps 1-2, TEST):** MAE below both baselines;
  |bias| under 0.3 kills; the 80% range holds 76-84% of results; every calibration bin with 100+ series within 5
  points. This is **not** a betting rule. The calibration lines here are synthetic (the x.5 nearest the projection),
  and the CS2 work showed real offered lines behave differently. Grades and stakes need the real-line check first
  (`lines_archive.jsonl` collects those lines from now on).

## Amendment A1 (written 2026-10-17 03:20 UTC, before any fit or TEST number): consolidated data, 8-month window, baseline

Supersedes the data / period lines above. The earlier protocol stays for the record. Nothing below was chosen after seeing a result.

**Data**
- One canonical history: the data center's tables (`lol/center`), exported to `lol_games` / `lol_player_games`.
- Sources: Leaguepedia (bot login) and LoL Esports. One source per real series; LoL Esports and Leaguepedia ids are linked through the series both hold.
- Window: games from 2026-02-16 to 2026-10-16 (the agreed 8 months).
- Coverage is reported per league: games, missing games, unresolved winners.
- Only finished games count. A live-stats game without finished frames is never stored.

**Periods**
| Period | Dates | Role |
|---|---|---|
| History only | 2026-02-16 to 2026-03-31 | earlier games for the first targets |
| TRAIN | targets 2026-04-01 to 2026-07-31 | priors, grids and negative-binomial sizes |
| TEST | targets 2026-08-01 to 2026-10-16 | reported once |

A projection uses only games on days before the match day, in both the backtest and live.

**Targets and settlement (identical for every model)**
- One (series, player) for each scope:
  - Map 1;
  - Maps 1-2: every best-of-3 or best-of-5 series in which the player played games 1 and 2;
  - Maps 1-3 in a best-of-5;
  - Maps 1-3 in a best-of-3 that reached game 3. This is reported separately and marked conditional. A 2-0 best-of-3 is never scored under Maps 1-3: no platform's rule for it is confirmed.
- A series with an unknown or invalid game order, or with a player missing from a game in scope, is excluded and counted.

**Models**
- **B0 (simple baseline).** The player's per-game average over their earlier games, shrunk toward the TRAIN average for their role in their league (by role alone when the role and league have fewer than 200 TRAIN games). The prior weight k ∈ {2, 5, 10, 20, 40} and the NB size per (stat, scope) are fitted on TRAIN.
- **lol-1.** As specified above: the grid on TRAIN and the NB size on TRAIN.
- **C1** (the regularised component candidate drafted 2026-10-17) is not run. Its components (team kills x share x opponent) are what lol-1's share form already tests, so it would not add something meaningfully different.

**TEST metrics, for kills and for assists**
- MAE and bias.
- Mean negative-binomial log score per (series, player).
- 80% interval coverage.
- Calibration at the nearest x.5 line. These are synthetic lines, development evidence only.
- The log-score difference (lol-1 minus B0), with a 95% interval from a series-grouped bootstrap (2,000 resamples).
- Breakdowns by league group and by role.

**Selection (per stat)**
- lol-1 is the production projection for a stat if, on TEST, both hold for Maps 1-2 and for best-of-5 Maps 1-3:
  - the lower bound of the log-score gain interval is above 0;
  - its MAE is not higher than B0's.
- Otherwise B0 is the production projection for that stat.
- Either way the result is development evidence. It is not an advantage at real lines. That needs saved pre-match forecasts settled at archived offered lines (300 player-markets across at least 60 series, scored once). No grade or stake is shown until then.

## Amendment A2 (2026-10-17 03:25 UTC, still before any fit): Maps 1-3 in a best-of-3

The owner confirmed the rule for both platforms on 2026-10-17: "the kills from Maps 1-3 reflect the kills from Maps 1-3 - the same as CS2". A Maps 1-3 market counts the games among 1-3 that are played, so a best-of-3 that ends 2-0 settles on games 1 + 2.

This replaces A1's best-of-3 lines. The rest of A1 stands.
- **Target.** Every best-of-3 series counts under Maps 1-3. The target is the player's total over the games played (two or three). The player must have played every game that was played.
- **Forecast, for every model alike.** A mixture of the 2-game and 3-game totals, with P(game 3 is played) = 2p(1-p). Here p is the team's per-game win chance from the history's Elo, from earlier days only. The 2-game part uses the Maps 1-2 negative-binomial size and the 3-game part the Maps 1-3 size.
- **Reporting.** Best-of-3 Maps 1-3 is reported in its own row, next to best-of-5 Maps 1-3. The selection rule is unchanged: it uses Maps 1-2 and best-of-5 Maps 1-3. The best-of-3 Maps 1-3 row is reported, not used to select.

## Amendment A3 (2026-10-17 03:30 UTC, still before any fit): market scope follows the series format

The owner confirmed on 2026-10-17 that Maps 1-2 lines are posted on best-of-3 series and Maps 1-3 lines on best-of-5 series. Every game in a market's scope is therefore always played.

This replaces A1's and A2's best-of-3 Maps 1-3 rows.

**Scopes evaluated:**
- Maps 1-2 = best-of-3 series, games 1 + 2.
- Maps 1-3 = best-of-5 series, games 1-3.
- Map 1 is reported for completeness.

**Selection.** Uses Maps 1-2 (best-of-3) and Maps 1-3 (best-of-5). The rule is otherwise unchanged.

**Mismatched lines.** A live line whose scope does not fit its series' format (for example, Maps 1-3 on a best-of-3) is saved, flagged `format_scope_mismatch` and kept out of slips. The A2 mixture is still the forecast for such a line.

## Results

(filled in from `python -m lol.backtest` once the history is complete)
