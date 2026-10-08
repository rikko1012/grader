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
| BO3.gg map rows (fallback for players HLTV lacks) | BO3.gg (yes) | players outside HLTV | rate | **USED** (perf-2-bo3, provisional) | validated vs HLTV 2026-10-09: 866 maps matched independently - map order 100%, date 100%, rounds 99.9%, HS 98.0%, kills 96.1% identical (3.5% off by one, BO3.gg lower), 4 outliers (one broken partial map). Same feature definitions (kills, rounds = both scores, HS, date, LAN, 0-HS-on-12+ excluded); maps with substitutes cannot be detected on BO3.gg |
| Team strength (Elo from results) | HLTV results (yes) | all | win scenario | **TESTED, REJECTED** (twice) | b = 0 on TRAIN; re-tuned on VALID 2026-10-09: b 0.05, TEST MAE -0.002 (95% -0.005 to +0.001); underdog bias +0.76 -> +0.53 only |
| Team-level prior for thin history | HLTV (yes) | all | shrink target | **TESTED, REJECTED** | MAE -0.002, within noise (the newcomer prior replaced it) |
| Stronger shrink for thin history (k0 600 / 1000) | HLTV (yes) | all | rate | **TESTED, REJECTED** | VALID MAE worse (6.355 -> 6.362 / 6.370) |
| Expected rounds: matchup features (Elo gap, recent map lengths, LAN) | HLTV (yes) | all | rounds | **TESTED, REJECTED** | rounds MAE 5.07 -> 5.06; kills unchanged |
| Map 1 specifics (own rounds 21.77, map-1 history, BO1 history) | HLTV (yes) | all | Map 1 | **TESTED, TIED** | Map 1 candidate not better than live Map 1 (MAE 3.19 vs 3.14) |
| Maps / veto scenarios (G4) | HLTV raw_pages veto (yes for history) | all | rounds + rate | **TESTED, REJECTED (upper bound)** | ORACLE test with the maps actually played (map rounds + player per-map rate): TEST -0.001 (95% -0.002 to +0.000). A veto model can only know less than the oracle, so it cannot help kills |
| Eco-adjusted / rating family (G2): damage per kill, eco-adjusted kill share, deaths/round, KAST, rating beyond kills, round swing | HLTV player_stats (yes) | all | rate / HS | **TESTED, REJECTED for kills**; HS: small signal, re-test on perf-2 | kills TEST -0.002 (95% -0.008 to +0.003). HS TEST -0.014 (-0.022 to -0.006) on top of perf-1 - part of it is the HS shrink now fixed in perf-2; re-test this group on top of perf-2 before any use |
| Schedule load (G5): days since last map, maps in last 7 / 30 days | HLTV dates (yes) | all | rate | **TESTED, REJECTED** | TEST +0.001 (95% -0.003 to +0.005). Legacy rest bonus stays legacy-only |
| AWP share (G1) | HLTV player_awp: CURRENT snapshot only | most board players | rate / HS / spread | **UNAVAILABLE historically**; proxy tested | proxy (shrunk HS/kill < 0.38): kills TEST -0.002 (95% -0.004 to +0.001); HS effect = the HS shrink (now perf-2); spread for low-HS players 1.486 TRAIN vs 1.347 TEST (flips) - rejected. **Collect:** dated daily snapshots of AWP kill share per player (scraper table with a date column) so the share known before each match can be tested |
| Weapon usage beyond AWP share | HLTV player_weapon_kills (90 d snapshot) | up to 300 players/scan | HS propensity | **UNAVAILABLE historically** | same collection need: dated snapshots |
| CT / T side splits | HLTV player_stats sideid 1/2 (in the DB, not in the export) | all | rate x starting side | **NOT PROMOTED (bound too small)** | Every map gives each side a 12-round half; knowing the starting side moves expected kills by (12 - 9.74)/2 x (CT - T kills/round) = 1.13 x gap per map: a 0.1 gap = 0.23 kills on Maps 1-2 vs MAE 6.1. **Collect:** add sideid 1/2 rows to /admin/hltv_export and the veto's side choice, then test as a group |
| Market: Underdog line movement (timestamped) | Underdog log since 2026-10-04 (yes) | 2,106 player lines, 589 moved | anchor / info | **TESTED (dev), NO SIGNAL** | 170 settled verified Maps 1-2 lines that moved (36 matches): result in the direction of the move 78/170 = 46% (95% about 38-53%). Keep collecting; retest at 150+ matches. Note: the logger re-logs unchanged lines after a restart (in-memory last-seen) - analysis dedupes by player/prop/start |
| Map HS modifiers (Mirage +6% etc.) | owner's fixed numbers | - | HS | **TESTED, REJECTED (earlier)** | league map HS factors about neutral; legacy pipeline only |
| Online +6% on top of online-only stats | fitted Oct 2-6 | - | rate | **REMOVED (G5)** | double counting |
| Rest bonus +4% / rust -8% / star cut / hot form / regional / H2H blends | legacy pipeline | - | rate | **LEGACY ONLY** (not in perf) | - |
| Teammate competition for kills (team kill share) | HLTV (yes) | all | rate | **NOT YET TESTED** in perf | next cycle |
| Roster continuity / stand-ins | BO3.gg + HLTV lineups (partly) | partial | confidence / rate | **GATES ONLY** (units) | - |
| Event tier / stage / format (BO1/BO3) | HLTV events, scrape_queue format (yes) | most | rate / rounds | **PARTLY** (BO1 history blend tied) | - |
| Market: series / map win prices | Polymarket (live only) | when listed | scenario | **NOT IN perf**; not stored historically | **Collect:** timestamped price snapshots |
| Cross-book lines (PrizePicks vs Underdog) | not collected | - | anchor | **UNAVAILABLE** | **Collect:** a second book's lines with timestamps |
| Opening duels, trades, multikills, clutches | not in the HLTV DB | - | - | **UNAVAILABLE** | `ek`/`ed` are eco-adjusted, NOT openings |
| Economy / buy rounds, server / ping | not collected | - | - | **UNAVAILABLE** | - |

Scripts (scratchpad, dev): factor_groups.py (G1/G2/G4/G5), khs_check.py / khs_eval.py / kappa2.py (HS shrink), newcomer.py,
weak_seg.py, perf2_eval.py (through perf.py), bo3_validate.py, moves_check.py.

Next: re-test G2 on HS on top of perf-2; teammate kill share; CT/T and AWP once dated data is collected; line movement at
150+ matches; confirm perf-2 on matches from 2026-10-09 on against the logged perf-1 (G7) comparison.
