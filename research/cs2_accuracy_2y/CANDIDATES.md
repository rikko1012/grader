# rm-1 and th-1: development evaluation (registered in REGISTER.md, commit 6d9bb32, before any candidate number)

Development evidence only. Every period used here (HLTV 2025-01..2026-10-21; real P1 lines 2026-10-11..10-21) had already been
looked at by earlier studies, so nothing here is a holdout result. Live grading is unchanged. Scripts are in `candidates/`; the
machine-readable output is in `candidates/results/` (`report.json`, `tables.md` = every table, `params_<fold>.json`). All
intervals are match-grouped bootstrap 95% (B = 2,000; subgroups B = 1,000) unless marked Bonferroni (99.58%, 12 cells).

## Corrections 2026-10-23 (owner review)

The original text is kept unchanged; overstated sentences are prefixed **[Superseded 2026-10-23: C5]**. No number was changed.
Script: `corrections/power_rm1.py` -> `corrections/results/power_rm1.md` / `.json`.

**C5. Current real-line results for rm-1 are INCONCLUSIVE, and "a forward pass looks / is unlikely" is withdrawn** (it was not
backed by a power analysis). Development real-line effect: all 2,692 units / 155 m **+0.0004 [-0.0024, +0.0030]**; the
registered cells (kills Map 1, kills Maps 1-3, HS Map 1) **+0.0029 [-0.0073, +0.0125]** (297 u / 21 m). Both intervals contain 0
and also contain effects the forward rule could detect; the data neither support nor rule out a real-line gain. (The power script's own
bootstrap, another seed, gives [-0.0021, +0.0030] and [-0.0068, +0.0123] for the same points.)

Power of the registered forward rule (primary test only: paired log-loss gain, 95% match-grouped interval > 0; Brier /
calibration guards not modelled, so these are UPPER bounds on P(pass)). Per-match sd of the linearised paired log-loss
difference from the development real lines: sigma_m 0.0169 (90% range 0.0152-0.0186) on all units (17.4 u per match);
0.0237 (0.0147-0.0297, only 21 matches) on the registered cells (14.1 u per match). SE at a look = sigma_m / sqrt(M).
Normal approximation, checked by Monte Carlo (400 simulated looks, bootstrap B = 500 each; agreement within 0.03).

| true effect (nats per unit) | registered cells: P(pass) at 300 / 600 / 1,000 m | all-units population: 300 / 600 / 1,000 m |
|---|---|---|
| -0.0020 | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 |
| 0 (no effect) | 0.025 / 0.025 / 0.025 | 0.025 / 0.025 / 0.025 |
| +0.0004 (development point, all units) | 0.048 / 0.061 / 0.077 | **0.061 / 0.084 / 0.113** |
| +0.0010 | 0.109 / 0.177 / 0.266 | 0.175 / 0.305 / 0.464 |
| +0.0029 (development point, registered cells) | **0.563 / 0.850 / 0.972** | 0.844 / 0.988 / 1.000 |
| +0.0050 | 0.955 / 0.999 / 1.000 | 0.999 / 1.000 / 1.000 |
| +0.0100 | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 |

- Smallest true effect with 80% power: registered cells +0.0038 / +0.0027 / +0.0021 at 300 / 600 / 1,000 m; all units +0.0027 /
  +0.0019 / +0.0015.
- If the true effect equals the all-units development point (+0.0004), P(pass) is 6% / 8% / 11% (about 13,400 matches would be
  needed for 80%). If it equals the registered-cell point (+0.0029), P(pass) is 56% / 85% / 97% (80% at about 526 matches;
  with sigma_m at its 90% range, 39-93% at 300 m). The two points differ because they are different populations; the forward
  rule tests the registered cells (learning.CANDS_REVIEW `cells`).
- Under the stricter 98.33% level used by the general REVIEW_RULE (3-look Bonferroni), the registered-cell figures at +0.0029
  are 0.392 / 0.727 / 0.930 (the frozen CANDS_RULE says 95%).
- Calendar: only 21 of 155 development matches (14%, 11 days) had a unit in the registered cells, so reaching 300 eligible
  matches would take months at that rate (an observation about the development period, not a forecast of the feed).

Statement for the record: **current real-line evidence on rm-1 is inconclusive; the probability of a forward pass depends on the
unknown true effect and is about 2.5% if rm-1 has no real-line effect, rising to about 56% (300 m) / 85% (600 m) / 97% (1,000 m)
if the registered-cell development point is the true effect.**

## 0. Decisions

| candidate | cells that pass the registered gate G1-G5 | HLTV effect (pooled, nats per player-series) | real-line effect on the passing cells | decision |
|---|---|---|---|---|
| **rm-1** rounds mixture | **kills Map 1, kills Maps 1-3, HS Map 1** | kills Map 1 **+0.0423 [+0.0271, +0.0625]** (14,664 m); kills Maps 1-3 **+0.0166 [+0.0147, +0.0187]** (5,408 m); HS Map 1 **+0.0077 [+0.0069, +0.0085]** (14,664 m); every fold > 0 | **+0.0029 [-0.0073, +0.0125]** log loss (297 units / 21 m): **[Superseded 2026-10-23: C5 - inconclusive]** no measurable change on real lines | **PASS (dev) -> shadow for those 3 cells.** Maps 1-2 kills fails G3 (real-line point -0.0002, 1,233 u / 136 m); HS Maps 1-2 and HS Maps 1-3 fail G4 (thin history < 50 rounds significantly worse; HS Maps 1-3 Under-side calibration worse) |
| **th-1** thin / fallback | **kills Map 1 only** (perf-2 bands D0 < 50, D1 50-150 weighted rounds) | kills Map 1 cohort **+0.0309 [+0.0257, +0.0361]** (12,974 rows / 3,894 m), every fold > 0 | **+0.0682 [+0.0305, +0.0976]** (only **10 units / 5 m**) | **PASS (dev) -> shadow for that cell.** Every other cell fails G1 (one fold's interval crosses 0, mostly F2 2025-Q4) although their pooled gains are positive |

What the gains are made of:
- **[Scope note 2026-10-23: AMENDMENT A1 - the all-unit figures in this bullet and the next are the whole development population (2,375 of 2,692 units Maps 1-2, not a registered cell); the registered cells' figure is +0.0029 [-0.0073, +0.0125], 297 u / 21 m]** **rm-1 improves the shape, not the picks.** On HLTV the gain is in the integer log score (the tails: overtime maps). At the
  lines that are actually offered (close to the projection) it changes almost nothing: real-line log loss +0.0004
  [-0.0024, +0.0030] on all 2,692 units / 155 m, split equally between units whose favoured side is unchanged (+0.0002) and
  units whose side flips (+0.0002, 246 units). It does correct the HLTV "actual beats the projection only 45% of the time"
  calibration gap at a line next to the projection (kills Map 1: over rate 45.2%, live chance 50.0%, rm-1 46.9%; favoured-side
  calibration gap 4.8 -> 1.7 pts on both sides).
- **rm-1 moves the side mix toward Unders as a by-product** (median below mean): real lines Over-favoured 1,259 -> 1,013 of 2,692;
  on the passing cells kills Map 1 82 -> 58 of 150, HS Map 1 60 -> 47 of 127. This was not targeted and is not a reason to
  promote (owner rule), and it is exactly the correlated-Under exposure the owner warned about (UT-1: OT explains most Under
  losses after the fact). The forward review must report the side mix and Under-side calibration beside the gain.
- **th-1 is a level + spread correction for thin players** (Map 1: level x0.887 for < 50 rounds, x0.972 for 50-150; spread
  x1.20 / x1.11). It mainly lowers thin-player projections (they were over-projected by 1.24 kills on training rows) and widens
  their distribution. On real lines the 10 units were 9 Over-favoured, 2 went over; th-1 keeps 9 Over-favoured but at lower
  chances (63.1% -> 59.6%), so the gain is better probabilities on the same picks (no side flipped). Ten units cannot carry a
  decision; the forward cohort will also be small (see section 4).
- **Coverage**: identical by construction (every unit / row gets a chance from both arms; checked: replay of the logged live
  chance from the saved inputs differs by mean 0.0003, max 0.0008 - rounding of the logged numbers).

## 1. What was run (as registered)
- **Population (a)**: ED-1 `series.csv.gz` (2-year HLTV export rebuild, live formula: perf-2 Map 1 / Maps 1-3, cm-2 Maps 1-2
  with perf-2 fallback), scored 2025-07-01 .. 2026-10-21 in six fold-periods F1-F6; every fitted quantity from matches dated
  before the fold. Map 1 146,640 player-series / 14,664 m; Maps 1-2 134,015 / 13,480; Maps 1-3 53,685 / 5,408.
- **Rounds**: `rounds_archive.sqlite`, 46,518 window maps (46,516 from validated archive sequences incl. recovered overtime,
  2 from the export map score; the two agree on all 46,516 where both exist). Per-fold pmf from the 365 days before the fold.
  Final (frozen) fit: 26,156 maps 2025-10-22 .. 2026-10-21, OT share q = 11.35%; OT total rounds 28 / 29 / 30 = 14.5 / 25.2 /
  30.5% of OT maps, double OT 34-36 = 21.2%, triple+ 8.5%.
- **Fitted rho** (kills sd given rounds, as a ratio of the live c): 0.795-0.805 Map 1, 0.80-0.82 cm-2, 0.83-0.84 perf-2 Maps 1-2,
  0.77-0.78 Maps 1-3 across folds (stable). The total variance stays close to live; the difference is where it sits.
- **Real lines**: the 2,692 P1 units / 155 matches (start < 2026-10-22), live = the logged chance; F6 fit (data < 2026-10-01).
  th-1 real-line cohort: 81 units / 23 m (perf-2 Maps 1-2 D0 27, D1 37, Map 1 D1 14, cm-2 D1 3; no saved cm-2-ineligible
  fallback and no BO3.gg unit under 150 rounds before the cutoff).

## 2. rm-1 results
HLTV, paired gain in log score of the integer actual (+ = rm-1 better):

| stat | scope | rows / matches | pooled 95% | Bonferroni 99.58% | fold-periods F1..F6 (all intervals) | G1 | G2 |
|---|---|---|---|---|---|---|---|
| kills | Map 1 | 146,640 / 14,664 | +0.0423 [+0.0271, +0.0625] | [+0.0248, +0.0702] | +0.059 / +0.025 / +0.047 / +0.029 / +0.054 / +0.028, all lower bounds > +0.015 | pass | pass |
| kills | Maps 1-2 | 134,015 / 13,480 | +0.0162 [+0.0144, +0.0181] | [+0.0137, +0.0192] | +0.020 / +0.013 / +0.017 / +0.015 / +0.016 / +0.016 | pass | pass |
| kills | Maps 1-3 | 53,685 / 5,408 | +0.0166 [+0.0147, +0.0187] | [+0.0138, +0.0199] | +0.019 / +0.012 / +0.015 / +0.017 / +0.021 / +0.014 | pass | pass |
| HS | Map 1 | 146,581 / 14,664 | +0.0077 [+0.0069, +0.0085] | [+0.0066, +0.0090] | +0.0075 .. +0.0089 | pass | pass |
| HS | Maps 1-2 | 133,914 / 13,480 | +0.0042 [+0.0037, +0.0047] | [+0.0035, +0.0049] | +0.0035 .. +0.0063 | pass | pass |
| HS | Maps 1-3 | 53,614 / 5,408 | +0.0051 [+0.0045, +0.0057] | [+0.0042, +0.0060] | +0.0046 .. +0.0071 | pass | pass |

Subgroups (G4, pooled folds): by depth every band positive except **< 50 weighted rounds: kills Maps 1-2 -0.0079 [-0.0185,
+0.0041], HS Maps 1-2 -0.0048 [-0.0086, -0.0005] (worse), HS Maps 1-3 -0.0053 [-0.0104, -0.0004] (worse)**; by tier (Elo fifths)
no subgroup worse. Unregistered screen (reported, not a gate): the perf-2 fallback rows of Maps 1-2 are worse under rm-1
(kills -0.0305 [-0.0427, -0.0187], HS -0.0101, 3,017 rows / 1,151 m) - their level is wrong (ED-1: -3.0 kills), so a skewed
shape around a wrong mean hurts; this is th-1's territory and Maps 1-2 is not in rm-1's shadow anyway.

HLTV synthetic line floor(projection) + 0.5 (log loss / Brier gain; calibration = |mean favoured chance - hit rate|):

| stat | scope | d log loss | over rate | mean chance live -> rm-1 | Over-fav gap live -> rm-1 | Under-fav gap live -> rm-1 | PIT 50/80/95 live -> rm-1 |
|---|---|---|---|---|---|---|---|
| kills | Map 1 | +0.0040 [+0.0035, +0.0045] | 45.2% | 50.0 -> 46.9 | 4.8 -> 1.7 | 4.8 -> 1.7 | 52.2/81.9/95.4 -> 50.0/79.9/95.1 |
| kills | Maps 1-2 | +0.0013 [+0.0009, +0.0017] | 47.4% | 50.0 -> 47.6 | 2.7 -> 1.5 | 2.5 -> 0.1 | 51.8/81.2/95.0 -> 50.4/80.0/94.9 |
| kills | Maps 1-3 | -0.0001 [-0.0007, +0.0005] | 49.0% | 50.0 -> 47.8 | 1.1 -> 4.0 (n.s.) | 0.8 -> 1.2 | 51.8/81.8/95.2 -> 49.6/79.8/94.8 |
| HS | Map 1 | +0.0014 [+0.0011, +0.0016] | 45.1% | 47.9 -> 46.1 | 3.0 -> 1.5 | 2.7 -> 0.9 | 52.4/82.7/96.2 -> 50.9/81.4/95.9 |
| HS | Maps 1-2 | +0.0000 [-0.0001, +0.0002] | 47.4% | 48.1 -> 46.9 | 1.0 -> 0.1 | 0.5 -> 0.6 | |
| HS | Maps 1-3 | -0.0003 [-0.0006, -0.0001] | 48.8% | 48.3 -> 47.3 | 0.8 -> 2.4 (worse) | 0.4 -> 1.4 (worse) | |

Maps 1-3 has a +0.6 kills level bias (ED-1: a third map selects close, long series); rm-1 keeps the mean, so moving the median
down there makes the line chances worse - consistent with the gamma over-correction ED-1 saw. Dependence: Spearman of PIT(Map 1)
vs PIT(Maps 1-2) on the same 134,015 player-series 0.7064 live, 0.7065 rm-1 (registered tolerance 0.02).

Real lines (P1, all 2,692 units; logged live chance):

| set | units / matches | d log loss | d Brier | over rate | favoured hit live / rm-1 | sides flipped | Over-favoured live / rm-1 |
|---|---|---|---|---|---|---|---|
| all | 2,692 / 155 | +0.0004 [-0.0024, +0.0030] | +0.0002 [-0.0011, +0.0015] | 48.2% | 51.2 / 51.5 | 246 | 1,259 / 1,013 |
| kills Map 1 | 150 / 19 | +0.0094 [-0.0036, +0.0212] | +0.0046 | 39.3% | 46.0 / 50.0 | 24 | 82 / 58 |
| kills Maps 1-2 | 1,233 / 136 | -0.0002 [-0.0040, +0.0038] | -0.0001 | 48.9% | 51.4 / 51.5 | 145 | 612 / 467 |
| kills Maps 1-3 | 20 / 2 | -0.0280 [-0.0433, -0.0127] | -0.0134 | 75.0% | 35.0 / 30.0 | 3 | 8 / 5 |
| HS Map 1 | 127 / 19 | +0.0000 [-0.0076, +0.0076] | +0.0001 | 48.0% | 42.5 / 38.6 | 13 | 60 / 47 |
| HS Maps 1-2 | 1,142 / 136 | +0.0004 [-0.0014, +0.0021] | +0.0002 | 48.2% | 52.8 / 53.6 | 61 | 493 / 432 |
| passing cells (k1, k123, h1) | 297 / 21 | +0.0029 [-0.0073, +0.0125] | +0.0015 [-0.0034, +0.0061] | | | | |

Real-line subgroups (scope, stat, depth, tier, source): no interval entirely below 0. Calibration by favoured side (all units):
Over-favoured gap 7.1 -> 6.5, Under-favoured 2.8 -> 3.8 (+1.0 [-1.0, +1.9], not significant). Maps 1-3 real lines are 2 matches -
no evidence either way (the registered per-cell real-line requirement applies from 100 units / 15 matches).

## 3. th-1 results
Fitted per fold (kills only; HS follows through the kills pmf). Final fit (all data <= 2026-10-21), as (a = log level, s = log
spread): Map 1 D0 (-0.120, +0.185), D1 (-0.029, +0.101); Maps 1-2 perf-2 D0 (-0.078, +0.228), D1 (-0.009, +0.108); cm-2 D0
(+0.007, +0.154), D1 (+0.011, +0.088); F fallback (-0.123, +0.259); Maps 1-3 D0 (+0.038, +0.136), D1 (+0.039, +0.043).
Signs are stable across folds (Map 1 D0 a from -0.05 in F1 to -0.12 in F6; spreads always > 0).

| stat | scope | cohort rows / matches | pooled 95% | F1..F6 | G1 | G2 |
|---|---|---|---|---|---|---|
| kills | Map 1 | 12,974 / 3,894 | +0.0309 [+0.0257, +0.0361] | +0.023 / +0.015 / +0.036 / +0.030 / +0.046 / +0.034, all lower > 0 | pass | pass |
| kills | Maps 1-2 | 10,886 / 3,371 | +0.0482 [+0.0393, +0.0581] | F2 +0.0129 [-0.0026, +0.0301] | FAIL | pass |
| kills | Maps 1-3 | 3,490 / 1,122 | +0.0291 [+0.0132, +0.0448] | F2-F4 cross 0 | FAIL | pass |
| HS | Map 1 | 12,969 / 3,893 | +0.0117 [+0.0084, +0.0150] | F2 -0.0006 [-0.0073, +0.0061] | FAIL | pass |
| HS | Maps 1-2 | 10,879 / 3,370 | +0.0210 [+0.0157, +0.0263] | F2 +0.0016 [-0.0082, +0.0123] | FAIL | pass |
| HS | Maps 1-3 | 3,487 / 1,122 | +0.0121 [+0.0042, +0.0201] | F1-F4 cross 0 | FAIL | pass |

Within the cohort the gain is concentrated in D0 (< 50 rounds: kills Map 1 +0.052, Maps 1-2 +0.089) and the cm-2-ineligible
fallback (Maps 1-2 perf-2 rows +0.142); D1 (50-150) is small (kills Map 1 +0.007 [+0.002, +0.013]; HS D1 slightly negative,
not significant). Check of the perf-2 Maps 1-2 bands (the pre-10-20 / BO3.gg route): +0.047 [+0.038, +0.055] (10,876 / 3,370).
Synthetic line, kills Map 1 cohort: over rate 39.6% vs live 50.0% / th-1 45.7%; Over- and Under-favoured calibration gaps
10.6 -> 6.1 and 10.2 -> 6.1; PIT 50/80/95 45.1/75.8/93.3 -> 50.4/81.1/95.7.
Real lines: whole cohort 81 / 23 m +0.0521 [+0.0214, +0.0774] (kills 42 u +0.0705, HS 39 u +0.0322); passing cell (kills Map 1)
**10 units / 5 m +0.0682 [+0.0305, +0.0976]**, Brier +0.0314, no side flipped, no subgroup worse.

**Against FACTOR_AUDIT rounds 4-5** (rejected on N0-era data): R4-thin widened the spread only (r_w < 600, Maps 1-2 perf-2,
projection unchanged, export from 2025-10-02 with truncated history) and found +0.0008; rd 5 C1 strengthened the shrink (MAE
+0.0016) and found thin players NOT over-projected in that window. What is different here: th-1 moves the LEVEL and the spread
together, only below 150 rounds, with untruncated 420-day histories from the 2-year export, on all scopes and on cm-2 / its
fallback. The level, not the spread, is where the Map 1 cohort gain comes from (D0 a = -0.12: thin players score 1.2 kills less
than projected on training rows). The older null results were on a different cohort (< 600) and a history revision where r_w
was truncated, so they do not contradict this; they do say the < 600 widening alone was worthless, which is consistent with D1
being small here.

## 4. Forward rule (frozen in CHALLENGERS.md on branch shadow/cs2-cands)
**[Amended 2026-10-23: A2 decision level (one-sided 0.025 / 6 per look, not 95%), A3 th-1 look counter = its kills Map 1 cell, the 60 counts cohort matches - AMENDMENT_2026-10-23.md; text below kept]**
Implemented on branch `shadow/cs2-cands` (commit c1293dd, from origin/main 5faaaf9; NOT merged, NOT deployed): `cands_shadow.py`
(frozen 2026-10-23 07:45 UTC, constants sha256 3d00b8c8...), hooked into `picklog._factors` as `forecasts.shadow.cands`,
copied / reported by `learning.cands_report` (scorecard.product.candidates_2y), tests/test_cands_shadow.py; full suite 1,412
passed. Parity with this folder's code on the 2,692 development units: max |difference| 5e-7.
Frozen config = `candidates/results/params_FINAL.json` (sha256 `66a39c0c20ef2e35...`) for exactly the passing cells; logged per
forecast as shadows `rm1` / `th1` (raw chances stored apart from any recommendation; live grades byte-identical). Forward set =
P1 units of matches starting at/after the shadow deploy (and >= 2026-10-22). Looks only at 300 / 600 / 1,000 distinct forward
matches with >= 1 eligible logged unit. Promotion requires forward real-line paired log-loss gain interval > 0 over the cells,
Brier point >= 0, favoured-side calibration not worse (|gap| no more than 1.0 pt worse and not significantly worse), coverage
identical; the Over / Under mix is reported, never a reason; the owner decides. th-1 additionally needs >= 60 distinct cohort
matches at a look; at ~10 Map 1 thin units per 5 matches in 11 days of P1, that may not happen before the 1,000-match look - if
so th-1 stays a shadow and is reported as "not enough forward evidence".

Expectation stated now (so it cannot be re-read later): rm-1's real-line effect in development is ~0 (interval +-0.01 on 297
units); **[Superseded 2026-10-23: C5]** a forward pass at 300 matches is unlikely unless Map 1 / Maps 1-3 lines become common. Its value is the corrected
distribution shape (calibration near the projection, PIT, tails) - which the forward rule does not reward by itself.

## 5. Reproduce / resume
```
cd research/cs2_accuracy_2y/candidates
S=<scratchpad>/cs2acc            # needs ED-1 checkpoints (ed1/series.csv.gz, ed1/p1.csv.gz), codex_export, data/rounds_archive.sqlite, under/p1_fc.json
python run_hltv.py $S            # ~13 min; per-fold checkpoints cands/params_<fold>.json + cands/hltv_<fold>_<scope>.csv.gz,
                                 #   existing files are skipped (resume = re-run the same command); FINAL = frozen fit only
python run_p1.py   $S            # real lines (F6 fit) -> cands/p1_cands.csv.gz
python report.py   $S            # gate + tables -> cands/report.json, cands/tables.md (copied to candidates/results/)
```
No .pkl is read; no production endpoint or database was contacted.
