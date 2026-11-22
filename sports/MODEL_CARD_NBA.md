# Model card - Scout Sports sm-1, NBA (statistical-only)

Generated 2026-10-23 from the committed study results (`sports/research/results/model_study_nba_*.json`) and artifact `sports/model/artifacts/sm1_nba.json` (sha `5ec8adf73a6dad18`, fitted 2026-10-23T23:00:17Z on data known at 2026-10-23T23:00:00Z). Registered protocol: `sports/research/REGISTER.md` (+ amendments A1-A3, all made before any locked-test score). Data rights: ESPN and Kalshi display rights are owner-attested (owner attestation 2026-10-23); nflverse data is CC-BY 4.0 (FTN-charted fields CC-BY-SA 4.0).

## What the model is

E[minutes] (EW mean of prior minutes) x per-minute rate (empirical-Bayes shrinkage to the train position-group rate, k fitted on train) x exp(intercept + fitted GLM terms of the features that passed the registered tune test), then one fitted outcome distribution per prop (family chosen on tune) and cal-1 (mean factor c, spread scale s fitted on tune). Conditional on playing: pick'em platforms void a DNP; the DNP share of rotation players in ds-1 train is 7.5% (reported, never mixed into pOver / pUnder).

Every probability is read from ONE distribution per prop: pOver = P(X > L), pUnder = P(X < L), pPush = P(X = L), unconditional (sum 1); the conditional-on-no-push values are separate named fields. Replay: the forward path computes each PMF from its saved feature row; a replay from saved inputs is bit-identical (200 / 200 sampled live forecasts on 2026-10-23).

## Data (ds-1)

ESPN public site API box scores: 2023-24, 2024-25, 2025-26 (regular, play-in, playoffs) and 2026-27 to 2026-10-22: 3,974 games with box scores (4 playoff summaries answered HTTP 404 after 4 attempts), ~105k player rows. Point-in-time rule: a row feeds a forecast only if its game started before the forecast game and start + 4 h <= forecast time.

## Per market

### Points (`points`)

- Validation state: **history-validated** (history gate H passed). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [3, 10], family **ratio** (tune CRPS poisson 3.6797, negbin 3.5293, mixture 3.5116, ratio 3.4637), cal-1 c = 0.98, s = 0.98; features kept: nba.usage, nba.role_stability, nba.starter_prev, nba.opp_pace, nba.teammate_absent_sameday.
- Rows: train 40770 (2571 games), tune 11976 (725 games), LOCKED test 9834 (593 games); forward artifact fit on 62842 rows / 3905 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 3.467 | 3.583 | 3.752 |
| MAE | 4.914 | 5.068 | 5.120 |
| log-loss at L_ref | 0.665 | 0.688 | 0.703 |
| Brier at L_ref | 0.236 | 0.247 | 0.255 |
| 80% PIT coverage | 0.792 | 0.769 | 0.756 |
| RMSE | 6.319 | 6.490 | 6.562 |
| bias (mean - actual) | -0.187 | -0.105 | 0.080 |
| NLL | 3.177 | 3.226 | n/a |
| ECE / in-the-large at L_ref | 0.018 / -0.0130 | 0.041 / +0.0228 | 0.066 / +0.0108 |

- CRPS difference sm-1 - B1: -0.1153, game-clustered 95% [-0.1341, -0.0951], date-block [-0.1523, -0.0877]; sm-1 - B0: -0.2850 [-0.3075, -0.2632].
- Log-loss at L_ref sm-1 - B1: -0.0226 [-0.0265, -0.0184]; push check at the whole line: predicted 0.064 vs observed 0.067.
- Market track: not run (game lines cover < 50% of eligible train rows (amendment A1c)).
- Declared slices with the weakest CRPS difference vs B1: role:started_prev n=5776 -0.0667 [-0.0917, -0.0416]; band:0.4-0.6 n=5967 -0.0707 [-0.0910, -0.0514]; side:under n=7025 -0.0984 [-0.1185, -0.0786].
- Caveat: `nba.teammate_absent_sameday` (kept) is known only at the injury report; history reconstructs it from box-score did-not-play rows; forward it comes from ESPN's injury report (Out) and is UNKNOWN (train mean + reason code teammate_absence_unknown) without a fresh report. POST-HOC sensitivity (not registered; the test had been inspected): without it CRPS sm-1 - B1 = -0.0726 [-0.0872, -0.0573] - the gain is not mainly that feature.
- Limitation: the empirical-ratio family stores 200 ratio quantiles per mean quintile, so its probabilities move in steps of about 0.5 percentage points.

### Rebounds (`rebounds`)

- Validation state: **history-validated** (history gate H passed). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [3, 20], family **negbin** (tune CRPS poisson 1.3781, negbin 1.3700, mixture 1.3704, ratio 1.3719), cal-1 c = 0.99, s = 0.98; features kept: nba.role_stability, nba.opp_pace, nba.opp_positional_defence.
- Rows: train 40770 (2571 games), tune 11976 (725 games), LOCKED test 9834 (593 games); forward artifact fit on 62842 rows / 3905 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 1.378 | 1.398 | 1.486 |
| MAE | 1.971 | 1.995 | 2.040 |
| log-loss at L_ref | 0.663 | 0.671 | 0.700 |
| Brier at L_ref | 0.235 | 0.239 | 0.253 |
| 80% PIT coverage | 0.787 | 0.766 | 0.756 |
| RMSE | 2.574 | 2.604 | 2.661 |
| bias (mean - actual) | -0.004 | 0.025 | 0.066 |
| NLL | 2.252 | 2.276 | n/a |
| ECE / in-the-large at L_ref | 0.009 / -0.0006 | 0.012 / +0.0053 | 0.068 / +0.0201 |

- CRPS difference sm-1 - B1: -0.0196, game-clustered 95% [-0.0258, -0.0134], date-block [-0.0253, -0.0133]; sm-1 - B0: -0.1081 [-0.1175, -0.0987].
- Log-loss at L_ref sm-1 - B1: -0.0087 [-0.0120, -0.0055]; push check at the whole line: predicted 0.167 vs observed 0.159.
- Market track: not run (game lines cover < 50% of eligible train rows (amendment A1c)).
- Declared slices with the weakest CRPS difference vs B1: depth:5-9 n=51 +0.0162 [-0.1045, +0.1695]; season:postseason n=1449 -0.0112 [-0.0249, +0.0011]; role:started_prev n=5776 -0.0130 [-0.0209, -0.0041].

### Assists (`assists`)

- Validation state: **history-validated** (history gate H passed). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [3, 10], family **negbin** (tune CRPS poisson 0.9953, negbin 0.9939, mixture 0.9946, ratio 0.9967), cal-1 c = 1.02, s = 1.0; features kept: nba.role_stability, nba.starter_prev, nba.team_pace.
- Rows: train 40770 (2571 games), tune 11976 (725 games), LOCKED test 9834 (593 games); forward artifact fit on 62842 rows / 3905 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 1.024 | 1.037 | 1.102 |
| MAE | 1.495 | 1.503 | 1.528 |
| log-loss at L_ref | 0.658 | 0.667 | 0.693 |
| Brier at L_ref | 0.233 | 0.237 | 0.249 |
| 80% PIT coverage | 0.787 | 0.768 | 0.745 |
| RMSE | 1.982 | 2.002 | 2.039 |
| bias (mean - actual) | 0.073 | 0.012 | 0.037 |
| NLL | 1.910 | 1.930 | n/a |
| ECE / in-the-large at L_ref | 0.019 / +0.0186 | 0.017 / +0.0043 | 0.056 / +0.0170 |

- CRPS difference sm-1 - B1: -0.0126, game-clustered 95% [-0.0164, -0.0088], date-block [-0.0183, -0.0077]; sm-1 - B0: -0.0782 [-0.0846, -0.0717].
- Log-loss at L_ref sm-1 - B1: -0.0086 [-0.0114, -0.0059]; push check at the whole line: predicted 0.230 vs observed 0.221.
- Market track: not run (game lines cover < 50% of eligible train rows (amendment A1c)).
- Declared slices with the weakest CRPS difference vs B1: season:postseason n=1449 -0.0059 [-0.0141, +0.0019]; band:>=0.6 n=895 -0.0061 [-0.0207, +0.0093]; role:started_prev n=5776 -0.0093 [-0.0146, -0.0038].

### 3-pointers made (`threes`)

- Validation state: **history-validated** (history gate H passed). Not forward-validated: stakes 0, grade withheld (sports-gp-1).
- Configuration (tune): half-lives [3, 20], family **negbin** (tune CRPS poisson 0.6525, negbin 0.6509, mixture 0.6510, ratio 0.6567), cal-1 c = 0.99, s = 1.0; features kept: none (base only).
- Rows: train 40770 (2571 games), tune 11976 (725 games), LOCKED test 9834 (593 games); forward artifact fit on 62842 rows / 3905 games.

| LOCKED test | sm-1 | B1 history-shape | B0 hist-rate |
|---|---|---|---|
| CRPS (primary) | 0.647 | 0.652 | 0.697 |
| MAE | 0.970 | 0.972 | 0.993 |
| log-loss at L_ref | 0.603 | 0.607 | 0.632 |
| Brier at L_ref | 0.211 | 0.213 | 0.224 |
| 80% PIT coverage | 0.799 | 0.783 | 0.759 |
| RMSE | 1.313 | 1.323 | 1.354 |
| bias (mean - actual) | -0.004 | -0.002 | 0.020 |
| NLL | 1.421 | 1.431 | n/a |
| ECE / in-the-large at L_ref | 0.016 / +0.0073 | 0.024 / +0.0069 | 0.050 / +0.0196 |

- CRPS difference sm-1 - B1: -0.0059, game-clustered 95% [-0.0080, -0.0038], date-block [-0.0085, -0.0036]; sm-1 - B0: -0.0508 [-0.0554, -0.0460].
- Log-loss at L_ref sm-1 - B1: -0.0031 [-0.0052, -0.0010]; push check at the whole line: predicted 0.359 vs observed 0.352.
- Market track: not run (game lines cover < 50% of eligible train rows (amendment A1c)).
- Declared slices with the weakest CRPS difference vs B1: depth:10-19 n=87 +0.0042 [-0.0188, +0.0268]; depth:5-9 n=51 +0.0040 [-0.0147, +0.0236]; role:started_prev n=5776 -0.0040 [-0.0065, -0.0014].

## Intended use and limits

- Research / shadow forecasts only. A history-validated market is NOT a betting-edge claim: no forecast of this version has settled at a real offered line; gate F (>= 300 settled props, >= 100 games, chosen-side calibration, log-loss vs sharp-2 / mc-1) decides that, at the 300 / 600 / 1,000 windows.
- Historical evaluation uses a pre-game reference line (floor of the last-10 mean + 0.5), not book lines: books set lines with information the model lacks, so accuracy at offered lines can be lower.
- Players outside the validated population (NBA < 5 prior games or < 15 L10 minutes; NFL < 3 games or below the pool thresholds) still get a research forecast, reason-coded `outside_validated_population` / `thin_history`.
- Every factor of the owner's list is in `sports/features.py` with its status and measured tune effect; unavailable (licensed) factors are absent, never imputed.


## Walk-forward fine-tuning tw-1 (REGISTER A7, 2026-10-25) - nothing switched

Protocol: `sports/research/REGISTER.md` amendment A7 (committed 6170ab6 before any A7 fit or score); code
`sports/model/tune.py`; results `sports/research/results/model_tw1_nba.json` (packaged ds-1
`history_ds1_2026-10-23.db.gz`, as_of 2026-10-25T18:00Z; a re-run reproduces every number). Every fitted parameter is
refit at each origin on games known before it; calibration layers read only earlier out-of-sample forecasts. This is
honest out-of-sample evidence for the PROCEDURE but weaker than a never-seen period: the windows were scored before
(labelled "walk-forward (procedure out-of-sample; windows previously inspected)"). 95% intervals are game-clustered
(B = 2,000). Criteria A7 7: (a) CRPS interval below 0, (b) |in-the-large| <= 0.02 and ECE <= 0.03 at L_ref, (c) PIT
80% coverage 0.76-0.84, (e) every band with n >= 100 within 0.03 of its mean probability.

Span: the last 60 game-days of ds-1 (warm-up = the 30 before); selection block 2026-03-31..04-17 (15 game-days);
evaluation block 2026-04-18..2026-10-22 = the 2026 playoffs (1,339 rows / 81 games) + the 2026-27 opening days
(262 rows / 16 games).

| Market | Arm (h / k) | Eval rows / games | CRPS cand - live [game 95%] | in-the-large L_ref live / cand | ECE live / cand | PIT cov80 live / cand | bias live / cand | A band p / hit (cand) | Pass (a b c e) |
|---|---|---|---|---|---|---|---|---|---|
| points | rc (inf) | 1601 / 97 | +0.0256 [+0.0149, +0.0369] | +0.076 / +0.094 | 0.069 / 0.085 | 0.774 / 0.774 | +0.95 / +1.25 | 0.663 / 0.668 (n 638) | n n Y n |
| assists | rc (56 d) | 1601 / 97 | -0.0151 [-0.0211, -0.0100] | +0.109 / +0.092 | 0.109 / 0.092 | 0.784 / 0.794 | +0.54 / +0.44 | 0.671 / 0.713 (n 717) | Y n Y n |
| rebounds | rc (7 d) | 1601 / 97 | +0.0072 [+0.0013, +0.0148] | +0.016 / +0.018 | 0.018 / 0.024 | 0.788 / 0.788 | +0.19 / +0.19 | 0.680 / 0.667 (n 777) | n Y Y n |
| threes | rc (inf) | 1601 / 97 | +0.0009 [+0.0004, +0.0015] | +0.055 / +0.058 | 0.054 / 0.057 | 0.813 / 0.814 | +0.16 / +0.17 | 0.732 / 0.771 (n 917) | n n Y n |

Live grade bands at L_ref (evaluation block):  points: A 742 p 0.664 hit 0.685, B 391 p 0.578 hit 0.560, C 178 p 0.545 hit 0.567, Lean 290 p 0.518 hit 0.479; assists: A 674 p 0.670 hit 0.708, B 378 p 0.577 hit 0.590, C 200 p 0.545 hit 0.525, Lean 349 p 0.518 hit 0.579; rebounds: A 725 p 0.681 hit 0.666, B 348 p 0.578 hit 0.566, C 190 p 0.544 hit 0.521, Lean 338 p 0.517 hit 0.500; threes: A 926 p 0.732 hit 0.771, B 303 p 0.578 hit 0.627, C 134 p 0.546 hit 0.604, Lean 238 p 0.517 hit 0.525

- **No NBA market passes; nothing switched.** The recency-weighted recalibration `rc` cannot fix what is wrong in this
  window: the evaluation block is the playoffs, where the live model over-projects (points bias +0.48, in-the-large at
  L_ref +0.05; assists +0.51 / +0.11) - a regime change at the playoff start that the calibration fitted on the late
  regular season (c 1.02 for points) moves the wrong way. assists' `rc` improves CRPS (-0.015 [-0.021, -0.010]) but
  stays far out of band (in-the-large +0.092).
- **Post-hoc (not evidence, not a gate):** the 2026-27 opening days show live points bias +3.3 (EW minutes 27.6 vs
  24.7 played). Registered as A7b and tested on the 2024-25 and 2025-26 openings (`model_so1_nba.json`): earlier
  openings show only +0.27 / +0.42 points bias and the regular-season-minutes challenger `so-1` does not pass:

| Market | Pooled rows / games | CRPS so-1 - live [game 95%] | bias live / so-1 | in-the-large live / so-1 | Pass (a b c e) | 2026-27 opening (post-hoc) live bias / so-1 |
|---|---|---|---|---|---|---|
| points | 3592 / 229 | -0.0063 [-0.0253, +0.0131] | +0.341 / +0.261 | +0.018 / +0.014 | n Y Y Y | +3.34 / +2.92 |
| assists | 3592 / 229 | -0.0011 [-0.0055, +0.0031] | +0.125 / +0.100 | +0.023 / +0.020 | n Y Y Y | +0.69 / +0.59 |
| rebounds | 3592 / 229 | +0.0056 [-0.0026, +0.0138] | +0.109 / +0.089 | +0.015 / +0.013 | n Y Y n | +0.37 / +0.23 |
| threes | 3592 / 229 | -0.0021 [-0.0049, +0.0007] | +0.005 / -0.001 | -0.001 / -0.003 | n Y Y Y | +0.31 / +0.27 |

- **Book lines (reconstructed, not evaluable):** `model_tw1_bookline_nba_reconstructed.json` - 435 settled props
  (8 pushes excluded) from 7 games of one slate (2026-10-23), last pre-start production sm-1 forecast per prop,
  settled now from ESPN box scores. Over-rate 0.455 vs mean P(Over) 0.465 (share favouring Over 0.375, |s - o| 0.08);
  A band p 0.690 vs hit 0.495 (n 208, hit - p game-clustered [-0.263, -0.117]); B 0.576 / 0.571 (105); C 0.544 /
  0.543 (46). Below A7 7(d)'s 100 props from 20 games per market, so nothing is decided on it; it is the first real
  book-line warning that the strongest disagreements with the book (A band) are over-confident. bl-1 could not be
  fitted (no prop had settled before these forecasts). Production settled props (service learning.db) were not
  reachable from this workstation (HTTP 401).

## nba8-1: the eight stats sm-1 does not model (NBA_STATS8, 2026-11-21)

`sports/model/nba8.py`, frozen artifact `artifacts/nba8_nba.json` (sha 69e5e27b376e5485, ds-1 through 2026-10-22).
PRA, P+R, P+A, R+A, steals, blocks, Blks+Stls, turnovers. Per component: EW minutes x shrunk per-minute rate x tune
calibration; outcome = a shared minutes draw (40 train quantiles) with negative-binomial components and the fitted
within-player residual correlation per position group, so a combo is a JOINT distribution (history: joint beats
independent CRPS for PRA / P+R / R+A / Blks+Stls, game-clustered CIs below 0; P+A CI includes 0).

Real-line walk-forward (pre-registered, cs2_scout_ops/logs/audit_1121/round2/nba-stats/PREREG.md + ADDENDUM.md):
closing PrizePicks standard / Underdog balanced lines, settled from ESPN box scores, TEST game days 2026-11-10..20
(partial: the offers route could not serve every recent event), pass = graded hit >= 53% with game-clustered CI low
> 50%, >= 100 rows / 20 games, A8a balance <= 0.15, beats the L10-vs-line side.

| Stat | TEST graded W-L | hit | game 95% CI | games | A8a (all rows) | L10 baseline | DEV graded | Verdict |
|---|---|---|---|---|---|---|---|---|
| steals | 273-149 | 64.7 | 59.8-69.9 | 48 | 0.026 | 58.2 | 772-508 (60.3) | PASS |
| pr | 346-296-2 | 53.9 | 49.3-58.6 | 55 | 0.239 | 49.0 | 873-830 (51.3) | fail |
| pra | 378-366 | 50.8 | 45.9-55.6 | 54 | 0.139 | 48.4 | 1284-1250 (50.7) | fail |
| ra | 303-281-16 | 51.9 | 47.3-56.5 | 54 | 0.196 | 53.0 | 778-713 (52.2) | fail |
| pa | 291-311-1 | 48.3 | 43.5-53.5 | 55 | 0.243 | 47.1 | 701-671 (51.1) | fail |
| blocks | 43-24 | 64.2 | 50.0-78.3 | 38 | 0.235 | 51.7 | 112-79 (58.6) | short / unbalanced |
| stocks | 51-37 | 58.0 | 46.9-68.8 | 37 | 0.198 | 51.8 | 167-138 (54.8) | short / unbalanced |
| turnovers | 18-15 | 54.5 | 37.9-69.1 | 14 | 0.022 | 46.2 | 86-73 (54.1) | short |

Only steals is gradable (capabilities.NBA_STATS8_PASSED); it is lettered like points (rg-1 letter, 0U, model_version
nba8-1, raw chance - the sm-1 pc-1 / cal-mi-1 layers do not apply). Caveat: the steals hit rate comes from priced
Underdog lines where nba8-1 takes the book's short-priced side 92-95% of the time (that favourite hit 63.6% DEV /
67.5% TEST); at even-money lines (PrizePicks standard + Underdog 1.0x) steals went 168-169 DEV and 61-47 TEST.
Rollback: NBA_STATS8=0 (cap-2 exactly as before; no nba8-1 forecast is written).
