# Model card - Scout Sports sm-nfl-2 (NFL challenger, SHADOW only)

Registered protocol: `sports/research/REGISTER.md` amendment A6, committed (32241f0) before any sm-nfl-2 fit or
score. Code: `sports/model/nflv2.py`. Tune report: `sports/research/results/model_nflv2_tune.json`. Frozen
configuration `sports/model/artifacts/model_config_nflv2.json` (config sha `9d523145b99dbcd2`); forward artifact
`smnfl2_nfl.json` (sha `05f7f75a46f61e85`, fitted 2026-10-25T05:04:47Z = freeze_at); hash manifest
`MANIFEST_NFLV2.json`. Post-hoc diagnostic D: `sports/research/results/model_nflv2_diagnostic_D.json`.

**NFL stays CONTAINED** (no lean / edge / confidence; projection only, "NFL model not validated", vg-1). Nothing here
changes sm-1, sm-2, the board, grades or stakes.

## What it is

sm-1's frozen configuration per market (half-lives, family, EB efficiency) + the A5 round-2 factors, with:
- **Participation split.** Full-role game = offense snap share >= 50%. Performance given role: the opportunity history
  is the EW mean over prior FULL-ROLE games only (partial games skipped); GLM and family fitted on train rows whose
  game was full-role. Partial component: empirical y / mu_full of partial train rows. P(full role | pre-game) by a
  logistic regression (EW snap share, previous snap share, EW partial share, no full-role history, position).
  PMF = pi P_full + (1 - pi) P_partial.
- **cal-2.** Mean factor c = sum y / sum E[X] on tune (bias removal) instead of cal-1's CRPS-chosen c; s by tune CRPS.

## Results

Tune (2025 weeks 1-9; development evidence, cal-2 fitted on these rows; sm-1 = its frozen configuration). 95%
game-clustered intervals, B = 2,000.

| Market | CRPS v2 - sm-1 | log-loss at L_ref v2 - sm-1 | bias v2 / sm-1 | ECE / in-the-large v2 | PIT cov80 v2 | cal-2 (c, s) | A6 2 sanity rule |
|---|---|---|---|---|---|---|---|
| pass_yds | -0.692 [-2.348, +0.972] | +0.008 [-0.009, +0.027] | -0.00 / -0.67 | 0.009 / +0.015 | 0.798 | 0.986, 0.98 | kept -> shadow |
| rush_yds | +0.645 [+0.202, +1.043] | +0.026 [+0.008, +0.044] | +0.00 / -0.94 | 0.054 / +0.014 | 0.762 | 0.955, 0.98 | **dropped** (worse) |
| rec_yds | +0.042 [-0.092, +0.185] | +0.012 [-0.006, +0.041] | +0.00 / +0.16 | 0.015 / +0.003 | 0.804 | 0.965, 0.96 | kept -> shadow |
| rush_att | +0.098 [+0.004, +0.186] | +0.031 [+0.007, +0.054] | -0.00 / -0.00 | 0.021 / +0.020 | 0.777 | 0.936, 0.88 | **dropped** (worse) |
| rec | +0.006 [-0.005, +0.017] | +0.017 [-0.002, +0.046] | +0.00 / -0.00 | 0.035 / +0.003 | 0.803 | 0.983, 0.94 | kept -> shadow |

Diagnostic D (POST-HOC on sm-1's spent windows - not evidence, never a gate; the design was written after these
windows' sm-1 results were known):

| Market | Window | Rows / games | CRPS v2 - sm-1 | log-loss v2 - sm-1 | bias v2 / sm-1 | ECE v2 / sm-1 | in-the-large v2 / sm-1 | cov80 v2 / sm-1 |
|---|---|---|---|---|---|---|---|---|
| pass_yds | 2025 wk 10 - post | 302 / 149 | -1.969 [-3.507, -0.355] | -0.008 [-0.024, +0.007] | +4.29 / +4.59 | 0.043 / 0.034 | -0.002 / +0.007 | 0.759 / 0.815 |
| pass_yds | 2026 wk 1-7 | 194 / 94 | +1.026 [-0.155, +2.414] | +0.025 [+0.006, +0.045] | -7.40 / -9.86 | 0.033 / 0.063 | -0.047 / -0.057 | 0.770 / 0.826 |
| rec_yds | 2025 wk 10 - post | 1207 / 150 | -0.154 [-0.277, -0.022] | -0.003 [-0.010, +0.004] | +0.19 / +0.32 | 0.035 / 0.056 | +0.011 / +0.014 | 0.804 / 0.804 |
| rec_yds | 2026 wk 1-7 | 779 / 94 | -0.142 [-0.307, +0.032] | -0.004 [-0.012, +0.005] | -1.37 / -1.38 | 0.048 / 0.054 | -0.015 / -0.014 | 0.786 / 0.786 |
| rec | 2025 wk 10 - post | 1207 / 150 | -0.010 [-0.019, -0.001] | -0.006 [-0.013, +0.002] | +0.11 / +0.08 | 0.033 / 0.025 | +0.020 / +0.015 | 0.799 / 0.796 |
| rec | 2026 wk 1-7 | 779 / 94 | -0.007 [-0.023, +0.008] | -0.002 [-0.012, +0.008] | -0.12 / -0.13 | 0.035 / 0.054 | -0.025 / -0.025 | 0.776 / 0.768 |

Held-out H2 (forward only - no historical NFL period is unscored): **0 / 48 games** at the freeze; book-line part
0 / 150 props per market. Promotion rule and display bar: REGISTER A6 5. **No market is eligible to un-contain.**

## Findings

- The participation split did not remove the left tail: partial-role games are 21% of rec_yds / rec tune rows and
  35% of rush rows, and the market settles them, so the tail mass is real (rec_yds mass at <= 25% of L_ref: 0.185 v2
  vs 0.187 sm-1 on tune). For RBs, "< 50% snaps" is the normal committee role, not an injury exit, which is why the
  rush markets got worse.
- cal-2 removes the tune bias by construction, but sm-1's tune bias was already ~0; the 2026 under-forecast
  (pass_yds -9.9) is drift after tune that a tune-fitted factor cannot fix (D: -7.4 with sm-nfl-2).
- On the 2026-10-24 store board (unsettled, descriptive) the share of props favouring Over is pass_yds 0.37 (sm-1
  0.23), rec_yds 0.32 (0.36), rec 0.30 (0.27): still Under-leaning; criterion (g) decides on settled H2 data.

## Shadow

`model-forward` slot, same process and frame as sm-1; rows in `shadow.db` (model_version `sm-nfl-2`) + `shadow_inputs`
(saved row, sm-1 probabilities of the same offer). End to end on the 2026-10-24 store copy: sm-1 forecasts identical
with the shadow off / on, 624 shadow rows (pass_yds 52, rec_yds 335, rec 237), peak RSS 179.3 MB vs 177.1 MB (cap
340), +7.5 s. Off switch `SPORTS_SHADOW_NFLV2=0`. One-time scorer: `python -m sports.model.nflv2 heldout <history.db>`.
