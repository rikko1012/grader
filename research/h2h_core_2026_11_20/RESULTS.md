# Roster-core H2H (hc-1): walk-forward result

Run 2026-11-20 with `walkforward.py`, following FROZEN_DEFINITIONS.md and Amendment 1 (the split dates). Raw output is in `walkforward_out.json`.

## Decision

**Not promoted: nothing passes.** None of the three H2H definitions improves the cm-2 kills projection on TEST:
- N, the org rule used today;
- C, the new roster-core rule;
- U, the two combined.

At real book lines, all three point to the losing side more often than not (hit rate below 50%). The live grade is unchanged. The roster-core meetings ship as display only, on the card and in the board H2H cell.

## Rows

- 101,380 series-players: TRAIN 40,189 / VALID 23,908 / TEST 37,283.
- TEST covers 3,824 matches.
- Baseline TEST kills MAE is 6.103 (cm-2, `cm2_lam12`).

| Rule | b TRAIN / VALID | TEST dMAE (95%, match-grouped) | Rows with a meeting (TEST) | dMAE on those rows | Pass |
|---|---|---|---|---|---|
| N: same org (today's rule) | +0.061 / +0.096 | -0.0001 (-0.0010 to +0.0008) | 14,445 (38.7%) | -0.0002 (-0.0025 to +0.0020) | no |
| C: 3+ of tonight's players (new) | +0.043 / +0.109 | -0.0002 (-0.0008 to +0.0005) | 14,554 (39.0%) | -0.0005 (-0.0021 to +0.0012) | no |
| U: N or C (shown on the card) | +0.049 / +0.102 | -0.0002 (-0.0010 to +0.0005) | 15,769 (42.3%) | -0.0006 (-0.0024 to +0.0011) | no |

## Meetings only the roster rule finds

These are org switches, or meetings where the player himself was on another team. They make up 3.6% of TEST rows (1,324 rows).
- Mean residual on these rows: +0.027.
- Correlation between the factor and the residual: +0.043.
- dMAE: -0.0023 (95% interval -0.0065 to +0.0020).
- Conclusion: the interval includes 0, so there is no measurable effect.

## Real book lines

Kills, Maps 1-2, 5,604 decided lines. This is the hit rate of the side the factor points to: Over when f > 0, Under when f < 0.

| Rule | Lines | Hit rate (95%, match-grouped) |
|---|---|---|
| N | 3,102 | 47.1% (44.7% to 49.5%) |
| C | 2,998 | 46.7% (44.3% to 49.0%) |
| U | 3,231 | 46.9% (44.7% to 49.2%) |
| roster-only meetings | 129 | 44.2% (33.1% to 55.6%) |

The interval for N, C and U lies entirely below 50%. Following past over-performance against an opponent loses at real lines. The TRAIN and VALID coefficients are positive (past over-performance carries on a little), but the TEST gain is zero and the side it points to at real lines loses more often than it wins. The card already says of H2H: "Past meetings at today's line. Not a predicted chance."

## Limits

- The baseline exists from 2025-11-01 only. Earlier meetings have no projection, so they do not enter the feature, and TRAIN rows have fewer meetings.
- In the test, the opponent's current roster is the lineup of its previous series, so a first series under a new team id has no roster. The live card uses the team's newest HLTV series, if it is no older than 60 days.
- The R3-h2h test from Oct 10 (FACTOR_AUDIT.md, rejected) used kills per round against perf-2. This test uses residuals against cm-2. Both come out null.
