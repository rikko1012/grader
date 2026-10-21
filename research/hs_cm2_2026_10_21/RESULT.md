# hs-cm2 result (protocol PROTOCOL.md @ e7e5e12)

Run 2026-10-21 19:04 UTC. HLTV database pulled 18:57:24 UTC (read-only). Candidate, hold-out, metrics and decision rule are
exactly as registered. Nothing was fitted. Code: `data.py` (pull), `validate.py` (pipeline checks, no metrics), `study.py`
(the registered test). Outputs: `result.json`, `units.json` (every scored row), `validate_output.txt`, `study_output.txt`.

## Decision: PASS under the registered rule (see caveat 1 before acting)

| | rule | result |
|---|---|---|
| (1) HLTV-wide MAE gain, 95% CI above 0 | required | **+0.063 HS [+0.020, +0.105]** - met |
| (2) P1 log-loss gain point estimate >= 0 | required | **+0.0049 [-0.0013, +0.0112]** - met |

Under the protocol, hs-cm2 goes live for HS Maps 1-2 (as perf-cm2 for HS, old forecasts kept, rollback flag). This study
does not deploy anything.

## Hold-out size
- Matches from 2026-10-17 00:00 UTC up to the HLTV pull (2026-10-21 18:57 UTC).
- (1) HLTV-wide: **1,866 player-series, 187 matches** (Maps 1-2 played, both maps' HS recorded). Excluded: 140 player-rows
  from series with no map 2 in HLTV (BO1s), 3 not on both maps, 2 with 0 HS on 12+ kills (the live "not recorded" flag).
  cm-2 eligible on 1,811; 55 had no cm-2 player maps in the window and keep perf-2 (as registered).
- (2) P1 lines: **580 lines, 70 matches** (241 PrizePicks, 339 Underdog). **10 pushes excluded.** All 580 published as
  perf-2. cm-2 eligible on 553; 27 keep the published chance (own team or opponent not identified in HLTV).

## Metric 1 - HLTV-wide HS projection error (match-grouped bootstrap, 2,000 resamples)
| arm | MAE | bias (proj - actual) |
|---|---|---|
| perf-2 on N0, rebuilt for the same day | 3.962 | +0.057 |
| hs-cm2 | 3.899 | +0.130 |
| gain (+ = candidate better) | **+0.063 [+0.020, +0.105]** | |

Cut by eligibility: cm-2-eligible series only +0.065 [+0.021, +0.109].

## Metric 2 - P1 offered lines, log-loss of P(Over | no push)
| | value |
|---|---|
| log-loss published | 0.6861 |
| log-loss hs-cm2 | 0.6812 |
| gain | **+0.0049 [-0.0013, +0.0112]** |
| Brier gain | +0.0024 [-0.0006, +0.0055] |
| mean P(Over): published / candidate / observed | 0.491 / 0.493 / 0.464 |
| by book: PrizePicks / Underdog | +0.0053 [-0.0048, +0.0165] / +0.0046 [-0.0024, +0.0115] |
| cm-2-eligible lines only (553) | +0.0051 [-0.0013, +0.0119] |
| HS projection MAE on these lines: published / candidate | 3.825 / 3.790 |

## Calibration on the P1 lines (favoured side: printed vs won, match-grouped 95% CI)
| band | published: lines, printed, won [CI] | hs-cm2: lines, printed, won [CI] |
|---|---|---|
| 50-55% | 315, 52.3%, 51.4% [46.0, 57.0] | 323, 52.1%, 57.6% [51.7, 63.3] |
| 55-60% | 173, 57.1%, 57.2% [49.1, 65.3] | 183, 57.0%, 61.2% [53.8, 68.6] |
| 60-65% | 70, 61.9%, 58.6% [47.7, 69.6] | 53, 62.2%, 50.9% [38.0, 65.9] |
| 65%+ | 22, 69.2%, 77.3% [56.5, 95.0] | 21, 67.6%, 76.2% [50.0, 96.3] |

Both arms are within noise of calibrated in every band. hs-cm2 wins more than it prints at 50-60% and less at 60-65%.
Every interval is wide (about 70 matches).

## Caveats and data problems (read before acting on the PASS)
1. **Metric 1's gain comes mostly from players with no N0 history.** In 332 of the 1,866 series the player has no N0 map in
   the window. There, "perf-2 N0 rebuilt" (perf.project on no rows) is the pro-average prior. On the live board those
   players would mostly be on perf-2-bo3 (BO3.gg history), which cannot be rebuilt from the HLTV database. For the 1,534
   series **with** N0 history, the gain is **+0.015 [-0.021, +0.053]** (not significant). The protocol's metric is computed
   as written, but most of the HLTV-wide improvement is over the prior, not over a live perf-2 projection. Metric 2 is not
   affected: all 580 P1 lines were published perf-2 on the HLTV route.
2. **hs-cm2 projects higher.** Bias is +0.13 HS vs +0.06 HLTV-wide. On P1 lines Overs hit 46.4%, against a mean candidate
   P(Over) of 49.3%. This is the same direction as the owner's correlated-risk concern, reversed: it would add Overs.
3. **P1 was reconstructed, not read from production's P1 export.** The registered sources were the paged
   /admin/forecasts_page.json (inputs=1) and the HLTV database. Production's /admin/picks.json and
   /admin/learning/p1_units.json were not used, so:
   - board provenance and book come from the board quote on the forecast's pick (any of its forecasts);
   - the unit is (HLTV match, HLTV player);
   - the start is the start on the unit's most recently logged forecast;
   - settlement is the player's HLTV headshots on maps 1 and 2.

   Checked against production's own stored P1 export saved 2026-10-19 (starts up to Oct 19 18:00): 271 of production's 280
   HS Maps 1-2 units got the same forecast, and **settlement agreed 271/271** (outcome, actual and published chance). Of the
   9 misses, 4 have no HLTV player or match identity in the forecast inputs and 4 are rescheduled matches (production's unit
   start differs). This reconstruction added 5 units in that window that production's export does not list.
4. **Run 1 had a match-resolution bug, fixed before the reported run.** When a forecast had no `hltv:` match id, run 1 fell
   back to the player's own team id even when the opponent id was known but did not match, and allowed +-1 day. That settled
   about 50 lines on a different match of the same team. Now a known opponent id must match. The team-id fallback is used
   only when the forecast has no opponent id, and only on the same day; the 17 such lines were checked by team name and are
   all cm-2-ineligible. Run 1 gave P1 585 lines / 72 matches, gain +0.0054 [-0.0004, +0.0115]; metric 1 was identical. Both
   runs PASS; the bug only affected settlement plumbing.
5. **One opponent mapping is unclear.** One match (hltv:2399805, paiN) has production opponent "LP" (id 13304), but the
   HLTV match is paiN vs BARCA (14107), via production's own canonical match id. It covers 10 lines; leaving that match out
   gives a gain of +0.0052.
6. **The baseline rebuild reproduces live.** On the 580 P1 lines the perf-2 N0 rebuild matches the published kills lambda
   (median |diff| 0.0004; 100% within 0.25) and HS-per-kill (99.5% within 0.002). The cm-2 rebuild matches production's live
   perf-cm2 kills lambda on 3,000 forecasts (99.3% within 0.01; 64 ineligible here because of identity or time differences).
7. **Data as stored at the pull.** Tables were built once at the pull (cm-2 tables sha 5a0291a4d3c87bd4, 304,940 clean
   rows, 38 incomplete maps excluded, 20 unknown-environment rows). Each forecast reads only rows strictly before its match
   day, the latest refit on or before it, and Elo at the start of that day. A row that reached the database late for an
   earlier date is included, as in live's 2-hourly rebuild. N0 at the pull: pre 8,597, late_in 241.
8. Coverage is the HLTV database's (top-200 scrape). The 1,640 HS forecasts without a PrizePicks / Underdog board quote on
   their pick were treated as member or unquoted lines. 239 goblin / demon / promo forecasts and 136 picks with no HLTV
   identity or match were excluded.
