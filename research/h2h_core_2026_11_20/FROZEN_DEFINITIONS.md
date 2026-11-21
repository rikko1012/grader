# Roster-core H2H (hc-1): frozen test definitions

Written 2026-11-20 BEFORE running anything. Owner request (2026-11-20): "3 people = a team's core: if a player played against 3
players on the same team then that is an H2H, not only their team versus that team". Teams also switch orgs, so the
rule reads HLTV player ids only, never team names.

## What is tested
- **Live grade: unchanged.** The live CS2 kills grade (cm-2 performance model for Maps 1-2) does not read H2H.
- **Question:** does a roster-core H2H factor improve the projection? It is tested as a shadow factor under the factor
  rule: it may move grades only if it passes this walk-forward test, and then only with the owner's go-ahead.

## Data
- Local HLTV extract `cs2_scout_ops/data/cs2_full` (through 2026-11-05):
  - `work/player_map.csv.gz` gives per-map kills;
  - `work/team_match_ctx.csv.gz` gives the lineup of each team in each match (HLTV player ids);
  - `proj_full/baseline.pkl` gives the baseline projection `cm2_lam12`, the cm-2 Maps 1-2 kills projection made with
    history strictly before the match day.
- **Rows:** series-players with `cm2_lam12` and both Map 1 and Map 2 kills. Actual `y` is the Map 1 + Map 2 kills.
- **Target:** relative residual `r = (y - lam) / lam`, clipped to [-1, 1] when it is used as a feature input.

## Definitions (each uses only series strictly before the match day, within 365 days)
- **Opponent current roster:** the opponent team's five player ids in its previous series. This means the same HLTV team id,
  the newest earlier date, and exactly 5 ids. With no such series there is no roster and no core H2H.
- **N (name / org H2H, as today):** earlier series where his opponent had the same HLTV team id as tonight's opponent, with
  any lineup.
- **C (roster-core H2H, new):** earlier series where at least 3 of the opponent's current five played on the side against
  him. Team ids and team names are ignored. Series where those players were his teammates never count.
- **U (union, proposed display rule):** N or C.

## Feature
For each definition, the shrunk mean of his earlier relative residuals in those meetings:

`f = sum(w_i * r_i) / (sum(w_i) + 2)`, where `w_i = 0.5 ** (age_days_i / 120)`.

The feature is 0 when there is no meeting.

## Fit and score
- **Splits by match day:**
  - TRAIN: before 2025-11-01;
  - VALID: 2025-11-01 to 2026-03-31;
  - TEST: 2026-04-01 to 2026-11-05.
- **Fit:** `r ~ a + b * f` (OLS) on TRAIN and again on VALID, to check the sign.
- **Candidate projection:** `lam' = lam * (1 + b_TRAIN * f)`.
- **Score:** TEST kills MAE of `lam'` vs `lam`, with a match-grouped bootstrap 95% interval (2,000 resamples, seed 7).
- **Pass:** the TEST dMAE interval is entirely below 0, AND `b` has the same sign on TRAIN and VALID.
- **Reporting:**
  - C and U are reported next to N on the same rows;
  - also the subset where C finds a meeting that N misses (an org switch, or a player who changed teams).
- **Real lines** (`proj_full/line_rows.csv.gz`, kills, scope 12, decided rows):
  - hit rate of the side `f` points to (Over if `f > 0`, Under if `f < 0`) on rows with a meeting;
  - match-grouped 95% interval;
  - reported for N, C and U.
- **Decision:**
  - a pass becomes a shadow challenger, and promotion needs the owner's go-ahead;
  - a fail leaves the live grade untouched;
  - the display change (C ∪ N meetings on the card) is display only and ships either way.

## Amendment 1 (2026-11-20, after the first run; this changes only the split dates)
The first run found the TRAIN split empty. The baseline `cm2_lam12` in `baseline.pkl` exists only from 2025-11-01. The frozen
split had assumed cm-2 coverage from 2024. That run therefore had no TRAIN fit (b = 0, dMAE = 0). It showed two things, both
already seen before this amendment:
- the 2025-11 to 2026-03 coefficients;
- the real-line hit rates, which do not depend on the split.

The first run's output is kept as `walkforward_run1_empty_train.json`.

The new splits keep the same rule, three chronological blocks:
- TRAIN: 2025-11-01 to 2026-03-31;
- VALID: 2026-04-01 to 2026-06-30;
- TEST: 2026-07-01 to 2026-11-05.

Everything else is unchanged. A meeting counts in a feature only when the earlier series has a baseline projection, so meetings
before 2025-11-01 are not used.
