# Calibration study cal-cs2lol-1: REGISTER (frozen before any test run)

Written 2026-11-05 05:40 UTC, committed before the diagnosis and before any candidate is fitted or scored. Nothing in
this file is changed after that commit; any later change is a dated amendment below the line at the end, and an
amendment can never change the selection rule or the pass rule for this run.

## Why

The owner's Nov 4 audit of the live Results page (reproduced exactly from the pulled data, see "Data"):

| cohort | n | stated | hit |
|---|---|---|---|
| CS2 Results (favoured side >= 57%, Map 1 held off) | 1,831 | 60.9% | 53.2% |
| CS2 A / B+ | 11 / 294 | 76.4% / 65.0% | 45.5% / 48.6% |
| LoL Results (offered side, all chances) | 1,037 | 60.6% | 53.3% |
| LoL A+ / B | 27 / 237 | 83.7% / 59.8% | 63.0% / 49.8% |

Goal: a calibration candidate, fitted only on earlier data and tested on later data, that would make the stated chance
match results. Shadow only. No live grade, CONFIG value or model hash (picklog MODEL_VERSION) changes in this study.

What I knew before writing this: the aggregate table above, the per-letter table of both Results pages, the unit counts
by date, prop, scope and model tag. I had NOT looked at any outcome split by date, side, prop, line distance, source,
or at any residual, and no candidate had been fitted.

## Data (read-only snapshot)

- CS2: `/admin/learning/p1_units.json` (paged), export `at` 2026-11-05T05:21:45Z, 6,317 scored P1 units + 50 pushes,
  file sha256 prefix `7b22802bda29b0ab`. The stated chance is each unit's saved `p` = P(Over) of its own P1 forecast
  (latest board forecast on a standard line >= 30 min before the start). Outcome `over` (pushes excluded by the export).
  `/admin/forecasts_page.json` (paged windows) adds the saved raw chances and source for the diagnosis only; no
  candidate reads it.
- LoL: settled forecasts via `/admin/lol/query.json` (SELECT only, id windows), exactly the Results-page cohort
  (`lol/center/learning.dashboard`: latest forecast before the series start per platform x market x line, latest
  settlement): 1,840 rows, 1,795 graded, file sha256 prefix `09f330eaee7b5650`. Every row is uncalibrated
  (`cal_version` null), so the shown chance is the raw chance. Stated chance for fitting/scoring:
  q = p_over / (p_over + p_under) (P(Over | no push)); outcome `over_hit`; only `graded` rows are scored.

## Candidates (fixed list; each maps the live P(Over) p to p')

All fits are per (sport, prop): CS2 kills / hs (scopes pooled); LoL kills / assists (scopes pooled; any other prop
stays identity). z = Phi^-1(clip(p, 0.001, 0.999)). A group with fewer than 200 training units stays identity (= live)
for my candidates C1-C4 and C6; C5 uses gradecore's own thresholds.

| id | candidate | fit (maximum likelihood on training wins/losses unless stated) |
|---|---|---|
| C0 | live (identity) | - |
| C1 | isotonic | PAV on P(Over), `gradecore.calibrate.fit_isotonic` (min block 30), linear interpolation, flat outside knots, clip 0.01-0.99 |
| C2 | Platt | logit p' = a logit p + b, `gradecore.calibrate.fit_platt` |
| C3 | spread inflation | p' = Phi(z / k), one k per group, k in [0.4, 3.0] |
| C4 | mean-bias correction | p' = Phi(z - d), one d per group, d in [-1.5, 1.5] (a shift of d model-sds toward Under when d > 0) |
| C5 | existing gradecore/calibrate.py | `gradecore.calibrate.build` unchanged: rows {market = prop, side = live favoured side, p = live favoured-side chance, win/loss}; `apply(lookup(doc, prop, side), p_side)`; its MIN_N 300 / ISOTONIC_MIN_N 1000 rules |
| C6 | per-side spread | p_side' = Phi(z_side / k_side) with z_side = Phi^-1(live favoured-side chance), separate k for units the live model favours Over and units it favours Under, per group, k in [0.4, 3.0] |

A candidate may move a unit's favoured side (p' crossing 0.5); that is kept, never forced back.

## Chronological design (rolling origin by date)

- Test day D (UTC calendar day of the match / series start). The model for day D is fitted on every unit whose start is
  before D 00:00 UTC minus 6 hours (so its result was settled before D's forecasts), expanding window, refitted every day.
- Two segments, fixed now:
  - **Development (selection only):** CS2 test days 2026-10-18 .. 2026-10-27; LoL 2026-10-23 .. 2026-10-27.
  - **Confirmation (the registered test):** both sports 2026-10-28 .. 2026-11-04 (last settled day in the snapshot).
    For CS2 this is the live tag 3007c59b74 period.
- Units scored: CS2 all P1 units of the day (every chance, both sides, all scopes incl. Map 1). LoL all graded units of
  the day (every chance, both sides, alternate lines included, as the Results cohort includes them).

## Metrics

- Paired per-unit log loss (p clipped to [0.001, 0.999]) and Brier on P(Over), gain = live minus candidate (+ = better).
- 95% interval: bootstrap that resamples whole matches (CS2 `match`, LoL `sid`), 2,000 draws, seed 20261105, percentile,
  statistic = sum of gains / number of units in the draw.
- Reliability: 5-point bins of the favoured-side chance (50-55, 55-60, ... 80+), stated vs hit, with match-clustered CIs.
- Results cohort: CS2 favoured side >= 57% with Map 1 off (the live member rule); LoL favoured side >= 0% on an offered
  side (the live LoL rule; side offered recomputed for the candidate's side). Stated vs hit, live and candidate.
- Letter distribution before / after on the confirmation units (CS2 `grader.grade_from_p`, LoL published BANDS).
- Balance (A8a, unchanged): |share of units whose favoured side is Over - observed Over rate| <= 0.15.
- Diagnostics are descriptive and cannot change the candidate list, the selection or the pass rule.

## Selection rule (per sport, on the development segment only)

The selected candidate is the one among C1-C6 with the largest pooled development log-loss gain whose pooled
development Brier gain is also > 0. If no candidate has a development log-loss gain > 0 and a Brier gain > 0, nothing is
selected and nothing can pass. Only the selected candidate is judged on the confirmation segment; every other candidate
is reported there as descriptive only and can never be promoted from this run.

## Pass rule (selected candidate, confirmation segment, per sport; ALL must hold)

1. Paired log-loss gain 95% interval lower bound > 0.
2. Paired Brier gain 95% interval lower bound > 0.
3. Balance (A8a) holds for the candidate on the confirmation units.
4. No prop with >= 100 confirmation units has a log-loss gain interval entirely below 0.
5. Stated matches results on the candidate's own Results cohort: the match-clustered 95% interval of (hit rate - mean
   stated chance) contains 0, and |mean stated - hit| <= 0.03 over all confirmation units on the favoured side.

Selection quality (the hit rate of the picks a candidate makes) is reported separately and is not part of this pass
rule (owner, Oct 16: calibration and selection are judged separately).

## If a candidate passes

It may only become a shadow: logged next to each forecast behind a flag that defaults to off, never read by any
member-facing path, never in the model hash, CONFIG or the live grade. Promotion is the owner's decision. Forward
evidence from the shadow stays forward; this run's confirmation segment, once inspected, is development data for any
later change.

---
Amendments (dated, below this line only):
