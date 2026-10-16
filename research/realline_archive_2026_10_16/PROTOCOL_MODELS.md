# Complete-model comparison - protocol (written before any model is fitted; file time = registration time)

Target: Maps 1-2 kills (both maps with full scoreboards); HS Maps 1-2 lines are scored as kills x the HS ratio of the same
history view (the HS ratio itself is not re-modelled). All periods through 2026-10-16 are development data.

## Feature availability (fixed)
Only inputs computed from HLTV maps strictly before the match day, plus facts known before the match:
- full-history (view n) sums of the study record: player kills / rounds (all; tonight's environment), opponent conceded
  kills / rounds, both teams' rounds per map, map count;
- the current formula's components from those sums (shrunk rate, environment rate, opponent factor);
- the opponent-adjusted sums and joint opponent factor (oa_sums; weekly refits on strictly earlier maps);
- pre-match Elo of both teams; the event's LAN flag.
Never: anything from the match (rounds, maps, result), the n0-export membership flag, market prices or lines.
Feature list (14): log rate_all, log rate_env, log opp factor, log(1+history rounds), log(1+environment rounds),
log(1+maps), LAN, Elo own / 400, Elo opponent / 400, Elo gap / 400, log team rounds per map (shrunk 10 maps to 21.74),
log opponent rounds per map (same), log opponent-adjusted rate_all, log joint opponent factor.

## Models (one configuration chosen per family on validation; budget 4 configurations each)
- CUR: the current formula, live-pin history (n0), spread c 1.457 - the live model. CUR-FULL (view n) as a reference.
- GLM: Poisson regression, log link, standardised features (train statistics), L2 alpha in {1e-4, 1e-3, 1e-2, 1e-1}.
- GBM: histogram gradient boosting, Poisson loss, learning rate 0.05, min 200 samples per leaf, l2 1.0, monotone
  increasing in log rate_all, log rate_env, log opp factor, log adjusted rate, log joint opponent factor; configurations
  (leaves, iterations) in {(8, 300), (16, 300), (8, 600), (16, 150)}.
Predictive distribution for every model: discretised normal, sd = c sqrt(lambda), c^2 = mean((y - lambda)^2 / lambda) on
its training rows (CUR keeps the live 1.457); kills 22-78% cap at lines, as live.

## Splits (by match day)
Train 2025-11-01 .. 2026-06-30; validation 2026-07-01 .. 08-31 (choosing the configuration only).
Test A: fit on 2025-11-01 .. 08-31, predict 2026-09-01 .. 09-30. Test B: fit on 2025-11-01 .. 09-30, predict 10-01 .. 10-16
(contains the real-line days, so every line prediction comes from a fit on earlier matches only).

## Evaluation
History (tests A + B): MAE, bias, log score, paired vs CUR, match-grouped 95% (1,000). Segments: book-listed players
(an offered line 2026-10-02 .. 10-16), book-listed established (>= 600 weighted rounds) / thin, all established / thin,
original top-200 set / lower tier, LAN / online.
Real lines (product): the decision-time forecasts at their original, timestamped offered line (actionable-line cohort),
kills and HS Maps 1-2: log loss and Brier vs CUR and vs BASE, calibration slope, the fixed 57% rule (hit - BASE same side).

## Selection (at most one candidate for independent forward confirmation)
A candidate is selected only if ALL hold: (1) book-listed players, history test: MAE gain AND log-score gain vs CUR with
95% intervals above 0; (2) book-listed established players: log-score gain vs CUR above 0 (95%); (3) real lines, kills
and HS pooled: log-loss gain vs CUR with 95% interval above 0 and a point estimate vs BASE >= 0. If both pass, the larger
real-line gain is chosen. If none passes: none is selected. Lower-tier gains alone never qualify. Nothing is deployed or
registered; live grading and the frozen experiments stay unchanged.
