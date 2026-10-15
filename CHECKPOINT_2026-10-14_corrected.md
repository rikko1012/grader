# Evaluation checkpoint 2026-10-14 - corrected (2026-10-15)

The first version (CHECKPOINT_2026-10-14.md) is kept unchanged beside this file. This version is about predictive
performance only. Passing tests and the correctness releases say nothing about whether the grades are accurate.

**Conventions.**
- Every interval is 95% from 2,000 resamples of canonical matches, so all the lines of one match move together.
- **One direction everywhere:** a gain or difference is baseline minus candidate of a loss (log-loss, Brier, absolute
  error). **Positive means the candidate is better:** ours vs 50%, our projection vs the book's line, or a challenger
  vs live.
- Rate gaps are happened minus printed, so negative means the printed chance was too high.

## Completion status

| item | status |
|---|---|
| Correctness deployments | `23bea2fe37` live 2026-10-14 22:53:13 UTC (audit fixes + N0 pin n0-r3); `6b6a43f2a6` 00:38:11 UTC (n0-r4); evaluation pipeline 0fafa17 01:02:42 UTC and bc5bef5 (no hashed file: tag unchanged); `a310d76b55` 01:11 UTC (release session: Thunderpick outage repairs) |
| Forecast log recovered | **complete**: ids 1-26,575 through the new bounded `/admin/forecasts_page.json` (fixed cutoff 26,575, 67 pages of 400 with inputs, no gap or duplicate; the 6,685 rows already held match exactly). Production: no restart, 0 5xx, memory 378 to 921 MB (warm-up). Every Render snapshot is now exact |
| Railway records | **not recoverable here**: Railway is shut down; its forecast log exists only to 2026-10-08 23:24 UTC and its picks export stops 2026-10-10 16:42. Railway Oct 9-10 snapshots are a separate SUPPLEMENTAL analysis, never pooled. Railway's volume (project "thriving-insight") would be the only source if the owner restores it |
| Settlement faults (7) | **cause found and fixed in code** (EVALUATION.md section 9). HLTV 2399144 confirms the settled totals (BRACE 23 + 15 = 38, yourwombat 15 + 14 = 29); the stored detail was BO3.gg's full scoreboard. The repair runs on the settle pass (it was first in rotation after the restart). Not acknowledged to unlock the counter. Production confirmation: see "Learning loop" below |
| Learning loop | see "Learning loop" at the end (filled from production after the pass) |
| Product scorecard | protocol amendment P1 (EVALUATION.md section 8), live on the learning dashboard; the registered analysis is unchanged |
| Member wording | chances labelled "model estimate"; the stake scale explained as relative units, not a bankroll plan; no grade, chance or stake changed |

## 1. Corrections to the first version

1. **About half the registered snapshots were reconstructed.** Now every Render snapshot is exact (the full log was
   recovered). Railway Oct 9-10 is supplemental. Even exact, the board-only result is no better (section 3).
2. **"x1.2 spread improves projection accuracy" was wrong.** A spread never moves a point projection; its own table
   showed 0 projection change. It changes probabilities. On exact registered snapshots the pre-freeze spread is
   slightly worse: log-loss -0.0013 (-0.0030 to +0.0002), Brier -0.0008 (-0.0016 to -0.0001). That is preliminary
   favourable evidence for the live spread's probabilities, not a passed gate. On book lines the comparison changes no
   forecast at all: perf-2 lines don't use it.
3. **The shrinkage (lb-001) verdict leaned on member-entered lines.** On exact book lines it is neutral: +0.0011
   (-0.0180 to +0.0182), 21 matches. On exact registered snapshots it is -0.0097 (-0.0301 to +0.0094), 27 matches.
   **Inconclusive, not demonstrated harmful.**
4. **"BO3.gg fallback supported" was too strong.** On exact snapshots, removing the fallback makes projections worse by
   kills 0.032 (-0.008 to +0.073) and HS 0.027 (-0.007 to +0.072), on 8 changed matches. The intervals cross 0:
   preliminary favourable, not supported.
5. **rr-1 had no exact snapshots.** Its matches were all in the missing window, so its first figures were
   provisional. It now has exact snapshots (section 6).
6. **Thunderpick does not affect projected totals.** It changes market-quality stake caps and the shown win chance. The
   integration of market information into projections (rr-1) is a separate shadow candidate, unfinished (section 8).
7. **Count differences:**
   - The first version's 1,375 vs 1,365 scored units: 10 units landed exactly on the snapshot's line (pushes); the
     published view had 5.
   - 685 vs 680 exact units: 5 of those pushes.
   - Over + Under = 790 vs 822 board units: 32 board forecasts printed at exactly 50.0% (mostly 0U Leans of
     `beb4b5ba0c`) have no favoured side.
   - The corrected counts are reconciled in section 2.

## 2. Data, cohorts and count reconciliation

- **Units:** player x canonical match x stat x scope.
- **MAIN cohort:** every snapshot exact. That means Render Oct 11-14, plus Railway units whose cutoff falls before its
  log ends (Oct 7 21:11 to Oct 8).
- **SUPPLEMENTAL:** Railway Oct 9-10, reported only in section 7.
- **Excluded before units are formed:** 18 impossible Railway member lines (server PLAUSIBLE_MAX rule).

Views (one row per unit, never mixed):
- **P1:** latest standard board forecast 30+ min before start, with book, feed market id and player id.
- **R:** the registered snapshot, any source.
- **BU:** board line without feed ids (Railway's board rows predate those columns; reported apart, never in P1).
- **MEM:** member-entered lines.
- **ADJ:** goblin / demon lines.

| view (MAIN) | units | push on its line | scored | ties at exactly 50% | favoured Over | favoured Under | matches |
|---|---:|---:|---:|---:|---:|---:|---:|
| **P1 standard book lines** | 638 | 3 | 635 | 1 | 347 | 287 | 36 |
| R registered (any source) | 987 | 9 | 978 | 62 | 479 | 437 | 67 |
| MEM member-entered | 669 | 15 | 654 | 48 | 297 | 309 | 65 |
| ADJ adjusted (goblin / demon) | 24 | 0 | 24 | 0 | 14 | 10 | 3 |

Over + Under + ties = scored; calibration bands sum to scored minus ties (P1: 634; R: 916).

## 3. Product scorecard P1 - standard book lines (MAIN: 635 units / 36 matches, all Render Oct 11-14)

**1 · Projection error**
- **Kills** (325 / 36 m): MAE 5.57. Bias (projection - actual) +0.56 (-0.73 to +1.73). Vs the book line:
  -0.220 kills (-0.430 to +0.004): the book's line was closer, borderline.
- **Headshots** (310 / 36 m): MAE 3.72. Bias +0.40 (-0.31 to +1.06). Vs the line: -0.046 (-0.185 to +0.113).

**2 · Probability quality and calibration**
- Log-loss 0.7077 vs 0.6931 at a flat 50%: **gain vs 50% -0.0145 (-0.0332 to +0.0059)**.
- Brier 0.2570: gain vs 0.25 is -0.0070 (-0.0160 to +0.0028).
- **Calibration slope 0.09 (-0.47 to 0.63):** the printed chances do not separate winners from losers.

| favoured-side band | units | matches | printed | happened | happened - printed |
|---|---:|---:|---:|---:|---|
| 50-55% | 281 | 36 | 52.5% | 43.8% | -16.2 to -1.5 pts |
| 55-60% | 202 | 35 | 57.4% | 52.5% | -13.1 to +2.8 |
| 60-65% | 85 | 32 | 62.3% | 44.7% | -29.4 to -5.7 |
| 65-70% | 46 | 19 | 67.2% | 54.3% | -30.7 to +3.9 |
| 70-75% | 17 | 13 | 71.3% | 58.8% | -40.0 to +12.1 |
| 75%+ | 3 | 3 | 75.3% | 66.7% | |

**3 · Side selection**
- Favoured Over: 347 / 34 m, printed 57.6%, happened 47.0% (-20.2 to -0.5 pts).
- Favoured Under: 287 / 35 m, printed 56.3%, happened 49.1% (-14.1 to -0.2 pts).
- Both: printed 57.1%, happened 47.9% (-14.7 to -3.7 pts).
- 1 tie at exactly 50%.

**4 · Staking** (the grader's stake scale; no payout exists for a single prop)

| stake | units | matches | printed | happened | happened - printed |
|---|---:|---:|---:|---:|---|
| 0U (Lean) | 333 | 36 | 53.9% | 44.4% | -16.6 to -2.7 pts |
| 0.5U | 234 | 36 | 58.7% | 51.7% | -14.2 to +0.2 |
| 1U | 45 | 17 | 65.1% | 51.1% | -27.2 to +0.9 |
| 1.5U | 21 | 13 | 70.4% | 57.1% | -42.2 to +11.3 |
| 2U | 1 | 1 | 75.4% | 0% | |

Stake-weighted, all staked lines: printed 62.2%, happened 51.9%. These results do not validate the stake scale as a
bankroll allocation.

## 4. P1 by model version, history revision and stat (no pooling across versions)

| version | history | stat | units | matches | printed / happened | gain vs 50% |
|---|---|---|---:|---:|---|---|
| a64083e3c2 (G8.5e) | N0 | kills | 44 | 5 | 57.4% / 52.3% | +0.012 (-0.021 to +0.049) |
| a64083e3c2 | N0 | HS | 43 | 5 | 55.9% / 55.8% | +0.028 (-0.020 to +0.076) |
| 313626ab10 (G8.5f) | N0 | kills | 24 | 4 | 59.3% / 29.2% | -0.155 (-0.379 to -0.016) |
| 313626ab10 | N0 | HS | 22 | 3 | 56.9% / 31.8% | -0.053 (-0.148 to +0.023) |
| 0d6b117256 (G8.5h) | N0 | kills / HS | 5 / 5 | 1 / 1 | 64.9% / 20.0%; 61.6% / 40.0% | (one match) |
| c80ac64bc8 (G8.6) | N0 | kills | 46 | 8 | 56.8% / 32.6% | -0.069 (-0.132 to -0.009) |
| c80ac64bc8 | N0 | HS | 43 | 8 | 56.6% / 37.2% | -0.029 (-0.070 to +0.029) |
| 1088faf157 (G8.6) | N0 | kills | 40 | 6 | 56.6% / 52.5% | -0.012 (-0.042 to +0.036) |
| 1088faf157 | N0 | HS | 38 | 8 | 57.2% / 45.9% | -0.022 (-0.055 to +0.054) |
| 1088faf157 | partial | kills | 13 | 3 | 55.2% / 53.8% | +0.021 |
| 1088faf157 | N | kills / HS | 10 / 20 | 3 / 3 | 56.0% / 60.0%; 55.1% / 40.0% | +0.069; +0.004 |
| **6f6e2d39d7 (G8.8)** | N | kills | 143 | 15 | 57.1% / 51.0% | -0.005 (-0.037 to +0.030) |
| **6f6e2d39d7** | N | HS | 138 | 15 | 57.3% / 55.8% | +0.009 (-0.020 to +0.042) |

- **6f6e2d39d7, both stats:** 281 / 15 matches, printed 57.2%, happened 53.4% (-11.8 to +3.5 pts); gain vs 50%
  +0.002 (-0.025 to +0.032). Not distinguishable from 50% on 15 matches, and not demonstrably worse than printed either.
- **No settled book line yet** for the versions after it (`23bea2fe37`, `6b6a43f2a6`, `a310d76b55`).
- **The pooled weakness is concentrated in Oct 11-12** (313626ab10 / 0d6b117256 / c80ac64bc8: 145 units / 10 matches,
  printed 57.6%, happened 33.1%). Ten matches cannot separate a version effect from a bad slate. The current version's
  performance cannot be inferred from the pooled figure.
- **Old vs backfilled history:** N0 311 / 20 m, gain -0.035 (-0.062 to -0.008); partial + N 324 / 18 m, +0.005. Those
  are different days and versions, so the difference can't be attributed to the history.

## 5. Registered analysis R (any source; preserved, now exact - 978 units / 67 matches)

- **Gain vs 50%:** +0.0137 (-0.0058 to +0.0328).
- **Calibration slope:** 0.58 (0.39 to 0.86), too confident.
- **Favoured side:** printed 58.7%, happened 50.0% (-13.5 to -4.0 pts).
- **Projection vs line:** kills +0.153 (-0.172 to +0.533); HS +0.541 (+0.242 to +0.866).
- **75%+ band:** 60 units printed 87.6% hit 98.3%. Those are member-typed off-market lines (members:
  654 / 65 m, gain vs 50% +0.059).

## 6. Live vs each challenger on the same exact book-line forecasts (P1)

Eligible = a forward forecast where the comparison has a value. Changed = it differs from live.

| comparison | eligible units / matches | changed units / matches | log-loss diff | Brier diff | kills abs-err diff | HS abs-err diff |
|---|---|---|---|---|---|---|
| lb-001 (shrink toward 50%) | 349 / 21 | 346 / 21 | +0.0011 (-0.0180 to +0.0182) | +0.0005 | n/a | n/a |
| rr-1 (market-informed projection) | 275 / 15 | 257 / 15 | +0.0025 (-0.0097 to +0.0147) | +0.0013 | +0.041 (-0.162 to +0.248) | -0.008 (-0.100 to +0.091) |
| match_m1 (shared-match model) | 615 / 35 | 615 / 35 | +0.0033 (-0.0074 to +0.0138) | +0.0016 | +0.025 (-0.067 to +0.116) | +0.006 |
| perf_map1 (Map 1 candidate) | 635 / 36 | 20 / 1 | +0.0003 | +0.0001 | +0.010 | -0.001 |
| prior_smooth | INVALID | | equals the legacy pipeline (shadow bug; fixed in `23bea2fe37`, new clock from then) | | | |
| online_taper | 635 / 36 | 0 / 0 | 0 | 0 | 0 | 0 |
| perf-1 (perf1_g7) vs live perf-2 | 635 / 36 | 614 / 36 | -0.0027 (-0.0091 to +0.0035) | -0.0012 | **-0.072 (-0.141 to 0.000)** | -0.008 |
| legacy pipeline vs live | 635 / 36 | 635 / 36 | +0.0108 (-0.0073 to +0.0285) | +0.0051 | -0.021 (-0.240 to +0.215) | -0.037 |
| no BO3.gg fallback | 635 / 36 | 38 / 6 | +0.0023 (-0.0011 to +0.0067) | +0.0011 | -0.018 (-0.050 to +0.015) | -0.017 |
| perf-1 normal HS curve | 635 / 36 | 310 / 36 | -0.0012 (-0.0039 to +0.0016) | -0.0006 | 0 | 0 |
| old_spread / online_none / no_rest_bonus / pre_fix_v2 | 635 / 36 | 0 / 0 | 0 | 0 | 0 | 0 |

On exact registered snapshots (R), the paired results are in the same direction and within noise: lb-001 -0.0097,
rr-1 +0.0023 (17 m), match_m1 +0.0072 (-0.0035 to +0.0184, 42 m), and perf-1's kills projection worse by 0.067
(-0.130 to -0.001).

## 7. Diagnosis: why higher printed chances do not separate winners from losers (P1, descriptive, nothing fitted)

- **(a) Spread is not too narrow.**
  - Kills: z = (actual - projection) / predictive sd has sd 0.91, E[z^2] 0.69-0.98, and 71% fall inside +-1 sd
    (68% expected).
  - Headshots: sd 0.95, 71% inside +-1 sd.
  - If anything, kills are slightly too wide. Overconfidence does not come from the width of the distribution.
- **(b) The mean is roughly right on average.**
  - Kills Maps 1-2: predicted rounds 43.5 vs actual 42.2 (-0.4 to +3.0); kills per round predicted 0.661 vs 0.676
    (-0.033 to +0.001). The two partly offset.
  - Headshots per kill: 0.521 vs 0.527.
  - Overall bias +0.56 kills, within noise.
- **(c) The model's disagreement with the book line carries no measurable information.**
  - The slope of (actual - line) on (projection - line) is kills -0.08 (-0.62 to +0.48) and headshots +0.34
    (-0.23 to +0.95). 1 would mean fully informative, 0 none.
  - Printed chances rise exactly where the projection departs from the line. Those departures are not followed by
    outcomes: for kills, at 0.25-0.5 sd away, printed 63.9% vs happened 40.7%.
  - A correct average and a correct spread can still give wrong probabilities: the chance is computed against a line
    that already contains most of the information, so the model's distance from it is mostly noise, and the grader
    turns that noise into confidence.
- **Selection rules then concentrate stakes** on exactly those departures (1U: printed 65.1%, happened 51.1%).
- **What follows (not done here):** a candidate that treats the book line as information, for example by shrinking
  the projection's departure from the line by an amount estimated on earlier matches only. It would be frozen,
  versioned and tested forward as a separate challenger. Nothing is fitted to this week's 60-65% band, and no printed
  percentage is remapped from these outcomes.

## 8. Thunderpick's role (as it is today)

- **Thunderpick odds do not affect projected totals.** perf-2 takes no market input on any scope.
- **What they do change:** the market-quality stake cap (a bookmaker price is SOFT, max 1.5U), the shown win chance,
  and the per-map chances used in the simulation scenarios.
- **Recorded per grade:** whether the odds changed the projection, probability or stake (`detail.market_effect`).
- **Market information in the projection** is only the rr-1 shadow candidate, on correctly matched, timestamped
  pre-match odds (the selected qualified market, so Thunderpick when eligible, else Polymarket). On exact book lines:
  15 matches, +0.0025 (-0.0097 to +0.0147). Nothing is inserted into live grades to claim an integration.
- Since 2026-10-14 22:53 Thunderpick quotes were mostly stale (429 backoff); `a310d76b55` changes its polling. That is
  the release session's change, outside this report.

## 9. Supplemental (approximate, never pooled)

- **Railway Oct 9-10, registered view:** 398 / 25 m, gain vs 50% +0.063 (+0.032 to +0.091). These snapshots are earlier
  forecasts standing in for the unrecoverable latest ones.
- **Railway board lines without feed ids (BU), exact:** 93 / 13 m, gain -0.037 (-0.067 to -0.011).

## 10. Verdicts

The registered rule is unchanged:
- Decisions only at **300 / 600 / 1,000 distinct forward matches**.
- A challenger needs a log-loss gain of at least 0.002 with a family-wise interval (98.3%) above 0, a calibration slope
  interval overlapping 0.8-1.2 that is at most 1.0 wide, and no major slice worse than 0.005.
- A failed window retires a challenger only if its estimate is 0 or worse.
- Passing makes it eligible for the owner's review; it never auto-promotes.
- P1 board-line results are reported beside every review; any rule built on them applies prospectively only.

Dates below are collection forecasts at about 10 scored matches a day (Oct 11-14: 6, 11, 8, 17). They say when a
window fills, not that anything will pass.

| proposed improvement | verdict | eligible matches (R / P1) | matches where it changes predictions (R / P1) | rule and next review |
|---|---|---:|---:|---|
| lb-001 shrink toward 50% | **inconclusive** (book lines neutral; registered -0.010, interval across 0) | 27 / 21 | 27 / 21 | 300-match window, filling around 2026-11-09 |
| rr-1 market-informed projection | **inconclusive** (first exact results; all on backfilled history N) | 17 / 15 | 17 / 15 | 300-match window, around 2026-11-10 |
| match_m1 shared-match model | **inconclusive - preliminary favourable** (registered +0.007, book +0.003, both across 0) | 42 / 35 | 42 / 35 | 300-match window, around 2026-11-06 |
| perf_map1 Map 1 candidate | **inconclusive** (differs from live on 3 / 1 matches) | 42 / 36 | 3 / 1 | too few changed matches to judge; keep logging |
| prior_smooth | **not evaluable** (invalid shadow until `23bea2fe37`) | 0 valid | - | clock from 2026-10-14 22:53 UTC |
| online_taper | **inconclusive** (changes 4 / 0 matches) | 63 / 36 | 4 / 0 | 300-match window |
| live spread x1.2 vs pre-freeze | **preliminary favourable for probabilities** (Brier interval excludes 0; log-loss does not; no projection effect) | 66 / 36 | 24 / 0 | not a promotion question; recheck at 300 |
| BO3.gg fallback (live) vs none | **preliminary favourable for projections** (intervals cross 0) | 42 / 36 | 8 / 6 | recheck at 300 |
| perf-2 (live) vs perf-1 | **preliminary favourable for kills projections** (-0.067 / -0.072, interval touching 0); probabilities level | 42 / 36 | 42 / 36 | recheck at 300 |
| repaired history N for perf input | **not supported for activation**; only the 23-match retrospective replay, inconclusive | - | - | needs the N0-pinned release forward plus an opponent-strength candidate |
| Thunderpick as the market input | **no effect on projections by design**; stake caps and shown chance only | - | - | correctness question |

**Bottom line.** On the lines the books offer, the live grader's printed chances run about 9 points above what
happened (MAIN P1: 57.1% printed, 47.9% happened), and they do not rank winners above losers. Its projections are
about as close to the result as the book's line, and slightly further for kills. No candidate has demonstrated an
improvement yet. The diagnosis points at how the projection's distance from the line becomes confidence (section 7), not at
the spread or the average.

## Learning loop

(Filled from production once the settle pass has run.)

Scripts (read-only, local): session scratchpad ck/fetch_pages.py (bounded extraction + manifest), ck/build2.py,
ck/report2.py, ck/extra2.py; settle/hltv_2399144.py (HLTV records of the faulted match).
