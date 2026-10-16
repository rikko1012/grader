# Real-line archive study - every offered line we hold (2026-09-28 .. 2026-10-16)

Scripts, protocol and full outputs: `research/realline_archive_2026_10_16/`. PROTOCOL.md there was written before any
model was scored. All of it is **development evidence**: every settled day has been inspected before (the lb-001 diagnosis
covered Oct 3-12, P1 covers Oct 11-16). The one exception is each frozen candidate's own saved values after its freeze,
which stay that candidate's forward evidence. Independent confirmation of anything found here needs matches after this
study (2026-10-17 onward). Nothing live changed and nothing was deployed.

## Answer

**No candidate establishes an advantage over the offered lines, and no deployable accuracy upgrade exists on this
evidence.**

- **Data:** the full archive holds 192 distinct HLTV matches with real offered lines. 159 of them can be scored
  chronologically.
- **Kills.** The projection's disagreement with the line carries no detectable information. The offered line is the
  better point forecast. At the archive's last lines (capture time not recorded), the line's departures from our
  projection were right.
- **Headshots.** The full-history projection does carry information the closing line lacks. That information is
  reproducible across both eras, but it is too small, and the projection too biased, to produce a detectable probability
  or pick-selection gain on 159 matches.
- **Why it fails, as far as the data shows:**
  - The line tracks team strength / match context that perf-2 leaves out.
  - Our projection's independent error is about 5-8 times the line's.
  - Lines that move after our forecasts mostly move toward our projection. What drives those moves is not identified here,
    and whether they are correct is not established (section 6).
- **At the line actually available when a pick is made:** see section 6 (added 2026-10-16 after the owner's focused
  question).

## 1. Inventory and reconciliation

**Sources (read-only):**
- **Offered lines:** the scraper's `prop_grades` archive in the HLTV database. It holds the last line each platform offered
  before the start, graded against HLTV: Underdog from Oct 2, PrizePicks from Oct 11; 4,784 lines.
- **Railway pick log** (exported Oct 10 16:40 UTC) and **Railway forecast log** (Oct 7 21:11 .. Oct 8 23:24).
- **Render pick + forecast logs:** complete from Oct 11 04:56; pulled Oct 16 ~18:40.
- **HLTV history** through Oct 16, rebuilt with strictly earlier inputs per match day (the Oct 15 study extended). Map-1-only
  (BO1) matches are now included.

| Match day (UTC) | Offered lines (UD / PP) | Standard Maps 1-2 + Map 1 | Unique player props | Archive usable (lines / matches) | Our board lines (Railway / Render) | With a saved pre-match forecast | Our forecasts usable (obs / matches) | Member-entered lines | Not usable: pending / void / no HLTV match / push / ambiguous or wrong match |
|---|---|---|---|---|---|---|---|---|---|
| 2026-09-29 | 0 / 0 | 0 | 0 | 0 / 0 | 0 / 0 | 0 | 0 / 0 | 78 | 0/0/0/0 / 0 |
| 2026-09-30 | 0 / 0 | 0 | 0 | 0 / 0 | 0 / 0 | 0 | 0 / 0 | 204 | 0/0/0/0 / 0 |
| 2026-10-01 | 0 / 0 | 0 | 0 | 0 / 0 | 0 / 0 | 0 | 0 / 0 | 514 | 0/0/0/0 / 0 |
| 2026-10-02 | 18 / 0 | 13 | 13 | 5 / 1 | 0 / 0 | 0 | 0 / 0 | 577 | 0/0/0/0 / 8 |
| 2026-10-03 | 405 / 0 | 371 | 371 | 348 / 22 | 103 / 0 | 86 | 69 / 16 | 1453 | 0/2/0/0 / 19 |
| 2026-10-04 | 211 / 0 | 191 | 191 | 162 / 10 | 134 / 0 | 115 | 79 / 12 | 709 | 0/8/0/0 / 21 |
| 2026-10-05 | 153 / 0 | 133 | 133 | 124 / 8 | 57 / 0 | 49 | 41 / 8 | 823 | 0/0/0/0 / 9 |
| 2026-10-06 | 149 / 0 | 124 | 124 | 113 / 8 | 43 / 0 | 40 | 37 / 8 | 678 | 0/2/0/0 / 9 |
| 2026-10-07 | 487 / 0 | 449 | 449 | 333 / 24 | 390 / 0 | 361 | 245 / 28 | 1071 | 0/7/59/0 / 58 |
| 2026-10-08 | 305 / 0 | 286 | 278 | 220 / 15 | 196 / 0 | 179 | 95 / 13 | 684 | 2/22/27/0 / 8 |
| 2026-10-09 | 273 / 0 | 236 | 236 | 199 / 16 | 144 / 0 | 134 | 107 / 15 | 814 | 0/15/7/0 / 13 |
| 2026-10-10 | 229 / 0 | 211 | 211 | 160 / 11 | 127 / 0 | 113 | 73 / 10 | 430 | 0/12/0/0 / 39 |
| 2026-10-11 | 122 / 123 | 196 | 133 | 161 / 7 | 11 / 259 | 194 | 130 / 6 | 117 | 0/4/0/2 / 0 |
| 2026-10-12 | 157 / 115 | 257 | 175 | 170 / 9 | 0 / 525 | 499 | 196 / 9 | 234 | 11/15/10/0 / 26 |
| 2026-10-13 | 77 / 104 | 150 | 91 | 116 / 5 | 0 / 253 | 228 | 120 / 5 | 201 | 0/2/0/0 / 0 |
| 2026-10-14 | 252 / 273 | 487 | 299 | 342 / 14 | 0 / 795 | 775 | 361 / 14 | 368 | 0/18/0/6 / 24 |
| 2026-10-15 | 244 / 234 | 443 | 291 | 325 / 16 | 0 / 738 | 672 | 339 / 16 | 343 | 3/0/0/3 / 47 |
| 2026-10-16 | 276 / 349 | 573 | 348 | 352 / 16 | 0 / 953 | 925 | 410 / 18 | 368 | 100/10/0/10 / 2 |
| 2026-10-17 | 90 / 138 | 226 | 138 | 0 / 0 | 0 / 145 | 145 | 0 / 0 | 41 | 184/0/0/0 / 0 |

**Days omitted, and why:**
- **Sep 28 - Oct 1:** no offered line exists in any archive. The scraper's prop archive starts Oct 2 and our board Oct 3.
  These days have member-entered lines only, which are kept out of every real-line result.
- **Oct 2:** 13 standard Underdog lines, 5 usable (1 match). Training only.
- **Oct 3** (E1) and **Oct 3-7** (E2) are training only. A day is scored only once 300 earlier observations exist.
- **Oct 10 16:40 - Oct 11 04:56:** our forecasts are lost. The Railway log ends with the export and Render starts at
  04:56. The offered lines of those matches are in the archive.
- **Oct 16 evening - Oct 17:** 300 lines still pending at the 20:06 UTC archive read. Excluded.
- **Before Oct 7 21:11:** Railway saved no separate forecast log. The saved pre-match forecast is the pick row itself,
  used only when it was last written at least 30 min before the start (117 board rows fail that and are excluded).
  730 of Railway's 1,205 board rows were graded by pre-perf-2 pipelines.

**Merge rules (no double counting):**
- **E1 (archive):** one observation per player x HLTV match x stat x scope x distinct line.
  - The same line on both platforms counts once (cross-platform copies merged: 386).
  - Different lines on the two platforms are both kept (329 units), grouped by match.
- **E2 (our forecasts):** the latest saved board forecast at least 30 min before the start per unit x platform, at its own
  line.
  - Regrades, same-platform line moves and host migrations collapse into it (1,196 superseded rows).
  - Same-line copies across platforms count once (318).
- **Outcome:** the HLTV total. Pushes and voids are out.
- **Uncertainty:** whole matches resampled.

**Excluded for identity or settlement reasons:**
- Archive lines whose HLTV match had another opponent: 150.
- Archive lines whose match was ambiguous ("2 HLTV matches fit, took the closest by date"): 133.
- Maps 1-3 lines: 46 (no HLTV Maps 1-3 construction).
- Board rows without an identifiable HLTV player / match: 668. That includes Oct 16-17 rows whose archive lines were still
  pending.

**Reconciliation with the narrower cohorts.**
- **Research cohort (72 matches, Oct 12-16) and P1 (1,211 units / 72 matches, Oct 11-16).** Both are Render-era board
  lines only.
- **What the full archive adds:**
  - the Railway era, Oct 2-10 (about 100 matches);
  - the closing lines of units our board never graded;
  - the second platform's line where it differs.
- **Unit-by-unit check against P1:** 1,089 of P1's 1,211 units are in E2 with the identical line, estimate and outcome.
  Their 57% picks number 399 / 61 matches and hit 50.4%.
- **The other 122 P1 units** (18 matches; 44 picks, hit 47.7%) have no identifiable HLTV player / match here. P1 overall
  remains 443 / 69, 50.1%.

**Data-quality finding.** A first pass of this study linked our board picks to HLTV matches by player and start time
without checking the opponent.
- When a team plays several BO1s in one day, that picks the wrong match: 155 apparent "settlement conflicts", almost all on
  Map 1 lines.
- With the opponent required, production's settled totals agree with HLTV on every linked E2 row.
- The scraper's own archive also links 150 lines to a match with another opponent. Map-1 line settlement on multi-BO1 days
  needs the opponent check in both pipelines.

## 2. Chronological evaluation (rolling origin by match day; fit on earlier days only; match-grouped 95%)

### E1 - every offered line, last line the scraper saw (2,777 scored observations / 159 matches, Oct 4-16)

The archive records the line but not when it was captured. E1 is therefore a market benchmark with that limitation, not a
verified closing benchmark, and it never enters any forecast. The verified, decision-time comparison is section 6.

All models are rebuilt from strictly earlier HLTV days with the same live formula, so one model version covers all 15 days.
Positive gain = the model is better.

| Model | Log loss | Brier | AUC | Calibration slope | vs PROJ (log loss) | vs BASE (log loss) | vs BASE (Brier) |
|---|---|---|---|---|---|---|---|
| FLAT 50% | 0.6931 | 0.2500 | 0.500 | - | +0.0139 (+0.0035, +0.0245) | +0.0002 | +0.0001 |
| BASE (earlier-day Over rate) | 0.6934 | 0.2501 | 0.502 | 0.04 | +0.0137 | - | - |
| PROJ (live perf-2, live-pin history) | 0.7070 | 0.2564 | 0.513 | 0.11 (-0.17, 0.42) | - | **-0.0137 (-0.0249, -0.0033)** | -0.0063 (-0.0117, -0.0013) |
| FULL (perf-2, full history = fh-1) | 0.7005 | 0.2535 | 0.524 | 0.29 | +0.0066 (-0.0004, +0.0137) | -0.0071 (-0.0172, +0.0032) | -0.0034 |
| lb-001 frozen (on PROJ) | 0.6961 | 0.2514 | 0.518 | 0.15 | +0.0109 | -0.0028 (-0.0090, +0.0026) | -0.0013 |
| pl-2 frozen anchoring (on PROJ) | 0.6923 | 0.2496 | 0.527 | 1.00 (0.15, 1.78) | +0.0148 | +0.0011 (-0.0021, +0.0040) | +0.0005 (-0.0010, +0.0020) |
| CORR rolling (lc-1 form, PROJ) | 0.6947 | 0.2507 | 0.503 | 0.17 | +0.0124 | -0.0013 | -0.0006 |
| CORR rolling (FULL) | 0.6928 | 0.2498 | 0.506 | 0.46 | +0.0142 | +0.0005 (-0.0026, +0.0036) | +0.0003 |
| anchor w rolling (FULL) | 0.6930 | 0.2499 | 0.530 | 0.61 (0.10, 1.14) | +0.0141 | +0.0004 (-0.0046, +0.0056) | +0.0002 |

**Selection (favoured side >= 57%, same rows):**

| Model | Picks / matches | Hit (95%) | Hit minus BASE's estimate for the same sides (95%) |
|---|---|---|---|
| PROJ | 1,116 / 153 | 51.7% (47.4-56.3) | +0.018 (-0.025, +0.063) |
| FULL | 1,032 / 151 | 52.6% (48.1-57.5) | +0.032 (-0.012, +0.080) |
| CORR rolling (FULL) | 260 / 82 | 57.7% (50.8-64.3) | +0.046 (-0.023, +0.113) |
| anchor w rolling (FULL) | 272 / 104 | 55.5% (49.1-62.3) | +0.067 (+0.002, +0.135) |
| others | | | intervals include 0 |

- The anchor result is the only interval excluding 0. It is one of nine models and is not significant after
  multiplicity. It is recorded, not claimed.

### E2 - our saved pre-match forecasts at their own lines (1,831 scored / 106 matches, Oct 8-16)

- **Probability scores:**
  - **Live as published:** log loss 0.7015, worse than BASE by 0.0073 (-0.0208, +0.0053).
  - **pl-2 frozen:** 0.6919, the best. It beats live by +0.0095 (-0.0016, +0.0213) and BASE by +0.0022 (-0.0005,
    +0.0050). Neither is established.
  - **CORR rolling on live:** worse than BASE, -0.0034 (-0.0073, -0.0003).
  - **The rest:** intervals include 0.
- **Live's 57% picks:** 645 / 91 matches, hit 51.2%, +0.013 (-0.048, +0.069) vs BASE.
  - By era: Railway 55.7% (246 picks / 85 matches, +0.057, -0.014 to +0.129); perf-2 era 50.9% (566 / 63, +0.009).
  - No model's selection beats BASE. CORR on live is significantly worse (-0.150).

**Existing candidates on their own forward windows** (saved values; development-inspected but forward for them):

| Candidate | Forward units / matches | Log-loss gain vs live (95%) | Selection vs BASE | Source |
|---|---|---|---|---|
| lb-001 (exact transform of the saved live estimate) | 1,238 / 55 | -0.0004 (-0.0113, +0.0096) | 104 picks, -0.043 (-0.170, +0.133) | E2 |
| rr-1 (saved `market_rounds`) | 1,060 / 48 | +0.0016 (-0.0061, +0.0084) | 269 picks, +0.056 (-0.030, +0.141) | E2 |
| pl-2 | 440 / 33 | +0.0128 (-0.0056, +0.0337) | recommends nothing | production P1 export |
| fh-1 | 280 / 18 | -0.0020 (-0.0100, +0.0060) | 73 picks, -0.009 | production P1 export |
| lc-1 | 0 | - | - | frozen 10-16 19:05 |

## 3. Why the projection's disagreement with the line fails (E1, totals)

**Slope b of (actual - line) on (projection - line).** b = 1 means the projection is right where it disagrees; b = 0 means
the disagreement is pure projection error.

| Slice | Obs / matches | sd(proj - line) | b, live-pin history (95%) | b, full history (95%) | MSE proj - MSE line, live-pin (95%) |
|---|---|---|---|---|---|
| Kills Maps 1-2 | 1,434 / 155 | 1.81 | +0.11 (-0.19, +0.41) | +0.17 (-0.17, +0.51) | **+2.59 (+0.60, +4.66)** |
| HS Maps 1-2 | 1,276 / 155 | 1.25 | +0.33 (+0.05, +0.63) | **+0.59 (+0.25, +0.93)** | +0.54 (-0.37, +1.46) |
| Kills, history < 600 rounds | 305 / 64 | 2.53 | -0.17 | -0.22 | **+8.62 (+2.38, +14.11)** |
| Kills, history >= 600 rounds | 1,129 / 136 | 1.56 | +0.31 (-0.10, +0.70) | +0.26 | +0.96 (-0.93, +2.93) |
| Kills, online | 737 / 82 | 1.96 | -0.05 | -0.24 | +4.24 (+1.16, +7.27) |
| Kills, LAN | 697 / 73 | 1.63 | +0.32 (-0.20, +0.80) | +0.34 | +0.84 (-1.80, +3.37) |

**What this shows:**
1. **Kills: the disagreement is about 90% projection error.**
   - Of var(proj - line) = 3.3 kills^2, the projection's own error is about 2.9 and the line's about 0.4.
   - The closing kills line is a significantly better point forecast.
   - This holds in both eras: b = +0.08 (Oct 2-10) and +0.15 (Oct 11-16).
2. **The line's departures from our projection are right.**
   - The slope of (actual - projection) on (line - projection) is 0.89 (0.58, 1.19).
   - Part of the departure tracks team strength. The line sits +0.50 kills above our projection per 100 Elo of own-team
     advantage (+0.33, +0.71), and the actual total +0.57 (+0.00, +1.11).
   - perf-2 uses a constant 21.74 rounds per map and no own-team win term.
   - The existing fix for that, rr-1 (series price + round race), passed its HLTV test. But it brings the projection
     toward what the line already prices: its forward line-relative gain is +0.0016 (n.s.).
3. **Line moves after our forecasts** (Corrected 2026-10-16, see section 6).
   - The first version of this item compared our forecast-time line with the scraper archive's last line. That archive line
     has no capture time, so it cannot serve as a verified closing line, and its "the move was right (slope 0.87)" is
     withdrawn.
   - On our own timestamped records (section 6), 30% of decision-time lines later moved, and 70% of those moved toward the
     recorded projection.
   - The move does not significantly predict the outcome at the original line: slope 0.36 (-0.11 to +0.85).
   - No cause (lineups, vetoes or anything else) is tested here. A line moving our way is agreement with a later market,
     not selection skill.
4. **Headshots are the one place with independent information.**
   - The full-history HS projection is as accurate as the closing line (MSE -0.17, -0.94 to +0.58), with b = 0.59. It is
     reproducible by era: +0.63 (+0.22, +1.10) for Oct 2-10 and +0.53 (-0.10, +1.10) for Oct 11-16.
   - Why it does not become a probability gain:
     - the projection runs high: its mean estimate is +5.1 points above the observed Over rate (+1.6, +8.9);
     - once that is corrected (CORR on FULL, HS only), the gain over BASE is +0.0025 (-0.0026, +0.0083), below what 159
       matches can detect;
     - at our forecast-time HS lines (50 scored matches) no HS model beats BASE.
5. **Thin history, online and lower tier are where the projection is worst** (MSE +4 to +9 kills^2 versus the line). These
   are places to defer to the line, not edges.
6. **The two platforms' lines.** Where they differ (329 pairs / 49 matches), outcomes follow the PrizePicks line rather
   than Underdog's (slope +1.21, 0.41 to 2.06). But a one-bet-per-pair rule on that difference hits 50.2% (43.9-56.5). It is
   not usable at 0.5-1 kill differences.

## 4. What this archive can establish (power ESTIMATES)

These are estimates. They are conditional on the assumed effect sizes and on the variability observed in these 159 scored
matches (about 17 observations per match, picks in 65% of matches). They are not universal match requirements: a
different observation density or variance changes them.

| Distinct matches | Smallest log-loss gain detectable (80% power, 5% two-sided) | Smallest selection edge vs BASE detectable |
|---|---|---|
| 159 (what we have) | about 0.0076 | about 0.09 |
| 300 | 0.0056 | 0.067 |
| 600 | 0.0040 | 0.048 |
| 1,000 | 0.0031 | 0.037 |
| 2,000 | 0.0022 | 0.026 |

- The effects the archive points to (+0.0025 log loss on HS; 3-7 points selection) are below what 159 matches can
  separate from zero.
- Under these estimates, the registered windows are not what delays a decision; the effect sizes are. The registered
  minimum gain (0.002 log loss) would need about 2,000 matches to detect with 80% power.
- A prospective sequential replacement that also allows earlier futility stops, with multiple candidates handled
  explicitly, is proposed in EVALUATION_P2_PROPOSAL.md. It is not adopted.

## 5. Decision and the specific limitations

**Qualifies for live deployment: nothing.**

| Candidate | Status |
|---|---|
| lb-001, rr-1, pl-2, fh-1, lc-1 | Unchanged and kept frozen with their forward records |
| Rolling refits of the same families | Development results only |

**Specific limitations and missing data (where further work can matter):**
1. **Team strength / match context.** The market adjusts for it and is right; perf-2 does not. rr-1 is the existing model
   of it. Its value relative to the line is unproven, because the line already contains it.
2. **Line moves after our forecasts.** They move toward our projection more often than not. This analysis does not
   identify what causes them (no lineup / veto data is tested), and does not establish that they are right (section 6).
3. **HS level bias.** The full-history HS projection's Over rate runs about 5 points high. Its information (b ~0.6) is the
   only measurable independent signal, worth one prospective HS-only test (definition in the proposal). The effect it
   implies needs several hundred to a thousand matches to confirm.
4. **Thin / online / lower tier.** The projection is unreliable there. This is a reason to withhold confidence, not a source
   of edge.
5. **The line archive keeps only the last line.** Evaluating at the line members actually see needs the platforms' opening
   lines and moves with timestamps. The scraper's `prop_lines` keeps them only for the current board.
6. **Map 1 match identity** on multi-BO1 days (section 1).

**Wording that stays true:** the grader's percentages are model estimates. Nothing here shows it beating real lines, and it
must not be advertised as proven to.

## 6. The actionable-line question (CLOSED; added 2026-10-16 21:00 UTC, commit f9a1b5d; protocol written 20:56 UTC before it ran)

Question: did the grader provide useful information at a line that was actually available when the pick was made, and
did its fixed recommendation rule benefit there? Protocol and script: `research/realline_archive_2026_10_16/
PROTOCOL_ACTIONABLE.md`, `actionable.py`, output `actionable_out.md`. Nothing is fitted; no candidate is used or changed.

**Cohort (exact).**
- **Decision-time forecast:** per player x HLTV match x stat x scope x platform, the EARLIEST saved board forecast logged at
  least 30 min before the start. Median lead time is 16.6 h before the start (25%: 11.6 h, 75%: 20.0 h).
- **Recorded values only:** the live estimate and projection as logged then, at the platform line of that board row.
  - Render forecast log: 1,606.
  - Railway forecast log: 128.
  - Railway pick row at its last write: 624.
- **Outcome:** the HLTV total at THAT original line. The corrected identity join is used (opponent must agree, including
  the HLTV record's opponent); ambiguous / other-opponent archive rows are quarantined.
- **Counts:** 2,358 observations / 178 distinct matches, match days Oct 3-16.
- **Excluded:**
  - no verified HLTV identity: 689;
  - same line on the other platform (copy): 253;
  - no forecast at least 30 min before the start: 61;
  - Maps 1-3: 46;
  - push at the original line: 29;
  - no HLTV total: 10.
- **Offered side:** logged only from 2026-10-15 02:44. 383 observations / 16 matches have the favoured side verified as
  offered. For the other 1,975 the side was not recorded and stays missing.
- **Other platform's line at the decision:** logged from 2026-10-15 03:26; only 121 observations / 13 matches. Too few to
  use.

**Results at the original line** (recorded estimates vs FLAT 50% and BASE; match-grouped 95%):

| Cohort | Obs / matches | Log-loss gain vs FLAT | vs BASE | Calibration slope | 57% picks / matches | Hit (95%) | Hit - BASE same side (95%) |
|---|---|---|---|---|---|---|---|
| All decision-time forecasts | 2,358 / 178 | -0.0066 (-0.0169, +0.0034) | -0.0052 (-0.0163, +0.0057) | 0.25 (-0.12, 0.65) | 784 / 149 | 52.6% (47.1-58.4) | +0.028 (-0.026, +0.086) |
| Railway era, Oct 3-10 (mostly older pipelines) | 752 / 111 | -0.0021 (-0.0158, +0.0110) | +0.0019 | 0.44 | 246 / 86 | 56.5% (50.0-63.3) | +0.067 (+0.002, +0.135) |
| Render era, Oct 11-16 (perf-2 tags) | 1,606 / 67 | -0.0087 (-0.0221, +0.0045) | -0.0085 | 0.19 | 538 / 63 | 50.7% (43.1-58.2) | +0.010 (-0.066, +0.085) |
| Line + favoured side verified (Oct 16 only) | 383 / 16 | -0.0052 | -0.0066 | - | 86 / 14 | 51.2% (29.2-72.6) | +0.015 (-0.203, +0.229) |

**Projection information at the original line** (slope of actual - line on recorded projection - line):

| Stat | Slope (95%) | Matches | MSE projection - MSE line |
|---|---|---|---|
| Kills | +0.18 (-0.23, +0.56) | 176 | +1.64 (-0.57, +3.72) |
| HS | +0.50 (+0.15, +0.84) | 169 | -0.08 (-1.00, +0.82); as accurate as the line |

**Line movement after the decision (our own timestamped records only).**
- 2,327 verifiable observations / 173 matches. The 31 Railway observations after its export have no later record.
- **Last verified line:** captured a median 52 min before the start (25%: 20 min, 75%: 450 min); only 52% within 60 min.
  It is the last line we verifiably saw, not necessarily the true close.
- **Unchanged:** 1,635 observations / 172 matches. **Moved:** 692 observations / 120 matches.
  - Moves of 0.5: 87. Moves of 1: 445. Moves of 1.5 or more: 160.
- **Direction:** moved toward the recorded projection 70.4% (65.2-75.9); toward live's favoured side 66.9%. The share is
  similar at every size (0.5: 71%; 1: 69%; 1.5 or more: 74%).
- **Is the move predictive?** Slope of (actual - original line) on the move: +0.36 (-0.11, +0.85). Not established.
- **The fixed rule, scored at the ORIGINAL line:**

  | Subset | Picks / matches | Hit | Hit - BASE (95%) |
  |---|---|---|---|
  | Unchanged lines | 513 / 138 | 51.9% | +0.020 (-0.038, +0.082) |
  | Moved lines | 263 / 86 | 54.0% | +0.044 (-0.031, +0.116) |
  | Moved toward the projection | 205 / 79 | 54.1% | +0.046 (-0.031, +0.130) |
  | Moved away | 58 / 28 | 53.4% | +0.038 (-0.134, +0.196) |

**Closing benchmark** (separate): live's estimate recorded at the last verified pre-start line, 2,324 observations / 173
matches.
- Log loss vs FLAT -0.0060 (-0.0168, +0.0048).
- 57% picks: 809 / 147 matches, hit 53.3%, +0.033 vs BASE (-0.021, +0.085).

**Direct answer.**
- **Useful information at an available line: weak and partial.**
  - The recorded HS projection carried information at the original line; kills did not.
  - Lines that later moved went our way about 70% of the time.
  - But the recorded estimates were not better than 50% (-0.0066), they were overconfident (slope 0.25), and the moves are
    not shown to predict outcomes.
- **Fixed recommendation rule: no established benefit.** 784 picks / 149 matches hit 52.6%, +2.8 points over the baseline
  (-2.6 to +8.6).
  - The Railway-era subset (+6.7, interval just above 0) is one of four cuts and comes from older pipelines.
  - The current perf-2 era is +1.0 (-6.6 to +8.5).
  - Nothing here shows the rule beating the lines members could actually take.

## 7. Headshot model comparison (CLOSED; owner 2026-10-16: resume the headshot proposal; protocol PROTOCOL_HS.md written before it ran)

The lead (development, not an advantage): at the original line, the recorded HS projection's disagreement with the line
carries information (slope 0.50), and the recorded HS estimates are overconfident. Two separate questions:
1. Does correcting the overconfidence improve probability accuracy?
2. Does the fixed 57% rule improve selection?

**Development cohort.** HS Maps 1-2 decision-time forecasts (recorded values at the original line; corrected identity).
- 882 observations / 140 matches in all.
- Scored on days with at least 300 earlier HS observations: 576 / 50 matches, Oct 12-16. Each model is fitted on earlier
  days only (the Railway days are training only). Ridge is chosen leave-last-day-out from the fixed grid.

| Model | Log loss | vs LIVE (95%) | vs BASE (95%) | Calibration slope | 57% picks / matches | Hit | Hit - BASE (95%) |
|---|---|---|---|---|---|---|---|
| LIVE (recorded) | 0.6948 | - | -0.0014 (-0.0189, +0.0185) | 0.47 (-0.20, 1.20) | 218 / 47 | 52.8% | +0.030 (-0.066, +0.120) |
| BASE | 0.6933 | +0.0014 | - | - | 0 | - | - |
| RECAL-LIVE (overconfidence corrected: logit a + b logit p_live) | 0.6930 | +0.0018 (-0.0105, +0.0124) | +0.0004 (-0.0084, +0.0095) | 0.50 | 43 / 22 | 53.5% | +0.029 (-0.146, +0.182) |
| Z-REC (lc-1 form on the recorded projection) | 0.6918 | +0.0030 (-0.0144, +0.0182) | +0.0016 (-0.0023, +0.0052) | 1.59 | 2 / 2 | - | - |
| Z-FULL (hs-1 form, refitted each day) | 0.6956 | -0.0008 (-0.0183, +0.0144) | -0.0022 (-0.0102, +0.0049) | -0.07 | 52 / 16 | 51.9% | +0.012 (-0.180, +0.213) |

- **The day-by-day refits are unstable.** The chosen ridge jumps between 0.3 and 100, and the Z-FULL slope runs from 1.29
  down to 0.05. That is the sign of a weak signal on 50 matches.
- **The larger development benchmark** (section 3; offered-line archive, capture time not recorded, 1,286 HS lines / 159
  matches) points the same way. The hs-1 form gains +0.0025 log loss over BASE (-0.0026, +0.0083); its 57% rule picks
  are +0.050 (-0.020, +0.117).
- **HLTV history (12 months) shows no persistent level bias** in the full-history HS projection. Monthly bias stays
  between -0.37 and +0.26 HS per series, with no consistent sign. (Corrected, section 8: a near-zero HLTV-wide average
  does not establish why errors occur on the book-listed players and lines; the cause of the Over tilt at the lines is not
  identified.)

**Forward (separate).** hs-1 was frozen at 2026-10-16 20:50:42 UTC (commit 6e7dc53).
- It is registered in the learning registry as `hs-calibration-1`. It is scored only on forecasts logged after its
  registry freeze, from the pre-match fh-1 records.
- Forward units at writing (production export 21:04 UTC): **0**.

**Result.**
- **Probability:** correcting the HS overconfidence moves the estimates from slightly worse than a coin to about coin level:
  +0.0018 to +0.0030 log loss vs live, with every interval including 0. It does not beat the base rate.
- **Selection:** the fixed rule improves nothing measurable. The corrected models make far fewer picks (2-52), and no hit
  rate is distinguishable from the baseline.
- **Deployment:** nothing qualifies. Development results never qualify a model under the registered rule, and hs-1 has no
  forward matches.

**Kills.**
- No kills candidate was built. No additional predictive information has been shown, and no weight or bonus is tried.
- A large projection-vs-line gap is not evidence of a strong kills pick: the gap carries no measurable information at
  original or archive lines.
- The live grader still turns that gap into a grade under the existing rules. Changing that requires a candidate that
  passes the registered review. lb-001, whose kills arm is exactly 50%, is the frozen one under that review.

## 8. Opponent-adjusted kill rate (CLOSED - OA-2 not registered or deployed; owner 2026-10-16; protocol PROTOCOL_OA.md written before any result)

**Question.** Does adjusting each historical performance for the opposition faced improve the player's estimated ability?

**Prior work.** It had not been tested.
- FACTOR_AUDIT R3-oppagg used the upcoming opponent's own kill pace.
- R4-ko changed the shrinkage of the prediction-time opponent factor.
- The 2026-10-15 "no opponent adjustment" row removed that factor.
- None adjusted the history itself.

**Candidate.** A regularised multiplicative model: kills = rounds x exp(mu + player + opponent).
- Fitted by iterative proportional fitting on maps strictly before each weekly refit date.
- Shrinkage uses the live constants: player 300 rounds, opponent 5 x 300 player-rounds. Half-life 120 d, window 420 d.
- History view n0 (the live pin).
- Variants:
  - **OA-1** (primary; isolates the change): each historical map's kills are divided by that opponent's fitted defensive
    factor. Everything else is unchanged: the prediction-time opponent factor, the environment blend, expected rounds
    21.74, spread and HS ratio.
  - **OA-2** (secondary): OA-1 plus the upcoming opponent's factor from the same joint fit.
  - **OA-3** (post-hoc attribution, recorded as an extra configuration): the current player rate with the joint opponent
    factor only.
- **Builder check:** the unadjusted sums reproduce the current model's history sums exactly (maximum difference 0).

**HLTV history, chronological** (Jan - Oct 16 2026, Maps 1-2 kills: 85,861 player-series / 8,597 matches). Gains are
paired vs the current formula on the same history; positive = candidate better; match-grouped 95%.

| Segment | Series | Current MAE | OA-1 MAE gain | OA-1 log-score gain | OA-2 MAE gain | OA-2 log-score gain |
|---|---|---|---|---|---|---|
| All | 85,861 | 6.335 | -0.001 (-0.004, +0.002) | -0.0011 (-0.0016, -0.0006) | +0.005 (+0.002, +0.009) | +0.0003 (n.s.) |
| Book-listed players (628 with an offered line Oct 2-16) | 38,082 | 6.144 | -0.002 (n.s.) | -0.0011 (worse) | +0.007 (+0.002, +0.012) | +0.0009 (-0.0000, +0.0018) |
| Book-listed, history >= 600 rounds | 20,678 | 5.994 | +0.000 (n.s.) | -0.0001 | **+0.014 (+0.007, +0.021)** | **+0.0023 (+0.0012, +0.0035)** |
| Book-listed, history < 600 | 17,404 | 6.321 | -0.005 (worse) | -0.0022 (worse) | -0.001 (n.s.) | -0.0007 (n.s.) |
| Established (>= 600) | 32,871 | 6.000 | +0.003 (n.s.) | -0.0002 | +0.014 (+0.009, +0.019) | +0.0020 (+0.0011, +0.0028) |
| Thin (< 600) | 52,990 | 6.542 | -0.004 (worse) | -0.0017 (worse) | +0.000 | -0.0008 |
| Original top-200 set | 40,639 | 6.130 | +0.008 (+0.002, +0.014) | +0.0001 | +0.016 (+0.011, +0.020) | +0.0024 (+0.0016, +0.0031) |
| Lower tier | 45,222 | 6.518 | -0.009 (worse) | -0.0022 (worse) | -0.004 | -0.0016 (worse) |

**Attribution.**
- **OA-3 vs current** (joint opponent factor alone): MAE +0.002 (n.s.), log-score +0.0006 to +0.0010.
- **OA-2 vs OA-3** (adding the historical adjustment to the joint factor):
  - established +0.012 (+0.006, +0.019); book-listed >= 600 +0.010 (+0.002, +0.019); top-200 +0.017 (+0.011, +0.024);
  - lower tier -0.008 (worse).
- **So:** the historical adjustment improves the estimate only inside a consistent joint model (OA-2), and only for
  established / top-tier players. With the current prediction-time opponent factor (OA-1) it adds nothing.
- The month-by-month OA-1 gains change sign (2026-06 -0.018, 2026-10 -0.021).

**Practical effect at real lines** (decision-time forecasts at their original, timestamped line; all models rebuilt the
same way):

| Stat | Obs / matches | Log-loss gain OA-1 vs current | Log-loss gain OA-2 vs current | vs BASE (OA-2) | Projection MSE minus line MSE: current -> OA-2 | 57% rule hit - BASE: current / OA-2 |
|---|---|---|---|---|---|---|
| Kills | 1,045 / 147 | -0.0019 (-0.0084, +0.0044) | +0.0016 (-0.0026, +0.0058) | -0.0034 | +1.73 -> +0.90 kills^2 | +0.034 / +0.044 (both include 0) |
| HS | 882 / 140 | -0.0031 | +0.0003 (-0.0036, +0.0040) | +0.0013 | +0.42 -> +0.25 | +0.056 / +0.071 (+0.012, +0.126) |

- **The offered-line archive** (separate benchmark, capture time not recorded) agrees.
  - Kills: OA-2 vs current +0.0023 (-0.0014, +0.0059); still worse than BASE.
  - The kills projection's MSE gap to the line goes +2.59 -> +1.97.
- **The HS selection difference** (+0.071 vs +0.056) is not a paired test. It sits among several models and both stats, so
  it is not evidence of better picks.

**Result.**
- **OA-1, the opponent adjustment of the history itself, does not improve the player estimate.** It is no better overall,
  no better on book-listed players, and worse on thin / lower-tier players.
- **OA-2, a consistent joint player-and-opponent model, gives a small, significant gain on established and book-listed
  established players:** MAE -0.014 kills (about 0.2%) and log score +0.002.
  - Its effect at genuinely available lines is not established: +0.0016 log loss on kills, +0.0003 on HS.
  - It narrows the kills projection's gap to the line (MSE gap +1.73 -> +0.90) but does not close it.
- **Nothing qualifies, and nothing is deployed.**

**Remaining limitation.**
- The component is real but small. The kills projection's own error relative to the line (section 3: about 2.9 of 3.3
  kills^2 of disagreement variance) is far larger than anything opponent strength explains.
- A frozen OA-2 would be a legitimate projection-component candidate for a forward test of projection error. It is not
  registered here: the owner asked for the comparison, not another shadow.

**Correction to section 7.** Section 7 said the Over tilt at HS lines "comes from where the platforms set HS lines". That
is withdrawn. A near-zero average bias across all HLTV matches does not establish why errors occur on book-listed players
and lines, which are a selected subset. The cause of the tilt is not identified.

## 9. Complete-model comparison (owner 2026-10-16; protocol PROTOCOL_MODELS.md written before any fit)

Section 8 (opponent adjustment) is CLOSED. OA-2 is not registered or deployed.

**Design (fixed before fitting).**
- **Models:**
  - the current model (CUR: live formula, live-pin history; spread 1.457);
  - a regularised count model (GLM: Poisson, log link, L2);
  - one constrained nonlinear model (GBM: histogram boosting, Poisson loss, at most 16 leaves, at least 200 rows per leaf,
    monotone in the rate / opponent features).
- **Features:** 14, all pre-match: full-history sums and the current formula's components, opponent-adjusted sums, pre-match
  Elo, LAN. No market data.
- **Tuning budget:** 4 configurations per family, chosen on validation (Jul-Aug 2026).
- **Splits:** train Nov 2025 - Jun 2026. Test A: fit to Aug 31, predict September. Test B: fit to Sep 30, predict Oct 1-16.
  Every real-line prediction therefore comes from a fit on earlier matches only.
- **Spread:** each model's own spread from its training residuals.
- **Selection rule (fixed):** at most one candidate, and only if it improves book-listed players and book-listed established
  players in history (intervals above 0) AND real-line probabilities vs CUR (interval above 0, and not worse than BASE).
- **Evidence status:** all periods are development data.

**Validation.** Log score: CUR -3.5272, GLM -3.4944 (best alpha 1e-4), GBM -3.4845 (16 leaves, 150 iterations).

**History test** (Sep 1 - Oct 16; 17,028 player-series / 1,704 matches; gains vs CUR, positive = better; match-grouped 95%):

| Segment | Series | CUR MAE | Full-history CUR gain | GLM MAE gain | GLM log-score gain | GBM MAE gain | GBM log-score gain |
|---|---|---|---|---|---|---|---|
| All | 17,028 | 6.238 | +0.078 | +0.137 (+0.114, +0.162) | +0.0230 (+0.0190, +0.0272) | +0.137 | +0.0235 |
| Book-listed players | 8,988 | 6.099 | +0.050 | +0.091 (+0.060, +0.123) | +0.0162 (+0.0114, +0.0215) | +0.072 | +0.0128 |
| Book-listed established | 6,497 | 5.959 | +0.016 | +0.047 (+0.016, +0.077) | +0.0075 (+0.0025, +0.0126) | +0.030 | +0.0053 |
| Book-listed thin | 2,491 | 6.463 | +0.138 | +0.205 | +0.0388 | +0.181 | +0.0324 |
| Original top-200 set | 12,332 | 6.146 | +0.013 | +0.063 (+0.041, +0.085) | +0.0104 | +0.044 | +0.0073 |
| Lower tier | 4,696 | 6.477 | +0.246 | +0.330 | +0.0564 | +0.382 | +0.0661 |

**Separating the model from the history.** GLM vs the current formula on full history (fh-1's history set):
- book-listed MAE +0.041 (+0.019, +0.062), log +0.0054 (+0.0021, +0.0087);
- book-listed established +0.031 (+0.007, +0.052), log +0.0046 (+0.0010, +0.0079).

**Real lines** (decision-time forecasts at their original, timestamped offered line, Oct 3-16; 1,927 observations / 149
matches; kills + HS pooled):

| Model | Log loss | vs CUR (95%) | vs BASE (95%) | Calibration slope | AUC | 57% picks / matches | Hit (95%) | Hit - BASE (95%) |
|---|---|---|---|---|---|---|---|---|
| Live as recorded | 0.6974 | -0.0001 | -0.0024 | 0.35 | 0.528 | 675 / 127 | 54.1% | +0.043 (-0.013, +0.104) |
| CUR (rebuilt) | 0.6973 | - | -0.0022 | 0.38 | 0.535 | 787 / 138 | 54.3% | +0.045 (-0.008, +0.098) |
| Full-history CUR | 0.6929 | +0.0044 (-0.0026, +0.0116) | +0.0022 | 0.54 | 0.542 | 747 / 136 | 55.8% | +0.061 (+0.006, +0.119) |
| **GLM** | **0.6852** | **+0.0120 (+0.0032, +0.0203)** | +0.0098 (-0.0016, +0.0213) | **0.85 (0.40, 1.27)** | 0.565 | 701 / 131 | **58.8% (53.6-64.4)** | **+0.092 (+0.039, +0.149)** |
| GBM | 0.6899 | +0.0073 (-0.0020, +0.0165) | +0.0051 | 0.66 | 0.547 | 753 / 140 | 57.1% | +0.074 (+0.019, +0.128) |

- **By stat:**
  - Kills (147 matches): GLM +0.0138 vs CUR (+0.0049, +0.0235); 57% picks +0.092 (+0.019, +0.167).
  - HS (140 matches): GLM +0.0099 (+0.0011, +0.0198); 57% picks +0.092 (+0.032, +0.151).
  - The GLM's kills projection is the first whose squared error is not worse than the line's: -1.21 (-3.16, +0.67), where
    CUR is +1.73.
- **Against full-history CUR:** GLM +0.0076 log loss (+0.0008, +0.0139).
- **Stability:** the gain is similar in both halves of the line period. Oct 3-9: +0.0141 vs CUR, picks +0.119. Oct 10-16:
  +0.0113 (+0.0016, +0.0209), picks +0.081 (+0.012, +0.145).
- **Leakage probe:** with features permuted within each day, the gain disappears (-0.031 log score).
- **What the GLM does:** it weights the opponent-adjusted rate well above the unadjusted rate, keeps the environment rate,
  uses the joint opponent factor, and is less dispersed than CUR, which is why it is better calibrated.

**Selection (protocol rule).**
- **GLM: SELECTED** for independent forward confirmation. All three conditions hold.
- **GBM: not selected.** Its real-line gain vs CUR has an interval that includes 0.

**What this is, and is not.**
- It is the strongest development result so far. A complete model trained on the full history improves projections for
  book-listed players and probabilities at genuinely offered lines.
- Its picks beat the base rate on the predeclared rule.
- It is NOT established:
  - every period evaluated has been inspected before;
  - vs BASE the probability gain's interval still includes 0;
  - 149 matches is a small sample.
- **Live grading and the frozen experiments are unchanged. Nothing is deployed or registered.**

**Frozen for forward confirmation.**
- Definition: `research/realline_archive_2026_10_16/cm1_frozen.json` (cm-1).
- One fit on every record through 2026-10-16 (98,121 rows; data hash 9073466e2d54); alpha 1e-4; spread 1.473.
- Frozen 2026-10-16 22:08 UTC.
- Forward evidence must be predictions saved before matches that start after the freeze, at genuinely offered lines.
- The mechanism to save them is the owner's decision. Two options: a local scheduled snapshot job (no production change),
  or a production shadow.
