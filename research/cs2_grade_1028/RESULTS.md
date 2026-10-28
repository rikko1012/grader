# CS2 grading check 2026-10-28 - results (rules registered in REGISTER.md, commit 38fb79d, before any number below)

Data: production exports pulled 2026-10-28 16:10-16:45 UTC (picks 16,768 rows; P1 units 4,376 / 249 matches, starts
Oct 11-28; forecasts 107,578 rows; fh-1 records 20,101; learning dashboard 16:07:22Z, live tag `2085aa791c`). Every
table: percentages, W-L-P, match-grouped bootstrap 95% intervals. Full per-level tables: LEVELS_P1.md (registered
product rule), LEVELS_PICKS_BOARD.md (first grade, platform board lines), LEVELS_PICKS_ALL.md (first grade incl.
member-typed lines). Scripts in `scripts/`, raw outputs in `outputs/`.

Rule labels: **P1** = latest board forecast >= 30 min before the start on a standard offered line (4,376 units, 0 cross-
platform duplicates by construction, 0 rows after start). **PICKS** = the first published grade per exact line and platform
(board lines 11,232 rows / 321 matches after dropping 711 void, 596 open, 36 logged after the start, 6 owner tests; a
line offered on both books is one row per platform - 4,285 of 11,042 distinct lines were offered on 2+ platforms).

## 1. Biggest findings (P1 unless stated)

| finding | n (units / matches) | stated % | hit % [95%] | gap pts [95%] |
|---|---|---|---|---|
| **Staked picks (B- or better) are 7 pts overconfident**; Leans are calibrated | 1,664 / 246 | 59.2 | 52.0 [48.4, 55.2] | +7.2 [+4.0, +10.7] |
| ... and the grade ORDER does not hold: Lean 52.4% > B band 51.8% (A band 56.5% on 62 units) | 2,712 / 1,602 / 62 | 53.4 / 58.8 / 70.5 | 52.4 / 51.8 / 56.5 | +1.0 / +7.0 / +14.0 |
| **B+ is the worst letter**: 65% printed, under half hit | 178 / 91 | 65.0 | 48.9 [39.4, 58.6] | +16.1 [+6.4, +25.6] |
| **BO3.gg-route projections (perf-2-bo3, PROVISIONAL) are anti-signal** | 148 / 32 (PICKS board 259 / 34: 37.4%) | 58.8 | 33.8 [24.2, 44.8] | +25.0 [+13.7, +35.3] |
| **Map 1 lines** (and BO1 series) miss | 277 / 19 (BO1 261 / 17: 44.4%) | 55.2 | 44.4 [37.0, 51.8] | +10.8 [+3.3, +18.3] |
| Kills are worse than HS (kills Maps 1-2 52.1%, HS Maps 1-2 53.8%) | 2,269 / 2,107 | 55.5 / 55.7 | 51.5 / 53.1 | +4.0 / +2.6 |
| Overtime series: projection 3.9 below actual, picks hit 49.6% (no-OT series +1.45 above, 53.6%) | 1,362 / 76 | 55.5 | 49.6 [45.5, 53.9] | +5.9 [+1.6, +10.0] |
| Rank 100+ tier and non-HLTV matches miss | 538 / 36 and 171 / 11 | 56.0 / 56.4 | 47.6 / 43.3 | +8.4 / +13.1 |
| No market price at all (odds source none): 49.3% | 1,061 / 68 | 56.4 | 49.3 [44.8, 53.9] | +7.1 [+2.5, +11.5] |
| Demon lines (PICKS board): 44.7% hit, projection 2.2 below actual | 132 / 15 | 58.0 | 44.7 [33.6, 55.7] | +13.3 [+1.6, +24.6] |
| Live tag `2085aa791c` (Oct 26-28) is the first tag at or above its stated chance | 688 / 37 | 54.8 | 58.0 [53.7, 61.8] | -3.1 [-7.0, +1.4] |

Overall P1: 4,376 units, 2287-2089-0, stated 55.6%, hit 52.3% [50.3, 54.4], gap +3.3 [+1.1, +5.4], Brier 0.2496,
log loss 0.6987, bias -0.21, Over share 43.6% / Over hit 49.5% (balance gap 5.9 pts, inside the 15-pt rule).
Member-typed lines (PICKS only, not a product cohort): 4,187 rows, 62.7% stated, 60.0% hit - every A / A+ of the first-grade
set lives there (board A band: 154 rows, 70.7% stated, 56.9% hit).

## 2. Level tables (P1; the PICKS-board figure in brackets where it differs by more than 2 pts)

| level | group | n | W-L-P | stated | hit [95%] | gap | bias | Over share / Over hit |
|---|---|---:|---|---:|---|---|---:|---|
| platform | Underdog | 2,722 | 1400-1322 | 55.6 | 51.4 [49.0, 54.1] | +4.1 | -0.08 | 44.7 / 49.3 |
| platform | PrizePicks | 1,654 | 887-767 | 55.7 | 53.6 [50.7, 56.6] | +2.1 | -0.41 | 41.7 / 49.8 |
| stat | kills | 2,269 | 1169-1100 | 55.5 | 51.5 [49.1, 54.0] | +4.0 | -0.33 | 46.9 / 50.1 |
| stat | HS | 2,107 | 1118-989 | 55.7 | 53.1 [50.5, 55.6] | +2.6 | -0.07 | 40.0 / 48.9 |
| scope | Maps 1-2 | 4,059 | 2147-1912 | 55.6 | 52.9 [50.7, 55.0] | +2.7 | -0.24 | 43.2 / 49.8 |
| scope | Map 1 | 277 | 123-154 | 55.2 | 44.4 [37.0, 51.8] | +10.8 | +0.50 | 51.3 / 43.3 |
| scope | Maps 1-3 | 40 | 17-23 | 56.4 | 42.5 (2 matches) | +13.9 | -2.03 | 30.0 / 62.5 |
| stat x scope | kills Maps 1-2 | 2,099 | 1093-1006 | 55.6 | 52.1 [49.5, 54.5] | +3.5 | -0.39 | 46.4 / 50.6 |
| stat x scope | HS Maps 1-2 | 1,960 | 1054-906 | 55.7 | 53.8 [51.1, 56.3] | +1.9 | -0.07 | 39.7 / 48.9 |
| stat x scope | kills Map 1 | 150 | 69-81 | 55.2 | 46.0 [38.0, 54.1] | +9.2 | +0.81 | 54.7 / 39.3 |
| stat x scope | HS Map 1 | 127 | 54-73 | 55.2 | 42.5 [34.1, 52.3] | +12.7 | +0.13 | 47.2 / 48.0 |
| letter | LEAN (0U) | 2,712 | 1422-1290 | 53.4 | 52.4 [50.3, 54.9] | +1.0 | -0.36 | 41.9 / 50.0 |
| letter | B- | 580 | 309-271 | 56.0 | 53.3 [48.8, 58.0] | +2.7 | -0.37 | 42.4 / 50.9 |
| letter | B | 844 | 434-410 | 59.4 | 51.4 [47.2, 56.1] | +8.0 | +0.03 | 43.4 / 46.7 |
| letter | B+ | 178 | 87-91 | 65.0 | 48.9 [39.4, 58.6] | +16.1 | +0.68 | 60.7 / 50.0 |
| letter | A- | 57 | 33-24 | 70.0 | 57.9 [40.7, 73.0] | +12.1 | +1.69 | 78.9 / 50.9 |
| letter | A | 5 | 2-3 | 76.2 | 40.0 | +36.2 | +7.49 | 100 / 40 |
| side | Under | 2,469 | 1295-1174 | 55.4 | 52.5 [49.0, 55.9] | +3.0 | -0.68 | - |
| side | Over | 1,907 | 992-915 | 55.8 | 52.0 [48.3, 55.9] | +3.8 | +0.41 | - |
| stat x side | kills Under | 1,205 | 619-586 | 55.1 | 51.4 [47.0, 55.2] | +3.7 | -1.00 | - |
| stat x side | kills Over | 1,064 | 550-514 | 56.0 | 51.7 [47.7, 55.6] | +4.4 | +0.43 | - |
| stat x side | HS Under | 1,264 | 676-588 | 55.8 | 53.5 [49.8, 57.0] | +2.3 | -0.37 | - |
| stat x side | HS Over | 843 | 442-401 | 55.5 | 52.4 [47.9, 56.5] | +3.1 | +0.38 | - |
| source | perf-2 (HLTV N0) | 2,159 | 1126-1033 | 55.8 | 52.2 [49.4, 55.6] | +3.6 | -0.08 | 44.4 / 48.0 |
| source | perf-cm2 | 2,009 | 1081-928 | 55.2 | 53.8 [50.4, 56.6] | +1.4 | -0.49 | 41.5 / 51.3 |
| source | perf-2-bo3 (BO3.gg) | 148 | 50-98 | 58.8 | 33.8 [24.2, 44.8] | +25.0 | +1.41 | 54.7 / 43.9 |
| source | legacy | 60 | 30-30 | 56.4 | 50.0 [37.7, 63.0] | +6.4 | +0.43 | 56.7 / 56.7 |
| line type | standard (P1 is standard only) | 4,376 | | | | | | PICKS board: demon 132 rows 44.7%, goblin 17 rows 47.1% |
| format | BO3 | 3,616 | 1943-1673 | 55.5 | 53.7 [51.6, 56.1] | +1.8 | -0.34 | 41.8 / 50.1 |
| format | BO1 | 261 | 116-145 | 55.2 | 44.4 [36.8, 52.5] | +10.8 | +0.64 | 54.0 / 42.1 |
| format | BO5 | 20 | 7-13 | 55.6 | 35.0 (1 match) | +20.6 | -4.10 | 25.0 / 70.0 |
| tier | top-30 team in the match | 1,721 | 912-809 | 55.3 | 53.0 [49.5, 56.4] | +2.3 | -0.35 | 39.2 / 49.8 |
| tier | rank 31-100 | 1,946 | 1045-901 | 55.8 | 53.7 [50.6, 56.5] | +2.1 | -0.20 | 45.5 / 49.4 |
| tier | rank 100+ | 538 | 256-282 | 56.0 | 47.6 [42.2, 53.4] | +8.4 | +0.16 | 49.8 / 49.1 |
| tier | non-HLTV match | 171 | 74-97 | 56.4 | 43.3 [34.2, 52.5] | +13.1 | +0.02 | 46.2 / 49.1 |
| strength | favourite 65%+ | 1,024 | 548-476 | 55.4 | 53.5 [50.4, 56.7] | +1.9 | -0.56 | 36.4 / 49.7 |
| strength | slight fav 50-65% | 1,453 | 743-710 | 55.5 | 51.1 [47.7, 54.8] | +4.4 | -0.58 | 30.8 / 48.2 |
| strength | slight dog 35-50% | 1,081 | 571-510 | 54.7 | 52.8 [48.8, 56.6] | +1.9 | +0.02 | 41.2 / 49.9 |
| strength | underdog <35% | 818 | 425-393 | 57.3 | 52.0 [46.4, 57.0] | +5.4 | +0.62 | 78.4 / 51.1 |
| env | LAN | 2,498 | 1287-1211 | 55.4 | 51.5 [48.7, 54.1] | +3.9 | -0.27 | 41.8 / 50.0 |
| env | online | 1,355 | 742-613 | 55.7 | 54.8 [51.7, 57.9] | +0.9 | -0.13 | 46.6 / 48.6 |
| overtime | no OT map | 2,860 | 1532-1328 | 55.6 | 53.6 [51.2, 55.9] | +2.1 | +1.45 | 44.3 / 39.8 |
| overtime | OT map in the series | 1,362 | 675-687 | 55.5 | 49.6 [45.5, 53.9] | +5.9 | -3.93 | 40.2 / 71.0 |
| odds source | Thunderpick | 2,831 | 1489-1342 | 55.3 | 52.6 [50.1, 55.0] | +2.7 | -0.34 | 41.7 / 50.1 |
| odds source | Polymarket | 484 | 275-209 | 55.8 | 56.8 [51.8, 62.1] | -1.0 | -0.24 | 40.9 / 49.4 |
| odds source | none | 1,061 | 523-538 | 56.4 | 49.3 [44.8, 53.9] | +7.1 | +0.16 | 49.9 / 48.0 |
| version | 794a775f51 (Oct 20-21) | 757 | 405-352 | 56.0 | 53.5 [48.2, 58.5] | +2.5 | +0.01 | 48.0 / 48.2 |
| version | aa6696d5d6 (Oct 23-25) | 743 | 376-367 | 55.1 | 50.6 [45.3, 55.1] | +4.5 | -0.65 | 41.7 / 52.4 |
| version | 2085aa791c (Oct 26-28, live) | 688 | 399-289 | 54.8 | 58.0 [53.7, 61.8] | -3.1 | +0.18 | 37.9 / 47.7 |
| version | 9cfcbd217a (Oct 16-17) | 609 | 331-278 | 55.5 | 54.4 [48.5, 60.3] | +1.2 | -0.38 | 43.2 / 49.1 |
| version | c36104d079 (Oct 18-19) | 287 | 140-147 | 54.6 | 48.8 [42.5, 54.6] | +5.8 | +0.51 | 46.3 / 44.6 |
| version | f663af8917 (Oct 21-22) | 233 | 120-113 | 55.3 | 51.5 [40.2, 63.5] | +3.8 | -2.77 | 31.8 / 63.9 |

Upset days (Over share far from Over hit, P1): Oct 22 (34.7% Over share / 66.9% Over hit, bias -3.7, hit 49.9%), Oct 23
(29.5 / 55.2, 46.2%), Oct 18 (49.0 / 58.4). The Under lean is correlated with overtime series (bias -3.9 there).

## 3. Grading logic audit (projection -> chance -> grade -> stake; grader.grade_leg, pick_verdict, grade_display)

Rules read from the code, each checked against the first-grade data (PICKS, board lines; outputs/hs_rules.json):

| rule (where) | what it does | data | verdict |
|---|---|---|---|
| chance = discretised normal(lambda, sd) on the line cut, push conditioned out (p_three / pmore, fix_v2) | | live replay of raw -> capped chance reproduces the logged chance, mean gap 0.0002 | consistent |
| kills cap 78% (p_cap_kills) | no kills side above 78% | reached on 3 board rows only | moot on board lines |
| grade bands 55/57/63/68/74/80 (grade_from_p); units 0.5/1/1.5/2/3 (units_for_p) | | 0.5U 52.6% [49.1, 55.7] (3,851), 1.0U 51.8% [46.4, 57.5] (447), 1.5U 55.0% (130): stake size does not track hit rate | the bands are not calibrated above 57% (section 1); stake follows the band, so bigger stakes are not better picks |
| thin data <20 maps -> 1.5U cap; <5 maps -> 0U | | 389 rows 47.5% [36.2, 56.9] vs 58.2% stated | cap justified by the data (cohort is worse than the board) |
| rust >= 29 d -> 1U cap; 15-28 d flag only | | >28 d: 141 rows 46.8% vs 58.9%; 15-28 d: 575 rows 44.9% [37.2, 53.8] vs 55.8% | the capped cohort is justified; the UNCAPPED 15-28 d cohort is as bad (Over share 30.9% vs Over hit 53.0%: it leans Under on rusty players who then score) - candidate for a stake rule, not changed here |
| stand-in -> 1U cap (is_standin -> 0U) | | 131 rows 55.4% vs 56.3% stated | cohort is calibrated; the cap costs nothing measurable, the 0U gate (26 rows) is unverifiable |
| SOFT BOOK / NO MARKET -> 1.5U cap | | SOFT 8,269 rows 52.5% (= board); NO MARKET 794 rows 46.6% [41.6, 51.5] vs 56.4% | NO MARKET cohort is clearly worse (also P1 odds source none 49.3%): cap justified, possibly too light |
| TAPE FIGHT -> 0.5U cap | | 737 rows 57.3% [51.0, 62.9] vs 60.1% | the best cohort on the board; the cap removes stake from above-average picks |
| FORM FIGHT -> 0U | | 178 rows 62.1% [51.4, 73.7] vs 60.7% | the 0U gate zeroes the best-hitting cohort on the board (model side kept, record disagreed) |
| AWPer on an HS prop -> 0U | | 753 rows 56.0% [52.2, 60.3] vs 56.2% stated | calibrated; the gate is not supported by outcomes (owner rule since v7.2; 623 of the 1,535 "graded chance but 0U" picks) |
| PROVISIONAL BO3.gg route (perf-2-bo3): flag only, full stake | | 259 rows 37.4% [28.0, 46.4] vs 58.6%; staked 100 rows 31.0% | **the one cohort with no cap is the worst on the board** - see sd-bo3-1 (fails as a spread fix); a stake cap is the obvious rule, left to the owner |
| legacy-only steps (rested +4%, star -, hot form -) | not applied under the performance model | rested 53.6%, star 54.9%, hot form 49.5% (these flags appear on legacy-era rows only) | no change |
| gd-1 display: letter from the unrounded chance; 0U keeps the letter + reason | | 0 letter/band mismatches on 16,762 picks; 1,535 picks with chance >= 55% logged LEAN = 0U gates (AWPer 642, FORM FIGHT 254, sample 26, the rest = side not offered / 3-decimal rounding at 0.5495-0.5505) | display matches the logged grade |
| Map 1 = half of Maps 1-2; Maps 1-3 = 1.5 x | | HLTV series since 2025-10-01: Map 1 = 49.8% of Maps 1-2 kills (online) / 49.7% (LAN) | no Map 1 bias in the projection; the Map 1 miss is spread / selection (rm-1's cell) |

No code defect was found in the projection -> chance -> grade -> stake path (the audit.py run on the live board, section 6,
also found 0 grade/chance and 0 units mismatches). The findings are rule-evidence gaps, not bugs: four stake rules
(FORM FIGHT 0U, AWPer HS 0U, TAPE FIGHT 0.5U, no cap on the BO3.gg route) point the wrong way on the data, and the
B+/A- bands are not reached by calibrated chances on board lines.

## 4. Registered candidates (each against its own rule; all intervals match-grouped)

| candidate | rule | evidence now | verdict |
|---|---|---|---|
| cm-1 complete model (shadow since Oct 16 22:35Z, clock never restarted) | REVIEW_RULE at 300 matches | 170 forward matches / 2,590 units: log-loss gain +0.0030 [-0.0014, +0.0079]; kills +0.0040, HS +0.0018; replay gap 0.024 (> 0.01 tolerance) | interim, window not reached; no decision |
| cm-2 kills Maps 1-2 (live since Oct 20 by owner override; shadow clock Oct 18 21:55Z) | REVIEW_RULE at 300 | 150 matches / 1,222 units: +0.0030 [-0.0022, +0.0084]; replay gap 0.033 | interim, no decision; the live perf-cm2 cohort hits 53.8% vs perf-2 52.2% (P1, overlapping intervals) |
| hs-cm2 (HS Maps 1-2 on cm-2 kills, live since Oct 21) | forward vs the saved perf-2 arm | 136 P1 units / 44 matches with a perf-2 arm: +0.0020 [-0.0181, +0.0233]; staked 44.6% (65) vs perf-2 arm 50.7% (71) | inconclusive (too few units with a replay arm); stays live by its passed registered rule |
| hs-1 HS calibration shadow | REVIEW_RULE | 169 matches: +0.0017 [-0.0036, +0.0062] | interim |
| lc-1 line correction shadow | REVIEW_RULE | 178 matches: +0.0038 [-0.0038, +0.0129]; HS Maps 1-2 slice -0.0030 | interim |
| fh-1 full history | REVIEW_RULE | 198 matches / 3,057 units: +0.0014 [-0.0014, +0.0042] | interim; no improvement claim |
| pl-2 corrected-anchor prop line | REVIEW_RULE (300) | 210 matches / 2,773 units: +0.0047 [-0.0020, +0.0110]; kills Maps 1-2 +0.0033, HS Maps 1-2 +0.0002, Map 1 +0.03 (18 matches) | interim; 90 matches short of its first window |
| lb-1 line blend | REVIEW_RULE | 264 matches: -0.0004 [-0.0051, +0.0049] | interim; heading for a futility retirement at 300 |
| ch-9 perf refit | REVIEW_RULE | 102 matches: -0.0004 [-0.0011, +0.0005] | interim, no gain |
| rm-1 / th-1 (AM-1) | forward P1 units in their cells (Map 1 / Maps 1-3) | 0 eligible forward units: every P1 unit with a saved `cands` arm (1,422) is Maps 1-2 ("not a rm-1 cell") | not testable yet (as on Oct 25) |
| vm-1 map props | research/map_veto/props REGISTER section 4 | development result: no map gain beyond rm-1's rounds mixture; no forward P1 record of vm-1 chances is exported by the admin pages used here | not evaluated further; stays shadow |
| LW-1 (parked Oct 25) | forward reading with its final frozen weights, starts after its data cut (Oct 25 19:38Z) | 688 units / 37 matches: log-loss **-0.0066 [-0.0158, +0.0041]**, Brier -0.0033; it would have staked 13 picks (69.2%) against live's 286 (60.8% [53.1, 68.4]) | forward reading negative; the parking decision stands |
| **oc-1** offered-line calibration per cell (NEW, REGISTER) | pass = gain CI > 0, Brier > 0, retention >= 60%, staked hit >= live, balance, slices | 4,376 units / 18 days walk-forward: gain **+0.0014 [-0.0025, +0.0057]** (rule 1 FAIL), Brier +0.0006 [-0.0013, +0.0027] (FAIL), retention **43%** (888 vs 2,061 staked; FAIL), staked hit 51.5% vs live 52.8% (FAIL), balance 12.5 pts (pass), worst day Oct 18 balance 50 pts on 22 picks (FAIL); last fit kills Maps 1-2 w 0.17, HS w 0.49 | **FAIL - documented shadow, nothing shipped** |
| **sd-bo3-1** spread multiplier on the BO3.gg route (NEW, REGISTER) | pass = cohort gain CI > 0 with >= 30 matches, Brier > 0, balance, staked hit >= live | 148 units / 32 matches: gain +0.0049 [-0.0059, +0.0171] (FAIL), Brier +0.0025 [-0.0027, +0.0084] (FAIL); m chosen 2.5 from Oct 21 on; staked 30.4% vs 31.0%; balance 29 pts (FAIL) | **FAIL - documented shadow; a wider spread does not repair an anti-signal cohort** |

Nothing passed its registered rule, so no model, chance, grade or stake rule was changed on the branch.

## 5. What this says about "levelling up"

- Calibration: board chances above 57% are 7-16 pts too high on both rules; every registered probability correction
  (lc-1, lb-1, oc-1, LW-1) either shows no significant gain or buys its gain by removing most staked picks. On this
  data the information is in the line: live's log loss (0.6987) sits above the 50% baseline's 0.6931.
- Selection: the current live tag `2085aa791c` (Oct 26-28) is the first that hits above its stated chance (58.0% on 688
  units, 37 matches) - three days, interval [53.7, 61.8]; not evidence of a step change until it survives a week.
- Projection: the two cohorts with a real, repeatable miss are the BO3.gg route (anti-signal, 32 matches) and Map 1 /
  BO1 (19 matches). The first needs a stake rule (owner), the second is rm-1's registered cell, still without forward
  units. Overtime is the dominant bias term (-3.9 kills in OT series) and is rm-1's rounds-mixture question too.
- Stake rules: the FORM FIGHT and AWPer-HS 0U gates and the TAPE FIGHT cap remove or shrink the board's BEST cohorts;
  the uncapped PROVISIONAL route is its worst. These are owner rules; changing them needs a registered stake-rule test
  (not done here - nothing was registered for stake rules before this data was seen).

## 6. audit.py on the live board (2026-10-28 ~16:50 UTC, scratchpad/audit.py, read-only GET)

version 2085aa791c; 40 board rows, 40 full cards; 0 "p out of range", 0 "bad projection", 0 "grade != chance",
0 "units != grade with no cap reason", 0 "graded but 0U"; 9 bets (7 More / 2 Less), 31 leans, 16 coin flips (<52%);
last-10 depth 10/10 on every row; AWP source HLTV weapon stats on 40/40; 10 rows "graded vs different opponent than
Underdog" = the same team under two spellings (MIBR / Made in Brazil, name check only); 10 rows LAN/online unknown
(one match with no event). No stake or cap was rescaled (no grader change).

### 6b. sr-bo3-1 (coordinator directive after this report): second audit.py run, 2026-10-28 ~17:45 UTC

version 2085aa791c (gate not yet live); 20 board rows / 20 graded; 0 grade/chance, 0 units/grade, 0 graded-but-0U
mismatches; 2 bets (both Less), 18 leans; the same MIBR name note. **Rows on the PROVISIONAL BO3.gg route at that moment:
0 of 20** (no row's source, flags or reason names perf-2-bo3 / PROVISIONAL), so the gate would hold 0 live rows today.
Historically it would have held 259 first-grade board rows (100 of them staked, 31.0% hit) and 148 P1 units over Oct 11-28.

### 6c. sr-bo3-2 (owner directive: BO3.gg-route projections shadow only) - third audit.py run, 2026-10-28 ~18:40 UTC

Live board version 3007c59b74 (main after the grade/cs2-1028 merge; sr-bo3-2 not yet deployed): 100 rows / 100 graded,
46 bets (31 More / 15 Less), 54 leans, 21 coin flips; 0 "grade != chance", 0 "graded but 0U"; 2 "units != grade with no cap
reason" are the pre-existing same-match-cap display rows (vinaabEAST / puni, Turma do Pagode) that audit.py's cap-word
regex does not recognise - unrelated to this change; 10 MIBR-name notes, 4 LAN/online unknown, 2 last-10 under 10.
**Rows on the PROVISIONAL BO3.gg route today: 0 of 88 member-visible rows** - sr-bo3-2 would hide nothing on the board
at this moment.
**Oct 11-28 (first grades):** 368 provisional-route forecasts, of which **299 platform board lines (240 Underdog, 59
PrizePicks; 276 distinct player x stat x line x scope x match; 38 matches; 257 settled / 40 void / 2 push; 174 were
staked)** would have been hidden from members, plus 69 member-typed card lookups that would have shown "no projection".
Last provisional board line logged 2026-10-24.
