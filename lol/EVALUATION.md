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

## Archived PR #8 protocol notes

The earlier branch protocol below is retained for provenance. Its source-only dataset and dates are superseded by the consolidated amendments A1-A5 above; it is not a second production protocol. The measurement correction (discrete interval coverage) and chronological-ordering fix remain applicable.

### Earlier branch amendment A1 (2026-10-17, owner decision, written before any TEST number was seen)

The owner chose to cover the current props with about 8 months of recent history and to fetch the full history in a
separate run. Until that run exists, the fit and the results below use:

- **Data:** LoL Esports feed games from **2026-02-17** on only, for history and for targets (`--history-from
  2026-02-17`): the same window the live database holds after a default `sync`.
- **TRAIN:** matches 2026-03-17 to 2026-07-31 (17 Feb to 16 Mar is history only). **TEST:** matches 2026-08-01 to
  2026-10-16.
- Everything else (grid, metrics, pass criteria) as above.

The full-history protocol (TRAIN 2025-03-01 to 2026-03-31, TEST 2026-04-01 to 2026-10-16, Leaguepedia included) is
for the separate full-history run, which replaces this fit when it is done.

```
python -m lol.backtest --history-from 2026-02-17 --train-from 2026-03-17 --test-from 2026-08-01 --test-to 2026-10-17 --write
```

### Earlier branch amendment A2 (2026-10-17, measurement correction found in code review, before any TEST number was seen)

Counts are whole numbers, so the "80%" range (`model.interval`) is the narrowest whole-number range holding *at least*
80% of the model's probability, often 85-90%. Judging it against a flat 80% would call a well-calibrated model
under-confident. The coverage criterion becomes: **the share of results inside the range is within 4 points of the
probability the model itself puts on that range** (`cover80_nominal`, averaged over the same series). Everything else
is unchanged. The same review also fixed, before results: the last-10 baseline taking a day's games in kill order, and
a game held by both sources being counted twice.

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

## Amendment A6 (2026-10-17 21:10 UTC): what the results show, the data repair, and forward evaluation

**What the results show.**
- The MAE gains of lol-1 over B0 (Results, above) are evidence that lol-1 predicts players' actual totals better than the simple baseline over August–October.
- The P(over) scores in those tables are measured at each player's own historical-median line. They are not evidence of an advantage at Underdog or PrizePicks lines, and nothing here claims one.
- The August–October period chose the production model: VALID's provisional choice was B0 for kills and assists, and the final period selected lol-1. Its numbers are kept as recorded. They are not an untouched validation of the selected model.

**Data repair (2026-10-17 evening) and the fit.**
The identity and duplicate repairs found these defects:
- LoL Esports academy matches had been linked to their main teams' series. Academy players' games sat in main-roster players' histories (e.g. DuDu 45 games, Lehends 29).
- Spelling variants of one Leaguepedia page had been split into separate ids.
- LoL Esports copies of Leaguepedia series had been counted twice.

They affected both the training snapshot (a1f892f4) and the live inputs. The effect on the fit was measured with a frozen refit: the same fit period and the selected lol-1 settings, with only role priors, team-kill mean, win ratios and NB sizes re-estimated on the repaired history.
- The NB sizes are unchanged.
- The team-kill mean moved 17.0387 → 17.0360.
- Projections over the August–October targets moved by at most 0.011 (mean ≤ 0.003).
- MAE changed by at most 0.0001.

The deployed fit is therefore kept, with no new version. Live forecasts made after the repair use the repaired history; each records the export it used (`inputs.data_version`). Forecasts saved before the repair are unchanged.

**Live history window.** Scout stores every source record it receives; the full-history collector reaches back to 2025-01-01. Live projections and Elo use the games of the last 245 days (`LOL_FORECAST_HISTORY_DAYS`), the window lol-1 was evaluated on.

**Forward evaluation (from now on).** `learning.dashboard` scores:
- **What it scores:** saved pre-match forecasts at the offered lines, with their saved inputs.
- **Statuses:** pushes and voids are counted apart from graded results, per platform rule.
- **Counts:** forecast rows, distinct offered props, distinct player props and distinct matches are kept separate.
- **Uncertainty:** intervals resample whole series, because same-match props are correlated.

The real-line requirement is unchanged: 300 settled player-markets across 60+ series.

**Future factors** (league-adjusted kill pace is the first candidate: cross-league team and opponent factors are relative to an all-league average) are judged as increments over the deployed lol-1, not over B0. They are tested on forward data or a development split that excludes the opened August–October period.

## Amendment A7 (2026-10-19, written before any A7 number was computed): the owner's full factor list as increments over lol-1

The owner's brief of 2026-10-19 lists every factor that should go into a LoL prop grade, "including team win odds". Under
the standing rule a factor moves a grade only after a chronological test shows it helps; the rest are shown at weight 0.
A6 requires new factors to be judged as increments over the deployed lol-1, on forward data or a development split that
excludes the opened August-October period.

**Data.** Production's repaired history (lol_games + lol_player_games), pulled read-only on 2026-10-19 (local copy
sha256 prefix b8345d955b098cd2). It starts 2025-11; the 2025-01..10 backfill never landed.

**Periods (cut on the series start day).**
| Period | Targets | Use |
|---|---|---|
| History only | 2025-11-01 .. 2025-12-31 | earlier games only |
| F-TRAIN | 2026-01-01 .. 2026-04-30 | fit the increment coefficients |
| F-VALID | 2026-05-01 .. 2026-07-31 | scored once for this amendment |
| (spent) | 2026-08-01 .. 2026-10-16 | not used |

Caveat recorded in advance: lol-1's own settings were fitted on 2026-04..07 targets, which overlap F-TRAIN's last month and
F-VALID. The base projection is therefore partly in-sample there; this makes an increment harder, not easier, to pass.

**Base.** The deployed lol-1 (lol/model_fit.json: params, priors, NB sizes) with the Elo win chance from the same history,
unchanged. Every candidate multiplies its per-game mean: mean = lol-1 mean x exp(beta . z).

**Factors (pre-match only: games on days before the match day; z-scored on F-TRAIN; missing = 0 plus an indicator).**
| Group | Owner's items | Features |
|---|---|---|
| pace | expected game length / tempo; patch pace | both teams' time-weighted game length; combined kills per minute (both teams) |
| script | team strength differential, expected stomp | (p_win - 0.5)^2 from Elo (the strength term itself is already in lol-1) |
| matchup | lane opponent, opponent style | the opposing player in the same role (game 1 starters): his deaths (kills / assists) or kills (deaths) per game vs his role |
| form | L5-L10 form, sample quality | last-5 average vs long-run rate; opponents' win rate in his last 10 games |
| individual | KP%, kill / damage share, DPM, CS/min, vision, death share | each vs the role average, shrunk |
| team | team form, CKPM style, stage, league | team last-10 win rate vs long-run; playoffs flag; league group |
| patch | patch age, sample on current patch | days since the league's current patch first appeared; share of his last 10 games on it |
| situational | schedule density, roster stability, champion comfort | days since the team's last game; share of the team's last 5 games with today's 5 starters; distinct champions in his last 20 |

Not testable pre-match or not in the data, and shown at weight 0: the actual draft and champion classes (known only after
the draft; no class table); GD / XPD / CSD at 15 (not ingested); side for games 2+ (not known); travel and coaching (no
source); patch champion strength (no table). Market team win odds: no timestamped pre-match history exists for the
period, so they are logged with every live forecast from this release and judged forward (as A4's market variant).

**Fit.** Per stat (kills, assists, deaths), beta by negative-binomial maximum likelihood on F-TRAIN targets of all scopes
(Map 1, Maps 1-2 best-of-3, Maps 1-3 best-of-5), NB size fixed at lol-1's, ridge penalty 1.0 per standardised coefficient.
Each group is fitted alone, and all groups together ("ALL").

**Decision (one per stat, fixed now).** ALL replaces lol-1 for a stat only if on F-VALID:
- Maps 1-2 log-score gain over lol-1 has a 95% series-grouped bootstrap interval (2,000 resamples) above 0;
- Maps 1-3 log-score gain point estimate is not below 0;
- Maps 1-2 MAE is not higher than lol-1's.
Single groups are reported as evidence only; a group that passes alone while ALL fails is not promoted (it needs forward
data). A passing stat becomes model version lol-1f with ALL's coefficients refitted on F-TRAIN + F-VALID; forecasts made
before it keep their version. Stakes stay 0U under the unchanged real-line requirement.

### A7 result as run (2026-10-19; lol/eval/factors_a7_2026-10-19.json)

F-TRAIN 36,832 units per stat, F-VALID 29,681. ALL vs lol-1 on F-VALID, log-score gain (95% series-grouped interval):

| stat | Map 1 | Maps 1-2 | Maps 1-3 | Maps 1-2 MAE lol-1 -> ALL | A7 rule |
|---|---|---|---|---|---|
| kills | +0.0048 (+0.0035, +0.0062) | +0.0051 (+0.0026, +0.0078) | +0.0022 (-0.0030, +0.0077) | 3.006 -> 2.993 | pass |
| assists | +0.0038 (+0.0014, +0.0063) | +0.0069 (+0.0018, +0.0119) | +0.0039 (-0.0062, +0.0138) | 5.135 -> 5.086 | pass |
| deaths | +0.0044 (+0.0023, +0.0066) | +0.0056 (+0.0018, +0.0096) | -0.0067 (-0.0162, +0.0023) | 2.511 -> 2.494 | fail (Maps 1-3 below 0) |

Single groups (evidence only): individual statistics carry most of it (kills Maps 1-2 +0.0024, assists +0.0032), then form;
pace, script, team and patch add nothing measurable over lol-1 (whose match-strength term already uses the Elo win chance).
Kills Maps 1-3 MAE rose 3.696 -> 3.721 although its log score did not fall; recorded.

## Amendment A7.1 (2026-10-19, written after the A7 result, before re-running): lineups knowable before the match

The A7 run took each match's actual game-1 starters for two features (the lane opponent; whether today's five played the
team's last five games). Live forecasts do not know the starters before the match. To make the tested features the same
features the live forecast can compute, both use the **lineup each team fielded in its last game before the match day**
(by role). Nothing else changes: periods, features, ridge, the decision rule. The study is re-run once with this
definition; the A7.1 run alone decides, under A7's rule. The change is forced by what is knowable live, not by a result.

### A7.1 result (2026-10-19; lol/eval/factors_a71_2026-10-19.json) - the deciding run

| stat | Maps 1-2 log-score gain (95%) | Maps 1-3 | Maps 1-2 MAE lol-1 -> ALL | A7 rule |
|---|---|---|---|---|
| kills | +0.0043 (+0.0019, +0.0069) | +0.0025 (-0.0027, +0.0079) | 3.006 -> 2.995 | pass |
| assists | +0.0068 (+0.0016, +0.0118) | +0.0027 (-0.0069, +0.0126) | 5.135 -> 5.089 | pass |
| deaths | +0.0051 (+0.0008, +0.0092) | -0.0068 (-0.0159, +0.0020) | 2.511 -> 2.494 | fail |

**Production (lol-1f).** `python -m lol.factor_study fit` refitted ALL on F-TRAIN + F-VALID (66,513 units per stat) into
`lol/factor_fit.json` for kills and assists; deaths has no fit and stays lol-1. Live forecasts (lol/center/forecast.py) use
the same code (lol/factors.py); on the pulled history the live index reproduced the study's features exactly (300 and
400 sampled F-VALID targets, maximum difference 0.0). Each lol-1f forecast records lol-1's projection (`lambda_lol1`), the
multiplier and every feature's effect; deaths forecasts show the same features at weight 0. Every forecast also saves the
team's Elo win chance and, when a market is open, the Polymarket match-winner price (no bookmaker margin) for a forward
test. Forecasts made before lol-1f keep their version. Stakes stay 0U under the unchanged real-line requirement.

**What this does and does not show.** The gains are small (about +0.004 to +0.007 log score per player-market on
development data; lol-1's gain over B0 was about +0.035). They are development evidence on synthetic totals, not evidence
of an advantage at Underdog / PrizePicks lines - that still needs the forward real-line count.

## Amendment A8 (2026-10-20, written before any candidate number was computed): event pace (ep-1)

**Why.** The owner asked (2026-10-20) for better LoL grades. The forward record is 2 settled series (Worlds 2026 Play-In,
lol-1, 101 lines: graded 38-40 at P1, printed 68.7%), far too few to tune on. A trend scan of lol-1f's own projections on
the pulled history (scratchpad lol_trends.py / lol_trends2.py; aggregate bias by period, scope, tournament and home league
- no candidate was scored) showed: domestic leagues within about 2%; international events off by about 10-15% in both
directions, event by event (MSI 2026 and Esports World Cup 2026 projected too low; Worlds 2026 Play-In too high: kills
+24%, assists +34% on 4 Map 1 series). Reading: each event has its own kill environment (patch, stage, field) that a
player's domestic history cannot carry. **Disclosure:** that scan looked at F-VALID and the spent period in aggregate, so
neither is a clean hold-out for this idea; hence the two-period rule below and the forward check.

**Candidate ep-1 (one family, all events, domestic included).** For a target series on day d in tournament T (its game 1's
tournament name) and stat s:
- every game of T on days before d, between teams A and B: actual = both teams' total s in that game; expected = the mean of
  A's and B's average per-game total s (own + opponent's) over their games outside T in the 120 days before d (a team
  with no such games is left out of the sum);
- c = (sum actual + K * m) / (sum expected + K * m), m = mean expected per game; no earlier game of T: c = 1;
- the projection becomes lol-1f mean x c^b (the NB size unchanged).
- Grid chosen on F-TRAIN only (best mean NB log score over all scopes): K in {2, 5, 10, 20, 40, 80} games, b in {0.5, 1.0}.

**Decision (per stat, kills and assists; fixed now).** ep-1 replaces lol-1f for a stat only if it passes A7's rule (Maps 1-2
log-score gain 95% series-grouped interval above 0; Maps 1-3 point estimate not below 0; Maps 1-2 MAE not higher) in BOTH
F-VALID (2026-05-01..07-31) and the spent period (2026-08-01..10-16), and Map 1's log-score gain point estimate is not below 0
in both (the next live events are best-of-1). International events are reported separately (evidence only). A passing stat
becomes lol-1g, refitted on nothing (K and b stay as chosen on F-TRAIN); forecasts before it keep their version; stakes
stay 0U; the forward real-line record is reported per version.

### A8 result (2026-10-20; lol/eval/event_pace_a8_2026-10-20.json) - ep-1 FAILS, nothing changes

Chosen on F-TRAIN: kills K=40, b=0.5; assists K=80, b=0.5 (F-TRAIN mean log score gain about +0.0001 / -0.00001).

| stat | period | Map 1 LS gain | Maps 1-2 LS gain (95%) | Maps 1-3 | Maps 1-2 MAE | A8 rule |
|---|---|---|---|---|---|---|
| kills | F-VALID | +0.0001 | +0.0005 (-0.0002, +0.0012) | -0.0004 | 2.989 -> 2.989 | fail |
| kills | spent | +0.0003 | +0.0008 (-0.0002, +0.0018) | -0.0001 | 3.074 -> 3.076 | fail |
| assists | F-VALID | -0.0000 | +0.0003 (-0.0004, +0.0011) | -0.0004 | 5.069 -> 5.069 | fail |
| assists | spent | +0.0007 | +0.0017 (+0.0009, +0.0026) | +0.0005 | 5.159 -> 5.150 | pass |

Decision: fail for both stats (A8 needs both periods). International events alone show larger gains (kills Maps 1-2
F-VALID +0.0075 on 15 series) - evidence only, a selected subset, not promoted. lol-1f stays.

**Other checks on the same day (evidence only, no change).**
- lol-1f at book-style lines (evaluate.dev_line), out of its fit (Aug 1 - Oct 16): kills graded picks printed 67.5%, won
  67.2% (16,840 picks, 1,363 series); assists printed 67.8%, won 65.6% - assists Unders 68.5% printed / 64.6% won, the same
  pattern in F-VALID (68.6% / 64.2%). Book-style lines are a proxy; real lines are sharper (the CS2 record).
- Forward (2 Worlds Play-In best-of-5s, lol-1): the model's side when the platform offered it went 11-26, when it did not
  27-14. CS2's 521 P1 lines with saved offered sides do not show over-only lines shading the Over (Over hit 56.6%, 95%
  46.7-67.0%, 46 matches), so no rule is built on it. The LoL page already shows "No pick" when the model's side is not offered.

## Amendment A9 (2026-10-20, written before any candidate number): assists level (lr-1) - owner-chosen fix

**Owner decision (2026-10-20, answer "1").** Fix the assists Under gap as a tested change, released as a new model version
only if it passes, accepting that assists Under chances may print lower. This is the owner's explicit exception to the
"never fix calibration by lowering percentages" rule, for this change only.

**Evidence that motivates it (recorded in A8's checks).** At book-style lines, out of lol-1f's fit, assists Unders print
about 4 points above what they win and assists Overs about 1.7 below, in both F-VALID and the spent period - the signature
of an assists level that runs low; lol-1f's assists bias drifts from about +2% (Jan-Apr) to about -2% / -3% (May-Oct).

**Candidate lr-1 (assists only).** For a target on day d in league L:
- residual units = every earlier assists target's game-1 total (scope Map 1, all leagues) on days in [d - W, d), with lol-1f's
  projection mu and the actual y;
- global ratio c_G = sum y / sum mu over all of them (1 if none);
- league ratio c_L = (sum y_L + K * mbar_L * c_G) / (sum mu_L + K * mbar_L), mbar_L = mean mu_L (c_G if L has none);
- projection = lol-1f mean x c_L (every scope; the NB size unchanged).
- W in {14, 30, 60} days, K in {10, 40, 160} units, chosen on F-TRAIN by mean NB log score over all scopes.

**Decision (fixed now).** lr-1 replaces lol-1f for assists only if, in BOTH F-VALID and the spent period:
1. A7's rule holds (Maps 1-2 log-score gain 95% series-grouped interval above 0; Maps 1-3 point estimate not below 0; Maps 1-2
   MAE not higher);
2. Map 1's log-score gain point estimate is not below 0;
3. at book-style lines (evaluate.dev_line), the assists Under gap |printed - won| is smaller than lol-1f's.
Kills is reported with the same candidate as evidence only (kills is calibrated; no kills change in this amendment).
A pass becomes model version lol-1g (kills and deaths unchanged); earlier forecasts keep their version; stakes stay 0U;
the forward record is reported per version.
