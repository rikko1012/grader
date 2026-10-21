# hs-cm2: headshot lines (Maps 1-2) on cm-2 kills - protocol (registered 2026-10-21, before any hold-out number)

**Owner instruction (2026-10-21):** "tweak the projections cs2 and LoL to make them more accurate". This protocol is written
before any number on its hold-out is computed. Development checks by the audit scout (scratchpad audit/accuracy_candidates,
155 matches of earlier-inspected lines) motivated it and are NOT evidence for the decision.

## Why
HS Maps 1-2 lines (~40% of the CS2 board) still use perf-2 on the N0-pinned history (kills bias about -0.75 on Maps 1-2).
Most HS error comes from the kills underneath it (FACTOR_AUDIT round 7: knowing kills cuts HS MAE 3.72 -> 2.53). cm-2 is
live for kills Maps 1-2 (perf-cm2) since 2026-10-20.

## Candidate hs-cm2 (nothing fitted)
- Kills under the HS line: cm-2's kills mean and spread for the same player/match (cm2.projection: lambda, sd = 1.4757 x sqrt(lambda)).
- HS per kill: cm1.hs_ratio on the same cm-2 tables (full history, (hk + k_hs * pop_hs) / (hkk + k_hs), k_hs = 25, unchanged).
- Distribution: the live headshot model unchanged (perf.hs_probs: HS | kills beta-binomial, kappa 100) with that kills
  distribution and that HS-per-kill; the same grader path (pushes, cuts, no kills cap on HS).
- Eligibility: cm-2's own (environment known, team and opponent identified, maps in window); otherwise perf-2 stays.

## Baseline
The live HS grade as published: perf-2 (N0) HS projection and chance.

## Hold-out (never inspected for this question)
Matches played from 2026-10-17 00:00 UTC to the day the study runs. Tables strictly before each match day (cm1/cm2 rules).
1. **HLTV-wide:** every Maps 1-2 player-series in the hold-out with both maps' headshots recorded: absolute error of the HS
   projection (candidate vs perf-2 N0 rebuilt for the same day), match-grouped bootstrap 95% interval of the MAE gain.
2. **Offered lines (P1):** production's latest board forecast >= 30 min before start on standard PrizePicks / Underdog HS Maps 1-2
   lines in the hold-out, settled: log-loss of P(Over | no push) candidate vs the published chance; match-grouped 95% interval.

## Decision (fixed now)
hs-cm2 goes live for HS Maps 1-2 (as model version perf-cm2 for HS, old forecasts kept, rollback flag) only if
(1)'s MAE gain interval is above 0 AND (2)'s log-loss gain point estimate is >= 0. Otherwise nothing changes and the result is
recorded. Calibration (printed vs won by chance band) is reported for both arms on (2).
