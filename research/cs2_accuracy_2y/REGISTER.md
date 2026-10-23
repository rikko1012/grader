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
(to be filled by phase 1)

## Experiment register
| id | date registered | hypothesis | data / split | primary metric | promotion rule | status | result |
|---|---|---|---|---|---|---|---|
| UT-1 | 2026-10-22 | Under concentration trace: the share of 55%+ P1 picks that are Under, the step-by-step decomposition of projection - line (perf-2 rate / prior shrink / environment blend / opponent factor / rounds; cm-2 features; grader), and whether the direction and confidence are justified by settled outcomes; implementation-bug hunt (sign, double application, rounds, stale inputs) | P1 units (learning p1_units export, latest standard board forecast >= 30 min before start, pushes excluded+counted) joined to each forecast's saved grader inputs (forecasts_page inputs=1); HLTV DB read-only for repros. Diagnostic only - DEVELOPMENT data, no model is fitted or compared for promotion | share Under; mean(proj-line), mean(actual-line), mean(actual-proj) with match-grouped bootstrap 95% CI (B=2000); printed vs won by band/side | none (diagnostic; any bug goes to a separate fix with its own version + regression test) | running | UNDER_TRACE.md |

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
