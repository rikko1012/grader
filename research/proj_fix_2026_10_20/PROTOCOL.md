# perf-3 candidate: projection fix (registered 2026-10-20, before any candidate number was computed)

Owner 2026-10-20: "FIX PROJECTIONS" (keep the rule: never fix calibration by lowering printed chances; fix the projection).

**Diagnosis that motivated it** (perf-2 rebuilt from 98,121 HLTV player-series, Nov 2025 - Oct 16 2026, live N0 inputs):
projections ~2.7% low overall; actual moves only 0.915 of the projected deviation (too extreme); heavy Elo underdogs
over-projected ~5%, favourites under-projected ~4%.

**Candidate (perf-3), three numbers on top of perf-2, applied per map:**
lambda3 = exp(alpha) x lambda2 ^ beta x exp(gamma x (p_elo - 0.5)), with p_elo = the team's pre-match Elo win chance
(start-of-day ratings from earlier results only - the cm-1 table method). Headshots = lambda3 x the player's HS per kill (unchanged).
Spreads stay perf-2's (C x sqrt(lambda)).

**Data / periods.** The accuracy study's HLTV series records (rl20/acc2/series_m1.pkl), live N0 sums per record.
FIT: match days 2025-12-01 .. 2026-06-30. HOLD-OUT: 2026-07-01 .. 2026-10-16 (scored once). Unit = player-series.
Fit: Poisson maximum likelihood on Maps 1-2 kills (offset log lambda2), one set of (alpha, beta, gamma).

**Pass rule (all required):**
1. HOLD-OUT Maps 1-2 kills MAE gain > 0 with a 95% match-grouped bootstrap interval above 0;
2. HOLD-OUT Map 1 kills MAE gain point >= 0; Maps 1-2 headshots MAE gain point >= 0;
3. HOLD-OUT bias closer to 0 than perf-2;
4. Real offered lines (P1 units Oct 11-19 that have a saved pre-match Elo in their cm-1 record): log-loss vs live not worse
   (point >= 0) - a secondary check, those days were already inspected.
Failing any -> no change. Passing -> live as a new model version; old forecasts unchanged; board audited before/after.

## Result (run once, 2026-10-20): FAIL - no change to live grading

FIT 58,003 player-series; HOLD-OUT 32,041 (3,208 matches). theta: alpha 0.552, beta 0.802, gamma 0.201.
| hold-out | MAE perf-2 | MAE perf-3 | gain (95% match-grouped) | bias perf-2 -> perf-3 |
|---|---|---|---|---|
| kills Maps 1-2 | 6.302 | 6.330 | -0.028 (-0.043, -0.013) | -0.43 -> +0.38 |
| kills Map 1 | 4.199 | 4.223 | -0.025 (-0.032, -0.018) | -0.15 -> +0.26 |
| HS Maps 1-2 | 4.037 | 4.045 | -0.008 (-0.015, -0.001) | -0.36 -> +0.06 |

Rule 1 fails (interval below 0). Why: perf-2's under-projection shrank month by month (Nov -1.04 -> Oct -0.46 kills), so a
level fitted on Dec-Jun overshoots Jul-Oct; with the spread correction (beta 0.80) the team-strength term is small (0.20).
Files: perf3_study.py, perf3_result.json.
