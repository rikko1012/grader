# Map-veto upgrade veto-1 - results

Frozen candidate **veto-1-a140ca69ec** (params sha `a140ca69ec`, FROZEN.json, committed 7ce1ed0 before FINAL was
touched). Registration REGISTER.md (e36760c, before any fitting). Full tables: RESULTS_DEV.md, RESULTS_FINAL.md
(raw: results_dev.json, results_final.json). FINAL (2026-08-23 .. 2026-10-22) was scored exactly once.

## Decision (predeclared rule): PASSES
FINAL, BO3 matches where the frozen simulator returns a forecast (2,000 distinct matches), mean log-loss difference
frozen - candidate, match-grouped bootstrap 95% CI (> 0 = candidate better):

| target | frozen M2 | candidate M3 | difference [95% CI] |
|---|---|---|---|
| Map 1 | 1.7642 | 1.6383 | **0.1259 [0.1087, 0.1436]** |
| Map 2 | 1.6856 | 1.6396 | **0.0460 [0.0293, 0.0626]** |
| ordered (Map 1, Map 2) | 3.3254 | 2.9217 | **0.4038 [0.3726, 0.4342]** |

All three intervals are entirely above zero, so veto-1 qualifies to replace hltv.predict_maps **for the display only**.
Grading is unchanged; any grading use needs its own registration. Map effects still enter nothing but context.

## Benchmark summary (log-loss, mean [95% CI]; distinct matches; lower is better)
Uniform-legal log-loss for one map slot = ln 7 = 1.9459.

| period | format / target | n | M0 uniform | M1 simple recent | M2 frozen (own coverage n) | M3 candidate |
|---|---|---|---|---|---|---|
| DEV D1-D4 | BO3 Map 1 | 15,057 | 1.9459 | 1.7851 [1.7772, 1.7930] | 1.7740 [1.7663, 1.7823] (12,687) | 1.6725 [1.6625, 1.6827] |
| DEV D1-D4 | BO3 Map 2 | 15,057 | 1.9459 | 1.7731 [1.7673, 1.7791] | 1.6973 [1.6913, 1.7032] (12,687) | 1.6500 [1.6414, 1.6585] |
| DEV D1-D4 | BO3 decider | 15,057 | 1.9459 | 1.7752 | - | 1.6343 [1.6258, 1.6432] |
| DEV D1-D4 | BO3 map set (35) | 15,057 | 3.5553 | 2.8161 | - | 2.4710 [2.4562, 2.4855] |
| DEV D1-D4 | BO3 inclusion (per map) | 15,057 | 0.6829 | 0.5625 | - | 0.5080 |
| DEV D1-D4 | BO1 map | 1,764 | 1.9459 | 1.7033 | - | 1.6249 [1.5946, 1.6559] |
| DEV D1-D4 | BO5 map set (21) | 171 | 3.0445 | 2.0604 | - | 1.0781 [0.8864, 1.2914] |
| FINAL | BO3 Map 1 | 2,320 | 1.9459 | 1.7803 [1.7597, 1.8013] | 1.7642 [1.7422, 1.7842] (2,000) | 1.6728 [1.6469, 1.6989] |
| FINAL | BO3 Map 2 | 2,320 | 1.9459 | 1.7653 [1.7495, 1.7815] | 1.6856 [1.6704, 1.7004] (2,000) | 1.6505 [1.6286, 1.6736] |
| FINAL | BO3 decider | 2,320 | 1.9459 | 1.7349 [1.7183, 1.7511] | - | 1.6095 [1.5888, 1.6316] |
| FINAL | BO3 (Map 1, Map 2) pair | 2,320 | 3.7377 | 3.2834 | 3.3254 (2,000) | 2.9818 [2.9416, 3.0232] |
| FINAL | BO3 map set | 2,320 | 3.5553 | 2.7525 [2.7230, 2.7861] | - | 2.4337 [2.3971, 2.4714] |
| FINAL | BO3 full played order | 2,320 | 5.3471 | 4.4950 | - | 4.0762 |
| FINAL | BO3 inclusion | 2,320 | 0.6829 | 0.5519 | - | 0.5022 [0.4960, 0.5086] |
| FINAL | BO1 map | 273 | 1.9459 | 1.7280 [1.6385, 1.8082] | - | 1.6779 [1.6018, 1.7550] |
| FINAL | BO5 map set | 23 | 3.0445 | 2.0093 | - | 1.1879 [0.6141, 1.9572] |

Top-choice accuracy FINAL BO3: Map 1 M3 29.7% (M2 27.6% on its coverage, M1 25.2%, uniform 16.4%); Map 2 30.9% (M2 28.3%);
decider 34.1%; exact map set 27.3% (M1 22.5%); exact (Map 1, Map 2) 15.2% (M2 10.9%). Brier FINAL BO3 Map 1: M3 0.786,
M2 0.807, M1 0.816, M0 0.857.

Coverage: M3/M1/M0 cover every complete veto (100%). M2 covers BO3 only and only when both teams have >= 3 stored
vetoes: 12,687 / 15,057 dev (84.3%), 2,000 / 2,320 FINAL (86.2%); with a uniform fallback on the rest M2's FINAL Map 1
log-loss is 1.7893 (candidate better by 0.1164 [0.0999, 0.1330]).

Secondary (not part of the rule), FINAL:
- vs M1 simple recent: BO3 Map 1 +0.1074 [0.0930, 0.1219], Map 2 +0.1147 [0.1010, 0.1281], decider +0.1253, set
  +0.3188 [0.2988, 0.3385], inclusion +0.0498. BO1 map +0.0501 [-0.0122, 0.1093] (273 matches: not separable from M1).
  BO5 23 matches: intervals wide.
- Live updates (conditioned on the confirmed actions; M2 cannot condition): after the first 2 bans, BO3 Map 1 1.1589
  vs M1 1.3102 (diff 0.1512 [0.1315, 0.1702]), Map 2 1.0948 vs 1.2667, decider 1.4914 vs 1.5501. After 4 actions
  (picks known) the decider: 0.9291 vs M1 0.9331 (diff 0.0040 [-0.0092, 0.0178]: no separation once only 3 maps remain).
- Calibration (ECE, 10 bins, FINAL BO3): Map 1 0.0095, Map 2 0.0085 (M2 0.0311 / 0.0230, M1 0.0195 / 0.0163). Map
  inclusion ECE 0.0433: UNDER-confident at the extremes (predicted 6% -> observed 1.0%; 84% -> 88.9%) - the eps=0.05
  uniform floor; a recalibration is a prospective follow-up (FINAL is spent, it cannot be tuned on).
- Listing order (F7) adds little: candidate without it differs from the frozen version by <= 0.003 on every BO3 target
  (eg Map 1 +0.0011 [-0.0030, 0.0054]). It only applies when HLTV's team1/team2 order is passed (`listed_first`).

## What was tuned / admitted (D1-D3 tuning, D4 admission; tuning_log.json)
Per-match observed-veto negative log-likelihood (steps): uniform 8.525; tuned base 5.722 (D1-D3); final 5.363 (D1-D3),
5.300 on D4 (base 5.670). Base settings: half-life 60 d, k_team 1 (grid edge), k_glob 20, global half-life 60 d,
20 most recent vetoes, beta ban1 1.2 / banN 1.0 / pick 1.0, eps 0.05, template alpha 0.25.

| factor | tuned | D4 gain per match [95% CI] (2,844 matches) | admitted |
|---|---|---|---|
| F1 roster continuity (overlap^rho) | rho 2 | 0.0483 [0.0382, 0.0588] | yes |
| F2 team identity (>= 3 shared players) | on | 0.0213 [0.0099, 0.0332] | yes |
| F3 opponent pick/removal rates | gamma ban1 0.25, pick 0.25 | 0.0586 [0.0456, 0.0718] | yes |
| F4 opponent-adjusted map strength | delta 4 (grid edge) | 0.2689 [0.2491, 0.2895] | yes |
| F5 BO1 removals separate | - | no D1-D3 gain | no |
| F6 first actor from Elo | - | no D1-D3 gain | no |
| F7 first actor from HLTV listing | -0.5 (listed team1 acts first less often: 40.4%) | 0.0113 [0.0050, 0.0176] (first-actor LL) | yes |
Joint D4 gain over base: 0.3702 [0.3429, 0.3998].

Leakage controls: every context is taken from the day-by-day table BEFORE that day's matches are added (same-day
matches never see each other; Elo updates after the day); lineups = each team's previous match; the pool is the
event's (public) pool; no model gets any veto action of the match pre-veto; M2 gets the same pool and date.

## Limits / what is left
- k_team = 1 and delta = 4 sit on the edges of the registered grids; wider grids need a new registration and must be
  judged prospectively (FINAL is spent).
- Inclusion under-confidence at the extremes (above); a calibration layer would be a new version, judged forward.
- BO5 (23 FINAL matches) and BO1 (273) are too small for firm conclusions; BO3 is where the rule was decided.
- Data = matches the scraper stored (top-200 gap, HLTV_COVERAGE.md); tiers come from our Elo (no historical rankings).
- "Map inclusion" = the veto's selected maps (decider counted even if a 2-0 never plays it).
- Serving (another phase): build the table off-request (research/map_veto/build_table.py; 400 days ~21 s,
  table ~23 MB resident, file ~1 MB), load it, call veto_model.predict; precompute board matches at refresh. Load test
  (load_test.py, 700 threads): 59 board-like inputs cold 1.2 s wall, warm cache max 0.05 ms; 700 all-distinct inputs
  13 s wall (single process, CPU-bound) - so never compute per member request; full 1,024-entry cache ~15 MB.
  Builder peak ~130 MB (tracemalloc) - run it as a job, not inside the web worker.
- Prospective: log veto-1 forecasts for upcoming matches from 2026-10-23 and score them after the vetoes.
