# vm-1 (map-informed props) - development results

Registration: REGISTER.md (commit bcacfe4, before any prop number). Frozen shadow: **vm-1-ceabd6f325** (vm1.py +
vm1_params.json, commit 96325e9, frozen BEFORE the development results below were read). Development constants:
vm-1-a16f4f0d83 (same procedure, maps 2025-07-01 .. 2026-06-30). Raw numbers: results_dev.json (eval_dev.py); per-unit
rows in scratch veto/vprops (p1_vm1_dev.csv.gz, hltv_vm1_dev_<scope>.csv.gz). Live grading and veto-1 are unchanged.

## Answer
**No improvement from map context is demonstrated, and map-informed grading is not activated.** The predicted maps
(A1) and even the confirmed maps (A2, RETROSPECTIVE) move a forecast almost exactly as far as the same model run without
any map information (C0). Whatever A1 gains over live grading, C0 gains too: it comes from the shape of the rounds
mixture, which is rm-1's effect, already in shadow. Map context changes a match's expected rounds by only about 0.04-0.08
rounds per match (sd across matches). On real lines no cell's interval excludes zero. The forward test (REGISTER section 4)
is the only way vm-1 could ever activate, and on this evidence it is not expected to pass on account of maps.

## Script model (fits)
Development fit (25,345 maps): logit(mu) = 0.324 logit(E_elo) + 0.379 (s_A(m) - s_B(m)); kappa 17.3 (per map 16.2-18.4
after shrinkage); overtime 9.9% modelled vs 11.5% observed (> 1 pt), so tau = 1.16 was fitted, as registered; mean
21.46 rounds per map. Frozen fit (26,429 maps, 2025-10-22 .. 2026-10-21): c0 0.330, c1 0.384, kappa 17.3, tau 1.157,
Rbar 21.447. One map score was excluded as not a legal MR12 score.

## (c) Map-slot checks: rounds, overtime, map winner (HLTV BO3, 2026-07-01 .. 10-21, one row per played map)
Compared with the pool rounds pmf (fitted 2025-07-01 .. 2026-06-30). Higher log score = better; lower MAE / Brier = better.

| slot (maps) | arm | rounds log-score gain | rounds MAE change | OT Brier change | map-winner Brier change |
|---|---|---|---|---|---|
| Map 1 (3,424) | A1 vm-1 | +0.0017 [-0.0021, +0.0053] | -0.013 [-0.022, -0.005] | -0.0001 [-0.0002, +0.0001] | -0.0138 [-0.0176, -0.0103] |
| | A2 RETRO | +0.0022 [-0.0017, +0.0058] | -0.015 [-0.023, -0.006] | -0.0000 | -0.0141 [-0.0179, -0.0107] |
| | C0 no-map | +0.0015 [-0.0022, +0.0050] | -0.011 [-0.019, -0.003] | -0.0000 | -0.0132 [-0.0171, -0.0097] |
| Map 2 (3,427) | A1 vm-1 | -0.0020 [-0.0060, +0.0019] | -0.007 [-0.015, +0.002] | -0.0001 [-0.0003, +0.0000] | -0.0209 [-0.0243, -0.0174] |
| | A2 RETRO | -0.0025 [-0.0064, +0.0017] | -0.008 [-0.018, +0.001] | -0.0001 | -0.0220 [-0.0256, -0.0184] |
| | C0 no-map | -0.0023 [-0.0063, +0.0016] | -0.006 [-0.014, +0.003] | -0.0001 | -0.0200 [-0.0234, -0.0164] |

Pool baseline: rounds MAE 3.30 (Map 1) / 3.46 (Map 2). The script model predicts the map winner better than a coin flip,
but almost all of that comes from Elo (C0). The map-strength term adds 0.0006-0.0011 Brier. The rounds distribution and
overtime risk are not better than the pool pmf, with or without maps.

## (a) Real offered P1 lines (A0 = the logged live chance)
2,692 units / 155 matches (2026-10-11 .. 10-21). Maps resolvable for 2,500 units / 143 matches. vm-1 eligible for 2,480
units / 142 matches. Not eligible: 192 units with no veto record (12 matches); 20 BO5 Maps 1-3 units (1 match), because the
development build asked veto-1 for 250 sequences and a BO5 has 2,520 (serving must pass top=2520 for BO5). Replaying the
logged live chance from the saved inputs differs by mean 0.0002, max 0.0005. Units are scored on vm-1-eligible units only;
the other units are identical in every arm. Log-loss gain = A0 minus arm (> 0 = arm better); Brier change = arm minus A0;
projection MAE change = arm minus A0.

| cell | units / matches | A1 vm-1 log-loss gain | A2 RETRO gain | C0 no-map gain | A1 Brier change | A1 MAE change | sides flipped A1 / A2 | mean abs change in P, A1 |
|---|---|---|---|---|---|---|---|---|
| kills Map 1 | 150 / 19 | +0.0135 [-0.0055, +0.0314] | +0.0138 [-0.0051, +0.0319] | +0.0128 [-0.0063, +0.0307] | -0.0066 [-0.0154, +0.0028] | -0.039 [-0.085, -0.001] | 31 / 33 | 0.039 |
| kills Maps 1-2 | 1,145 / 125 | -0.0009 [-0.0075, +0.0058] | -0.0011 [-0.0080, +0.0058] | -0.0007 [-0.0069, +0.0060] | +0.0004 [-0.0028, +0.0036] | +0.002 [-0.037, +0.040] | 153 / 156 | 0.026 |
| kills Maps 1-3 | 0 (20 BO5 units ineligible, see above) | - | - | - | - | - | - | - |
| HS Map 1 | 127 / 19 | +0.0019 [-0.0058, +0.0101] | +0.0017 [-0.0067, +0.0105] | +0.0017 [-0.0059, +0.0098] | -0.0010 [-0.0050, +0.0027] | -0.012 [-0.029, +0.002] | 17 / 15 | 0.025 |
| HS Maps 1-2 | 1,058 / 125 | -0.0011 [-0.0053, +0.0025] | -0.0012 [-0.0054, +0.0025] | -0.0007 [-0.0047, +0.0027] | +0.0005 [-0.0012, +0.0024] | +0.006 [-0.008, +0.023] | 71 / 75 | 0.014 |
| HS Maps 1-3 | 0 (as kills) | - | - | - | - | - | - | - |

Reference only (forward alpha, not a test here): the one-sided 99.167% lower bounds of the A1 gain are -0.0098 (kills Map 1),
-0.0089 (kills Maps 1-2), -0.0075 (HS Map 1), -0.0064 (HS Maps 1-2).

Favoured-side calibration gap |mean chance - hit rate|, live -> A1 (n favoured by A1): kills Map 1 Over 0.194 -> 0.182 (51),
Under 0.032 -> 0.031 (99); kills Maps 1-2 Over 0.053 -> 0.035 (410), Under 0.024 -> 0.038 (735); HS Map 1 Over 0.150 -> 0.193
(43), Under 0.107 -> 0.115 (84); HS Maps 1-2 Over 0.028 -> 0.003 (385), Under 0.005 -> 0.010 (673). Every difference's
interval contains 0. Signed projection error (actual minus projection), live -> A1: kills Map 1 -0.81 -> -0.68; kills
Maps 1-2 +0.12 -> +0.19; HS Map 1 -0.10 -> -0.03; HS Maps 1-2 -0.02 -> +0.02.

**Changed-forecast subset** (|P_A1 - P_A0| >= 0.01 or a side flip): kills Map 1 149 units / 19 matches, gain +0.0137
[-0.0054, +0.0317]; kills Maps 1-2 1,113 / 125, -0.0010 [-0.0077, +0.0058]; HS Map 1 118 / 19, +0.0020 [-0.0061, +0.0103];
HS Maps 1-2 469 / 87, -0.0029 [-0.0119, +0.0049]. Almost every unit changes, but because of the mixture shape and the
level (Rbar 21.46 vs live 21.74), not the maps. **The subset where the predicted maps change the forecast** (|P_A1 - P_C0|
>= 0.01 or a side flip vs C0): kills Map 1 0 units; kills Maps 1-2 28 units / 13 matches (A1 vs live -0.0401 [-0.1003,
+0.0136]; C0 vs live -0.0389); HS Map 1 1 unit; HS Maps 1-2 9 units / 6 matches. These are too few to read, and they do
not favour maps.

## (b) HLTV population (ED-1 series, 2026-07-01 .. 10-21, complete vetoes; A0 = the live formula)
Integer log-score gain (arm minus live, > 0 = better), synthetic line floor(live projection) + 0.5 log-loss gain, MAE change.

| scope | stat | rows / matches | A1 log score | A2 RETRO | C0 no-map | A1 line log-loss gain | A1 MAE change | A2 MAE change | C0 MAE change |
|---|---|---|---|---|---|---|---|---|---|
| Map 1 | kills | 37,580 / 3,758 | +0.0488 [+0.0250, +0.0905] | +0.0488 | +0.0487 | +0.0047 [+0.0036, +0.0058] | -0.0073 [-0.0102, -0.0046] | -0.0073 | -0.0063 |
| Map 1 | HS | 37,567 / 3,758 | +0.0090 [+0.0073, +0.0109] | +0.0090 | +0.0090 | +0.0015 [+0.0009, +0.0021] | -0.0029 | -0.0028 | -0.0024 |
| Maps 1-2 | kills | 33,905 / 3,410 | +0.0177 [+0.0135, +0.0221] | +0.0177 | +0.0176 | +0.0017 [+0.0006, +0.0029] | -0.0131 [-0.0190, -0.0067] | -0.0131 | -0.0115 |
| Maps 1-2 | HS | 33,883 / 3,410 | +0.0055 [+0.0042, +0.0068] | +0.0055 | +0.0054 | +0.0000 [-0.0006, +0.0006] | -0.0041 | -0.0042 | -0.0032 |
| Maps 1-3 | kills | 14,040 / 1,414 | +0.0198 [+0.0159, +0.0242] | +0.0199 | +0.0198 | +0.0005 [-0.0007, +0.0015] | +0.0037 [-0.0025, +0.0106] | +0.0022 | +0.0055 |
| Maps 1-3 | HS | 14,024 / 1,414 | +0.0061 [+0.0047, +0.0077] | +0.0061 | +0.0061 | -0.0004 [-0.0008, +0.0000] | +0.0009 | +0.0005 | +0.0013 |

**What the maps alone add (A1 minus C0, paired, match-clustered):**

| scope | kills log score | kills MAE change | A2 RETRO minus C0 kills MAE | HS log score | expected-rounds shift (sd per match) |
|---|---|---|---|---|---|
| Map 1 | +0.00011 [+0.00002, +0.00021] | -0.0010 [-0.0014, -0.0005] | -0.0010 [-0.0018, -0.0001] | +0.00005 [-0.00002, +0.00011] | 0.040 rounds |
| Maps 1-2 | +0.00011 [-0.00003, +0.00025] | -0.0016 [-0.0025, -0.0008] | -0.0016 [-0.0029, -0.0003] | +0.00004 [-0.00005, +0.00012] | 0.075 |
| Maps 1-3 | +0.00000 [-0.00016, +0.00017] | -0.0018 [-0.0031, -0.0006] | -0.0033 [-0.0054, -0.0014] | -0.00004 [-0.00014, +0.00005] | 0.061 |

Map information is statistically detectable on 3,000+ matches, but tiny: about one thousandth of a kill in MAE and one
ten-thousandth in log score. Even knowing the confirmed maps (A2) does not change this. At the real-line volume (about
14 matches a day) it cannot show up in a chance. The Maps 1-3 split condition lowers the live +0.37 kills under-projection
to +0.26 (A1) on HLTV, but C0 does the same (+0.24): that comes from series length, not maps.

## Decision
- Development (registered section 3): nothing demonstrated. No real-line cell excludes zero. Maps add nothing measurable
  over the no-map control at any scope, for kills or HS, on real lines or on 3,758 HLTV matches. Development results cannot
  activate anything in any case.
- **Map-informed grading is NOT activated.** The frozen shadow vm-1-ceabd6f325 is ready for the registered forward test if
  the coordinator wants it logged. A forward pass would most likely reflect the rounds-mixture shape (rm-1's shadow
  territory), not maps. A cleaner forward question would be A1 vs C0, but that is not the registered rule, and it would
  need its own registration before any forward unit exists.
- No per-map player kill-rate adjustment was used or tested (owner instruction): maps entered only through rounds,
  scoreline, overtime and series length.

## Limits
- The real-line archive is 11 days. Maps 1-3 had only 20 BO5 units (1 match), and those were not scored (top=250 build
  limit, above). BO3 Maps 1-3 real lines do not exist in the archive.
- The HLTV period overlaps veto-1's FINAL period (veto-1 was frozen before it). The script constants are strictly earlier
  (<= 2026-06-30); rho = rm-1 F6 (fit < 2026-10-01).
- Elo and map strength are the veto table's own (ELO_K 24, k_strength 6). No other team-strength source was tried
  (registration).
