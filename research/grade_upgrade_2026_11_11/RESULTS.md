# CS2 grade-upgrade study - results (2026-11-11)

Definitions: FROZEN_DEFINITIONS.md (commit d8aa3e3, before scoring). Full report with every table:
`cs2_scout_ops/logs/grade_upgrade_cs2_2026-11-11.md`. Outputs: `cs2_scout_ops/data/grade_up_1111/`.

Test set W1-W3 (Oct 19 - Nov 8): 8,032 graded app rows / 296 matches. Live 53.5% [51.6, 55.4]; Overs 52.5%,
Unders 54.3%, Over share 43.7%. Staked 2,679 at 53.1%.

- **Family H (23 pre-match HLTV factors as a projection multiplier, fitted on 101,380 history rows):** none passes.
  Differences -0.83 to +0.01 points; 6 are significantly worse.
- **Family B (12 forecast-context cohorts, board-fitted):** none passes. Differences -0.95 to -0.26; 5 significantly
  worse. B09 is not a real test (flag only on W0 rows).
- **Family G (grade layer, 8 rules):** G6 passes; G1-G5, G7, G8 do not.
- **G6 - lift the AWPer-on-HS 0U gate** (sr-gates-1's named forward re-test, starts after 2026-10-28 13:40 UTC):
  staked 1,193 -> 1,314, hit 53.0 -> 54.1, +1.13 [+0.20, +2.04]; staked Overs 51.2 -> 51.6, Unders 54.8 -> 56.7;
  staked Over share 51.6 -> 50.8. The added picks: 121 rows / 95 units / 79 matches, 65.3% [55.9, 74.8] vs 58.7%
  printed. Whole record Oct 11 - Nov 8: 381 rows / 239 matches, 62.5% [56.8, 68.2] vs 59.2% printed. Fresh holdout
  Nov 9-11 (after scoring): 35 picks / 18 matches, 60.0%.
- **Shipped 2026-11-12 02:48 UTC as awp-hs-1** (owner "yes"; main 1900a1e, model tag `042a5339de`, rollback
  `CS2_AWP_HS_LIFT=0`): the rule is in `grader.grade_leg` (Gate A); `awp_hs_lift.py` holds the stake-rebuild helper;
  tests `tests/test_awp_hs_lift.py`. See MODEL_VERSIONS.md and FACTOR_AUDIT.md round 8.
- **Board preview (2026-11-11 23:17 UTC, 376 lines / 14 matches):** 0 sides, 0 letters, 0 chances change; 14 picks
  0U -> staked (4 Over, 10 Under); staked 152 -> 166; staked Over share 59.2% -> 56.6%.

About 43 tests were run; one pass at this level is roughly what chance produces. G6 has no fitted parameter, was named
in advance, and its cohort beat 55% in all five earlier periods. Owner decision required before any wiring.
