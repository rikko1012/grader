# AMENDMENT 2026-10-23 to the rm-1 / th-1 forward confirmation (`cands-amend-1`)

**Made 2026-10-23, drafted from 16:54:15 UTC, recorded by the research commit that adds this file (branch
research/cs2-accuracy-2y; `git log -- research/cs2_accuracy_2y/AMENDMENT_2026-10-23.md` gives the exact time) and implemented on
branch close2/contracts (learning.py `CANDS_AMENDMENT`, effective in the production report from deploy
<COORDINATOR: deploy time UTC / main commit>).**

**It was made BEFORE any eligible forward outcome existed or was examined.** Production check (bounded keyset reads of
`/admin/forecasts_page.json`, pages of 500, cutoff id 83425): every forecast logged since the shadow deploy (ids 81456..83425,
logged 2026-10-23 07:58:08 .. 16:50:35 UTC, 1,970 forecasts) carries rm-1 and th-1 entries, and **all 1,970 / 1,970 are
`eligible: false`** for both (1,039 "not a cell (kills 12)", 931 "not a cell (hs 12)"); 0 rm-1-eligible, 0 th-1-eligible and
0 in th-1's wider cell (no kills Map 1 line was offered). Re-checked at 17:00 UTC just before this commit: ids 83426..83453 (28 more,
logged to 16:56:30 UTC) are also all `eligible: false` (Maps 1-2) - **1,998 / 1,998, 0 eligible**. So no eligible forward forecast, and therefore no eligible forward
outcome, exists; none was looked at. The Oct 22 confirmation units described in CR-1 (230 units / 15 matches, signed bias only)
started before the deploy and are outside the forward set by its definition; no rm-1 / th-1 value was computed on their
outcomes. Because nothing had been examined, the amended evaluation applies to the whole forward set from the deploy
(2026-10-23 07:55:42 UTC); no prospective restart is needed.

The original registration (REGISTER.md section "Frozen candidate definitions rm-1 / th-1", commit 6d9bb32, 2026-10-23 07:10
UTC; CHALLENGERS.md rm-1 / th-1 section frozen 07:45 UTC) is kept verbatim; the changed sentences carry an "[Amended
2026-10-23: An]" marker. Nothing frozen changed: cells, constants (sha256 3d00b8c8...), eligibility flags, logged shadow values,
live grading and every historical log are untouched.

## A1. Registered scope vs development coverage (no change; wording corrected)

What the registration says (6d9bb32): the development study evaluates **6 cells per candidate** (kills / HS x Map 1 / Maps 1-2 /
Maps 1-3) and "a shadow is implemented only for cells that pass, live elsewhere"; "nothing is tuned after seeing these numbers".
The deployment scope is therefore exactly the cells that passed the gate (CANDIDATES.md section 0), frozen in CHALLENGERS.md
07:45 UTC and in `cands_shadow.RM1 / TH1["cells"]`:

| candidate | cell | development evaluation (HLTV pooled; real lines) | gate | registered for deployment | implemented (`cands_shadow`) |
|---|---|---|---|---|---|
| rm-1 | kills Map 1 | +0.0423 [+0.0271, +0.0625]; 150 u / 19 m +0.0094 | pass | **yes** | yes (perf-2 / perf-2-bo3 grades) |
| rm-1 | kills Maps 1-2 | +0.0162 [+0.0144, +0.0181]; 1,233 u / 136 m **-0.0002** [-0.0040, +0.0038] | fail G3 | no | no ("not a rm-1 cell") |
| rm-1 | kills Maps 1-3 | +0.0166 [+0.0147, +0.0187]; 20 u / 2 m | pass | **yes** | yes |
| rm-1 | HS Map 1 | +0.0077 [+0.0069, +0.0085]; 127 u / 19 m +0.0000 | pass | **yes** | yes |
| rm-1 | HS Maps 1-2 | +0.0042 [+0.0037, +0.0047]; 1,142 u / 136 m +0.0004 | fail G4 (< 50 rounds -0.0048 [-0.0086, -0.0005]) | no | no |
| rm-1 | HS Maps 1-3 | +0.0051 [+0.0045, +0.0057] | fail G4 (< 50 rounds worse; Under-side calibration worse) | no | no |
| th-1 | kills Map 1 (cohort < 150 weighted rounds) | cohort +0.0309 [+0.0257, +0.0361] (3,894 m); 10 u / 5 m +0.0682 | pass | **yes** | yes (D0 < 50, D1 50-150; >= 150 = th-1 = live, logged `eligible: false`) |
| th-1 | the other 5 cells (incl. Maps 1-2 cm-2 / fallback bands) | pooled gains positive | fail G1 (a fold interval crosses 0) | no | no |

Pooled real-line development figure on the registered rm-1 cells: **+0.0029 [-0.0073, +0.0125] (297 units / 21 matches,
inconclusive)**. The figure **+0.0004 [-0.0024, +0.0030] on 2,692 units / 155 matches is the whole development population (all
scopes, 2,375 of the 2,692 units are Maps 1-2)** - it describes historical study coverage, not the shadow.

Implementation vs registration: matches. Verified in SHADOW_CONTRACTS.md (e43fe76) - deployed module = research code on the
2,922 development inputs (rm-1 297 eligible, max |diff| 5.0e-7; th-1 10 eligible, 4.9e-7); cells exactly
kills 1 / kills 123 / hs 1 and kills 1; replay byte-identical on 1,757 / 1,757 production forecasts. No eligibility fix is
needed, so no code change to `cands_shadow.py`. **Production shows 0 eligible forecasts because only Maps 1-2 lines have been
offered since the deploy, and Maps 1-2 is not a registered cell.** Adding Maps 1-2 (or any cell) would be a new registration
(new id, own clock), never a change to rm-1 / th-1.

Wording that implied a broader scope (marked, not deleted):
- MODEL_VERSIONS.md (main, rm-1 / th-1 shadow entry): "rm-1 beats live HLTV-wide in every fold ... no measurable real-line gain
  (+0.0004 [-0.0024, +0.0030], 2,692 units)" - all-cell / all-scope figures next to a 3-cell shadow. Scope-correction note added
  (branch close2/contracts).
- CANDIDATES.md section 0 "What the gains are made of" (rm-1 "improves the shape, not the picks ... +0.0004 on all 2,692 units",
  side mix 1,259 -> 1,013 of 2,692): all-scope development description; marker added.
- Session memory note cs2-accuracy-2y-1023 ("rm-1's real-line gain is +0.0004 ..., so a forward pass is unlikely"): all-scope
  figure, and the "unlikely" was already withdrawn (C5). For the coordinator to correct (not in this repo).

## A2. Decision procedure (amended)

**Superseded:** REGISTER "Forward confirmation" / CHALLENGERS "Forward rule": "forward real-line paired log-loss gain 95%
match-grouped interval > 0" read at each of up to 3 looks with no alpha spending (each look a one-sided 2.5% test; with 3 looks
the chance that a candidate with no gain passes somewhere is above 2.5%, up to 7.5%).

**Amended rule, per candidate (rm-1, th-1), at each look W in {300, 600, 1,000}:**

1. *Unit set.* Settled, scoreable forward P1 units (matches starting at/after 2026-10-23 07:55:42 UTC and >= 2026-10-22, logged
   after the 07:45 freeze) of the candidate's registered cells whose P1 forecast carries an eligible entry and whose stored live
   chance equals the P1 chance. Pushes on the board line and void / settlement-conflict / incomplete / unresolved-identity units
   are excluded and counted (`learning.product_units`). th-1: cohort units only (< 150 weighted rounds).
2. *Look cohort.* The first W counted matches by start (REVIEW_RULE cohort), frozen at the look. Counted matches: rm-1 = matches
   with >= 1 rm-1-eligible unit; th-1 = see A3.
3. *Statistic.* d = mean over the cohort's UNITS of [LL(logged live) - LL(candidate)], LL = -ln p(outcome), p = P(Over | no push)
   at the offered P1 line, clipped to [1e-4, 1 - 1e-4]; candidate = the logged shadow `p_more` (after the live 22-78% kills cap;
   HS uncapped); + = candidate better. One pooled test per candidate over all its cells (unit-weighted ratio sum / count).
4. *Test.* Match-clustered nonparametric bootstrap: matches resampled with replacement (multinomial weights over the cohort's
   matches), statistic = resampled sum / resampled count, **B = 4,000, seed 20261023, percentile method** (not BCa, not
   studentized). Direction: **one-sided, H1 = candidate better.** Level: **one-sided alpha per test = 0.025 / 6 = 0.0041667**;
   the bound is the floor(alpha x B) = 16th order statistic (0-based) of the 4,000 sorted ratios, which is the lower end of a
   two-sided 99.17% percentile interval. Primary condition: bound > 0.
5. *Family and multiplicity.* Family = **2 candidates x 3 looks = 6 primary tests**, one-sided family-wise alpha **0.025**,
   Bonferroni (valid under any dependence: the looks share data and the two candidates share kills Map 1 units). Per candidate
   that is 0.0125 over its 3 looks (0.0041667 each); a candidate that has no gain is promoted at any look with probability
   <= 0.0125, and either candidate <= 0.025. Pooling cells creates no extra test (one pooled test per candidate; per-cell numbers
   are descriptive only). Secondary metrics (the `gain` vs the live replay, the 95% figures, Brier and calibration intervals,
   side mix, PIT) are not tests and cannot pass a candidate. A look skipped (th-1 < 60) does not pass its alpha to later looks;
   there is no futility stop (stopping for futility can only lower the error rate). After a candidate meets the rule it is not
   tested again unless the owner declines it, in which case it stays a shadow and later looks are read at the same level.
6. *Conditions (all required; non-inferiority conditions, NOT tests - they can only block a pass, so they spend no alpha):*
   (a) Brier gain point >= 0 (paired, unit-weighted); (b) calibration by favoured side, for each side (Over, Under; each arm's
   own favoured side, ties at exactly 0.5 excluded): diff = |mean favoured chance - hit rate| candidate minus the same for live,
   diff <= 1.0 pt AND its 95% match-clustered percentile interval (B = 2,000, seed 20261024) not entirely > 0; a side on which an
   arm has no favoured pick is reported, not a condition; (c) coverage identical: 0 forward P1 units in the candidate's cells,
   in the look's matches, whose forecast has no logged entry or an errored entry; (d) th-1 only: >= 60 cohort matches (A3).
   The Over / Under mix is reported, never a reason (owner rule).
7. *Outcome.* `rule_met` = conditions 4 and 6 all hold -> "eligible for the owner's review"; never automatic promotion
   (promotion = owner action + new MODEL_VERSIONS entry + rollback). A candidate that never meets the rule by the 1,000 look
   stays a shadow ("not confirmed").

Approximate effect on power (normal approximation, the C5 inputs: registered-cell sigma_m 0.0237, primary test only, upper
bounds): true gain +0.0029 -> P(primary passes) at 300 / 600 / 1,000 matches = 0.56 / 0.85 / 0.97 at the registered 95%,
0.30 / 0.64 / 0.89 at the amended one-sided 0.0041667 (0.39 / 0.73 / 0.93 at the per-candidate 0.00833); no true gain -> 0.0042
per look. Stated before any forward outcome; not a reason to change the level later.

Implemented: `learning.cands_report` adds to every reached look a `decision` block (`learning.cands_decision`): units, matches,
point gain, `primary_lower_bound` at the amended level, `primary_met`, Brier gain / `brier_met`, `calibration` per side (gaps,
diff, 95% interval, met), `coverage_gaps` / `coverage_met`, `cohort_ok`, `rule_met`, and "never automatic". `review.rule` =
the amended text, `review.rule_registered` = the original text (verbatim, `learning.CANDS_RULE`), `review.amendment` =
`CANDS_AMENDMENT`. The look's 95% figures (`log_loss_gain_vs_logged_ci`, B = 300) remain for reading only.

## A3. th-1's 60-match minimum (resolved from the original text)

Original text (REGISTER 6d9bb32, written before any candidate number): "Looks only at the REVIEW_RULE checkpoints: 300, 600,
1,000 distinct forward matches with >= 1 eligible logged unit (th-1 **additionally** needs >= 60 distinct **cohort** matches at a
look, else that look is skipped)". The same registration defines th-1 on every row of a cell ("outside the cohort th-1 = live by
construction"; coverage = "the candidate produces a chance for exactly the units / rows live does") and the cells as stat x scope.
CANDIDATES.md section 4 (ec1b0a7, before the freeze) adds that "the 60-cohort-match minimum may not be reached before the
1,000-match look" - which is only possible if the look counter counts more than the cohort. So the 60 was meant to count a
subgroup (cohort matches) of a wider look counter (matches with a th-1 unit in its cell).

The deployed report (CHALLENGERS.md 07:45 / `learning.cands_report`) counted only cohort (`eligible: true`) matches for the
looks, so "additionally >= 60" could never bind - the reading-1 "redundant" outcome, which contradicts the original wording.
**Decision: the 60 is not redundant; the look counter is defined from the original text:**

- th-1 counted matches = distinct forward matches with >= 1 paired P1 unit in **th-1's cell, kills Map 1 on a perf-2 /
  perf-2-bo3 grade**, carrying a logged th-1 entry - cohort (`eligible: true`) or outside the cohort (`eligible: false` with
  reason "history >= 150 weighted rounds" or "no saved history depth", stored live chance = the P1 chance). Same route
  restriction as rm-1 and as the frozen cell; legacy-route and other-cell units are not counted.
- At look W: look cohort = the first W counted matches by start; the look is **skipped unless >= 60 of them have a cohort unit**;
  the statistics (A2) use the cohort units of those matches only (outside-cohort units have difference exactly 0 and are not
  scored).
- This only widens the counter to what the registration said; it adds no eligibility restriction, changes no logged value or
  flag, and no outcome had been seen (0 units in the cell).
- The sentence "may not be reached before the 1,000-match look" is consistent with this definition and stays; its arithmetic
  ("~10 thin units per 5 matches in 11 days") is development-period description, not a forecast.
- Implemented: `cands_report` `look_matches` (the counter), `looks[].cohort_matches`, `cohort_ok`; `eligible_matches` keeps its
  meaning (cohort matches). rm-1's counter is unchanged (its eligible units are its cells).

## Tests (branch close2/contracts, tests/test_cands_shadow.py; each fails on origin/main 6d6564c and passes after)
- `test_th1_look_counts_its_cell_not_only_the_cohort` - 300 cell matches, 60 with a cohort unit -> look 300 reached, 60
  cohort matches, statistics on the 60 cohort units.
- `test_th1_look_skipped_below_60_cohort_matches` - 59 cohort matches -> look skipped, `rule_met` false; 299 cell matches -> no look.
- `test_amended_decision_level_is_bonferroni_over_2_candidates_x_3_looks` - alpha = 0.025 / 6, registered text kept verbatim,
  the bound equals an independent recomputation of the 16th order statistic of the clustered bootstrap.
- `test_amended_decision_guards_block_a_worse_calibrated_candidate` - calibration and coverage conditions block; a clearly
  better, calibrated candidate meets the rule.
- `test_rm1_look_reports_the_amended_decision` - a reached rm-1 look carries the decision block; no gain -> not met.
