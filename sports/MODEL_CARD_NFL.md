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

## Owner re-audit 2026-10-24 (N1/N2) - containment, a bug fix, measured bias

- **Containment (vg-1, sports/board_view.py):** all five sm-1 NFL markets failed gate H, so no NFL row shows a lean,
  edge, confidence or edge rank (board, sheets, slip pool). Projection + chances stay, labelled "NFL model not
  validated - projection only". sg-1 sanity guard (all markets) holds a row when the projection is under 25% / over
  4x the line (lines >= 2) or the strict L10 at the line contradicts the lean (>= 60% of >= 5 games on the other side).
- **Bug fixed (featurize.nfl_frame):** training maps FB -> RB, upcoming rows kept "FB"; no position prior matched,
  efficiency was NaN and the PMF collapsed onto 0 (Kyle Juszczyk rec_yds: 0.05 projected at 11.5; 11.2 after the
  map, same frozen artifact). 13 of 3,747 forward NFL sm-1 forecasts on the 2026-10-24 store copy were FB rows.
  No artifact or calibration change; forward rows of fullbacks re-forecast because their feature-row hash changes.
- **Measured (no settled forward NFL prop exists in any reachable store; production settled rows need the service
  admin token):** held-out signed bias (mean - actual) and calibration-in-the-large P(Over) - hit on 2026 weeks 1-7
  (secondary locked test): pass_yds -9.9 yds / -5.7 pts, rush_att -0.37 / -3.9 pts, rec -0.13 / -2.5 pts, rec_yds
  -1.4 / -1.4 pts, rush_yds -1.0 / +2.0 pts. At the 2026-10-24 BOOK lines (unique player x stat x line, store copy):
  mean P(Over) 0.39 (rush_att) - 0.46 (rush_yds); the projected MEAN tracks the player's last-6 average (rec_yds
  -1.5, rush_yds +0.7, rec -0.2; pass_yds -12.7) and P(Over) matches the player's own last-16 over-rate at the same
  line (rec_yds 0.447 vs 0.451, rec 0.414 vs 0.427, rush_yds 0.456 vs 0.479, pass_yds 0.452 vs 0.501) EXCEPT
  rush_att (0.387 vs 0.465). The one-direction Under edges come from (1) book lines sitting above the players'
  history (L16 over-rate 0.43-0.50 at the line while the market estimate is ~0.50), (2) the left tail of
  partial-participation games (snaps < 50%: injury exits, backup weeks) kept at full weight in the opportunity
  history - 21% of rec_yds PMF mass sits at <= 25% of the line (history: 25%), which the market prices out for a
  player expected to play his full role, (3) the rush_att / pass_yds under-forecast above. Fixing (2) / (3) is a
  model change: a new version through REGISTER.md (held-out test first), not done silently.

- **Follow-up (2026-10-25):** challenger sm-nfl-2 (REGISTER A6: participation split + cal-2) - tune results, post-hoc
  diagnostic and shadow state in `sports/MODEL_CARD_SMNFL2.md`. pass_yds / rec_yds / rec in shadow, rush markets
  dropped on tune; held-out H2 is forward only (0 / 48 games); NFL stays contained.


## Walk-forward fine-tuning tw-1 (REGISTER A7, 2026-10-25) - nothing switched, NFL stays contained

Protocol: `sports/research/REGISTER.md` amendment A7 (committed 6170ab6 before any A7 fit or score); code
`sports/model/tune.py`; results `sports/research/results/model_tw1_nfl.json` (packaged ds-1
`history_ds1_2026-10-23.db.gz`, as_of 2026-10-25T18:00Z; a re-run reproduces every number). Every fitted parameter is
refit at each origin on games known before it; calibration layers read only earlier out-of-sample forecasts. This is
honest out-of-sample evidence for the PROCEDURE but weaker than a never-seen period: the windows were scored before
(labelled "walk-forward (procedure out-of-sample; windows previously inspected)"). 95% intervals are game-clustered
(B = 2,000). Criteria A7 7: (a) CRPS interval below 0, (b) |in-the-large| <= 0.02 and ECE <= 0.03 at L_ref, (c) PIT
80% coverage 0.76-0.84, (e) every band with n >= 100 within 0.03 of its mean probability.

Span: 2025 week 10 - postseason + 2026 weeks 1-7 (warm-up 2025 weeks 1-9 feeds calibration only); selection block
(half-life / window choice) 2025 weeks 10-14; evaluation block 2025 week 15 - 2026 week 7.

| Market | Arm (h / k) | Eval rows / games | CRPS cand - live [game 95%] | in-the-large L_ref live / cand | ECE live / cand | PIT cov80 live / cand | bias live / cand | A band p / hit (cand) | Pass (a b c e) |
|---|---|---|---|---|---|---|---|---|---|
| rush_yds | rc (2 wk) | 547 / 171 | +0.0814 [-0.0424, +0.2059] | +0.003 / +0.022 | 0.030 / 0.037 | 0.784 / 0.777 | -0.92 / +0.64 | 0.678 / 0.661 (n 310) | n n Y n |
| rush_yds | sn (4 wk) | 547 / 171 | +0.1411 [-0.0119, +0.2947] | +0.003 / +0.025 | 0.030 / 0.034 | 0.784 / 0.785 | -0.92 / +0.84 | 0.687 / 0.663 (n 309) | n n Y n |
| rush_att | rc (2 wk) | 547 / 171 | +0.0127 [-0.0106, +0.0356] | -0.030 / -0.007 | 0.040 / 0.034 | 0.759 / 0.789 | -0.28 / -0.03 | 0.685 / 0.650 (n 283) | n n Y n |
| rush_att | sn (2 wk) | 547 / 171 | +0.0358 [-0.0106, +0.0815] | -0.030 / -0.002 | 0.040 / 0.028 | 0.759 / 0.804 | -0.28 / -0.00 | 0.698 / 0.651 (n 304) | n Y Y n |
| rush_att | dr (4 wk) | 547 / 171 | +0.0117 [-0.0047, +0.0294] | -0.030 / -0.012 | 0.040 / 0.042 | 0.759 / 0.749 | -0.28 / -0.04 | 0.692 / 0.658 (n 301) | n n n n |
| pass_yds | rc (inf) | 343 / 170 | +0.0336 [-0.0474, +0.1126] | -0.046 / -0.049 | 0.046 / 0.049 | 0.815 / 0.814 | -4.24 / -4.86 | 0.651 / 0.516 (n 91) | n n Y Y |
| pass_yds | v2 (inf) | 343 / 170 | -0.1593 [-1.4606, +1.1515] | -0.046 / -0.053 | 0.046 / 0.048 | 0.815 / 0.772 | -4.24 / -4.81 | 0.675 / 0.549 (n 144) | n n Y n |
| pass_yds | dr (3 wk) | 343 / 170 | -0.0905 [-0.2589, +0.0573] | -0.046 / -0.043 | 0.046 / 0.049 | 0.815 / 0.812 | -4.24 / -3.61 | 0.644 / 0.521 (n 96) | n n Y Y |
| rec_yds | rc (inf) | 1366 / 171 | +0.0065 [-0.0013, +0.0155] | -0.008 / -0.009 | 0.049 / 0.049 | 0.788 / 0.788 | -1.29 / -1.41 | 0.664 / 0.607 (n 766) | n n Y n |
| rec_yds | v2 (inf) | 1366 / 171 | -0.1442 [-0.2568, -0.0265] | -0.008 / -0.009 | 0.049 / 0.030 | 0.788 / 0.789 | -1.29 / -1.31 | 0.672 / 0.636 (n 761) | Y n Y n |
| rec | rc (2 wk) | 1366 / 171 | +0.0005 [-0.0031, +0.0041] | -0.022 / -0.026 | 0.033 / 0.027 | 0.776 / 0.787 | -0.10 / -0.12 | 0.677 / 0.620 (n 692) | n n Y n |
| rec | v2 (2 wk) | 1366 / 171 | -0.0124 [-0.0230, -0.0017] | -0.022 / -0.022 | 0.033 / 0.030 | 0.776 / 0.797 | -0.10 / -0.11 | 0.680 / 0.627 (n 773) | Y n Y n |

Live grade bands at L_ref (evaluation block):  rush_yds: A 337 p 0.683 hit 0.677, B 90 p 0.580 hit 0.578, C 51 p 0.546 hit 0.549, Lean 69 p 0.517 hit 0.667; rush_att: A 316 p 0.692 hit 0.655, B 123 p 0.580 hit 0.618, C 31 p 0.545 hit 0.710, Lean 77 p 0.514 hit 0.545; pass_yds: A 95 p 0.646 hit 0.526, B 86 p 0.574 hit 0.616, C 60 p 0.545 hit 0.467, Lean 102 p 0.515 hit 0.520; rec_yds: A 751 p 0.663 hit 0.607, B 300 p 0.580 hit 0.600, C 127 p 0.545 hit 0.551, Lean 188 p 0.519 hit 0.537; rec: A 686 p 0.674 hit 0.627, B 346 p 0.578 hit 0.572, C 207 p 0.543 hit 0.469, Lean 127 p 0.519 hit 0.559

- **No NFL market passes; tw-1 switched nothing; vg-1 containment unchanged.** sm-nfl-2 (`v2`) is the only arm with a
  real walk-forward gain: rec_yds CRPS -0.144 [-0.257, -0.027] and rec -0.012 [-0.023, -0.002], but at the reference
  line its A band is over-confident (rec_yds 0.672 vs 0.636, rec 0.680 vs 0.627, n 761 / 773) and calibration misses
  the band (rec_yds ECE 0.0303 > 0.03; rec in-the-large -0.022, ECE 0.0303). The live sm-1 A band is over-confident the same way in every NFL
  market except rush_yds (pass_yds A 0.646 vs 0.526).
- pass_yds under-forecast persists (live bias -4.2 yds, in-the-large -0.046); the rolling level factor `dr` moves it
  to -3.6 and does not reach calibration. The RB snap-share fix `sn` removes rush_att's bias (-0.28 -> -0.00) and fixes
  its calibration (ECE 0.028) but CRPS is worse (+0.036 [-0.011, +0.082]) and the A band is over-confident.
- Book lines: no settled forward NFL prop is reachable (production learning.db: HTTP 401 with the available token);
  A7 7(d) is not evaluable for NFL.
