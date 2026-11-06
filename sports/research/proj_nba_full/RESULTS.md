# pnf-1 NBA (research only, nothing live imports it)

## Rerun 2026-11-06 with the FRESH service export (owner-built 05:21Z) - still FAILS at the lines

Input: `cs2_scout_ops\data\exports_2026-11-06\gradecore_export_nba.json`, built by the owner in the Render Shell
(read-only export, 10,210 rows, scanned 24,372; every row `sm-1:5ec8adf73a6dad18`; the earlier 8,657 rows are all
inside it). Work copy: `cs2_scout_ops\data
ba_fullerun_20261106\` (sha256 in its `MANIFEST.json`); nothing in
production was written.

- History: a copy of `history_ext.db` refreshed with the repo ESPN crawler (read-only GETs). The only newly settled
  export game without a box score was 401909936 (2026-11-06 02:00Z); it is now final with 25 player rows. The day
  re-crawl blanks the summary spread/total of the 105 games crawled before, so those were restored from the original
  copy (all 105 equal again; no other table changed: player/team/absence rows of earlier games identical).
- Box score vs service settlement: 10,210 / 10,210 agree (5,423 win, 4,685 loss, 102 push).
- Population: the fresh export only (service-settled; no results-ledger or offline-scored rows; `--export-only 1`).
  With the ledger and offline comparison added the scored set is identical (10,102 rows), so the export now covers them.
- Walk-forward final window rerun to 2026-11-07 (`PNF_FINAL_END`), 6,488 rows / 100 games: frozen sm-1 vs pnf-1 log
  score +0.0623 [+0.0450, +0.0790] (was +0.0628 [+0.0445, +0.0794] on 6,424); refit sm-1 vs pnf-1 +0.0618
  [+0.0449, +0.0785]. History check passes.

### At the offered lines (pnf minus live; gaps = stated minus hit, pts; whole-game bootstrap CIs)

| | before: old export + ledger + offline (9,872 / 89 games) | after: fresh export (10,102 / 90 games) |
|---|---|---|
| live gap all | +5.9 [+4.3, +7.6] | +5.9 [+4.3, +7.4] |
| live gap Over / Under | +4.6 [+1.5, +7.6] / +6.6 [+3.8, +9.1] | +4.3 [+1.7, +6.7] / +6.7 [+4.1, +9.2] |
| pnf gap all | +6.0 [+4.2, +7.6] | +5.9 [+4.0, +7.7] |
| pnf gap Over / Under | +6.7 [+3.4, +9.5] / +5.5 [+2.8, +8.1] | +6.3 [+3.6, +8.9] / +5.6 [+2.7, +8.2] |
| log loss live / pnf | 0.70549 / 0.70008 | 0.70555 / 0.69984 |
| dLL pnf vs live | -0.0054 [-0.0134, +0.0023] | -0.0057 [-0.0134, +0.0028] |
| graded live n / hit | 6,447 / 55.3% | 6,613 / 55.2% |
| graded pnf n / hit | 7,237 / 54.2% | 7,419 / 54.3% |
| graded count ratio | 1.12 | 1.12 |
| graded hit diff | -1.1 [-3.1, +0.8] | -0.9 [-2.9, +1.0] |
| Over share live / pnf | 34.2% / 42.0% | 34.2% / 42.2% |
| **pass** | **FAIL** | **FAIL** (calibration all/Over/Under, graded hit) |

By market, after (before in brackets):

| market | rows | pnf gap all [CI] | pnf Over / Under | live all / Over / Under | dLL [CI] | graded hit diff [CI] | count ratio | Over share live / pnf |
|---|---|---|---|---|---|---|---|---|
| points | 3,941 (3,849) | +8.2 [+5.2, +10.9] (+8.2) | +9.8 / +6.6 | +7.9 / +7.4 / +8.2 | -0.0080 [-0.0215, +0.0050] (-0.0070) | -0.8 [-4.3, +2.5] (-1.2) | 1.24 | 33.5% / 48.0% |
| rebounds | 2,745 (2,677) | +4.6 [+1.9, +7.3] (+4.6) | +0.7 / +6.7 | +3.4 / -1.8 / +6.0 | -0.0001 [-0.0110, +0.0124] (-0.0008) | -2.4 [-4.8, +0.0] (-2.3) | 1.08 | 33.6% / 33.8% |
| assists | 1,754 (1,716) | +4.9 [+2.0, +7.9] (+5.2) | +6.0 / +3.9 | +5.5 / +5.0 / +5.8 | -0.0051 [-0.0217, +0.0121] (-0.0042) | +0.2 [-4.2, +4.5] (-0.3) | 1.01 | 38.5% / 45.7% |
| threes | 1,662 (1,630) | +3.6 [+1.1, +6.2] (+3.9) | +4.4 / +3.1 | +5.8 / +6.7 / +5.4 | -0.0104 [-0.0188, -0.0026] (-0.0104) | +1.0 [-1.3, +3.2] (+1.1) | 1.07 | 32.2% / 38.3% |

No market passes: every pnf calibration gap is above 2.5 pts except rebounds Over. The Nov 5 UTC games alone (1,433 rows /
14 games) again favour pnf (dLL -0.027 [-0.038, -0.015], gap +3.0 vs live +7.1) but that is one slate and pnf Over
is +5.4 there.

### Reproduce

```
python <rerun>/scripts/build_nba_full.py            # fresh export -> <rerun>/nba_full.db
python -m sports.research.proj_nba_full.data <rerun>/nba_full.db <frame.pkl>
PNF_FINAL_END=2026-11-07T00:00:00Z python -m sports.research.proj_nba_full.walkforward run <rerun>/history/history_ext.db <frame.pkl> <preds> final
PNF_FRAME=<frame.pkl> python -m sports.research.proj_nba_full.walkforward report <preds> <wf.json>
python -m sports.research.proj_nba_full.bookline <preds> <out.json> <rerun>/nba_full.db   --export <exports_2026-11-06>/gradecore_export_nba.json --live-tag sm-1:5ec8adf73a6dad18 --export-only 1
```

Results: `sports/research/results/pnf1_nba_bookline_fresh_export_2026-11-06.json`,
`sports/research/results/pnf1_nba_walkforward_fresh_export_2026-11-06.json`


## Rerun 2026-11-06 with the owner-approved ledger download

The download is in `cs2_scout_ops\data\nba_full\downloads\sports_ledger_20261105\`, with sha256 values in its `MANIFEST.json`. It was fetched GET-only and read with `python -I`.

What the download adds:

- **Gradecore export:** byte-identical to the copy already in `nba_full.db` (built 2026-11-03), so it adds nothing new.
- **Results report:** the same 2,000 rows as `reports_20261105/nba-results.json`, so it adds nothing new.
- **Gradecore comparison:** this is the new data. It holds 29,933 saved sm-1 raw forecasts with no outcomes. For the 14 games of Nov 4 US (Nov 5 UTC), each offer keeps its last forecast at least 30 min before tip, and that forecast is scored **offline** against the `nba_full.db` box score. A player who did not play counts as void.
  - 1,377 offers, 59 void (did not play).
  - 797 are new rows on 9 games. The remaining offers are already settled by the service in the results report, and the service settlement wins.
  - Scoring checks: the comparison chances equal the service's settled chances on all 987 shared forecasts (max difference 0). Box-score actuals equal the service actuals on all 1,016 settled sm-1 rows.

Current live tag only: `sm-1:5ec8adf73a6dad18`.

- All 8,657 export rows carry this tag.
- The service's 2026-11-06 03:17Z forecast run reports the NBA artifact `5ec8adf73a6dad18`. tw-1 is off, and refits are challenger-only.
- The results and comparison rows are sm-1 raw forecasts (gradecore adapter rule). No row comes from another tag.
- The grade policy (gp-2 to gp-3 at 2026-11-05 10:06Z) does not enter this test. "Graded" here is the research rule: C band 53.45% plus the projection on that side.

The all-outcome walk-forward is unchanged, because `nba_full.db` already held the Nov 5 box scores. The final window was rerun from scratch and reproduced the committed numbers exactly: frozen sm-1 vs pnf-1 +0.0628 [0.0445, 0.0794] on 6,424 rows. Dev is +0.0353 [0.0321, 0.0392].

### At the offered lines

dLL = pnf minus live log loss, so negative means pnf is better. CIs are whole-game bootstrap.

| | before (service-settled, 9,075 / 82 games) | after (+ offline-scored Nov 5 UTC, 9,872 / 89 games) |
|---|---|---|
| live gap all / Over / Under | +5.6 / +4.0 / +6.4 | +5.9 / +4.6 / +6.6 |
| pnf gap all / Over / Under | +6.2 / +6.7 / +5.8 | +6.0 / +6.7 / +5.5 |
| pnf gap CI (all) | [+4.5, +8.1] | [+4.2, +7.6] |
| dLL pnf vs live | -0.0026 [-0.0106, +0.0059] | -0.0054 [-0.0134, +0.0023] |
| graded live: n / hit | 5,943 / 55.8% | 6,447 / 55.3% |
| graded pnf: n / hit | 6,692 / 53.9% | 7,237 / 54.2% |
| graded hit diff | -1.9 [-3.7, -0.1] | -1.1 [-3.1, +0.8] |
| count ratio | 1.13 | 1.12 |
| **pass** | **FAIL** (all 3 calibration checks, hit) | **FAIL** (all 3 calibration checks, hit) |

The Nov 5 UTC games alone (1,313 rows / 14 games) are too few to decide anything:

- pnf: gap +3.5, dLL -0.025 [-0.038, -0.013], graded hit +6.8.
- live: gap +7.2.

The pass rule still fails on Over calibration (+6.6).

### Reproduce

```
python -m sports.research.proj_nba_full.data <nba_full.db> <frame.pkl>
python -m sports.research.proj_nba_full.walkforward run <history_ext.db> <frame.pkl> <preds> final
PNF_FRAME=<frame.pkl> python -m sports.research.proj_nba_full.walkforward report <preds> <wf.json>
python -m sports.research.proj_nba_full.bookline <preds> <out.json> \
  --comparison <dl>/gradecore_comparison_nba.json --export <dl>/gradecore_export_nba.json --live-tag sm-1:5ec8adf73a6dad18
```

Result: `sports/research/results/pnf1_nba_bookline_rerun_2026-11-06.json`

### Still missing

The service's own settlement for the other 7 Nov 4 games, and for everything after 2026-11-03 06:30Z, needs `python -m sports.model.gradecore_export` run in the scout-sports-api Render Shell. That is a production write and needs the owner's OK.
