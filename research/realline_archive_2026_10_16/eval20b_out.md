
#### E1 closing lines, platforms differ: 329 pairs with different lines / 49 matches (Underdog line as the base, one row per pair)
  slope of (actual - UD line) on (PP line - UD line): +1.214 (+0.412, +2.064)  [0 = UD right, 1 = PP right, 0.5 = their mean]
  MAE Underdog line 4.82 (4.30, 5.36) | PrizePicks line 4.81 (4.30, 5.33)
  kills: 208 pairs / 49 matches, slope +1.323 (+0.334, +2.384)
  hs: 121 pairs / 41 matches, slope +0.986 (-0.507, +2.312)
  Underdog side pointed to by the PrizePicks line (one bet per pair): hit 0.502 (0.439, 0.565)

#### E2 forecast-time lines, platforms differ: 411 pairs with different lines / 51 matches (Underdog line as the base, one row per pair)
  slope of (actual - UD line) on (PP line - UD line): +1.151 (+0.402, +1.861)  [0 = UD right, 1 = PP right, 0.5 = their mean]
  MAE Underdog line 4.80 (4.29, 5.30) | PrizePicks line 4.80 (4.29, 5.28)
  kills: 251 pairs / 51 matches, slope +1.336 (+0.364, +2.204)
  hs: 160 pairs / 47 matches, slope +0.812 (-0.135, +1.646)
  Underdog side pointed to by the PrizePicks line (one bet per pair): hit 0.496 (0.443, 0.554)

### E1 rolling - kills only: 1491 observations / 159 matches, days 2026-10-04 .. 2026-10-16, Over rate 0.495
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.005 (-0.036, +0.047) | +0.0008 (-0.0014, +0.0034) | +0.0004 (-0.0007, +0.0017) |
| p_BASE | 0.6940 | 0.2504 | 0.495 | -0.74 (-4.06, 2.43) | +0.003 (-0.039, +0.046) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_n0 | 0.7116 | 0.2587 | 0.496 | -0.08 (-0.45, 0.32) | +0.003 (-0.039, +0.048) | -0.0176 (-0.0311, -0.0043) | -0.0082 (-0.0148, -0.0018) |
| p_n | 0.7047 | 0.2556 | 0.503 | 0.06 (-0.33, 0.50) | +0.022 (-0.019, +0.067) | -0.0108 (-0.0228, +0.0015) | -0.0052 (-0.0110, +0.0008) |
| p_PL2_proj | 0.6929 | 0.2499 | 0.510 | 0.92 (0.43, 1.50) | +0.005 (-0.036, +0.047) | +0.0011 (-0.0013, +0.0037) | +0.0005 (-0.0006, +0.0018) |
| p_CORR_full | 0.6951 | 0.2510 | 0.469 | -0.82 (-2.67, 0.86) | +0.003 (-0.039, +0.046) | -0.0012 (-0.0040, +0.0014) | -0.0006 (-0.0020, +0.0007) |
| p_ANCHORfit_proj | 0.6946 | 0.2507 | 0.505 | -0.27 (-1.52, 0.88) | +0.006 (-0.036, +0.048) | -0.0006 (-0.0041, +0.0030) | -0.0003 (-0.0021, +0.0015) |
| p_ANCHORfit_full | 0.6937 | 0.2503 | 0.512 | 0.29 (-0.88, 1.52) | +0.009 (-0.033, +0.051) | +0.0003 (-0.0035, +0.0042) | +0.0002 (-0.0018, +0.0021) |

#### selection rule (favoured side >= 57%) - E1 rolling - kills only
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_n0 | 581 | 142 | 0.622 | 0.497 (0.445, 0.550) | 0.498 | -0.001 (-0.053, +0.051) | 0.458 (273) | 0.532 (308) |
| p_n | 527 | 133 | 0.616 | 0.520 (0.468, 0.576) | 0.496 | +0.024 (-0.027, +0.080) | 0.466 (352) | 0.629 (175) |
| p_PL2_proj | 0 | - | - | - | - | - | - | - |
| p_CORR_full | 9 | 7 | 0.577 | 0.444 (0.125, 0.875) | 0.506 | -0.062 (-0.373, +0.356) | 0.400 (5) | 0.500 (4) |
| p_ANCHORfit_proj | 15 | 12 | 0.585 | 0.333 (0.067, 0.600) | 0.494 | -0.161 (-0.432, +0.108) | 0.385 (13) | 0.000 (2) |
| p_ANCHORfit_full | 12 | 7 | 0.586 | 0.500 (0.273, 0.800) | 0.495 | +0.005 (-0.220, +0.305) | 0.500 (12) | nan (0) |

### E1 rolling - hs only: 1286 observations / 159 matches, days 2026-10-04 .. 2026-10-16, Over rate 0.470
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.030 (-0.004, +0.067) | -0.0005 (-0.0056, +0.0044) | -0.0003 (-0.0028, +0.0022) |
| p_BASE | 0.6926 | 0.2497 | 0.475 | -1.38 (-3.82, 0.99) | -0.003 (-0.038, +0.034) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_n0 | 0.7018 | 0.2539 | 0.535 | 0.29 (-0.01, 0.60) | +0.035 (-0.000, +0.072) | -0.0092 (-0.0221, +0.0044) | -0.0041 (-0.0103, +0.0024) |
| p_n | 0.6955 | 0.2510 | 0.548 | 0.54 (0.18, 0.97) | +0.051 (+0.016, +0.089) | -0.0029 (-0.0149, +0.0100) | -0.0013 (-0.0072, +0.0049) |
| p_PL2_proj | 0.6915 | 0.2492 | 0.534 | 1.04 (0.06, 2.07) | +0.031 (-0.002, +0.068) | +0.0011 (-0.0044, +0.0071) | +0.0005 (-0.0022, +0.0035) |
| p_CORR_full | 0.6901 | 0.2485 | 0.520 | 0.63 (0.04, 1.20) | -0.001 (-0.036, +0.036) | +0.0025 (-0.0026, +0.0083) | +0.0012 (-0.0013, +0.0041) |
| p_ANCHORfit_proj | 0.6945 | 0.2506 | 0.537 | 0.43 (-0.02, 0.91) | +0.034 (-0.001, +0.070) | -0.0019 (-0.0096, +0.0067) | -0.0008 (-0.0045, +0.0034) |
| p_ANCHORfit_full | 0.6921 | 0.2495 | 0.547 | 0.72 (0.28, 1.21) | +0.044 (+0.010, +0.081) | +0.0005 (-0.0081, +0.0099) | +0.0003 (-0.0039, +0.0049) |

#### selection rule (favoured side >= 57%) - E1 rolling - hs only
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_n0 | 535 | 149 | 0.629 | 0.538 (0.489, 0.590) | 0.499 | +0.039 (-0.011, +0.091) | 0.527 (292) | 0.551 (243) |
| p_n | 505 | 144 | 0.617 | 0.533 (0.479, 0.589) | 0.492 | +0.041 (-0.013, +0.096) | 0.500 (342) | 0.601 (163) |
| p_PL2_proj | 32 | 23 | 0.588 | 0.594 (0.414, 0.758) | 0.499 | +0.094 (-0.084, +0.257) | 0.625 (16) | 0.562 (16) |
| p_CORR_full | 251 | 82 | 0.608 | 0.582 (0.510, 0.648) | 0.531 | +0.050 (-0.020, +0.117) | 0.632 (38) | 0.573 (213) |
| p_ANCHORfit_proj | 206 | 80 | 0.619 | 0.544 (0.474, 0.616) | 0.499 | +0.045 (-0.025, +0.116) | 0.538 (119) | 0.552 (87) |
| p_ANCHORfit_full | 260 | 104 | 0.609 | 0.558 (0.496, 0.624) | 0.488 | +0.070 (+0.006, +0.136) | 0.542 (192) | 0.603 (68) |

#### stability of the disagreement slope by era (E1 full history, lam_n)
| stat x scope | era | obs | matches | slope (95%) | MSE proj - MSE line (95%) |
|---|---|---|---|---|---|
| kills Maps 1-2 | Oct 02-10 (Railway era) | 780 | 100 | +0.192 (-0.243, +0.613) | +1.77 (-0.79, +4.36) |
| kills Maps 1-2 | Oct 11-17 (Render era) | 654 | 55 | +0.128 (-0.483, +0.667) | +1.53 (-1.14, +4.15) |
| hs Maps 1-2 | Oct 02-10 (Railway era) | 710 | 100 | +0.626 (+0.217, +1.102) | -0.28 (-1.39, +0.67) |
| hs Maps 1-2 | Oct 11-17 (Render era) | 566 | 55 | +0.529 (-0.104, +1.097) | -0.04 (-1.26, +1.19) |

#### stability of the disagreement slope by era (E1 live-pin history, lam_n0)
| stat x scope | era | obs | matches | slope (95%) | MSE proj - MSE line (95%) |
|---|---|---|---|---|---|
| kills Maps 1-2 | Oct 02-10 (Railway era) | 780 | 100 | +0.079 (-0.311, +0.514) | +3.06 (+0.02, +5.84) |
| kills Maps 1-2 | Oct 11-17 (Render era) | 654 | 55 | +0.150 (-0.350, +0.638) | +2.02 (-0.88, +4.88) |
| hs Maps 1-2 | Oct 02-10 (Railway era) | 710 | 100 | +0.223 (-0.102, +0.582) | +0.97 (-0.29, +2.25) |
| hs Maps 1-2 | Oct 11-17 (Render era) | 566 | 55 | +0.494 (+0.019, +0.964) | +0.00 (-1.20, +1.18) |

#### stability of the disagreement slope by era (E2 full history, lam_n)
| stat x scope | era | obs | matches | slope (95%) | MSE proj - MSE line (95%) |
|---|---|---|---|---|---|
| kills Maps 1-2 | Oct 02-10 (Railway era) | 332 | 91 | +0.730 (-0.036, +1.461) | -1.73 (-6.60, +2.49) |
| kills Maps 1-2 | Oct 11-17 (Render era) | 700 | 56 | +0.135 (-0.523, +0.709) | +1.73 (-1.19, +4.76) |
| hs Maps 1-2 | Oct 02-10 (Railway era) | 247 | 84 | +0.905 (+0.365, +1.514) | -1.29 (-3.26, +0.39) |
| hs Maps 1-2 | Oct 11-17 (Render era) | 599 | 56 | +0.584 (-0.018, +1.171) | -0.14 (-1.51, +1.13) |

#### line movement (E2 forecast-time line vs the platform's closing line): 1987 joined, 293 moved (113 matches)
  moved toward our rebuilt projection: 0.662 (0.597, 0.726); toward the side live favoured: 0.631 (0.569, 0.691)
  slope of (actual - forecast-time line) on the later move: +0.872 (+0.192, +1.617)  [1 = the move was fully right]
  (diagnostic only: a line moving our way is agreement with a later market, not proof of selection skill)

#### E2 restricted to P1-like rules (Render, one per unit across platforms, starts <= 10-16 18:10): 1138 units / 67 matches (P1 at 19:37: 1,211 / 72)

#### selection rule (favoured side >= 57%) - P1-like E2
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 425 | 63 | 0.617 | 0.513 (0.446, 0.581) | 0.500 | +0.013 (-0.054, +0.081) | 0.444 (223) | 0.589 (202) |
| p_n0 | 441 | 64 | 0.623 | 0.531 (0.467, 0.597) | 0.497 | +0.033 (-0.031, +0.101) | 0.482 (255) | 0.597 (186) |
  rows E2 keeps beyond P1 (the other platform's different line): 411

#### power (E1 scored): 159 matches, 17.5 observations per match; selection picks in 65% of matches, 2.6 picks per picked match
| matches | SE log-loss gain | detectable log-loss gain (80%, 5% two-sided) | SE hit - BASE | detectable selection edge (80%, 5%) |
|---|---|---|---|---|
| 100 | 0.0035 | 0.0097 | 0.042 | 0.117 |
| 189 | 0.0025 | 0.0071 | 0.030 | 0.085 |
| 300 | 0.0020 | 0.0056 | 0.024 | 0.067 |
| 600 | 0.0014 | 0.0040 | 0.017 | 0.048 |
| 1000 | 0.0011 | 0.0031 | 0.013 | 0.037 |
| 2000 | 0.0008 | 0.0022 | 0.009 | 0.026 |
