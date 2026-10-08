# Factor audit (living document, started 2026-10-08)

Status of every factor that could affect a projection. Rules:
- a factor moves the live forecast only after a predefined group test on chronological data;
- correlated factors are grouped and regularized, never stacked as separate multipliers;
- missing data stays unknown.

"Dev" = tested on Sep 2025 - Oct 8 2026 data (development). Confirmation uses matches after a candidate's freeze.

| Factor / group | Source (pre-match?) | Coverage | Role | Status | Evidence |
|---|---|---|---|---|---|
| Player kills per round, time-weighted + shrunk | HLTV player_stats (yes) | 1,431 players, 113,859 player-maps | rate | **USED** (perf-1) | Held-out kills MAE 6.12; 80% intervals 79-83% |
| Rounds as exposure | HLTV matches scores (yes, as history) | all maps | rate denominator | **USED** | kills per round flat across match length (0.99-1.02) |
| LAN / online environment (player's own split, blended by sample) | HLTV match_overview.lan, events (yes) | all | rate | **USED** | part of the perf-1 tuning (env blend 800 rounds) |
| Opponent kills conceded per enemy player | HLTV (yes) | teams in HLTV | rate | **USED** (shrunk) | part of the perf-1 tuning (300 rounds) |
| Headshots per kill, shrunk; HS out of kills (beta-binomial) | HLTV map_headshots (yes) | 3,032 maps/45 d fully read | HS propensity + variance | **USED** (perf2_hs, provisional) | board HS log-loss 0.6666 -> 0.6592 (dev) |
| Team strength (Elo from results) | HLTV results (yes) | all | win scenario | **TESTED, REJECTED** | b = 0 on TRAIN; underdog bias seen only in the later period |
| Team-level prior for thin history | HLTV (yes) | all | shrink target | **TESTED, REJECTED** | MAE -0.002, within noise |
| Expected rounds: matchup features (Elo gap, both teams' recent map lengths, LAN) | HLTV (yes) | all | rounds | **TESTED, REJECTED** | rounds MAE 5.07 -> 5.06; kills unchanged |
| Map 1 specifics (own rounds 21.77, map-1 history, BO1 history) | HLTV (yes) | all | Map 1 | **TESTED, TIED** | Map 1 candidate not better than live Map 1 (MAE 3.19 vs 3.14) |
| Player per-map rates (map identity) | HLTV (map known after veto) | all | rate | **TESTED, REJECTED (earlier)** | per-map rates did not beat all-maps rates even with actual maps |
| Map HS modifiers (Mirage +6% etc.) | owner's fixed numbers | - | HS | **TESTED, REJECTED (earlier)** | league map HS factors about neutral; still in the legacy pipeline only |
| Online +6% on top of online-only stats | fitted Oct 2-6 | - | rate | **REMOVED (G5)** | double counting; "+6% always" log-loss worse on board (dev) |
| Rest bonus +4% / rust -8% / star cut / hot form / regional / H2H blends | legacy pipeline | - | rate | **LEGACY ONLY** (not in perf-1) | no-rest-bonus comparison scored better on a small board sample (dev) - pending |
| Eco-adjusted kills / deaths (ek, ed), eco ADR / KAST | HLTV player_stats (yes) | all | rate / variance | **PROMISING, NOT TESTED** | - |
| ADR, KAST, rating, round swing, deaths | HLTV player_stats (yes) | all | rate / variance | **NOT YET TESTED** as a group in perf-1 | earlier 23-stat regression on board lines: no gain |
| CT / T side splits (sideid 1/2 rows) | HLTV player_stats by side (yes, as history) | all | rate x starting side | **NOT YET TESTED** | starting side known only at match time |
| Map vetoes / map pool (raw match pages) | HLTV raw_pages (veto text, yes for history) | stored pages | rounds / scenario | **NOT YET TESTED** in perf | veto model calibrated (log-loss 1.764 vs 1.869), weight 0 |
| Roles: AWPer (HLTV weapon share), IGL (Liquipedia) | HLTV player_awp, player_roles (yes) | most board players | rate / HS propensity | **DISPLAY + slips only** | to test as a group (AWP share vs HS/kill) |
| Weapon usage beyond AWP share | HLTV player_weapon_kills (90 d) | up to 300 players/scan | HS propensity | **NOT YET TESTED** | - |
| Teammate competition for kills (team kill share) | HLTV (yes) | all | rate | **NOT YET TESTED** in perf-1 | - |
| Roster continuity / stand-ins | BO3.gg + HLTV lineups (partly) | partial | confidence / rate | **GATES ONLY** (units) | - |
| Schedule: rest days, matches in recent days | HLTV dates (yes) | all | rate | **NOT YET TESTED** in perf-1 | rest bonus was legacy |
| Event tier / stage / format (BO1/BO3) | HLTV events, scrape_queue format (yes) | most | rate / rounds | **PARTLY** (BO1 history blend tied) | - |
| Market: series / map win prices | Polymarket (yes, live only) | when listed | scenario | **NOT IN perf-1**; not stored historically | can't backtest without history |
| Market: Underdog line, line movement, alternate payouts | Underdog log since Oct 7 (yes) | board lines | anchor / info | **NOT YET TESTED** | a candidate feature, not proof of value |
| Cross-book lines (PrizePicks vs Underdog) | not collected | - | anchor | **UNAVAILABLE** (not collected) | - |
| Opening duels (attempts / success), trades, multikills, clutches | not in the HLTV DB | - | - | **UNAVAILABLE** | `ek`/`ed` are eco-adjusted, NOT openings |
| Economy / buy rounds, server / ping | not collected | - | - | **UNAVAILABLE** | - |

Next group tests (in this order):
1. AWP share and weapon mix -> HS propensity.
2. Eco-adjusted and rating-family stats -> rate.
3. CT/T splits.
4. Veto-based map scenarios -> rounds.
5. Schedule load.
6. Underdog line movement.

Each test uses predefined groups, ablation against perf-1, and match-grouped uncertainty.
