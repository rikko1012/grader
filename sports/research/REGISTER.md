# Scout Sports model register (sm-1 / mi-1 / sports-gp-1) - written and committed BEFORE any comparison

Registered 2026-10-23 by the model agent, before any candidate was fitted or scored. The commit that adds this file
precedes every result in `sports/research/results/model_*`. Changing anything below after results exist makes the
affected result **post-hoc / development evidence** and it must be labelled so; the locked test is scored once per
registered candidate set (section 9).

Owner rules applied: chronological train / tune+calibration / locked test grouped by game; promotion criteria before
comparisons; failed candidates stay shadow; no invented weights (every coefficient is fitted on train or tune and
reported); factors get influence only with measured effect; one distribution per prop for every line and both sides;
unconditional pOver / pUnder / pPush; grades from unrounded calibrated probabilities; stakes 0 until a registered gate
passes; replay-deterministic.

## 1. Markets

| Sport | Markets (canonical keys, sports/markets.py) | Box-score source |
|---|---|---|
| NBA | points, rebounds, assists, threes (3-pointers made) | ESPN summary box score (ds-1 `nba_pg`) |
| NFL | pass_yds, rush_yds, rec_yds, rush_att (carries), rec (receptions) | nflverse weekly player stats (ds-1 `nfl_pg`) |

Combos, touchdowns, double-doubles, spreads / totals are out of scope (capability gate refuses them).

## 2. Data (ds-1, sports/model/datasets.py)

- NBA: ESPN public site API, every final regular-season, play-in and playoff game of 2023-24, 2024-25, 2025-26 and the
  current 2026-27 season through 2026-10-22. Player rows: minutes, points, FGM/FGA, 3PM/3PA, FTM/FTA, OREB/DREB/REB,
  AST, STL, BLK, TOV, PF, +/-, starter flag, did-not-play + reason. Team totals per game. ESPN pickcenter game spread /
  total when present (provider and capture time NOT stated - treated as near-closing).
- NFL: nflverse-data releases 2022-2026: weekly player stats, snap counts (PFR id -> gsis id via players.csv),
  schedules (kickoff, roof, temp, wind, closing spread / total), play-by-play (red-zone targets / carries inside the
  20 and the 10; team plays; neutral pass rate). A player with offensive snaps but no stats row is a ZERO row (he
  played and recorded nothing); a player in neither file did not play.
- Point-in-time: a row may feed a forecast only if its game started before the forecast game AND its rule-based
  known_at (start + 4 h NBA, start + 6 h NFL) <= forecast_at. Historical forecast_at = game start - 60 min.

## 3. Eligible rows (defined from pre-game information only)

The distribution is CONDITIONAL ON PLAYING (pick'em platforms void a DNP; Kalshi settles an inactive player at a fair
price) - a player who does not play is not an outcome of the prop. The probability that an eligible player does not
play is reported separately (diagnostic), never mixed into pOver / pUnder.

- NBA: played (minutes > 0); >= 5 prior played games in ds-1; mean minutes of the last <= 10 prior played games >= 15.
- NFL (QB / RB / WR / TE; played = offensive snaps > 0 or a stats row): >= 3 prior played games; pool by market from
  the mean of the last <= 6 prior played games: pass_yds - position QB and mean pass attempts >= 15; rush_yds and
  rush_att - mean carries >= 6; rec and rec_yds - mean targets >= 3.

## 4. Partitions (by game start; every row of one game falls in one partition)

| Sport | Train | Tune + calibration | LOCKED test | Secondary locked (out of time) | Prospective |
|---|---|---|---|---|---|
| NBA | start < 2025-10-01 (2023-24, 2024-25) | 2025-10-01 <= start < 2026-02-01 | 2026-02-01 <= start < 2026-07-01 | - | forecasts after the sm-1 freeze |
| NFL | seasons 2022-2024 | 2025 weeks 1-9 | 2025 week 10 - postseason | 2026 weeks 1-7 (played before 2026-10-23) | forecasts after the sm-1 freeze |

NBA 2026-27 games before the freeze are used only in the final refit (never scored). History features of a row may
use earlier rows of any partition (they are inputs, not labels); every fitted parameter (population priors,
shrinkage strength, regression coefficients, dispersion, residual distributions) is fitted on TRAIN rows only;
distribution family, half-lives and calibration are chosen on TUNE only.

## 5. Candidates

Baselines
- **B0 hist-rate** - the player's last <= 10 prior played games: projection = their mean; distribution = their
  empirical CDF (CRPS); binary probability at a line = (games over + 0.5) / (n + 1). NLL not defined (reported n/a).
- **B1 history-shape** (the data branch's incumbent `projection.baseline` + `fair.py`): recency-weighted mean (half-life
  10 games NBA / 5 NFL, last <= 40 / 20 games), distribution = fair.py shape with `dispersion.json` as shipped. Note:
  dispersion.json was fitted on 2025-26 NBA and 2025 NFL box scores, which overlap the tune and test partitions - this
  favours B1; it is kept as the incumbent reference unchanged.

Statistical-only candidate **sm-1** (no market input of any kind)
- NBA mean: E[minutes] x per-minute rate x exp(beta . x). E[minutes] = exponentially weighted prior minutes (half-life
  h_m in {3, 5, 10} games, chosen on tune). Rate = empirical-Bayes shrinkage (sum_w stat + k * r_pos) / (sum_w min + k)
  with weights of half-life h_r in {10, 20, 40}; r_pos = train population per-minute rate of the position group (G /
  F / C), k fitted on train by Poisson deviance. Minutes and rate are estimated separately and multiplied ONCE (one
  measure of opportunity, one of conversion - no second opportunity measure is stacked).
- NFL mean: E[opportunity] x efficiency x exp(beta . x): rec = E[targets] x catch rate; rec_yds = E[targets] x yards
  per target; rush_att = E[carries]; rush_yds = E[carries] x yards per carry; pass_yds = E[attempts] x yards per
  attempt. E[opportunity] = EW mean (half-life in {2, 4, 8} games, chosen on tune); efficiency shrunk to the train
  position prior with k fitted on train.
- beta . x: candidate features (sports/features.py, status "available + candidate" with a computed definition) enter
  only through the selection protocol below. beta fitted on train by a Poisson log-link GLM with the base mean as
  offset (yards markets use the same log-link quasi-Poisson fit).
- **Feature selection protocol** (tune only): each candidate feature is added alone to the offset-only base and fitted
  on train; it is KEPT if its tune CRPS improvement has a game-clustered 95% bootstrap interval entirely below 0. The
  kept features are then fitted jointly; the joint model is used only if its tune CRPS beats the base with an
  interval entirely below 0, otherwise sm-1 = the base. Every feature's tune effect (point + interval) is published
  in the registry's `observed_effect`, kept or not.
- **Distribution families** (one per market, chosen on tune by mean CRPS; all centred on the same sm-1 mean):
  counts (NBA points / rebounds / assists / threes, NFL rec / rush_att): Poisson; negative binomial with
  var = mu + alpha mu^2 (alpha ML-fitted on train); opportunity mixture = joint distribution computed by deterministic quadrature (amendment A1) of opportunity (minutes / targets / carries / attempts drawn from the train distribution of actual /
  expected opportunity) then outcome given opportunity (NB conditional on minutes; receptions ~ Binomial(targets,
  catch rate); threes ~ Binomial(3PA, 3P%)); empirical-ratio (train ratios y / mu of the same mu-quintile).
  yards (NFL pass / rush / rec yards): normal with var = a mu + b mu^2 (train fit); empirical-ratio; opportunity
  mixture (opportunity draw, then yards = n * efficiency + sqrt(n) * sigma * e with e drawn from the train
  standardised per-game residuals - right skew kept; receptions <= targets and completions <= attempts in every draw).
- **Calibration cal-1** (fit on tune, frozen for test): per market a mean factor c (mu -> c mu) and a dispersion scale
  s (spread around the mean scaled by s) on the grids c in 0.90..1.10 step 0.01, s in 0.80..1.30 step 0.02, searched
  by coordinate descent (c with s = 1, then s, then c again; amendment A1) by tune CRPS. Probabilities used for grades are the calibrated distribution's.

Market track (statistical-only and market candidates are never pooled into one claim)
- **mi-hist** (history study only): sm-1 + game spread / total features (ESPN pickcenter NBA, nflverse closing lines
  NFL). Labelled "market-informed (closing game lines; available about kickoff)". Same selection protocol, separate
  table; never used as evidence for the statistical-only candidate.
- **sharp-2** (foundation, market-only, Kalshi midpoint ladder + dispersion.json shape) and **mi-1** (market-informed:
  location solved from the same eligible Kalshi rungs, shape = sm-1's calibrated distribution for that player) are
  FORWARD ONLY: no historical player-prop quotes exist in ds-1 or the store (sports/AUDIT.md). The anchor is Kalshi
  only; the target books (PrizePicks, Underdog) are never in the anchor and the target line / price is never an input.
  When a target-book offer exists, mi-1 and sharp-2 use the same rungs (same eligibility: sharp2.eligible_rung).

## 6. Metrics (all declared now)

- Projection: MAE, RMSE, bias (mean prediction - actual) of the mean.
- Probability (primary): mean **CRPS** over the integer support = sum over every threshold k of the Brier score of
  P(X <= k); it scores every alternate line at once. Secondary: NLL of the observed integer (P floored at 1e-6),
  log-loss and Brier of the binary event X > L_ref at the reference line, central 50 / 80 / 90% interval coverage.
- Reference line L_ref (defined pre-game, independent of every candidate): floor(B0 mean) + 0.5 (half point, no push).
  Push behaviour is checked separately at the whole line round(B0 mean).
- Reliability: 10 equal-width bins of P(X > L_ref), bins with n >= 50; ECE = sum n_b |mean p_b - hit_b| / n;
  calibration-in-the-large = mean p - hit rate.
- Uncertainty: paired differences (candidate - baseline) with **game-clustered bootstrap** 95% intervals (B = 2,000,
  seed 20261023); secondary **date-block** bootstrap (NBA: ISO week blocks; NFL: season-week blocks).
- Slices (declared): sport, stat, side (favoured side at L_ref: Over if P(X > L_ref) > 0.5), role (NBA: started his
  previous game yes / no; NFL: position QB / RB / WR / TE), history depth (NBA prior games 5-9 / 10-19 / 20-39 / 40+;
  NFL 3-5 / 6-11 / 12+), probability band of P(X > L_ref) (< 0.4 / 0.4-0.6 / >= 0.6), season type (regular /
  postseason). Price bands exist only for real quotes (forward).

## 7. Promotion criteria (history gate H, per market, on the LOCKED test)

sm-1 is **history-validated** for a market only if ALL hold:
1. CRPS difference vs B1 and vs B0: 95% game-clustered interval entirely below 0.
2. Reference-line log-loss difference vs B1: point estimate <= 0.
3. Calibration at L_ref: |calibration-in-the-large| <= 0.02 and ECE <= 0.03.
4. 80% central interval coverage within [0.76, 0.84].
5. No declared slice with n >= 300 whose CRPS difference vs B1 has an interval entirely above 0.
6. NFL: the secondary locked test (2026 weeks 1-7) CRPS difference vs B1 has point estimate < 0.
A market that fails stays shadow (research only, projections may be displayed with their validation state).

History validation is NOT a stake gate. **Forward gate F** (per sport x market group, on forecasts recorded after the
freeze, at REAL offered lines, settled): >= 300 distinct settled props from >= 100 distinct games; chosen-side
calibration |mean p - hit rate| <= 0.03 with the game-clustered interval of (hit - p) containing 0; log-loss at the
offered line better than sharp-2 on the same props (interval entirely below 0) or, where sharp-2 has no anchor,
better than B1. Reviews only at 300 / 600 / 1,000 settled-prop windows. Until F passes for a group: stake 0 and the
grade policy shows the validation state (section 8). Promotion additionally needs the existing owner release
process - nothing auto-promotes.

## 8. Grade policy sports-gp-1 (separate from every esports band)

- Input: the calibrated distribution's UNROUNDED unconditional probability of each OFFERED side at the offer's line.
  A side that is not offered is `unavailable` (no probability shown as tradable, cannot enter a slip).
- Chosen side = the offered side with the higher unconditional probability.
- Bands (chosen-side unconditional probability p; anchored on published Underdog break-evens, sports/AUDIT.md 1 -
  lowest 53.45%, highest 55.59%): A p >= 0.60; B 0.5559 <= p < 0.60; C 0.5345 <= p < 0.5559; below 0.5345 = "Lean
  Over / Lean Under". PrizePicks payouts are unpublished, so no band claims a PrizePicks break-even.
- Output fields (separate): `validation_state` (unvalidated / history-validated / forward-validated), `band` (the
  letter the probability falls in), `grade` (= band only when validation_state is forward-validated, else None),
  `stake_units` (0 until gate F; then sports-sp-1), `stake_reason` (why 0), `data_quality` (codes: thin history,
  role change, teammate absence unknown, no Kalshi anchor ...). A 0U pick keeps its letter + reason once validated; it
  never becomes a Lean because its stake is 0.
- Concise member text while unvalidated: "Projection only - model not yet validated" (no confidence words).

## 9. Prospective freeze, inspection and retraining

- The forward artifact = the configuration chosen on tune (family, half-lives, kept features, cal-1), refit on every
  ds-1 row through 2026-10-22; its sha256 is recorded in MODEL_CARD files and in each Forecast (artifact_version).
- Forward forecasts count only if forecast_at > freeze time and < event start. Same inputs + artifact + seed replay
  the identical Forecast.
- The locked test is evaluated ONCE for this registered candidate set; results are committed as is. Any re-run after
  changes is labelled post-hoc.
- Retraining (worker only, `train learn` checks it): cadence weekly (NBA every 7 days; NFL on Tuesday after a week's
  games) OR sample trigger >= 2,000 new settled NBA player-games / >= 600 NFL player-games since the last fit. A
  retrain makes a CHALLENGER artifact in the registry (same frozen configuration, new data); the champion is never
  replaced automatically.
- Every settlement updates the scorecard and the diagnostic queue (largest |PIT - 0.5| misses, settlement
  corrections, voids) automatically in the worker.

## Amendments (each dated, all BEFORE any comparison was run)

- **A1 (2026-10-23, before any fit or score):** (a) the opportunity-mixture family is computed by deterministic
  quadrature over opportunity counts / 40 quantiles of the train opportunity-ratio distribution / 25 quantiles of the
  train standardised residuals instead of seeded Monte-Carlo draws - same model, exact and replay-identical without a
  seed; (b) cal-1 searches its two grids by coordinate descent instead of the full product (cost); (c) ESPN summaries
  carry the CURRENT injury report (dates of the read day, 2026-10-23), not the report at game time, and historical
  NBA pickcenter game lines are empty for the games checked - so NBA historical injury context is taken only from
  box-score did-not-play rows, and NBA mi-hist runs only if game lines exist for >= 50% of eligible rows (coverage is
  reported either way); (d) NFL rush_att has no separate opportunity under it, so its mixture family is not defined
  (Poisson / NB / empirical-ratio compared).
