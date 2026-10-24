# Model card - Scout Sports sm-2 (challenger, SHADOW only)

Registered protocol: `sports/research/REGISTER.md` amendment A5 (+ A5a), committed before any round-2 score. Tune
results: `sports/research/results/model_study2_{nba,nfl}_tune.json`. Frozen configurations:
`sports/model/artifacts/model_config2_{nba,nfl}.json`. Forward artifacts: `sm2_nba.json` (sha `2290682e04d353eb`),
`sm2_nfl.json` (sha `e5751bf215095a10`), fitted 2026-10-24T22:14Z; hash manifest `MANIFEST_SM2.json` (with the NFL
play-by-play extras package `ds2_extras_nfl.json.gz`).

## What it is

sm-2 = sm-1's frozen configuration for the market (half-lives, kept fr-1 features, family) + the round-2 factors that
passed the registered one-at-a-time AND joint tune tests (game-clustered 95% interval of the tune CRPS difference
entirely below 0). Same GLM form, same family; cal-1 re-searched on the selection window. Nothing hand-weighted.

| Market | sm-1 features | sm-2 adds (tune CRPS change of the joint step, 95% game-clustered interval) |
|---|---|---|
| NBA points | usage, role_stability, starter_prev, opp_pace, teammate_absent_sameday | starter_today + teammate_absent_usage: -0.0441 [-0.0568, -0.0325] |
| NBA rebounds | role_stability, opp_pace, opp_positional_defence | starter_today -0.0037 [-0.0060, -0.0015]; game_total (R2) -0.0004 [-0.0007, -0.0002] |
| NBA assists | role_stability, starter_prev, team_pace | opp_drtg + travel + starter_today -0.0120 [-0.0151, -0.0089]; game_total + implied_team_total (R2) -0.0008 [-0.0014, -0.0003] |
| NBA threes | - | starter_today -0.0013 [-0.0023, -0.0004] |
| NFL pass_yds | - | wind -0.469 [-0.900, -0.022] |
| NFL rush_yds | - | mates_out_carries -0.171 [-0.332, -0.0004] |
| NFL rec_yds | snap_share | implied_total -0.069 [-0.125, -0.013] |
| NFL rush_att | home | scramble_rate + mates_out_carries -0.048 [-0.088, -0.007]; spread -0.014 [-0.028, -0.001] |
| NFL rec | - | team_explosive + travel -0.0033 [-0.0057, -0.0009] |

## Status and limits

- **Not promoted, not displayed, not graded.** Tune passes are development evidence with ~10-20 tests per market
  (multiple testing acknowledged in A5 6). Promotion needs the new forward locked test L2 (games after the freeze;
  scored once at >= 150 NBA / >= 48 NFL games) on all of (a) CRPS interval below 0 vs sm-1, (b) reference-line
  log-loss not worse, (c) calibration, (d) 80% PIT coverage, then (e) gate F at real offered lines on the shadow
  scorecard, then the owner release process.
- **NBA starter_today has no forward source** (no pre-game lineup is collected): the NBA shadow forecasts run with the
  starter term unknown (A5a b). On the 2026-10-23 board copy the NBA points / assists shadow chances moved on average
  7.6 / 7.9 points from sm-1 for that reason; rebounds / threes 0.8 / 0.4. A pre-game lineup source is a blocker for
  NBA sm-2.
- Same-game teammate absences (NBA usage share, NFL carry share) come forward from the ESPN injury report (Out) when a
  report is fresh, else unknown. Game lines and NFL weather come from the games store (ESPN pickcenter / NWS) observed
  before the forecast; without the games store they are unknown.
- The NBA game-line factors were evaluated on split R2 (fit < 2026-02-01, evaluation 2026-02-01..07-01 = sm-1's spent
  locked window, development reuse, A5 5).
