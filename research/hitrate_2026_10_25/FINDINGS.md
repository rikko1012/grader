# Hit-rate investigation, 2026-10-25 (no grading change)

Data: P1 export at the 2026-10-25T18:31:28Z learning cycle. The rule is the latest standard board forecast logged at least 30 min before
the start; there is one unit per player x canonical match x stat x scope, pushes are excluded and older-key duplicates are
counted once. That gives 3,650 settled CS2 units, starts Oct 11-25 (P1 has no earlier units). LoL uses the same rule on lol_forecasts:
standard / balanced lines, latest settlement, one unit per series x player x stat x scope. That gives 388 units / 20 series
(645 platform rows; 186 units were offered on both platforms, 31 of them on different lines with opposite sides). The
intervals are match-clustered 95% bootstraps (B = 2000). The weeks are: W1 = Oct 11-17, W2 = Oct 18-24, W3 = Oct 25 (partial). Scripts are in
scripts/ and outputs are in outputs/.

## CS2: is it declining?

| cohort | W1 | W2 | W3 | slope per day (95%) |
|---|---|---|---|---|
| all lines (favoured side) | 790-759, 51.0% [47.1, 54.7], printed 56.2 | 958-929, 50.8% [47.4, 54.1], printed 55.5 | 112-102, 52.3% [43.9, 59.9] | +0.0018 [-0.0043, +0.0077] |
| 57%+ | 300-266, 53.0% [47.0, 58.6], printed 61.4 | 279-296, 48.5% [42.7, 54.1], printed 60.8 | 23-33, 41.1% [27.4, 57.9] | -0.0037 [-0.0138, +0.0060] |
| staked (units > 0) | 357-314, 53.2% [47.2, 58.8], printed 59.8 | 336-353, 48.8% [43.3, 54.1], printed 59.1 | 38-36, 51.4% [38.7, 64.9] | -0.0030 [-0.0118, +0.0060] |

The weekly figures are not distinguishable from one another, and no trend is detectable. The gap that persists is printed ~59-61% against
~49-53% won on graded picks, and it has been there since the first P1 week (Oct 3-11 staked first-grade recap: 54.3% vs 61.0%). On all
P1 units the live log loss is worse than a constant 50% by 0.0089 [0.0006, 0.0169].

## Root causes

1. **The distance between the projection and the line carries almost no information for kills.** The slope of (actual - line) on
   (projection - line) is +0.11 [-0.26, +0.50] for kills Maps 1-2 and +0.42 [+0.07, +0.76] for HS Maps 1-2. LoL lol-1f gives
   kills +0.23 [-0.24, +0.87] and assists +0.02 [-0.58, +0.94]. The chance treats the full distance as real (slope 1), so 57%+
   grades are overconfident. The kills projection MAE is about 1-2% worse than the book line (W2: 6.80 vs 6.69). The spread is not the cause:
   the standardised kills residual variance is 1.03.
2. **The W2 dip (staked 48.8%) came from realised rounds, not the model.** 7.0% of Maps 1-2 maps went to overtime (W1 2.7%,
   W3 4.2%). Maps 1-2 had +2.52 [+0.03, +5.08] more rounds than the rr-1 shadow's pre-match expectation. Kills ran +0.97 above our
   projection and +0.97 above the book line alike. 57%+ Unders went 13-53 in the 14 matches with an OT map; in matches without
   an OT map, staked was 299/588 = 50.9% [45.5, 56.5] (W1 53.5%). Correlated Unders (memory: under-lean-correlated-risk).
3. **Version changes did not cause it.** On the same perf-cm2 units, against the saved perf-2 numbers, cm-2 kills gives a log loss gain of +0.0037
   [-0.0042, +0.0117] (856 units / 97 matches), and hs-cm2 gives +0.0009 [-0.0086, +0.0109] (428 / 53).
4. **Subgroups (small; reading only).** perf-2-bo3 in W1 went 3-22; the bo3-env-2 fix landed Oct 18, and W2 was 37-40. Thin history (< 150 weighted rounds,
   W2+) went 20-32. "Opponent not in HLTV - average opponent" went 54-63 (117 units; err -1.60 on the 105 without thin history) on HLTV matches whose
   opponents exist on HLTV but only outside the pinned N0 history (rule n0-r4; the fh-1 shadow tests full history).

## Data-quality checks (W2+ P1 forecasts, saved inputs)

- No forecast was graded on an HLTV DB more than 1 day old (0 of 3,361 with a data_rev).
- No legacy or transient-fallback grades appear in P1.
- No duplicate units.
- On 1,894 of 1,914 checkable units the saved team + opponent ids equal the HLTV match's teams, and the settled total equals a
  player of the graded team. The other 20 are LP -> BARCA (hltv:2399805), the dated rename rule match-rules-3, which is intended.
- The one outlier settlement (7 kills on a 27.5 line, hltv:2398992) is verified against HLTV: a 17- and a 15-round stomp.

No code bug was found that explains the results.

## Tested change

LD-1, a recency level-drift correction (REGISTER.md f3f882a, LD1_RESULT.md), FAILED. Kills MAE gain +0.005 [-0.042, +0.050],
HS +0.001 [-0.015, +0.015]. Nothing changes.

The registered line-anchored shadows lead on probability (interim, below the 300-match review):
- pl-2: +0.0079 [+0.0004, +0.0154] log loss (2,541 units / 171 matches);
- pl-1: +0.0082 [+0.0006, +0.0157].

Both make almost no picks (pl-2: 6 at 57%+), so they do not answer selection. Their decision stays at REVIEW_RULE 300 matches.

## LoL (stakes 0U)

- lol-1f kills: 105-74, 58.7% [50.3, 67.4], printed 60.0% (calibrated).
- lol-1f assists: 81-89, 47.6% [35.3, 61.2], printed 62.5%. Over assists went 20-33, and 57%+ Over assists 5-18. The log loss is worse than 50% by
  0.085 [-0.007, +0.180]. This is the known assists overconfidence (Oct 20: A8-A12 all failed, so a recalibration is the
  owner's decision).
- Days: Oct 23 55.0%, Oct 24 48.1%, Oct 25 64.1%, so no decline.
- No deaths lines were offered (0 rows), so deaths cannot be measured.
