# vm-2 (HLTV map chances) - pre-registration

Registered 2026-11-04 (~18:40 UTC) BEFORE any vm-2 entry exists and BEFORE any result is computed. The hltv-scraper started
writing HLTV's "Maps -> Simulated" tables on 2026-11-04 ~18:13 UTC (scraper commit d5d583c). At registration the
production tables held 18 upcoming matches (126 map-chance rows, 54 simulated vetoes, 378 veto steps, 19 status rows,
1 with found = false) and **no finished match**; no vm-2 entry has been logged (the code is not deployed). Only the schema,
row counts, the per-match chance sums (298-301% for the 18 BO3s) and one example match were looked at, through hltv.py's
read path (read-only; hltv._connect). No outcome of any kind has been seen. Nothing here changes a live grade, chance, stake or
projection; the grader is untouched and `use_predicted_maps` stays False.

Owner rules that apply: a factor moves grades only after a walk-forward / chronological test passes, until then it is
shown at weight 0 ([grading-factor-rule]); the predicted-map input was switched off on 2026-10-01 (top Map 1 pick right
17/106; grader.py `use_predicted_maps`); vm-1 (research/map_veto/props, AM-1) already logs A1 vs C0 from veto-1.
Calibration and selection are judged separately; CIs, not point estimates; forward evidence stays forward.

## 0. Data (exact tables, read-only; keyed by HLTV matchid = the hltv:<id> canonical match id)
- `map_chances (matchid int, map varchar, chance smallint %, position smallint, updated_at timestamptz)` PK (matchid, map):
  the scraper's LATEST read of each match (rows replaced on every read until the start, kept afterwards).
- `simulated_vetoes (matchid, rank smallint 1..3, maps text[] played order, chance smallint % NULL-able, updated_at)`
  PK (matchid, rank); `simulated_veto_steps (matchid, rank, step 1..7, team varchar NULL = left-over map, action
  removed|picked|leftover, map, chance smallint %)` PK (matchid, rank, step).
- `map_chances_status (matchid, checked_at, found bool, last_error text)` PK (matchid).
- `map_chances_history (matchid, map, chance, seen_at)` - line-movement display only; **never read by vm-2**.
- Player kills per map: `map_player_stats (k, sideid 0 = total)` joined to `match_maps (matchid, mapstatsid, mapid)`,
  `maps (mapid, name)` and `match_overview (date)`. Map names follow `maps.name` ("Dust 2" with a space); keys are
  hltv.map_key (lower-case alphanumerics: "dust2").

## 1. The candidate vm-2 (frozen: `vm-2-37ec7435b0` = sha of vm2_params.json without "meta"; vm2.py's own sha is logged
with every entry as `code_sha`)
For one CS2 forecast (player P, prop kills | HS, scope Map 1 / Maps 1-2 / Maps 1-3, offered line L, live projection from
perf-2 / perf-2-bo3 / perf-cm2):
1. **Snapshot** (hltv_mapchances.map_chances, on hltv.py's read path): the match's map_chances rows, used only when every
   row was updated BEFORE the start,
   where start = the earlier of the forecast's match start and HLTV's listed start (upcoming_matches.time). chance_m = the
   integer % of map m. Format k = HLTV's listed format (bo1 / bo3 / bo5 -> 1 / 3 / 5), else round(sum / 100); the chances
   must add up to 100 x k within +-4 points.
2. **a_m** (hltv_mapchances.player_map_kills): P's kills per map on map m over maps of matches dated in [match day - 180 days, match
   day) (match day = the UTC date of that start; nothing from the match itself or later). A map with fewer than 5 such maps
   uses his overall per-map average over the same window (a0 = all his kills / all his maps). Fewer than 10 maps in all ->
   ineligible.
3. **Projection** (kills): Maps 1-2 / Maps 1-3 = sum_m(chance_m x a_m) / sum_m(chance_m) x maps in the prop (2 / 3), over
   every map HLTV lists. Map 1 = a_m of the likeliest simulated veto's Map 1 (rank 1; the first step in step order whose
   action is picked or leftover - BO3 / BO5 the first pick, BO1 the left-over map) x 1; its step chance and the veto's
   chance are logged.
4. **Control C0-pooled**: a0 x maps in the prop (the same arithmetic with no map weighting) - same units, same inputs.
5. **Chance** (vm-1 / rm-1 arithmetic, cands_shadow): kills = one normal at the projection, sd = c x sqrt(max(projection,
   1)), c = the live sd / sqrt(max(live lambda, 1)); the live cut rule; P(Over | no push); capped to 22-78%. HS = the kills
   pmf at the kills projection with the live kills spread (perf.kills_c), beta-binomial HS (kappa 100) at the live HS per
   kill; P(Over | no push); uncapped (as live).
6. **Eligibility** (both arms together - identical units by construction): a vm-2 cell; the live projection is
   perf-2 / perf-2-bo3 / perf-cm2 with lambda and sd (HS: lambda_kills and HS per kill); a whole / half line; an HLTV match
   id; a snapshot captured before the start; chances adding up to the format; scope not longer than the format; for Map 1 a
   likeliest veto captured before the start; an HLTV player id; >= 10 window maps. Every other case is logged INELIGIBLE with
   its reason, including a failed database read ("HLTV database unreachable", `transient: true`) - a failure never becomes
   a number, is never retried for the same row, and never touches the live row.

## 2. Logging (the shadow)
Every CS2 forecast row the pick-log writer appends carries `forecasts.shadow["vm2"]` (picklog._shadow), computed in the
writer thread from two cached reads done before the pick-log lock is taken (vm2.prefetch): version, params_sha, code_sha,
match_id, eligible / reason (+ transient), snapshot {captured_at, start_used, read_at, age_min, lead_min,
logged_before_start, format, sum, status_found, chances}, player {id, since, before, n, avg, per_map {n, avg, fallback}},
map1 (Map 1), map_factor (per-map vm-2 / a0), live_replay, live_p_more and the two arms `vm-2` and `C0-pooled`
{p_over, p_over_uncapped, raw over / under / push, lam, lam_kills, per_map_kills, side, delta_p}. Shadow only: never shown,
never a grade / chance / stake / projection input.

## 3. Forward test (the only route to any grade effect)
**Units.** P1 board units (EVALUATION.md section 8): player x canonical match x stat x scope; its forecast = the latest
BOARD forecast logged >= 30 min before the start on a STANDARD platform line; settled; a push on the board line is
excluded and counted. Forward = matches starting at or after the first production forecast row carrying shadow.vm2. Each
unit uses the shadow.vm2 entry stored on THAT forecast row (computed when the row was appended, before the start; never
recomputed). Eligible units = `shadow.vm2.eligible` is true; every other unit is counted by reason and stays out of every
comparison. Cells: {kills, HS} x {Map 1, Maps 1-2, Maps 1-3} (six).

**Computed per cell, on the eligible units** (y = 1 when the settled total is over the line):
- p_v = shadow.vm2["vm-2"].p_over; p_c = shadow.vm2["C0-pooled"].p_over; p_l = forecasts.p_more of the same row (the live
  P(Over | no push)). LL(p) = -(y ln p + (1 - y) ln(1 - p)), p clipped to [1e-6, 1 - 1e-6]; Br(p) = (p - y)^2.
- Calibration / probability quality:
  - C1 map effect: G_C = mean(LL(p_c) - LL(p_v)) (> 0 = map weighting helps);
  - C2 vs live: G_L = mean(LL(p_l) - LL(p_v));
  - C3 Brier vs live: D_B = mean(Br(p_v) - Br(p_l));
  - C4 favoured-side calibration, Over-favoured and Under-favoured units separately: gap(arm) = |mean favoured chance -
    favoured hit rate|, dgap = gap(vm-2) - gap(live).
- Selection (favoured side = Over when p > 0.5, Under when p < 0.5; units where either compared arm sits at exactly 0.5 are
  counted and left out of that comparison):
  - S1 vs live: H_L = mean(hit(side_v) - hit(side_l));
  - S2 vs C0: H_C = mean(hit(side_v) - hit(side_c)), hit = 1 when the side's outcome happened;
  - S3 side balance (reported): share of Over sides for vm-2, C0 and live.
- Reported, not decisive: projection bias mean(actual - lam) and MAE for vm-2, C0 and live (forecasts.lambda); eligible
  coverage (eligible / all forward P1 units) with every ineligible reason; flips (side_v != side_l) and their hit rates.
- Intervals: match-clustered bootstrap - canonical matches resampled with replacement, 10,000 resamples, seed 20261104,
  percentile intervals; distinct matches shown with every number.

**Look schedule.** One look per cell, when the cell has **300 distinct settled forward matches** with eligible units (the
minimum). Below 300 there is no decision (descriptive reporting only, clearly marked); a cell short of 300 on 2027-04-30
gets one descriptive report and stays shadow. No early stopping, no re-looks, no other window.

**Verdicts per cell (one-sided family-wise alpha 0.05 over six cells -> 0.00833 per cell for the decisive bounds).**
- CALIBRATION PASS = lower 99.167% bound of G_C > 0 AND lower 99.167% bound of G_L > 0 AND upper 95% bound of D_B < +0.0025
  AND, for Over-favoured and Under-favoured units each, dgap <= +0.01 (point) with upper 95% bound < +0.03.
- SELECTION PASS = lower 99.167% bound of H_L > 0 AND lower 99.167% bound of H_C > 0.
- The two verdicts are computed, judged and reported separately; neither compensates for the other. A cell is decidable
  only when vm-2 is eligible on >= 90% of its forward P1 units.
- **Promotion needs the owner's approval.** Only a cell with BOTH verdicts PASS (and >= 90% coverage) may be put to the
  owner, with the frozen version (vm-2-37ec7435b0 and the logged code_sha). Nothing is activated by this rule: activation is
  a separate, owner-approved, versioned release. Any other outcome keeps vm-2 in shadow; a failure is reported as a failure.
- vm-2 is never evaluated on matches already seen as development data for itself; any change to vm2_params.json or
  vm2.py is a new version with its own registration and its own forward clock.

## 4. Separate prospective check (diagnostics only, not part of any rule)
mapchances.py, every 15 minutes in the veto-serve background thread: for every match with map chances, the last snapshot
captured before HLTV's listed start is kept (map_chance_check.db); after the match (3 h after the start, up to 10 days),
the maps actually played (`match_maps` rows with a mapstatsid, in number order) are compared with it: hit rate per match
= played maps among HLTV's k likeliest (k = maps in the format) / maps played; also the n likeliest vs the n played and
whether the likeliest veto's Map 1 was Map 1. Shown in /admin/diagnostics(.json) with a match-level bootstrap 95% CI. A
BO3 that ends 2-0 has no decider in match_maps, so its hit rate counts the two played maps only.

## 5. Display (weight 0)
The classic CS2 card (map / veto section) and the research hub card show "HLTV map chances" (each map's %, the likeliest
veto's maps with its share of HLTV's simulations; the classic card also lists its steps) with "Weight in the grade: 0.
The grade does not use HLTV map chances yet." A match without a projection, without a simulation (status found = false) or with a failed read shows no block.
