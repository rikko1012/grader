# CS2 accuracy investigation (2-year window) - shared experiment register

Owner brief: 2026-10-22 (full CS2 accuracy investigation). Branch: `research/cs2-accuracy-2y` (worktree
`.claude/worktrees/cs2-acc-2y`). **Every agent working on CS2 accuracy logs here before running anything.** One row per experiment;
nothing is compared before its row (hypothesis, data, split, primary metric, promotion rule) is committed.

Rules: live grading unchanged during the investigation; model changes stay in shadow until they pass; confirmed implementation
bugs are fixed separately with regression tests and their own version; original forecasts and results are never rewritten.
Other agents: the Codex session works on LoL (PR #11, codex/lol-ui-accuracy-20261022) - no CS2 overlap. Do not edit files
another branch is editing; do not deploy competing CS2 model changes.

## Window
Research cutoff: 2026-10-22 00:00 UTC. Two-year window: 2024-10-22 .. 2026-10-21 (match start dates). Matches after the cutoff are
the untouched confirmation set (forward).

## Data periods (set in phase 1 after the inventory; previously inspected periods are DEVELOPMENT data)
Set by phase 1 (COVERAGE.md, 2026-10-23):
- DEVELOPMENT: every match dated <= 2026-10-21 (HLTV history 2024-10-22..2026-10-21; real lines 2026-10-02..2026-10-21; forecasts
  2026-10-07..2026-10-21). All real-line days were inspected by earlier studies - none is a holdout.
- FORWARD CONFIRMATION: matches starting >= 2026-10-22 00:00 UTC. Local exports contain some 2026-10-22+ rows; exclude them from fits.
- Round data: use `rounds_archive.sqlite` (DB map_rounds + recovered overtime, validated); production map_rounds lacks every OT map.

## Experiment register
| id | date registered | hypothesis | data / split | primary metric | promotion rule | status | result |
|---|---|---|---|---|---|---|---|
| UT-1 | 2026-10-22 | Under concentration trace: the share of 55%+ P1 picks that are Under, the step-by-step decomposition of projection - line (perf-2 rate / prior shrink / environment blend / opponent factor / rounds; cm-2 features; grader), and whether the direction and confidence are justified by settled outcomes; implementation-bug hunt (sign, double application, rounds, stale inputs) | P1 units (learning p1_units export, latest standard board forecast >= 30 min before start, pushes excluded+counted) joined to each forecast's saved grader inputs (forecasts_page inputs=1); HLTV DB read-only for repros. Diagnostic only - DEVELOPMENT data, no model is fitted or compared for promotion | share Under; mean(proj-line), mean(actual-line), mean(actual-proj) with match-grouped bootstrap 95% CI (B=2000); printed vs won by band/side | none (diagnostic; any bug goes to a separate fix with its own version + regression test) | done 2026-10-23 | **[Superseded 2026-10-23: C3, C4]** UNDER_TRACE.md: no systematic below-line projection (kills M1-2 proj-line +0.09, actual-line +0.50); graded Under share kills 48.8%, HS 59.1% (HS skew, calibrated); board spikes = slates with lines above raw history + prior/opponent steps (both pass step-out); overconfidence symmetric; OT = half of graded Under losses, not priced by any pre-match proxy; no implementation bug |
| AU-1 | 2026-10-22 | Data/implementation audit of the real offered P1 lines: (1) identity - line's player/team/opponent/match resolved to the right HLTV player and match (namesakes, academies, alias variants, roster changes, stand-ins, the LP vs BARCA hltv:2399805 case); (2) settlement - our settled actual vs HLTV map-level numbers for every settled line (map order, OT included, forfeits/remakes, incomplete scoreboards, Maps 1-3 / BO3 2-0, push/void consistency); (3) duplicates across books / re-keyed units / repeated forecasts and P1 dedup; (4) line/quote freshness at forecast time, lines moved after the forecast, stale-feed forecasts; (5) leakage - every saved input available before prediction time | P1 units export (learning p1_units, at 2026-10-23T04:29:55Z, 2,922 units) + each unit's saved forecast row with inputs (forecasts_page inputs=1, UT-1 pull, cutoff id 79170); 2y HLTV export (Codex, read-only) for map-level settlement; HLTV DB read-only bounded SELECTs for identity checks. Diagnostic only - DEVELOPMENT data, nothing fitted | counts and affected matches per problem class; effect on measured favoured-side hit rate and log loss (all units vs units with the problem removed / corrected), match-grouped bootstrap 95% CI (B=2000) | none (diagnostic; bugs -> failing regression test in audit/bug_tests + fix proposal, fixed separately with own version) | done | AUDIT.md: settlement 2,904/2,904 = HLTV, leakage 0; defects 106 units / 25 m (hit 28.3%): removing them +0.86 pts hit [+0.31, +1.55], log loss -0.0040 [-0.0073, -0.0014] (clean 52.0% vs printed 55.8%). Bug D1 (8 units double-counted under two match keys; failing test audit/bug_tests/test_au1_duplicate_units.py); design gap I4 (N0 lookup routes HLTV-known players to perf-2-bo3, skipping cm-2: 67 units / 14 m, hit 25.4%) |
| ED-1 | 2026-10-22 | Error decomposition (diagnostic): where does (actual - projection) come from, for KILLS and HS separately and Map 1 / Maps 1-2 / Maps 1-3 separately? Components: (1) rounds in scope (model rounds assumption vs realized: regulation length, overtime); (2) team strength / scoreline (team kills per round vs realized round-win share); (3) player kills per round = team kills per round x share of team kills; (4) HS given kills; (5) opponent / LAN-online / roster change / role (AWP proxy) residual structure; (6) predictive distribution shape (PIT, 50/80/95% coverage, spread vs |error|, calibration of P(over) at real lines); (7) real-line freshness / scope / identity / settlement (light; AU-1 owns the full audit). Method: chained + one-at-a-time + all-order (Shapley) oracle substitutions on the live model's own projections (realized rounds -> realized team kills (model share x realized team total) -> realized share; HS: realized kills x model HS/kill -> actual), reporting change in MAE / squared error / bias / log-loss (log-loss with a spread multiplier re-fitted per substitution, stated). Predictability: each component's realized log-ratio regressed on PRE-MATCH-ONLY features (team/opponent rounds, OT and round-win history, Elo, LAN, format, history depth, player share history, roster continuity, days off, AWP proxy) with chronological month-forward cross-fitting (train strictly earlier months), fixable part = MAE / SE reduction when the component is replaced by its predicted value | (a) HLTV-wide player-series from the 2y Codex export (2024-10-23..2026-10-21 match dates; scoring from 2025-01-01 for history burn-in): cm-2 rebuilt with cm2.py/cm1.py code on the export (full available history, refits from the first week with >=1000 rows, Elo from 2024-10-23; HS from map_player_stats.hs - approximations documented), perf-2 (perf.py) on full history for Map 1 / Maps 1-3 / Maps 1-2 fallback (N0 pin not reproducible for 2y - documented); (b) real offered P1 lines (codex_export p1_units.json at 2026-10-23T04:29:55Z + saved forecast inputs from forecasts_page inputs=1, UT-1 pull reused, missing ids paged), joined to HLTV map-level realized rounds / team kills. cm-2 coefficients were fit through 2026-09-30 on overlapping data: (a) is IN-SAMPLE for the mean model - a decomposition, not an out-of-sample evaluation. DEVELOPMENT data throughout; nothing fitted for promotion | per component: share of MSE and MAE removed by its oracle substitution (chained, one-at-a-time, Shapley), bias change, log-loss change; predictable share of each; all with distinct matches and match-grouped bootstrap 95% CI (B=2000); stability by month, tier (Elo band), history depth | none (diagnostic; output = ranked list of FIXABLE error sources (large AND predictable pre-match) to be registered as separate experiments) | done | **[Superseded 2026-10-23: C1, C2]** ERROR_BREAKDOWN.md: ~85-100% of error is unpredictable (share of team kills 47-53% of kills MSE, rounds incl. OT 36-38%, HS/kill 29-35% of HS; forward-predictable <=1% of MSE in total). Fixable: (1) distribution shape - actuals beat the projection 45.4% (Map 1) / 47.4% (Maps 1-2) vs 50% live; (2) thin (<150 rounds) / fallback level -0.6..-3.0 kills and spread c 1.67-1.86 vs 1.44-1.48. Recommends registering rm-1 (rounds-mixture distribution) and th-1 (thin/fallback correction) |
| rm-1 | 2026-10-23 (frozen before any candidate number; definition: section "Frozen candidate definitions rm-1 / th-1") | Same live mean (cm-2 / perf-2 lambda unchanged), but the symmetric normal replaced by a mixture over a PRE-MATCH per-map rounds distribution (league regulation length + overtime probability and OT length from rounds_archive.sqlite, earlier maps only); kills given rounds normal with a fitted spread ratio; HS = live beta-binomial over the mixed kills. Right skew comes from real OT data | 2y HLTV export (ED-1 series.csv.gz rebuild), 6 chronological fold-periods 2025-07 .. 2026-10-21, every fitted quantity from strictly earlier matches; real P1 units start < 2026-10-22 00:00 UTC (fit through 2026-09-30). All DEVELOPMENT data | paired log score of the integer actual vs live (HLTV) AND paired log loss of P(Over given no push) vs logged live (real lines); guards Brier, calibration by favoured side, coverage identical | dev gate (section) -> frozen shadow; promotion only by the forward rule (section), owner decides | dev PASS for 3 cells -> shadow (forward clock not started) | CANDIDATES.md: passes kills Map 1 (+0.0423 [+0.0271, +0.0625] nats, 14,664 m), kills Maps 1-3 (+0.0166), HS Map 1 (+0.0077), every fold > 0; real lines on those cells +0.0029 [-0.0073, +0.0125] (297 u / 21 m) = **[Superseded 2026-10-23: C5 - inconclusive]** no measurable line gain; Maps 1-2 kills fails G3 (-0.0002 on 1,233 u), HS Maps 1-2 / 1-3 fail G4 (< 50 rounds worse). Side mix moves toward Unders (by-product, not a reason) |
| th-1 | 2026-10-23 (same section) | Thin history / fallback correction: a multiplicative level term and a spread multiplier per (model, scope, depth band) for weighted history < 150 rounds, plus a separate band for cm-2-ineligible Maps 1-2 fallback rows; BO3.gg-fallback lines take the perf-2 band of their saved depth. New definition vs FACTOR_AUDIT rd 4 (R4-thin) / rd 5 (C1): see the section | same folds; cohort rows only (outside the cohort th-1 = live by construction) | same as rm-1, on the th-1 cohort | same | dev PASS for kills Map 1 only -> shadow | CANDIDATES.md: kills Map 1 cohort +0.0309 [+0.0257, +0.0361] (3,894 m), every fold > 0; real lines 10 u / 5 m +0.0682; other 5 cells fail G1 (a fold interval crosses 0) despite positive pooled gains |
| CX-A | 2026-10-22 (Codex, protocol fixed before fitting) | earlier team round histories (pistol conversion, CT/T win share, OT / close-map frequency) improve map-length prediction | 2y HLTV export; rolling folds Jul/Aug/Sep/Oct 2026 (DEVELOPMENT); 8,717 maps / 3,802 matches | rounds MAE | none (exploratory) | done | rounds MAE gain +0.0099 [+0.0034, +0.0159]; incremental over the cm-2 14-feature family (kills Maps 1-2): +0.0020 [-0.0055, +0.0095] - inconclusive. codex_2y/results.json, family_results.json |
| CX-B | 2026-10-22 (Codex) | A + earlier full-buy / eco frequency and full-buy success | same | rounds MAE; family kills MAE | none | done | rounds +0.0168 [+0.0096, +0.0239]; family -0.0029 [-0.0124, +0.0058], worse in all 4 months - no improvement |
| CX-C | 2026-10-22 (Codex) | player opening-kill/death balance, deaths, assists beyond recent kill rate | 87,170 player-maps / 3,802 matches | kills/map MAE | none | done | +0.00057 [+0.000001, +0.00112] (negligible); family +0.0005 [-0.0006, +0.0017] - inconclusive |
| CX-OT | 2026-10-22 (Codex, diagnostic) | current-tag real-line losses vs overtime | f663af8917 P1: 230 props / 15 matches | Under hit by OT / no-OT | n/a (retrospective) | done | Under hit 16.9% in 8 OT matches vs 73.8% in 7 non-OT; explains losses after the fact, NOT a pre-match rule. Data fact: all 5,466 maps missing round outcomes are OT maps (economy stops at regulation); cm-1/cm-2 read final scores (include OT) |
| CR-1 | 2026-10-23 (owner review of ED-1 / UT-1 / CANDIDATES; logged with the corrections, descriptive only) | Corrections: (a) signed projection errors on real P1 lines (actual - projection, projection - line, actual - line) by stat, scope, version tag, favoured side, projection source; (b) power of rm-1's registered forward rule | (a) data/p1_units.json 2026-10-23T04:29:55Z: development start < 2026-10-22 (2,692 u / 155 m), confirmation >= 2026-10-22 (230 u / 15 m, descriptive only); (b) cands/p1_cands.csv.gz development per-unit paired log loss | means with match-grouped 95% bootstrap (B = 2,000); P(primary test passes) at 300 / 600 / 1,000 matches | none (no candidate, nothing promoted; no frozen definition changed) | done | corrections/results/signed_bias.md, power_rm1.md; see "Corrections 2026-10-23" below |

## Corrections 2026-10-23 (owner review)
The owner found overstated conclusions in the ED-1, UT-1 and rm-1 result cells above. Those cells are kept unchanged and
prefixed **[Superseded 2026-10-23: Cn]**; no number was changed and no frozen definition (rm-1 / th-1 sections below) was
touched. Full text: ERROR_BREAKDOWN.md, UNDER_TRACE.md, CANDIDATES.md (each has a "Corrections 2026-10-23" section); scripts
`corrections/`.
- **C1 (ED-1).** The ~85% figure is an ex-post oracle / Shapley share of squared error (kills: share of team kills 47-53%,
  rounds 36-38%; HS per kill 29-35% of HS). It does not show those components are unpredictable. Measured: ED-1 A5 regressed
  each realized component ratio on 51 pre-match features with gradient boosting (fixed hyper-parameters), 6 expanding quarterly
  folds 2025-07..2026-10-21, 2y HLTV export; the predicted part removed -2.50% .. +1.02% of MSE per component (intervals in
  ERROR_BREAKDOWN.md C1). UT-1's OT proxies (prior OT frequency, market closeness, Elo gap; 129 matches) showed no separation
  with wide intervals. Untested inputs / model classes are not covered.
- **C2 (ED-1).** "Forward-predictable <= 1% of MSE in total" / "ceiling ~1%" is replaced by the specific results: ED-1 total
  ratio +0.55% [+0.25, +0.84] (Map 1, 14,664 m), +1.04% [+0.65, +1.43] (Maps 1-2, 13,480 m), -0.91% [-1.46, -0.35] (Maps 1-3,
  5,408 m); CX-A rounds MAE +0.0099 [+0.0034, +0.0159], incremental kills MAE +0.0020 [-0.0055, +0.0095]; CX-B +0.0168 /
  -0.0029 [-0.0124, +0.0058]; CX-C +0.00057 [+0.000001, +0.00112] / +0.0005 [-0.0006, +0.0017]. Results for those inputs only.
- **C3 (UT-1).** Similar Over / Under hit rates do not establish the absence of a directional bias. Signed errors measured
  (CR-1): development kills Maps 1-2 actual - projection +0.07 [-0.70, +0.92] (1,233 u / 136 m), Map 1 -0.81 [-2.04, +0.59];
  HS Maps 1-2 -0.03 [-0.46, +0.42]; HS projection - line significantly positive (+0.16 [+0.08, +0.24] Maps 1-2, +0.17 [+0.03,
  +0.31] Map 1); within sides the error points toward the line (kills Map 1 Over-favoured -1.84 [-3.55, -0.13]); version-tag
  cells: 9cfcbd217a kills Maps 1-2 Under-favoured +1.88 [+0.11, +3.69], a310d76b55 HS Maps 1-2 Over-favoured -1.29 [-2.48,
  -0.06]; confirmation (Oct 22, f663af8917, descriptive) kills Maps 1-2 +3.76 [+0.45, +7.01].
- **C4 (UT-1).** "Lines above raw history" describes the line-vs-history disagreement on those slates; it does not show the
  projections were right. On the one settled such slate (Oct 22) outcomes landed above both line and projection.
- **C5 (rm-1).** Real-line results are inconclusive (+0.0004 [-0.0024, +0.0030] all units; +0.0029 [-0.0073, +0.0125]
  registered cells, 21 m). "A forward pass is unlikely" is withdrawn; power analysis (CANDIDATES.md C5): P(pass) at 300 / 600 /
  1,000 eligible matches = 0.025 with no true effect; 0.048 / 0.061 / 0.077 at +0.0004; 0.563 / 0.850 / 0.972 at +0.0029
  (registered cells, primary test only, upper bounds).

## Prior experiments (already run - do not repeat; see phase-0 digest)
Full digest with data, intervals and sources: `PRIOR_WORK.md` (this folder). Live model at the cutoff: tag `f663af8917` -
kills + HS Maps 1-2 on cm-2 (full corrected history; rollback CM2_LIVE=0 / CM2_HS_LIVE=0), Map 1 / Maps 1-3 / fallback on
perf-2 with the N0 pin (n0-r4), BO3.gg-only players on perf-2-bo3. Latest period any study inspected: matches to 2026-10-21
18:57 UTC (hs-cm2 hold-out); P1 aggregates viewed 2026-10-22 ~15:25 UTC (UPGRADE_2026-10-22.txt, no model fitted).

| id / family | date | what | status | where |
|---|---|---|---|---|
| v7.3-v7.5 legacy tuning (spread c, cap 78%, win/loss factors, H2H/last-10, form blend, sim, share/opening/trade kills, margins, gap trust, regional, dog_drop) | 09-28..10-07 | BO3.gg walk-forward 900 cases + member/real lines Oct 2-6 | legacy pipeline only | FRAMEWORK_v7.2.md, grader.py CONFIG |
| perf-1, perf2_hs, perf-1/2-bo3, perf-2, Maps 1-3 perf, Map 1 on perf-2 | 10-08..10-11 | performance-model rebuild on HLTV Sep 2025-Oct 8 | live (perf-2 = current Map 1 / Maps 1-3 / fallback) | MODEL_VERSIONS.md, FACTOR_AUDIT.md |
| FACTOR_AUDIT rd 1 (G1 AWP proxy, G2 eco/rating, G3 Elo x2, G4 maps, G5 schedule, team prior, k0, rounds matchup) | 10-08/09 | ridge on perf-1 residual, TEST 08-13..10-08 | all rejected | FACTOR_AUDIT.md |
| FACTOR_AUDIT rd 2 (map spread, CT/T, IGL, G2 on HS, line moves) | 10-09 | frozen defs | rejected; IGL = shadow idea needing dated roles | FACTOR_AUDIT.md |
| FACTOR_AUDIT rd 3 (share, form, H2H, opp pace, blowout, stage, opp roster, same-day, group) | 10-10 | 42,587 series-players | all rejected (stage: re-test with more data) | FACTOR_AUDIT.md |
| FACTOR_AUDIT rd 4 (k0, half-life, hot shrink, ko, thin spread, team consistency) | 10-12 | HLTV + board 360 units / 80 m | all rejected | FACTOR_AUDIT.md |
| FACTOR_AUDIT rd 5 (C0 BO3.gg fill, C1, C2, C3 line blend) | 10-12 | thin / returning / low coverage | nothing frozen (C3 thin lead) | FACTOR_AUDIT.md |
| match_m1, perf_map1, prior_smooth (valid from 10-14 22:53), online_taper/none, no_bo3, perf1_g7, legacy, old_spread, no_rest_bonus, pre_fix_v2 | 10-08..10-09 | picklog comparison shadows | shadow; none promoted | MODEL_VERSIONS.md, CHALLENGERS.md, CHECKPOINT_2026-10-14_corrected.md |
| lb-001 line-blend-1 | 10-12 15:49 | probit shrink to 50% (kills 0, HS 0.5) | shadow; forward level with live | CHALLENGERS.md, ACCURACY_2026-10-16.md |
| staged N vs N0 replay | 10-13 | repaired history | not activated; N0 pinned | HLTV_COVERAGE.md |
| rr-1 RR-KS (FACTOR_AUDIT rd 6) | 10-13 17:51 | market series price -> round race | passed HLTV test -> shadow; no line gain | FACTOR_AUDIT.md, CHALLENGERS.md |
| moneyline features + 208 beat-the-market variants; ml-candidate-1 | 10-12 | series-winner prices | none beats market; candidate shadow | ML_EVAL.md |
| checkpoint P1 | 10-14 | live on book lines | P1 adopted; printed 57% hit 48% | CHECKPOINT_2026-10-14_corrected.md, EVALUATION.md s8 |
| pl-1 / C1 / pl-2 | 10-15 | line-anchored location | pl-1 blocked; pl-2 shadow | CHALLENGERS.md |
| A15 full history + one-change table; fh-1 | 10-15 | N0 starvation | fh-1 shadow; others rejected | ACCURACY_2026-10-15.md |
| CORR (+thin/form/book/logit) -> lc-1 | 10-16 | real-line correction | lc-1 shadow, no further development | ACCURACY_2026-10-16.md |
| real-line archive E1/E2, actionable line, HS RECAL/Z-REC/Z-FULL, OA-1/2/3, CUR/CUR-FULL/GLM/GBM | 10-16 | Oct 2-16 offered lines + HLTV | nothing qualified; GLM -> cm-1; hs-1 registered | REALLINE_ARCHIVE_2026-10-16.md, research/realline_archive_2026_10_16 |
| hs-1 hs-calibration-1 | 10-16 21:18 | HS logit on fh-1 z | shadow | learning.HS_CAL |
| cm-1 complete-model-1 | 10-16 22:35 | 14-feature GLM | shadow (forward +0.0037 n.s. at 20 m) | CHALLENGERS.md, research/cm1_forward_2026_10_18 |
| P2 sequential proposal | 10-16 | qualification route | not adopted | EVALUATION_P2_PROPOSAL.md |
| Codex 10-18 study: corrected GLM, +6 context features, recalibration, native Map 1, normal HS, rounds / no_bo3 diagnostics | 10-18 | HLTV to 10-18, P1 Oct 11-18 | corrected GLM -> cm-2; rest rejected | claude-handoffs/accuracy-2026-10-18 |
| cm-2 complete-model-2-kills | 10-18 21:55 | corrected GLM, kills Maps 1-2 | live 10-20 by owner override (no review) | CHALLENGERS.md, MODEL_VERSIONS.md |
| bo3-env-2 | 10-18 | BO3.gg unknown env bug | live fix | MODEL_VERSIONS.md |
| perf-3 | 10-20 | level / slope / Elo correction on perf-2 | FAILED hold-out | research/proj_fix_2026_10_20 |
| hs-cm2 | 10-21 | HS Maps 1-2 on cm-2 kills | PASSED -> live f663af8917 | research/hs_cm2_2026_10_21 |
| perf-refit-1 (150-match auto refit, e.g. ch-009) | auto | perf-2 parameter refit | shadow; ch-009 non-positive (details in production learning.db only) | learning.TRAIN_RULE, UPGRADE_2026-10-22.txt |

## Frozen candidate definitions rm-1 / th-1 (written 2026-10-23 BEFORE any candidate number was computed)
Source: ERROR_BREAKDOWN.md (ED-1) recommendations 1-2. Both are evaluated SEPARATELY against the live model (never stacked; a
combination would be a new registration). Live grading is untouched; every result here is development evidence only.

### Shared definitions
- Live model replicated as in ED-1 (`error_breakdown/common.py live_frame`): Map 1 / Maps 1-3 = perf-2 (full-history replay),
  Maps 1-2 = cm-2 where eligible else perf-2 fallback; kills spread c 1.442 (Map 1) / 1.457 (Maps 1-2 perf-2, Maps 1-3) /
  1.4756515691 (cm-2); HS = beta-binomial (kappa 100, the row's HS per kill) over the kills pmf. Integer pmf = normal(lam,
  c sqrt(max(lam,1))) discretised to integers and conditioned >= 0 (perf.kills_pmf). P(Over | no push) at a line = the live
  cut rule (x.5: at the line; whole: line +- 0.5) on the continuous normal for kills (grader p_three) and on the HS pmf for HS;
  kills chance capped to [0.22, 0.78] exactly as live (HS uncapped, as live). Real lines: live = the LOGGED p_over of each P1
  unit (sanity: a replay from the saved lambda / sd / hs_per_kill / spread_c_kills is reported beside it).
- Populations. (a) HLTV: ED-1 `series.csv.gz` (scored rows from 2025-01-01; forfeits and incomplete scoreboards excluded;
  match dates <= 2026-10-21). (b) Real lines: P1 units (codex_export p1_units.json, 2026-10-23T04:29:55Z) for matches starting
  before 2026-10-22 00:00 UTC (2,692 units / 155 matches as exported; AU-1 D1 duplicates kept as exported, intervals by match).
- Development split: evaluation fold-periods F1 2025-07-01..09-30, F2 2025-10-01..12-31, F3 2026-01-01..03-31,
  F4 2026-04-01..06-30, F5 2026-07-01..09-30, F6 2026-10-01..10-21, assigned by the match's Map 1 day (every scope and stat of
  a match falls in one period; a match is never split between fit and evaluation). For a fold every fitted quantity uses only
  matches dated strictly before its first day. Real lines (dated 2026-10-11..10-21) use the F6 fit (data < 2026-10-01).
  Every period was already inspected by earlier studies: all of this is DEVELOPMENT evidence, never a holdout claim.
- Cells: (stat in {kills, HS}) x (scope in {Map 1, Maps 1-2, Maps 1-3}) = 6 per candidate, 12 in total.
- Metrics. HLTV primary: paired gain in mean log score of the integer actual (nats per player-series; + = candidate better).
  Real-line primary: paired gain in log loss of P(Over | no push) vs the logged live chance (pushes excluded and counted).
  Guards: Brier (paired); calibration by favoured side (each model's own side: p > 0.5 Over, else Under; gap = mean favoured
  chance - favoured hit rate; compared as |gap| candidate - |gap| live), on real lines and on HLTV at a synthetic line
  floor(projection) + 0.5 (kills chance capped as live); coverage = the candidate produces a chance for exactly the units /
  rows live does (must be identical). Also reported: favoured-side hit rate, number of 55%+ picks, side mix (descriptive only,
  NEVER a target), gain split into units whose favoured side is unchanged vs flipped (probability quality vs pick selection),
  PIT 50/80/95 coverage. Intervals: match-grouped bootstrap 95% (B = 2,000; subgroups B = 1,000), seed 20261023.
- Multiple comparisons: 12 HLTV cells + 2 real-line pooled tests. Besides the per-fold rule, the pooled (all folds) HLTV gain of
  a cell must clear a Bonferroni interval at 1 - 0.05/12 (99.58%). Subgroup screens are reported in full; a deterioration flag
  is a 95% interval entirely below 0.
- Development gate (per candidate, per cell; a shadow is implemented only for cells that pass, live elsewhere):
  G1 HLTV gain 95% interval lower bound > 0 in EVERY fold-period in which the cell has >= 200 distinct matches;
  G2 pooled HLTV gain Bonferroni (99.58%) lower bound > 0;
  G3 real lines: point gain >= 0 pooled over all units of the passing cells AND per stat pooled; a single cell with >= 100 units
     and >= 15 matches must also have point >= 0;
  G4 no significant deterioration (95% interval entirely < 0) of the primary metric in any subgroup among scope, stat, history
     depth (< 50, 50-150, 150-500, 500-1500, 1500+ weighted rounds), tier (match mean Elo fifths), on HLTV (pooled folds) and on
     real lines; real-line Brier not significantly worse; calibration by side not significantly worse (|gap| difference
     interval entirely > 0) on real lines and on HLTV synthetic lines;
  G5 coverage identical.
  A candidate passes if >= 1 cell passes G1-G5. Nothing is tuned after seeing these numbers; a failed candidate is reported as
  failed (no re-run with changed definitions under the same id).
- Forward confirmation (passing candidates only): the shadow config is re-fitted ONCE on all development data (matches
  <= 2026-10-21) with the same code, frozen (parameters + sha256 in CHALLENGERS.md) before deployment, and logged per forecast
  beside the live grade (raw probabilities stored apart from any recommendation; live grades byte-identical). Forward set = P1
  units of matches starting at/after the shadow's deploy time (and >= 2026-10-22 00:00 UTC). Looks only at the REVIEW_RULE
  checkpoints: 300, 600, 1,000 distinct forward matches with >= 1 eligible logged unit (th-1 additionally needs >= 60 distinct
  cohort matches at a look, else that look is skipped). Promotion requires at the look: forward real-line paired log-loss gain
  95% match-grouped interval > 0 over the candidate's cells, Brier point >= 0, calibration by side not worse (|gap| point not
  larger than live's by > 1.0 pt and its interval not entirely worse), coverage identical. Owner rules: never promoted for a
  different Over / Under mix, never by lowering chances to fix calibration without this validated correction; the owner decides.

### rm-1 rounds-mixture distribution (family `rounds-mixture-1`)
- Per-map rounds pmf pi(r): from `rounds_archive.sqlite` (validated round sequences incl. recovered OT; rounds per mapstatsid =
  its row count; rejected pages excluded; a map without an archive sequence uses the export map score sum, which includes OT).
  Training window: all maps dated in the 365 days before the fold's first day (F1: 2024-10-23..2025-06-30). pi = (1 - q) P_reg
  + q P_ot: q = share of maps with > 24 rounds; P_reg = empirical pmf of rounds on maps with <= 24 rounds (support 13..24);
  P_ot = empirical pmf of total rounds on OT maps (support as observed in training: 28, 29, 30, 34, ...). League level for all
  scopes, BO1 / BO3 / LAN / online pooled. Team terms: none (ED-1 A5: regulation length predictable part +0.10 / +0.41% of MSE
  and the mean is pinned to live, so a team regulation term could only move the conditional spread; OT gets no team term).
- Scope rounds R = sum of m iid draws from pi (m = 1 / 2 / 3), i.e. pi convolved m times; E[R] = m E_pi[r].
- Kills: rate = lam / E[R] (mean exactly the live lam); K | R ~ normal(mu_R = rate R, sd_R = rho c sqrt(max(mu_R, 1))), c = the
  live kills spread of the row, discretised + conditioned >= 0 per R for the integer pmf; P(Over | no push) for kills lines =
  mixture over R of the continuous-normal cut probabilities, then the live cap.
- rho: one per (scope, model): (Map 1, perf-2), (Maps 1-2, cm-2), (Maps 1-2, perf-2: fitted on the perf-2 Maps 1-2 projection
  2 x lam_p2_1 of ALL rows - used for fallback rows and pre-10-20 / BO3.gg real lines), (Maps 1-3, perf-2); maximum likelihood
  of the integer kills actual (golden section on [0.3, 1.5]) on a fixed-seed sample of <= 40,000 training rows (day >=
  2025-01-01 and before the fold). Real lines use the rho of their (scope, source family); perf-2-bo3 = perf-2.
- HS: the live beta-binomial (kappa 100, the row's HS per kill) over the rm-1 kills pmf - no HS-specific parameter.
- Dependence across overlapping scopes: the Map 1 distribution is the 1-map case of the same per-map construction used for
  Maps 1-2 / 1-3 (same pi; Maps 1-2 rounds = Map 1 rounds + Map 2 rounds); all scopes of a match share its fold and its
  bootstrap cluster. Reported diagnostic: Spearman correlation of PIT(Map 1) and PIT(Maps 1-2) for the same player-series,
  live vs rm-1 (must not move by more than 0.02).
- Fitted per fold: q, P_reg, P_ot (pi), 4 x rho. Nothing else.

### th-1 thin / fallback correction (family `thin-fallback-1`)
- Cohort and band (pre-match facts only): r_w = weighted history rounds (HLTV: the rebuild's `r_w`; real lines: the saved
  `history_rounds_weighted`, for perf-cm2 lines the cm-2 inputs' r when saved). Bands: D0 r_w < 50, D1 50 <= r_w < 150. Cells:
  perf-2 x scope {1, 12, 123} x {D0, D1} (the perf-2 Maps 1-2 cells are fitted on 2 x lam_p2_1 of all rows and applied to
  pre-10-20 perf-2 Maps 1-2 lines and BO3.gg Maps 1-2 lines); cm-2 x {D0, D1} (cm-2-eligible Maps 1-2 rows); F =
  cm-2-ineligible Maps 1-2 rows (perf-2 fallback, any depth; precedence over the D bands; real lines: a saved
  `cm2_live.ineligible` marker). perf-2-bo3 lines: the perf-2 band of their scope by their saved depth; >= 150 = not in the
  cohort (no BO3.gg-specific parameter: there are no earlier BO3.gg training rows).
- Formula: lam' = lam exp(a_cell); kills spread c' = c exp(s_cell); kills = the live discretised normal / cut rule with
  (lam', c'), cap as live; HS = live beta-binomial (row HS per kill) over the th-1 kills pmf (the HS level moves with kills).
- Fit: (a, s) per cell by maximum likelihood of the integer kills actual (Nelder-Mead from (0, 0); a clipped to [-0.5, 0.5],
  s to [-0.5, 0.7]) on training rows (day >= 2025-01-01, before the fold); a cell with < 500 training rows gets (0, 0).
  Fitted per fold: 9 cells x 2 = 18 numbers. Nothing else.
- HLTV gate cells = stat x scope on the LIVE-route rows of the cohort (outside the cohort th-1 = live). The perf-2 Maps 1-2
  bands (not a live HLTV route after 10-20) are reported on HLTV as a check and must not be significantly worse (G4).
- Different from FACTOR_AUDIT rd 4 R4-thin (spread x m for r_w < 600, Maps 1-2 perf-2 only, projection unchanged, N0-era
  export from 2025-10-02 with truncated r_w; rejected +0.0008 log) and rd 5 C1 (stronger shrink k0_eff for r_w < 600, MAE;
  rejected +0.0016): th-1 is a level AND spread correction, for r_w < 150 (the depth where ED-1 found -0.76 / -0.61 kills bias
  and c 1.67-1.71), fitted on full-history 2-year data with untruncated 420-day windows, on all three scopes, on cm-2 rows,
  and with a separate cm-2-ineligible fallback band (ED-1: -3.01 kills, c 1.86). It is a new definition, not a re-run.
- Known mismatch (stated before the run): live perf-2 reads the N0-pinned history (smaller r_w, other lam) while the HLTV fit
  uses full-history perf-2; the real-line evaluation uses exactly the saved live inputs, i.e. what a deployment would apply.
