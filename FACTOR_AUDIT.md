# Factor audit (living document, started 2026-10-08; group tests completed 2026-10-09)

Status of every factor that could affect a projection. Rules:
- a factor moves the live forecast only after a predefined group test on chronological data;
- correlated factors are grouped and regularized, never stacked as separate multipliers;
- missing data stays unknown.

"Dev" = tested on Sep 2025 - Oct 8 2026 data (development). Splits: TRAIN < 2026-07-01, VALID < 2026-08-13, TEST after.
Group tests: ridge on the relative residual of perf-1 (TRAIN fit, strength chosen on VALID, scored on TEST, n 13,255
series-players, match-grouped 95% intervals). A group passes only if TEST improves with the interval below 0.
Confirmation of anything promoted uses matches after its freeze (2026-10-09 on).

| Factor / group | Source (pre-match?) | Coverage | Role | Status | Evidence |
|---|---|---|---|---|---|
| Player kills per round, time-weighted + shrunk | HLTV player_stats (yes) | 1,431 players, 113,859 player-maps | rate | **USED** (perf-1, perf-2) | Held-out kills MAE 6.12; 80% intervals 79-83% |
| Newcomer prior: shrink toward 0.93 x pro average | HLTV (yes) | all (matters for thin history) | rate | **USED** (perf-2, 2026-10-09) | thin-history bias was +1.29 (VALID) / +1.89 (TEST) kills; m chosen on TRAIN; kills MAE VALID -0.035 (95% -0.061 to -0.010), TEST -0.033 (-0.046 to -0.021) |
| Rounds as exposure | HLTV matches scores (yes, as history) | all maps | rate denominator | **USED** | kills per round flat across match length (0.99-1.02) |
| LAN / online environment (player's own split, blended by sample) | HLTV match_overview.lan (yes) | all | rate | **USED** | part of the perf-1 tuning (env blend 800 rounds) |
| Opponent kills conceded per enemy player | HLTV (yes) | teams in HLTV | rate | **USED** (shrunk) | part of the perf-1 tuning (300 rounds) |
| Headshots per kill, shrunk; HS out of kills (beta-binomial) | HLTV map_headshots (yes) | all | HS propensity + variance | **USED**; shrink 150 -> 25 rounds in perf-2 | k_hs chosen on TRAIN; HS MAE VALID -0.035 (-0.054 to -0.017), TEST -0.036 (-0.046 to -0.028); kappa re-checked on TRAIN: flat 100-150, kept 100 |
| BO3.gg map rows (fallback for players HLTV lacks) | BO3.gg (yes) | players outside HLTV | rate | **USED** (perf-2-bo3, provisional) | validated vs HLTV 2026-10-09 (`disc_audit.py`, categories fixed before counting): 970 BO3.gg maps, 866 matched independently, 832 identical on kills / HS / rounds. The 34 disagreements: **30 BO3.gg exactly 1 kill LOWER, deaths and rounds equal** (6 of them overtime maps; in about half the missing kill was a headshot) - a consistent sign, so a scoring-rule difference rather than random error, cause not identifiable without demos; **3 large unexplained** (XeeDo 4 vs 15 with fewer deaths = partial BO3.gg record; Sliimey 20 vs 17 and Djon8 20 vs 16, BO3.gg HIGHER - one source wrong, unresolved); **1 restarted map** (4 vs 14 rounds); **0 substitution maps**. The 104 unmatched: 47 matches not in HLTV, 48 HLTV lists another opponent name that day (team renamed / roster under another tag, e.g. CYBERSHOKE Prospects vs ex-RUSTEC), 8 ambiguous (same opponent twice in 2 days), 1 map missing in HLTV. Effect: fallback projections about -0.035 kills/map low; settlement - Map 1 / Maps 1-3 settle from HLTV; Maps 1-2 from HLTV when it has the match (428 board lines) else BO3.gg (529 picks Oct 2026: 442 member, 87 board; none of them in HLTV, so not cross-checkable); expected result flips from the 1-kill rule: about 0.3% of those picks (estimate: 7% of Maps 1-2 totals off by one x about 5% of totals sitting exactly at the line edge) |
| Team strength (Elo from results) | HLTV results (yes) | all | win scenario | **TESTED, REJECTED** (twice) | b = 0 on TRAIN; re-tuned on VALID 2026-10-09: b 0.05, TEST MAE -0.002 (95% -0.005 to +0.001); underdog bias +0.76 -> +0.53 only |
| Team-level prior for thin history | HLTV (yes) | all | shrink target | **TESTED, REJECTED** | MAE -0.002, within noise (the newcomer prior replaced it) |
| Stronger shrink for thin history (k0 600 / 1000) | HLTV (yes) | all | rate | **TESTED, REJECTED** | VALID MAE worse (6.355 -> 6.362 / 6.370) |
| Expected rounds: matchup features (Elo gap, recent map lengths, LAN) | HLTV (yes) | all | rounds | **TESTED, REJECTED** | rounds MAE 5.07 -> 5.06; kills unchanged |
| Map 1 specifics (own rounds 21.77, map-1 history, BO1 history) | HLTV (yes) | all | Map 1 | **TESTED, TIED** | Map 1 candidate not better than live Map 1 (MAE 3.19 vs 3.14) |
| Maps / veto scenarios (G4) | HLTV raw_pages veto (yes for history) | all | rounds + rate | **CANDIDATE REJECTED - map information NOT exhausted** | One estimator (each map's average rounds + the player's shrunk per-map kill rate), even given the maps actually played: TEST -0.001 (95% -0.002 to +0.000). This rejects that candidate only. Untested ways maps could matter: map-specific round DISTRIBUTIONS (variance, overtime), side effects per map, map x opponent interactions |
| Eco-adjusted / rating family (G2): damage per kill, eco-adjusted kill share, deaths/round, KAST, rating beyond kills, round swing | HLTV player_stats (yes) | all | rate / HS | **TESTED, REJECTED for kills**; HS: small signal, re-test on perf-2 | kills TEST -0.002 (95% -0.008 to +0.003). HS TEST -0.014 (-0.022 to -0.006) on top of perf-1 - part of it is the HS shrink now fixed in perf-2; re-test this group on top of perf-2 before any use |
| Schedule load (G5): days since last map, maps in last 7 / 30 days | HLTV dates (yes) | all | rate | **TESTED, REJECTED** | TEST +0.001 (95% -0.003 to +0.005). Legacy rest bonus stays legacy-only |
| AWP share (G1) | HLTV player_awp: CURRENT snapshot only; **dated daily snapshots collected from 2026-10-09** (`role_snapshots.jsonl`, `/admin/role_snapshots`) | most board players | rate / HS / spread | **UNAVAILABLE historically**; proxy tested | proxy (shrunk HS/kill < 0.38): kills TEST -0.002 (95% -0.004 to +0.001); HS effect = the HS shrink (now perf-2); spread for low-HS players 1.486 TRAIN vs 1.347 TEST (flips) - rejected. **Collect:** dated daily snapshots of AWP kill share per player (scraper table with a date column) so the share known before each match can be tested |
| Weapon usage beyond AWP share | HLTV player_weapon_kills (90 d snapshot) | up to 300 players/scan | HS propensity | **UNAVAILABLE historically; daily snapshots collected from 2026-10-09** (in the role snapshot when the table exists) | - |
| CT / T side splits | HLTV player_stats sideid 1/2 (in the DB, not in the export) | all | rate x starting side | **NOT PROMOTED (bound too small)** | Every map gives each side a 12-round half; knowing the starting side moves expected kills by (12 - 9.74)/2 x (CT - T kills/round) = 1.13 x gap per map: a 0.1 gap = 0.23 kills on Maps 1-2 vs MAE 6.1. **Data:** history already in the HLTV DB; exported from 2026-10-09 by `/admin/hltv_sides` (per-side player rows + per-side scores; sideid meaning to confirm on known matches). Still needed: the veto's side choice. Then test as a group (bound above is for the average player; individual gaps may be larger) |
| Market: Underdog line movement (timestamped) | Underdog log since 2026-10-04 (yes) | 2,106 player lines, 589 moved | anchor / info | **TESTED (dev), NO SIGNAL** | 170 settled verified Maps 1-2 lines that moved (36 matches): result in the direction of the move 78/170 = 46% (95% about 38-53%). Keep collecting; retest at 150+ matches. The logger re-logged unchanged lines after each restart; fixed 2026-10-09 (seeds from the log) |
| Map HS modifiers (Mirage +6% etc.) | owner's fixed numbers | - | HS | **TESTED, REJECTED (earlier)** | league map HS factors about neutral; legacy pipeline only |
| Online +6% on top of online-only stats | fitted Oct 2-6 | - | rate | **REMOVED (G5)** | double counting |
| Rest bonus +4% / rust -8% / star cut / hot form / regional / H2H blends | legacy pipeline | - | rate | **LEGACY ONLY** (not in perf) | - |
| Teammate competition for kills (team kill share) | HLTV (yes) | all | rate | **NOT YET TESTED** in perf | next cycle |
| Roster continuity / stand-ins | BO3.gg + HLTV lineups (partly) | partial | confidence / rate | **GATES ONLY** (units) | - |
| Event tier / stage / format (BO1/BO3) | HLTV events, scrape_queue format (yes) | most | rate / rounds | **PARTLY** (BO1 history blend tied) | - |
| Market: series / map win prices | Polymarket (live only) | when listed | scenario | **NOT IN perf**; dated since 2026-10-08: each forecast stores the series win chance (p_win) with its time; market quality is in the pick log | test once enough settle |
| Cross-book lines (PrizePicks vs Underdog) | not collected | - | anchor | **UNAVAILABLE** | **Collect:** a second book's lines with timestamps |
| Opening duels, trades, multikills, clutches | not in the HLTV DB | - | - | **UNAVAILABLE** | `ek`/`ed` are eco-adjusted, NOT openings |
| Economy / buy rounds, server / ping | not collected | - | - | **UNAVAILABLE** | - |

Scripts (scratchpad, dev): factor_groups.py (G1/G2/G4/G5), khs_check.py / khs_eval.py / kappa2.py (HS shrink), newcomer.py,
weak_seg.py, perf2_eval.py (through perf.py), bo3_validate.py, moves_check.py.

How much perf-2 improved: about 0.03-0.04 kills / headshots of average error on held-out HLTV data - small. On board Over/Under
probabilities the gain is inside the uncertainty. It does NOT yet show members' grades are reliably more accurate; that is
decided only by forecasts made after G8.1 (`83b2b67d6b`) against the logged previous model (`perf1_g7`, `pre_fix_v2`),
scored by `forward_g8.py` (rules fixed 2026-10-09, HLTV-settled lines only, no conclusion before 150 matches).

The simulation's match-script rows (2-0 / 1-1 / 0-2, overtime) are heuristic: ordering the grade's own totals by a script
makes them add up, but their conditional chances have not been validated.

Next: re-test G2 on HS on top of perf-2; teammate kill share; CT/T and AWP once dated data is collected; line movement at
150+ matches; confirm perf-2 on matches from 2026-10-09 on against the logged perf-1 (G7) comparison.

## Frozen test definitions - round 2 (written 2026-10-09 BEFORE running; not tuned afterwards)
All on HLTV history through perf.py (perf-2 = G8.1 live), splits TRAIN < 2026-07-01, VALID < 2026-08-13, TEST after;
pass = TEST improves with the match-grouped 95% interval below 0 AND the TRAIN/VALID direction agrees.
- R2-maps (map rounds distribution): per actual map pair, the spread of perf-2 Maps 1-2 kills residuals (z = (y - lam)/(C sqrt lam))
  estimated on TRAIN; candidate = per-map-pair spread multiplier (shrunk 50 maps to 1). Metric: TEST log-score of the kills
  distribution. ORACLE (actual maps) -> an upper bound; a veto model could only do worse.
- R2-ctt (CT/T): per-player |CT kpr - T kpr| on history (HLTV side rows) -> the size of the effect knowing the starting side
  would have: 1.13 x gap per map. Value needs the starting side before the match (not collected) -> decision is a data
  dependency unless the measured gap is negligible.
- R2-igl (roles): mean perf-2 residual for IGLs vs others (current Liquipedia role, applied to history = proxy) on TEST.
- R2-g2hs (eco/rating group on HS): the G2 ridge from round 1, refit on TRAIN on top of perf-2 (not perf-1), HS target.
- R2-moves (line movement, more data): share of settled verified moved lines whose result went the move's way, Oct 4 onward.

## Round-2 results (run 2026-10-09 after freezing the definitions above)
| Test | Result | Decision |
|---|---|---|
| R2-maps: map-specific spread (ORACLE maps) | TRAIN multipliers 0.99-1.05; TEST log-score -0.0002 (95% -0.0009 to +0.0006); VALID +0.0007 (-0.0005 to +0.0020) | **Rejected in this formulation** (map rounds variance). Map identity / vetoes remain untested as map x opponent interactions |
| R2-ctt: CT/T | 92,982 side rows (Jul 10 - Oct 8): median |CT-T| gap 0.110 kills/round but early vs late gap correlation r = 0.16 (365 players) -> the stable part is worth ~0.04 kills on Maps 1-2 (90th pct 0.08) | **Rejected (measured bound negligible)** - not a data block: even with the starting side known it could not move a projection meaningfully |
| R2-igl: IGL role | perf-2 residual IGL vs others: TRAIN +0.16 vs +0.51, VALID +0.20 vs +0.45, TEST -0.15 (95% -0.48 to +0.22) vs +0.24 -> IGLs ~0.3 kills lower, same direction in all three periods | **Shadow candidate** (proxy: today's roles applied to history). Promotion needs dated role history (collected daily since 2026-10-09) and a forward test |
| R2-g2hs: eco/rating on HS (on perf-2) | TEST HS MAE +0.0013 (95% -0.0017 to +0.0042) | **Rejected** - round-1 signal was the HS shrink, now in perf-2 |
| R2-moves: line movement | 65/144 HLTV-settled moved lines (31 matches) followed the move: 45% (about +-8%) | **Rejected in this formulation**; collection continues |

## Final status by group (2026-10-09)
| Group | Status |
|---|---|
| Player rate, rounds exposure, environment, opponent conceded, HS/kill, newcomer prior | **Used** (perf-2) |
| Map / veto | Rejected as tested (map means, oracle; map spread, oracle). Untested: map x opponent interactions - blocked on dated veto/side-choice collection |
| CT/T | Rejected (measured bound ~0.04 kills) |
| Roster / roles (IGL) | Shadow candidate; dated roles collected daily from 2026-10-09 |
| AWP usage / weapon mix | Proxy rejected; **blocked by data** until dated daily snapshots (started 2026-10-09) cover enough history |
| Eco-adjusted / rating stats | Rejected (kills: round 1; HS: round 2) |
| Opponent / team strength | Rejected (Elo twice; matchup rounds); market win price - **blocked by data** (stored with forecasts only since 2026-10-08; too few settled) |
| Schedule load | Rejected |
| Timestamped markets (line movement) | Rejected as tested; collecting |
| Independent projections / second book | **Blocked by access**: none accessible (PrizePicks CAPTCHA; OddsPapi free quota exhausted - player-prop coverage unknown) |

## Frozen test definitions - round 3 (written 2026-10-10 BEFORE running; owner asked for every factor possible)
Baseline: perf-2 live (G8.3, `12f83a9da0`) through perf.py, day = the match day, history strictly before it.
Target: relative kills residual (y - lam) / lam on Maps 1-2 (HS target for R3-share only, as a second check).
Every feature uses only data from days before the match. Each feature is standardised on TRAIN.
Fit: one coefficient per feature (single-feature tests) and one ridge over all of them (group test) on TRAIN.
The ridge strength is chosen on VALID from {0, 1, 10, 100, 1000} x n.
Scoring: TEST kills MAE and normal log-score, both vs the baseline, with match-grouped 95% intervals.
Pass = TEST improves with the interval below 0 AND the coefficient has the same sign fitted on TRAIN and on VALID.
A pass becomes a shadow challenger first; promotion needs the owner's go-ahead (stated with the evidence).
- R3-share: his time-weighted share of his team's kills (same half-life as the rate) - star vs 4th / 5th rifle.
- R3-form: last 10 series kills per round vs his time-weighted rate, shrunk 300 rounds: (k10 + 300 rate) / (r10 + 300) / rate - 1.
- R3-h2h: his kills per round in earlier meetings with this opponent team (any lineup, 365 days, half-life 120 d) vs his
  rate, shrunk 300 rounds.
- R3-oppagg: the opponent team's own kills per round (time-weighted) vs the average - more fights, more kills.
- R3-blowout: (Elo win chance - 0.5) x (share - 0.2), with the Elo main effect in the same model (blowout compression:
  the losing side gets fewer kill chances; the high-share player on the winning side is not compressed).
- R3-stage: event class from its name - qualifier / cup / open / league (tier 3 online) vs major / playoffs / final.
  Two indicators.
- R3-opproster: share of tonight's opposing five who did NOT play the opponent's previous series (roster change).
- R3-sameday: he already played a series earlier that same day (lower match id, same date).
Untestable here (data not in the HLTV DB, or no dated history yet):
- opening, trade, multi-kill, exit and eco kills;
- economy, pistol and force rounds;
- ping and server;
- AWP and rifle HS splits;
- opponent AWPer;
- veto pick / ban and starting side;
- region style clash (team countries not in the export).

## Round-3 results (run 2026-10-10 after freezing the definitions above; 42,587 series-players, TEST 13,255 / 1,343 matches)
Baseline TEST kills MAE 6.085. Effects per standard deviation are fitted on TRAIN, as a share of the projection; dMAE is TEST.
| Test | TRAIN / VALID coefficient | TEST dMAE (95%) | Decision |
|---|---|---|---|
| R3-share (team kill share) | +0.0003 / +0.0027 | -0.0000 (-0.0002 to +0.0001) | **Rejected**: the player's rate already carries his share |
| R3-form (last 10 series) | -0.0001 / +0.0002 | +0.0001 (+0.0000 to +0.0001) | **Rejected**: the 120-day weighting already carries recent form |
| R3-h2h (vs this org) | +0.0017 / +0.0041 | -0.0001 (-0.0011 to +0.0008) | **Rejected**: no measurable effect (38% of rows have a meeting) |
| R3-oppagg (opponent kill pace) | -0.0011 / +0.0003 | +0.0001 (-0.0006 to +0.0009) | **Rejected** |
| R3-sameday | +0.0021 / -0.0025 | +0.0006 (-0.0012 to +0.0024) | **Rejected** (sign flips) |
| R3-opproster | +0.0016 / +0.0020 | +0.0008 (-0.0002 to +0.0019) | **Rejected** (worse on TEST) |
| R3-blowout (Elo + Elo x share) | +0.0002, +0.0006 / -0.0028, -0.0013 | -0.0002 (-0.0005 to +0.0002) | **Rejected** (sign flips) |
| R3-stage | -0.0014, -0.0040 / +0.0025, +0.0031 | -0.0005 (-0.0021 to +0.0011) | **Rejected**. Raw residual: qualifier / cup / open / league rows run 0.15 (TRAIN) to 0.46 (TEST) kills lower, but VALID points the other way. Re-test with more data |
| R3 group (all ten, ridge) | - | +0.0008 (-0.0018 to +0.0036) | **Rejected** |
Sanity check (`r3_quint.py`): mean residual by factor quintile moves by a few tenths of a kill with no consistent order between
TRAIN and TEST, so the null result is not a computation artefact. Nothing promoted; the live projection is unchanged.

## Frozen test definitions - round 4: how the kill rate is estimated (written 2026-10-12 BEFORE running; owner-approved "option 1")
Why: the 10-day board diagnosis (Oct 3-12; Maps 1-2 kills: 360 units, 94 matches) found that our distance from the book line
carries ~no information on the Over side (projection +2.35 above the line -> result -0.22 vs the line; Under side -2.35 ->
-1.50); slope of (actual - line) on (projection - line) 0.38 (95% -0.24 to 0.83); expected rounds are not the cause.
Note before running: 259 of those kills units were projected by the older pipeline, not perf-2, so the board check
RECOMPUTES perf-2 (and every candidate) for every unit from the HLTV database.
Baseline: perf-2 live (perf.PARAMS) through the same formula as perf.project, HLTV export (series-players from 2025-09-01,
TRAIN < 2026-07-01, VALID < 2026-08-13, TEST after), target Maps 1-2 kills. Spread C for every candidate is re-measured on
TRAIN residuals by the rule that reproduces perf.SPREAD for the baseline (checked first).
Candidates (one family each; the grid value is picked on TRAIN kills MAE only, never on VALID or TEST):
- R4-k0: prior strength k0 in {150, 450, 600} rounds (live 300).
- R4-hl: half-life hl in {60, 90, 180, 240} days (live 120; the one hl drives player, environment and opponent sums).
- R4-hot: asymmetric shrink of rates ABOVE the population target T = pop x prior_m: if the final rate > T,
  rate = T + a (rate - T), a in {0.9, 0.8, 0.7}; rates below T unchanged (hot-streak / high-rate regression).
- R4-ko: opponent-factor shrink ko in {150, 600, 1200, infinite = opponent factor 1} (live 300).
- R4-thin: spread multiplier m in {1.05, 1.1, 1.2, 1.3} for players with time-weighted history < 600 rounds. Projection
  unchanged, so the board gap cannot move by construction; scored on log-score (MAE unchanged).
- R4-team: team consistency: each player's rate x (S_team / sum of the five players' rates)^w, S_team = his team's own
  kills per round (time-weighted, shrunk 1,500 rounds toward 5 x pop), w in {0.5, 1}; the five = the lineup that played.
HLTV metrics: TEST Maps 1-2 kills MAE and discretised-normal log-score vs the baseline, match-grouped 95% bootstrap
intervals (1,000 resamples). Diagnostic only (not a test): calibration slope of actual on projection on TEST, by decile of
rate / pop.
Board metrics (kills Maps 1-2 units with an HLTV result and HLTV history; perf-2 recomputed with the live SQL, history
strictly before the match day, LAN from the HLTV match): B1 slope of (actual - line) on (projection - line), all units;
B2 Over-side gap = mean(projection - actual) on units with projection - line > 1 (each model's own Over set) and the paired
difference candidate - baseline with a match-grouped 95% interval; B3 Under-side gap and overall bias (a pure downward
shift would shrink B2 while hurting the Under side - reported to expose that).
Pass = (1) TEST improvement with the 95% interval excluding 0 (MAE; log-score for R4-thin) AND (2) the same direction on
TRAIN and VALID AND (3) the board Over-side gap (B2) shrinks. The board set is small (~94 matches) - its interval is
reported, not required to exclude 0. A pass becomes a frozen SHADOW challenger config (perf PARAMS json); members' grades
are not changed by this round.

## Round-4 results (run 2026-10-12 after freezing the definitions above; scripts `round4/r4_hltv.py`, `r4_board_pull.py`, `r4_board_eval.py`)
HLTV: 42,587 series-players (TRAIN 24,479 / VALID 4,853 / TEST 13,255, 1,343 TEST matches). The candidate code reproduces
perf.project exactly (max difference 7e-14) and the spread rule reproduces perf.SPREAD (1.457). Baseline TEST kills MAE 6.085.
Board: 360 kills Maps 1-2 units, 80 HLTV matches (94 board match slugs); recomputed perf-2 matches the logged perf-2
lambda on the 102 units that were live perf-2 (mean |diff| 0.09). Board intervals are wide - 80 matches.
| Test | Picked on TRAIN | dMAE TRAIN / VALID | TEST dMAE (95%) | TEST log-score (95%) | Board Over gap vs perf-2 (95%) | Decision |
|---|---|---|---|---|---|---|
| R4-k0 | 450 | +0.0023 / +0.0008 | -0.0012 (-0.0045 to +0.0023) | -0.0004 (-0.0009 to +0.0001) | +0.03 (-0.26 to +0.28) | **Rejected** |
| R4-hl | 180 d | -0.0003 / +0.0046 | +0.0015 (-0.0025 to +0.0054) | +0.0004 (-0.0002 to +0.0009) | +0.16 (-0.34 to +0.61) | **Rejected** |
| R4-hot | a 0.9 | -0.0005 / -0.0034 | -0.0057 (-0.0119 to +0.0008) | -0.0008 (-0.0018 to +0.0000) | +0.02 (-0.36 to +0.38) | **Rejected** (interval crosses 0; gap not smaller) |
| R4-ko | 150 | -0.0011 / -0.0005 | +0.0026 (-0.0008 to +0.0063) | +0.0004 (-0.0003 to +0.0011) | -0.03 (-0.34 to +0.28) | **Rejected** |
| R4-thin | m 1.05 | log +0.0005 / +0.0025 | - | +0.0008 (-0.0004 to +0.0022) | 0 by construction | **Rejected** |
| R4-team | w 0.5 | +0.0016 / +0.0012 | +0.0116 (+0.0029 to +0.0210) | +0.0005 (-0.0013 to +0.0023) | +0.32 (-0.14 to +0.91) | **Rejected** (worse) |
Board (kills Maps 1-2): live logged lambdas (mixed: 258 older-pipeline + 102 perf-2) slope 0.38 (-0.26 to 0.81), Over side
proj-line +2.35 -> actual-line -0.22 (gap 2.56). perf-2 recomputed for all 360 units: slope 0.57 (-0.26 to 0.93), Over side
+1.96 -> +0.02 (gap 1.93), Under side gap -1.17, bias +0.42, MAE 5.46 vs the line's 5.55. No grid value of any family shrinks the
Over gap by more than 0.30 (R4-hot a 0.7, which TRAIN rejects: MAE +0.020) and none changes the slope by more than 0.1.
Diagnostic: HLTV calibration slope of actual on projection 0.97 / 0.95 / 0.99 (TRAIN / VALID / TEST); top rate decile
TEST -0.21 kills (-0.65 to +0.25) - high-rate players are NOT over-projected in history.
EXPLORATORY (after the tests, not a test): on the board the Over-side failure of perf-2 sits mostly in thin-history players
(weighted history < 600 rounds: 42 units, 24 matches, bias +4.2 kills, Over gap +4.4 on 26 Over units; the book was also
too high on them, line - actual +3.1). Units with >= 600 rounds: slope 0.75, Over side +1.83 -> +0.73. In HLTV history
thin-history players are slightly UNDER-projected (-0.1 to -0.7 in every period), so this is a board-specific hypothesis
(stand-ins / returning or re-tagged players / tier-3 board selection) for a frozen round-5 test, not evidence.
Nothing promoted; the live projection is unchanged.
