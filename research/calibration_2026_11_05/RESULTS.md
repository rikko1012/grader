# Calibration study cal-cs2lol-1: RESULTS

Run 2026-11-05, exactly as frozen in REGISTER.md (committed and pushed as 9e49edb before any diagnosis or fit). Shadow
research only: no live grade, chance, stake, CONFIG value or model hash changed, no production write, nothing deployed.

**Verdict: no candidate passed, for either sport. Nothing is added as a shadow and nothing can be promoted from this run.**

Full tables: `artifacts/tables.md`. Raw outputs: `artifacts/test_results.json` (every fit per day, every arm),
`artifacts/diagnosis.json`, console logs `artifacts/test_output.txt` and `artifacts/diagnosis_output.txt`.

## Data

- CS2: P1 export `at` 2026-11-05T05:21:45Z, 6,317 scored units, 356 matches, Oct 11 - Nov 4. The member Results
  numbers reproduce exactly (1,831 picks, 60.9% stated, 53.2% hit; A 11 at 76.4% / 45.5%; B+ 294 at 65.0% / 48.6%).
  The saved forecast record was found for all 6,367 unit forecasts (units + pushes).
- LoL: 1,840 rows of the Results cohort query, 1,795 graded, 38 series, Oct 17 - Nov 4. The member Results numbers
  reproduce exactly (1,037 offered-side units, 60.6% / 53.3%; A+ 27 at 83.7% / 63.0%; B 237 at 59.8% / 49.8%).
- One service restart during the pull (a 502 at about 05:30 UTC). It was the auto-deploy of main 9debb09, not this
  pull; the pull resumed on the same P1 snapshot. Every request was paged and paced at 0.3 s.

## 1. Where the overstatement comes from

Three mechanisms, measured separately on projection-vs-actual residuals (`scripts/diagnose.py`):

- **bias**: the mean projection minus the actual;
- **gap**: of the projection's distance from the line, how much shows up in the result. This is the OLS slope beta of
  (actual - line) on (projection - line). 1 means the whole gap is real; 0 means the line already knew everything;
- **spread**: whether the model's sd is too narrow, i.e. the SD of (actual - projection) divided by the model sd.

A probit refit p' = Phi(a + b z) is the same split in chance terms: a is a shift, and b is the share of the stated
confidence that is real.

### CS2: the gap, not bias and not the sd

| | n | bias proj - actual (95% CI) | gap slope beta (95% CI) | resid sd / model sd | probit b | MAE projection / line |
|---|---|---|---|---|---|---|
| all | 6,317 | -0.07 (-0.49, +0.31) | 0.26 (0.03, 0.48) | 0.99 | 0.26 | 4.93 / 4.89 |
| kills | 3,295 | -0.16 (-0.69, +0.36) | 0.20 (-0.05, 0.45) | 1.00 | 0.08 | 6.01 / 5.94 |
| headshots | 3,022 | +0.02 (-0.24, +0.27) | 0.44 (0.21, 0.67) | 0.99 | 0.45 | 3.75 / 3.75 |

- **No mean bias.** Projections are centred on the results, with intervals across 0.
- **The sd is right for the projection's own error.** The residual SD is 0.99 of the model sd.
- **The chance treats the projection's distance from the line as real, and on kills almost none of it is.**
  - About 20% of the kills gap shows up in results (interval includes 0), and about 44% of the headshots gap.
  - The line is as accurate as the projection: MAE 4.89 vs 4.93.
  - In chance terms, the kills probit slope is 0.08: almost none of the stated kills confidence is real. For
    headshots it is about half.
- **Selection makes this worse at the top.** The farther the projection sits from the line, the more of that distance
  is projection error. So the units picked for being far from the line carry the error in their own direction:
  - kills favoured Over: projection 0.53 too high;
  - kills favoured Under: projection 0.82 too low (-1.51, -0.17).
  - This is why B+ (63-68%) hit 48.6% below B: the B+ band's gap slope is 0.11, with a +0.72 projection error.
- **Over and Under are overstated alike.** Results cohort: Over 61.6% stated / 52.6% hit; Under 60.3% / 53.7%.
  This is not an Under problem in CS2.
- **Caps and margins are not the cause.** The stated chance equals the saved raw chance on 6,025 of 6,028 units
  (mean difference 0.0003).
- **Book and odds source do not explain it.** Underdog 61.0 / 52.1; PrizePicks 60.8 / 54.9; Thunderpick, Polymarket
  and no-odds sources all sit 7-10 points high.
- **The provisional BO3.gg route (perf-2-bo3) is the worst source,** at 62.7 / 46.2, but it is only 78 Results units
  and is already shadow-only.
- **Map 1 is anti-informative** (gap slope about -0.7, interval includes 0; 258 kills and 206 headshots units). It is already held
  off the member Results page.
- **Every model tag and week is overstated.** It is not one release.
- **Overall level is fine; the high chances are not.** On every confirmation unit the live favoured-side chance is
  close to right in the large (55.1% stated, 54.1% hit). The 50-55% bin hits 54.9%. The miss is that chances do not
  rise with the hit rate: 55-60% hits 53.4%, 60-65% hits 51.9% and 65-70% hits 56.5%.

### LoL: half the gap is real, and Overs carry the miss at a low-kill event

| | n | bias proj - actual (95% CI) | gap slope beta (95% CI) | resid sd / model sd | probit a / b | Over rate |
|---|---|---|---|---|---|---|
| all | 1,795 | +0.24 (-0.07, +0.57) | 0.50 (0.18, 0.86) | 0.92 | -0.15 / 0.41 | 41.4% |
| kills | 1,365 | +0.16 (-0.10, +0.42) | 0.57 (0.26, 0.90) | 0.93 | -0.12 / 0.53 | 41.9% |
| assists | 430 | +0.50 (-0.17, +1.24) | 0.35 (-0.02, 0.83) | 0.87 | -0.28 / -0.10 | 40.0% |

- **About half the projection-line gap is real for kills.** For assists it is little, with the interval reaching 0.
- **The sd is not too narrow.** If anything it is slightly wide (0.92).
- **The shift is downward.** Only 41% of lines went Over: this is the Worlds 2026 low-kill event already on record.
  The line was high as well (actual minus line -0.38).
- **The overstatement sits on Overs:**
  - Results cohort Over picks: 60.6% stated / 48.0% hit;
  - Under picks: 62.7% / 61.2%, close to right;
  - assists Overs: 58.4% / 37.9%;
  - kills favoured Over: projection 0.90 too high (0.36, 1.43).
  - This is the opposite of the owner's Under worry, at least for this event.
- **Lines that differ by book or type:**
  - Underdog balanced lines: 59.9 / 49.6;
  - PrizePicks standard: 62.1 / 58.3;
  - goblin: 66.2 / 59.3.
  - lol-1 (the 52 early Maps 1-3 units) hit 28.8%.
- **Unlike CS2, LoL chances rise with the hit rate.** Confirmation bins went from 48% to 75% hit. They are shifted
  about 7-10 points high and too steep.
- **Only 38 series, 11 in the confirmation window, nearly all one event.** Every LoL conclusion here is weak.

## 2. The registered test

Rolling origin by day, every fit on units settled before the test day, development segment for selection only.
Confirmation was Oct 28 - Nov 4 for both sports (CS2: live tag 3007c59b74).

### CS2

Development: 2,713 units, 154 matches. Confirmation: 2,055 units, 113 matches.

| candidate | dev log-loss gain (95% CI) | dev Brier gain | conf log-loss gain (95% CI) | conf Brier gain (95% CI) |
|---|---|---|---|---|
| C1 isotonic | +0.0041 (-0.0016, +0.0103) | +0.0019 | +0.0045 (-0.0010, +0.0103) | +0.0021 (-0.0005, +0.0049) |
| C2 Platt | +0.0048 (-0.0020, +0.0120) | +0.0023 | +0.0043 (-0.0022, +0.0111) | +0.0020 (-0.0012, +0.0053) |
| **C3 spread inflation (selected)** | +0.0059 (+0.0005, +0.0114) | +0.0028 | **+0.0040 (-0.0014, +0.0098)** | **+0.0018 (-0.0008, +0.0046)** |
| C4 mean-bias | -0.0015 (-0.0041, +0.0010) | -0.0007 | -0.0000 (-0.0011, +0.0010) | -0.0000 (-0.0006, +0.0005) |
| C5 gradecore/calibrate.py | +0.0029 (-0.0039, +0.0100) | +0.0013 | +0.0043 (-0.0014, +0.0104) | +0.0020 (-0.0008, +0.0049) |
| C6 per-side spread | +0.0052 (+0.0000, +0.0105) | +0.0025 | +0.0040 (-0.0015, +0.0098) | +0.0018 (-0.0008, +0.0046) |

**C3 was selected by the registered rule and FAILED.**

- Its log-loss and Brier intervals both include 0 (rules 1 and 2).
- It passed balance (46.4% favoured Over vs a 48.0% Over rate), no-prop-worse, and both stated-vs-hit checks
  (rules 3, 4, 5a, 5b).
- The fitted spread factor shows how much of the CS2 confidence is real:
  - kills hit the bound of 3.0 on every day. The chance is divided down toward 50% by three or more;
  - headshots ran 2.0-2.7.
  - Platt slopes agree: kills 0.0-0.14, headshots 0.27-0.50.
- The other candidates all score in the same narrow band (+0.004) and every interval includes 0. They are
  descriptive only.
- C4 (mean-bias) does nothing, because there is no mean bias to remove.

**What letters would look like after C3** (CS2 confirmation, Results rule with Map 1 off):

| arm | Results picks | stated | hit (95% CI) | letters |
|---|---|---|---|---|
| live | 542 | 60.8% | 53.5% (47.4-59.2%) | A+ 1, A 4, A- 17, B+ 94, B 426 |
| C3 | 29 | 58.7% | 48.3% (31.0-65.2%) | B+ 1, B 28 |

- On all 2,055 confirmation units, live gives A+ 1, A 4, A- 17, B+ 96, B 458, B- 306 and C 1,173.
- C3 gives B+ 1, B 28, B- 104 and C 1,922.
- **An honest calibration of today's CS2 chances removes about 95% of CS2 letters.** That follows from the
  diagnosis: the kills chance carries almost no information beyond the line.
- The same 542 live picks re-stated by C3 would read 54.2% against a 53.5% hit, which is calibrated. But no
  calibration can turn them into B grades that hit.

### LoL

Development: 1,096 units, 24 series. Confirmation: 570 units, 11 series.

| candidate | dev log-loss gain (95% CI) | conf log-loss gain (95% CI) | conf Brier gain (95% CI) | conf favoured Over share (Over rate 45.1%) |
|---|---|---|---|---|
| C1 isotonic | -0.0056 (-0.0251, +0.0142) | +0.0095 (-0.0288, +0.0506) | +0.0039 (-0.0132, +0.0221) | 14.2% (fails balance) |
| C2 Platt | -0.0094 (-0.0335, +0.0145) | +0.0103 (-0.0334, +0.0566) | +0.0043 (-0.0151, +0.0245) | 11.6% (fails balance) |
| C3 spread inflation | -0.0077 (-0.0209, +0.0047) | +0.0133 (-0.0244, +0.0535) | +0.0056 (-0.0102, +0.0225) | 37.2% |
| **C4 mean-bias (selected)** | +0.0021 (-0.0020, +0.0066) | **-0.0060 (-0.0149, +0.0036)** | **-0.0024 (-0.0060, +0.0014)** | 31.8% |
| C5 gradecore/calibrate.py | -0.0043 (-0.0101, -0.0000) | -0.0020 (-0.0254, +0.0203) | -0.0015 (-0.0122, +0.0080) | 19.5% (fails balance) |
| C6 per-side spread | -0.0060 (-0.0164, +0.0039) | +0.0048 (-0.0254, +0.0366) | +0.0017 (-0.0106, +0.0152) | 37.2% |

**C4 was the only candidate with a development gain on both scores, so the rule selected it. It FAILED.**

- On confirmation it was worse than live on both scores, with intervals including 0.
- Calibration in the large stayed 9.3 points high (65.1% vs 55.8%).
- C3 looks best on confirmation (+0.013), but that is descriptive only. It lost on development, its interval is
  wide (11 series) and the register forbids promoting a candidate not selected on development.
- Isotonic, Platt and gradecore swing the board to Unders (12-20% favoured Over vs a 45% Over rate). They would fail
  the A8a balance check the owner set for correlated Under risk. That is the same failure lc-1 had on Oct 26.

**What LoL letters would look like** (confirmation, offered-side Results cohort):

| arm | units | stated | hit | letters |
|---|---|---|---|---|
| live | 323 | 61.7% | 52.9% (44.5-61.4%) | A+ 14, A 30, A- 48, B+ 36, B 63, B- 26, Lean 106 |
| C4 (selected) | 312 | 61.8% | 51.9% | A+ 13, A 32, A- 40, B+ 45, B 58, B- 21, Lean 103 |
| C3 (descriptive) | 323 | 57.2% | 52.9% | A 9, A- 15, B+ 45, B 73, B- 29, Lean 152 |

## 3. What this means

1. **CS2 cannot be fixed by calibration without removing almost every letter.** The chances are not shifted or too
   narrow. The projection's distance from the line is mostly noise. The fix has to come from the projection: a center
   that uses the line's information and only moves off it where the data support it.
   - That is the line-anchored pl-2 challenger already in production shadow (300-match review).
   - Today's gap slope (0.20 for kills, 0.44 for headshots) is the size of the move it must justify.
   - No new challenger is opened here (owner, Oct 16: no new ~50% challengers without new information).
2. **LoL evidence is too thin and too concentrated** (11 confirmation series, almost all Worlds) to pass any
   calibration. The miss is Over-side and event-specific, which matches the open Worlds low-kill finding.
3. **Selection was not judged here.** Under C3 the 29 CS2 picks hit 48.3%; under live the 542 hit 53.5%. Neither is a
   pass or fail of this rule.

## 4. What the owner must decide

Nothing was promoted and no shadow was added. The register allows a shadow only for a passing candidate. Decisions:

1. **Whether to keep showing CS2/LoL letters while they are measured overstated.** The audit recommends containment
   (show the chance + Lean).
   - This conflicts with the standing rule against turning B into Lean as a calibration shortcut. Only the owner can
     choose between them.
   - This study supplies the evidence: CS2 B+ and above hit 50.9% on 391 Results-cohort units (199-192), stated about 66%.
2. **Whether to register a forward re-run of this same frozen rule** (same candidates and selection, confirmation =
   units after 2026-11-05).
   - Suggested trigger: at least 300 new CS2 matches and at least 30 new LoL series from at least two events.
   - Owner approval is needed before the clock starts. Until then, this run's confirmation window is inspected and is
     development data only.
3. **The public `/record` page** says "CS2 letters are live on the board" above the overstated numbers. Keeping it
   public is a product decision.

## Reproduce

```
python scripts/fetch_cs2.py <data>      # read-only, paged; P1 snapshot + saved forecast records
python scripts/fetch_lol.py <data>      # read-only SELECT, id windows
python scripts/run_test.py <data> artifacts/test_results.json
python scripts/diagnose.py <data> artifacts/diagnosis.json
python scripts/tables.py artifacts
```

The pulled data (player-level rows) is not committed. Snapshot hashes are in REGISTER.md. The result is deterministic
(fixed seed 20261105, 2,000 match-clustered draws).
