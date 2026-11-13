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

## Frozen test definitions - round 5: thin / returning / low-coverage history (written 2026-10-12 BEFORE running)
Why: round-4 exploratory finding + audit5 (41 thin units / 20 matches: 22 missing ingestion, 9 return from inactivity,
7 genuinely limited, 3 borderline). The inspected 41 units and the whole Oct 3-12 board are DEVELOPMENT data, never a test.
Unchanged throughout: the live model (perf-2) and the frozen challenger lb-001 (probit pull toward 50%, kills w 0.0, HS w 0.5).
Only data dated before the match day defines a cohort. Scripts: research folder `round5/` (r5_*.py).
**Cohorts** (D = match day; his rows = the HLTV maps perf-2 uses: his one HLTV id, all environments, 10-player maps):
- r_w = sum exp(-(D - day)/120) x rounds over his rows with day in [D-420, D) (= perf.project history_rounds_weighted, logged live).
- Thin: r_w < 600. Graded alternative (reported, not separate tests): bands < 300, 300-600, 600-1200, >= 1200.
- Returning: gap = D - his last HLTV map day >= 60 days. No HLTV map at all before D: not "returning" (it is thin, r_w 0);
  for C2 its gap counts as 365.
- Low coverage (board only): cov = his HLTV maps in [D-420, D) / his BO3.gg finished maps (>= 13 rounds) in the same window
  < 0.75; no BO3.gg maps -> not low coverage. Production has this pre-match (BO3.gg match list, HLTV DB). Historically: the
  BO3.gg lists pulled 2026-10-12 (audit5 cache) and HLTV rows as of 2026-10-12, both filtered to days before D. No result
  of the graded match enters; the caveat is "as-of": a map ingested/added after D counts here but did not live.
- Established = none of the three (r_w >= 600, gap < 60, cov >= 0.75 or unknown).
**Candidates** (each on its own; nothing stacked):
- C0 DATA FIX (recorded separately; not a model change): for low-coverage units, his BO3.gg-only maps (games in his BO3.gg
  list in [D-420, D) whose match is not an HLTV match he has rows in - audit5 cross-match rules; >= 13 rounds) are added to
  his kill and round sums with the same weight exp(-(D-day)/120); kills from the BO3.gg scoreboard by his BO3.gg player id
  (a scoreboard without him is skipped). Environment unknown -> all-environment rate only, not the LAN/online sums.
  Opponent factor and spread (1.457) unchanged. Board only: the 42k HLTV set has no HLTV-to-BO3.gg identity map
  (exact-name rule, no fuzzy matching) and would need ~10^5 scoreboard downloads - infeasible, so no held-out test.
- C1 shrinkage by effective sample: k0_eff = 300 x (1 + a x max(0, 1 - r_w/600)), a in {0.5, 1, 2} (prior target
  0.93 x pop unchanged). Rows with r_w >= 600 are unchanged by construction. Spread C re-measured by the round-4 rule.
- C2 inactivity uncertainty: sd x (1 + b x min(gap, 365)/365), b in {0.1, 0.2, 0.4}; projection unchanged. C re-measured by
  the round-4 rule on z / multiplier, so total TRAIN dispersion is unchanged and uncertainty moves toward long gaps.
- C3 targeted line blend (kills, board only - needs lines): p' = Phi(w x Phi^-1(p)), w = r_w / (r_w + m), p = perf-2
  replay chance (78% cap, as live). m = 300 is fixed a priori (the line acts as a prior worth k0 = 300 rounds, the model's
  own prior strength); m = 150 and 600 are sensitivity only, never selectable. Gradual in r_w; no switch at 600.
**HLTV test (C1, C2)**: round-4 harness (perf-2 replica, Maps 1-2 kills, rows with HLTV history > 0). Periods: the export
starts 2025-10-02, so r_w is truncated early on (a continuously active player misses exp(-t/120) of his weight t days after
the start); parameter selection therefore uses TRAIN = 2026-04-01..06-30 only; VALID 07-01..08-12; TEST 08-13..10-08
(held out for these parameters). C1: a picked on TRAIN thin-cohort MAE. C2: b picked on TRAIN whole-set log-score.
Primary metric: C1 = TEST thin-cohort kills MAE change; C2 = TEST returning-cohort log-score change (discretised normal).
Also reported: bias, MAE, log-score, 80%-interval coverage, z-dispersion, by cohort / r_w band / established / whole set,
TRAIN, VALID, TEST, match-grouped 95% bootstrap (1,000), distinct matches. Brier / line calibration need lines (board only).
**Board dev set (all candidates)**: the 328 eligible deduplicated kills Maps 1-2 units / 80 HLTV matches (audit5), identical
rows for: live published, perf-2 replay, market (p = 0.5, which for kills IS lb-001: w 0.0), C0, C1 and C2 at their HLTV-picked
value (other grid values shown), C3 (m 300; 150 / 600 sensitivity). Metrics: bias and MAE (projection candidates; the
line's own bias / MAE shown), log-loss, Brier, logistic calibration slope, calibration bins; paired differences vs perf-2
replay with match-grouped 95% intervals (2,000); n units, distinct matches; all / each cohort / r_w bands / established.
Data hash = sha256 of the sorted (player, match slug, line, actual) list, first 16 hex.
**Promotion criteria** (to a SHADOW challenger only; members' grades unchanged; owner decides any promotion):
- C1 / C2 (HLTV-testable): (1) primary TEST change in favour with its 95% interval excluding 0; (2) same direction on
  TRAIN and VALID; (3) established not hurt: C1 established rows identical (checked), C2 established TEST log-score change
  >= -0.001 and whole-set TEST log-score change >= 0 (point estimates); (4) board dev targeted cohort not significantly worse
  (reported; a 95% interval entirely on the worse side blocks).
- C0 / C3 (board-only, development evidence): may be frozen only if (1) targeted-cohort board log-loss vs perf-2 replay
  improves with the 95% interval excluding 0; (2) whole board set not worse (point); (3) established units unchanged or not
  worse (point); C3 additionally (4) beats lb-001 global (p = 0.5) on the thin cohort (point) - otherwise lb-001, already
  frozen, carries the gain and C3 adds nothing.
- At most ONE is frozen. If several qualify: HLTV-tested before board-only; then the larger primary effect / CI half-width.
  If none qualifies, nothing is frozen. The frozen config is fixed before any new-match evaluation and reviewed on forward
  matches only (learning.REVIEW_RULE windows 300 / 600 / 1000 distinct matches).

## Round-5 results (run 2026-10-12 after freezing the definitions above at 17:00Z; scripts `round5/r5_cov.py`, `r5_games.py`, `r5_hltv.py`, `r5_board.py`; tables `r5_hltv_tables.txt`, `r5_board_tables.txt`)
Checks: harness r_w equals the 420-day definition (max diff 5e-13; the export starts 2025-10-02); baseline C 1.4572 = perf.SPREAD;
board perf-2 recomputed = audit5 replay (max diff 0). Board dev set: 328 units / 80 HLTV matches, data hash `bafe9e4b078aa906`.
**HLTV (held-out TEST 13,255 rows / 1,343 matches).** Baseline perf-2 by cohort, bias (projection - actual): thin TEST -0.17
(-0.64 to +0.29; 3,392 rows / 887 matches), TRAIN(Apr-Jun) -0.21, VALID -0.47 - thin players are NOT over-projected in HLTV
history (every r_w band within +-0.7 kills, all intervals cross 0). Returning (gap >= 60): only 98 / 121 / 127 rows; TEST
bias +1.17 (-0.25 to +2.46, 83 matches) but z-dispersion 0.92 (LESS spread than assumed, not more).
| Test | Picked on TRAIN(Apr-Jun) | Primary TEST (95%) | TRAIN / VALID same metric | Established | Decision |
|---|---|---|---|---|---|
| C1 shrink by r_w | a 0.5 (TRAIN thin dMAE +0.0001; a 1: +0.0009, a 2: +0.0028) | thin dMAE +0.0016 (-0.0033 to +0.0068) | +0.0001 / +0.0048 | identical (max dlambda 0) | **Rejected** (no gain; worse on returning rows: TEST dMAE +0.017, +0.0005 to +0.031) |
| C2 inactivity spread | b 0.2 (TRAIN whole-set dlog +0.00004) | returning dlog -0.0082 (-0.0213 to +0.0086) | -0.0085 / -0.0123 | TEST dlog +0.0001; whole +0.00002 | **Rejected** (returning rows get worse every period; 80% coverage 0.80 -> 0.85, over-wide) |
**Board (DEVELOPMENT, 80 matches).** All eligible: replay log-loss 0.692, market 0.5 (= lb-001 kills) 0.693, published 0.710.
Cohorts: thin 39 units / 20 matches (25 also low coverage), low coverage 156 / 58, established 158 / 58, RETURNING 0 - by the frozen
rule (days since the last HLTV map) no board unit qualifies (max gap 27 d): the 9 audit5 "return from inactivity" units had
already played 1+ maps after the break, so the rule only catches the first match back. C2 is therefore untestable on the board.
| Candidate vs perf-2 replay | thin (39 / 20) | low coverage (156 / 58) | established (158 / 58) | all (328 / 80) |
|---|---|---|---|---|
| C0 data fix: dMAE | +0.207 (+0.043 to +0.354) | +0.065 (+0.002 to +0.123) | 0 (not applied) | +0.031 (+0.002 to +0.062) |
| C0 data fix: dlog-loss | +0.021 (-0.002 to +0.041) | +0.009 (+0.001 to +0.016) | 0 | +0.004 (+0.000 to +0.008) |
| C1 a 0.5: dlog-loss | -0.002 (-0.009 to +0.007) | -0.001 (-0.003 to +0.001) | 0 | -0.000 (-0.001 to +0.001) |
| C2 b 0.2: dlog-loss | +0.000 | +0.000 | -0.000 | +0.000 |
| C3 m 300: dlog-loss | -0.054 (-0.094 to -0.003) | -0.017 (-0.031 to -0.002) | +0.003 (-0.002 to +0.008) | -0.007 (-0.016 to +0.001) |
| lb-001 global (p 0.5): dlog-loss | -0.062 (-0.128 to +0.021) | -0.029 (-0.062 to +0.005) | +0.030 (-0.000 to +0.059) | +0.001 (-0.023 to +0.025) |
| C3 m 300 minus lb-001: log-loss | +0.007 (-0.030 to +0.039) | +0.012 (-0.009 to +0.033) | -0.027 (-0.052 to -0.002) | -0.008 (-0.025 to +0.009) |
C0: BO3.gg-only maps (14,563 with his scoreboard of 17,818 listed) RAISE the low-coverage projections (+0.21 kills; thin +0.49) -
those tier-3 maps are high-kill maps, and the thin units were already over-projected (replay bias +4.23, line bias +3.14) - so
the data fix makes every board metric worse. The thin-cohort miss is not missing data in the rate; it is board-specific
(the book line was also +3.1 too high on the same units). C3 m 150 / 600 (sensitivity): thin -0.046 / -0.060, established
+0.001 / +0.006. C3 helps thin units about as much as lb-001 does and does not beat it there (frozen criterion 4), and its
established point estimate is +0.003 (criterion 3). By r_w band, C3 vs lb-001: < 300 +0.029 (+0.003 to +0.046) worse,
300-600 -0.013, 600-1200 -0.003, >= 1200 -0.018.
**Decision: nothing frozen.** C1, C2 fail the HLTV test; C0 makes the board worse; C3 fails criteria 3 and 4 (lb-001, already
frozen, carries the thin-history gain). Live model and lb-001 unchanged. Open item (NOT tested, would need a new frozen round on
new matches): a "returning" definition that survives the first match back (e.g. a >= 60-day break within the last 30 days).

## Round 5 - record note (added 2026-10-12, owner review)
Development research. The preregistration timing (definitions written 17:00 UTC, before the runs) is supported only by
file timestamps - definitions and results were committed together (c23f763, 17:14 UTC). The acceptance rule is not changed
after the result. C3's thin-history result is a lead for validation on new matches, not a basis for promotion. No
adjustment experiment runs until the HLTV coverage repair and the staged before/after comparison are done (HLTV_COVERAGE.md).

## Frozen test definition - round 6: market information in the projection (rr-1 / RR-KS) - frozen 2026-10-13 17:51 UTC
**Owner authorization (2026-10-13):** build and test the missing Maps 1-2 market integration; this one experiment is
run although the coverage gate above was still open (the HLTV backfill finished 2026-10-13 ~17:30 UTC; the staged
before/after replay is still to be run and is not part of this test).

**What was already inspected (honest record).** The definition below was chosen AFTER exploration: three designers
(empirical-closeness, round-race, scenario-dedup) and three judges fitted and compared candidates on TRAIN rows only
(HLTV date < 2026-07-01) between ~14:40 and ~17:00 UTC today; by their reports no VALID or TEST outcome was computed for
any candidate. The three kill-share designs proved to be one signal on TRAIN (projection correlation 0.91-0.995,
Maps 1-2 kills MAE vs perf-2 -0.059 / -0.060 / -0.061, differences within +-0.003); RR-KS was chosen on engineering
grounds (frozen live functions, exact perf-2 result without a price, no lineup data, no change to the perf-2 queries),
not on any held-out number. The TEST period (>= 2026-08-13) is NOT fresh data in general: rounds 1-5 and challenger M1
used its outcomes for other factors. **Untouched by anyone:** HLTV matches after 2026-10-08 (not in the export) and the
lower-tier matches added by the 2026-10-13 backfill - reserved for the forward shadow evaluation.

**Formula (rr-1, variant rr-ks).** p = pre-match series win chance of the player's team from the selected qualified
market (Thunderpick proportional margin-adjusted when eligible, else Polymarket midpoint); bo in {1,3,5} (else no market
term). Per-map chance q MODEL-DERIVED from p: BO1 q = p, BO3 p = q^2(3-2q), BO5 p = q^3(10-15q+6q^2) (maps independent,
equal q). A calibrated round race (MR12, overtime blocks, per-map round-win chance ~ Beta with kappa 22, match-point
effect eta 0.03, normalised so the TRAIN mean is 21.74 rounds) gives, per map, the expected rounds the team wins W(q) and
loses L(q) (and given a map win / loss). Kill-weighted rounds K(q) = a W(q) + b L(q), a = 1.4323, b = 0.5924 (kills per
round won / lost relative to the perf-2 per-round rate, TRAIN least squares on 44,302 player-maps; ratio stable
2.40-2.44 across TRAIN thirds). **De-duplication:** the perf-2 rate already contains the strength of his own team and the
opponent factor that of the opponent, so K is divided by H = M(rho_team) M(1 - rho_opp) / M(0.5), M(x) = a x + b(1-x),
rho = each team's time-weighted (half-life 120 d) round-win share in its HLTV maps strictly before the match day, shrunk
400 rounds to 0.5 - the market enters only through the difference between today's expected round share and the history
already in the rate and the opponent factor. lambda_kills = ro x n x K(q) / H with ro = perf-2 kills per round
(lambda_kills / (21.74 x maps)); Maps 1-3 of a BO3 uses the 1-1 scenario. Headshots = lambda_kills x HS/kill, HS
probabilities from the beta-binomial HS | kills on the candidate kills (conditional). Spread C = perf.SPREAD unchanged.
No price / BO2 / unknown format / BO1 with a scope other than Map 1 / a non-HLTV projection -> exactly perf-2.
Frozen constants: scratchpad design/round-race/rr_tables.json, copied into the repo as perf_market_tables.json.

**Prices and cutoffs.** Historical: ml_research/dataset.pkl - the last Polymarket (else Kalshi) series price at or before
the earliest scheduled start minus 30 min, <= 6 h old (exchange prices, NOT Thunderpick: historical tests cannot show a
Thunderpick-specific gain). Live: the forecast's own selected price (saved provenance).

**Data / splits / metrics / pass rules.** As in scratchpad design/round-race/rr_test_frozen.py (run once with
--frozen-run): series-players of the HLTV export joined to those prices; TRAIN < 2026-07-01 (fitting), VALID
2026-07-01..08-12 (direction), TEST >= 2026-08-13. Primary: TEST Maps 1-2 kills MAE candidate minus perf-2, paired,
match-grouped bootstrap (1,000, seed 20261013). Pass: (1) primary 95% upper bound < 0; (2) VALID and TRAIN point
estimates < 0; (3) no TEST secondary significantly worse (kills Map 1 / Maps 1-3, HS Map 1 / 1-2 / 1-3 MAE; kills Maps
1-2 log-score; HS Maps 1-2 log-score); (4) rr-ks minus rr-ks-noq (q = 0.5, no market) 95% upper bound < 0 - the gain
must come from the market. Also reported (descriptive, not gating): Brier and calibration of Over/Under at the
projection's own half-line, by match-grouped intervals. **Single primary arm: rr-ks.** rr (rounds only) is descriptive.
**On pass:** a logged SHADOW challenger only (no grade, chance or stake changes); promotion needs the established process
(forward windows, written owner decision, new model version). **On fail:** it stays logged in shadow as a measured
negative result, never promoted. The existing frozen challengers (M1 etc.) are unchanged.

## Round-6 results (run once with --frozen-run, 2026-10-13 ~18:00-18:30 UTC, after the definition above was committed in 3acff48)
| arm | TEST Maps 1-2 kills MAE vs perf-2 (95%, by match) | VALID | TRAIN | vs no-market control | worse secondaries | verdict |
|---|---|---|---|---|---|---|
| **rr-ks** | **-0.0535 (-0.0774 to -0.0337)**, 996 matches | -0.0588 | -0.0590 | -0.0848 (-0.1118 to -0.0580) | none | **PASS** |
| rr (rounds only) | -0.0097 (-0.0219 to +0.0031) | -0.0084 | -0.0033 | (descriptive) | kills Maps 1-3 | FAIL |

Secondaries for rr-ks on TEST: HS Maps 1-2 MAE -0.0256 (-0.0358 to -0.0145), HS log-score better (+0.0055, +0.0032 to
+0.0076); HS Maps 1-3 -0.0119 (-0.0239 to -0.0002). Descriptive: worse when the market underdog won the series
(+0.085 kills, 324 matches), better otherwise (-0.121, 672 matches) - the expected cost of leaning on the market; an
Elo-fed version (rr-ks-elo) shows no gain (+0.0006), so the gain is the market's. Development check on 947 settled
production lines (19 matches): MAE -0.097 (-0.174..-0.022), log-loss -0.010 (-0.020..-0.0005), Brier -0.0048
(-0.0097..-0.0003). No settled Thunderpick-priced line exists yet, so no Thunderpick-specific result.
**Outcome per the frozen rule:** rr-ks becomes a logged SHADOW challenger (CHALLENGERS.md rr-1); it changes no grade,
chance or stake. Promotion only through the forward review windows and a written owner decision. Full log: scratchpad
design/round-race/rr_test_FROZEN_RUN.log.


## Round 7 - market-informed prop challenger pl-1 (defined 2026-10-15, after the corrected checkpoint)

**Honest record.** pl-1 was designed AFTER the 2026-10-14 checkpoint was seen. Its fit data (board standard lines,
2026-10-11..14, 635 units / 36 matches, hash f1274a945c16b1da) are the same days the checkpoint reported, so its
walk-forward numbers are development evidence only. One design change was made after looking: a free spread multiplier
ran to the edge of a widened grid (3.0), so the spread was fixed at the live sd and only w per stat is fitted. The
definition, inputs, threshold-vs-median rule and frozen weights are in CHALLENGERS.md (pl-1) and learning.PROP_LINE;
code `learning.prop_line_value`, scripts session scratchpad ck/pl_fit.py.

| walk-forward (521 units / 30 matches; + = first model better) | log-loss gain (95%, by match) |
|---|---|
| kills: market baseline vs live | +0.0247 (-0.0011 to +0.0529) |
| HS: market baseline vs live | +0.0122 (-0.0102 to +0.0342) |
| both: pl-1 vs live | +0.0073 (-0.0107 to +0.0244) |
| both: pl-1 vs market baseline | -0.0113 (-0.0228 to -0.0012) |

Mean projections, kills (average miss): independent 5.55, market median 5.29, pl-1 5.29 (kills w = 0).
Fold weights: kills 0 / 0 / 0, HS 1.2 / 0 / 0. Frozen: kills 0.0, HS 0.25. Forward test only from its freeze.

**Error diagnosis on the same cohort (descriptive, ck/diag2.py; qualified 2026-10-15 below).**
- **Kills Maps 1-2 (222 units):** the log error splits into rounds (variance 0.011, mean +3.6%, -0.4% to +7.5%) and kill
  rate (variance 0.058, mean +0.5%). Exact rounds would cut the average miss only from 5.31 to 5.22; an exact kill rate
  would cut it to 2.76 (an oracle bound that includes irreducible per-match noise).
- **Headshots (305 units):** most of the error comes from the kills underneath (known kills: 3.72 to 2.53), not the HS
  share (known share: 3.26). Predicted HS share 0.521 vs actual 0.527.
- **Spread:** the 50% interval holds 55% / 53% and the 80% interval 83% / 81% (kills / HS). Not too narrow.
- **Probabilities (Brier decomposition):**
  - kills: calibration error 0.014, resolution 0.002;
  - HS: calibration error 0.009, resolution 0.004.
  The chances barely separate outcomes, and their miscalibration is larger than that separation.
- **Means vs probabilities:** removing the earlier days' mean bias (walk-forward) changes kills MAE by -0.06 and log-loss
  by -0.004, both across 0. A better mean is not a better hit probability; the two are separate objectives.

**Qualifications (owner review 2026-10-15).**
- **The substitutions of actual rounds and actual kill rate are RETROSPECTIVE ORACLE DECOMPOSITIONS.** They use
  information known only after the match. They show where the realised error sits, not which part is predictably fixable.
  - They interact: rounds known 5.31 to 5.22 (-0.09); rate known 5.31 to 2.76 (-2.55); both known 0. The two single
    reductions (2.64) are not additive with the joint one (5.31). In log space the covariance term is -0.0035 of a total
    variance of 0.0654.
- **Interval coverage** (50%: 55% / 53%; 80%: 83% / 81%) is broadly compatible with nominal coverage in this sample. It
  does not show the spread is right for every player, cohort or line.
- **Calibration by stat x cohort** (descriptive only - nothing is tuned on it; the cohorts overlap and multiple
  comparisons apply). Favoured side, printed vs happened:
  - Underdog: kills 57.2% vs 44.6%, HS 56.7% vs 39.8%.
  - PrizePicks: kills 57.2% vs 50.8%, HS 57.2% vs 60.9%.
  - Online: kills 44.3%, HS 45.8% happened; LAN: 51.1% / 53.1%.
  - Thin history (< 500 weighted rounds, 15 matches): kills 60.9% vs 31.0%, HS 58.1% vs 34.0%.
  - Kills 0.25+ sd from the line: 65.7% vs 43.6%.
  - The 80% intervals hold 77-85% in every cohort.
  - These are leads for prospective hypotheses, never fitted on these matches.

## Round 8 - grade-upgrade study (frozen 2026-11-11 in research/grade_upgrade_2026_11_11/FROZEN_DEFINITIONS.md, d8aa3e3, before scoring)

Walk-forward at the REAL offered lines (owner rule 2026-11-09: hit rate by side, graded count, split), Oct 19 - Nov 8:
8,032 graded app rows / 296 matches; live 53.5% [51.6, 55.4], Overs 52.5%, Unders 54.3%. Full table:
cs2_scout_ops/logs/grade_upgrade_cs2_2026-11-11.md; summary research/grade_upgrade_2026_11_11/RESULTS.md.

| Group | Tested on | Result |
|---|---|---|
| H01-H23: opponent strength, similar-ranked opponent, match tier, event class, LAN, form (20 maps / 90 d), time on team, own / opponent roster change, days since last match, long break, team rest, opponent kills allowed, closeness, overtime propensity, kill share, entry, AWPer, IGL, history size, head-to-head, all together (ridge) - as a multiplier on the live projection, fitted on 101,380 HLTV history rows | live reconstruction (cm-2 / perf-2 N0), real lines | **All rejected**: -0.83 to +0.01 points, none with the interval above 0; 6 significantly worse. Supersedes the round 1-3 results that were MAE-only on perf-1 / perf-2 G8.3 |
| B01-B12: market closeness and favourite / underdog (saved win odds), market quality, scope, app, sample size, rust, last-10 tape, role-unconfirmed, data-state flags - board-fitted cohort multipliers | live, real lines | **All rejected**: -0.95 to -0.26 points; 5 significantly worse (B09 hot-form: not a real test, flag on week 0 only) |
| G1-G5, G7, G8: letter thresholds per stat x side, Over/Under asymmetry, chance band, distance-from-line, sample-size stake rule, per-scope threshold, learned selection (staked picks, sides unchanged) | live, real lines | **Rejected** (interval across 0, or one staked side lower) |
| **G6: lift the AWPer-on-HS 0U gate** (sr-gates-1's forward re-test, no parameter) | starts after 2026-10-28 13:40 UTC | **PASSED -> LIVE as awp-hs-1 (owner "yes" 2026-11-11, tag `042a5339de`)**: staked 1,193 -> 1,314, hit 53.0 -> 54.1, +1.13 [+0.20, +2.04]; staked Overs 51.2 -> 51.6, Unders 54.8 -> 56.7; added picks 65.3% [55.9, 74.8] on 121 rows / 79 matches. Rollback `CS2_AWP_HS_LIFT=0` |

Caveats recorded with the promotion: about 43 tests, one pass (roughly the chance rate); the added picks lean Under
(237 Under / 144 Over over Oct 11 - Nov 8); four weeks of one model family; the holdout (Nov 9-11, 35 picks, 60.0%) is
too small to confirm. Table row "Roster continuity / stand-ins ... GATES ONLY" above is unchanged; the "AWP share (G1)"
row is unchanged (the AWP rule still marks the player; it no longer zeroes the HS stake). Untested lead for a later
frozen round: a stake rule on the favourite / underdog split (favourites 56.9% [54.5, 59.2] vs underdogs 51.7%).

## Frozen test definition - round 9: lobby kill budget lkb-1 (frozen 2026-11-13 BEFORE running)

Full definition: research/lkb1_2026_11_13/FROZEN_DEFINITIONS.md (committed before scoring). TEST ONLY - nothing goes live.
- **Origin:** MOUZ-Vitality review 2026-11-13 - perf-cm2's negative own / opponent Elo terms pull all 10 players of an
  elite-vs-elite lobby down together (implied 6.21 kills per round vs ~6.7). The review's quartile finding (lowest
  implied-KPR quarter: kills Maps 1-2 Unders 46.7%) was post hoc and overlaps this test window, so TEST is not fresh.
- **Candidate:** rescale all 10 players' saved Maps 1-2 (and, separately, Map 1) kills projections by one factor so the
  match's implied kills (team total = 5 x mean of 4+ known players per team) moves a fraction alpha toward
  N = trailing 90-day HLTV kills-per-round-per-map x mean rounds x maps (data before the match day only). HS inherits
  (lam_hs x f). A side flips only where the projection crosses the line.
- **alpha:** grid 0..1 step 0.1, minimum squared error of Maps 1-2 kills on FIT units (matches Oct 21-31); TEST Nov 1-13.
- **Data:** saved first-grade pick rows (paged picks export, cutoff 27921) at real offered standard lines; not re-derived.
- **Placebos (both must fail C1):** random-sign shift (seed 20261113); match-shuffled budgets (seed 20261114).
- **Pass (all):** C1 hit-rate gain CI lower bound > 0 (match-grouped bootstrap); C2 Over-pick and Under-pick hit each not
  lower; C3 graded count not lower; C4 Over share not > 1 point more lopsided; C5 Brier not worse; C6 gain >= 0 on upset,
  OT and remaining matches separately; C7 >= 0 in both test halves; C8 both placebos fail C1.

## Round-9 results (run once 2026-11-13 after the definition above was pushed in 2e22af6; script research/lkb1_2026_11_13/score.py)

**lkb-1: FAIL (C1, C2, C4, C6, C7 fail).** Full log: cs2_scout_ops/logs/lkb1_walkforward_2026-11-13.md.
alpha = 0.4 (FIT 1,624 player-match units; SSE 101,113 -> 101,022). The normal was 6.67 kills per round x 21.5 rounds;
the average TEST lobby implied 6.63, so f ran only 0.984-1.026 (MOUZ-Vitality 1.0205). 211 of 4,054 TEST rows flipped
(195 to Over), all of them 0U.

| Criterion (TEST Nov 1-13: 4,054 rows / 139 matches, match-grouped 95% CI) | Live | lkb-1 | Result |
|---|---|---|---|
| C1 hit rate | 52.57% | 52.59%, +0.02 pts (-0.94 to +0.98) | FAIL |
| C2 Over-pick / Under-pick hit | 53.09% / 52.06% | 52.86% / 52.28% (Over -0.23, -1.12 to +0.63) | FAIL (Overs lower) |
| C3 graded count | 4,054 | 4,054 | pass |
| C4 Over share | 49.06% | 53.48% (+2.5 pts more lopsided) | FAIL |
| C5 Brier on p_more | 0.2521 | 0.2513 (-0.0008, -0.0020 to +0.0003) | pass |
| C6 upset (37 m) / OT (25 m) / rest (58 m) | 52.78 / 49.04 / 52.04% | -0.37 / +3.21 / -0.74 pts | FAIL |
| C7 halves T1 / T2 | | +0.09 / -0.05 pts | FAIL |
| C8 placebos fail C1 | | random sign -0.59 (-1.39 to +0.21); shuffled budgets +0.25 (-0.67 to +1.29) | pass |

Excluding the five motivating MOUZ / Vitality matches: -0.03 pts (-0.97 to +0.96). FIT window in-sample: -0.49 pts.
The shuffled-budget placebo beat the candidate, and the only positive cohort was overtime matches: the effect is a generic
Over nudge, not lobby information. Logged as a measured negative result; no shadow, no flag, no grading change.

## Frozen test definition - round 10: miss decomposition A rounds / B Elo / C player rates (frozen 2026-11-13 BEFORE running)

Full definition: research/miss_decomp_2026_11_13/FROZEN_DEFINITIONS.md, scripts build.py / score.py, committed before
any outcome was computed. TEST ONLY - nothing goes live. Owner direction: lkb-1 was rightly rejected; test the three parts
of the MOUZ-Vitality miss SEPARATELY on chronological data, then validate on new matches.
- **Data:** saved first-grade pick rows (ids 7001-27921) at real offered standard lines, Maps 1-2 kills + HS (cm-2 /
  hs-cm2); cm-2 tables rebuilt locally with the live code from the HLTV extract + export (rebuilt vs saved projection:
  corr 0.9955, median |diff| 0.003 kills - an input check, no outcome).
- **Splits:** fit through 2026-10-31; TEST Nov 1-10 (halves Nov 1-5 / 6-10), the five MOUZ / Vitality BLAST matches
  (Nov 11-13) reported apart; FORWARD = matches starting after this freeze commit (untouched; review at 150 matches).
- **Candidates (each a multiplier on the saved kills and HS projection; HS inherits):**
  - **10-A1** rounds by Elo closeness: f = (a + b |q_elo - 0.5|) / mean, OLS on HLTV BO3 Maps 1-2 rounds Jan-Oct 2026.
  - **10-A2** rounds by market closeness: same with the saved market price, fit on board matches Oct 19-31.
  - **10-B1** Elo re-centred level + gap: Poisson on actual kills, offset projection x actual rounds / 44 (rounds taken
    out), terms (Elo_own + Elo_opp)/800 centred and (Elo_own - Elo_opp)/400; HLTV lobbies Oct 2026, recomputed cm-2.
  - **10-C1** recent form (last 10 maps vs cm-2's shrunk rate, 100-round shrink), **10-C2** role (AWPer by roles.py's
    AWP-share rule on the HLTV snapshot / entry by opening attempts per round, 80th pct / support), **10-C3** map-specific
    (saved map chances x his shrunk per-map kill-rate ratio, 365 d) - each fitted alone, same Poisson set-up.
- **Placebo per candidate (must fail C1):** its input permuted across TEST matches / units (seeds 20261115-20).
- **Pass (all, per candidate):** C1 hit CI lower bound > 0; C2 Over and Under hit each not lower; C3 graded count; C4
  Over share not > 1 pt more lopsided; C5 Brier not worse; C6 >= 0 on upset, OT and remaining matches; C7 >= 0 both
  halves; C8 placebo fails C1; C9 kills-per-round MAE not worse (A: rounds MAE and kills MAE not worse).

## Round-10 results (run once 2026-11-13 after the definition above was pushed in 841b984; script research/miss_decomp_2026_11_13/score.py)

**All six candidates FAIL; nothing gets a shadow.** Full log: cs2_scout_ops/logs/cs2_miss_decomposition_2026-11-13.md.
Two code fixes after the freeze, no definition change (null `pred_maps`; NaN role for units without HLTV inputs).
TEST Nov 1-10: 2,598 Maps 1-2 rows / 101 matches, live 52.23% (Overs 50.52%, Unders 53.82%, Over share 48.2%).

| Cand. | Hit diff, pts (95% CI) | Over / Under hit | Brier diff | Upset / OT / rest | T1 / T2 | Placebo | Fails |
|---|---|---|---|---|---|---|---|
| 10-A1 rounds x Elo closeness | +0.50 (-0.59..+1.59) | 50.98 / 54.56 | -0.0014 | -0.10 / +4.03 / +0.17 | +1.17 / -0.35 | +0.54 (fails C1, but as good as A1) | C1 C6 C7 |
| 10-A2 rounds x market closeness | +0.54 (-1.08..+2.12) | 51.01 / 54.65 | -0.0019 | 0.00 / +5.68 / -0.33 | +0.21 / +0.96 | +0.19 | C1 C6 |
| 10-B1 Elo re-centred + gap | -0.35 (-1.46..+0.74) | 50.16 / 53.50 | +0.0003 | -0.21 / -0.92 / -0.08 | -1.03 / +0.53 | -1.62 | C1 C2 C5 C6 C7 |
| 10-C1 recent form | -0.15 (-0.80..+0.49) | 50.35 / 53.78 | -0.0003 | -0.41 / +1.10 / -0.41 | -0.41 / +0.18 | -0.12 | C1 C2 C6 C7 |
| 10-C2 role | -0.27 (-0.81..+0.27) | 50.23 / 53.65 | 0.0000 | -0.41 / +0.55 / -0.58 | -0.41 / -0.09 | +0.04 | C1 C2 C5 C6 C7 |
| 10-C3 map-specific | -0.69 (-1.27..-0.12) | 49.80 / 53.19 | +0.0006 | -0.41 / +0.92 / -1.33 | -1.17 / -0.09 | -0.19 | C1 C2 C5 C6 C7 C9 |

Measurements:
- **A rounds:** not underestimated overall. Actual Maps 1-2 rounds were 43.4 (FIT) / 43.2 (TEST) vs 44 used.
  - Close matches (market 40-60%): 43.8 / 44.7 rounds, OT 26% / 30%.
  - Lopsided: 41-42 rounds.
  - Slope about -9 rounds per unit |q - 0.5|. Rounds MAE 5.63 -> 5.61-5.62, CIs across 0.
- **B Elo:** high-Elo lobbies are not over-shrunk.
  - Actual / projected KPR in the lowest vs highest combined-Elo quintile: 1.041 vs 1.018 in HLTV Oct; 1.011 vs 1.022 in TEST.
  - Fitted b_S = -0.027, the opposite of the hypothesis.
  - Favourites' players beat the projection (gap term +0.029 per 400 Elo).
- **C:** factor fits are tiny (form beta 0.096, role +/-1%, maps beta 0.39). They flipped only 0U rows.
- **Motivating five MOUZ / Vitality matches (apart):** every candidate is worse or no better there (A1 / A2 -6.9 pts).
- **Forward (untouched):** matches after 2026-11-13 20:55:13 UTC. Frozen params sha `ea241b17a6b42cdc`; 306 recorded
  factors sha `c02a13d03734d6bb`. Review at 150 matches (about Nov 28-29), as confirmation of the negatives only.
