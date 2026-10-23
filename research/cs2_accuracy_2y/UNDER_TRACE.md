# UT-1: Under concentration, traced end to end (CS2, diagnostic)

Registered as UT-1 in REGISTER.md before anything ran. It is diagnostic only, on DEVELOPMENT data. Nothing was fitted, tuned or
promoted, and no live code was changed. Every interval is a match-grouped percentile bootstrap (B = 2000, seed 20261022).
Pushes on the board line are excluded and counted (P1 rule).

**Data.**
- P1 units: the stored learning export of 2026-10-23 04:29 UTC. That is 2,922 units on 172 matches, starts 2026-10-11 to
  10-22. 2,910 of them are scored (12 exact-50% ties have no side). 27 pushes are excluded: kills Maps 1-2 8, HS Maps 1-2 16,
  HS Map 1 3. The other P1 exclusions are listed in `under_trace/out/report.md`.
- Each unit is joined to its forecast row and its saved grader inputs (bounded `forecasts_page` pages, 0 missing).
- The HLTV database was read once, SELECT only (cm1.SQL rows since 2025-09-01 plus the N0 sets). That read supplies replays,
  rounds and OT.
- 2,631 lines are on canonical HLTV matches with every map in scope scored. On those, the settlement equals HLTV on all
  1,378 kills and 1,253 HS lines.
- Projection sources on P1: perf-2 2,216, perf-cm2 600, perf-2-bo3 106, **legacy 0**. So the legacy Over margin never touched
  a P1 line (see section 5).

Scripts are in `under_trace/`:
- `pull_p1.py` and `pull_fc.py` pull the data.
- `build_units.py` builds the per-line decomposition and the replays.
- `report.py` writes sections 1-4.
- `mechanisms.py` writes the step-out, N0, rounds and cm-2 tests.
- `ot_trace.py` writes the OT addendum.
- `board_now.py` reads the current board.
- `maps123_rounds.py` checks the Maps 1-3 rounds.

The generated tables are in `under_trace/out/`.

## Corrections 2026-10-23 (owner review)

The owner found overstated conclusions in this file. The original text is kept unchanged; each overstated sentence is prefixed
**[Superseded 2026-10-23: Cn]** and is replaced by item Cn here. No original number was changed. New computations:
`corrections/signed_bias.py` -> `corrections/results/signed_bias.md` / `.json`.

**C3. Similar Over / Under hit rates (and "symmetric" overconfidence) do not establish that there is no directional projection
bias.** A signed bias can coexist with equal hit rates on both sides (for example when it is offset by where the lines sit, or
when it is concentrated in one scope, tag or side). Measured directly now, on the same P1 units (data/p1_units.json,
2026-10-23T04:29:55Z; pushes excluded), split into the DEVELOPMENT set (start < 2026-10-22 00:00 UTC, 2,692 u / 155 m) and the
CONFIRMATION set (start >= 2026-10-22, 230 u / 15 m, descriptive only). Note: section 3 below pooled both sets (1,355 kills Maps
1-2 lines / 151 m); the table here separates them. Projection = the unit's saved lam; match-grouped 95% bootstrap, B = 2,000.

| set | stat / scope | units / m | actual - projection | projection - line | actual - line |
|---|---|---|---|---|---|
| development | kills Map 1 | 150 / 19 | -0.81 [-2.04, +0.59] | +0.20 [-0.00, +0.39] | -0.61 [-1.74, +0.70] |
| development | kills Maps 1-2 | 1,233 / 136 | +0.07 [-0.70, +0.92] | +0.13 [-0.03, +0.30] | +0.20 [-0.57, +1.04] |
| development | HS Map 1 | 127 / 19 | -0.13 [-0.75, +0.43] | **+0.17 [+0.03, +0.31]** | +0.04 [-0.51, +0.54] |
| development | HS Maps 1-2 | 1,142 / 136 | -0.03 [-0.46, +0.42] | **+0.16 [+0.08, +0.24]** | +0.13 [-0.31, +0.58] |
| development | Maps 1-3 kills / HS | 20 / 2 each | +3.08 / +0.97 | -0.88 / -0.45 | +2.20 / +0.53 (2 m: no inference) |
| confirmation | kills Maps 1-2 | 127 / 15 | **+3.76 [+0.45, +7.01]** | -0.34 [-0.65, +0.06] | **+3.42 [+0.14, +6.67]** |
| confirmation | HS Maps 1-2 | 103 / 14 | +1.62 [-0.44, +3.68] | -0.19 [-0.46, +0.13] | +1.43 [-0.62, +3.51] |

By favoured side (development; logged p > 0.5 = Over-favoured), actual - projection: kills Map 1 Over **-1.84 [-3.55, -0.13]**
(82 u / 17 m), Under +0.45 [-0.81, +1.87]; kills Maps 1-2 Over -0.69 [-1.69, +0.47] (612 u / 125 m), Under +0.83 [-0.12, +1.84]
(618 u / 120 m); HS Maps 1-2 Over -0.51 [-1.16, +0.21], Under +0.35 [-0.09, +0.79]. On every Map 1 / Maps 1-2 cell the
projection overshoots in the direction of the favoured side (Over-favoured too high, Under-favoured too low); individually only
kills Map 1 Over-favoured is significant. By version tag (cells >= 10 m): kills Maps 1-2 9cfcbd217a Under-favoured **+1.88
[+0.11, +3.69]** (152 u / 26 m: those Under-favoured projections were too low); HS Maps 1-2 a310d76b55 Over-favoured **-1.29
[-2.48, -0.06]** (33 u / 10 m); projection - line for 6f6e2d39d7 kills Maps 1-2 **+0.58 [+0.10, +1.07]** and HS **+0.43 [+0.13,
+0.72]** (16 m). 3 of 48 actual - projection cells with >= 10 matches exclude 0 at nominal 95% (~2.4 expected by chance; the
cells overlap). By projection source: perf-2-bo3 kills Map 1 -7.17 (13 u / 4 m) and HS Map 1 -5.15 (6 u / 2 m) are large
negatives on very few matches (consistent with AU-1 / ED-1 on the BO3.gg fallback route).

What the data shows: (i) no significant signed actual - projection error at the stat x scope level in development, with
intervals of about +-0.8 kills (Maps 1-2) and +-1.3 kills (Map 1) - a bias of that size is not excluded; (ii) HS projections sat
significantly above the lines (+0.16 / +0.17 HS) while HS outcomes did not (actual - line +0.13 / +0.04, n.s.); (iii) within
sides the signed errors point toward the line (selection on projection error; significant for kills Map 1 Over-favoured and in
two version-tag cells); (iv) the confirmation slate (f663af8917, Oct 22) had a significant positive kills error (+3.76) -
descriptive only, an overtime-heavy slate. "No systematic below-line projection", "not systematically low", "symmetric",
"not an Under-direction bias" below are superseded by (i)-(iv).

**C4. "Lines sit above the players' history" describes the disagreement; it does not show our projections are right.** The
measured fact is: on the Oct 15 / Oct 22 / Oct 23 slates, the players' raw-history baseline minus the line was lower than usual
(+0.16 kills Oct 22, -0.27 Oct 23, about +0.6 normally), and the prior and opponent steps then moved projections further below
those lines. Which side was closer is answered only by settled outcomes: for the Oct 22 slate (confirmation set, 127 kills
Maps 1-2 u / 15 m) actual - line +3.42 [+0.14, +6.67] and actual - projection +3.76 [+0.45, +7.01], i.e. the outcomes landed
above both, closer to the lines than to the projections (descriptive; OT-heavy). The Oct 23 board has no settled outcomes in
this data. The prior-shrink and opponent step-out tests (section 4) measure whether each step reduces error on the HLTV
replays; they do not validate the projection-vs-line disagreement on a particular slate.

**C1 (as in ERROR_BREAKDOWN.md C1). "OT is not predictable before the match"** is replaced by: no tested pre-match proxy (pair
prior OT frequency terciles over 365 days, market closeness, cm-2 Elo gap; 129 Maps 1-2 matches, realized OT series 27.9% /
34.9% / 25.6% by tercile, intervals about +-13 pts) separated OT-prone matches; Codex CX-A (prior OT / close-map frequency) moved
rounds MAE by +0.0099 [+0.0034, +0.0159] rounds. Untested inputs are not covered.

## Answer in one paragraph
**[Superseded 2026-10-23: C3]** **Over the whole P1 period, projections are not systematically below the real lines.**

| Line type | mean(projection − line) | mean(actual − line) |
|---|---|---|
| Kills Maps 1-2 | +0.09 [−0.06, +0.24] | +0.50 [−0.29, +1.34] |
| HS Maps 1-2 | +0.13 [+0.06, +0.21] | +0.25 [−0.17, +0.71] |

The Under share of graded (55%+) picks has two different patterns:
- **Kills: no steady Under lean.** The share is 48.8% [42.3, 55.3].
- **HS: a mild Under lean, 59.1% [53.6, 64.4].** It comes mostly from the right-skewed HS distribution, which outcomes confirm
  (see G).

**The concentration you see on the board is day- and slate-driven.** **[Superseded 2026-10-23: C4]** On Oct 15, Oct 22 and the current Oct 23 board, the lines
sit at or above what the players actually average. The model's raw-history baseline minus the line was +0.16 kills on Oct 22
and −0.27 on Oct 23, against about +0.6 normally. The two steps that always lower projections then push those lines to Under:
- the prior shrink, −0.49 kills on average;
- the opponent factor, −0.27 kills on average.

Both steps are **justified by outcomes**. Removing either one makes the error worse, with intervals excluding zero on the
rounds-normalised actual.

**Unders did not win at their printed rate, and neither did Overs.** **[Superseded 2026-10-23: C3]** The overconfidence is about the same on both sides:

| Graded picks | Under: won vs printed | Over: won vs printed |
|---|---|---|
| Kills Maps 1-2 | 51.1% vs 58.8% | 51.0% vs 60.5% |
| HS Maps 1-2 | 55.1% vs 59.2% | 55.5% vs 60.1% |

**Bad days for Unders are overtime days.** That loss is correlated and was not predictable before the match from any proxy
tested. **No implementation bug was found in the projection path.** Every step reproduces exactly (section 5).

## 1. How concentrated are Unders (P1 lines; graded = favoured side ≥ 55%)
| group | P1 lines / matches | Under share, all lines | graded lines | Under share, graded [95% CI] |
|---|---|---|---|---|
| kills Map 1 | 150 / 19 | 45.3% | 64 | 42.2% [28.7, 59.6] |
| kills Maps 1-2 | 1355 / 151 | 51.4% | 662 | 48.8% [42.3, 55.3] |
| kills Maps 1-3 | 20 / 2 | 60.0% | 12 | 83% (2 matches - no inference) |
| HS Map 1 | 127 / 19 | 52.8% | 53 | 54.7% [38.6, 72.9] |
| HS Maps 1-2 | 1238 / 150 | 58.0% | 626 | 59.1% [53.6, 64.4] |
| HS Maps 1-3 | 20 / 2 | 80.0% | 10 | 90% (2 matches) |

**By version tag** (graded Maps 1-2 lines, kills / HS Under share). The full table is in `out/report.md`.

| Tag | Kills Under share | HS Under share |
|---|---|---|
| 6f6e2d39d7 (Oct 13-15) | 35% | 49% |
| a310d76b55 (Oct 15-16) | 66% | 72% |
| 9cfcbd217a (Oct 16-18) | 56% | 56% |
| c36104d079 (Oct 19-20) | 60% | 64% |
| 794a775f51 (Oct 20-21, cm-2 kills live) | 42% | 59% |
| f663af8917 (Oct 22, hs-cm2 live) | 71% | 80% |

**By day.** Graded kills Under share runs 34% (Oct 14) to 71% (Oct 22). HS runs 47% to 80%.

**Current board** (2026-10-23 04:45 UTC, 304 standard lines, all f663af8917):
- **Kills, perf-cm2: graded Under share is 90%** (70 graded lines). The decomposition is:
  - raw × R − line −0.27;
  - prior −0.35;
  - environment +0.20;
  - opponent −0.42;
  - cm-2 +0.13;
  - projection − line −0.72.
- **HS, perf-cm2: graded Under share is 91%** (55 graded lines). Projection − line is −0.33.
- **[Superseded 2026-10-23: C4]** Today's lines sit above the listed players' own raw history. That is the main reason, and it is a property of the slate, not
  of a model step.

## 2. The calculation, step by step (decomposition across ALL eligible lines)
perf-2, with R = 21.74 × maps:
- raw = his time-weighted kills per round, no prior. It is recovered exactly from the saved numbers:
  k = rate_all (r + 300) − 300 × 0.66442 × 0.93.
- Each step is measured in kills, or × HS-per-kill on HS lines:
  - **prior** = rate_all − raw: the shrink toward 0.93 × pop over 300 rounds.
  - **environment** = rate − rate_all: tonight's LAN or online sample, weight 800.
  - **opponent** = rate × (opp − 1).
  - **cm-2** = the cm-2 projection − the perf-2 projection, on perf-cm2 lines.
  - **pmf** = the HS distribution mean − λk × HS per kill.
- The steps add up exactly. Mean over lines:

| group | source | lines/matches | raw×R − line | prior | env | opponent | cm-2 | pmf | = proj − line |
|---|---|---|---|---|---|---|---|---|---|
| kills Maps 1-2 | perf-2 | 809/91 | +0.60 | −0.50 | +0.24 | −0.29 | 0 | 0 | +0.05 [−0.11, +0.22] |
| kills Maps 1-2 | perf-cm2 | 500/57 | +0.60 | −0.44 | +0.15 | −0.27 | +0.10 | 0 | +0.15 [−0.14, +0.44] |
| kills Map 1 | perf-2 | 137/19 | +0.13 | −0.23 | +0.03 | +0.09 | 0 | 0 | +0.02 |
| HS Maps 1-2 | perf-2 | 1100/132 | +0.43 | −0.24 | +0.11 | −0.14 | 0 | 0 | +0.16 [+0.08, +0.24] |
| HS Maps 1-2 | perf-cm2 | 97/14 | +0.03 | −0.24 | +0.08 | −0.21 | +0.10 | 0 | −0.24 |
| **graded Unders**, kills Maps 1-2 | perf-2 | 201/75 | **−0.90** | −0.63 | +0.25 | −0.48 | 0 | 0 | −1.75 |
| **graded Unders**, kills Maps 1-2 | perf-cm2 | 107/32 | **−0.41** | −0.59 | +0.16 | −0.65 | −0.18 | 0 | −1.67 |
| **graded Unders**, HS Maps 1-2 | perf-2 | 315/111 | **−0.57** | −0.24 | +0.08 | −0.21 | 0 | 0 | −0.93 |

**For graded Unders, about half the gap is the line itself** **[Superseded 2026-10-23: C4]** (the line is above his raw history). The other half is the prior
shrink plus the opponent factor. All CIs and the Map 1, Maps 1-3 and bo3 rows are in `out/report.md`.

**cm-2** (597 of 597 lines recompute λ to 1e-6). At the training mean, exp(intercept) is 28.50 kills. On the P1 lines:
- The feature total is +0.22 kills.
- The largest contributions are:
  - opponent-adjusted player rate, +1.72;
  - all-environment rate, −1.47;
  - current-opponent factor (D), −0.60;
  - weighted rounds, +0.47.
- On graded Unders, the current-opponent factor is −1.18 kills and the all-environment rate is −1.83, against an adjusted
  rate of +2.43.

**Two representative Under traces:**

1. **perf-cm2 kills Under, hltv:2399622** (tag 794a775f51, line 30.5, printed P(Over) 38.9%, actual 18 in 42 rounds).
   - raw 0.6966 kpr × 43.48 = 30.29;
   - prior −0.54 (1,606 weighted rounds) → rate_all 0.6842;
   - environment +0.18 (LAN, 986 rounds) → 0.6883;
   - opponent 0.9604 → −1.19, giving perf-2 28.74;
   - cm-2 swap −0.45, giving 28.29. The largest cm-2 contributions are the adjusted rate +0.166 log-points, all-env rate
     −0.086 and current-opponent factor −0.065.
   - At sd 7.9 that is 38.9% Over. The N0 vs full-history replay barely differs (28.74 vs 28.66).
2. **perf-2 HS Under, hltv:2399661** (tag 9cfcbd217a, line 13.5, printed 33.7% Over, actual 13).
   - raw 0.5489 × 43.48 × 0.501 HS per kill = 11.96;
   - prior +0.35 (his rate is below the prior target);
   - environment −0.30 (online);
   - opponent −0.20;
   - result 11.82. Full history would have given 12.59.

## 3. Projection vs line vs actual (pushes excluded)
| group | lines/matches | mean(proj − line) | mean(actual − line) | mean(actual − proj) | Over rate |
|---|---|---|---|---|---|
| kills Map 1 | 150/19 | +0.20 [−0.01, +0.39] | −0.61 [−1.78, +0.64] | −0.81 [−2.06, +0.53] | 39.3% |
| kills Maps 1-2 | 1355/151 | +0.09 [−0.06, +0.24] | +0.50 [−0.29, +1.34] | +0.41 [−0.36, +1.25] | 50.6% |
| HS Map 1 | 127/19 | +0.17 [+0.03, +0.31] | +0.04 [−0.53, +0.53] | −0.13 [−0.76, +0.44] | 48.0% |
| HS Maps 1-2 | 1238/150 | +0.13 [+0.06, +0.21] | +0.25 [−0.17, +0.71] | +0.12 [−0.30, +0.58] | 49.1% |
| Maps 1-3 (both stats) | 20+20/2 | −0.88 / −0.45 | +2.20 / +0.53 | +3.08 / +0.97 | 2 matches only |

- **[Superseded 2026-10-23: C3]** **The lines are not above actual outcomes on average.** Unders are therefore not favoured by the lines, and the projection is
  not systematically low.
- **f663af8917 (Oct 22)** had projection − line −0.35 against actual − line **+3.36** kills. Its 15 matches ran +5.4 rounds above
  R, with 8 overtime series.

**Rounds-normalised error** (section A of `out/mechanisms.md`). The error is split as actual − projection = rate part + rounds
part:
- Kills Maps 1-2: the rate part is +0.02 [−0.20, +0.22], so the per-round level is unbiased. The rounds part is +0.51
  [−0.32, +1.38].
- Graded Unders: rate part **+0.85 [+0.15, +1.63]**. Graded Overs: **−1.09 [−1.71, −0.45]**.

**[Superseded 2026-10-23: C3]** **This is selection on projection error, and it is symmetric.** When the model departs from the line, part of the departure is
its own noise. The line carries information. This explains the overconfidence on both sides. It is not an Under-direction bias.

**Printed vs won** (graded; full bands in `out/report.md` section 4):

| group | side | lines/matches | printed | won [95% CI] | won − printed |
|---|---|---|---|---|---|
| kills Maps 1-2 | Under | 323/113 | 58.8% | 51.1% [43.6, 57.8] | −0.077 [−0.153, −0.010] |
| kills Maps 1-2 | Over | 339/102 | 60.5% | 51.0% [44.7, 57.4] | −0.095 [−0.159, −0.030] |
| HS Maps 1-2 | Under | 370/128 | 59.2% | 55.1% [49.0, 61.4] | −0.040 [−0.102, +0.022] |
| HS Maps 1-2 | Over | 256/105 | 60.1% | 55.5% [48.2, 62.8] | −0.047 [−0.119, +0.028] |
| kills Map 1 | Under / Over | 27/14, 37/13 | 57.1% / 60.3% | 51.9% / 29.7% | |
| HS Map 1 | Under / Over | 29/15, 24/11 | 59.6% / 58.7% | 37.9% / 41.7% | |

- **Printed vs won by projection gap (kills Maps 1-2):**
  - Gap −1 to +1: printed and won agree within 3 points.
  - Gap below −1: printed 41.1% Over, observed 49.1%.
  - Gap above +1.5: printed 62.6% Over, observed 51.3%.
- **So confidence beyond about a 1-kill gap is not earned on either side.**
- By version, no tag shows Unders significantly better or worse than Overs. f663af8917 kills Unders went 30.0% (30 lines, 10
  matches, CI [5.6, 60.0]). That is an overtime day (OT section).

## 4. Mechanism tests (what pushes projections below lines, and is it justified?)
These are step-out tests: the projection without one step, scored against the actual and against the rounds-normalised actual
(actual × R / rounds played). Full tables are in `out/mechanisms.md` C-E.

| step (kills Maps 1-2) | mean size | normalised bias with / without | normalised \|err\| gain from the step |
|---|---|---|---|
| prior shrink | −0.49 [−0.54, −0.44] | +0.02 / −0.46 | **+0.064 [+0.009, +0.127]** (justified) |
| opponent factor | −0.27 [−0.35, −0.17] | +0.02 / −0.26 | +0.021 [−0.013, +0.057] (HS: **+0.034 [+0.013, +0.056]**) |
| environment blend | +0.20 | +0.02 / +0.22 | +0.005 (neutral) |
| cm-2 swap (500 lines) | +0.10 | −0.02 / +0.09 | +0.038 [−0.024, +0.104] (neutral) |

- **Prior shrink.** By history depth (800-1500 weighted rounds), the normalised bias is +0.04 with the shrink and −0.43 without
  it. **The pull toward 0.93 × pop is regression to the mean that outcomes confirm. It is not over-applied.** HS shows the same
  pattern: |err| gain +0.045 [+0.017, +0.074].
- **Opponent factor.**
  - For opponents with a factor of 0.95-0.99 (656 lines), the normalised bias is −0.10 with the factor and −0.88 without it.
    Those lines have a graded Under share of 61%.
  - For opponents above 1.01, it is +0.04 with and +0.72 without. Those lines have a graded Under share of 30%.
  - **The sign is right, the size is right, and it is applied once.** P1 opponents are on average stingier than the HLTV
    average (mean step −0.27), which is a true property of the matches that get lined.
- **Expected rounds (fixed 21.74 per map).**
  - P1 maps: map 1 +0.04 [−0.72, +0.80], map 2 +0.37 [−0.51, +1.28], map 3 +0.07 rounds.
  - HLTV-wide 2026: per-map rounds are 0 to 0.66 below 21.74 every month. The October map 1 figure is −0.45 [−0.77, −0.16].
  - **R is not why projections sit below lines.** HLTV-wide, R is if anything slightly high.
  - Maps 1-3 settle only when map 3 is played. Those series average +0.28 [+0.01, +0.56] rounds over 3 × 21.74, which is
    about +0.2 kills under-projected. That is real but negligible, and it is a design choice, not a bug.
- **N0 pin.** Replaying perf-2 on N0 rows reproduces the logged projection on the 1,560 n0-r4 lines: within 0.01 on 1,406 and
  within 0.43 on all. Full history raises projections by:
  - kills Maps 1-2: +0.23 [+0.18, +0.27];
  - HS Maps 1-2: +0.12 [+0.09, +0.16];
  - thin N0 history (under 500 rounds): +0.55.

  The N0 pin therefore shifts sides:
  - It produced 158 kills and 138 HS side flips.
  - The Under side share on kills Maps 1-2 is 54.1% under N0 and 48.7% on full history.

  **Accuracy does not separate the two.** The |err| gain from full history is −0.002 [−0.044, +0.036], and +0.019
  [−0.017, +0.062] normalised. Since Oct 20, kills on cm-2 (full tables) is out of N0's reach. The pin still drives perf-2
  lines: Map 1, Maps 1-3 and every non-cm-2 HS line.
- **Market inputs.** perf-2 and cm-2 take no market input. The only market effect is the stake cap. No P1 line used the legacy
  path, which takes the win price into the projection. **Market inputs do not move the projection or the side.**
- **Repeated or double-counted adjustments: none found.**
  - The published λ equals the model λ: kills to rounding, HS to the pmf mean, |d| ≤ 0.05.
  - The logged perf-2 numbers recompute exactly from the decomposition.
  - cm-2 recomputes exactly from its saved sums.
  - The environment blend shrinks toward the already-shrunk rate_all by design. cm-2's two current-opponent features are fitted
    jointly, so neither is counted twice.
  - No margin or gap compression applies under perf.
- **HS distribution shape** (G). 147 HS Maps 1-2 lines have the projection above the line but an Under chance. None of them is
  graded. Printed P(Over) was 49.0% and observed Over 49.0% [40.6, 57.2]. Near-line calibration is exact:
  - gap 0 to +0.5: printed 0.499, observed 0.500;
  - gap −0.5 to 0: printed 0.461, observed 0.465.

  **The right skew is real, so the HS Under lean it causes is justified.**

## 5. Implementation bugs
**No implementation bug in the live projection or chance path.** The checks were:
- the perf-2 replay;
- the decomposition identity;
- the cm-2 recompute;
- λ → chance;
- the sign of each step;
- rounds;
- the settlement vs HLTV comparison.

Notes for the record (not bugs in the P1 window):
- **grader.py:76 and :793 — legacy Over margin.** The `over_margin` (Overs scored as if projected 1.0 kill / 0.6 HS lower) is
  still active on the legacy path. It is a deliberate Under tilt dating from 2026-10-06. It applied to **0 of 2,922** P1 lines,
  but it would tilt any perf-unavailable fallback grade toward Under.
  - Proposal: if the owner wants fallback grades side-neutral, set `over_margin` to 0 under a new version tag.
  - Regression test: `grade_leg` with `perf=None` and a symmetric gap gives a symmetric |p − 0.5| for Over and Under.
  - Owner decision; not a correctness bug.
- **cs2_scout.py:1427 — perf-2-bo3 history window.** It uses `date.today() − 420` for the opponent-history window instead of
  the match day. This is cosmetic: the edge weight is e^−3.5 and the effect is under 0.01 kills.
  - Proposal: `since = (date.fromordinal(day) − timedelta(days=420))`.
  - Regression test: the bo3 path builds the same since as perf_live for a future match day.

## OT addendum (coordinator request, after Codex's f663af8917 finding)
`out/ot_trace.md`; ALL tags, 2,631 HLTV-scored P1 lines. A map is OT when its final score totals more than 24 rounds.

**(a) Realised OT.**
- **29.2% [21.9, 37.2] of P1 Maps 1-2 series had OT** (40 of 137). Codex measured 21.5% across all stored series, so lined
  matches run longer.
- Kills Maps 1-2:
  - Non-OT series: actual − projection −2.12 [−2.71, −1.57]; Over rate 38.7%; graded Unders won 66.7% [59.4, 73.6].
  - OT series: actual − projection **+6.79 [+5.52, +8.07]**; Over rate 80.5%; graded Unders won **20.9% [14.0, 29.4]**.
- HS Maps 1-2:
  - Non-OT: actual − projection −1.10; graded Unders won 68.2%.
  - OT: actual − projection +3.02; graded Unders won 32.7%.
- Graded Under hit rate in OT series falls with every tag (0-54%). It is 66-83% in non-OT series. Two examples:
  - 794a775f51: 83/130 non-OT, 7/41 OT.
  - f663af8917: 20/24 non-OT, 8/47 OT.
- This split conditions on the outcome, so a well-calibrated model shows it too. **The real questions are (b) and (c).**

**(b) Does the pre-match distribution price OT-length totals?**
- **Overall, yes.** For kills Maps 1-2, mean PIT is 0.507 [0.478, 0.538]. The top PIT decile holds 11.9% [8.8, 15.2] (10%
  expected) and the bottom 8.0% [6.2, 9.9]. Printed P(Over) is 50.4% against an observed 51.1%. HS is the same: PIT 0.504,
  top 10.8%, bottom 7.8%.
- The model's mean and spread already include the average OT mass. The upper tail is slightly thin, but the interval includes
  10%.
- **No pre-match proxy separates OT-prone matches:**
  - Both teams' prior OT frequency (365 days, terciles): realised OT series 27.9% / 34.9% / 25.6%. Printed vs observed Over
    within 4 points in each tercile.
  - Market closeness and the cm-2 Elo gap do no better.
- **[Superseded 2026-10-23: C1]** **OT is not foreseeable from these proxies.** Codex's round study found the same: prior OT and close-map frequency did not
  beat the baseline.

**(c) How many Under losses OT explains.**
- **Kills Maps 1-2 graded Unders went 152-135.**
  - 68 of the losses (50%) came in OT series.
  - 27 (20%) flip to wins when his total is scaled to regulation rounds. That is a proportional counterfactual, since kills
    are not stored per OT round.
  - The other 108 are level losses.
  - Counterfactual Under win rate with no OT rounds: 62.4%.
- HS Maps 1-2 graded Unders went 186-144: 51% of the losses were in OT series and 18% are OT-explained. Counterfactual win
  rate: 64.2%.
- The mirror image holds for Overs: kills Overs keep 47.0% without the 16 wins that needed OT.

**So OT is the main source of the variance and of the correlation behind bad Under days.** Half of all graded Under losses
come in OT series, and whole slates move together (Oct 22: +5.4 rounds). It is not a level bias the model could have priced
from the pre-match information tested. The distribution already carries the average OT mass.

## What this means (diagnostic; no change proposed to live grading)
- **Direction.** **[Superseded 2026-10-23: C3, C4]** Unders are concentrated on HS (skew, justified) and on slates whose lines sit above the players' history (Oct
  15, 22 and 23). The prior and opponent steps add to that and both pass step-out tests. **None of this is a bug, and there is
  no systematic below-line projection.**
- **Confidence.** Picks more than about 1 kill from the line are overconfident on both sides, by about 8-10 points for kills and
  4-5 for HS. That is the open accuracy problem. Following the grades-accuracy rule, the fix belongs in a tested challenger, not
  in lowering percentages.
- **Correlated risk.** Same-slate Unders share overtime risk (the owner's Oct 6 concern). **[Superseded 2026-10-23: C1]** Since OT is not predictable before the
  match, the lever is exposure (slip / slate concentration), not the projection.
