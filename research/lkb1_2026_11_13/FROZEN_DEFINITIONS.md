# lkb-1 "lobby kill budget" - frozen test definition (2026-11-13, FACTOR_AUDIT round 9)

Written and committed BEFORE any hit rate, error, calibration or cohort outcome of this test was computed. Only coverage
counts were looked at before freezing (how many matches have saved kills projections for 4+ players per team). Nothing
here is changed after scoring; anything found afterwards is labelled exploratory. TEST ONLY: no live grade, chance,
stake, flag or model change.

## 0. Origin and honest record

- Hypothesis source: cs2_scout_ops/logs/mouz_vitality_review_2026-11-13.md. In the MOUZ-Vitality lobby (Nov 13) the ten
  perf-cm2 Maps 1-2 kills projections implied 6.21 kills per round for the whole match vs ~6.7 actual. Both teams' Elo
  coefficients in the cm-2 artifact are negative, so an elite-vs-elite lobby is pulled down together.
- The review's quartile cut (lowest quarter of implied match KPR: kills Maps 1-2 Unders 46.7%) was chosen AFTER seeing
  the data, on cm2 lobbies Oct 21 - Nov 13. **That retrospective overlaps this test window (Nov 1-13), so the test window
  is NOT fresh data for this hypothesis.** A pass here is at most a candidate for a forward shadow; it can never be a
  promotion by itself. Truly fresh data starts with matches on 2026-11-14.

## 1. Data (local, read-only; production touched only by paged reads)

- Saved pre-match forecasts = the pick log (`/admin/picks.json`, keyset pages of 1,000, one call at a time, `/health`
  checked between calls; pulled 2026-11-13, cutoff id 27921, ids > 7000): each row is the FIRST grade shown for that
  exact line (projection `lambda`, `p_more`, side `pick`, `p_win`); later regrades never rewrite it. Saved in
  cs2_scout_ops/data/lkb1_1113/. The live rows are NOT re-derived. (The P1 forecasts export `/admin/forecasts_page.json`
  took 99 s per 500 rows on 2026-11-13 and is not used.)
- HLTV history for the normal: cs2_scout_ops/data/cs2_full/hltv (extract to 2026-11-05) plus `/admin/hltv_export`
  2026-10-28..2026-11-13 (one call). The export also gives Maps 1-2 rounds and the series winner for the upset/OT check.

**Rows scored (board rule):** pick rows with `on_board` = 1, `test` in (0, 2), `line_type` = standard, logged strictly
before `match_start`, status settled with an actual (push / void excluded), prop kills or hs, scope `12` (Maps 1-2) or
`1` (Map 1). Re-keyed duplicates (same player id, prop, scope, line, side, start date) counted once (lowest id).
Outcome: Over hit iff actual > line, Under hit iff actual < line.

**Windows (by match start, UTC):** FIT 2026-10-21 .. 2026-10-31 (cm-2 live for Maps 1-2 kills from Oct 20);
TEST 2026-11-01 .. 2026-11-13. Test halves for the stability guard: T1 Nov 1-7, T2 Nov 8-13.

## 2. Candidate lkb-1

For each match m (grouped by `match_slug`) and scope s in {Maps 1-2, Map 1}:

1. Lobby projections: each player's saved KILLS projection for that scope = median `lambda` over all his pre-start pick
   rows of that match (board and card lookups; any line). Players keyed by player id, teams by the row's `team` field.
2. Lobby eligible only if exactly two teams appear and BOTH have >= 4 players with a projection. Team total = 5 x mean of
   its players' projections (exactly the sum when 5 are known). T = sum of the two team totals. Not eligible -> live.
3. Normal: from complete HLTV maps (10 unique players, 5 per team, total rounds > 0) dated in [D-90, D-1], D = match
   start date (UTC): KPR_norm = mean over maps of (sum of the 10 players' kills / rounds); R_norm = mean rounds per map.
   N = KPR_norm x R_norm x n_maps (n_maps = 2 for Maps 1-2, 1 for Map 1). Only data dated before the match day is used.
4. Target T* = T + alpha x (N - T). Scale f = T* / T, the same for all 10 players (each team keeps its share).
5. Kills: lam' = lam x f. HS of the same scope: lam'_hs = lam_hs x f (hs-cm2 = cm2 kills x HS%, so HS inherits through
   kills). Map 1 uses its own lobby (Map 1 kills projections, n_maps = 1) and the same alpha.
6. **alpha** = the value in {0.0, 0.1, ..., 1.0} that minimises the sum of squared errors (actual - lam') of Maps 1-2 kills
   over FIT units (one per player-match: the player's median projection and his settled Maps 1-2 kills; eligible lobbies
   only). Fitted once on FIT, never touched on TEST. If alpha = 0 the candidate equals live and fails by construction.

**Side rule (as round 8, rule S):** candidate side = live side, flipped only when lam and lam' sit on opposite sides of the
row's line (lam' exactly on the line -> live side). The graded count therefore cannot fall; it is still reported.

**Candidate chance (for calibration only):** per (stat, scope) c = median over FIT rows with |lam - line| >= 1 and
0.02 < p_more < 0.98 of |lam - line| / (|z(p_more)| sqrt(lam)), z = standard-normal quantile.
p'_more = Phi(z(p_more) + (lam' - lam) / (c sqrt(lam))). Identical to live when f = 1.

## 3. Placebos (frozen; BOTH must fail)

- **PL-S random sign:** f_pl = 1 + s_m |f - 1|, s_m = +1 / -1 per match from numpy default_rng(20261113), one draw per
  match over the sorted list of eligible match slugs (shared by both scopes).
- **PL-X shuffled budgets:** each eligible match gets the T of another eligible match of the same scope and window (a
  permutation from numpy default_rng(20261114) over sorted slugs; fixed points allowed), and
  f_pl = (T_x + alpha (N_m - T_x)) / T_x with the match's own N; applied to the match's own projections.
Same alpha, same side rule, same scoring. A placebo "fails" when it does NOT meet criterion C1.

## 4. Pass criteria (owner's rules; all must hold on TEST rows)

Paired with live on the same test rows. CIs: match-grouped bootstrap, 2,000 resamples, numpy default_rng(7), 95%.

- **C1 hit rate improves with CIs:** all-rows hit rate candidate - live: 95% CI lower bound > 0.
- **C2 judged separately for Overs and Unders:** candidate Over-pick hit >= live Over-pick hit AND candidate Under-pick
  hit >= live Under-pick hit (point estimates; CIs of each difference reported).
- **C3 graded count not reduced:** candidate graded rows >= live graded rows.
- **C4 split not more lopsided:** |candidate Over share - 50%| <= |live Over share - 50%| + 1.0 point.
- **C5 calibration not worse:** Brier score of p_more vs the Over outcome, candidate - live, point estimate <= 0 (CI
  reported); log loss and stated-minus-hit by side reported, not gating.
- **C6 upset / OT check:** the paired hit difference point estimate is >= 0 on each of
  (a) upset matches (the team whose median pre-match `p_win` < 0.5 won the series, by HLTV map wins),
  (b) OT matches (Map 1 or Map 2 > 24 rounds), and
  (c) the remaining matches (no upset, no OT), so a gain is not bought only on long or upset series.
  Matches without an HLTV result or `p_win` are left out of (a) / (c) and counted.
- **C7 stability guard:** point estimate >= 0 in both test halves T1 and T2.
- **C8 placebos:** PL-S and PL-X each fail C1.

Pre-registered sensitivity (reported, not gating): the same scoring excluding the five MOUZ / Vitality BLAST matches of
Nov 11-13 that motivated the hypothesis (review section 3b). Descriptive (not gating): FIT-window hit with the fitted
alpha, staked (> 0U) rows only, kills vs HS, Maps 1-2 vs Map 1.

## 5. If it passes / fails

Pass -> a SHADOW record only (new module behind an env flag, default off, logged beside live, no grade / chance / stake
change), forward evaluation on matches from 2026-11-14, and a written owner decision before any live use. Fail -> logged
as a measured negative result in FACTOR_AUDIT; nothing changes. roles.py, cs2_scout.py, bo3.py, hltv.py are not edited.
