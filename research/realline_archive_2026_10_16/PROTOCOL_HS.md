# Headshot model comparison - protocol (written before it ran; file time = registration time)

Lead (development, not an advantage): at the original offered line the recorded HS projection's disagreement with the
line carried information (slope 0.50, 0.15 to 0.84), while the recorded HS estimates are overconfident.

Cohort (development): the decision-time forecasts of the actionable-line analysis restricted to HS Maps 1-2 - the
EARLIEST saved forecast >= 30 min before the start per player x HLTV match x platform, recorded values, scored at the
original line by the HLTV total; corrected identity join. Scored days: each day with >= 300 earlier HS observations.

Models (identical rows; each fitted ONLY on earlier days; ridge strength from {0.3, 1, 3, 10, 30, 100} chosen
leave-last-day-out inside the training days):
- LIVE: the recorded live HS estimate (baseline).
- BASE: earlier-day HS Over rate, shrunk with 50 pseudo-units.
- RECAL-LIVE: logit p = a + b logit(p_live), ridge toward the identity (a = 0, b = 1) - corrects the recorded
  estimate's overconfidence and level without new information.
- Z-REC: logit p = a + b z, z = (recorded projection - line) / (1.264 sqrt(recorded projection)) - the lc-1 form.
- Z-FULL: the same with the full-history projection (strictly earlier HLTV days) - the hs-1 form, refitted per day.
hs-1 itself (frozen 2026-10-16 20:50:42 UTC, coefficients fitted on Oct 2-16) is in-sample on these days and is scored
only FORWARD: P1 HS Maps 1-2 units whose forecast was logged and whose match started after its freeze, from fh-1's saved
pre-match projection.

Probability accuracy (separately): paired log loss and Brier vs LIVE and vs BASE, calibration slope and mean gap, AUC.
Selection (separately): each model's fixed rule = favoured side when its estimate >= 57%; picks, matches, hit, hit minus
BASE's estimate for the same side. Match-grouped bootstrap (2,000), 95%.
Deployment qualification: only through the registered rule (learning.REVIEW_RULE, forward matches); development
results never qualify anything. Kills: no candidate in this comparison (no additional predictive information has been
shown; no weight or bonus is tried).
