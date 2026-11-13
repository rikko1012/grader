# Miss decomposition - frozen test definitions (2026-11-13, FACTOR_AUDIT round 10: A rounds, B Elo, C player rates)

Written and committed BEFORE any hit rate, error, residual, calibration or cohort outcome of this round was computed.
Looked at before freezing: data coverage counts only (units, matches, how many map to HLTV) and one input check that uses
no outcome (the locally rebuilt cm-2 projection vs the saved perf-cm2 projection: 2,850 units, correlation 0.9955,
median absolute difference 0.003 kills). Scripts `build.py` (data) and `score.py` (measurement, fits, scoring) are
committed with this file; anything changed after scoring is listed as a deviation. TEST ONLY: no live grade, chance,
stake, flag or model change. roles.py, cs2_scout.py, bo3.py, hltv.py are not edited.

## 0. Why, and what is not fresh

- Owner direction 2026-11-13: lkb-1 (round 9) was rightly rejected, but a failed adjustment does not mean projections
  cannot improve. Split the MOUZ-Vitality miss into three parts and test each SEPARATELY on chronological data:
  A rounds (expected rounds / overtime vs closeness), B the opponent / Elo terms, C player rates (form, role, maps).
- The lkb-1 TEST window (Nov 1-13) overlapped matches already examined (review of Nov 11-13). Here the TEST window is
  Nov 1-10 and the five MOUZ / Vitality BLAST Rivals matches of Nov 11-13 are reported separately (none falls in Nov 1-10).
  Nov 1-10 outcomes were already used in aggregate by lkb-1 (round 9) and round 8, so TEST is not untouched either;
  **only the forward window (matches starting after this freeze commit) is untouched.**

## 1. Data (local, read-only; no new production call)

- Saved pre-match forecasts: `cs2_scout_ops/data/lkb1_1113/picks_7000.jsonl.gz` (paged `/admin/picks.json`, ids
  7001-27921, cutoff 27921; each row = FIRST grade shown for that exact line; matches from 2026-10-19).
- HLTV: local extract `cs2_scout_ops/data/cs2_full/hltv` for maps before 2026-10-28 and the one `/admin/hltv_export`
  (2026-10-28 .. 11-13) from 2026-10-28 on. From these rows `build.py` rebuilds the cm-2 tables with the live code
  (`cm2.build_tables`, unchanged): complete scoreboards, opponent refits, Elo at the start of the day.
- **Pick unit** = (match_slug, player) with Maps 1-2 kills or HS rows logged before the start: lam_k = median saved
  kills projection, lam_h = median saved HS projection, rounds_used = the board's expected rounds (`detail.rounds`, 44),
  market team price (`p_team_market`, median), `p_win` (median), map chances `pred_maps` (latest pre-start row).
- **HLTV mapping** (identity only): slug `hltv-<id>` -> that match; else the unique HLTV match within start day +-1 with
  the most exact lower-case player-name hits (>= 6 when both teams have 3+ named players, else >= 3; ties -> unmapped).
  Unmapped units take the HLTV id of a unique exact name active since 2026-07-15 and his latest team. Never fuzzy.
- **History unit** = one player in a complete HLTV best-of-3 lobby (maps 1 and 2 complete) with cm-2's Maps 1-2 kills
  projection recomputed from the tables (data strictly before the match day; artifact fitted through 2026-09-30).
- Coverage (counts only): pick units FIT (Oct 19-31) 2,422 / 322 slugs (256 map to an HLTV lobby, 2,215 units with
  Elo inputs), TEST (Nov 1-10) 1,071 / 148 slugs (118 mapped, 962 with Elo inputs); history units Oct 2026 9,939 / 993
  matches; all HLTV best-of-3 lobbies Jan-Oct 2026 for A1.

**Rows scored (board rule, as round 9 but Maps 1-2 only):** `on_board` = 1, `test` in (0, 2), standard line, logged
before `match_start`, settled with an actual (pushes excluded), prop kills or hs, scope `12`, side More / Less; re-keys
(same player id, prop, scope, line, side, start date) once (lowest id). Map 1 rows are perf-2, out of scope.

**Windows (match start, UTC).** FIT: everything through 2026-10-31 (each candidate's fit set below). TEST: 2026-11-01 ..
11-10 minus the five motivating slugs; halves T1 Nov 1-5, T2 Nov 6-10. MOTIVATING (reported apart):
vitality-vs-falcons-esports-11-11-2026, mouz-cs2-vs-g2-11-11-2026, furia-vs-vitality-12-11-2026,
fut-cs2-vs-mouz-cs2-12-11-2026, vitality-vs-mouz-cs2-13-11-2026. FORWARD: matches starting after the freeze commit.

## 2. Measurements (descriptive, reported for FIT, TEST and later separately; not gating)

- **A:** per match, actual Maps 1-2 rounds vs the 44 used and vs rr-1's saved market rounds (`market_rounds.rounds_sum`);
  overtime rate (Map 1 or Map 2 > 24 rounds); by closeness bands: market per-map chance q in 40-60% (|q-0.5| < 0.10),
  0.10-0.20, >= 0.20; Elo closeness |q_elo-0.5| < 0.05, 0.05-0.10, >= 0.10. Also all HLTV best-of-3 lobbies Jan-Oct 2026.
- **B:** per unit, kills-per-round residual (actual kills / actual Maps 1-2 rounds) - (projection / 44), and the ratio
  sum(actual KPR) / sum(projected KPR), by quintile of combined Elo S = (Elo_own + Elo_opp) / 800 and of the gap
  G = (Elo_own - Elo_opp) / 400; on history units Oct (recomputed cm-2), board FIT and board TEST (saved projections).

## 3. Candidates (each a multiplier f on the saved Maps 1-2 kills AND HS projections of a unit; HS inherits because
hs-cm2 = cm2 kills x HS%; no input -> f = 1, the live projection). All fitted ONCE on data through 2026-10-31.

Closeness: q = per-map win chance from a series chance p by the board's BO3 rule p = q^2 (3 - 2q); d = |q - 0.5|.
Elo series chance = 1 / (1 + 10^((Elo_opp - Elo_own)/400)) with the rebuilt cm-2 Elo at the start of the match day.

- **A1 rounds by Elo closeness.** Fit: OLS R12 = a + b d_elo on HLTV best-of-3 lobbies 2026-01-01 .. 10-31 (R12 = actual
  Maps 1-2 rounds, overtime included). f = (a + b d_elo) / Rbar, Rbar = mean fitted R12 over the fit matches (level
  neutral: cm-2's level is fitted on actual kills, so only the closeness shape is added).
- **A2 rounds by market closeness.** Same with d_mkt from `p_team_market`; fit on board matches Oct 19-31 that map to an
  HLTV lobby with maps 1-2 complete. No price -> f = 1.
- **B1 re-centred Elo level + gap.** Poisson regression of actual Maps 1-2 kills with offset log(lam x R12 / 44) (rounds
  taken out) on [1, S - mean(S), G]; history units Oct 1-31 2026 (recomputed cm-2). f = exp(b_S (S - mean S) + b_G G).
- **C1 recent form.** form = ((k10 + 100 r_all) / (r10 + 100)) / r_all, k10 / r10 = kills / rounds over his last 10
  complete maps before the match day, r_all = cm-2's shrunk all-environment rate (exp of feature 1). Same Poisson set-up
  on [1, log form], history units Oct. f = form^beta.
- **C2 role.** AWPer = HLTV `player_awp` AWP share >= 0.25 with >= 100 kills (roles.py's AWP rule, read from the local
  extract; roles.py itself untouched). Entry = not AWPer and opening attempts per round (opening kills + opening deaths
  over his complete maps in the 180 days before the day, >= 200 rounds) >= tau, tau = 80th percentile among non-AWPer
  FIT units. Support = everyone else with HLTV inputs. Poisson on [1, awp, entry], history units Oct;
  f = exp(gamma_role - share-weighted mean gamma) (level neutral).
- **C3 map-specific.** M = sum over maps of w_m x rho_m; w_m = (P(map 1 = m) + P(map 2 = m)) / 2 from the saved
  `pred_maps` (each slot normalised); rho_m = ((k_m + 150 r) / (r_m + 150)) / r, k_m / r_m his kills / rounds on map m
  in the 365 days before the day, r = his 365-day kills per round. Poisson on [1, log M] with offset
  log(lam_k x R12 / rounds_used), board pick units Oct 19-31 (saved projection; map chances exist only there).
  f = M^beta.

**Side rule (round 8 rule S, as round 9):** candidate side = live side, flipped only when lam and lam x f sit on opposite
sides of the row's line. **Candidate chance (calibration only):** c per prop = median over FIT board rows (Oct 21-31,
|lam - line| >= 1, 0.02 < p_more < 0.98) of |lam - line| / (|z(p_more)| sqrt(lam)); p' = Phi(z(p_more) + (lam f - lam) /
(c sqrt(lam))).

## 4. Placebos (frozen; each must FAIL C1)

Same fit, same scoring, input permuted with numpy default_rng(seed):
A1 match-level d_elo permuted across TEST matches (20261115); A2 d_mkt permuted across matches (20261116); B1 each match
takes another match's (S, G) pair, teams in name order (20261117); C1 form values permuted across units (20261118);
C2 role labels permuted across units (20261119); C3 each match takes another match's map chances with the player's own
per-map rates (20261120).

## 5. Pass criteria (all must hold on TEST rows, per candidate; paired with live on the same rows)

CIs: match-grouped bootstrap, 2,000 resamples, numpy default_rng(7), 95%.
- C1 hit rate at real offered lines improves: candidate - live CI lower bound > 0.
- C2 Over-pick hit and Under-pick hit each not lower (point estimates; CIs reported).
- C3 graded count not reduced (by construction; reported).
- C4 Over share no more than 1.0 point further from 50% than live.
- C5 calibration: Brier on p_more, candidate - live <= 0 (point; CI and by-side reported).
- C6 hit difference >= 0 on each of upset matches (the team with median `p_win` < 0.5 won on HLTV map wins), overtime
  matches (Map 1 or 2 > 24 rounds) and the remaining matches; unclassified matches counted.
- C7 hit difference >= 0 in both halves T1 and T2.
- C8 the candidate's placebo fails C1.
- C9 error: kills-per-round MAE on TEST units (|actual kills / actual R12 - lam f / 44|, A leaves KPR unchanged) not
  worse; for A1 / A2 instead rounds MAE (|R12 - 44 f|) AND Maps 1-2 kills MAE not worse. Kills, HS and HS-per-round MAE
  reported with CIs.

Six candidates, one test each: a single pass at this size is weak evidence (multiple comparisons) and leads only to a
forward shadow, never to promotion.

## 6. Forward validation (untouched)

- FORWARD = matches starting after the freeze commit time (committer time of the commit that adds this file; written
  into FREEZE_TIME.txt by the results commit). The fitted parameters (`fit_params.json`) and every candidate's factor
  for already-logged forward units (`forward_predictions.jsonl`) are saved with their sha256 at scoring time; units
  graded later get their factor from the same frozen code and parameters (inputs strictly before the match day).
- Review once 150 forward matches have settled Maps 1-2 board rows, with C1-C9 unchanged (bootstrap seed 7); no peeking
  decision before. A candidate that failed TEST stays a measured negative result; its forward record only confirms it.
- If a candidate passes TEST: a SHADOW proposal only (new module behind an env flag, default off, logged beside live,
  no grade / chance / stake effect), then an owner decision before any live use.
