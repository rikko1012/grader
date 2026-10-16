# Real-line accuracy upgrade - protocol (fixed 2026-10-16 before any comparison below was run)

Data: production pick log + forecast log pulled 2026-10-16 ~18:40 UTC (paged, read-only). Everything settled so far has
been inspected before (P1 scorecard) - all of it is DEVELOPMENT evidence. Forward evidence = forecasts saved after the
candidate's freeze.

## Cohort (identical for every model)
- Real offered props: board lines written by the board (picks.book set, on_board 1/2), line_type standard, the pick's
  line, the latest forecast of that pick logged >= 30 min before the start, settled (pushes and voids excluded).
- One row per unit (player x match x stat x scope): the latest such forecast across platforms (the P1 rule).
- Member-entered lines (no platform) and adjusted lines (goblin / demon) are reported separately, never pooled.
- Splits by match day (UTC date of the start); every row of one match is in one split. Rolling origin: each test day is
  predicted by models fitted on earlier days only; the first day is training only.
- Intervals: 95%, whole matches resampled (match key = the pick's match slug).

## Models
1. LIVE - the logged probability (P(over)).
2. BASE - simple line-based baseline: P(over) = the Over rate at offered lines on earlier days per stat, shrunk to 0.5
   with 50 pseudo-units. Also reported: FLAT 0.5.
3. Existing challengers: lb-001 (learning.line_blend_p) everywhere; pl-2 (learning.prop_line_2_value) where its logged
   anchors exist (forecasts from 2026-10-15 03:29 UTC).
4. CORR (the one candidate) - compact regularized correction:
   logit P(over) = a_s + b_s * z, s = stat (kills / HS), z = (lambda - line) / (c_s * sqrt(lambda)) = the independent
   projection's standardized disagreement with the line (c = the live spread constants). Ridge penalty
   lambda_r * (a^2 + b^2): shrinks toward 50% / the line. lambda_r chosen on earlier days only (leave-last-day-out inside
   the training days) from {0.3, 1, 3, 10, 30, 100}.
   Variants tried and reported (development only, not candidates unless stated): CORR+thin (z x [sample_maps < 30]),
   CORR+form (last-10 Over rate at the line - 0.5), CORR+book (PrizePicks indicator), CORR on logit(p_live) instead of z.

## Metrics (identical cohort, pooled out-of-sample predictions of all test days)
- Probability accuracy: log loss, Brier; paired differences vs LIVE and vs BASE (match-grouped intervals).
- Calibration: mean predicted vs observed, calibration slope (logistic regression of the outcome on logit p).
- Ranking: AUC.
- Projection error (separately): live lambda vs actual (MAE, RMSE, bias) and the offered line vs actual.
- Recommendation-selection rule (FIXED NOW): recommend the favoured side when the model's chance of that side >= 0.57
  (the live B threshold) on these lines. Report count, matches, hit rate with a match-grouped interval, mean printed
  chance; compared with 50% and with LIVE's own recommended subset.

## Decision
- A candidate must beat BOTH LIVE and BASE on probability accuracy (paired interval excluding 0) and show its
  recommendations hit above 50% (interval) to claim a useful prediction - otherwise it is reported as calibration only.
- Live deployment only through the registered review (300 / 600 / 1,000 forward matches, learning.REVIEW_RULE).
