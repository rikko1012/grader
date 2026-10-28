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
- **A2 (2026-10-23, after a development run on train / tune only, before any locked-test score):** (a) the sm-1 base
  includes a fitted intercept (train) - without it any feature with a non-zero mean passed the tune test by acting as
  a level shift (seen in development: `postseason` "passed" on a tune partition with no postseason game); (b) a
  feature that is constant or has < 30 known values on the tune rows is recorded "not evaluable on tune" and cannot be
  kept. No locked-test row had been scored.
- **A3 (2026-10-23, after the NFL tune-partition dry run of the scoring code, before any locked-test score):** interval
  coverage of a DISCRETE forecast is measured with the randomised PIT (the expected share of [F(y-), F(y)] inside
  the central band; exactly the nominal level for a calibrated forecast). The inclusive quantile interval
  [q_(1-l)/2, q_(1+l)/2] over-covers by construction when atoms are large (receptions: 0.90 "80%" coverage on tune
  for a forecast whose PIT is calibrated) - criterion 4 would have rejected every count market for a reason that is
  not miscalibration. The inclusive-interval number is still reported (`cov80_inclusive_interval`). Criterion 4's
  band [0.76, 0.84] is unchanged.
- **A4 (2026-10-24, integration repair; before any mkt-fanduel / mkt-betrivers forecast exists - the code that can
  write one lands in the same commit as this text):** the direct book feeds FanDuel (own sbapi feed, Ohio) and
  BetRivers (Kambi offering API, Ohio), collected by the canonical ingestion loop (`sports.worker` source `books`)
  and stored as sc-1 offers with platform `fanduel` / `betrivers`, are judged by EXACTLY the per-book rule section 10
  gives The Odds API books (both sides at the same line, decimal odds > 1, multiplicative de-vig on that exhaustive
  pair only, age from the book's own change time when it publishes one - BetRivers changedDate - else observed age,
  <= 15 min and before the start; ordinary main lines only - their alternate lines are variant `alternate` and never
  compatible). They are evaluated ONLY as single-source candidates `mkt-fanduel` / `mkt-betrivers`. They are NOT
  sources of mc-1 or mi-1: those two keep the source set registered in section 10 (Kalshi, Polymarket, each Odds API
  book), so no mc-1 / mi-1 number changes because a direct feed was added and nothing is refitted. Adding them to the
  consensus would need its own dated amendment before any comparison. OddsPapi stays excluded (section 10).

## 10. Market candidates (owner decision 2026-10-23: every book is a primary source) - registered before any evaluation

All forward only (no historical player-prop quotes exist). Implemented in `sports/model/market.py`; sources in
`sports/books/`.
- **mc-1 market-only consensus.** Quote eligibility per source: Kalshi = `sharp2.eligible_rung`; Polymarket =
  two-sided YES quote, spread <= 0.05, liquidity > 0, observed before the start and <= 15 min before the forecast;
  The Odds API (each book separately) = both sides at the same line, decimal odds > 1, de-vigged multiplicatively on
  that exhaustive pair only, age from the book's last_update (else observed age) <= 15 min; Thunderpick and OddsPapi
  excluded until their format is verified from a live answer. Only the same canonical event, player, stat, full-game
  scope and ordinary variants (capabilities.compatible). Every source gives P(Over | no push) at its line (books
  refund pushes); Polymarket resolves a non-playing player NO, so its midpoint is divided by (1 - d), d = the frozen
  DNP share in `sports/model/artifacts/market_mc1.json` (NBA measured on ds-1 TRAIN rows; NFL 0, reason-coded).
  Lines are translated with the foundation's dispersion.json shape (fair.at_line), never sm-1. Per source the quote
  nearest the target line (ties: lower line). Combination: equal-weight mean of log-odds over sources (weights fixed
  now; nothing estimated). 1 source = "single source", >= 2 = "consensus". The target's own platform is never a source
  for it. Unconditional output with the shape's push mass at a whole target line.
- **mkt-<source>**: the same rule with one source (each book evaluated on its own). Also the direct book feeds
  `mkt-fanduel` / `mkt-betrivers` (amendment A4: single-source only, never in mc-1 / mi-1).
- **mi-1 market-informed**: sm-1's calibrated PMF for the player, shifted by one mean factor c fitted (least squares on
  log-odds) to the same eligible sources' P(Over | no push).
- Evaluation: on the same settled target offers, separately per version (sm-1, mi-1, mc-1, mkt-*, sharp-2): log-loss
  and Brier at the offered line, chosen-side calibration (hit - p, game-clustered interval), counts by source. Gate F
  (section 7) applies to sm-1, mi-1 and mc-1 alike. A source that changes the consensus is reported with the cases
  where it actually moved the anchor (sources field of each forecast).
- Stake policy sports-sp-1 (reachable only after gate F): A 1.0U, B 0.5U, C / Lean 0U; any data-quality code = 0U
  with its reason; the letter stays.

## Amendment A5 - factor round 2 / candidate sm-2 (2026-10-24, registered BEFORE any round-2 factor was scored)

Owner 2026-10-24: "make sure grades are taking into account every possible factor". Grades are sports-gp-1 bands of
the calibrated probability, so a factor reaches a grade only by entering the distribution through a tested model
version. Standing rule: a factor moves grades ONLY if it passes a chronological test; every other factor is shown at
weight 0 ("tested - no measured effect") or as not available with its reason; no weight is ever set by hand.

What existed when this was written: the code that computes the round-2 factors (`sports/model/featurize2.py`, ds-2
extras tables `nfl_pgx` / `nfl_tgx` from the same nflverse play-by-play pass, registry fr-2 in `sports/features.py`) and
the protocol code (`sports/model/study2.py`). Value ranges / coverage of the new columns were inspected (no target,
no score). No round-2 factor had been fitted against an outcome or scored.

1. **Candidates (fr-2, registry entries with `round: 2`).** Factors that were derivable but not built in fr-1, plus
   factors whose forward source changed (NFL wind / temperature now have the NWS kickoff forecast; game spread / total
   are now tested as model inputs, not only in the separate mi-hist track). Round-1 results of every other factor
   STAND and are not re-tested (no second look at the same data). NBA: ts_pct, ft_rate, three_share, ast_to,
   opp_3pa_allow, team_ortg, opp_drtg, games_in_4, travel, starter_today, teammate_absent_usage; game lines (game
   total, |spread|, implied team total). NFL: route_proxy, target_share_trend, redzone_share, inside10, carry_share,
   carry_share_trend, scramble_rate, qb_designed_runs, qb_adot, pass / rush / rec EPA per opportunity, team EPA / play,
   team explosive rate, opponent defensive EPA / success / explosive allowed, neutral seconds per play, teammates out
   (target share, carry share), rest days, post-bye, travel, wind, temperature; game lines (spread, implied total, game
   total, |spread|). Players' own injury statuses have no point-in-time history (A1c): status "forward only (logged)",
   recorded with every shadow forecast, testable only on forward data.
2. **Point in time.** Own-player history = earlier rows known at the forecast (start + 4 h NBA / 6 h NFL <= forecast,
   forecast = start - 60 min); team / opponent history = final games public before min(start - 60 min, as_of); schedule
   facts known in advance; same-game facts (starter tonight, teammates out tonight) are used as known at the lineup /
   inactive report - NFL inactives are published ~90 min before kickoff (before the forecast time); NBA same-game
   facts are a stated assumption exactly like sm-1's nba.teammate_absent_sameday, and forward they come from the report
   or are UNKNOWN (missing indicator, data-quality code). Game lines: nflverse closing line (history), ESPN pickcenter
   NBA 2025-26 (history); forward = the games-store pickcenter snapshot observed before the forecast. Unit tests
   enforce each of these (sports/tests/test_factors_round2.py).
3. **Reference and candidate.** Reference = sm-1's frozen configuration for the market (half-lives, kept fr-1
   features, family) refit on the split's fit rows. Candidate = reference + ONE round-2 factor; same family, family
   parameters refit on the fit rows; standardised term with missing indicator exactly as sm-1 (REGISTER 5, A2).
4. **One-at-a-time test (stage A, statistical factors).** Split = the round-1 TRAIN (fit) / TUNE (evaluate)
   partitions (section 4). A factor PASSES iff the tune CRPS difference (candidate - reference) has a game-clustered
   bootstrap 95% interval entirely below 0 (B = 2,000, seed 20261023). Constant or < 30 known values -> "not
   evaluable". **Joint test:** reference + every passed factor; the market gets a stage-A sm-2 set only if the joint
   interval is entirely below 0, otherwise no stage-A factor is added.
5. **Game-line factors (stage B)**, on top of the stage-A set, same one-at-a-time + joint rule. NFL: round-1 split.
   NBA: train rows have no game lines (A1c), so split **R2**: fit rows start < 2026-02-01, evaluation rows 2026-02-01
   .. 2026-07-01. That evaluation window was the round-1 LOCKED test of sm-1; it is reused here only as a development
   window for the new game-line factors (no claim about sm-1 changes) and is labelled so. A game-line factor is
   evaluable only if lines cover >= 50% of the evaluation rows.
6. **Multiple testing** is acknowledged: ~10-20 factors per market at a one-sided 2.5% false-pass rate. That is why no
   tune pass promotes anything: the decision is the new locked test (8).
7. **Challenger.** A market whose joint test passes gets an sm-2 configuration (cal-1 re-searched on the window it was
   selected on, same grids) frozen in `sports/model/artifacts/model_config2_<sport>.json`, and a forward artifact
   `sm2_<sport>.json` refit on every row known at the freeze (hash manifest `MANIFEST_SM2.json`). Markets with no pass
   stay sm-1 only (no sm-2 artifact, nothing shadowed). sm-2 runs in SHADOW: its forecasts are recorded (own table,
   never in the member snapshot, never a grade shown) by the forward job in the same scheduler slot.
8. **New locked test L2 (forward, untouched).** The round-1 locked tests (NBA 2026-02-01..07-01; NFL 2025 week 10 -
   postseason and 2026 weeks 1-7) are used. No historical period remains that no model has seen (the sm-1 artifact was
   refit through 2026-10-22), so L2 = every game starting after the sm-2 freeze time recorded in MANIFEST_SM2.json.
   Scored ONCE, per market, when L2 first holds >= 150 NBA games / >= 48 NFL games with box scores (checked by the
   learning job; the result is committed as is; a later re-run is post-hoc). Both versions are scored from their
   frozen artifacts at the registered forecast time (start - 60 min) on the same eligible rows.
   **Promotion rule per market (all must hold):** (a) L2 CRPS sm-2 - sm-1: game-clustered 95% interval entirely below
   0; (b) L2 log-loss at the pre-game reference line: point estimate <= that of sm-1; (c) calibration at the reference
   line |in-the-large| <= 0.02 and ECE <= 0.03; (d) randomised-PIT 80% coverage in [0.76, 0.84] (A3); then (e) gate F
   (section 7) at REAL offered lines on the shadow forecasts, exactly as for sm-1, log-loss compared with sm-1 on the
   same props. Nothing promotes automatically: the owner release process decides after (a)-(e).
9. **Metrics reported for every tested factor:** tune CRPS difference + interval, standardised coefficient, coverage
   on fit / evaluation rows; for passed factors and the joint model also log-loss at the reference line, ECE /
   in-the-large and 80% PIT coverage on the evaluation window. Real-line metrics exist only forward (shadow).
10. **Display.** Every factor of the registry is shown on the player page with its status: used (value), tested - no
   measured effect (weight 0, with its interval), not available (reason). A factor that passes tune but is only in
   the sm-2 shadow is "in the sm-2 challenger (shadow) - not in the shown grade".
11. **Grade-band validation.** Independently of letters being shown, the admin learning report groups settled forward
   props by sports-gp-1 band (A / B / C / Lean of the chosen side's unconditional probability) per league x stat x
   model version: n, games, hit rate, mean probability, hit - p with a game-clustered interval.
- **A5a (2026-10-24, after the round-2 tune results; operational only - no criterion, threshold, split or metric
  changes):** (a) the forward job COUNTS the L2 games each run (a SQL count, bounded) and reports "threshold reached";
  the one-time L2 score itself is run by `python -m sports.model.shadow l2 <history.db> <sport>` (it builds full feature
  frames, ~300 MB, too large for the forward job's 340 MB cap) and writes `<SPORTS_DATA_DIR>/model/l2_<sport>.json`
  once; an existing result is never re-scored. (b) Known forward limitation recorded before any L2 / shadow evidence:
  NBA `starter_today` (kept in all four NBA sm-2 markets) has NO pre-game lineup source in this build, so forward it
  is unknown on every row and the shadow forecasts of NBA sm-2 are the degraded model (starter term at its train
  mean). The L2 box-score test scores sm-2 with the box-score starter flag, exactly as tuned (the registered same-day
  assumption); NBA real-line shadow evidence is labelled "starter unknown" and is not evidence for the tuned model
  until a pre-game lineup source exists.

## Amendment A6 - NFL challenger sm-nfl-2 (participation split + bias calibration) - registered 2026-10-24, BEFORE any sm-nfl-2 fit or score

Why (measured, sports/MODEL_CARD_NFL.md "Owner re-audit 2026-10-24"): sm-1 failed gate H in all five NFL markets and
leans Under at book lines. Two causes are model structure: (1) partial-participation games (offensive snap share
< 50%: injury exits, backup weeks) enter the opportunity history at full weight, so ~21% of the rec_yds PMF mass sits at
<= 25% of the line; (2) pass_yds (-9.9 yds, 2026 weeks 1-7) and rush_att (-0.37 carries; -0.54 on the 2025 locked
test) are under-forecast, and cal-1 chooses the mean factor c by CRPS, which for right-skewed outcomes tracks the
median and moves the mean DOWN (sm-1 c = 0.94 rush_yds, 0.97 rush_att). NFL stays CONTAINED (no lean / edge /
confidence; projection only, "NFL model not validated", sports/board_view.py vg-1) until a market passes this protocol.
Honesty note: this design was written after sm-1's locked-test and 2026 bias numbers were known, so those spent windows
can never be evidence for sm-nfl-2 (they are diagnostic D only, item 7).

1. **Candidate sm-nfl-2 (per NFL market; nothing is selected on a scored window).** Same eligible population (section
   3), same half-lives and family as sm-1's frozen configuration for the market, same EB efficiency (all prior games, k
   refit on train), plus:
   a. **Role games.** A game is FULL-ROLE iff offense_pct >= 0.50; offense_pct unknown -> full-role (as in sm-1).
   b. **Performance given role.** E_full[opportunity] = EW mean (the market's half-life; partial games skipped but
      ageing the weights) over the player's PRIOR full-role games only; a player with no prior full-role game uses
      sm-1's all-games EW and carries the flag no_full_history = 1. mu_full = E_full x efficiency x exp(beta . x);
      beta (intercept + the market's sm-2 round-2 set from A5 - pass_yds wind; rush_yds mates_out_carries; rec_yds
      snap_share + implied_total; rush_att home + scramble + mates_out_carries + spread; rec team_explosive + travel -
      not re-tested; same standardisation and missing indicators) fitted by the same log-link GLM on TRAIN rows whose
      own game was full-role. Full-role component = sm-1's family for the market, parameters fitted on those rows.
   c. **Partial component.** The empirical distribution of y / mu_full on TRAIN rows whose own game was partial (200
      quantiles; separately for no_full_history 0 / 1, pooled when a group has < 50 rows), read like the ratio family.
   d. **Participation model.** pi = P(full role | pre-game inputs), logistic regression fitted on all eligible TRAIN
      rows: intercept + standardised EW snap share (all prior games), previous game's snap share, EW share of prior
      partial games, no_full_history, position indicators (constant ones dropped); missing -> train mean. Inputs use
      prior games only (featurize point-in-time rule). PMF = pi x P_full + (1 - pi) x P_partial (still conditional on
      playing; a DNP is not an outcome, section 3).
   e. **Calibration cal-2 (fitted on TUNE only; the same rule for every market).** Mean factor c = sum(y) / sum(E[X])
      on the tune rows (removes the tune signed bias; clipped to the cal-1 range 0.90..1.10), then the spread scale s
      by tune CRPS on the cal-1 S grid with c fixed; applied like cal-1, around the PMF's own mean.
   f. **Forward artifact.** Configuration (a-e) with every fitted piece refit on all eligible rows known at the freeze
      (cal-2 stays the tune-fitted one): `model_config_nflv2.json`, `smnfl2_nfl.json`, hash manifest
      `MANIFEST_NFLV2.json` (sha256 of every file + content sha of the artifact; a mismatch refuses to load), with
      freeze_at recorded there.
2. **Tune report (development evidence, not a gate).** On the round-1 tune partition (2025 weeks 1-9; fitted on
   train): sm-nfl-2 vs sm-1 (sm-1's frozen configuration refit on train) - CRPS difference with game-clustered 95%
   interval (B = 2,000, seed 20261023), signed bias, log-loss / Brier / ECE / in-the-large at the pre-game reference
   line L_ref (section 6), randomised-PIT 80% coverage. Sanity rule (the only tune decision): a market whose tune CRPS
   difference interval lies entirely ABOVE 0 is dropped (no artifact, no shadow).
3. **Held-out partition H2.** No historical NFL period remains that no NFL model has scored (ds-1 ends with 2026 week
   7's Thursday game; every season is train / tune / locked / secondary-locked), so H2 is FORWARD ONLY: every NFL game
   starting after freeze_at in MANIFEST_NFLV2.json.
   - H2-box: all eligible rows of H2 games with a box score, both frozen forward artifacts (sm-1 `sm1_nfl.json`,
     sm-nfl-2 `smnfl2_nfl.json`) at the registered forecast time (start - 60 min). Scored ONCE when H2 first holds
     >= 48 NFL games with box scores (the forward job only counts; `python -m sports.model.nflv2 heldout` scores and
     writes `<SPORTS_DATA_DIR>/model/heldout_nflv2.json`; an existing result is never re-scored).
   - H2-book: sm-nfl-2 SHADOW forecasts at real target-book lines (PrizePicks / Underdog offers, recorded by the
     forward job beside sm-1's forecast of the same offer at the same forecast time), H2 games only; one row per (game,
     player, stat, line) = the last pre-start forecast; outcome from the box score; pushes (y = line) excluded,
     probabilities read conditional on no push. Scored ONCE per market when it first holds >= 150 such props from
     >= 30 games.
4. **Metrics.** H2-box: CRPS (primary), log-loss and binary Brier at L_ref, ECE / in-the-large at L_ref,
   randomised-PIT 80% coverage, signed bias (mean - actual), all paired with sm-1 on the same rows (game-clustered
   intervals). H2-book: in-the-large (mean P(Over | no push) - over-rate) with the game-clustered interval of
   (hit - p), ECE (10 bins, n >= 20 per bin), log-loss and Brier paired with sm-1 on the same props, over-rate o,
   share s of props whose P(Over) > P(Under), signed bias of the projection vs the book line.
5. **Per-market promotion rule (all must hold; nothing promotes automatically).**
   (a) H2-box CRPS sm-nfl-2 - sm-1: game-clustered 95% interval entirely below 0;
   (b) H2-box at L_ref: |in-the-large| <= 0.02 and ECE <= 0.03; (c) randomised-PIT 80% coverage in [0.76, 0.84];
   (d) H2-box log-loss at L_ref: point estimate <= sm-1's;
   (e) H2-book calibration: |in-the-large| <= 0.03 and the game-clustered interval of (hit - p) contains 0;
   (f) H2-book log-loss: point estimate <= sm-1's on the same props;
   (g) NOT one-sided: |s - o| <= 0.15 and 0.25 <= s <= 0.75 (o = that market's H2-book over-rate).
   A market meeting (a)-(g) is PROMOTION-ELIGIBLE (it stays shadow; the owner release process decides).
   **Display bar (stricter; lifting containment for that market is the coordinator's decision):** (a)-(g) AND the
   H2-book sample reaches the gate-F size (>= 300 props from >= 100 games) with (e)-(g) still holding on it. Grades /
   stakes keep gate F (section 7) unchanged. A market that fails stays contained.
6. **Shadow.** Every market not dropped by the sanity rule (2) is recorded in SHADOW from the freeze on (it must be, or
   H2-book could never be measured): rows in `<SPORTS_DATA_DIR>/model/shadow.db` table shadow_forecasts with
   model_version `sm-nfl-2` (+ table shadow_inputs: the saved feature row and sm-1's probabilities for the same offer,
   for replay and the paired book-line metrics); never a store record, never in a member snapshot, never a grade
   shown; same scheduler slot and process as sm-1 (`model-forward`, cap 340 MB); off switch SPORTS_SHADOW_NFLV2=0.
7. **Diagnostic D (post-hoc, never a gate input).** After the configuration is frozen, the train-fit sm-nfl-2 and sm-1
   are also scored on sm-1's spent windows (2025 week 10 - postseason; 2026 weeks 1-7) with the same metrics,
   labelled "post-hoc diagnostic - window already used by sm-1" and committed as is; nothing is changed after it.

## Amendment A7 - tw-1 walk-forward fine-tuning from past games (registered 2026-10-25, BEFORE any A7 candidate was fitted or scored)

Owner 2026-10-25: "finish calibration for NBA and NFL ASAP" / "use previous matches to fine tune" - do not wait for
the forward windows (A5 L2, A6 H2, ~Nov 9-10); use past games now, honestly. What existed when this was written: the
frozen sm-1 configs (`model_config_<sport>.json`), the A6 config (`model_config_nflv2.json`), ds-1 through 2026-10-23
UTC. No A7 candidate had been fitted against an outcome or scored; the code (`sports/model/tune.py`) is written after
this text and implements it.

**Honesty statement.** A rolling-origin (walk-forward) evaluation is honest out-of-sample evidence for the PROCEDURE:
every forecast uses only parameters fitted on games known before its origin. It is WEAKER than a never-seen period:
every window below has been scored before (NBA span = inside sm-1's locked test 2026-02-01..07-01 plus the 2026-27
opening days; NFL span = sm-1's locked + secondary locked tests, also sm-nfl-2 diagnostic D), and the candidate ideas
were motivated by results on these windows (MODEL_CARD_NFL re-audit, MODEL_CARD_SMNFL2). Every result is labelled
"walk-forward (procedure out-of-sample; windows previously inspected)".

1. **Span and origins.**
   - NFL: one origin per game-week (season, week). Scored span = 2025 week 10 through the 2025 postseason + 2026
     weeks 1-7. Warm-up = 2025 weeks 1-9: their out-of-sample forecasts feed calibration layers only, never scored.
   - NBA: game-day = UTC date of (start - 10 h). Scored span = the last 60 game-days of ds-1 with eligible rows
     (ending 2026-10-22); warm-up = the 30 game-days before it.
   - **Selection block** = the first 25% of scored origins (NFL 5 game-weeks, NBA 15 game-days): used ONLY to choose
     each candidate's half-life / window (item 4). **Evaluation block** = the remaining origins; every promotion
     statistic comes from it, on identical rows for live and candidate (paired).
2. **Procedure at origin t** (t = earliest start among the origin's games - 60 min). Fit rows = eligible rows (section
   3) whose known_at (start + 6 h NFL / + 4 h NBA) <= t. Every fitted parameter (priors, k, GLM beta, family
   parameters; the sm-nfl-2 participation model and partial component; the sn snap regression) is REFIT at every
   origin on the fit rows only. Configuration (half-lives, features, family; live's cal-1) = the frozen sm-1 / A6
   configs, chosen on windows before the span. Forecast = every eligible row of the origin's games; features are the
   frames' point-in-time columns (prior games only). Calibration layers read only out-of-sample forecasts of EARLIER
   origins whose rows have known_at <= t.
3. **Arms (per market).**
   - `live`: the sm-1 frozen config refit at each origin, cal-1 frozen (the production procedure).
   - `rc` (all 9 markets), recency-weighted recalibration of the live distribution: PMF_rc = transform(PMF_live, c_t,
     s_t) around its own mean (the cal-1 transform). c_t = sum w y / sum w E_live[X] over earlier OOS rows (bias
     removal of the projection, clipped 0.90..1.10); s_t = argmin weighted CRPS over the cal-1 S grid with c_t
     fixed; w = 0.5^(age / h), age = days from that row's start to t (NFL in weeks = days / 7). Grid h: NFL {2, 4, 8,
     16 weeks, inf}, NBA {7, 14, 28, 56 days, inf}. Fewer than 200 earlier OOS rows -> c = s = 1 (= live).
   - `v2` (pass_yds, rec_yds, rec - the A6 shadow markets): sm-nfl-2 (A6 1a-d, A6 features) refit at each origin;
     its raw PMF then gets the `rc` rule fitted on its OWN earlier OOS raw forecasts (replacing cal-2's tune c).
   - `sn` (rush_yds, rush_att), running-back fix with snap share as a continuous input (not the 50% split):
     E[opp] = R x S. R = sum_w opp / sum_w snap over prior games with a known snap share > 0 (EW, the market's
     half-life; no such game -> sm-1's EW opportunity). S = linear regression (fit rows) of the game's offense snap
     share on the EW snap share, the previous game's snap share, the EW share of partial (< 50%) games and position
     indicators, clipped to [0.05, 1]. Then sm-1's EB efficiency, intercept + GLM features and family exactly as sm-1
     around that mean, then the `rc` rule on its own OOS raw forecasts.
   - `dr` (pass_yds, rush_att), level drift: PMF_dr = transform(PMF_live, c_t, 1), c_t = sum y / sum E_live[X] over
     the OOS rows of the previous k game-weeks only (k in {2, 3, 4, 6}), clipped 0.90..1.10; fewer than 100 rows -> 1.
   - NBA: `live` and `rc` only.
4. **Choice of h / k** per candidate x market: the lowest mean CRPS over the selection block (ties -> the longer h /
   k). Never revisited after the evaluation block is scored.
5. **Metrics (evaluation block).** CRPS (primary) paired cand - live with a game-clustered 95% interval (B = 2,000,
   seed 20261023) and a date-block interval (NFL season-week, NBA ISO week); log-loss / Brier at L_ref (section 6);
   ECE and in-the-large at L_ref; randomised-PIT 80% coverage (A3); signed bias; grade bands at L_ref: chosen side =
   the side with the higher probability, band by sports-gp-1 (section 8) - per band n, games, mean p, hit rate,
   hit - p with a game-clustered interval.
6. **Book-line calibration `bl-1`** (forward, settled props). Location-only recalibration of a forecast's whole CDF
   (one distribution for every line kept): F'(k) = sigmoid(logit F(k) + a); a fitted by maximum likelihood on the
   binary outcome at the offered line (P(Over | no push), pushes excluded) of settled target-book props (PrizePicks /
   Underdog; one row per (game, player, stat, line) = the last pre-start forecast) whose settlement was known BEFORE
   the forecast's forecast_at; needs >= 100 props from >= 20 games, else a = 0; rolling (refit per forecast time),
   scored on the later props only. No slope term: shrinking chances toward 50% is never used to fit bands (owner
   rule). Metrics: log-loss / Brier paired with live (game-clustered interval), in-the-large, over-rate o vs share
   favouring Over s, by market. Production settled props are in the service's learning.db (scorecard); the scorer
   runs where the data is (`python -m sports.model.tune bookline`); a locally reconstructed sample is labelled so.
7. **Promotion rule per market** (the candidate with its selected h / k; all must hold):
   (a) evaluation-block CRPS cand - live: game-clustered 95% interval entirely below 0;
   (b) at L_ref: |in-the-large| <= 0.02 and ECE <= 0.03;
   (c) randomised-PIT 80% coverage in [0.76, 0.84];
   (d) at book lines (>= 100 settled props from >= 20 games): log-loss cand - live point estimate <= 0 AND not
       one-sided, |s - o| <= 0.15; below that sample "not evaluable";
   (e) grade bands: evaluation block at L_ref, every band with n >= 100: |hit - mean p| <= 0.03; at book lines,
       every band with >= 30 graded props: |hit - mean p| <= 0.05 or its game-clustered interval contains 0 (else
       not evaluable).
   Owner rule: a candidate is never chosen for moving chances toward 50%; the report gives c_t, s_t and the change in
   mean chosen-side probability. Several passing candidates for one market -> the lowest evaluation-block CRPS (a
   selection on the evaluation block, reported with every candidate's numbers). Multiple testing: 16 candidate x
   market tests at a one-sided 2.5% false-pass rate each (~0.4 expected false passes), stated in the report.
   **Decision.** (a), (b), (c) and the evaluation-block part of (e) pass, and (d) / book-line (e) pass or are not
   evaluable -> the market SWITCHES to tw-1 (labelled "book-line evidence pending" while (d) is not evaluable; the
   first check at >= 100 settled props is reported by the CLI, and a failure switches the market back through its
   off switch). NFL containment (vg-1) is lifted for a market only when (a)-(e) hold with (d) and book-line (e)
   evaluated on >= 300 props from >= 100 games (the A6 display bar). Grades / stakes keep gate F (section 7).
8. **Freeze and forward use.** Switched markets -> `sports/model/artifacts/tw1_<sport>.json` (arm, h / k, parameters
   fitted at the end of the span on all OOS rows, sha of the sm-1 artifact it applies to) + `MANIFEST_TW1.json`
   (sha256 of every file + content sha; a mismatch or a different sm-1 artifact refuses to load -> sm-1 unchanged).
   The forward job applies a switched market's layer to sm-1's PMF; per-market switch = the artifact's market list;
   global off switch `SPORTS_TUNE_TW1=0`. Forecast records keep model_version `sm-1` (the displayed product line);
   artifact_version `sm-1:<sha>+tw-1:<sha>`, calibration_version `tw-1:<arm>:c=..,s=..` and reason code `tw-1` name
   the exact version; old forecasts are untouched (new content -> new forecast ids). The sm-2 / sm-nfl-2 shadows keep
   comparing with the frozen sm-1 probabilities. A re-run on new data makes a challenger artifact; nothing switches
   automatically. Results: `sports/research/results/model_tw1_<sport>.json`, committed as produced.

- **A7b (2026-10-25, after the A7 results; registered BEFORE any so-1 fit or score) - NBA season-opening minutes
  challenger `so-1`.** Why (post-hoc diagnostic of the A7 NBA evaluation block, labelled so in the results): the 2026-27
  opening days (262 rows / 16 games, 2026-10-20..22) show live points bias +3.3 and EW minutes 27.6 vs 24.7 played -
  the minutes history of a regular-season forecast is carried over from last season's playoffs / end of season.
  That window is spent (seen) and is never evidence for so-1.
  1. Candidate `so-1`: identical to the live arm except E[minutes]: for a REGULAR-season forecast row, the EW minutes
     (sm-1 half-life h_m) are taken over the player's prior REGULAR-season games only (postseason and play-in games
     skipped but still ageing the weights, ew_prior semantics); no prior regular-season game -> sm-1's all-games value.
     Postseason / play-in rows are unchanged. Every fitted parameter refit at each origin with the same column (A7 2).
  2. Windows (walk-forward, A7 2 procedure, never scored for this question): the first 15 regular-season game-days of
     2024-25 and of 2025-26, pooled (30 origins); no warm-up, no selection block (nothing to choose). The 2026-27
     opening days are reported separately as post-hoc.
  3. Promotion rule per market: A7 7 (a), (b), (c) and the evaluation-block part of (e) on the pooled windows, paired
     with the live arm on identical rows; the book-line parts (d) / (e) as in A7 7. A market that passes gets an so-1
     forward artifact (sm-1 config refit on the so-1 minutes column, own hash manifest) and switches through the same
     per-market switch for regular-season forecasts; otherwise nothing changes. Results:
     `sports/research/results/model_so1_nba.json`.

## Amendment A8 - cal-mi-1 market-informed calibration of the displayed Scout chance at book lines (registered 2026-10-25, BEFORE any cal-mi fit or score)

Owner 2026-10-25: "we need everything calibrated and fixed TODAY, this hit rate is terrible." What existed when this was
written: the production bl-1 book-line check (`python -m sports.model.tune bookline`, settled forward props, ~8
game-days per league; the owner relayed: NBA Overs won 0.54-0.67 while Scout favoured Over on 0.27-0.42 of props; NBA A
bands stated ~0.70 hit 0.47-0.58; NFL A pass_yds 0.72 / 0.04 (27), rush_att 0.75 / 0.46, rec 0.73 / 0.56, rec_yds
0.70 / 0.68). No cal-mi candidate had been fitted or scored; `sports/model/cal.py` is written after this text.
Hypothesis: sm-1 ignores the information in the book line / market price (lines sit above player history).

**OWNER OVERRIDE (stated risk).** The owner directs that cal-mi is evaluated NOW on the available settled props,
below the registered 20-game minimum of A7 6 / 7(d). Risks: few independent game-days (outcomes inside one game and
one night are correlated, so one slate can dominate a fit); the game-clustered intervals are wide and a point-estimate
pass can be noise; walk-forward days at the start have no fit. Every result is labelled "below the 20-game minimum
(owner override)". The decision is reversible every day (the weights file is refit daily; per-market and global off
switches), forward evidence stays forward, and nothing here changes stakes (all stakes stay 0U, gate F unchanged).

1. **Candidate `cal-mi-1`** (per league x stat). For an sm-1 Scout chance at an offered target-board line (PrizePicks
   / Underdog; pushes excluded): s = logit P_scout(Over | no push), m = logit P_market(Over | no push).
   - variant M (a market chance exists at that exact quote): P(Over | no push) = sigmoid(a + w_s s + w_m m);
   - variant S (no market chance): P(Over | no push) = sigmoid(b + v_s s) (w_m = 0, v_s shrunk by the prior below).
   Unconditional output: P'(Over) = (1 - P(Push)) q, P'(Under) = (1 - P(Push)) (1 - q), P'(Push) = sm-1's P(Push)
   (sums to one; the push atom is unchanged). One input per side, both sides from the same q (no separate fits).
   - Market chance at serve time = mv-1 display consensus at that exact quote (`board_view.market_at`: the offer's own
     `mk.at[record]`, else the headline when it is at the same line; mc-1 rule, nearest line of each book moved to the
     target line with dispersion.json = the market's line read). In the fit / evaluation (settled props) = the
     equal-weight log-odds mean of the settled single-source forecasts `mkt-<source>` made for the same offer at the
     same forecast_at (= the mc-1 rule over every eligible source = the mv-1 source set), else the `mc-1` row, else
     none (variant S).
2. **Fit.** Penalised maximum likelihood (Newton), Gaussian prior with precision LAMBDA = 20 per parameter, centred
   on the IDENTITY (a = 0, w_s = 1, w_m = 0; b = 0, v_s = 1 = the live Scout chance). Two levels: league pooled (all
   stats of the league; M on the rows with a market chance, S on all rows using s only); per stat with the prior
   centred on the league fit. A level with fewer than MIN_FIT = 50 props or fewer than 2 games takes its parent (stat
   -> league -> identity). |each parameter| is bounded by 5 (a fit outside is refused -> parent). No other shrinkage;
   the prior centre is the live chance, never 50%.
3. **Data (production).** `learning.db` scorecard rows of model sm-1 on PrizePicks / Underdog, superseded = 0, status
   win / loss, value != line; one row per (event, platform, offer, stat, line) = the last pre-start forecast (offer from
   `forecasts.db` inputs; without inputs one row per forecast as bl-1). Outcome = value > line.
4. **Walk-forward by game-day.** Game-day D = UTC date of (forecast_at - 10 h). For day D the parameters are fitted
   only on props whose settled_at < the earliest forecast_at of D (no look-ahead); D's props are scored with them.
   Days whose training set gives no fit at any level are not scored (counted as such).
5. **Metrics per league x stat** (scored walk-forward rows, paired with live on identical rows): log loss cal - live
   with a game-clustered 95% interval (evaluate.boot_mean, B = 2,000, seed 20261023), Brier, calibration-in-the-large
   (mean p - over rate), share favouring Over s vs realised over rate o, grade bands (sports-gp-1 on the chosen-side
   chance) stated vs hit, and the variant mix (M / S).
6. **Promotion rule per league x stat** (needs >= 30 scored props from >= 2 game-days, else "not evaluable" -> OFF):
   PASS if (i) the log-loss difference's game-clustered interval is entirely below 0, OR (ii) (too few games for a
   significant result) the log-loss difference point estimate <= 0 AND |in-the-large| <= 0.03 AND |s - o| <= 0.15.
   PASS -> the market is ON with the parameters fitted on ALL settled props up to the run; FAIL -> OFF (the raw sm-1
   chance is displayed). Grade bands then use the calibrated chance (owner rule: accuracy from more information, never
   an arbitrary shrink toward 50%; the report gives w_s, w_m, v_s and the mean chosen-side chance before / after).
7. **Forward use.** One function (`sports/model/cal.py adjust`) applied where the snapshot builds every quote's Scout
   view (`snapshots.guard_row`, `sheets`), so the board, research grades (rg-1 = sports-gp-1 band of the calibrated
   chance), sheets, compare, slips and tracked picks all read the same calibrated chance; the raw sm-1 chance stays in
   the forecast record (unchanged) and in the quote's detail (`ca.raw`). mi-1 / market-only views are never
   recalibrated. The lean rule (lean_of with the mean, N4 positive edge vs the market) and the vg-1 / sg-1 guards are
   unchanged. Version `cal-mi-1:<params sha>`. Weights: `<SPORTS_DATA_DIR>/model/cal_mi.json` written by
   `python -m sports.model.cal fit` (validated: schema, version, finite bounded parameters, sha; invalid -> the
   committed default `sports/model/artifacts/cal_mi_default.json`; invalid default -> off). Per-market switch = the
   file's `on` flag per league x stat; global off switch `SPORTS_CAL_MI=0`.
8. **Committed default (local data).** The best local sample: settled props reconstructed from the scratch store of the
   2026-10-23 NBA slate (one game-day, 7 games) joined to the store's own mkt-* / mc-1 forecasts at the same offer and
   forecast_at. Walk-forward is impossible on one game-day, so the default uses the weaker leave-one-GAME-out check
   (fit on the other games, score the held-out game) with rule 6 (ii) on >= 30 props from >= 5 games; a market that
   passes is ON in the committed default with the parameters fitted on all local props, else OFF. Labelled
   "local leave-one-game-out, one night, below the 20-game minimum (owner override)". The owner's Shell `fit` run on
   production data supersedes it as soon as it is written.

## Amendment A8a - balance check on both cal-mi-1 pass paths (registered 2026-10-26, BEFORE the second NBA game-day is scored)

Owner decision 2026-10-26. Production `fit` on the first scored NBA night (Oct 25, 7 games) showed nba:points with a
log-loss interval entirely below 0 while the calibrated chance favoured Over on 100% of props (over rate 56.3%): a
one-night Over lean would have passed A8 6 (i), which had no balance condition. A8 6 is amended (stricter only):

1. `|share favouring Over - over rate| <= 0.15` is REQUIRED on both paths. PASS (i) = log-loss game-clustered interval
   entirely below 0 AND balanced; PASS (ii) unchanged (it already required it).
2. Nothing else changes (minimums, tolerances, data, walk-forward, switches).
3. Known limitation: a well-calibrated model on a market whose over rate is far from 50% favours that side more often
   than the over rate, so this check can block an honest fit there; accepted (owner's correlated-night risk ranks first).

## Amendment A9 - multilevel grade check gc-1 and the research-letter containment / release rule (registered 2026-10-28, BEFORE `gradecheck` was run on any settled data)

Owner directive 2026-10-28: "do a multilevel full check for grading logic across every single board; we have enough
data for NFL and NBA grades now." State when this was written: NBA research letters shown (rg-1 = sports-gp-1 band of
the unrounded lean-side chance; A >= 0.60, B >= 0.5559, C >= 0.5345, else Lean); NFL letters OFF since 2026-10-25
(`board_view.VG_CONTAIN_LEAN = True`, owner decision after A pass_yds hit 1 of 27 at real lines); cal-mi-1 live with
every market OFF (A8 + A8a); stakes 0U everywhere. No `gradecheck` output existed. The CLI
(`python -m sports.model.tune gradecheck`, sports/model/gradecheck.py) lands in the commit AFTER this text.

1. **Data (gc-1).** `learning.db` scorecard rows of sm-1, superseded = 0, PrizePicks / Underdog, with the forecast's
   saved inputs (`forecasts.db`) and, when reachable, the canonical store (offer variant, event home / away, start)
   and the game store (ESPN pickcenter moneyline / Kalshi home-win). One row per (event, platform, offer, stat, line)
   = the last pre-start forecast (A8 3). Status win / loss are scored; push / void / unresolved counted apart. The
   `--days N` window is by forecast_at (default: everything).
2. **Chance.** The raw sm-1 unconditional chance in the scorecard = the chance the board showed while the market's
   cal-mi-1 flag is OFF. For a market whose flag is ON in the weights in use, the A8 4 walk-forward cal-mi chance
   replaces it on the rows that have one (rows before the first fit keep the raw chance). "stated" = the mean lean-side
   chance given no push (p_side / (1 - p_push)), the same conditioning as the hit rate wins / (wins + losses).
3. **Side = the board lean**, never the scorecard's max-chance side: `board_view.lean_of` (chance > 50% on the side
   where the projection mean sits; mean from the saved PMF, chance alone without one). Rows without a lean are counted
   ("no_lean") and not scored. The scorecard's max-chance side is reported alongside as `chosen_side` for comparison
   only. The "as shown" set additionally removes rows the live board would have held: N4 (a market chance at the quote
   priced the lean side at least as high - from the settled `mkt-*` / `mc-1` forecasts of the same offer and
   forecast_at, A8 1), sg-1 ratio holds (mean < 25% of the line or > 4x the line, line >= 2), and sg-1 l10_contra
   when the history store is reachable (counted "unknown" otherwise). Letter = sports-gp-1 band of the unrounded
   lean-side unconditional chance. vg-1 containment is NOT applied (the point is to score what the letters would be).
4. **Levels** (per league, each with n, W-L-P, hit rate, stated, game-clustered 95% interval of the hit rate
   (evaluate.boot_mean, B = 2,000, seed 20261023), balance = share of leans that are Over vs the realised over rate):
   overall -> stat -> letter (A / B / C / Lean, order holds = hit(A) >= hit(B) >= hit(C) >= hit(Lean) on the bands
   with n >= 30) -> Over / Under -> platform -> variant (standard / goblin / demon / other) -> home / away ->
   favourite / underdog (pickcenter moneyline, else Kalshi home-win; unknown otherwise) -> |line - mean| bins (< 0.5,
   0.5-1.5, 1.5-3, >= 3) -> market chance at the quote yes / no -> day of week and back-to-back (NBA: rest_days <= 1.1)
   -> forecast age before start (< 1 h, 1-3 h, 3-12 h, >= 12 h) -> model / artifact sha. Also: vg-1 gate state per
   stat, sg-1 and N4 counts with the hit rate of what they removed, cf-2 confidence bins (signal() rebuilt from the
   saved inputs; the time-dependent stale cap left out), the cal-mi-1 walk-forward with A8a (cal.run_eval, unchanged),
   and calibration (hit - stated, in-the-large) reported SEPARATELY from selection (hit rate vs 50%, vs the over rate).
   Intervals from fewer than 2 games are null. Every slice with n < 30 is printed with n only (no verdict is read
   from it).
5. **Verdict rule per league** (read by the owner from the production output; nothing changes automatically; the
   letters of a league change only through the owner release process). Game-day = UTC date of (event start - 10 h),
   else of (forecast_at - 10 h). AB = the "as shown" rows with letter A or B pooled (A + B is what a member would act
   on; C and Lean are reported but do not enter the verdict - at 53-56% stated, 150 props cannot separate them from
   50%).
   - **release** (NFL: the owner may set `VG_CONTAIN_LEAN = False`; NBA: letters stay on) requires ALL of:
     (1) n(AB) >= 150 scored props from >= 3 game-days and >= 10 games;
     (2) hit(AB) >= stated(AB) - 0.05;
     (3) the game-clustered 95% interval of hit(AB) has lower bound >= 0.50;
     (4) balance |share Over - over rate| <= 0.15 on AB and on the league's whole scored set;
     (5) no stat with >= 30 AB props whose hit interval upper bound is < 0.50.
   - **contain** (NFL stays contained; NBA: the owner withdraws the letters of the league or of the named stat):
     n(AB) >= 150 from >= 3 game-days and (the hit(AB) interval upper bound < 0.50 OR hit(AB) < stated(AB) - 0.10);
     or any stat with >= 60 AB props whose hit interval upper bound is < 0.50 -> "contain:<stat>".
   - **keep** when n(AB) >= 150 from >= 3 game-days and neither release nor contain holds (letters stay as they are).
   - **insufficient** when n(AB) < 150 or fewer than 3 game-days: no change either way.
   A release of NFL letters keeps edges, confidence and edge ranking withheld (vg-1 unchanged) and stakes at 0U.
6. **Nothing else changes.** sports-gp-1 bands, sports-sp-1 (0U), gate H / gate F, A8 / A8a, cf-2, N4, sg-1 and
   `VG_CONTAIN_LEAN` are unchanged by this amendment. `gradecheck` is read-only (it writes no file and no row).
