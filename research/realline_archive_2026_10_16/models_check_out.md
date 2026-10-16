## (a) GLM vs CUR-FULL (full-history current formula = fh-1's history set), history test Sep 1 - Oct 16
  all: 17028 series; MAE gain +0.059 (np.float64(0.041), np.float64(0.077)); log gain +0.0064 (0.0035, 0.0091); bias GLM -0.24 CUR -0.43 CUR-FULL +0.12
  book-listed: 8988 series; MAE gain +0.041 (np.float64(0.019), np.float64(0.062)); log gain +0.0054 (0.0021, 0.0087); bias GLM -0.37 CUR -0.63 CUR-FULL -0.17
  book-listed established: 6497 series; MAE gain +0.031 (np.float64(0.007), np.float64(0.052)); log gain +0.0046 (0.001, 0.0079); bias GLM -0.39 CUR -0.56 CUR-FULL -0.31
  established: 9809 series; MAE gain +0.024 (np.float64(0.005), np.float64(0.043)); log gain +0.0026 (-0.0003, 0.0056); bias GLM -0.34 CUR -0.50 CUR-FULL -0.20
  top-200 set: 12332 series; MAE gain +0.050 (np.float64(0.029), np.float64(0.07)); log gain +0.0049 (0.0019, 0.0079); bias GLM -0.30 CUR -0.31 CUR-FULL +0.03

### (a) real lines: GLM vs CUR-FULL (kills + HS pooled): 1927 observations / 149 matches, days 2026-10-03 .. 2026-10-16, Over rate 0.502
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs p_FULL (95%) | Brier gain vs p_FULL (95%) | log-loss gain vs p_CUR (95%) | Brier gain vs p_CUR (95%) |
|---|---|---|---|---|---|---|---|---|---|
| p_BASE | 0.6950 | 0.2509 | 0.486 | -0.73 (-2.45, 0.92) | -0.002 (-0.046, +0.042) | -0.0022 (-0.0160, +0.0109) | -0.0010 (-0.0077, +0.0053) | +0.0022 (-0.0115, +0.0158) | +0.0010 (-0.0055, +0.0076) |
| p_CUR | 0.6973 | 0.2519 | 0.535 | 0.38 (0.08, 0.72) | +0.006 (-0.038, +0.050) | -0.0044 (-0.0116, +0.0026) | -0.0020 (-0.0055, +0.0013) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_FULL | 0.6929 | 0.2499 | 0.542 | 0.54 (0.15, 0.96) | +0.022 (-0.023, +0.065) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | +0.0044 (-0.0026, +0.0116) | +0.0020 (-0.0013, +0.0055) |
| p_GLM | 0.6852 | 0.2462 | 0.565 | 0.85 (0.43, 1.26) | +0.015 (-0.030, +0.057) | +0.0076 (+0.0008, +0.0139) | +0.0037 (+0.0004, +0.0068) | +0.0120 (+0.0037, +0.0202) | +0.0058 (+0.0017, +0.0097) |

#### selection rule (favoured side >= 57%) - (a) real lines
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_FULL | 747 | 136 | 0.619 | 0.558 (0.502, 0.615) | 0.497 | +0.061 (+0.006, +0.119) | 0.545 (530) | 0.590 (217) |
| p_GLM | 701 | 131 | 0.613 | 0.588 (0.534, 0.641) | 0.496 | +0.092 (+0.038, +0.145) | 0.560 (468) | 0.644 (233) |

## (b) GLM coefficients (standardised features; final fit on matches <= 2026-09-30)
  log_rate_all   -0.1407 (per raw unit -2.0279)
  log_rate_env   +0.0546 (per raw unit +0.6938)
  log_opp        +0.0058 (per raw unit +0.2158)
  log_hist_r     +0.0330 (per raw unit +0.0222)
  log_env_r      -0.0057 (per raw unit -0.0034)
  log_maps       -0.0109 (per raw unit -0.0087)
  lan            -0.0047 (per raw unit -0.0102)
  elo_own        -0.0084 (per raw unit -0.0435)
  elo_opp        -0.0079 (per raw unit -0.0406)
  elo_gap        -0.0005 (per raw unit -0.0026)
  log_team_rpm   +0.0053 (per raw unit +0.2106)
  log_opp_rpm    +0.0020 (per raw unit +0.0797)
  log_adj_rate   +0.1708 (per raw unit +2.3764)
  log_joint_opp  +0.0359 (per raw unit +1.0996)
  intercept +3.3499

## (c) the two halves of the line period

### (c) lines 2026-10-01 .. 2026-10-09: 505 observations / 83 matches, days 2026-10-03 .. 2026-10-09, Over rate 0.499
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs p_CUR (95%) | Brier gain vs p_CUR (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| p_BASE | 0.6987 | 0.2528 | 0.467 | -0.80 (-3.16, 1.54) | +0.014 (-0.056, +0.087) | +0.0009 (-0.0256, +0.0274) | +0.0001 (-0.0125, +0.0128) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_CUR | 0.6996 | 0.2529 | 0.544 | 0.35 (-0.20, 0.92) | -0.002 (-0.075, +0.071) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | -0.0009 (-0.0274, +0.0256) | -0.0001 (-0.0128, +0.0125) |
| p_GLM | 0.6855 | 0.2463 | 0.569 | 0.74 (0.11, 1.52) | +0.012 (-0.057, +0.084) | +0.0141 (-0.0027, +0.0317) | +0.0065 (-0.0014, +0.0148) | +0.0132 (-0.0111, +0.0381) | +0.0064 (-0.0053, +0.0184) |

#### selection rule (favoured side >= 57%) - (c) 2026-10-01 .. 2026-10-09
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_CUR | 227 | 76 | 0.632 | 0.555 (0.475, 0.635) | 0.496 | +0.059 (-0.022, +0.140) | 0.538 (117) | 0.573 (110) |
| p_GLM | 199 | 66 | 0.624 | 0.613 (0.527, 0.696) | 0.494 | +0.119 (+0.033, +0.204) | 0.556 (126) | 0.712 (73) |

### (c) lines 2026-10-10 .. 2026-10-16: 1422 observations / 66 matches, days 2026-10-10 .. 2026-10-16, Over rate 0.504
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs p_CUR (95%) | Brier gain vs p_CUR (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| p_BASE | 0.6937 | 0.2503 | 0.488 | -0.86 (-3.59, 2.32) | -0.008 (-0.063, +0.047) | +0.0027 (-0.0129, +0.0189) | +0.0013 (-0.0063, +0.0091) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_CUR | 0.6964 | 0.2516 | 0.531 | 0.40 (-0.06, 0.90) | +0.009 (-0.046, +0.065) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | -0.0027 (-0.0189, +0.0129) | -0.0013 (-0.0091, +0.0063) |
| p_GLM | 0.6851 | 0.2461 | 0.564 | 0.91 (0.28, 1.50) | +0.016 (-0.040, +0.070) | +0.0113 (+0.0016, +0.0209) | +0.0055 (+0.0007, +0.0102) | +0.0086 (-0.0048, +0.0220) | +0.0042 (-0.0024, +0.0107) |

#### selection rule (favoured side >= 57%) - (c) 2026-10-10 .. 2026-10-16
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_CUR | 560 | 62 | 0.624 | 0.537 (0.472, 0.602) | 0.498 | +0.040 (-0.027, +0.103) | 0.539 (345) | 0.535 (215) |
| p_GLM | 502 | 65 | 0.609 | 0.578 (0.509, 0.642) | 0.497 | +0.081 (+0.012, +0.145) | 0.561 (342) | 0.613 (160) |

## (d) leakage probe: features permuted across rows within each match day (the target kept) - the GLM must lose its gain
  permuted-feature GLM vs CUR on the history test: log-score gain -0.0309; MAE gain -0.239
