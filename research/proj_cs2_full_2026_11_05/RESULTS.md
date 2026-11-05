# pd-1: full-history CS2 projection distribution (2026-11-05, research only, NOT deployed; CS2 live tag stays 3007c59b74)

Owner, 2026-11-05: "dont ruin calibration simply fix projections ... we have every possible inch of data". This run uses the
whole local HLTV extract: 634,687 player-maps from 2024-01-09 to 2026-11-05. Projections are scored on every historical
outcome first, and on the offered book lines second.

## Data and method
- **Source.** The local read-only extract `C:/Users/tikic/cs2_scout_ops/data/cs2_full`. No production reads or writes.
- **Rows.** One row per (match, player) for Map 1, Maps 1-2 and Maps 1-3, for kills and HS (`features.py`). Features come
  only from maps dated before the match day. A day's rows are featurised first, and only then does that day add to the
  state. The test `test_feature_pass_excludes_same_day` checks this.
- **Features.**
  - Player rates at three half-lives: kills, HS per kill, assists, deaths, ADR, KAST, opening kills/deaths, multi-kills,
    rating, swing, clutches, share of team kills.
  - Rate in tonight's environment (LAN or online).
  - Days since the player's last map, and his maps with his current team.
  - For both teams: rounds per map, kills conceded per round, map win rate, round share and overtime rate.
  - Walk-forward Elo for both teams, the gap and the mean (a tier proxy).
  - Rest days, lineup changes and stand-in proxy, BO format, event-name flags.
  - Expected maps, from each team's decayed picks: the player's per-map kill-rate ratio, the global kill rate and rounds
    on those maps, the map-experience edge, and the entropy of the map-pick mix.
  - Snapshot tables (AWP share, Liquipedia roles) are left out because they would leak later information into older rows.
- **Model (`model.py`).**
  - Mean: one LightGBM Poisson model per (stat, scope).
  - Variance ratio: a LightGBM gamma model fitted on out-of-time residuals (the mean model refitted without the last 90
    days, predicting those days).
  - Predictive pmf: negative binomial with mean mu and variance v·mu.
  - No line input, no chance shrink, no side intercept, no balance forcing. The family and settings were fixed before
    scoring, with no iteration.
- **Walk-forward (`evaluate.py fit`).** The model is refitted monthly. Each month is predicted by a model trained on
  strictly earlier days, with training starting 2024-04-01.
  - Development window: 2025-11 to 2026-09.
  - Final untouched window: 2026-10-01 to 2026-11-05. Every settled book line falls in it.
- **CIs.** 95% bootstrap over matches (2,000 draws).

## Baseline: the live distribution reconstructed offline (`baseline.py`)
- **Code.** It runs the live code itself (`perf.project`, `cm2.build_tables`/`cm2.projection`, `cm1.hs_ratio`, beta-binomial
  HS) with the live routing:
  - Map 1 and Maps 1-3: perf-2.
  - Maps 1-2 kills: cm-2.
  - Maps 1-2 HS: hs-cm2.
- **N0 input revision.** Rebuilt from `raw_pages_meta` and `team_rankings`. It gives 8,597 seed matches, the same as
  production's 8,597, plus 585 later top-200 matches.
- **How close it is to live.** Compared with the logged P1 projections for tag 3007c59b74:

  | Cell | Projection MAE |
  |---|---|
  | kills Map 1 | 0.10 |
  | kills Maps 1-2 | 0.09 |
  | HS Map 1 | 0.04 |
  | HS Maps 1-2 | 0.05 |
  | kills Maps 1-3 | 0.54 |
  | HS Maps 1-3 | 0.21 |

  At book lines it is 1.3 pts mean absolute chance difference against the exact logged chance, and the same side on 92%.
- **Not reproducible offline:** the BO3.gg fallback for players with no N0 history. Those rows are scored with perf-2's
  prior, and the "has history" columns below exclude them.
- **Second reference:** perf-2 on the full (non-N0) history, as `full_hist_perf2` in the JSON.

## All historical outcomes (log score per row, pd-1 minus live; positive = pd-1 better)
| cell | dev rows / matches | dev Δlog [95% CI] | dev MAE live → pd-1 | final rows | final Δlog [95% CI] | final MAE |
|---|---|---|---|---|---|---|
| kills Map 1 | 102,787 / 10,278 | +0.046 [0.042, 0.050] | 4.24 → 4.15 | 12,911 | +0.036 [0.025, 0.049] | 4.24 → 4.18 |
| kills Maps 1-2 | 93,384 / 9,351 | +0.024 [0.021, 0.028] | 6.18 → 6.10 | 11,059 | +0.020 [0.011, 0.028] | 6.10 → 6.07 |
| kills Maps 1-3 | 37,852 / 3,791 | +0.063 [0.056, 0.070] | 7.76 → 7.48 | 4,690 | +0.033 [0.020, 0.048] | 7.51 → 7.43 |
| HS Map 1 | 102,747 | +0.036 [0.034, 0.039] | 2.70 → 2.63 | 12,902 | +0.017 [0.012, 0.022] | 2.67 → 2.64 |
| HS Maps 1-2 | 93,315 | +0.011 [0.009, 0.013] | 3.86 → 3.84 | 11,049 | +0.009 [0.004, 0.014] | 3.84 → 3.84 |
| HS Maps 1-3 | 37,796 | +0.082 [0.076, 0.088] | 5.09 → 4.76 | 4,683 | +0.031 [0.021, 0.041] | 4.79 → 4.72 |
| **pooled** | 467,881 | **+0.037 [0.035, 0.039]** | | 57,294 | **+0.023 [0.018, 0.028]** | |

- **Rows where live has HLTV history.** Every cell's interval also stays above 0 in both windows. For example, kills Map 1
  in the final window is +0.028 [0.018, 0.038].
- **PIT.** pd-1's PIT is flat, with every decile between 0.084 and 0.113. Live is under-dispersed in its upper tail on
  Maps 1-3 (top decile 0.145 in dev, from a -1.9 kills bias).
- **Calibration of P(X > t)** at seven half-integer thresholds around the projection: pd-1 is within 2 pts overall and on
  both sides in every cell. Live is off by 3 to 5 pts on one side for kills Maps 1-2 and for Maps 1-3.

## Settled book lines (graded standard PP/UD props, pushes excluded, final untouched window)
**Exact live chance.** 5,190 P1 board units, matched by line id:

| | graded (≥55%) | graded hit [95% CI] | stated − hit: all / Over / Under | log loss | Over share |
|---|---|---|---|---|---|
| live (logged chance) | 2,299 | 53.7% [51.0, 56.4] | +1.86 / +2.55 / +1.31 | 0.6962 | 44.8% |
| pd-1 | 2,291 (99.7% of live) | 55.5% [52.8, 58.1] | +2.06 / +1.84 / +2.21 | 0.6930 | 39.7% |

pd-1 minus live log loss: −0.0032 [−0.0076, +0.0010]. This is better, but the interval includes 0.

**All 11,717 settled props, against the reconstruction:**

| | graded | graded hit | stated − hit: all / Over / Under | log loss |
|---|---|---|---|---|
| live reconstruction | 5,023 | 54.9% | +1.51 / +2.01 / +1.12 | 0.6928 |
| pd-1 | 5,309 | 55.3% | +2.12 / +1.62 / +2.42 | 0.6919 |

By cell, pd-1 most improves Map 1. On kills Map 1, graded hit goes from 42.3% to 57.8% and the calibration gap from +8.0 to
−0.1. On HS Map 1, graded hit goes from 48.3% to 55.1%. On kills Maps 1-2 it is level or slightly worse: log loss 0.6940 →
0.6955 and gap +2.1 → +3.4.

## Verdict against the pass rule
| criterion | result |
|---|---|
| all-outcome log score better, CI excludes 0 | **PASS** (pooled and every cell, dev and final) |
| line calibration within 2.5 pts overall / Over / Under (all decided props) | **PASS** (+2.06 / +1.84 / +2.21) |
| graded count ≥ 90% of live | **PASS** (2,291 vs 2,299 = 99.7%) |
| graded hit not worse | **PASS** (55.5% vs 53.7%) |

## Caveats
- **Graded props only (≥55%).** pd-1 overstates by +3.5 pts overall: +5.0 on Over and +2.7 on Under. Live overstates by
  +5.4 overall: +6.7 on Over and +4.3 on Under. pd-1 is better, but still outside 2.5 pts on graded picks.
- **More Unders.** The Over share of picked sides falls from 44.8% to 39.7% (graded: 775 Over and 1,516 Under, against live's
  1,013 and 1,286). The owner has warned that Unders carry correlated upset risk. Check upset days before any promotion.
- **The line-level gains are not proven.** The book-line log loss and graded-hit gains have intervals that include 0. The
  line set covers one month (Oct 11 – Nov 4 for P1).
- **Approximate historical inputs.** Elo stands in for HLTV ranking history, which starts 2026-10-11 in the extract. Map
  picks come from decayed team pick counts, not vm-2, which has only 30 matches. The stand-in flag is a proxy. Rest is
  measured in whole days, with no travel.
- **Reproduce:**
  ```
  cd research/proj_cs2_full_2026_11_05
  python features.py
  python baseline.py
  python evaluate.py fit
  python evaluate.py score
  python evaluate.py lines
  ```
  The `fit` step takes about 50 minutes on 16 threads.
