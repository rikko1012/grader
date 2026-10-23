# Model card - Scout Sports sm-1, NFL (statistical-only)

Generated 2026-10-23 from the committed study results (`sports/research/results/model_study_nfl_*.json`) and artifact `sports/model/artifacts/sm1_nfl.json` (sha `cc56aeb8b88c31d8`, fitted 2026-10-23T23:00:21Z on data known at 2026-10-23T23:00:00Z). Registered protocol: `sports/research/REGISTER.md` (+ amendments A1-A3, all made before any locked-test score). Data rights: ESPN and Kalshi display rights are owner-attested (owner attestation 2026-10-23); nflverse data is CC-BY 4.0 (FTN-charted fields CC-BY-SA 4.0).

## What the model is

E[opportunity] (EW targets / carries / attempts; zero rows from snap counts included) x efficiency (EB shrinkage to the train position prior) x exp(intercept + passed GLM terms); family chosen on tune (Poisson / NB / opportunity mixture with receptions <= targets and completions <= attempts / empirical ratio / normal); cal-1 from tune. Conditional on playing (offensive snaps or a stat row).

Every probability is read from ONE distribution per prop: pOver = P(X > L), pUnder = P(X < L), pPush = P(X = L), unconditional (sum 1); the conditional-on-no-push values are separate named fields. Replay: the forward path computes each PMF from its saved feature row; a replay from saved inputs is bit-identical (200 / 200 sampled live forecasts on 2026-10-23).

## Data (ds-1)

nflverse weekly player stats + snap counts + play-by-play 2022-2026 (2026 through week 7, 94 games); zero rows added for QB/RB/WR/TE with offensive snaps but no stat row (3,917). Point-in-time rule: start + 6 h <= forecast time.

## Per market

### Passing yards (`pass_yds`)

- Validation state: **unvalidated (shadow)** (history gate H failed: 2_ref_logloss_not_worse_than_b1, 3_calibration). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [4, 4], family **ratio** (tune CRPS normal 47.5402, ratio 45.9433, mixture 49.2784), cal-1 c = 0.99, s = 1.0; features kept: none (base only).
- Rows: train 1590 (798 games), tune 272 (135 games), LOCKED test 302 (149 games); forward artifact fit on 2358 rows / 1176 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 49.687 | 51.433 | 52.855 |
| MAE | 70.840 | 71.067 | 71.261 |
| log-loss at L_ref | 0.699 | 0.688 | 0.719 |
| Brier at L_ref | 0.253 | 0.248 | 0.263 |
| 80% PIT coverage | 0.815 | 0.670 | 0.761 |
| RMSE | 88.672 | 89.371 | 91.732 |
| bias (mean - actual) | 4.590 | 11.885 | 12.690 |
| NLL | 7.694 | 6.093 | n/a |
| ECE / in-the-large at L_ref | 0.034 / +0.0069 | 0.067 / +0.0186 | 0.097 / +0.0361 |

- CRPS difference sm-1 - B1: -1.7462, game-clustered 95% [-3.0149, -0.4442], date-block [-2.5232, -1.0242]; sm-1 - B0: -3.1676 [-4.5750, -1.7259].
- Log-loss at L_ref sm-1 - B1: +0.0111 [-0.0034, +0.0258]; push check at the whole line: predicted 0.005 vs observed 0.003.
- Secondary locked test (2026 weeks 1-7, 194 rows / 94 games): CRPS sm-1 - B1 -0.1042 [-1.4569, +1.2591].
- Market track: game spread / implied total tested on tune, none passed.
- Declared slices with the weakest CRPS difference vs B1: band:<0.4 n=50 +0.0270 [-4.0461, +4.0294]; side:under n=177 -1.6544 [-3.5085, +0.1889]; role:QB n=302 -1.7462 [-3.0149, -0.4442].
- Limitation: the empirical-ratio family stores 200 ratio quantiles per mean quintile, so its probabilities move in steps of about 0.5 percentage points; on yards its PMF has gaps between ratio points, which the NLL (a secondary metric) penalises - CRPS and line probabilities are unaffected.

### Rushing yards (`rush_yds`)

- Validation state: **unvalidated (shadow)** (history gate H failed: 1_crps_beats_b1_and_b0, 3_calibration). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [2, 2], family **mixture** (tune CRPS normal 18.5193, ratio 18.1176, mixture 18.0909), cal-1 c = 0.94, s = 0.98; features kept: none (base only).
- Rows: train 2535 (806 games), tune 411 (134 games), LOCKED test 484 (150 games); forward artifact fit on 3724 rows / 1184 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 17.721 | 17.860 | 18.868 |
| MAE | 24.499 | 24.966 | 25.828 |
| log-loss at L_ref | 0.666 | 0.674 | 0.706 |
| Brier at L_ref | 0.237 | 0.240 | 0.256 |
| 80% PIT coverage | 0.789 | 0.833 | 0.791 |
| RMSE | 33.377 | 33.125 | 33.935 |
| bias (mean - actual) | -2.904 | 0.826 | 0.540 |
| NLL | 4.784 | 4.887 | n/a |
| ECE / in-the-large at L_ref | 0.038 / -0.0354 | 0.061 / +0.0691 | 0.085 / +0.0025 |

- CRPS difference sm-1 - B1: -0.1397, game-clustered 95% [-0.6257, +0.3664], date-block [-0.6161, +0.3619]; sm-1 - B0: -1.1470 [-1.6772, -0.5980].
- Log-loss at L_ref sm-1 - B1: -0.0076 [-0.0291, +0.0143]; push check at the whole line: predicted 0.012 vs observed 0.004.
- Secondary locked test (2026 weeks 1-7, 294 rows / 94 games): CRPS sm-1 - B1 -0.4381 [-0.9646, +0.1369].
- Market track: game spread / implied total tested on tune, none passed.
- Declared slices with the weakest CRPS difference vs B1: side:over n=85 +0.0233 [-0.5323, +0.5664]; band:0.4-0.6 n=207 -0.0464 [-0.5075, +0.4371]; depth:12-+ n=441 -0.0733 [-0.5714, +0.4550].

### Receiving yards (`rec_yds`)

- Validation state: **unvalidated (shadow)** (history gate H failed: 3_calibration). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [8, 8], family **ratio** (tune CRPS normal 16.0328, ratio 15.6800, mixture 15.6881), cal-1 c = 0.98, s = 0.98; features kept: nfl.snap_share.
- Rows: train 6794 (806 games), tune 1148 (135 games), LOCKED test 1207 (150 games); forward artifact fit on 9928 rows / 1185 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 16.136 | 16.486 | 17.018 |
| MAE | 23.302 | 23.551 | 23.591 |
| log-loss at L_ref | 0.670 | 0.692 | 0.685 |
| Brier at L_ref | 0.239 | 0.249 | 0.246 |
| 80% PIT coverage | 0.804 | 0.841 | 0.769 |
| RMSE | 30.448 | 30.512 | 30.593 |
| bias (mean - actual) | 0.324 | 3.069 | 3.044 |
| NLL | 4.775 | 4.769 | n/a |
| ECE / in-the-large at L_ref | 0.056 / +0.0139 | 0.113 / +0.1152 | 0.079 / +0.0555 |

- CRPS difference sm-1 - B1: -0.3509, game-clustered 95% [-0.6335, -0.0406], date-block [-0.5372, -0.1098]; sm-1 - B0: -0.8829 [-1.2083, -0.5601].
- Log-loss at L_ref sm-1 - B1: -0.0213 [-0.0344, -0.0070]; push check at the whole line: predicted 0.013 vs observed 0.018.
- Secondary locked test (2026 weeks 1-7, 779 rows / 94 games): CRPS sm-1 - B1 -0.2592 [-0.5847, +0.0620].
- Market track mi-hist (+ closing game lines; available about kickoff - market-informed, never a statistical-only claim): CRPS vs sm-1 -0.0906 [-0.1716, -0.0138] on 1207 rows.
- Declared slices with the weakest CRPS difference vs B1: role:TE n=304 +0.0160 [-0.4935, +0.5543]; side:over n=119 -0.2002 [-0.6335, +0.1889]; band:<0.4 n=610 -0.2857 [-0.8080, +0.2633].
- Limitation: the empirical-ratio family stores 200 ratio quantiles per mean quintile, so its probabilities move in steps of about 0.5 percentage points; on yards its PMF has gaps between ratio points, which the NLL (a secondary metric) penalises - CRPS and line probabilities are unaffected.

### Rushing attempts (carries) (`rush_att`)

- Validation state: **unvalidated (shadow)** (history gate H failed: 1_crps_beats_b1_and_b0, 2_ref_logloss_not_worse_than_b1, 3_calibration, 4_cov80_in_0.76_0.84). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [2, 2], family **ratio** (tune CRPS poisson 2.8092, negbin 2.7693, ratio 2.7325), cal-1 c = 0.97, s = 0.88; features kept: nfl.home.
- Rows: train 2535 (806 games), tune 411 (134 games), LOCKED test 484 (150 games); forward artifact fit on 3724 rows / 1184 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 2.998 | 2.978 | 3.170 |
| MAE | 4.164 | 4.145 | 4.331 |
| log-loss at L_ref | 0.674 | 0.661 | 0.722 |
| Brier at L_ref | 0.240 | 0.234 | 0.263 |
| 80% PIT coverage | 0.746 | 0.709 | 0.768 |
| RMSE | 5.422 | 5.337 | 5.521 |
| bias (mean - actual) | -0.542 | -0.049 | -0.131 |
| NLL | 3.206 | 3.139 | n/a |
| ECE / in-the-large at L_ref | 0.069 / -0.0376 | 0.037 / -0.0040 | 0.049 / -0.0073 |

- CRPS difference sm-1 - B1: +0.0191, game-clustered 95% [-0.0679, +0.1107], date-block [-0.0876, +0.1165]; sm-1 - B0: -0.1721 [-0.2785, -0.0603].
- Log-loss at L_ref sm-1 - B1: +0.0135 [-0.0089, +0.0377]; push check at the whole line: predicted 0.078 vs observed 0.070.
- Secondary locked test (2026 weeks 1-7, 294 rows / 94 games): CRPS sm-1 - B1 -0.1260 [-0.2182, -0.0334].
- Market track: game spread / implied total tested on tune, none passed.
- Declared slices with the weakest CRPS difference vs B1: band:<0.4 n=204 +0.0951 [-0.0472, +0.2508]; side:under n=344 +0.0530 [-0.0433, +0.1505]; depth:12-+ n=441 +0.0280 [-0.0585, +0.1206].
- Limitation: the empirical-ratio family stores 200 ratio quantiles per mean quintile, so its probabilities move in steps of about 0.5 percentage points.

### Receptions (`rec`)

- Validation state: **unvalidated (shadow)** (history gate H failed: 1_crps_beats_b1_and_b0, 6_nfl_secondary_test). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [8, 8], family **ratio** (tune CRPS poisson 1.1594, negbin 1.1562, mixture 1.1561, ratio 1.1561), cal-1 c = 0.98, s = 0.94; features kept: none (base only).
- Rows: train 6794 (806 games), tune 1148 (135 games), LOCKED test 1207 (150 games); forward artifact fit on 9928 rows / 1185 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 1.157 | 1.159 | 1.234 |
| MAE | 1.674 | 1.693 | 1.716 |
| log-loss at L_ref | 0.660 | 0.662 | 0.691 |
| Brier at L_ref | 0.234 | 0.235 | 0.249 |
| 80% PIT coverage | 0.796 | 0.756 | 0.752 |
| RMSE | 2.125 | 2.134 | 2.159 |
| bias (mean - actual) | 0.077 | 0.284 | 0.292 |
| NLL | 2.103 | 2.092 | n/a |
| ECE / in-the-large at L_ref | 0.025 / +0.0153 | 0.059 / +0.0562 | 0.075 / +0.0712 |

- CRPS difference sm-1 - B1: -0.0022, game-clustered 95% [-0.0187, +0.0145], date-block [-0.0177, +0.0134]; sm-1 - B0: -0.0772 [-0.0998, -0.0532].
- Log-loss at L_ref sm-1 - B1: -0.0016 [-0.0108, +0.0080]; push check at the whole line: predicted 0.181 vs observed 0.176.
- Secondary locked test (2026 weeks 1-7, 779 rows / 94 games): CRPS sm-1 - B1 +0.0061 [-0.0140, +0.0261].
- Market track: game spread / implied total tested on tune, none passed.
- Declared slices with the weakest CRPS difference vs B1: side:over n=185 +0.0171 [-0.0116, +0.0438]; band:0.4-0.6 n=575 +0.0082 [-0.0080, +0.0242]; role:TE n=304 +0.0066 [-0.0193, +0.0333].
- Limitation: the empirical-ratio family stores 200 ratio quantiles per mean quintile, so its probabilities move in steps of about 0.5 percentage points.

## Intended use and limits

- Research / shadow forecasts only. A history-validated market is NOT a betting-edge claim: no forecast of this version has settled at a real offered line; gate F (>= 300 settled props, >= 100 games, chosen-side calibration, log-loss vs sharp-2 / mc-1) decides that, at the 300 / 600 / 1,000 windows.
- Historical evaluation uses a pre-game reference line (floor of the last-10 mean + 0.5), not book lines: books set lines with information the model lacks, so accuracy at offered lines can be lower.
- Players outside the validated population (NBA < 5 prior games or < 15 L10 minutes; NFL < 3 games or below the pool thresholds) still get a research forecast, reason-coded `outside_validated_population` / `thin_history`.
- Every factor of the owner's list is in `sports/features.py` with its status and measured tune effect; unavailable (licensed) factors are absent, never imputed.
