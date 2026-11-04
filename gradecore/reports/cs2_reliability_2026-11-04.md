# CS2 held-out reliability report, 2026-11-04 (gp-2 shadow; evidence only, release_allowed: false)

Produced by `python -m gradecore cs2-local` + `python -m gradecore reliability` (gradecore/__main__.py) on the ONLY CS2
pick log reachable offline from this workstation. Nothing was fabricated: every number below is what the read-only
run returned. Member grades, chances and stakes are untouched.

## Source and why it cannot answer the question yet

| item | value |
|---|---|
| copy | `C:\Users\tikic\cs2_scout\picks.db` (local copy saved 2026-10-11 14:02; the OneDrive clone copy is older still) |
| opened | read-only (`?mode=ro`, `PRAGMA query_only`); file bytes unchanged |
| forecasts in copy | 909 (2026-10-08 23:45 UTC .. 2026-10-11 19:02 UTC), 7 frozen model tags |
| picks in copy | 531; real settled picks (test 0 or 2, status settled / push / void) 78 |
| columns missing | `probs_raw`, `quote` (raw distributions were first logged 2026-10-15; the saved board quote 2026-10-15) |
| `book` column | present but NULL on the real settled picks of this copy (book identity came later) |

Because the copy predates raw-distribution logging and has no replayable book identity, `history.cs2` selects no row for
any tag, so there is no fit / calibration / held-out partition and no reliability bin to judge against the 3-point rule.

## Selection per frozen model tag (history.cs2 exclusion reasons)

| model tag | forecasts | selected | not_real_settled_pick | identity_not_replayable | invalid_time_or_inside_30_minutes |
|---|---|---|---|---|---|
| ddc671ab73 | 226 | 0 | 170 | 56 | 0 |
| 7acc67616d | 186 | 0 | 186 | 0 | 0 |
| a64083e3c2 | 153 | 0 | 152 | 1 | 0 |
| 08d58cd05c | 150 | 0 | 123 | 22 | 5 |
| 21459594ca | 130 | 0 | 130 | 0 | 0 |
| f7c0888caf | 56 | 0 | 1 | 55 | 0 |
| 7f572a552c | 8 | 0 | 0 | 8 | 0 |

(`other_model` = the forecasts of the other six tags each time; the selection runs one frozen tag at a time.)

## Reliability run (tag ddc671ab73, fit end 2026-10-20, calibration end 2026-10-28, 400 bootstrap reps)

```
rows_in 0; counts fit 0, calibration 0, test 0, purged 0; held-out decided 0; bins 0
bins_within_max_gap: false
verdict: not evaluable: no settled rows for this sport in the input
release_allowed: false
```

## What produces the real held-out numbers

The settled CS2 forecasts with raw distributions (2026-10-15 onward) live on the Render disk of `cs2-scout`. In that
service's Shell, read-only, no production write:

```
python -m gradecore cs2-local --db /data/picks.db --list
python -m gradecore cs2-local --db /data/picks.db --model-version <frozen live tag> --out $SCOUT_GRADECORE_ARTIFACTS
python -m gradecore reliability --rows $SCOUT_GRADECORE_ARTIFACTS/cs2_rows_<tag>_<stamp>.jsonl --sport cs2 \
    --fit-end <date> --calibration-end <date> --out $SCOUT_GRADECORE_ARTIFACTS
```

The report passes only when every held-out bin is within 3 points AND at least 500 decided held-out forecasts exist
(the same rule `gradecore/release.py` enforces); even then it is evidence for the owner's review, never permission to
publish grades. No fitted calibration artifact exists as of this report.
