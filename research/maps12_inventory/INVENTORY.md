# CS2 Maps 1-2 accuracy inventory (2026-10-23, read-only)

Owner priority 2026-10-23: predictive accuracy on CS2 **Maps 1-2** (kills and headshots). This file lists every existing
Maps 1-2 challenger, its frozen definition, its forward record on real offered lines paired against the live board, and the
live model's own prospective Maps 1-2 record; then it names the single next justified step. Nothing was changed: no code,
no model, no parameter search, no grading change. rm-1 / th-1 do not cover Maps 1-2 (their Maps 1-2 cells FAILED the
development gate) and nothing in their forward record is evidence about the Maps 1-2 board.

## 0. Data, cutoff and definitions

- **Source (production, bounded reads only):** the learning cycle's stored P1 export `/admin/learning/p1_units.json`
  (paged; built 2026-10-23 **18:41:06 UTC**; 3,001 units, all scopes) - every settled P1 unit with each challenger's STORED
  value from the learning `shadow` table (never recomputed); the P1 forecasts themselves from `/admin/forecasts_page.json`
  (no inputs, 2,000-row pages, cutoff id 83732) for the live projection source (`shadow.old_spread.source`, equal to the live
  projection on 1,871 / 1,871 Maps 1-2 units) and the picklog shadows rr-1 / prior_smooth; `/admin/learning.json` (cached
  dashboard of the same cycle) for registry, freeze times and review counters. Analysis scripts (not committed):
  scratchpad `veto/m12inv/analyze.py`, `tables.py`; outputs `results.json`, `table_pairs.md`, `table_live.md`.
- **P1 unit** (learning.PRODUCT, EVALUATION.md section 8): one unit per player x canonical match x stat x maps; the latest
  STANDARD board forecast logged >= 30 min before the start; board-line pushes excluded; voids / unresolved apart. Here:
  scope Maps 1-2 only.
- **Forward (prospective) unit for a challenger:** its value exists in the learning `shadow` table (only filled for
  forecasts logged after the challenger's `frozen_at`) AND the unit's start is after `frozen_at`. rr-1: `shadow.market_rounds`
  status ok, freeze 2026-10-13 17:51 UTC, P1 forecasts with id >= 40,000 (starts 2026-10-16 .. 10-23). prior_smooth: only
  rows whose `source` ends with `-prior-smooth` (CHALLENGERS.md exclusion rule).
- **"vs published"** = paired against the logged published chance (P(Over | no push) after caps, 3 decimals) and the
  published projection of the same forecast. **"vs record arm"** = against the live arm saved in the challenger's pre-match
  fh-1 record (what the learning dashboard's `gain` uses for cm-1 / cm-2 / fh-1 / hs-1 / lc-1).
- **Live eras (by the P1 forecast's model tag):** kills `cm2-era` = tags 8404c86d3c / 794a775f51 / f663af8917 / aa6696d5d6
  (first P1 forecast 2026-10-20 01:17:35 UTC; kills Maps 1-2 published by perf-cm2 on 541 of 559 units, perf-2-bo3 on 18);
  HS `hscm2-era` = f663af8917 / aa6696d5d6 (first P1 forecast 2026-10-22 02:45:41 UTC; perf-cm2 on 127 of 133, perf-2-bo3
  on 6). Earlier units were published by perf-2 / perf-2-bo3.
- **Statistics:** gain = live minus challenger (positive = challenger better), unit-weighted; 95% percentile intervals of a
  match-clustered bootstrap (B = 2,000, seed 20261023, clusters = the export's canonical match key). Log loss / Brier on the
  decided outcome. Favoured-side calibration = for each side the arm favours, mean favoured chance ("printed") vs the share
  that won. These are interim readings: no challenger has reached a registered review window, and the registered review
  level is 98.33% (REVIEW_RULE, 3-look Bonferroni), not the 95% shown here.

## 1. Inventory of Maps 1-2 challengers

Review counter = the learning registry's own distinct-forward-match count (all scopes, its registered forward set),
as shown in `/admin/learning.json` 18:41 UTC. Every review is at 300 / 600 / 1,000 matches (REVIEW_RULE), pass =
"eligible for the owner's review" only.

| challenger | frozen definition (exact source) | frozen / version | logged in | Maps 1-2 cells | review counter (to 300) | development (pre-registration) evidence | status |
|---|---|---|---|---|---|---|---|
| **cm-1** `cm-001-4e5f42fb` | 14-feature Poisson GLM, full HLTV history, discretised normal sd 1.4732 sqrt(lambda), kills cap 22-78%; HS = lambda x full-history HS ratio, c_hs 1.2781 (cm1_model.json sha 2799a7f4a1b545e1; CHALLENGERS.md) | artifact frozen 2026-10-16 22:08; registry clock 22:35:14 UTC | fh-1 pre-match record `rec["cm1"]` (picks.db shadow_forecasts) | kills, HS | 95 | Real lines Oct 3-16 (1,927 obs / 149 m, kills+HS pooled): log loss +0.0120 (+0.0032, +0.0203) vs rebuilt live; kills +0.0138, HS +0.0099; 57% rule 58.8% (53.6-64.4) - all periods previously inspected (REALLINE_ARCHIVE_2026-10-16.md s.9) | shadow |
| **cm-2** `cm-002-9f1db397` | cm-1 features on corrected tables (complete scoreboards, unknown env never online/LAN), GLM refit through 2026-09-30, sd 1.4757 sqrt(lambda) (cm2_model.json canonical sha 6e586047...) | registered 2026-10-18 21:55:07 UTC | fh-1 record `rec["cm2"]` | kills only | 75 | Strict book lines Oct 11-16: kills log loss 0.7016 -> 0.6852; real-line eligible kills +0.0159 (+0.0058, +0.0264); HLTV MAE 6.238 -> 6.101 (retrospective) | **promoted by owner override 2026-10-20 (perf-cm2), before its review**; shadow + clock kept |
| **hs-cm2** (not a learning challenger) | HS Maps 1-2 on cm-2 kills + cm1.hs_ratio (k_hs 25) under the live HS beta-binomial (kappa 100); nothing fitted | protocol e7e5e12 (2026-10-21), result c83d446 | live grade (perf-cm2 HS) | HS | n/a | Registered hold-out (matches 2026-10-17 .. 10-21 18:57, reconstructed from saved forecasts): HLTV-wide HS MAE +0.063 (+0.020, +0.105) - mostly players with no N0 history (+0.015 n.s. for the rest); P1 580 lines / 70 m log loss +0.0049 (-0.0013, +0.0112) (research/hs_cm2_2026_10_21/RESULT.md) | **live since f663af8917 (shipped 2026-10-21 19:18 UTC; first P1 forecast 10-22 02:45)** |
| **fh-1** `fh-001-ba7806a4` | live perf-2 formula on full HLTV history instead of N0 (perf_shadow.py) | 2026-10-15 18:39:28 UTC | picks.db shadow_forecasts (own table) | kills, HS (+ Map 1 / Maps 1-3) | 123 | HLTV 12 months: kills MAE +0.131 (+0.118, +0.143); archived book lines (41 m) +0.0015 (-0.0054, +0.0101) (ACCURACY_2026-10-15.md) | shadow |
| **pl-2** `pl-002-b5b076b5` | line-anchored location m + w (lam_H - m), w kills 0 / HS 0.25, live sd; anchors only from `forecasts.quote.anchors` with eligibility rules | 2026-10-15 03:29:05 UTC | learning shadow (from logged anchors) | kills, HS | 135 | pl-1's development only: 521 units / 30 m walk-forward, pl-1 vs live +0.0073 (-0.0107, +0.0244); market baseline beat pl-1 -0.0113 (-0.0228, -0.0012); HS w weakly identified | shadow |
| pl-1 `pl-001-bb351ec5` | as pl-2 with uncorrected anchors | 2026-10-15 02:22:18 | learning shadow | kills, HS | n/a | as above | **blocked** (anchor defects), logged for pl-2 vs pl-1 only |
| **hs-1** `hs-001-9c8f70d8` | logistic a + b z on fh-1's full-history HS projection, a -0.15249, b 1.01202, c_hs 1.264 | defined 6e7dc53 20:50:42; registry 2026-10-16 21:18:34 UTC | from fh-1 records | HS only | 94 | 1,286 HS lines / 159 m: +0.0025 vs BASE (-0.0026, +0.0083); day refits unstable (REALLINE_ARCHIVE s.6) | shadow |
| **lc-1** `lc-001-c80c6b2e` | logit P = a + b z on the live projection, ridge 100 (kills -0.02688, -0.00717; HS -0.04084, +0.00771) | 2026-10-16 19:05:48 UTC | fh-1 record `rec["lc1"]` | kills, HS | 103 | +0.0132 (-0.0022, +0.0292) vs live but -0.0004 vs the line baseline; collapses to ~49% Over (ACCURACY_2026-10-16.md) | shadow; owner: no further development |
| **lb-001** `lb-001-c9b0fc42` | P = Phi(w Phi^-1(p_live)), w kills 0 (= 50%), HS 0.5 | 2026-10-12 15:49:04 UTC | learning shadow | kills, HS | 183 | Oct 3-12 walk-forward kills +0.0301 (+0.0077, +0.0528), HS +0.0048 (-0.0062, +0.0159) | shadow |
| ch-009 `ch-009-d2e37e88` | perf-refit-1 trained refit of perf-2 parameters (k0 200, prior_m 0.88, ke 1600, k_hs 100, c_mult 1.2, kappa 50, base_r 21.6) | 2026-10-21 19:23:09 UTC | learning shadow | perf-2-graded lines only (now mostly not the live model) | 21 | in-cycle time-ordered validation only | shadow |
| rr-1 (`rr-ks`) | market-informed rounds on perf-2's lambda (FACTOR_AUDIT round 6, perf_market.py) | 2026-10-13 17:51 UTC | `forecasts.shadow.market_rounds` (picklog), not in the learning registry | kills, HS | none scheduled in learning | Round-6 historical frozen test PASS: TEST Maps 1-2 kills MAE -0.0535 (-0.0774, -0.0337), 996 m | shadow |
| prior_smooth | perf-2 with prior target m(r) | valid rows from 23bea2fe37 (2026-10-14 22:53:13) | `forecasts.shadow.prior_smooth` | kills, HS on perf-2 grades | none | - | shadow (perf-2 lines only) |
| lm-0 `lm-0/2026-10-23` | grade_leg with the old-pipeline Over margin set to 0 | 2026-10-23 06:08 UTC | `forecasts.shadow.lm0` | legacy-pipeline grades only | 0 eligible units (2,945 not logged, 56 perf-cm2) | none | shadow; minimum 30 m / 60 units never reached so far |
| rm-1 / th-1 | rounds mixture / thin correction | 2026-10-23 07:45 UTC | `forecasts.shadow.cands` | **NOT Maps 1-2**: Maps 1-2 failed development (rm-1 kills Maps 1-2 real-line -0.0002 [-0.0040, +0.0038] on 1,233 u / 136 m, G3; HS Maps 1-2 G4; th-1 Maps 1-2 G1) | 0 eligible of 56 forward P1 units (all Maps 1-2) | CANDIDATES.md | out of scope - never evidence for the Maps 1-2 board |
| perf-3 | lambda3 = exp(a) lambda2^b exp(g (p_elo - .5)) | registered 2026-10-20 | not logged | - | - | FAILED its hold-out: kills Maps 1-2 MAE -0.028 (-0.043, -0.013) (research/proj_fix_2026_10_20) | rejected |

## 2. Paired forward results vs the published live chance (same P1 units)

Selected rows; the full table (both pairings, every era, both calibration sides) is in Appendix A.
"Fav side" = n units, printed / won (%), Over-favoured (O) and Under-favoured (U).

### 2a. Kills Maps 1-2

| challenger | cohort | units / matches | log-loss gain [95%] | Brier gain [95%] | bias live / ch (kills) | MAE live / ch [gain 95%] |
|---|---|---|---|---|---|---|
| cm-1 | all forward | 825 / 94 | **+0.0046 [+0.0007, +0.0089]** | +0.0022 [+0.0003, +0.0044] | -0.69 / -0.63 | 6.55 / 6.51 [+0.006, +0.080] |
| cm-1 | pre-cm2 (vs perf-2) | 271 / 32 | +0.0110 [-0.0004, +0.0220] | +0.0054 [-0.0001, +0.0108] | -0.36 / -0.25 | 6.29 / 6.18 [+0.004, +0.199] |
| cm-1 | cm2-era (vs perf-cm2) | 554 / 63 | +0.0014 [+0.0001, +0.0033] | +0.0007 [+0.0000, +0.0016] | -0.85 / -0.82 | 6.68 / 6.68 [-0.005, +0.025] |
| cm-2 shadow | pre-cm2 (vs perf-2) | 104 / 12 | +0.0089 [-0.0071, +0.0238] | +0.0043 [-0.0034, +0.0117] | +0.41 / +0.38 | 6.93 / 6.88 [-0.09, +0.19] |
| cm-2 shadow | cm2-era (vs itself live) | 554 / 63 | +0.0016 [+0.0004, +0.0036] (timing only: 539 / 554 within 0.001) | +0.0008 | -0.85 / -0.84 | 6.68 / 6.67 |
| cm-2 shadow | cm2-era vs record arm (= perf-2 replay) | 554 / 63 | +0.0016 [-0.0065, +0.0095] | +0.0006 [-0.0033, +0.0045] | -0.93 / -0.84 | 6.68 / 6.68 [-0.07, +0.09] |
| fh-1 | all forward | 981 / 108 | +0.0010 [-0.0050, +0.0068] | +0.0006 [-0.0022, +0.0034] | -0.69 / -0.52 | 6.50 / 6.51 [-0.067, +0.040] |
| pl-2 | all forward | 893 / 117 | +0.0032 [-0.0084, +0.0156] | +0.0014 [-0.0041, +0.0075] | -0.61 / -0.58 | 6.39 / 6.34 [-0.06, +0.15] |
| lc-1 | all forward | 848 / 94 | +0.0098 [-0.0033, +0.0240] | +0.0046 [-0.0018, +0.0115] | (live projection) | Under-favoured on 848 / 848 |
| lb-001 (= 50%) | all forward | 1,269 / 141 | +0.0055 [-0.0058, +0.0169] | +0.0025 [-0.0030, +0.0080] | - | - |
| lb-001 (= 50%) | cm2-era | 559 / 63 | +0.0128 [-0.0049, +0.0323] | +0.0060 [-0.0025, +0.0154] | - | - |
| rr-1 | all forward | 532 / 60 | **+0.0108 [+0.0019, +0.0202]** | +0.0053 [+0.0009, +0.0098] | -1.29 / -1.10 | 6.58 / 6.47 [+0.03, +0.21] |
| rr-1 | cm2-era (Oct 22-23 only, see note) | 150 / 18 | +0.0162 [+0.0028, +0.0290] | +0.0080 | -3.70 / -3.47 | 7.84 / 7.71 |
| ch-009 | all forward (Oct 22-23) | 156 / 18 | +0.0012 [+0.0000, +0.0028] (151 / 156 identical) | +0.0006 | -3.48 / -3.51 | 7.70 / 7.69 |
| prior_smooth | all valid | 441 / 55 | +0.0006 [-0.0013, +0.0024] | +0.0003 | -0.45 / -0.32 | 6.16 / 6.15 |

### 2b. Headshots Maps 1-2

| challenger | cohort | units / matches | log-loss gain [95%] | Brier gain [95%] | bias live / ch (HS) | MAE live / ch [gain 95%] |
|---|---|---|---|---|---|---|
| cm-1 | all forward | 737 / 91 | +0.0009 [-0.0062, +0.0069] | +0.0004 [-0.0031, +0.0034] | -0.29 / -0.24 | 4.12 / 4.12 [-0.03, +0.04] |
| cm-1 | pre-hscm2 (vs perf-2) | 605 / 74 | -0.0004 [-0.0088, +0.0072] | -0.0002 | -0.06 / +0.01 | 4.04 / 4.03 |
| cm-1 | hscm2-era (vs hs-cm2) | 132 / 17 | +0.0069 [-0.0016, +0.0154] | +0.0033 | -1.35 / -1.36 | 4.49 / 4.49 |
| fh-1 | all forward | 888 / 107 | -0.0008 [-0.0050, +0.0032] | -0.0004 [-0.0025, +0.0016] | -0.28 / -0.18 | 4.02 / 4.02 [-0.03, +0.02] |
| fh-1 | hscm2-era | 133 / 17 | +0.0113 [+0.0039, +0.0201] | +0.0054 [+0.0019, +0.0098] | -1.34 / -1.26 | 4.45 / 4.38 [+0.02, +0.12] |
| pl-2 | all forward | 826 / 116 | +0.0027 [-0.0064, +0.0121] | +0.0013 [-0.0031, +0.0058] | -0.15 / -0.19 | 3.87 / 3.86 [-0.04, +0.07] |
| hs-1 | all forward | 766 / 93 | +0.0008 [-0.0054, +0.0075] | +0.0004 [-0.0026, +0.0036] | -0.23 / -0.13 | 4.11 / 4.11 [-0.04, +0.03] |
| lc-1 | all forward | 762 / 93 | -0.0007 [-0.0127, +0.0123] | -0.0004 | (live projection) | Under-favoured on 762 / 762 |
| lb-001 (w 0.5) | all forward | 1,156 / 138 | +0.0016 [-0.0038, +0.0070] | +0.0008 | - | - |
| rr-1 | all forward | 815 / 100 | +0.0040 [-0.0016, +0.0094] | +0.0019 [-0.0007, +0.0046] | -0.27 / -0.18 | 4.01 / 4.00 [-0.02, +0.05] |
| rr-1 | hscm2-era | 127 / 17 | +0.0153 [+0.0030, +0.0285] | +0.0074 | -1.45 / -1.36 | 4.52 / 4.43 |
| ch-009 | all forward | 133 / 17 | -0.0011 [-0.0047, +0.0019] | -0.0006 | -1.34 / -1.36 | 4.45 / 4.46 |
| prior_smooth | all valid | 762 / 93 | +0.0000 [-0.0010, +0.0011] | +0.0000 | -0.09 / -0.02 | 3.93 / 3.93 |

**Reading.** No Maps 1-2 challenger has an interval above 0 on both stats, none is near a review window, and the only
intervals above 0 are interim 95% peeks:
- cm-1 kills +0.0046 [+0.0007, +0.0089]: two-thirds of the effect is the pre-cm2 period (cm-1 vs perf-2, +0.0110, 32 m); against
  the current live model (perf-cm2) it is +0.0014 on 63 matches - cm-1 and cm-2 are close relatives (220 of 554 cm2-era
  units within 0.001). Its registered look is at 300 matches (now 95) at 98.33%.
- cm-2 vs its own live copy is not a test (the difference is when the record vs the forecast was written).
- rr-1 kills +0.0108 / HS cm2-era +0.0153: rr-1 is built on **perf-2's** lambda, not cm-2's, and was **not logged on any
  perf-cm2 kills forecast on tags 8404c86d3c / 794a775f51** (Oct 20-21: 403 P1 kills units without an rr-1 value; logging
  resumed with f663af8917). Its cm2-era cohort is Oct 22-23 only, the two days with the largest outcome shock (below). It has
  no learning-registry entry and no scheduled review.
- The cm2-era / hscm2-era HS gains of fh-1 / rr-1 / cm-1 (17 matches, Oct 22-23) share one feature: every arm with a higher HS
  level looks better on those two days.

**Outcome shock, all arms alike.** Live perf-cm2 kills bias by start day: Oct 20 +0.63 (181 u / 21 m), Oct 21 -0.20
(210 / 22), **Oct 22 -4.04 (121 / 15; Over rate 68.6%)**, Oct 23 -2.28 (29 / 3). Every challenger's bias on the same units is
within about 0.2 kills of live's (table 2a/2b), so the recent misses are a correlated high-kill day, not a defect of one
projection - the same pattern as the Oct 6 upset day (memory: under-lean = correlated upset risk). perf-3 (a level fix)
already FAILED its hold-out; no level correction is indicated.

## 3. The live model's own prospective Maps 1-2 record (same P1 rule)

Prospective = P1 units whose forecast was published by the model after it went live. perf-cm2 kills went live by owner
override (not a passed review); hs-cm2 went live after passing its registered rule. Neither number below was available
when the decision was made.

| live cohort | units / matches | starts (UTC) | Over rate / mean P(Over) | log loss; gain vs constant 50% [95%] | Brier | bias [95%] | MAE proj / line; line minus proj [95%] | fav side O / U: n printed / won | 57% rule: picks, printed / won [95%] |
|---|---|---|---|---|---|---|---|---|---|
| **kills perf-cm2** | 541 / 61 | 10-20 03:00 .. 10-23 13:00 | 52.5% / 50.4% | 0.7044; -0.0113 [-0.0299, +0.0060] | 0.2553 | -0.89 [-2.55, +0.64] | 6.76 / 6.66; -0.110 [-0.269, +0.048] | O 270: 56.0 / 54.4; U 271: 55.2 / 49.5 | 169, 61.0 / 49.1 [40.3, 57.8] |
| kills cm2-era, perf-2-bo3 fallback | 18 / 10 | | 38.9% / 47.6% | 0.7516; -0.0584 [-0.1246, +0.0039] | 0.2780 | +0.38 | 3.91 / 3.00 | O 7: 57.4 / 28.6; U 11: 58.6 / 54.5 | 9, 63.1 / 44.4 |
| **HS hs-cm2 (perf-cm2)** | 127 / 17 | 10-22 11:00 .. 10-23 13:00 | 57.5% / 46.0% | 0.7059; -0.0128 [-0.0522, +0.0227] | 0.2566 | -1.45 [-3.23, +0.38] | 4.52 / 4.51; -0.011 [-0.207, +0.177] | O 32: 54.1 / 68.8; U 95: 56.7 / 46.3 | 42, 61.8 / 47.6 [30.0, 69.0] |
| reference: kills perf-2, starts >= 10-15 | 518 / 57 | 10-15 .. 10-20 12:00 | 50.4% / 49.1% | 0.6946; -0.0015 [-0.0163, +0.0140] | 0.2506 | -0.66 | 6.30 / 6.27 | O 212: 55.6 / 53.3; U 306: 55.4 / 51.6 | 164, 60.5 / 56.1 [47.2, 64.0] |
| reference: HS perf-2, starts >= 10-15 | 846 / 101 | 10-15 .. 10-21 18:00 | 48.4% / 49.0% | 0.6891; +0.0040 [-0.0077, +0.0164] | 0.2480 | -0.11 | 3.89 / 3.92 | O 346: 55.7 / 53.2; U 500: 55.6 / 55.0 | 266, 60.9 / 54.9 [48.3, 61.5] |

Favoured-side gap (printed minus won, match-clustered 95%): kills perf-cm2 Over +1.6 pt [-6.8, +10.0], Under +5.7 pt
[-4.6, +16.5]; HS hs-cm2 Over -14.6 pt [-31.4, +7.0], Under +10.4 pt [-5.1, +24.3]. On the offered lines neither live model
is shown to beat a constant 50% yet (both intervals include 0, on 61 and 17 matches), and the kills line was closer to the
result than the projection by 0.11 kills (interval includes 0). That is the same "no separation at real lines" result as
every earlier real-line study (Oct 14 checkpoint, REALLINE_ARCHIVE, ERROR_BREAKDOWN: about 85% of the error is not predictable
pre-match). It does not show perf-cm2 or hs-cm2 is worse than the perf-2 it replaced: the paired comparison against perf-2 on
the same units is the cm-2 shadow vs its record arm (+0.0016 [-0.0065, +0.0095], 63 m) and the hs-cm2 hold-out.

## 4. Single next justified accuracy step

**Step: register, before any Maps 1-2 challenger reaches its 300-match look, a documented correction of the live arm the
Maps 1-2 forward tests are judged against (a learning-report and documentation change only; no value, constant, cell or
live grade changes).** Evidence:

1. Every pre-match-record challenger (cm-1, cm-2, fh-1, lc-1, hs-1) is registered against "the record's live arm (the
   published chance)" (learning registry `base_config`). Since the cm-2 promotion (kills, 2026-10-20) and hs-cm2 (HS,
   2026-10-22 first P1 forecast) the record's live arm is the **perf-2 replay**, not the published chance. Mean |record arm
   - published| on Maps 1-2 P1 units: kills pre-cm2 **0.0000** (271 / 289 / 422 units for cm-1 / lc-1 / fh-1), kills cm2-era
   **0.036-0.037**; HS pre-hscm2 **0.0000**, hscm2-era **0.028**.
2. The registered review check "live replay reproduces the logged chances" (REVIEW_RULE replay_gap_max 0.01,
   learning.review_checks) therefore already fails today for cm-1 (0.0157), cm-2 (0.0306), fh-1 (0.0117) and lc-1 (0.0140),
   and will fail for hs-1 (0.0050 now) as hscm2-era units accumulate. At 300 matches each of these reviews would be "not
   passed" for a plumbing reason - and retired if its gain point is <= 0 - without having tested anything against the board
   members see. Conversely, a pass would mean "beats perf-2", not "beats the live Maps 1-2 model".
3. This is the same kind of issue the owner raised for rm-1 / th-1: a future result being read as validation of the main
   Maps 1-2 board when it does not compare against it. It is a documented defect introduced by the two promotions, found
   before any Maps 1-2 window was reached, so correcting it now moves no goalpost (as AM-1 did for rm-1 / th-1).

What the correction should say (to be written as its own amendment, frozen before the next look): (a) for cm-1, fh-1, lc-1,
hs-1, pl-2, lb-001: the primary pairing is the logged published chance, reported separately per live era (perf-2 vs
perf-cm2 / hs-cm2) and pooled only with that split shown; (b) for cm-2 (whose registered question after promotion is "cm-2
vs perf-2", CHALLENGERS.md): keep the record's perf-2 arm, and replace the replay check by "the record's live arm
reproduces the forecast's saved `perf2` numbers"; (c) rr-1: either register it in learning with its logging gap
(Oct 20-21 perf-cm2 kills) and its perf-2 base stated, or report it as descriptive only; (d) no other change. After it, the
first decision point is the registered 300-match look of fh-1 (123 now) / pl-2 (135) / cm-1 (95) - at the recent ~15-22
distinct P1 matches a day, roughly 1-2 weeks away - judged at 98.33% as registered.

**Why nothing else is justified now.**
- No challenger is at a review window (lb-001 183, pl-2 135, fh-1 123, lc-1 103, cm-1 95, hs-1 94, cm-2 75, ch-009 21 of
  300); all positive readings above are interim 95% peeks on 60-120 matches, several driven by Oct 22-23.
- cm-1's interim kills gain is mostly vs perf-2 before Oct 20; against perf-cm2 it is +0.0014.
- rr-1's interim gains sit on a perf-2 base, an incomplete cm2-era record and two shock days; it is not registered for review.
- lb-001 / lc-1 are 50%-type baselines (owner: no new ~50% challengers; lc-1 frozen with no further development); pl-1 is
  blocked; lm-0 has 0 eligible units; ch-009 is identical to live on 151 / 156 kills units.
- rm-1 / th-1: Maps 1-2 failed the development gate; not in scope.
- perf-3 failed its hold-out; the recent losses are a correlated outcome shock shared by every arm; no documented
  projection defect for Maps 1-2 is open. A new parameter search because recent picks lost is excluded by the owner's rule.
- Live grading stays unchanged: neither a validated improvement nor a documented grading correction exists.

## 5. Side check: settlement_lag warn (report only)

/health 18:49 UTC: `open_after_8h` 65 picks / 14 matches, oldest start 2026-10-20T22:00 (19:05 UTC: 66 / 15). Read-only,
bounded reads: `/admin/rule_pending.json` (complete, 1 page), forecast pages (no inputs, cutoff 83732), 1-row inputs reads for
a sample of the picks without a settled P1 unit, and SELECTs on the HLTV database. There is no bounded endpoint that lists open
picks (the unbounded /admin/picks.json was not used), so the 66 could not be enumerated one by one.

- **Not caused by the 2026-10-23 releases.** `/admin/rule_pending.json` returns **0 open picks** (searched_at 2026-10-23T16:52Z).
  Every 2026-10-23 hold - unpublished platform rule (forfeit / no-show / partial / remake), reschedule pending (36 h rule), and the
  LP -> BARCA lineup gate (`rename-lineup`) - writes `result.rule_pending` through `picklog._note_pending` (lines 1501, 1777,
  1783, 1970, 2047), so none of the open picks is held by those rules. LP -> BARCA itself works: hltv:2399805 (paiN vs BARCA,
  2026-10-21 14:00) has 20 settled P1 units.
- **What the open matches look like (sampled):**
  - hltv:2399823 (CCT South America Series 7, start 2026-10-20 22:00 = the oldest): HLTV has the full result (Gremio 2-0,
    10 players, both maps' HS), 5 P1 units settled; the sampled open picks are Gremio players graded on the BO3.gg route
    because HLTV does not know them by those names ("player 'balencyy' / 'souz4h' / 'MITHPUTTINI' / 'yeda' (Gremio) not found
    in HLTV"). The same team's hltv:2399907 (2026-10-22 22:00) has the same pattern. A player-name identity gap, not new.
  - hltv:2399331 (CCT Europe Series 11, start 2026-10-23 08:00, 29 sampled picks, 0 settled P1 units): **not in the HLTV
    database at all** (no overview, maps or player rows) - ordinary result latency (or a postponement not yet visible).
  - BO3.gg-only matches with canonical `cs2:` ids (no HLTV record): cs2:0db16736dd3e, cs2:bb97981fc843, cs2:c7af00c1b9c8,
    cs2:4cb9f560d8f3, cs2:5bda720442a3 - they settle only from BO3.gg scoreboards.
  - hltv:2399694 (CCT Challengers Europe 8, 2026-10-21 13:45) has `forfeited_maps = 1` (maps 1 and 3 played); it has no
    rule_pending note, so whatever open picks it holds are not waiting on the new forfeit rule.
- **Conclusion:** ordinary result latency plus pre-existing identity coverage (players HLTV lists under other names;
  BO3.gg-only matches). Nothing to change from this check.
- Note on load: one `/admin/diagnostics.json` read during this check triggered its 10-minute rebuild and timed out client-side
  (the next read returned the cached copy in 0.3 s); container memory stayed at its limit with page cache reclaimed
  (memory.max events 12,055 -> 26,477, 0 OOM kills, 0 5xx).

## Appendix A - every pairing (stored challenger values, P1 Maps 1-2, export 2026-10-23 18:41:06 UTC)

"published" = vs the logged published chance; "record arm" = vs the live arm of the challenger's own pre-match record.
Fav side columns: n units, printed / won (%).

| challenger | vs | stat | cohort | units / matches | log-loss gain [95%] | Brier gain [95%] | bias live / ch | MAE live / ch [MAE gain 95%] | fav side live: n printed/won | fav side ch: n printed/won |
|---|---|---|---|---|---|---|---|---|---|---|
| cm-1 | published | kills | all | 825 / 94 | +0.0046 [+0.0007, +0.0089] | +0.0022 [+0.0003, +0.0044] | -0.69 / -0.63 | 6.55 / 6.51 [+0.0059, +0.0797] | O 387: 55.9/54.3; U 438: 55.3/51.1 | O 403: 55.7/54.3; U 422: 54.9/51.4 |
| cm-1 | published | kills | cm2-era | 554 / 63 | +0.0014 [+0.0001, +0.0033] | +0.0007 [+0.0000, +0.0016] | -0.85 / -0.82 | 6.68 / 6.68 [-0.0051, +0.0253] | O 276: 56.1/54.0; U 278: 55.3/49.3 | O 277: 56.2/53.8; U 277: 55.1/49.1 |
| cm-1 | published | kills | pre-cm2 | 271 / 32 | +0.0110 [-0.0004, +0.0220] | +0.0054 [-0.0001, +0.0108] | -0.36 / -0.25 | 6.29 / 6.18 [+0.0040, +0.1994] | O 111: 55.5/54.9; U 160: 55.2/54.4 | O 126: 54.6/55.6; U 145: 54.5/55.9 |
| cm-1 | published | hs | all | 737 / 91 | +0.0009 [-0.0062, +0.0069] | +0.0004 [-0.0031, +0.0034] | -0.29 / -0.24 | 4.12 / 4.12 [-0.0338, +0.0440] | O 276: 55.5/55.1; U 461: 55.8/54.0 | O 402: 55.6/52.7; U 335: 55.2/54.6 |
| cm-1 | published | hs | hscm2-era | 132 / 17 | +0.0069 [-0.0016, +0.0154] | +0.0033 [-0.0009, +0.0075] | -1.35 / -1.36 | 4.49 / 4.49 [-0.0135, +0.0164] | O 34: 54.8/67.7; U 98: 56.7/46.9 | O 56: 54.3/60.7; U 76: 56.8/46.1 |
| cm-1 | published | hs | pre-hscm2 | 605 / 74 | -0.0004 [-0.0088, +0.0072] | -0.0002 [-0.0043, +0.0036] | -0.06 / +0.01 | 4.04 / 4.03 [-0.0437, +0.0528] | O 242: 55.6/53.3; U 363: 55.5/55.9 | O 346: 55.8/51.4; U 259: 54.8/57.1 |
| cm-1 | record arm | kills | all | 825 / 94 | +0.0045 [-0.0019, +0.0113] | +0.0021 [-0.0009, +0.0055] | -0.74 / -0.63 | 6.55 / 6.51 [-0.0213, +0.1044] | O 367: 56.1/54.5; U 458: 55.4/51.1 | O 403: 55.7/54.3; U 422: 54.9/51.4 |
| cm-1 | record arm | kills | cm2-era | 554 / 63 | +0.0013 [-0.0067, +0.0094] | +0.0005 [-0.0033, +0.0045] | -0.93 / -0.82 | 6.68 / 6.68 [-0.0711, +0.0881] | O 256: 56.4/54.3; U 298: 55.5/49.3 | O 277: 56.2/53.8; U 277: 55.1/49.1 |
| cm-1 | record arm | kills | pre-cm2 | 271 / 32 | +0.0110 [-0.0004, +0.0220] | +0.0054 [-0.0001, +0.0108] | -0.36 / -0.25 | 6.29 / 6.18 [+0.0040, +0.1994] | O 111: 55.5/54.9; U 160: 55.2/54.4 | O 126: 54.6/55.6; U 145: 54.5/55.9 |
| cm-1 | record arm | hs | all | 737 / 91 | -0.0010 [-0.0080, +0.0056] | -0.0005 [-0.0039, +0.0028] | -0.30 / -0.24 | 4.11 / 4.12 [-0.0451, +0.0367] | O 274: 55.6/54.7; U 463: 55.8/53.8 | O 402: 55.6/52.7; U 335: 55.2/54.6 |
| cm-1 | record arm | hs | hscm2-era | 132 / 17 | -0.0039 [-0.0202, +0.0112] | -0.0018 [-0.0098, +0.0056] | -1.41 / -1.36 | 4.43 / 4.49 [-0.1240, +0.0166] | O 32: 55.3/65.6; U 100: 57.1/46.0 | O 56: 54.3/60.7; U 76: 56.8/46.1 |
| cm-1 | record arm | hs | pre-hscm2 | 605 / 74 | -0.0004 [-0.0088, +0.0073] | -0.0002 [-0.0043, +0.0036] | -0.06 / +0.01 | 4.04 / 4.03 [-0.0436, +0.0529] | O 242: 55.6/53.3; U 363: 55.5/55.9 | O 346: 55.8/51.4; U 259: 54.8/57.1 |
| cm-2 (shadow) | published | kills | all | 658 / 74 | +0.0028 [+0.0001, +0.0057] | +0.0014 [+0.0000, +0.0028] | -0.65 / -0.65 | 6.72 / 6.71 [-0.0082, +0.0419] | O 319: 55.9/53.9; U 339: 55.2/50.4 | O 314: 55.9/53.5; U 344: 55.0/50.0 |
| cm-2 (shadow) | published | kills | cm2-era | 554 / 63 | +0.0016 [+0.0004, +0.0036] | +0.0008 [+0.0002, +0.0017] | -0.85 / -0.84 | 6.68 / 6.67 [-0.0022, +0.0282] | O 276: 56.1/54.0; U 278: 55.3/49.3 | O 275: 56.1/53.8; U 279: 55.1/49.1 |
| cm-2 (shadow) | published | kills | pre-cm2 | 104 / 12 | +0.0089 [-0.0071, +0.0238] | +0.0043 [-0.0034, +0.0117] | +0.41 / +0.38 | 6.93 / 6.88 [-0.0945, +0.1881] | O 43: 54.5/53.5; U 61: 55.0/55.7 | O 39: 54.6/51.3; U 65: 54.6/53.8 |
| cm-2 (shadow) | record arm | kills | all | 658 / 74 | +0.0027 [-0.0046, +0.0095] | +0.0012 [-0.0023, +0.0046] | -0.72 / -0.65 | 6.72 / 6.71 [-0.0559, +0.0808] | O 299: 56.2/54.2; U 359: 55.5/50.4 | O 314: 55.9/53.5; U 344: 55.0/50.0 |
| cm-2 (shadow) | record arm | kills | cm2-era | 554 / 63 | +0.0016 [-0.0065, +0.0095] | +0.0006 [-0.0033, +0.0045] | -0.93 / -0.84 | 6.68 / 6.67 [-0.0689, +0.0904] | O 256: 56.4/54.3; U 298: 55.5/49.3 | O 275: 56.1/53.8; U 279: 55.1/49.1 |
| cm-2 (shadow) | record arm | kills | pre-cm2 | 104 / 12 | +0.0089 [-0.0071, +0.0238] | +0.0043 [-0.0034, +0.0117] | +0.41 / +0.38 | 6.93 / 6.88 [-0.0945, +0.1881] | O 43: 54.5/53.5; U 61: 55.0/55.7 | O 39: 54.6/51.3; U 65: 54.6/53.8 |
| fh-1 | published | kills | all | 981 / 108 | +0.0010 [-0.0050, +0.0068] | +0.0006 [-0.0022, +0.0034] | -0.69 / -0.52 | 6.50 / 6.51 [-0.0670, +0.0404] | O 453: 55.9/53.6; U 528: 55.2/50.4 | O 493: 56.4/53.3; U 488: 54.9/50.4 |
| fh-1 | published | kills | cm2-era | 559 / 63 | +0.0005 [-0.0076, +0.0093] | +0.0003 [-0.0037, +0.0045] | -0.85 / -0.71 | 6.67 / 6.70 [-0.1010, +0.0622] | O 277: 56.1/53.8; U 282: 55.3/49.6 | O 296: 56.6/52.7; U 263: 55.2/48.7 |
| fh-1 | published | kills | pre-cm2 | 422 / 46 | +0.0016 [-0.0075, +0.0085] | +0.0010 [-0.0031, +0.0042] | -0.48 / -0.27 | 6.26 / 6.26 [-0.0770, +0.0592] | O 176: 55.6/53.4; U 246: 55.1/51.2 | O 197: 56.1/54.3; U 225: 54.6/52.4 |
| fh-1 | published | hs | all | 888 / 107 | -0.0008 [-0.0050, +0.0032] | -0.0004 [-0.0025, +0.0016] | -0.28 / -0.18 | 4.02 / 4.02 [-0.0296, +0.0237] | O 344: 55.7/56.4; U 544: 55.6/54.0 | O 396: 55.5/54.0; U 492: 55.3/53.2 |
| fh-1 | published | hs | hscm2-era | 133 / 17 | +0.0113 [+0.0039, +0.0201] | +0.0054 [+0.0019, +0.0098] | -1.34 / -1.26 | 4.45 / 4.38 [+0.0219, +0.1216] | O 35: 54.7/68.6; U 98: 56.7/46.9 | O 42: 54.6/69.0; U 91: 56.5/48.4 |
| fh-1 | published | hs | pre-hscm2 | 755 / 90 | -0.0030 [-0.0080, +0.0014] | -0.0014 [-0.0039, +0.0007] | -0.09 / +0.01 | 3.94 / 3.96 [-0.0469, +0.0139] | O 309: 55.8/55.0; U 446: 55.4/55.6 | O 354: 55.6/52.3; U 401: 55.0/54.4 |
| fh-1 | record arm | kills | all | 981 / 108 | +0.0009 [-0.0042, +0.0053] | +0.0005 [-0.0019, +0.0026] | -0.74 / -0.52 | 6.50 / 6.51 [-0.0572, +0.0250] | O 433: 56.1/53.8; U 548: 55.4/50.4 | O 493: 56.4/53.3; U 488: 54.9/50.4 |
| fh-1 | record arm | kills | cm2-era | 559 / 63 | +0.0004 [-0.0049, +0.0060] | +0.0001 [-0.0025, +0.0029] | -0.93 / -0.71 | 6.67 / 6.70 [-0.0687, +0.0277] | O 257: 56.4/54.1; U 302: 55.5/49.7 | O 296: 56.6/52.7; U 263: 55.2/48.7 |
| fh-1 | record arm | kills | pre-cm2 | 422 / 46 | +0.0016 [-0.0075, +0.0085] | +0.0010 [-0.0031, +0.0042] | -0.48 / -0.27 | 6.26 / 6.26 [-0.0770, +0.0592] | O 176: 55.6/53.4; U 246: 55.1/51.2 | O 197: 56.1/54.3; U 225: 54.6/52.4 |
| fh-1 | record arm | hs | all | 888 / 107 | -0.0024 [-0.0068, +0.0018] | -0.0012 [-0.0033, +0.0009] | -0.29 / -0.18 | 4.01 / 4.02 [-0.0379, +0.0152] | O 342: 55.7/56.1; U 546: 55.7/53.8 | O 396: 55.5/54.0; U 492: 55.3/53.2 |
| fh-1 | record arm | hs | hscm2-era | 133 / 17 | +0.0006 [-0.0091, +0.0097] | +0.0003 [-0.0044, +0.0048] | -1.40 / -1.26 | 4.40 / 4.38 [-0.0357, +0.0678] | O 33: 55.2/66.7; U 100: 57.1/46.0 | O 42: 54.6/69.0; U 91: 56.5/48.4 |
| fh-1 | record arm | hs | pre-hscm2 | 755 / 90 | -0.0029 [-0.0080, +0.0014] | -0.0014 [-0.0039, +0.0007] | -0.09 / +0.01 | 3.94 / 3.96 [-0.0468, +0.0139] | O 309: 55.8/55.0; U 446: 55.4/55.6 | O 354: 55.6/52.3; U 401: 55.0/54.4 |
| pl-2 | published | kills | all | 893 / 117 | +0.0032 [-0.0084, +0.0156] | +0.0014 [-0.0041, +0.0075] | -0.61 / -0.58 | 6.39 / 6.34 [-0.0646, +0.1520] | O 407: 55.7/55.0; U 486: 55.2/52.5 | O 779: 50.3/52.1; U 114: 52.1/57.0 |
| pl-2 | published | kills | cm2-era | 478 / 63 | +0.0064 [-0.0113, +0.0242] | +0.0030 [-0.0056, +0.0116] | -0.87 / -0.96 | 6.65 / 6.58 [-0.0994, +0.2465] | O 240: 55.9/57.1; U 238: 55.2/51.3 | O 436: 50.2/54.1; U 42: 52.4/59.5 |
| pl-2 | published | kills | pre-cm2 | 415 / 54 | -0.0006 [-0.0159, +0.0149] | -0.0003 [-0.0078, +0.0073] | -0.32 / -0.15 | 6.08 / 6.07 [-0.1150, +0.1393] | O 167: 55.5/52.1; U 248: 55.3/53.6 | O 343: 50.3/49.6; U 72: 51.9/55.6 |
| pl-2 | published | hs | all | 826 / 116 | +0.0027 [-0.0064, +0.0121] | +0.0013 [-0.0031, +0.0058] | -0.15 / -0.19 | 3.87 / 3.86 [-0.0415, +0.0673] | O 324: 55.4/53.1; U 502: 55.7/55.4 | O 443: 51.8/51.9; U 383: 51.7/56.7 |
| pl-2 | published | hs | hscm2-era | 121 / 17 | +0.0133 [-0.0141, +0.0452] | +0.0068 [-0.0067, +0.0220] | -1.04 / -0.84 | 4.46 / 4.42 [-0.1136, +0.1805] | O 31: 54.2/64.5; U 90: 56.8/50.0 | O 48: 51.3/58.3; U 73: 52.2/49.3 |
| pl-2 | published | hs | pre-hscm2 | 705 / 99 | +0.0009 [-0.0088, +0.0104] | +0.0004 [-0.0044, +0.0049] | +0.01 / -0.08 | 3.77 / 3.76 [-0.0447, +0.0662] | O 293: 55.6/51.9; U 412: 55.4/56.5 | O 395: 51.8/51.1; U 310: 51.6/58.4 |
| pl-1 (blocked) | published | kills | all | 1030 / 117 | +0.0059 [-0.0056, +0.0187] | +0.0027 [-0.0028, +0.0090] | -0.62 / -0.55 | 6.45 / 6.38 [-0.0285, +0.1879] | O 465: 55.8/53.3; U 565: 55.4/51.7 | O 895: 50.4/51.6; U 135: 52.2/56.3 |
| pl-1 (blocked) | published | kills | cm2-era | 541 / 63 | +0.0115 [-0.0067, +0.0309] | +0.0054 [-0.0034, +0.0148] | -0.80 / -0.87 | 6.67 / 6.54 [-0.0385, +0.2986] | O 268: 56.1/54.5; U 273: 55.2/50.5 | O 490: 50.3/53.5; U 51: 52.5/62.7 |
| pl-1 (blocked) | published | kills | pre-cm2 | 489 / 55 | -0.0004 [-0.0153, +0.0139] | -0.0002 [-0.0075, +0.0068] | -0.42 / -0.20 | 6.21 / 6.20 [-0.1162, +0.1336] | O 197: 55.5/51.8; U 292: 55.5/52.7 | O 405: 50.5/49.4; U 84: 52.0/52.4 |
| pl-1 (blocked) | published | hs | all | 933 / 116 | +0.0018 [-0.0071, +0.0108] | +0.0009 [-0.0034, +0.0053] | -0.20 / -0.23 | 3.96 / 3.95 [-0.0465, +0.0617] | O 361: 55.6/54.0; U 572: 55.8/54.7 | O 493: 51.9/52.9; U 440: 51.9/56.1 |
| pl-1 (blocked) | published | hs | hscm2-era | 132 / 17 | +0.0153 [-0.0119, +0.0448] | +0.0078 [-0.0056, +0.0222] | -1.32 / -1.14 | 4.46 / 4.42 [-0.1074, +0.1726] | O 35: 54.7/68.6; U 97: 56.7/47.4 | O 54: 51.7/64.8; U 78: 52.2/48.7 |
| pl-1 (blocked) | published | hs | pre-hscm2 | 801 / 99 | -0.0004 [-0.0094, +0.0088] | -0.0003 [-0.0046, +0.0042] | -0.01 / -0.09 | 3.87 / 3.87 [-0.0511, +0.0605] | O 326: 55.7/52.4; U 475: 55.6/56.2 | O 439: 52.0/51.5; U 362: 51.8/57.7 |
| hs-1 | published | hs | all | 766 / 93 | +0.0008 [-0.0054, +0.0075] | +0.0004 [-0.0026, +0.0036] | -0.23 / -0.13 | 4.11 / 4.11 [-0.0354, +0.0260] | O 298: 55.7/53.7; U 468: 55.8/53.8 | O 183: 53.3/54.6; U 583: 54.9/52.7 |
| hs-1 | published | hs | hscm2-era | 133 / 17 | +0.0034 [-0.0097, +0.0167] | +0.0017 [-0.0048, +0.0083] | -1.34 / -1.26 | 4.45 / 4.38 [+0.0219, +0.1216] | O 35: 54.7/68.6; U 98: 56.7/46.9 | O 23: 52.2/65.2; U 110: 56.1/44.5 |
| hs-1 | published | hs | pre-hscm2 | 633 / 76 | +0.0003 [-0.0069, +0.0076] | +0.0001 [-0.0033, +0.0036] | -0.00 / +0.10 | 4.04 / 4.06 [-0.0547, +0.0128] | O 263: 55.8/51.7; U 370: 55.5/55.7 | O 160: 53.4/53.1; U 473: 54.6/54.5 |
| hs-1 | record arm | hs | all | 766 / 93 | -0.0010 [-0.0071, +0.0057] | -0.0005 [-0.0034, +0.0028] | -0.24 / -0.13 | 4.10 / 4.11 [-0.0442, +0.0156] | O 296: 55.7/53.4; U 470: 55.9/53.6 | O 183: 53.3/54.6; U 583: 54.9/52.7 |
| hs-1 | record arm | hs | hscm2-era | 133 / 17 | -0.0074 [-0.0179, +0.0019] | -0.0034 [-0.0085, +0.0010] | -1.40 / -1.26 | 4.40 / 4.38 [-0.0357, +0.0678] | O 33: 55.2/66.7; U 100: 57.1/46.0 | O 23: 52.2/65.2; U 110: 56.1/44.5 |
| hs-1 | record arm | hs | pre-hscm2 | 633 / 76 | +0.0003 [-0.0069, +0.0076] | +0.0001 [-0.0033, +0.0037] | -0.00 / +0.10 | 4.04 / 4.06 [-0.0545, +0.0129] | O 263: 55.8/51.7; U 370: 55.5/55.7 | O 160: 53.4/53.1; U 473: 54.6/54.5 |
| lc-1 | published | kills | all | 848 / 94 | +0.0098 [-0.0033, +0.0240] | +0.0046 [-0.0018, +0.0115] | -0.57 / -0.62 | 6.55 / 6.55 [-0.0513, +0.0496] | O 399: 56.0/53.1; U 449: 55.3/51.7 | O 0; U 848: 50.7/49.4 |
| lc-1 | published | kills | cm2-era | 559 / 63 | +0.0121 [-0.0057, +0.0316] | +0.0057 [-0.0029, +0.0149] | -0.85 / -0.93 | 6.67 / 6.67 [-0.0792, +0.0786] | O 277: 56.1/53.8; U 282: 55.3/49.6 | O 0; U 559: 50.7/47.9 |
| lc-1 | published | kills | pre-cm2 | 289 / 32 | +0.0053 [-0.0159, +0.0287] | +0.0024 [-0.0079, +0.0136] | -0.03 / -0.03 | 6.30 / 6.30 [+0.0000, +0.0000] | O 122: 55.8/51.6; U 167: 55.3/55.1 | O 0; U 289: 50.7/52.2 |
| lc-1 | published | hs | all | 762 / 93 | -0.0007 [-0.0127, +0.0123] | -0.0004 [-0.0061, +0.0059] | -0.24 / -0.25 | 4.12 / 4.11 [-0.0033, +0.0232] | O 296: 55.6/54.0; U 466: 55.8/53.9 | O 0; U 762: 51.0/50.8 |
| lc-1 | published | hs | hscm2-era | 133 / 17 | +0.0085 [-0.0223, +0.0424] | +0.0045 [-0.0105, +0.0211] | -1.34 / -1.40 | 4.45 / 4.40 [-0.0143, +0.1246] | O 35: 54.7/68.6; U 98: 56.7/46.9 | O 0; U 133: 51.0/42.9 |
| lc-1 | published | hs | pre-hscm2 | 629 / 76 | -0.0027 [-0.0152, +0.0108] | -0.0014 [-0.0075, +0.0051] | -0.01 / -0.01 | 4.04 / 4.04 [-0.0003, +0.0000] | O 261: 55.8/52.1; U 368: 55.5/55.7 | O 0; U 629: 51.0/52.5 |
| lc-1 | record arm | kills | all | 848 / 94 | +0.0097 [-0.0027, +0.0228] | +0.0045 [-0.0015, +0.0108] | -0.62 / -0.62 | 6.55 / 6.55 [+0.0000, +0.0000] | O 379: 56.2/53.3; U 469: 55.4/51.6 | O 0; U 848: 50.7/49.4 |
| lc-1 | record arm | kills | cm2-era | 559 / 63 | +0.0120 [-0.0042, +0.0287] | +0.0056 [-0.0023, +0.0136] | -0.93 / -0.93 | 6.67 / 6.67 [+0.0000, +0.0000] | O 257: 56.4/54.1; U 302: 55.5/49.7 | O 0; U 559: 50.7/47.9 |
| lc-1 | record arm | kills | pre-cm2 | 289 / 32 | +0.0053 [-0.0159, +0.0287] | +0.0024 [-0.0079, +0.0136] | -0.03 / -0.03 | 6.30 / 6.30 [+0.0000, +0.0000] | O 122: 55.8/51.6; U 167: 55.3/55.1 | O 0; U 289: 50.7/52.2 |
| lc-1 | record arm | hs | all | 762 / 93 | -0.0026 [-0.0141, +0.0100] | -0.0012 [-0.0068, +0.0049] | -0.25 / -0.25 | 4.11 / 4.11 [+0.0000, +0.0000] | O 294: 55.7/53.7; U 468: 55.9/53.6 | O 0; U 762: 51.0/50.8 |
| lc-1 | record arm | hs | hscm2-era | 133 / 17 | -0.0022 [-0.0327, +0.0287] | -0.0006 [-0.0156, +0.0146] | -1.40 / -1.40 | 4.40 / 4.40 [+0.0000, +0.0000] | O 33: 55.2/66.7; U 100: 57.1/46.0 | O 0; U 133: 51.0/42.9 |
| lc-1 | record arm | hs | pre-hscm2 | 629 / 76 | -0.0027 [-0.0152, +0.0108] | -0.0014 [-0.0075, +0.0051] | -0.01 / -0.01 | 4.04 / 4.04 [+0.0000, +0.0000] | O 261: 55.8/52.1; U 368: 55.5/55.7 | O 0; U 629: 51.0/52.5 |
| lb-001 | published | kills | all | 1269 / 141 | +0.0055 [-0.0058, +0.0169] | +0.0025 [-0.0030, +0.0080] | - | - | O 602: 56.3/53.3; U 667: 55.4/51.4 | O 1269: 50.0/50.8; U 0 |
| lb-001 | published | kills | cm2-era | 559 / 63 | +0.0128 [-0.0049, +0.0323] | +0.0060 [-0.0025, +0.0154] | - | - | O 277: 56.1/53.8; U 282: 55.3/49.6 | O 559: 50.0/52.1; U 0 |
| lb-001 | published | kills | pre-cm2 | 710 / 79 | -0.0003 [-0.0138, +0.0130] | -0.0003 [-0.0068, +0.0062] | - | - | O 325: 56.4/52.9; U 385: 55.4/52.7 | O 710: 50.0/49.9; U 0 |
| lb-001 | published | hs | all | 1156 / 138 | +0.0016 [-0.0038, +0.0070] | +0.0008 [-0.0018, +0.0033] | - | - | O 474: 56.0/53.8; U 682: 55.8/54.4 | O 474: 53.0/53.8; U 682: 52.9/54.4 |
| lb-001 | published | hs | hscm2-era | 133 / 17 | +0.0096 [-0.0080, +0.0287] | +0.0049 [-0.0038, +0.0142] | - | - | O 35: 54.7/68.6; U 98: 56.7/46.9 | O 35: 52.4/68.6; U 98: 53.4/46.9 |
| lb-001 | published | hs | pre-hscm2 | 1023 / 121 | +0.0006 [-0.0049, +0.0062] | +0.0003 [-0.0024, +0.0030] | - | - | O 439: 56.1/52.6; U 584: 55.7/55.6 | O 439: 53.1/52.6; U 584: 52.8/55.6 |
| ch-009 (perf-refit-1) | published | kills | all | 156 / 18 | +0.0012 [+0.0000, +0.0028] | +0.0006 [+0.0000, +0.0014] | -3.48 / -3.51 | 7.70 / 7.69 [-0.0058, +0.0307] | O 59: 53.7/67.8; U 97: 55.0/36.1 | O 59: 53.4/67.8; U 97: 55.0/36.1 |
| ch-009 (perf-refit-1) | published | kills | cm2-era | 156 / 18 | +0.0012 [+0.0000, +0.0028] | +0.0006 [+0.0000, +0.0014] | -3.48 / -3.51 | 7.70 / 7.69 [-0.0058, +0.0307] | O 59: 53.7/67.8; U 97: 55.0/36.1 | O 59: 53.4/67.8; U 97: 55.0/36.1 |
| ch-009 (perf-refit-1) | published | hs | all | 133 / 17 | -0.0011 [-0.0047, +0.0019] | -0.0006 [-0.0023, +0.0009] | -1.34 / -1.36 | 4.45 / 4.46 [-0.0277, +0.0114] | O 35: 54.7/68.6; U 98: 56.7/46.9 | O 34: 54.7/67.7; U 99: 56.7/46.5 |
| ch-009 (perf-refit-1) | published | hs | hscm2-era | 133 / 17 | -0.0011 [-0.0047, +0.0019] | -0.0006 [-0.0023, +0.0009] | -1.34 / -1.36 | 4.45 / 4.46 [-0.0277, +0.0114] | O 35: 54.7/68.6; U 98: 56.7/46.9 | O 34: 54.7/67.7; U 99: 56.7/46.5 |
| rr-1 | published | kills | all | 532 / 60 | +0.0108 [+0.0019, +0.0202] | +0.0053 [+0.0009, +0.0098] | -1.29 / -1.10 | 6.58 / 6.47 [+0.0292, +0.2072] | O 211: 54.9/55.5; U 321: 55.1/47.0 | O 264: 54.1/58.0; U 268: 54.4/50.0 |
| rr-1 | published | kills | cm2-era | 150 / 18 | +0.0162 [+0.0028, +0.0290] | +0.0080 [+0.0014, +0.0143] | -3.70 / -3.47 | 7.84 / 7.71 [+0.0008, +0.2637] | O 56: 53.2/69.6; U 94: 54.9/36.2 | O 73: 53.5/69.9; U 77: 54.8/37.7 |
| rr-1 | published | kills | pre-cm2 | 382 / 42 | +0.0087 [-0.0026, +0.0206] | +0.0042 [-0.0014, +0.0100] | -0.34 / -0.17 | 6.08 / 5.98 [-0.0018, +0.2227] | O 155: 55.5/50.3; U 227: 55.2/51.5 | O 191: 54.3/53.4; U 191: 54.3/55.0 |
| rr-1 | published | hs | all | 815 / 100 | +0.0040 [-0.0016, +0.0094] | +0.0019 [-0.0007, +0.0046] | -0.27 / -0.18 | 4.01 / 4.00 [-0.0223, +0.0522] | O 310: 55.6/57.1; U 505: 55.6/55.0 | O 369: 54.7/56.6; U 446: 55.1/56.3 |
| rr-1 | published | hs | hscm2-era | 127 / 17 | +0.0153 [+0.0030, +0.0285] | +0.0074 [+0.0015, +0.0138] | -1.45 / -1.36 | 4.52 / 4.43 [+0.0166, +0.1752] | O 32: 54.1/68.8; U 95: 56.7/46.3 | O 41: 54.1/70.7; U 86: 56.8/48.8 |
| rr-1 | published | hs | pre-hscm2 | 688 / 83 | +0.0019 [-0.0044, +0.0083] | +0.0009 [-0.0021, +0.0040] | -0.05 / +0.04 | 3.92 / 3.92 [-0.0402, +0.0437] | O 278: 55.8/55.8; U 410: 55.4/57.1 | O 328: 54.7/54.9; U 360: 54.7/58.1 |
| prior_smooth | published | kills | all | 441 / 55 | +0.0006 [-0.0013, +0.0024] | +0.0003 [-0.0007, +0.0012] | -0.45 / -0.32 | 6.16 / 6.15 [-0.0182, +0.0188] | O 183: 55.6/52.5; U 258: 55.3/51.5 | O 195: 55.9/53.3; U 246: 54.8/52.4 |
| prior_smooth | published | kills | cm2-era | 18 / 10 | -0.0036 [-0.0130, +0.0037] | -0.0020 [-0.0064, +0.0016] | +0.38 / +0.57 | 3.91 / 3.95 [-0.1258, +0.0477] | O 7: 57.4/28.6; U 11: 58.6/54.5 | O 8: 57.3/25.0; U 10: 58.3/50.0 |
| prior_smooth | published | kills | pre-cm2 | 423 / 46 | +0.0008 [-0.0012, +0.0026] | +0.0004 [-0.0006, +0.0012] | -0.48 / -0.36 | 6.25 / 6.25 [-0.0164, +0.0204] | O 176: 55.6/53.4; U 247: 55.1/51.4 | O 187: 55.8/54.5; U 236: 54.7/52.5 |
| prior_smooth | published | hs | all | 762 / 93 | +0.0000 [-0.0010, +0.0011] | +0.0000 [-0.0005, +0.0005] | -0.09 / -0.02 | 3.93 / 3.93 [-0.0087, +0.0039] | O 313: 55.9/55.3; U 449: 55.4/55.7 | O 337: 55.9/53.7; U 425: 55.2/55.1 |
| prior_smooth | published | hs | hscm2-era | 6 / 3 | -0.0011 [-0.0050, +0.0172] | -0.0005 [-0.0023, +0.0086] | +0.90 / +1.00 | 3.00 / 3.02 [-0.0500, -0.0050] | O 3: 61.3/66.7; U 3: 55.4/66.7 | O 3: 62.0/66.7; U 3: 54.6/66.7 |
| prior_smooth | published | hs | pre-hscm2 | 756 / 90 | +0.0001 [-0.0010, +0.0010] | +0.0000 [-0.0005, +0.0005] | -0.09 / -0.03 | 3.94 / 3.94 [-0.0088, +0.0038] | O 310: 55.8/55.2; U 446: 55.4/55.6 | O 334: 55.9/53.6; U 422: 55.2/55.0 |
