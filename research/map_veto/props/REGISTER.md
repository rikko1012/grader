# vm-1 (map-informed props) - pre-registration

Registered 2026-10-23 BEFORE any prop result is computed (no P1 or HLTV player-level number has been calculated for any arm
below). Only coverage counts were looked at: of the 155 P1 matches (2,692 units, start < 2026-10-22), 143 have a stored
complete veto (125 BO3, 17 BO1, 1 BO5) and 12 have no veto record. Nothing here changes live grading or veto-1
(veto-1-a140ca69ec, FROZEN.json); the live grader is never edited by this work.

Owner (item 5): test whether map context improves props on identical real offered lines - current grading vs grading
integrated over predicted legal map sequences vs a clearly labelled retrospective comparison using the confirmed maps;
integrate full outcome distributions (never average map-specific hit percentages, never treat one guessed veto as
certain); map effect only through expected rounds, scorelines, overtime risk and opportunity (no per-map player kill-rate
adjustment - previous tests found none); kills and headshots scored separately by scope; projection error, log-loss and
Brier; include the subset where predicted maps change a forecast; activate only through a predeclared comparison.

## 1. The candidate vm-1 (fixed here; constants come from the fits in section 2, nothing else is chosen later)
For one forecast (player P on team A vs team B, prop kills|HS, scope Map 1 / Maps 1-2 / Maps 1-3, offered line L):

1. **Map sequences**: the veto-1 distribution over complete legal veto sequences (veto_model.predict, frozen params,
   pre-veto: no known actions, the event pool, table cut strictly before the match day, HLTV listed order if known,
   lineups = each team's previous match). Played order: BO3 (Map 1, Map 2, decider), BO1 (the map), BO5 (Map 1..4, decider).
2. **Match script per map** (team strength from pre-match data only). Rounds are a Beta-binomial round process: on map m,
   team A wins each round with probability pi, pi ~ Beta(mu kappa_m, (1 - mu) kappa_m), CS2 MR12 rules (first to 13;
   12-12 -> overtime periods of 6 rounds, first to 4, 3-3 -> another period). The probability of a final score (a, b) is
   exactly N(a, b) B(alpha + a, beta + b) / B(alpha, beta), N = number of legal round orders ending at (a, b). This gives,
   per map, the joint distribution of (total rounds, map winner) - so rounds, scoreline, overtime risk and the map winner
   come from one model.
   - logit(mu) = c0 * logit(E_elo) + c1 * (s_A(m) - s_B(m)); E_elo = the veto table's pre-match Elo expectation for A;
     s_T(m) = the veto table's opponent-adjusted map-win residual of team T on map m (veto_model team_stats "strength",
     frozen k_strength 6, lineup-weighted exactly as veto-1).
   - kappa_m: one global kappa fitted with (c0, c1); then per-map log kappa_m fitted with (c0, c1) fixed and shrunk to the
     global value: log kappa_m* = (n_m log kappa_m + 2000 log kappa) / (n_m + 2000), n_m = fit maps on m. A map never seen
     in the fit uses the global kappa.
   - Overtime check (predeclared): if the fitted model's overtime share on the fit window differs from the observed share by
     more than 1.0 point, one factor tau (per-map OT probability q -> min(0.5, tau q), regulation and OT parts each
     renormalised) is fitted by ML on the fit window and applied everywhere; otherwise tau = 1.
3. **Series script**: Map 1 = the Map 1 marginal mixture; Maps 1-2 = both maps (always played in BO3/BO5); Maps 1-3 in a BO3 =
   the joint over (Map 1, Map 2, decider) sequences x Map 1 / Map 2 winners, **conditioned on the series reaching map 3**
   (Map 1 and Map 2 split; a Maps 1-3 BO3 line only grades when map 3 is played); BO5 Maps 1-3 = three maps always played.
   The scope rounds pmf R_S is the exact mixture sum over sequences s of w_s x (convolution of the per-map rounds pmfs,
   with the split condition where it applies), renormalised.
4. **Player opportunity**: the player's kill rate per round is the live one, unchanged: rate = lam_live / (m x Rbar), m =
   maps in scope, Rbar = the fit window's mean rounds per map (so an average matchup reproduces the live level). Kills | R
   ~ normal(mu_R = rate x R, sd_R = rho_cell x c x sqrt(max(mu_R, 1))), c = the live kills spread (sd / sqrt(max(lam, 1))
   for kills; perf.kills_c for HS), rho_cell = the rm-1 F6 fit (fit before 2026-10-01: 1_p2 0.7991, 12_p2 0.8328, 12_cm2
   0.8009, 123_p2 0.7765; Map 1 rows use 1_p2, Maps 1-2 perf-cm2 rows 12_cm2, other Maps 1-2 rows 12_p2, Maps 1-3 123_p2).
   Kills chance = the live continuous-normal line cuts on each component, mixed (rm-1 lib.p_over_kills), P(Over | no push),
   then the live kills cap 22-78%. HS = the live beta-binomial (kappa 100, the forecast's HS per kill) over the mixed integer
   kills pmf, P(Over | no push), uncapped (as live). Projection lam_vm = rate x E[R_S].
5. **Eligibility**: the live grade is on perf-2 / perf-2-bo3 / perf-cm2 with a saved lambda and sd (and lambda_kills,
   HS per kill for HS), and the match has a veto-1 prediction (both teams + pool). Ineligible units keep the live chance in
   every arm (coverage is identical by construction) and are reported separately.

Arms on identical units:
- **A0 current** = the LOGGED live chance (a replay from saved inputs is shown beside it as a check).
- **A1 vm-1** = section 1 with the veto-1 sequence distribution (pre-veto; eligible for live use).
- **A2 RETROSPECTIVE (confirmed maps)** = section 1 with the actually played map order given probability 1 (from the stored
  complete veto). Uses post-veto information: **never eligible for live grading**; reported only as an upper reference for
  what map knowledge could add.
- Secondary control **C0 no-map script** = section 1 with no map context (c1 = 0, global kappa for every map, one map-free
  rounds pmf per slot) - separates "matchup closeness / rounds mixture" from "which maps". Not part of any rule.

## 2. Fits (all strictly before the data they are used on)
- Data: the read-only 2-year HLTV export (scratch cs2acc/data/hltv_window.sqlite: per-map final scores `matches` sideid 0)
  + the veto dataset (scratch veto/vmodel/veto_dataset.json.gz). Features for a historical map come from the day-by-day
  VetoTable BEFORE that day's matches are added (the veto-1 leakage rule).
- **Development constants**: script model fitted by ML on maps of matches dated 2025-07-01 .. 2026-06-30 (table warmed from
  2024-10-01). These constants are used for every development number (HLTV secondary and the real P1 lines).
- **Frozen shadow constants**: the same procedure refitted once on 2025-10-22 .. 2026-10-21 (the latest 365 days); no
  setting is chosen by any prop result. Frozen = version vm-1-<params sha> committed with the module.

## 3. Development evaluation
**(a) Real offered lines (primary for the record, cannot activate).** The P1 rule units (scratch cs2acc/ed1/p1.csv.gz +
under/p1_fc.json saved inputs; latest board forecast >= 30 min before start, standard lines), start < 2026-10-22 (all 2,692
units, 155 matches, 2026-10-11 .. 10-21). Decided units only (no pushes exist in this set).

**(b) HLTV population (supporting, larger).** ED-1 series.csv.gz player-series (live formula: perf-2 Map 1 / Maps 1-3,
cm-2 Maps 1-2 with perf-2 fallback) of matches dated 2026-07-01 .. 2026-10-21 with a stored complete veto: kills and HS
integer actual for Map 1 / Maps 1-2 / Maps 1-3 (Maps 1-3 = BO3 that reached map 3 and BO5). Scored by the integer log score,
PIT-free; plus the same rows' map-level checks: rounds log-loss / MAE / overtime Brier per map slot and map-winner Brier
for A1 / A2 / C0 vs the pool pmf (rm-1's PI fitted on the 365 days before 2026-07-01).

Cells: {kills, HS} x {Map 1, Maps 1-2, Maps 1-3}.

Metrics per cell and arm (A1 vs A0, A2 vs A0, C0 vs A0, A1 vs C0):
- paired per-unit log-loss gain (A0 minus arm; > 0 = arm better; natural log, chances clipped to [1e-6, 1 - 1e-6]);
- Brier difference (arm minus A0; < 0 = arm better);
- signed projection error mean(actual - projection) and MAE |actual - projection| (projection = lam for kills,
  lam_kills x HS per kill for HS, live vs lam_vm);
- favoured-side calibration: |mean favoured chance - favoured hit rate| for Over-favoured and Under-favoured units
  (rm-1 lib.boot_absgap);
- sides flipped, mean |delta P|;
- intervals: match-clustered bootstrap (2,000 resamples, seed 20261023, 95% percentile; lib.boot); distinct matches shown.
- **Changed-forecast subset**: units with |P_A1 - P_A0| >= 0.01 or a favoured-side flip (P on the other side of 0.5);
  reported per cell. Secondary: the "maps changed it" subset |P_A1 - P_C0| >= 0.01 or side flip vs C0.

## 4. Decision rule (predeclared) - activation needs the FORWARD test
The development real-line archive is small (11 days, 155 matches; Map 1 and Maps 1-3 cells have <= 150 units), so **a
development pass alone never activates map-informed grading**. Activation of a cell requires all of the following on the
FORWARD set:
- Forward set: P1-rule units (same rule as 3a) of matches starting at or after the vm-1 shadow's first logged forecast,
  with the vm-1 shadow entry logged before the match (no recomputation after the start), on the units where vm-1 is
  eligible; A0 = the logged live chance on exactly those units (coverage identical: both arms on the same unit set; a
  cell also needs vm-1 eligible on >= 90% of its live P1 units).
- One look per cell, when it reaches 300 distinct forward matches; a cell that has not reached 300 by 2027-03-31 is looked
  at once then if it has >= 100 matches, otherwise no decision (stays shadow). No early stopping, no re-looks.
- Primary (one-sided, family-wise alpha 0.05 split equally over the 6 cells -> 0.00833 each): the match-clustered bootstrap
  (10,000 resamples) lower 99.167% bound of the mean log-loss gain (A0 - A1) is > 0.
- Non-inferiority: Brier - the upper 95% bound of mean(Brier_A1 - Brier_A0) < +0.0025; favoured-side calibration - for
  both Over-favoured and Under-favoured units, gap_A1 - gap_A0 <= +0.01 (point) and its upper 95% bound < +0.03.
- Only the passing cell(s) may be activated, with the frozen vm-1 version, by a separate owner-approved release.
A2 (retrospective) is never part of any rule. The development results (3a, 3b) are reported for the record and to judge
whether the forward test is worth running; they cannot activate anything and do not change this rule.

## 5. Shadow
vm-1 is implemented as a pure function module (research/map_veto/props/vm1.py: shadow_entry(forecast_inputs,
veto_prediction, match_context) -> {version, eligible, p_over, lam, rounds_mean, ot_prob, ...}), bounded and fast, frozen
(version + params sha) before any forward unit exists. Logging it beside the live grade is the serving phase's job; it is
shadow only (never shown, never changes a grade, chance or stake).

## Amendment AM-1 (2026-10-23, before any forward unit exists)
Made after the development results (RESULTS.md, commit a325fd8) and after the owner's rule "if map prediction improves
but props do not, ship the map display and keep the grading effect in shadow". The text above is unchanged. **0 forward
units existed at amendment time**: vm-1 is not yet logged by the server, so no forward vm-1 entry has been written.

The serving phase logs two shadow entries per forecast with the frozen module vm-1-ceabd6f325: **A1** (shadow_entry(...,
use_maps=True)) and **C0** (shadow_entry(..., use_maps=False)). Both get the same veto prediction and match context, so they
are eligible on the same units.

- **PRIMARY forward question = the map effect, A1 vs C0**, on identical units where both are eligible: paired per-unit
  log-loss gain (C0 minus A1; > 0 = maps help), the same six cells, the same look schedule (one look per cell at 300
  distinct forward matches; else once on 2027-03-31 if >= 100 matches, else no decision), the same alpha split (one-sided,
  0.05 / 6 = 0.00833 per cell), match-clustered bootstrap (10,000 resamples), lower 99.167% bound > 0.
- **A1 vs live** (the section 4 comparison) is still reported, but it is **explicitly NOT evidence for maps**: in
  development its gain equals C0's, i.e. it is the rm-1 rounds-mixture shape, which is already in shadow as rm-1.
- **Activation of a map effect in grading** requires BOTH (1) the primary A1-vs-C0 test passes in that cell AND (2) A1 is
  not worse than live in that cell: the Brier non-inferiority and favoured-side calibration non-inferiority conditions of
  section 4 (upper 95% bound of mean(Brier_A1 - Brier_live) < +0.0025; for Over- and Under-favoured units gap_A1 -
  gap_live <= +0.01 point and upper 95% bound < +0.03), with the >= 90% eligibility coverage condition. Section 4's
  log-loss condition for A1 vs live no longer counts as map evidence on its own. A2 (RETROSPECTIVE) never enters any rule.
- Only passing cells may be activated, with the frozen version, by a separate owner-approved release.
