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

## Prior experiments (already run - do not repeat; see phase-0 digest)
(to be filled by phase 0)
