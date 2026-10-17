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

## Amendment A4 (2026-10-17 04:05 UTC; no TEST number has been computed or seen): development split, candidates, metrics

The owner's brief of 2026-10-17 adds candidates and factor tests. Using TEST to choose any of them would turn TEST into development data. TEST therefore stays sealed for **one final comparison**. All development moves to a validation split inside the old TRAIN window.

**Periods**
| Period | Dates | Use |
|---|---|---|
| History only | 2026-02-16 to 2026-03-31 | earlier games only |
| DEV-TRAIN | targets 2026-04-01 to 2026-06-14 | fitting |
| VALID | targets 2026-06-15 to 2026-07-31 | development: tuning, factor groups, hypotheses, the provisional choice |
| TEST | targets 2026-08-01 to 2026-10-16 | sealed; scored once, all candidates together, refit on DEV-TRAIN + VALID |

Every period is cut on the series start date, so all players and games of a series stay in one period.

**Candidates.** All are scored on identical targets, the same day cutoff and the A3 scopes.
- **B0**, the simple baseline.
- **lol-1**, current.
- **lol-2**, the proposed revision: whole-match simulation through lol-1's interface. It models both teams, game duration, combined kill pace, the kill split between the teams, and each player's share. Player kills sum to team kills. Assists come from a participation model. Deaths relate to opposing kills plus executions. Each simulation sums the required games.
- **Market-informed variant.** Only where historical prices saved before the match actually exist. For props they do not (the line archive starts 2026-10-17). Match-winner prices are used only if a timestamped pre-match history is obtained. Otherwise the variant is reported as not evaluable.

**Metrics.** For each stat, scope, role, league group and history-depth band, with 95% intervals from a series-grouped bootstrap:
- Projections: MAE, squared error, bias.
- Probabilities: log score of the count, log-loss and Brier of P(over), calibration, and 80% interval coverage.
- Development line for P(over): the player's median total of the same scope over their previous comparable series (up to 10, at least 3), rounded to x.5. It is model-independent and known before the match. It is development evidence, not a book line.

**Provisional production choice (2026-10-17).** B0 against lol-1 by the A1 rule, applied on VALID instead of TEST. It is labelled provisional on every forecast. The final choice is made only by the TEST comparison above.

**Factor groups and hypotheses.**
- Factor groups (player, team/opponent, context, draft, market) and the owner's old decision rules are tested on VALID, as increments over the chosen projection.
- A group or rule is kept only if it improves VALID log score with a 95% interval above 0.
- Whatever survives is then confirmed on TEST in the single final comparison.

## Amendment A5 (2026-10-17 06:00 UTC; TEST not opened - lol/test_opened.json absent): the production choice and the fit file

Fixes how the single final comparison turns into the production model. Nothing here was chosen from a TEST number.
- **History used.** The repaired export from the data center after the Leaguepedia backfill completed (all 18 windows), pulled once into a SQLite snapshot; the snapshot's file hash is recorded in the fit.
- **Development.** `python -m lol.evaluate tune` (lol-2 settings, VALID log score) and `python -m lol.evaluate dev` on that snapshot. A partial-history dev run (2026-10-16 23:04 UTC, VALID only) informed nothing but runtime and was superseded.
- **Final.** `python -m lol.evaluate final` runs once with the tuned lol-2 setting. TEST (2026-08-01..10-16) was never scored before this run.
- **Choice per stat.** First B0 vs lol-1 by the A1 rule (both Maps 1-2 / best-of-3 and Maps 1-3 / best-of-5: log-score gain 95% interval above 0 and MAE not higher). Then lol-2 replaces that choice only under the same rule against it. Otherwise the simpler choice stays.
- **Fit file** (`lol/model_fit.json`, written by `python -m lol.evaluate fit`): the selected forms' parameters fitted on DEV-TRAIN + VALID (targets 2026-04-01..07-31), the NB sizes, lol-2's parameters when selected, the selection rule and evidence, the snapshot hash, fitting dates, the simulation seed rule (crc32 of market key and day) and the final report's hash.
- **Stakes.** Unchanged: 0U for LoL until the real-line requirement (300 settled player-markets across 60+ series) passes. Grades follow the published bands.

## Development record (2026-10-17 13:40 UTC, written before the TEST numbers were returned)

- **History snapshot:** production export after the full backfill, the wrong-merge repair (3 player pairs split) and the
  duplicate-series fix (LoL Esports copies of Leaguepedia series with a differently named team: 235+ games removed).
  10,756 games, 107,553 player rows, 3,982 players, 717 teams, 76 leagues (LPL 538 games, Feb-Sep).
  sha256 of the ordered rows: a1f892f40649a6da1630fbf0a0af935e5ab318b06394bea6457c5b373653c726.
  An earlier snapshot (8897ada1..., before the duplicate fix) was used for nothing: its tuning run was stopped.
- **lol-2 tuning (VALID, kills + assists, Maps 1-2 and 1-3 log score):** hl 60, k0 16, k_team 8 (-3.0199 over 7,416 totals;
  the grid spans -3.0199 to -3.03).
- **VALID (18,660 DEV-TRAIN targets, 8,126 scored; Maps 1-2: 3,093 totals / 311 series; Maps 1-3: 615 / 62):**

| stat, scope | MAE B0 / lol-1 / lol-2 | log-score gain lol-1 vs B0 (95% CI) | lol-2 vs B0 | lol-2 vs lol-1 |
|---|---|---|---|---|
| kills, Maps 1-2 | 3.194 / 3.110 / 3.119 | +0.024 (+0.013, +0.035) | +0.025 (+0.014, +0.036) | +0.001 (-0.008, +0.009) |
| kills, Maps 1-3 | 4.076 / 4.013 / 3.974 | -0.019 (-0.054, +0.012) | +0.002 (-0.029, +0.031) | +0.022 (+0.003, +0.042) |
| assists, Maps 1-2 | 5.298 / 5.180 / 5.203 | +0.023 (+0.006, +0.039) | +0.031 (+0.014, +0.047) | +0.008 (-0.002, +0.018) |
| assists, Maps 1-3 | 7.630 / 7.623 / 7.554 | -0.012 (-0.057, +0.034) | -0.010 (-0.064, +0.040) | +0.002 (-0.037, +0.039) |
| deaths, Maps 1-2 | 2.676 / 2.584 / 2.589 | +0.035 (+0.023, +0.045) | +0.034 (+0.020, +0.048) | -0.001 (-0.009, +0.007) |
| deaths, Maps 1-3 | 3.650 / 3.515 / 3.504 | +0.049 (+0.012, +0.087) | +0.056 (+0.010, +0.104) | +0.008 (-0.014, +0.031) |

- **Provisional choice (A1 rule on VALID):** kills B0, assists B0, deaths lol-1. lol-1 and lol-2 beat B0 on Maps 1-2 for
  every stat; on Maps 1-3 (62 series) the intervals include 0 for kills and assists, so the simpler model stays.
- **Every model under-projects on VALID** (mean projection minus actual, Maps 1-2: kills -0.36 to -0.47, assists -0.46 to
  -0.69, deaths -0.37 to -0.59). Recorded, not corrected: a correction would be a new candidate, not part of this plan.

## Results (final comparison, run once: TEST opened 2026-10-17 13:40:00 UTC, lol/test_opened.json)

Fit on DEV-TRAIN + VALID targets (2026-04-01..07-31, 26,786 targets); TEST = targets 2026-08-01..10-16 (14,336 scored).
Same snapshot as the development record; lol-2 at the tuned setting (hl 60, k0 16, k_team 8). Output:
`lol/eval/final_2026-10-17.json` (sha256 eeb40a47...); per-target rows archived with the snapshot in
`exports/lol_eval_2026-10-17/` (SHA256SUMS there). Targets without a known best-of are excluded and counted (440 per scope).

| stat, scope (series) | MAE B0 / lol-1 / lol-2 | bias lol-1 | 80% range held lol-1 (expected) | log-score gain lol-1 vs B0 (95% CI) | MAE gain lol-1 vs B0 | lol-2 vs lol-1 log score |
|---|---|---|---|---|---|---|
| kills, Maps 1-2 (742) | 3.218 / 3.090 / 3.127 | -0.13 | 85.4% (86.0%) | +0.035 (+0.026, +0.043) | +0.129 (+0.099, +0.161) | -0.003 (-0.008, +0.002) |
| kills, Maps 1-3 (353) | 3.891 / 3.684 / 3.742 | -0.05 | 84.9% (84.7%) | +0.038 (+0.027, +0.051) | +0.207 (+0.151, +0.265) | -0.007 (-0.016, +0.001) |
| assists, Maps 1-2 (742) | 5.427 / 5.199 / 5.243 | -0.23 | 82.5% (82.9%) | +0.036 (+0.025, +0.046) | +0.228 (+0.158, +0.296) | +0.001 (-0.006, +0.008) |
| assists, Maps 1-3 (353) | 6.568 / 6.263 / 6.315 | -0.39 | 85.5% (82.2%) | +0.030 (+0.016, +0.045) | +0.305 (+0.197, +0.432) | +0.004 (-0.012, +0.019) |
| deaths, Maps 1-2 (742) | 2.671 / 2.609 / 2.625 | -0.19 | 84.3% (85.3%) | +0.025 (+0.016, +0.034) | +0.062 (+0.036, +0.088) | -0.002 (-0.009, +0.005) |
| deaths, Maps 1-3 (353) | 3.122 / 3.058 / 3.106 | +0.11 | 85.7% (84.2%) | +0.026 (+0.013, +0.041) | +0.064 (+0.017, +0.112) | -0.010 (-0.019, -0.001) |

P(over) at the development line (lol-1, Maps 1-2 / Maps 1-3): Brier kills 0.221 / 0.214, assists 0.223 / 0.211, deaths
0.228 / 0.215; log-loss kills 0.632 / 0.614 (B0 0.654 / 0.635). Calibration bins, by-league, by-role and by-history-depth
breakdowns are in the output file.

**Choice (A5):** lol-1 for kills, assists and deaths - on TEST it passes the A1 rule against B0 on both Maps 1-2 and
Maps 1-3; lol-2 does not pass against lol-1 for any stat, so it is not used. `lol/model_fit.json` was written by
`python -m lol.evaluate fit` from this file.

**What the final period influenced.** The choice itself was made on TEST, as registered in A4/A5 (VALID's provisional
choice was B0 for kills and assists, because VALID's Maps 1-3 had only 62 series). Nothing else was chosen after TEST was
opened. TEST is now spent: later model changes need new forward data.

**Factor groups and the owner's old rules (VALID, residuals of the provisional B0):** no factor group improved VALID except
draft for deaths Maps 1-2 (MAE +0.011, 95% CI +0.003..+0.021); the favourite "sweet spot" rule (Elo proxy for -150..-400)
adds information over B0, which has no match-strength term - lol-1, the selected model, has one. None was part of the
registered final comparison, so none is used; they stay hypotheses. H2H shows nothing.

**Limits.** Development lines are the player's own median, not book lines - this is not evidence of an advantage at
Underdog / PrizePicks lines. Stakes stay 0U until 300 settled player-markets across 60+ series at offered lines.
Projections run slightly low on Maps 1-2 (bias -0.1 to -0.2 kills/deaths, -0.2 to -0.4 assists).
