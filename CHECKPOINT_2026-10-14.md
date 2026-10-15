# Evaluation checkpoint - 2026-10-14 (agreed 2026-10-07)

This report is about predictive performance only. It is separate from the correctness release
(`release/audit-2026-10-14`): passing tests says nothing about whether the grades are accurate. Nothing here changed a
grade. Every interval is 95% and comes from 2,000 resamples of canonical matches, so all the lines of one match move
together. That clustering is the correction for repeated player lines. Log-loss and Brier are scored on
P(Over | no push) as published; pushes are left out. A "gain" is baseline minus ours, so positive means ours (or the
challenger) did better. One week, or any fixed number of prop rows, decides nothing by itself; the decision rules are
the ones already registered (EVALUATION.md, learning.REVIEW_RULE).

## Answer first

**Has the grader's accuracy or probability calibration improved? Not demonstrated.** On the lines the books actually
offered (board lines, 822 units / 69 matches, Oct 7-14):

- The published chances do not beat a flat 50%: log-loss gain vs 50% is **-0.0085 (-0.0234 to +0.0073)**.
- They are overconfident. The favoured side was printed at 56.6% on average and came in 51.1% of the time
  (gap -9.8 to -0.8 points). The calibration slope is 0.19 (-0.40 to 0.78), so the chances barely separate winners
  from losers.
- **Kills:** the projection was further from the result than the book's line by 0.19 kills (-0.36 to -0.02), and
  log-loss vs 50% is -0.017 (-0.035 to +0.001).
- **Headshots:** level with the line (projection vs line -0.002; log-loss vs 50% +0.001).
- **Staked board lines:** printed 59.9% vs actual 51.8%. The 0.60-0.65 band was printed 62.3% and hit 44.7%
  (gap -28.6 to -6.3 points).
- **Over time (board lines):** gain vs 50% was -0.037 on Oct 7-8, +0.016 on Oct 9-10, -0.028 on Oct 11-12 and
  +0.005 on Oct 13-14, every interval crossing 0 except Oct 7-8. That is no steady trend. The latest period is
  confounded with the unintended backfill (below), the odds-source switch and the slate mix, so it cannot be credited
  to any model change.

On the registered snapshot, which takes all sources including lines members typed in, the numbers look better:
gain vs 50% +0.037 (+0.020 to +0.052); printed 60.0% / actual 54.3%. That gain comes from member-typed lines far from
the market. In the 0.75+ band, 126 units were printed at 87.7% and hit 96.8%. Those lines are easy and say nothing
about beating a book. **Use the board-line figures to judge the grader.**

No candidate is supported for promotion on forward evidence. Two live design choices are supported on projection
error: the BO3.gg fallback and the x1.2 spread. Everything else is inconclusive at 17-91 distinct matches, against
review windows of 300 / 600 / 1,000.

## 1. Data and its limits

| source | period (match start) | units | matches | forecast log available |
|---|---|---:|---:|---|
| Railway (shut down) | 2026-10-07 21:11 to 2026-10-11 04:56 UTC | 703 | 50 | forecasts ids 1-5102 (to Oct 8 23:24); picks export of Oct 10 16:42 (later Railway settlements not in it) |
| Render | 2026-10-11 04:56 to 2026-10-14 18:00 UTC | 672 | 42 | ids 1-6685 (to Oct 13 17:56) + ids 19798-24797 (from Oct 14 17:36) |
| **total** | | **1,375** | **92** | |

- **Unit** = player x canonical match x stat x scope (learning.unit_key / identity.assign). Excluded:
  - 79 impossible member lines, under the server's PLAUSIBLE_MAX rule (e.g. a 2,475-kill line);
  - 2 units in the 7 picks that fail the settlement audit (Fruju vs MARKandLARRY);
  - 20 units with no forecast 30 minutes or more before the start;
  - pushes and voids;
  - owner test rows.
- **Three forecast choices, never mixed:**
  - **E1, original published forecast:** the unit's first published forecast, 30+ minutes before start.
  - **E2, registered snapshot (EVALUATION.md section 1):** the latest forecast 30+ minutes before start, any source.
  - **E3, board lines only:** the same rule restricted to the book's own offered lines.
- **Missing forecast log (Render ids 6686-19797, Oct 13 17:56 to Oct 14 17:36 UTC).** Today a full
  `/admin/forecasts.json` export (~95 MB) knocked the production instance over: it restarted at 22:26:38 UTC, then
  ran normally. The endpoint has no paging, so this window could not be fetched safely. Railway also has no forecast
  log after Oct 8 23:24. Where the true snapshot may lie in a missing window, the latest *known* earlier forecast is
  used and the unit is labelled approximate. Exact snapshots: E2 685 of 1,375 units (49 matches); E3 436 units
  (36 matches). The exact-only tables are in the appendix; they point the same way, and for board lines they are worse
  (gain vs 50% -0.025, -0.046 to -0.004).
- **The learning dashboard has been paused since 2026-10-13 00:53** (settlement-audit fault), so its own scorecard
  stops at 16 matches. These tables are an independent reconstruction from the exports, using the same rules.

## 2. Settled matches and forecasts per live version and history revision

History revision is taken from the forecast time:
- **N0:** before 2026-10-13 00:49 UTC.
- **partial:** 00:49-04:26:15 UTC, while the backfill loaded.
- **N:** after that, with the full repaired history live (unintended).

Counts by the version of the first published forecast. Railway and Render combined:

| version | grading group | history | settled pick rows | units | matches |
|---|---|---|---:|---:|---:|
| 9abfe8cf5f / e4a0a0d278 / 11edec8d68 | G3 | N0 | 177 | 96 | 13 |
| beb4b5ba0c | G5 (online guard) | N0 | 755 | 408 | 31 |
| 4a9d15b1fa / 2391c7f756 / 25e9e825c6 | G6 perf-1 | N0 | 53 | 50 | 5 |
| 457dc4bfab | G7 | N0 | 7 | 7 | 5 |
| 4527c7974b / 83b2b67d6b | G8 / G8.1 perf-2 | N0 | 523 | 293 | 22 |
| 1118ff120e / 405653024c / 757f51be04 / aedb7b7446 | G8.2 | N0 | 37 | 31 | 6 |
| c997d119be | G8.4 | N0 | 122 | 57 | 4 |
| 6f26c18ce4 ... 694348fd49 (70a8247c14, fa0ff604d2, a64083e3c2, af09e740c8, fe063dc2e9) | G8.5x | N0 | 541 | 228 | 15 |
| 313626ab10 / 0d6b117256 | G8.5f-h | N0 | 440 | 173 | 13 |
| c80ac64bc8 / 1088faf157 | G8.6 | N0 | 367 | 229 | 20 |
| 1088faf157 | G8.6 | partial | 116 | 73 | 11 |
| 1088faf157 / acbe039647 | G8.6 | N | 143 | 82 | 10 |
| 822a6b4505 / 17e5e85837 | G8.7 tp-first-1 | N | 270 | 165 | 10 |
| 6f6e2d39d7 | G8.8 tp-first-2 | N | 640 | 249 | 18 |
| untagged / d408f04949 | pre-freeze / G1b | N0 | 53 | 37 | 5 |

(Units are distinct within a row; matches overlap between rows, since one match is forecast under several versions.)

Board lines (E3) by version with enough rows, gain vs 50%:

| version | units | matches | gain vs 50% |
|---|---:|---:|---|
| beb4b5ba0c (G5) | 95 | 19 | -0.003 (-0.024 to +0.021) |
| 83b2b67d6b (G8.1) | 70 | 15 | +0.013 (-0.036 to +0.051) |
| a64083e3c2 (G8.5e) | 91 | 8 | +0.018 (-0.027 to +0.061) |
| 313626ab10 (G8.5f) | 45 | 6 | -0.094 (-0.195 to -0.028) |
| c80ac64bc8 (G8.6) | 72 | 9 | -0.043 (-0.057 to -0.012) |
| 1088faf157 (G8.6), N0 / partial / N | 63 / 22 / 28 | 8 / 4 / 4 | -0.015 / +0.012 / +0.026 |
| 17e5e85837 (G8.7, N) | 105 | 8 | +0.003 (-0.031 to +0.042) |
| 6f6e2d39d7 (G8.8, N) | 161 | 15 | +0.009 (-0.024 to +0.045) |

No version has enough matches to be judged on its own. Within one grading group the tags pool only as listed in
MODEL_VERSIONS.md.

## 3. Live model: actual vs printed

Views: E3 = board lines (main view); E2 = registered snapshot (all sources); E1 = original published forecast.

| slice | view | units / matches | printed / actual hit (favoured side) | log-loss | gain vs 50% | Brier | projection vs line (abs-error gain) |
|---|---|---|---|---:|---|---:|---|
| all | E3 board | 822 / 69 | 56.6% / 51.1% | 0.7017 | -0.0085 (-0.0234, +0.0073) | 0.2541 | -0.100 (-0.224, +0.034) |
| kills Maps 1-2 | E3 | 426 / 68 | 56.5% / 48.0% | 0.7099 | -0.0167 (-0.0346, +0.0005) | 0.2581 | -0.182 kills (-0.351, -0.025) |
| HS Maps 1-2 | E3 | 376 / 67 | 56.6% / 54.7% | 0.6925 | +0.0007 (-0.0189, +0.0195) | 0.2497 | -0.005 HS (-0.139, +0.132) |
| Maps 1-3 | E3 | 20 / 1 | 56.8% / 50.0% | 0.6998 | | | (one match) |
| favoured Over | E3 | 395 / 64 | 56.7% / 52.2% | 0.7061 | -0.0129 (-0.0373, +0.0128) | 0.2562 | |
| favoured Under | E3 | 395 / 65 | 56.5% / 50.1% | 0.6979 | -0.0048 (-0.0237, +0.0155) | 0.2523 | |
| staked (> 0U) | E3 | 367 / 64 | 59.9% / 51.8% | 0.7113 | -0.0182 (-0.0458, +0.0102) | 0.2587 | -0.219 (-0.448, +0.016) |
| Lean (0U) | E3 | 455 / 68 | 53.7% / 50.6% | 0.6939 | -0.0007 (-0.0129, +0.0113) | 0.2504 | |
| all | E2 snapshot | 1,365 / 92 | 60.0% / 54.3% | 0.6566 | +0.0366 (+0.0199, +0.0521) | 0.2359 | +0.690 (+0.446, +0.923) |
| kills | E2 | 694 / 86 | 58.5% / 52.5% | 0.6741 | +0.0190 (-0.0001, +0.0370) | 0.2417 | +0.392 |
| headshots | E2 | 671 / 89 | 61.5% / 56.3% | 0.6384 | +0.0547 (+0.0324, +0.0764) | 0.2299 | +0.998 |
| Map 1 | E2 | 9 / 4 | 69.1% / 88.9% | | | | (four matches) |
| all | E1 published | 1,370 / 92 | 57.1% / 49.2% | 0.7011 | -0.0079 (-0.0212, +0.0049) | 0.2544 | -0.093 (-0.227, +0.035) |

- **Map 1 and Maps 1-3** have 4 and 3 matches (E2): no conclusion is possible for those scopes.
- **E1 vs E2:** the first published forecast, often a day ahead, scores at coin-flip level. The later snapshot scores
  better on all lines, but that gap is mostly the member-line effect above.

Calibration, favoured-side chance band (E3 board lines; E2 in brackets):

| band | units (E3) | matches | printed | actual | actual - printed (95%) |
|---|---:|---:|---:|---:|---|
| 50-55% | 367 | 67 | 52.5% | 49.6% | -8.3 to +2.9 pts  [E2 46.6%, -10.9 to -0.8] |
| 55-60% | 259 | 64 | 57.3% | 54.8% | -8.7 to +4.1  [E2 52.6%] |
| 60-65% | 103 | 38 | 62.3% | 44.7% | **-28.6 to -6.3**  [E2 49.5%, -19.8 to -5.7] |
| 65-70% | 46 | 23 | 67.3% | 54.3% | -30.0 to +3.0  [E2 50.7%, -27.7 to -3.5] |
| 70-75% | 13 | 10 | 71.2% | 53.8% | -44.7 to +12.4 |
| 75%+ | 2 | 2 | 78.2% | 100% | (E2: 126 units at 87.7% hit 96.8% - member off-market lines) |

Overconfidence is concentrated in the 60-70% chances, which is where most stakes are placed. Per the standing rule,
the fix belongs in the projections, not in lowering the printed percentages.

## 4. Live vs each logged challenger, on the same forecasts

Paired on identical forecasts. "Forward" means the forecast was logged after the comparison existed and the match
started after that.

| comparison | forward from | E2 units / matches | changed (matches) | E2 log-loss gain | E3 board: units / matches, gain | projection abs-error gain (E2, by match) |
|---|---|---|---|---|---|---|
| **lb-001** shrink toward 50% | 2026-10-12 15:49 | 385 / 27 | 381 (27) | **-0.0240 (-0.0416, -0.0054)** | 342 / 23: -0.0003 (-0.0166, +0.0147) | n/a (probability only) |
| **rr-1** market rounds | 2026-10-13 17:51 | 233 / 17 | 226 (17) | -0.0023 (-0.0163, +0.0109) | 158 / 15: -0.0043 (-0.0222, +0.0129) | kills -0.006 (-0.243, +0.235); HS -0.019 (-0.118, +0.072) |
| **match_m1** shared match | 2026-10-09 19:15 | 715 / 51 | 715 (51) | +0.0089 (-0.0017, +0.0191) | 595 / 42: +0.0020 (-0.0092, +0.0125) | kills +0.035 (-0.048, +0.118); HS +0.003 |
| perf_map1 (Map 1 candidate) | 2026-10-08 22:42 | 1,058 / 67 | 31 (6) | -0.0007 (-0.0015, -0.0001) | +0.0003 | kills +0.032 (0.000, +0.079) |
| prior_smooth | 2026-10-09 | INVALID | | equals the legacy pipeline's output on every exact snapshot (shadow bug; fix in the undeployed release) | | |
| online_taper | 2026-10-08 01:12 | 1,334 / 88 | 16 (5) | -0.0001 (-0.0004, +0.0002) | +0.0001 | ~0 |
| perf-1 (perf1_g7) vs live perf-2 | 2026-10-08 23:20 | 1,053 / 67 | 1,014 (67) | +0.0016 (-0.0030, +0.0061) | +0.0002 | perf-1 kills -0.034 (-0.092, +0.025); exact snapshots -0.107 (-0.207, -0.006) |
| old pipeline (legacy) vs perf-2 | 2026-10-08 22:10 | 1,062 / 67 | 1,054 (67) | -0.0011 (-0.0134, +0.0115) | +0.0038 | legacy kills -0.209 (-0.435, +0.001); HS **-0.434 (-0.577, -0.289)** |
| no BO3.gg fallback | 2026-10-08 22:51 | 1,058 / 67 | 103 (15) | +0.0002 | -0.0011 | **kills -0.041 (-0.084, -0.003); HS -0.032 (-0.072, -0.001)** |
| perf-1 normal HS curve vs perf2_hs | 2026-10-08 22:51 | 1,058 / 67 | 503 (65) | -0.0005 (-0.0024, +0.0015) | -0.0001 | 0 |
| old_spread (pre-x1.2) | 2026-10-07 03:12 | 1,354 / 91 | 215 (28) | -0.0009 (-0.0021, +0.0002); E1 **-0.0017 (-0.0030, -0.0007)** | -0.0004 | 0 |
| online_none (pre-G5) | 2026-10-08 01:12 | 1,334 / 88 | 204 (16) | +0.0003 (-0.0026, +0.0038) | +0.0013 | ~0 |
| no_rest_bonus | 2026-10-07 03:12 | 1,354 / 91 | 153 (17) | +0.0002 | +0.0003 | ~0 |
| pre_fix_v2 | 2026-10-08 23:20 | 1,053 / 67 | 8 (3) | 0.0000 | 0 | ~0 |

## 5. What is forward-tested, what is development, what is replay

| evidence | kind |
|---|---|
| Sections 2-4 above (forecasts logged before each match, scored afterwards) | **forward**: published forecasts and logged shadows. Each comparison only from the time it was logged |
| lb-001 walk-forward fit (Oct 3-12 board units, +0.0149) | development (fitted on those days; mostly the old pipeline) |
| rr-1 round-6 frozen test (TEST Maps 1-2 kills MAE -0.054, 996 matches) | retrospective historical test. It ran on the Oct 8 export (no backfill), but its TEST period had been used by rounds 1-5, so it is not fresh data |
| rr-1 check on 947 settled lines (19 matches) and the 8-match Thunderpick note | development (those lines were inspected before the freeze) |
| N (repaired history) vs N0, 1,163 lines / 23 matches | retrospective replay (HLTV_COVERAGE.md) |
| perf-2 held-out VALID/TEST (G8), perf2_hs, prior_smooth TRAIN fit | development |

## 6. What the unintended backfill activation affected

The backfill was live from 2026-10-13 04:26 UTC (partial from 00:49) and still is: the N0 pin is in the undeployed
release.

- **The live model's own record from Oct 13 00:49** is on unapproved repaired history:
  - E2: 344 units / 24 matches, gain vs 50% +0.063;
  - E3: 294 / 20, +0.009 (-0.013 to +0.032).
  - It is reported separately and not pooled with N0 as one model. Better-looking numbers in this period cannot be
    credited to the backfill: different matches, a different slate and a different odds source.
- **Paired live-vs-challenger comparisons are not invalidated.** Both arms are graded on the same inputs. They do
  measure the candidate on a different input distribution, so they are split by revision (appendix B). Their clocks
  are **not** restarted.
- **lb-001:** 352 of 385 forward units (24 of 27 matches) are on N. On N0 alone it has 4 matches.
- **rr-1:** every forward unit (233 / 17) is on N. Its frozen historical test used the Oct 8 export and is not
  affected.
- **match_m1, perf-1, legacy, BO3 fallback:** about a third of forward units are on N. No comparison changes sign
  significantly between N0 and N.
- **The N-vs-N0 question itself** has only the retrospective replay. It was inconclusive:
  - kills MAE -0.003 (-0.164 to +0.174);
  - Over bias up (+0.93 to +1.33);
  - log-loss worse where 20-50% of history was added: +0.020 (+0.002 to +0.037).
  - Live forecasts on N0 resume only once the pin deploys, so no forward paired comparison of N vs N0 exists.
- **Not affected:** every forecast before Oct 13 00:49 (Railway and Render Oct 11-12), and the Thunderpick A/B (a
  pure odds swap on identical inputs).

## 7. Verdict per proposed improvement

Rule (learning.REVIEW_RULE):
- Decisions are taken only at **300 / 600 / 1,000 distinct forward matches**.
- A challenger needs a log-loss gain of at least 0.002 with a family-wise interval (98.3%) above 0, a calibration slope
  interval overlapping 0.8-1.2 that is at most 1.0 wide, no major slice worse than 0.005, and a replay gap of 0.01 or
  less.
- A failed window retires a challenger only if its point estimate is 0 or worse.
- Passing makes it eligible for the owner's review; it never auto-promotes.

Recent accrual is about 12 scored matches a day (8-17 per day, Oct 12-14), so the 300-match windows fall around early
November.

| improvement | verdict | evidence | forward matches now | next review |
|---|---|---|---:|---|
| lb-001 (probability shrinkage toward 50%) | **inconclusive - current evidence against** | registered snapshot -0.024 (-0.042 to -0.005), worse; board lines -0.0003, neutral; published forecasts +0.010 (-0.014 to +0.032) | 27 | 300 matches, about 2026-11-06 |
| rr-1 (market-informed projection) | **inconclusive** | log-loss -0.002 (-0.016 to +0.011); projection ~0 with wide intervals; all on N history | 17 | 300 matches, about 2026-11-07 |
| match_m1 (shared-match model) | **inconclusive - leaning favourable** | +0.0089 (-0.0017 to +0.0191); exact snapshots +0.016 (+0.001 to +0.030) on 24 matches; board +0.002 | 51 | 300 matches, about 2026-11-04 |
| perf_map1 (Map 1 candidate) | **inconclusive (practically moot)** | differs from live on 31 units / 6 matches; -0.0007 | 6 changed | Map 1 has too few settled lines to judge (4 matches) |
| prior_smooth | **not evaluable** | production shadow equals the legacy pipeline (bug) | 0 valid | clock starts when the fixed shadow deploys |
| online_taper | **inconclusive** | 16 changed units / 5 matches; no difference | 5 changed | 300-match window |
| G5 online guard (live) vs old +6% | **inconclusive** | +0.0003 (-0.003 to +0.004) on 16 changed matches | 88 | 300-match window |
| perf-2 (live since G8) vs perf-1 | **inconclusive** for probabilities; projection leans perf-2 | log-loss +0.0016 (-0.003 to +0.006); kills MAE perf-1 worse by 0.034 (-0.092 to +0.025) | 67 | 300-match window; perf-2 stays live (it was promoted on held-out data, and forward does not show it worse) |
| perf2_hs (G7) vs normal HS curve | **inconclusive** | -0.0005 (-0.0024 to +0.0015) | 67 | 300-match window |
| BO3.gg fallback (live) vs none | **supported** (projection accuracy) | removing it makes projections worse: kills -0.041 (-0.084 to -0.003), HS -0.032 (-0.072 to -0.001); log-loss equal | 15 changed | keep; recheck at 300 |
| spread x1.2 (live) vs pre-freeze spread | **supported** (small) | old spread worse on published forecasts: -0.0017 (-0.0030 to -0.0007); snapshot -0.0009 (-0.0021 to +0.0002) | 91 | keep |
| perf-2 vs old pipeline (legacy) | **supported** (projection) | legacy HS projection worse by 0.43 (0.29 to 0.58); kills by 0.21 (-0.43 to 0.00); log-loss tie | 67 | keep |
| repaired history N for the perf input | **not supported for activation (inconclusive)** | retrospective replay only: no MAE gain, higher Over bias, worse where 20-50% was added | 23 (replay) | forward N vs N0 needs the pinned release live, plus an opponent-strength candidate (arm C) |
| Thunderpick as the market input | **no predictive effect expected or seen** | perf-2 projections take no market input; A/B on identical inputs: projections identical on 287 lines, 9 stake/grade changes (1.5U cap); board lines priced on Thunderpick: 95 units / 7 matches, +0.003 | 7 | correctness / stake-rule question, not an accuracy claim |
| rest bonus, fix_v2 | **inconclusive (no detectable effect)** | ~0 on 17 / 3 changed matches | | 300-match window |

**Nothing is supported for promotion today.** The main finding is about the live model itself. On real book lines it
is not yet better than 50%, its 60-70% chances hit about 45-55%, and its kills projection is slightly worse than the
book's line. That is the gap the projection work has to close.

## 8. Operational items found while preparing this

- **Production restart:** a full `/admin/forecasts.json` export restarted production at 22:26:38 UTC. A paged export
  (`before_id` / `after_id`) would make the missing window recoverable without that risk; it's the owner's call and
  isn't part of the current release.
- **Learning dashboard:** paused since Oct 13 00:53 on the settlement-audit fault (7 picks, Fruju vs MARKandLARRY:
  stored map detail 1 kill above the settled total). Its review counter is frozen until those are fixed or
  acknowledged.
- **Railway settlements:** results after the Oct 10 16:42 export (233 units open then) are not in this checkpoint.
  The recap's read-only re-settlement covers its own cohort only.

## Appendix A - exact snapshots only

- **E2 exact:** 680 units / 49 matches; printed 59.1% / actual 48.2%; gain vs 50% +0.0147 (-0.0095 to +0.0380).
- **E3 exact (board):** 436 / 36; printed 56.4% / actual 46.3%; gain vs 50% -0.0248 (-0.0457 to -0.0041); kills
  projection vs line -0.29 (-0.52 to -0.06).
- **Paired, exact:**
  - match_m1 +0.0158 (+0.0008 to +0.0300), 24 matches;
  - lb-001 -0.0365 (-0.0806 to +0.0024), 9 matches;
  - rr-1 has no exact snapshot (its matches fall in the missing window).

## Appendix B - paired log-loss gain by history revision (E2)

| comparison | N0: units / matches, gain | N (+partial): units / matches, gain |
|---|---|---|
| perf-1 | 700 / 44: +0.0022 (-0.0038, +0.0086) | 353 / 24: +0.0002 (-0.0073, +0.0071) |
| legacy | 709 / 44: +0.0066 (-0.0070, +0.0217) | 353 / 24: -0.0166 (-0.0386, +0.0022) |
| match_m1 | 362 / 28: +0.0108 (-0.0057, +0.0266) | 353 / 24: +0.0069 (-0.0062, +0.0191) |
| lb-001 | 33 / 4: +0.0052 (-0.0503, +0.0319) | 352 / 24: -0.0268 (-0.0468, -0.0093) |
| rr-1 | 0 | 233 / 17: -0.0023 (-0.0163, +0.0109) |
| no_bo3 | 705 / 44: +0.0002 | 353 / 24: +0.0002 |

Scripts (read-only, local exports): session scratchpad `ck/build.py` (units, E1/E2/E3, exactness), `ck/report.py`
(tables, `ck/report_tables.md`), `ck/rev_split.py`. Inputs: `ck/picks.json` and `ck/forecasts.json` (Render,
2026-10-14 ~22:30 UTC), `fc_all_release.json` (Render forecasts to Oct 13 17:56), `picks_now.json` and
`forecasts_now2.json` (Railway).
