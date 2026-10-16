# Opponent-adjusted kill rate - protocol (written before any result; file time = registration time)

Prior work checked: FACTOR_AUDIT rounds 3-6 and the 2026-10-15 study never adjusted each HISTORICAL performance for the
opponent faced. R3-oppagg = the upcoming opponent's own kill pace; R4-ko = shrinkage of the prediction-time opponent
factor; the 2026-10-15 "no opponent adjustment" row removed the prediction-time factor. So this is untested.

## Current model (exact formula of perf.project, study.comp)
rate_all = (K + k0 pop pm) / (R + k0); rate = (Ke + ke rate_all) / (Re + ke); opp = ((ck + ko pop 5) / (cr 5 + ko 5)) / pop;
lambda = rate x opp x 21.74 x 2; K, R (Ke, Re) = his time-weighted kills / rounds (tonight's environment) over maps in
[day-420, day), half-life 120 d; ck / cr = the upcoming opponent's conceded kills / rounds; history view n0 (the live pin).

## Candidate OA (one change)
Opponent effects: a regularised multiplicative model kills = rounds x exp(mu + a_player + d_opponent), fitted by
iterative proportional fitting with gamma-type shrinkage (player pseudo 300 rounds = k0, opponent pseudo 1,500
player-rounds = 5 ko - the live constants), time-weighted (half-life 120 d, window 420 d), on maps strictly before each
weekly refit date (Mondays); a prediction on day D uses the latest refit on or before D (only earlier data).
- OA-1 (primary, isolates the historical adjustment): K and Ke replaced by sum w k_i / D_opp(i), D = exp(d) normalised to
  exposure-weighted mean 1; everything else (R, Re, priors, environment blend, the prediction-time opp factor, expected
  rounds 21.74, spread c 1.457, HS ratio) unchanged.
- OA-2 (secondary): OA-1 plus the upcoming opponent's factor from the same joint fit (D_upcoming) instead of ck / cr.
Same history view as the current model (n0); the full-history view (n) reported as a secondary check. Builder check: the
unadjusted sums must reproduce the study's K, R exactly.

## Evaluation
1. HLTV history, chronological (scored months 2026-01 .. 2026-10; Nov-Dec 2025 burn-in), Maps 1-2 kills: MAE, bias, log
   score (live discretised normal), paired vs the current model, match-grouped 95% (1,000). Separately for: book-listed
   players (players with an offered line Oct 2-16), thin history (n0 weighted rounds < 600), established (>= 600), plus the
   original top-200 set vs lower tier. Lower-tier gains alone never count as success.
2. Real lines: the decision-time forecasts at their original offered line (timestamped board lines; the actionable-line
   cohort), kills and HS Maps 1-2: probability at the line with the live distribution (kills 22-78% cap) for the current
   model (rebuilt) vs OA-1 / OA-2: paired log loss and Brier, calibration slope, slope of (actual - line) on (projection -
   line), MAE vs the line; vs BASE; the fixed 57% rule (hit - BASE same side). The offered-line archive (capture time not
   recorded) is a separate benchmark only.
3. Qualification: development results qualify nothing; a promising result becomes at most a frozen shadow proposal.
