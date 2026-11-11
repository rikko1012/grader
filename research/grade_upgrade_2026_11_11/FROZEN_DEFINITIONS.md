# CS2 grade-upgrade study - frozen definitions (2026-11-11)

Written and committed BEFORE any hit rate, residual or cohort outcome of this study was computed. Only coverage counts
(build.py: rows, joins, flag vocabulary, field ranges) were looked at. Nothing here is changed after scoring; anything
found afterwards is labelled exploratory.

Owner request (2026-11-11): full grading-logic upgrade - test new factors on previous projections versus how they ended,
to see what improves grades. Research only: no live grade, chance, stake or flag changes.

## 0. What is already settled (not re-run here; cited in the report)

| factor | last test | model it was tested on | status |
|---|---|---|---|
| map pool / veto-weighted map exposure | 2026-11-09 (cs2_maps_accuracy), oracle maps + proxy, real lines | live (perf-2 N0 Map 1, cm-2 Maps 1-2) | no gain, even with the played maps known |
| economy / pistol / round-end / score-state / CT-T side / trades / tier-of-opponent kill rate | r7, 2026-11-07 | pd-1 (not live) | no gain on pd-1 |
| line discrepancy, bumps, agreement letters | 2026-11-09 (line_gap_cs2) | live sides | no pass (best-line display only) |
| recent-level correction (global) | 2026-11-09 | live | fails, pushes Over share up |
| logged shadows cm-1, ch-009, fh-1, hs-1, rm-1 at real lines | 2026-11-09 (esports_hit) | - | none beats live; pl-1 / pl-2 / lc-1 use the line |
| team kill share, last-10 form, h2h, opponent pace, blowout, stage, opp roster, same-day | FACTOR_AUDIT round 3 (2026-10-10) | perf-2 G8.3 (OLD: before N0 pin, cm-2, hs-cm2), MAE only, never at real lines | rejected on the old version -> RE-TESTED here on the live reconstruction at real lines |
| Elo strength, schedule load, eco/rating, IGL, AWP proxy | rounds 1-2 (Oct 8-9) | perf-1 / perf-2 (OLD), MAE only | rejected / shadow on the old version -> RE-TESTED here |
| stake gates (FORM FIGHT, AWPer-HS, TAPE FIGHT, rust 15-28 d) | sr-gates-1, 2026-10-28 | split halves of Oct 11-28 | none passed; AWPer-HS lift named as the forward re-test -> G6 here |

## 1. Data (all local, read-only; no production call is needed to score)

- Rows: `cs2_scout_ops/data/line_gap_1109/rows.json` - 10,839 app rows / 6,782 units / 389 matches, starts
  2026-10-11 .. 2026-11-08. One row per (unit, app): that app's latest BOARD forecast >= 30 min before the start on a
  standard line; outcome = settled total vs that row's own line. Pushes, voids and re-keyed duplicates are already
  removed (P1 rule; the 8 double-keyed units are counted once).
- Context saved with the forecast's pick row (picks export of 2026-11-09): series win chance `p_win` and market quality
  (HARD / SOFT / NONE), `sample_maps`, flags, last-results average (`rec_avg`), projection `lambda`.
  Caveat (stated in the report): the pick row holds its LAST pre-start regrade, which can be later than the P1 forecast
  time; it is still pre-match.
- Projection `lam`: the P1 unit's saved projection for the P1-book row, the pick row's `lambda` for the other app.
- HLTV pre-match features: `cs2_full/proj_full/series_features.pkl` (pd-1 feature table, every value computed from
  matches strictly before the match day; extract ends 2026-11-05), joined by HLTV match id + HLTV player id (exact id
  pairs only, no name matching). Board rows after 2026-11-05 have no features: for H-family tests they keep the live side.
  `bo` / `maps_played` from that table are NOT used (possible leak); series format comes from the line's own scope.
- HLTV history for fitting (H family): `cs2_full/proj_full/baseline.pkl` - live-formula reconstructions per
  player-series (cm-2 Maps 1-2 kills `cm2_lam12`, HS = `cm2_lam12 x cm2_hsr`), 2025-11-01 .. 2026-11-05, with the
  actual `k12` / `hs12` from series_features.
- Roles: `hltv/player_roles.csv.gz` is a CURRENT snapshot (2026-11-05) applied to history = proxy only.

Weeks by start date (UTC): W0 Oct 11-18 (fit only), W1 Oct 19-25, W2 Oct 26-Nov 1, W3 Nov 2-8. Test set = W1-W3 pooled.
Every parameter used in week Wk is fitted on data dated before Wk's first day (expanding window).

Live baseline per row: side = Over if p > 0.5, Under if p < 0.5, none if p = 0.5 (not graded). Staked = units > 0.

## 2. The owner's acceptance rule, as scored

**Rule S (anything that can change a side: families H, B):** paired with live on the same test rows,
1. hit rate (side vs outcome at the row's real line) improves and the match-grouped bootstrap 95% CI of the paired
   difference has its lower bound > 0 (2,000 resamples, seed 7);
2. Over-pick hit not lower than live's Over-pick hit, Under-pick hit not lower than live's Under-pick hit;
3. graded count not lower;
4. Over share not more than 1.0 point further from 50% than live's;
5. (multiple-comparison guard) the point estimate is >= 0 in at least 2 of the 3 test weeks.

**Rule G (grade layer: sides unchanged, so the all-lines hit rate cannot move by construction; the grade layer decides
which picks carry a letter with a stake):** on the test rows,
1. hit rate of the candidate's staked picks minus hit rate of live's staked picks: match-grouped bootstrap 95% CI lower
   bound > 0;
2. staked Over hit and staked Under hit each not lower than live's;
3. staked count not lower than live's;
4. staked Over share not more than 1.0 point further from 50% than live's;
5. point estimate >= 0 in at least 2 of the 3 test weeks.
The printed chance is never changed by any candidate (no shrink toward 50%).

Calibration gap, log loss and MAE are reported where cheap, never used to pass or fail.
About 40 tests are run: a single pass at the 2.5% one-sided level is expected by chance. A pass is therefore a CANDIDATE
(flag, default off, forward confirmation), never an automatic change.

## 3. Family H - pre-match HLTV factors as a projection multiplier (the prop line is not an input)

For each factor F, stat s in {kills, HS} and test week Wk:
- Fit rows: HLTV history rows with day < first day of Wk. Target e = actual / projection - 1, clipped to [-1, 2]
  (kills: k12 / cm2_lam12; HS: hs12 / (cm2_lam12 x cm2_hsr)).
- z = (x_F - mean) / sd with the fit rows' mean and sd, clipped to [-3, 3]; missing x -> z = 0.
- OLS e = a + b z. Only b z is applied (the level a is NOT applied: the global level correction already failed).
- Board: lam' = lam x (1 + b z) for every scope of that stat. Candidate side = the live side, flipped only when lam and
  lam' sit on opposite sides of the row's line. lam' exactly on the line, lam missing, or no side live -> live side kept
  (so the graded count can never fall).

Single factors (x_F):
| id | factor (owner list item) | x_F |
|---|---|---|
| H01 | opponent strength | elo_diff (own Elo - opponent Elo, pre-match) |
| H02 | similar-ranked opponent | -abs(elo_diff) |
| H03 | match tier | elo_mean |
| H04 | event class | ev_qual, ev_academy, ev_major (3 indicators, one joint OLS) |
| H05 | LAN vs online (residual beyond the live split) | lan |
| H06 | recent form, 20-map window | kpr_20 / kpr_365 - 1 (0 when maps_20 < 3) |
| H07 | recent form, 90-day window | kpr_90 / kpr_365 - 1 |
| H08 | time on team | log1p(maps_with_team_60) |
| H09 | own roster change / stand-in | 1 if players_new_60d > 0 or lineup_changes_vs_prev > 0 |
| H10 | opponent roster change / stand-in | 1 if opp_players_new_60d > 0 |
| H11 | time since the player's last match | log1p(min(days_since, 60)) |
| H12 | long break | 1 if days_since >= 15 |
| H13 | team rest / travel proxy | log1p(min(rest_days_pre, 30)); opponent the same (2 features, joint) |
| H14 | opponent defensive profile (kills allowed per round, residual) | o_conc_kpr |
| H15 | expected closeness from history (rounds proxy) | -abs(t_rshare - o_rshare) |
| H16 | overtime propensity (rounds proxy) | t_ot + o_ot |
| H17 | role: star vs support | kshare_90 |
| H18 | role: entry | okpr_90 (opening kills per round) |
| H19 | role: AWPer (snapshot proxy) | is_awper |
| H20 | role: IGL (snapshot proxy) | is_igl |
| H21 | history size | log1p(r_365) |
| H22 | head-to-head | his kills per round vs this opponent team in the prior 365 days / kpr_365 - 1, shrunk 300 rounds: ((k_h + 300 kpr_365) / (r_h + 300)) / kpr_365 - 1; 0 without a meeting |
| H23 | ALL of the above together | ridge on every feature above; strength picked from {0, 10, 100, 1000, 10000} x 1 on the last 60 days of the fit rows (fitted on the earlier fit rows), then refitted on all fit rows |

H rows are scored on the test rows; rows without features keep the live side (reported: how many rows had features).

## 4. Family B - forecast-time context cohorts as a projection multiplier (board-fitted)

For factors that exist only on the forecast's pick row (not in HLTV history). For factor F with FIXED cohorts, stat s,
week Wk: fit rows = P1-book rows (one per unit) of earlier weeks with lam > 0; e = actual / lam - 1 clipped [-1, 2];
d_c = n_c / (n_c + 200) x (mean_c(e) - mean_all(e)) (cohort minus the whole board of that stat, shrunk 200 units; the
whole-board level is never applied). lam' = lam x (1 + d_c); side rule as in family H.

| id | factor | cohorts |
|---|---|---|
| B01 | market closeness (expected rounds proxy; match-winner odds logged with the forecast) | market NONE; abs(p_win - 0.5) < 0.10; 0.10-0.25; >= 0.25 |
| B02 | favourite / underdog | market NONE; p_win < 0.35; 0.35-0.50; 0.50-0.65; >= 0.65 |
| B03 | market quality | HARD / SOFT / NONE |
| B04 | series format / scope | Map 1; Maps 1-2; Maps 1-3 |
| B05 | app | Underdog / PrizePicks |
| B06 | sample size | sample_maps < 20; 20-49; >= 50 |
| B07 | rust flag | flag text contains "RUST" or "Rust" / not |
| B08 | last-results tape vs projection | rec_avg / lam - 1: < -0.10; -0.10..+0.10; > +0.10; rec_avg missing |
| B09 | hot-form flag | flag "Hot form" / not |
| B10 | role unconfirmed flag | flag "Role unconfirmed" / not |
| B11 | data state | flag "HLTV STALE" or "PROVISIONAL" or "OLD PROJECTION" or "Thin data" / none |
| B12 | closeness x side of strength | B02 cohorts x stat fitted jointly with B04 scope (product cohorts, same shrink) |

## 5. Family G - the grade layer (sides and printed chances unchanged)

p_side = max(p, 1 - p). live_gated = p_side >= 0.55 and units = 0 (an existing 0U rule holds the pick); existing gates
stay in force for every candidate except G6. Parameters are picked on the earlier weeks (expanding) to maximise the
staked hit rate there, subject to: staked count >= live's staked count on those weeks AND staked Over share not more
than 1 point further from 50% than live's on those weeks. If no grid value satisfies that, the live rule is kept.

| id | grade-layer rule | staked when | grid |
|---|---|---|---|
| G1 | letter threshold per stat x side | p_side >= t[stat, side] and not live_gated | each t in {0.52 .. 0.60 step 0.01} (4 cells) |
| G2 | Over-vs-Under asymmetry | Over: p_side >= 0.55 + d; Under: p_side >= 0.55 - d; not live_gated | d in {-0.03, -0.02, -0.01, 0, +0.01, +0.02, +0.03} |
| G3 | chance band (a top cap: the B+ / A- bands hit worst on Oct 28) | lo <= p_side < hi and not live_gated | lo in {0.52, 0.53, 0.54, 0.55}; hi in {0.60, 0.63, 0.68, none} |
| G4 | distance-from-line bucket | a <= abs(lam - line) / sqrt(lam) < b and not live_gated | a in {0.05, 0.10, 0.15, 0.20, 0.25, 0.30}; b in {0.5, 0.7, 1.0, none} |
| G5 | sample-size stake rule | sample_maps >= m and p_side >= t and not live_gated | m in {5, 10, 20, 30}; t in {0.52, 0.53, 0.54, 0.55} |
| G6 | lift the AWPer-on-HS 0U gate (sr-gates-1's named forward re-test) | live staked, plus HS rows with an "AWP" flag, p_side >= 0.55 and units = 0 | none; scored on starts after 2026-10-28 13:40 UTC only (forward of sr-gates-1); weeks rule 5 uses W2 and W3 + the Oct 28-Nov 1 part |
| G7 | per-scope threshold | p_side >= t[scope] and not live_gated | t in {0.52 .. 0.60, never} for Map 1, Maps 1-2, Maps 1-3 |
| G8 | learned selection | logistic regression (L2, C = 0.1) of win on p_side, stat, side, scope, app, market quality, abs(p_win - 0.5), log sample_maps, standardised distance; staked = score >= the cut that gives live's staked share on the fit weeks; not live_gated | none |

Reported for every G rule besides Rule G: all-lines hit (unchanged), staked count, staked Over share, stated-minus-hit.

## 6. Descriptive tables (never used to choose anything)

Live hit by stat x scope x side, by chance band, by distance bucket, by each B cohort - printed AFTER the tests, in the
report's appendix, labelled descriptive.

## 7. If something passes

It is implemented in a NEW module behind an env flag, default off (roles.py, cs2_scout.py, bo3.py, hltv.py untouched, so
MODEL_VERSION does not change), with tests; a board-wide before/after preview on the current live board is produced; a
fresh holdout (P1 units starting 2026-11-09 or later, pulled paged / read-only after scoring) is reported as
confirmation when production answers. No merge, no deploy. If nothing passes, the report says so with the full table.
