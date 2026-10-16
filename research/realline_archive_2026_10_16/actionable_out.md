observations 3130 matches 182 days 2026-10-02 .. 2026-10-16
with our forecast 1758 | outcome disagrees with HLTV study total 0
excluded: {"the archive's HLTV match has another opponent (wrong match for this line)": 150, 'player not identified in the HLTV scoreboard of this match': 6, 'no outcome (void)': 117, 'ambiguous HLTV match (the teams played twice; the archive took the closest by date)': 133, 'no outcome (no_match)': 103, 'not settled yet (pending)': 300, 'push': 21}
Railway forecast log ends 2026-10-08T23:24:24+00:00
# Actionable-line analysis

decision-time observations: 2358 / 178 distinct HLTV matches, match days 2026-10-03 .. 2026-10-16
excluded / collapsed: {'no verified HLTV player / match (identity)': 689, 'Maps 1-3 (no HLTV construction)': 46, 'no HLTV total for this stat / scope': 10, 'no forecast >= 30 min before the start': 61, 'push at the original line': 29, 'same line on the other platform (copy)': 253}
decision lead time before the start: median 16.6 h (25% 11.6 h, 75% 20.0 h)
by host / record kind: {('railway', 'pick row'): 624, ('railway', 'log'): 128, ('render', 'log'): 1606}
offered side of the favoured side logged: {'True': 383} | no quote logged (before 2026-10-15 02:44): 1975

### ALL decision-time forecasts at their original line (line-verified): 2358 observations / 178 matches, days 2026-10-03 .. 2026-10-16, Over rate 0.491
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs flat (95%) | Brier gain vs flat (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.009 (-0.032, +0.051) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | +0.0014 (-0.0007, +0.0034) | +0.0007 (-0.0003, +0.0017) |
| p_BASE | 0.6945 | 0.2507 | 0.493 | -0.49 (-1.92, 0.91) | +0.009 (-0.033, +0.051) | -0.0014 (-0.0034, +0.0007) | -0.0007 (-0.0017, +0.0003) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_live | 0.6997 | 0.2532 | 0.519 | 0.25 (-0.12, 0.65) | +0.012 (-0.030, +0.054) | -0.0066 (-0.0169, +0.0034) | -0.0032 (-0.0082, +0.0016) | -0.0052 (-0.0163, +0.0057) | -0.0025 (-0.0079, +0.0028) |

#### selection rule (favoured side >= 57%) - ALL decision-time forecasts at their original line (line-verified)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 784 | 149 | 0.618 | 0.526 (0.471, 0.584) | 0.497 | +0.028 (-0.026, +0.086) | 0.501 (413) | 0.553 (371) |
  kills: slope of (actual - original line) on (recorded projection - original line) +0.182 (-0.231, +0.560); MSE projection - MSE line +1.64 (-0.57, +3.72); 1293 obs / 176 matches
  hs: slope of (actual - original line) on (recorded projection - original line) +0.503 (+0.153, +0.842); MSE projection - MSE line -0.08 (-1.00, +0.82); 1065 obs / 169 matches

### Railway era (Oct 3-10; mostly older pipelines): 752 observations / 111 matches, days 2026-10-03 .. 2026-10-11, Over rate 0.500
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs flat (95%) | Brier gain vs flat (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.000 (-0.058, +0.054) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | +0.0040 (-0.0014, +0.0096) | +0.0020 (-0.0007, +0.0048) |
| p_BASE | 0.6971 | 0.2520 | 0.478 | -0.81 (-2.78, 0.88) | +0.009 (-0.050, +0.063) | -0.0040 (-0.0096, +0.0014) | -0.0020 (-0.0048, +0.0007) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_live | 0.6952 | 0.2509 | 0.541 | 0.44 (-0.11, 0.99) | -0.019 (-0.075, +0.035) | -0.0021 (-0.0158, +0.0110) | -0.0009 (-0.0075, +0.0053) | +0.0019 (-0.0140, +0.0171) | +0.0011 (-0.0065, +0.0082) |

#### selection rule (favoured side >= 57%) - Railway era (Oct 3-10; mostly older pipelines)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 246 | 86 | 0.620 | 0.565 (0.500, 0.633) | 0.498 | +0.067 (+0.002, +0.135) | 0.589 (73) | 0.555 (173) |
  kills: slope of (actual - original line) on (recorded projection - original line) +0.341 (-0.292, +0.976); MSE projection - MSE line +1.30 (-3.45, +5.30); 432 obs / 109 matches
  hs: slope of (actual - original line) on (recorded projection - original line) +0.744 (+0.260, +1.246); MSE projection - MSE line -0.99 (-2.89, +0.72); 320 obs / 102 matches

### Render era (Oct 11-16; perf-2 tags): 1606 observations / 67 matches, days 2026-10-11 .. 2026-10-16, Over rate 0.486
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs flat (95%) | Brier gain vs flat (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.014 (-0.042, +0.068) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | +0.0001 (-0.0014, +0.0017) | +0.0001 (-0.0007, +0.0008) |
| p_BASE | 0.6933 | 0.2501 | 0.497 | -0.33 (-2.61, 2.23) | +0.009 (-0.047, +0.063) | -0.0001 (-0.0017, +0.0014) | -0.0001 (-0.0008, +0.0007) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_live | 0.7018 | 0.2542 | 0.510 | 0.19 (-0.31, 0.72) | +0.026 (-0.030, +0.081) | -0.0087 (-0.0221, +0.0045) | -0.0042 (-0.0108, +0.0022) | -0.0085 (-0.0227, +0.0053) | -0.0041 (-0.0111, +0.0026) |

#### selection rule (favoured side >= 57%) - Render era (Oct 11-16; perf-2 tags)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 538 | 63 | 0.617 | 0.507 (0.431, 0.582) | 0.497 | +0.010 (-0.066, +0.085) | 0.482 (340) | 0.551 (198) |
  kills: slope of (actual - original line) on (recorded projection - original line) +0.079 (-0.477, +0.578); MSE projection - MSE line +1.81 (-0.53, +4.08); 861 obs / 67 matches
  hs: slope of (actual - original line) on (recorded projection - original line) +0.335 (-0.094, +0.775); MSE projection - MSE line +0.31 (-0.66, +1.27); 745 obs / 67 matches

### line + favoured side verified offered (quote logged, from 2026-10-15): 383 observations / 16 matches, days 2026-10-16 .. 2026-10-16, Over rate 0.457
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs flat (95%) | Brier gain vs flat (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.043 (-0.073, +0.168) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | -0.0014 (-0.0053, +0.0023) | -0.0007 (-0.0026, +0.0011) |
| p_BASE | 0.6918 | 0.2493 | 0.557 | 1.93 (-3.10, 6.60) | +0.040 (-0.076, +0.165) | +0.0014 (-0.0023, +0.0053) | +0.0007 (-0.0011, +0.0026) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_live | 0.6983 | 0.2525 | 0.523 | 0.33 (-1.68, 2.39) | +0.056 (-0.060, +0.180) | -0.0052 (-0.0372, +0.0249) | -0.0025 (-0.0181, +0.0121) | -0.0066 (-0.0412, +0.0255) | -0.0032 (-0.0201, +0.0126) |

#### selection rule (favoured side >= 57%) - line + favoured side verified offered (quote logged, from 2026-10-15)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 86 | 14 | 0.606 | 0.512 (0.292, 0.726) | 0.496 | +0.015 (-0.203, +0.229) | 0.493 (69) | 0.588 (17) |
  kills: slope of (actual - original line) on (recorded projection - original line) -0.183 (-1.885, +1.388); MSE projection - MSE line +1.42 (-2.13, +4.44); 204 obs / 16 matches
  hs: slope of (actual - original line) on (recorded projection - original line) +0.733 (-0.412, +1.670); MSE projection - MSE line -0.49 (-2.08, +0.95); 179 obs / 16 matches

## Line movement after the decision (our own records; 2327 verifiable obs / 173 matches; 31 Railway obs after its export have no later record and are excluded here)
last verified line captured: median 52 min before the start (25% 20, 75% 450, share within 60 min 52%)
unchanged: 1635 obs / 172 matches | moved: 692 obs / 120 matches
move size distribution (last verified - original): {np.float64(-3.0): 6, np.float64(-2.0): 48, np.float64(-1.5): 8, np.float64(-1.0): 171, np.float64(-0.5): 31, np.float64(0.5): 56, np.float64(1.0): 274, np.float64(1.5): 16, np.float64(2.0): 61, np.float64(2.5): 1, np.float64(3.0): 13, np.float64(4.0): 6, np.float64(5.0): 1}
moved toward the recorded projection: 0.704 (0.652, 0.759); toward the side live favoured: 0.669 (0.615, 0.726)
  |move| 0.4-0.6: 87 obs / 40 matches; toward projection 0.713
  |move| 0.9-1.1: 445 obs / 109 matches; toward projection 0.688
  |move| 1.4-99: 160 obs / 69 matches; toward projection 0.744
slope of (actual - original line) on the move: +0.357 (-0.112, +0.850)

#### selection rule (favoured side >= 57%) - unchanged lines (scored at the ORIGINAL line)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 513 | 138 | 0.619 | 0.519 (0.460, 0.580) | 0.498 | +0.020 (-0.038, +0.082) | 0.467 (261) | 0.571 (252) |

#### selection rule (favoured side >= 57%) - moved lines (scored at the ORIGINAL line)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 263 | 86 | 0.617 | 0.540 (0.464, 0.612) | 0.496 | +0.044 (-0.031, +0.116) | 0.556 (151) | 0.518 (112) |

#### selection rule (favoured side >= 57%) - moved TOWARD the projection (scored at the ORIGINAL line)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 205 | 79 | 0.621 | 0.541 (0.464, 0.626) | 0.495 | +0.046 (-0.031, +0.130) | 0.582 (122) | 0.482 (83) |

#### selection rule (favoured side >= 57%) - moved AWAY from the projection (scored at the ORIGINAL line)
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 58 | 28 | 0.603 | 0.534 (0.362, 0.696) | 0.497 | +0.038 (-0.134, +0.196) | 0.448 (29) | 0.621 (29) |

## Closing benchmark (separate): the LAST line our board recorded before the start, with live's chance recorded then; 2324 obs / 173 matches

### last verified pre-start line: 2324 observations / 173 matches, days 2026-10-03 .. 2026-10-16, Over rate 0.486
| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) | log-loss gain vs flat (95%) | Brier gain vs flat (95%) | log-loss gain vs p_BASE (95%) | Brier gain vs p_BASE (95%) |
|---|---|---|---|---|---|---|---|---|---|
| flat | 0.6931 | 0.2500 | 0.500 | nan (nan, nan) | +0.014 (-0.029, +0.056) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) | +0.0013 (-0.0010, +0.0038) | +0.0007 (-0.0005, +0.0019) |
| p_BASE | 0.6945 | 0.2507 | 0.497 | -0.32 (-1.42, 0.92) | +0.010 (-0.032, +0.052) | -0.0013 (-0.0038, +0.0010) | -0.0007 (-0.0019, +0.0005) | +0.0000 (+0.0000, +0.0000) | +0.0000 (+0.0000, +0.0000) |
| p_close | 0.6991 | 0.2528 | 0.525 | 0.27 (-0.17, 0.66) | +0.010 (-0.032, +0.052) | -0.0060 (-0.0168, +0.0048) | -0.0028 (-0.0082, +0.0024) | -0.0047 (-0.0161, +0.0069) | -0.0022 (-0.0077, +0.0034) |

#### selection rule (favoured side >= 57%) - last verified pre-start line
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_close | 809 | 147 | 0.619 | 0.533 (0.478, 0.584) | 0.499 | +0.033 (-0.021, +0.085) | 0.499 (373) | 0.562 (436) |

## other-platform line logged at the decision (from 2026-10-15 03:26): 121 obs / 13 matches
  other line equal: 5 obs / 4 matches
  other line differs: 116 obs / 13 matches

#### selection rule (favoured side >= 57%) - other line differs
| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |
|---|---|---|---|---|---|---|---|---|
| p_live | 30 | 10 | 0.598 | 0.533 (0.263, 0.800) | 0.496 | +0.037 (-0.233, +0.301) | 0.522 (23) | 0.571 (7) |
