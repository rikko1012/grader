# Evaluation rules and reconciled counts (2026-10-12)

Live model `1088faf157` and challenger lb-001 are frozen. Nothing here changed a member grade.

## 1. Unit of observation and the primary forecast (learning.py)

- **Unit** = player x canonical match (identity.assign: a BO3.gg slug and `hltv-<id>` of the same match are one) x stat x
  map scope.
- **Primary (evaluation) forecast of a unit** - deterministic, uses only information available before the match:
  among the forecasts of the unit's pick rows logged at or before `match_start - 30 min` (match_start = the earliest
  scheduled start of the unit's rows), take the maximum of
  `(logged_at, line_type is standard, source rank board 2 > board_pp 1 > member 0, -forecast_id)` - i.e. the latest
  such forecast; ties -> the standard line, then the board copy, then the earliest-stored forecast. The result, the
  settlement and any later forecast are never inputs to this choice. No forecast 30+ min before the start -> bucket
  `incomplete` (never scored).
- **Every distinct line** of the unit (one per line value x line type: the latest forecast 30+ min before the start)
  is kept with its own outcome in `ledger.lines`; the all-lines view weights each 1/k (k = distinct lines of the unit).
  Primary scorecard, training rows and reviews use the primary forecast only. Uncertainty is grouped by canonical match.

## 2. Three evaluations, never mixed

1. **Original published forecasts**, scored as published under the version that produced them (picks row = first
   published forecast; regrades in `forecasts`).
2. **Historical replays** of a newer model = **event-time reconstructions**: inputs are matches dated before the
   prediction day; whether that information was public, and whether our system had ingested it, is labelled per row
   (section 4). Development evidence only.
3. **Forward forecasts** recorded after a model / challenger was frozen (learning.py `forward_units`: forecast logged
   after the freeze AND match start after the freeze).

## 3. Projection-vs-line labels

Every gain = baseline - ours, positive = ours better. Q1 = log-loss(50%) - log-loss(published P(Over | no push)).
Q2 = |line - actual| - |projection - actual|, in kills for kills and in headshots for headshots; a pooled kills + HS row
is relative to the line ((|line - actual| - |projection - actual|) / line). Dashboard numbers between review windows are
interim (14 matches on 2026-10-12): they are not decisions.

## 4. Replay reconciliation (kills Maps 1-2 board set, scratchpad/evid)

Round-4 set 360 rows / 80 HLTV matches -> excluded 30 second copies of the same player x HLTV match under another slug and
2 rows whose player identity resolves only from post-cutoff rows (carN, chante; chicken-coop-vs-detonate) ->
**328 rows / 80 matches**, every paired difference computed on exactly these rows.

Pipeline subsets (rows add to 328; matches overlap): older pipeline 234 rows / 72 matches; perf-2 forward 91 / 19;
other 3 / 2 (1 perf-1 row, 2 perf-2-bo3 rows).

Ingestion at forecast time (mutually exclusive, add to 328):

| category | rows | matches |
|---|---:|---:|
| proven: saved weighted rounds equal the replay's (same HLTV ids) | 31 | 5 |
| contradicted in part: live had fewer weighted rounds (20 used matches found after the forecast; 11 forecasts made before the rebuild) | 31 | 6 |
| consistent, not proof: Railway perf-2 rate (3 dp) + opponent factor (2 dp) equal the replay's | 29 | 11 |
| inconsistent: Railway perf-2 rate / opponent factor differ | 2 | 2 |
| unknowable: Render forecast without saved history counts | 11 | 4 |
| unknowable: no saved inputs, older pipeline | 223 | 68 |
| unknowable: no saved inputs, perf-1 | 1 | 1 |
| **total** | **328** | |

Underlying matches occurred before the match start on 328/328 rows. Relative to the forecast time: 0 rows use a map
dated after its day; 61 rows have their newest map on the forecast's own UTC day (order unknowable); 191 on the day
before; 76 only older. Public availability is assumed within a few hours of a match; dropping the 0-48 h maps moves
log-loss +0.003 (-0.003 to +0.007).

## 5. Recap (recap.py, frozen snapshot 2026-10-12 15:55:35 UTC; match days Oct 3-11 Central)

Frozen report (unchanged) and the dated update (Railway rows open in the snapshot settled read-only with the live rules
- HLTV strict identity, else BO3.gg full scoreboard; Railway is shut down). Pending = still open; in the update it is
the unresolved remainder. Hit rate = W / (W + L).

| cohort / basis | report | total | W | L | push | void | pending | hit | expected |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| board, deduplicated | frozen | 1387 | 568 | 529 | 2 | 125 | 163 | 51.8% | 55.7% |
| board, deduplicated | update | 1387 | 602 | 564 | 2 | 125 | 94 | 51.6% | 55.7% |
| staked board, deduplicated | frozen | 520 | 220 | 185 | 1 | 47 | 67 | 54.3% | 61.0% |
| staked board, deduplicated | update | 520 | 235 | 197 | 1 | 47 | 40 | 54.4% | 60.8% |
| member lines, deduplicated | frozen | 6732 | 3559 | 2374 | 83 | 309 | 407 | 60.0% | 62.5% |
| member lines, deduplicated | update | 6732 | 3743 | 2466 | 86 | 309 | 128 | 60.3% | 62.5% |

Audit settlement of the 564 open Railway rows: 345 settled, 3 push, 216 unresolved (120 BO3.gg does not list the player
in that match, 78 match not in our HLTV DB, 10 identity not confirmed, 8 no full scoreboard); by date / cohort / version
in `recap/update_20261012_unresolved.txt`. Railway's own pick-log volume (project "thriving-insight") would be the
authoritative source if restored.

## 6. Research record

Round 5 (FACTOR_AUDIT.md) is development research. Its preregistration timing (definitions 17:00 UTC, before the runs)
is supported only by file timestamps: definitions and results were committed together at 17:14. Its acceptance rule is
not changed after the result. C3's thin-history improvement (-0.054, -0.094 to -0.003) is a lead for future validation
on new matches, not a basis for promotion.

## 7. Settlement sources (2026-10-12 discrepancy audit, scratchpad/coverage2)

80,125 player-maps in both sources: kills differ on 3,384 (4.22%) across 2,052 maps.

| class (first match wins) | n | share | mean BO3 - HLTV |
|---|---:|---:|---:|
| team-kill definition (full scoreboard subtracts team-kills; 1,930 exactly -1) | 1,976 | 58.4% | -1.06 |
| kills and deaths both differ (311 rows = 36 whole-map mismatches) | 466 | 13.8% | +1.06 |
| round counts differ (BO3.gg partial / odd score) | 446 | 13.2% | -1.30 |
| full scoreboard differs, quick = HLTV (maps from 2026-03) | 384 | 11.3% | +3.93 |
| substitution (not 5 rows per team) | 40 | 1.2% | |
| forfeit / technical map | 34 | 1.0% | |
| wrong player paired | 25 | 0.7% | |
| kill credited to another teammate | 13 | 0.4% | |
| HLTV correction / other | 0 | | |

Maps 1-2 totals differ on 7.49% of 30,875 player-series; with lines placed like the 962 graded live props about 0.92% of
settlements would flip. From 2026-03 BO3.gg's QUICK scoreboard equals HLTV on ~99.2% of player-maps, the FULL one on
96.07%. Rule (picklog.SOURCE_RULE): HLTV authoritative; BO3.gg results provisional until HLTV re-derives them; the quick
total stands over the full one; when they differ with the line between them the pick is held for review (recap: pending;
learning: settlement_conflict, never scored) and logged in settlement_reviews.jsonl. The earlier 2026-10-12 corrections
that replaced quick totals with full ones (7 Fruju vs MARKandLARRY picks) are reverted with a logged entry each. The
settlement audit reports BO3.gg-based differences and never applies them.
