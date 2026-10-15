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

## 8. Settlement identity, cohorts and version pooling (2026-10-13 audit, picklog / recap / diagnostics / learning)

No member grade, side or stake changes (picklog.py is not in the model-version hash; the tag is unchanged).

- **Match identity = grading's.** A pick settles from HLTV only on its own HLTV match id: an `hltv-<id>` pick on that
  id, on every scope; a BO3.gg-slug pick on the HLTV match `matchid.canonical_match(team, opponent, listed start)` names -
  the same call the projection's odds link makes (canonical team ids + the maintained aliases; an HLTV schedule time
  within 3 h of the start, or a finished match dated that UTC day), picks.team first, then the BO3.gg profile team. The
  opponent is checked by matchid's canonical team id. No date window: yesterday's upper-bracket final or this morning's
  opener between the same teams is never the match (it used to settle the pick, stored as source hltv). Nothing settles
  before the listed start (was 12 h before). A pick whose canonical match names no HLTV match (hashed id, ambiguous,
  team not resolved) is not settled from HLTV: Maps 1-2 falls back to BO3.gg's own match slug (provisional), Map 1 /
  Maps 1-3 wait and void after 3 days like a match never found. Each HLTV settlement stores the identity it was read
  under (`result.identity`: "hltv id" / "canonical match"; "date window (matchid not loaded)" only where matchid has no
  HLTV team table - the unit tests, a first boot without the database). Evaluation groups matches with
  `identity.assign` (recap, learning, diagnostics); keying settled rows on the canonical HLTV id needs identity.py
  (RULE_VERSION 3) - learning re-keys its ledger automatically when that version changes.
- **Outages.** HLTV unreachable never becomes "not in HLTV": a Maps 1-2 BO3.gg-slug pick waits (no BO3.gg settlement,
  no void); a forfeit check that cannot be read waits too.
- **Missing stats.** A BO3.gg quick scoreboard without headshots is "not published yet" (was 0: an HS pick settled as a
  big Under).
- **Held for review.** When HLTV (authoritative) confirms the total, the hold and the provisional mark are released and
  the HLTV total recorded; an HLTV-confirmed pick is never held again by BO3.gg's full scoreboard. Held picks are
  excluded from diagnostics (counted) and slip-batch legs wait on them; an HLTV settlement decides a leg over a BO3.gg one.
- **Rechecks rotate.** The HLTV recheck (60 picks per pass) and the quick-scoreboard recheck (40 matches) start after the
  last one checked and wrap around, so rows HLTV never has no longer hold newer rows back.
- **Cohorts: one rule** (`recap.board_line`): board = test 2, or test 0 with board provenance (on_board 1 recorded /
  2 inferred; no column: a platform recorded). Used by recap, diagnostics (summaries, the pre-registered forward HS
  check, slip-leg lookup) and learning (`source_of`; forecast copies relabelled once - event `source_rule` - and the
  scorecard's line-source split read from them). Before, diagnostics and learning counted a board line a member opened
  as a member line. A board write lifts a row an owner load test reached first (test 1, no platform) to the board and
  fills its platform + market id; a board row the owner marked as a test by hand keeps test 1. The on_board backfill
  runs until its marker (`schema_marks.on_board_backfill`) is stored and only fills values it is sure of.
- **Pooling across versions.** MODEL_VERSIONS.md pools tags only inside one "same grading" group; no machine-readable
  group map exists, so every challenger review / interim forward result (`by_version`), the forward HS check
  (`by_version` per model) and the scorecard (`by.version`) report each exact tag, and `pooled_across_versions` flags a
  pooled figure spanning more than one tag. The pre-registered decision rules (REVIEW_RULE, the forward HS bands) are
  unchanged: a pooled decision that spans tags must be read against the group column of MODEL_VERSIONS.md.

## 8. Protocol amendment P1 - the product scorecard on offered book lines (owner, 2026-10-14)

**Adopted after the 2026-10-14 checkpoint results were seen.** That timing is stated here on purpose: P1 was not the
registered primary endpoint. At the checkpoint, the registered primary (section 1: the latest forecast of ANY source 30+
min before the start) scored better than the lines the books actually offered, because member-typed off-market lines are
easy (126 units at a printed 87.7% hit 96.8%). The owner made the offered lines the product scorecard.

- **Registered analysis kept unchanged.** Section 1's primary scorecard, the ledger, the lb-001 forward evaluation and
  learning.REVIEW_RULE are not altered or re-scored. P1 is reported beside them (learning `scorecard.product`, dashboard
  "Product scorecard").
- **P1 unit / forecast.** The registered unit (player x canonical match x stat x scope) with a scoreable settlement. Its
  forecast is the latest BOARD forecast logged >= 30 min before the start, on a STANDARD line from the platform feed. The
  verified fields are: platform (book), the board row's market id and player id from the feed, canonical match, stat, map
  scope, line and logging time. Member-entered lines and adjusted markets (goblin / demon) never enter P1; they are
  reported separately. A push on the board line is excluded and counted.
- **Four separate sections:**
  1. projection error (vs the actual total, and vs the book's line);
  2. probability quality and calibration (log-loss, Brier, calibration bands, slope);
  3. side selection (the favoured side's printed vs actual rate; a chance of exactly 50% has no side and is counted);
  4. staking (staked vs 0U, stake-weighted printed vs actual).
  No payout is computed: a single prop has no payout of its own.
- **Promotion rules unchanged.** Any new promotion rule built on P1 applies prospectively, to forecasts logged after it
  is frozen. It is never applied to matches already seen, and never fitted on this week's outcomes (for example, the
  60-65% band).
- **History revision** is reported per forecast:
  - N0 before 2026-10-13 00:49 UTC;
  - partial backfill until 04:26:15;
  - N (the unintended backfill) until the N0 pin;
  - from 2026-10-14 22:53:13 UTC, `perf_input=N0/<rule>` from data_rev.

## 9. Settlement fault of 2026-10-13 (learning paused) - cause and repair (2026-10-14)

Seven Fruju vs MARKandLARRY Maps 1-2 kills picks (524, 536, 551, 560, 830, 923, 936) failed the settlement audit:
the stored map detail gave 39 / 30, the settled totals were 38 / 29. Learning paused at 2026-10-13 00:53 UTC and stayed
paused.

**Checked against the underlying records.** HLTV match 2399144, read-only on 2026-10-14:
- BRACE 23 + 15 = 38;
- yourwombat 15 + 14 = 29.

The settled totals were right. The stored detail was BO3.gg's FULL scoreboard (map 2: 16 / 15), which counts team-kills
differently.

**Two code causes:**
1. `revert_full_scoreboard_corrections` restored the quick totals from the correction log. That log never kept per-map
   numbers, so the full scoreboard's maps stayed as the "quick" detail.
2. `recheck_bo3_with_hltv` confirmed the totals from HLTV but copied the BO3.gg detail and `source: bo3` forward.

**Fix.**
- An HLTV confirmation now writes HLTV's own detail and source. The BO3.gg record is kept nested as `bo3_detail`.
- When the old detail gave a different total, a detail-only entry is appended to settlement_corrections.jsonl (old and
  new detail; the settlement itself is unchanged).
- The revert keeps no per-map detail it does not have.
- Rows already confirmed with BO3.gg detail are re-derived once on the next settle pass. That is idempotent: a row
  becomes `source: hltv` and is never due again.
- Original forecasts are untouched. The learning ledger records the repaired units as a new settlement version (a
  correction) and resumes by itself once the audit is clean.
- The faults were not acknowledged to unlock the counter.

Tests: tests/test_eval_pipeline.py.

## 10. What updates automatically, and what never does (2026-10-15)

- **Every learning cycle** (after settlement passes) copies new forecasts and settles and scores newly eligible units:
  the registered scorecard, the P1 product scorecard, and every frozen challenger's shadow and paired comparison.
  **This is automatic settlement and evaluation. It never changes a live weight, grade, chance or stake.** Grading
  changes only through a code release.
- **The 150-match training counter** (`learning.N_NEW`, `TRAIN_RULE`):
  - It counts DISTINCT canonical matches with at least one scored registered evaluation unit, processed after the
    last training run's cutoff. Only matches are counted - not forecasts, lines or units.
  - At 150, one training run builds a snapshot: scored registered units with match start before the cutoff, whose
    perf-2 projection can be inverted exactly, saved with its hash.
  - It refits only the perf-refit-1 family (perf-2 parameters: prior strengths, LAN/online blend, HS shrink, spreads)
    by log-loss on time-ordered folds of that snapshot.
  - It FREEZES a challenger only if both validation gains are positive, and does nothing while one of that family is
    in its forward test.
  - Hand-defined challengers (lb-001, rr-1, pl-1) are never retrained by it.
- **The 300 / 600 / 1,000 promotion review** (REVIEW_RULE) is per challenger. It counts distinct FORWARD matches: the
  forecast logged after that challenger's freeze AND the match started after it. It is reported on the registered view
  and on P1 board lines.
  - A pass makes the challenger "eligible for review" only.
  - Promotion is an explicit owner action with a written reason and a new model version.
  - So the training counter (150, since the last training run, all scored matches) and a challenger's review count
    (300+, since its own freeze) are different clocks.
- **No match count guarantees an improvement.** A window records the result, whatever it is.
- **A refit is a new immutable candidate** (2026-10-15).
  - When perf-refit-1 trains, the training run saves its cutoff, snapshot id, data hash, the history revision of every
    training row (`trainings.detail.data_revisions`), and its parameters.
  - A frozen candidate gets a new id and an immutable `challengers.provenance` record with all of these.
  - Its forward test and review windows start at its own freeze: forward_units and reviews are keyed by its id. It never
    inherits an earlier candidate's forward results or review counter.
  - A forced retrain supersedes the earlier candidate; that candidate's forward rows become development data.
  - Tested: tests/test_eval_pipeline.py.
