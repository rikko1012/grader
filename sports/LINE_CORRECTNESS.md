# Line correctness (sportsbook store, game-data store, canonical store, cached snapshots)

Owner requirement (2026-10-23): finish the A->B->A audit across every place that decides which line is CURRENT before
deploying. This file holds the rules, the test matrix (done / remaining) and the results. Branch `sports/lines`.

## Rules

R1. **One current revision per identity.** Canonical store (`sports/store.py`): the current revision of
(kind, source, source_key) is the highest revision among rows with `late = 0` (`store.CURRENT_SQL`), known at `as_of`
when one is given. Every reader uses that predicate: `store.latest / offers / events / _latest_rows / history`,
`snapshots._latest` (board + player card), `slips.pool` (via `store.offers`), `model.forward` (via `store.offers`,
`_current_team`), `worker._latest_memberships`, `books.sources`, `assistant.tools`, `lab.sources`.

R2. **Duplicate = same content as the CURRENT revision only.** A -> B -> A is three revisions, the final A current.
A -> A is a sighting: `last_seen_at` (newest observation, MAX - never moves backwards), `last_source_updated_at`
(newest source time, MAX), `seen_count + 1`. No new revision, no line change invented. The payload is the record as
first observed and is never rewritten (replay reads it).

R3. **Delayed responses never overwrite a newer quote** (`store.is_late`). A record whose content differs from the
current revision is LATE when
- both it and the current revision carry a SOURCE update time and they differ: late iff its source time is older;
- otherwise: late iff its observation time is older than the current revision's newest sighting (`last_seen_at`).
A late record is stored as history (`late = 1`, next revision number) and is never current - not now and not in any
`as_of` read. A delayed duplicate only moves the sighting counter (times are MAX'ed).

R4. **Withdrawal and reopening.** A COMPLETE read that no longer lists an offer writes a `removed` revision
(`store.withdrawn`: availability removed, source time cleared because the time is ours, observed_at = that read). A
failed / partial read never withdraws. Reopening at the same line is a new revision (R2) and is `open` again.
Because the withdrawal carries our observation time, a delayed older read can neither reopen nor withdraw a newer
state (R3).

R5. **Atomic current decisions across processes.** Every canonical write runs in `BEGIN IMMEDIATE` (`store.write_tx`;
`put_many` = one transaction per batch), and `UNIQUE (kind, source, source_key, revision)` (`records_ident_rev`) makes
two rows with one revision impossible. NBA / NFL workers and the service's children may write one SQLite file at once.

R6. **Existing databases.** Migration 2 is additive (`ALTER TABLE ADD COLUMN late / source_updated_at /
last_source_updated_at / origin`, `CREATE INDEX`), applied inside `BEGIN IMMEDIATE` (concurrent starts migrate once; a
crash rolls back). Then, once, `repair_prefix_duplicates`: pre-c0cc754 stores matched returning content against ANY
earlier revision, so A -> B -> A only bumped revision 1's `last_seen_at` / `seen_count` and `latest` kept B (also: a
withdrawn offer reopened at the same line stayed `removed`). Evidence = an earlier revision last seen AFTER the current
one was last seen. For each such identity one revision is APPENDED with the most recently seen content,
`known_at = observed_at = that last sighting` (the exact return time is lost; the earliest provable time is used),
`origin = repair:prefix-duplicate:<source record id>`. No row is updated or deleted. Result stored in
`meta.repair_prefix_duplicates`. A read-only reader of a not-yet-migrated file reads through a temp view with the new
columns' defaults (late = 0), i.e. exactly the old behaviour until the worker migrates.

R7. **Forecast inputs are immutable.** A forecast names the offer record id it was made for (`offer_snapshot_ids`);
that row's payload never changes, so earlier forecasts keep their original inputs and replay bit-identically.

R8. **Source time and observation time are separate** everywhere: `source_updated_at` / `last_source_updated_at`
(the source's own clock, None when it gives none - never invented) vs `observed_at` / `last_seen_at` (our read).

## Test matrix

| # | Check | Store | Test | State |
|---|---|---|---|---|
| C1 | A->B->A = 3 revisions, final A current | canonical | test_line_correctness::test_c1_a_b_a_makes_three_revisions_and_final_a_current | done |
| C2 | A->A refreshes freshness, no line change | canonical | test_c2_a_a_refreshes_freshness_without_a_line_change, test_c2_withdrawn_offer_seen_again_unchanged_stays_withdrawn_but_fresh | done |
| C3 | older delayed response cannot overwrite | canonical | test_c3_older_source_time_cannot_overwrite_a_newer_quote, test_c3_without_source_times_the_observation_time_orders, test_c3_delayed_duplicate_never_moves_freshness_backwards, test_c3_delayed_complete_read_cannot_withdraw_a_newer_offer | done |
| C4 | withdraw + reopen same line = available | canonical (bridge + pick'em collector) | test_c4_withdrawal_and_reopening_at_the_same_line_is_available_again, test_c4_pickem_board_withdrawal_and_reopen_through_the_collector | done |
| C7 | concurrent processes: one current revision | canonical | test_c7_concurrent_workers_cannot_create_conflicting_current_revisions (4 processes x 40 rounds) | done |
| C8 | existing-db migration + repair, restart | canonical | test_c8_existing_prefix_database_is_migrated_and_repaired_additively, test_c8_read_only_reader_of_an_unmigrated_store_still_reads, test_c8_two_processes_migrating_at_once_migrate_once, test_c8_worker_killed_mid_cycle_leaves_no_partial_batch_and_rerun_is_idempotent | done |
| C9 | source vs observation times separate | canonical | test_c9_source_and_observation_times_are_separate_columns | done |
| C1-C4,C7-C9 | same checks | sportsbook store (books.db) | - | remaining |
| C1-C4,C7-C9 | same checks | game-data store (games.db) | - | remaining |
| C5 | board, card, slip pool + validation, new forecast use the same revision | snapshots / slips / forward | - | remaining |
| C6 | earlier forecasts keep inputs, replay unchanged | forward | - | remaining |
| C10 | settlement scores a forecast at its own line | settlement / learning / slips settle | - | remaining |
| C11 | forecasting is incremental (no new forecast for unchanged inputs) | forward | - | remaining |

## Results

Checkpoint 1 (canonical store): `python -m pytest sports/tests -q` -> 430 passed, 10 skipped.
