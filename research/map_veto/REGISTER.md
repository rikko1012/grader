# Map-veto upgrade - pre-registration (veto-1)

Registered 2026-10-23 BEFORE any model fitting or scoring. Only coverage counts (COVERAGE.md) have been computed.
Nothing here changes live grading: map predictions are display context today (MODEL_VERSIONS 405653024c) and this
registration only decides whether the candidate may replace the display simulator (hltv.predict_maps).

## Data
- Source: the read-only 2-year HLTV export (`map_vetoes` = the scraper's parse of the stored match pages; a 360-page
  stratified re-parse with hltv._VETO/_LEFT matched every step - check_parse.py). Matches dated 2024-10-01 .. 2026-10-22.
- Evaluation units: `complete` vetoes only (7 steps, 7 distinct maps, an event-used template), BO1 / BO3 / BO5.
  `inferred` (leftover reconstructed) and `partial` records feed history only; `anomalous` records are excluded.
- Pool per match = the 7 maps of its own complete veto (the event's active pool, public before the veto); every model
  gets the same pool. Retired maps can never get probability (the pool is an input; history on a retired map only
  affects nothing but that map).

## Periods (chronological; every forecast uses only matches dated strictly before its own date)
| period | dates | role |
|---|---|---|
| burn-in | 2024-10-01 .. 2024-12-31 | history only, never scored |
| D1 | 2025-01-01 .. 2025-06-30 | tuning |
| D2 | 2025-07-01 .. 2025-12-31 | tuning |
| D3 | 2026-01-01 .. 2026-04-30 | tuning |
| D4 | 2026-05-01 .. 2026-08-22 | development confirmation (factor admission, final dev check) |
| FINAL | 2026-08-23 .. 2026-10-22 | untouched: scored ONCE with the frozen candidate |
| prospective | 2026-10-23 on | logged forward by the serving phase |

All settings (smoothing, recency, calibration, factor switches) are chosen on D1-D3 and checked on D4. FINAL is not
read for anything (no metric, no plot) until the candidate's version id + params sha are committed.

## Models compared (identical information: pre-veto, same pool, same date cutoff)
- **M0 uniform-legal**: every step uniform over the remaining legal maps.
- **M1 simple recent**: each team's last 10 vetoes (any format): counts of its removals and of its picks by map; a
  step chooses among remaining maps with weight count+1 (removal counts for removals, pick counts for picks);
  template = the format's earlier mix (+0.5), first actor 0.5. Settings fixed here, not tuned.
- **M2 frozen simulator**: hltv._predict_maps exactly as live (4000 runs, seed 7, both orders, 50% model + 50% flat),
  pool_keys = the match pool. Its database history query is emulated on the same export with its own semantics
  (team matched by normalised name; the 50 latest dated matches before the date; 7-step vetoes; up to 25). BO3 only;
  returns nothing when either team has < 3 vetoes. Produces Map 1 and Map 2 only. For the ordered (Map 1, Map 2) pair
  it is extended as p(a, b) = p1(a) * p2(b) / (1 - p2(a)), renormalised over a != b (its own two marginals, nothing
  more). It produces no decider / set / inclusion forecast.
- **M3 candidate veto-1** (veto_model.py): sequential choice model, one step at a time over the remaining legal maps:
  `p = (1-eps) softmax(u) + eps/|R|`, `u_T,k(m) = beta_k log rate_T,k(m)` with kinds ban1 (team's first removal),
  banN (later removals), pick; `rate = (C + k_team g)/(E + k_team)` with C = recency-weighted choices, E = recency-
  weighted opportunities (map available when the team acted), g = pool-wide rate shrunk to the kind mean with k_glob.
  Unknown order = explicit mixture over (event template x first actor): template prior from earlier vetoes at the same
  event (+ tmpl_alpha x the format's recent mix), first actor 0.5 unless a first-actor factor is admitted. Exact
  enumeration (dynamic programme over remaining maps), so all outputs are legal-sequence distributions.

## Tuning (D1-D3)
Objective: mean per-match negative log-likelihood of the observed full veto (steps; the order term is separable and
tuned on its own likelihood). Coordinate descent, two passes, in this order and over these grids:
half_life_days {60,120,240,480,none}; k_team {1,2,4,8,16}; k_glob {5,20,80}; glob_half_life_days {60,120,365};
max_records {20,40,60}; beta_ban1/beta_banN/beta_pick {0.6,0.8,1.0,1.2,1.4,1.6}; eps {0,0.02,0.05,0.1,0.2};
tmpl_alpha {0.25,1,4} (order likelihood).

## Candidate factors (start OFF; an "additional factor" is never an automatic bonus)
| id | factor | parameter grid |
|---|---|---|
| F1 | roster continuity: past veto weight = (shared current players / 5) ^ rho, current lineup = the team's previous match | rho {0.5,1,2} |
| F2 | team identity: also use vetoes of another team id fielding >= 3 current players | on/off |
| F3 | opponent terms: removals += gamma log(opponent pick rate); picks += gamma log(opponent removal rate) | gamma_k {-0.5,-0.25,0.25,0.5} |
| F4 | map strength: opponent-adjusted map win residual vs Elo expectation, shrunk (k=6); picks +delta diff, removals -delta diff | delta_k {0.5,1,2,4} |
| F5 | BO1 removals as their own kinds | on/off |
| F6 | first actor: logistic on pre-match Elo difference | first_elo {0.05,0.1,0.2,0.4} |
| F7 | first actor: HLTV listed order (only where the listing is known; an API input) | first_listed {+-0.25,+-0.5,+-1} |

Admission: tuned on D1-D3 against the tuned base; ADMITTED only if (a) it improves the D1-D3 objective and (b) on D4
the match-grouped bootstrap 95% interval of the per-match log-likelihood gain (factor on - off) is entirely > 0
(F6/F7: gain on the first-actor log-likelihood). Admitted factors are then checked jointly on D4 (the joint model must
keep an interval > 0 vs the base); if not, only the single best admitted factor stays.

## Metrics (every model, every period it covers)
Ordered map probabilities (Map 1, Map 2, decider for BO3; the map for BO1; Map 1..4 + decider for BO5), map
inclusion (7 binary outcomes per match: the map is in the veto's selected list), the unordered map set, the ordered
played sequence, and for BO3 the ordered (Map 1, Map 2) pair. Reported: log-loss (natural log, floor 1e-9), Brier
(multi-class sum), calibration (10-bin reliability tables + ECE), top-choice accuracy, coverage (share of evaluation
matches with a forecast; full-coverage tables give a model with no forecast the uniform-legal forecast).
Intervals: match-grouped bootstrap, 2000 resamples, seed 20261023, 95% percentile; distinct match counts always shown.
Secondary (not part of the rule): live updates - after the first 2 and first 4 confirmed actions, M3 vs M0/M1 both
conditioned on the same actions (M2 cannot condition and is not given them).

## Decision rule (predeclared)
The candidate **beats the frozen simulator** iff, on FINAL, BO3 matches where M2 produces a forecast, the 95% interval
of mean(LL_M2 - LL_M3) is entirely > 0 for ALL THREE of Map 1, Map 2 and the ordered (Map 1, Map 2) pair.
Otherwise the frozen simulator stays and the candidate stays research. Passing qualifies the candidate for the
display only; any use in grading needs its own registration. Secondary comparisons (full coverage with uniform
fallback, M1/M0 on sets and inclusion for all formats, calibration) are reported without changing the rule.
