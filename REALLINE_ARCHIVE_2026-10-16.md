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
  better point forecast. When the line departs from our projection, the departure is right.
- **Headshots.** The full-history projection does carry information the closing line lacks. That information is
  reproducible across both eras, but it is too small, and the projection too biased, to produce a detectable probability
  or pick-selection gain on 159 matches.
- **Why it fails, specifically:**
  - The market prices team strength / match context that perf-2 leaves out.
  - It also prices late information (lineups, vetoes) that arrives after our forecast.
  - Our projection's independent error is about 5-8 times the line's.

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

### E1 - every offered line, last line before the start (2,777 scored observations / 159 matches, Oct 4-16)

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
3. **Late information goes to the market.**
   - 293 of our forecast-time lines moved before the start (113 matches). 66% moved toward our rebuilt projection (60-73%),
     and the move was right (slope 0.87, 0.19 to 1.62).
   - The projection's information is real but becomes public by the close. A line moving our way is agreement with a later
     market, not selection skill. Live's realised record at forecast-time lines does not beat BASE.
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

## 4. What this archive can establish (power, from the scored matches' own variance)

| Distinct matches | Smallest log-loss gain detectable (80% power, 5% two-sided) | Smallest selection edge vs BASE detectable |
|---|---|---|
| 159 (what we have) | about 0.0076 | about 0.09 |
| 300 | 0.0056 | 0.067 |
| 600 | 0.0040 | 0.048 |
| 1,000 | 0.0031 | 0.037 |
| 2,000 | 0.0022 | 0.026 |

- The effects the archive points to (+0.0025 log loss on HS; 3-7 points selection) are below what 159 matches can
  separate from zero.
- The registered windows are not what delays a decision; the effect sizes are. The registered minimum gain (0.002 log loss)
  needs about 2,000 matches to detect with 80% power.
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
2. **Late pre-match information** (lineups, stand-ins, veto). Lines move after our forecasts and the moves are right. We do
   not observe that information before the market does.
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
