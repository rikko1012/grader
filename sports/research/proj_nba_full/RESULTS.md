# pnf-1 NBA (research only, nothing live imports it)

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
