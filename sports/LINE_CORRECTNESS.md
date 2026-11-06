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

R6. **Existing databases.** Migration 2 is the archive schema (sports/archive.py, Starter); migration 3 is additive (`ALTER TABLE ADD COLUMN late / source_updated_at /
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

R9. **Incremental forecasting** (store migration 4, `forecast_keys`). A forecast's INPUT KEY (`store.forecast_key`) is
its whole content except the time it was made: offer revision (`offer_snapshot_ids`), every input record id, model /
artifact / data / calibration / grade-policy / stake-policy versions (sm-1 `data_version` = hash of the feature row),
reason codes and the output. "Inputs changed" = any of those differs: a new offer revision, new stat observations or
context that change the feature row, a new quote record or a quote dropping out, a new artifact or policy version.
`store.put` returns `unchanged` (writes nothing) for a key already stored; forward writes `model/forecasts.db` only for
inserted forecasts. model/forecasts.db `inputs` holds ONE row per stored forecast: feature row (row_json), market
sources (market_json), PMF (pmf_lo, pmf_json), grade-policy output (policy_json), offer record id, artifact sha.
Existing forecasts are never deleted or rewritten; forecasts stored before migration 4 are indexed once (oldest first).
The slip pool selects forecasts by the current offers' events (an unchanged revision keeps its first forecast).

R10. **Settlement at the forecast's own line** (`settlement.at_line`): settlements are keyed (platform, offer, side)
and carry the offer's line at settlement; a forecast or slip leg made at another line is scored at its own line from
the recorded box-score value; void (did not play) and unresolved are line-independent.

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
| A1 | A->B->A where A (and B) were archived: the return continues the numbering (revision 3), is current and hot; archived A->B->A reads back the final A | canonical + archive | test_a1_a_b_a_where_a_was_archived, test_a1_archived_a_b_a_reads_back_the_final_a | done |
| A2 | a delayed response for an archived identity is late (judged against the archived current revision) | canonical + archive | test_a2_a_delayed_response_for_an_archived_identity_is_late | done |
| A3 | withdrawal -> archive -> reopen at the same line = open, current | canonical + archive | test_a3_withdrawal_then_archive_then_reopen_at_the_same_line | done |
| A4 | 3 writer processes while archive passes run: unique contiguous revisions per identity across hot + archive, newest write current, archive verify clean | canonical + archive | test_a4_concurrent_writers_while_the_archive_pass_runs | done |
| B1 | price A->B->A = 3 quote rows, final current; line A->B->A reopens the first line (open, removed, open) | books.db | test_line_correctness_books::test_b1_price_a_b_a_is_three_rows_final_a_current, test_b1_line_a_b_a_reopens_the_first_line | done |
| B2 | A->A: last_seen_at + last_provider_updated_at move, no quote row | books.db | test_b2_a_a_moves_freshness_only | done |
| B3 | delayed response = late history row, never current; delayed complete read never withdraws; last_seen never rewinds | books.db | test_b3_delayed_older_response_never_becomes_current, test_b3_delayed_complete_read_cannot_withdraw_what_a_newer_read_saw, test_b3_delayed_duplicate_does_not_rewind_last_seen | done |
| B4 | withdrawal + reopen at the same line = open again; suspended without price no longer crashes the change check | books.db | test_b4_withdrawal_and_reopening_at_the_same_line, test_b4_suspended_without_price_does_not_crash | done |
| B7 | 4 concurrent writer processes: one consistent current quote (newest observation) | books.db | test_b7_concurrent_writers_keep_one_consistent_current_quote | done |
| B8 | existing books.db: additive ADD COLUMN, rows kept, restart idempotent | books.db | test_b8_existing_books_db_is_migrated_additively | done |
| B9 | provider vs observation times separate; each read stamped with its own read time (was: pass start) | books.db / books worker | test_b9_provider_and_observation_times_are_separate, test_b9_books_worker_stamps_each_read_with_its_own_time | done |
| B10 | shared books lease: released after a pass, so a second league process runs next (one ingest-loop child runs both leagues in turn under _COLLECT_LOCK in the deployed config) | books.db | test_b_lease_is_released_so_the_other_league_process_runs_next | done (decision: no per-league lease; overlap is refused, not starved) |
| G1 | prop A->B->A = 3 rows, factors read the final A; Kalshi market A->B->A and closed->reopened | games.db | test_line_correctness_games::test_g1_prop_a_b_a_is_three_rows_and_factors_read_the_final_a, test_g1_kalshi_market_a_b_a_and_closed_then_reopened | done |
| G2 | A->A with a new provider time = freshness only (was: a new history row - provider_updated_at was in the change digest) | games.db | test_g2_a_a_with_a_new_source_time_is_freshness_not_a_line_change | done |
| G3 | delayed prop / market / snapshot = late row; factors.py (_dk, _game_lines, _snapshot) skip late rows (was: MAX(id) let a late insert win); last_seen never rewinds | games.db | test_g3_delayed_older_response_is_history_never_current | done |
| G4 | prop closed and reopened at the same line = open again | games.db | test_g4_prop_withdrawn_and_reopened_at_the_same_line | done |
| G7 | 4 concurrent writer processes: one consistent current row, factors agree | games.db | test_g7_concurrent_writers_keep_one_consistent_current_row | done |
| G8 | existing games.db: additive columns; digests written under the old rule still match (no one-time burst of rows) | games.db | test_g8_existing_games_db_is_migrated_additively_and_old_digests_still_match | done |
| G9 | provider vs observation times separate | games.db | test_g9_provider_time_and_observation_time_are_separate | done |
| C5 | after A->B->A: store.offers, the new forecast, board.json, the player card, the slip pool and slip validation all name revision 3; a slip built on revision 1 is refused ("changed") | snapshots / slips / forward | test_line_correctness_model::test_c5_board_card_slip_pool_validation_and_new_forecast_use_the_same_revision | done |
| C6 | forecasts of revisions 1, 2, 3 keep their offer_snapshot_ids and replay bit-identically (also after a new artifact, under their own artifact) | forward | test_c11_forecasting_is_incremental_and_c6_earlier_forecasts_replay_unchanged | done |
| C10 | a settlement records the offer's line at settlement; a forecast / leg made at another line is scored at ITS line from the box-score value (settlement.at_line), void/unresolved unchanged: learning.on_settled, slips.settle.settle_slip, snapshots.results_view | settlement / learning / slips settle / snapshots | test_c10_settlement_is_read_at_the_forecast_or_leg_line, test_c10_slip_leg_is_settled_at_its_own_line | done |
| C11 | incremental forecasting: two runs with no change add 0 rows to canonical.db and model/forecasts.db; A->B and B->A add exactly one forecast per new revision; a new artifact re-forecasts once; pre-migration forecasts are indexed so the first run after deploy adds nothing; every writer (forward, sharp-2, legacy) goes through the store rule; the slip pool finds an unchanged revision's old forecast | store / forward / sharp2 / slips.pool | test_c11_forecasting_is_incremental_and_c6_earlier_forecasts_replay_unchanged, test_c11_existing_forecasts_are_indexed_on_migration_so_the_first_run_after_deploy_adds_nothing, test_c11_the_store_rule_covers_every_forecast_writer, test_c11_slip_pool_finds_an_old_forecast_of_an_unchanged_revision | done |
| C5b | slip COPY re-checks each leg's live revision behind the cached (prebuilt <= 90 s) pool: a line that moved since the snapshot is "changed", never copied | slips.validate / api_slips | test_c5_copy_rechecks_the_live_revision_behind_a_cached_pool | done |
| C12 | an unchanged quote re-confirmed by its source is as fresh as its NEWEST source time (was: the first sighting's), never a source time after forecast_at | model.market / forward | test_c12_an_unchanged_book_quote_is_as_fresh_as_its_newest_source_time | done |

## Results

Checkpoint 1 (canonical store): `python -m pytest sports/tests -q` -> 430 passed, 10 skipped.
Checkpoint 2 (books.db): `python -m pytest sports/tests -q` -> 443 passed, 10 skipped.
Checkpoint 3 (games.db): `python -m pytest sports/tests -q` -> 451 passed, 10 skipped.
Checkpoint 4 (read side + incremental forecasting): `python -m pytest sports/tests -q` -> 459 passed, 10 skipped;
esports `python -m pytest tests lol/tests -q` -> 1782 passed, 5 skipped.

Forecast growth per run (test world: one target offer, sm-1; scratch script, same world on both commits):

| run | before (d85b4a7) canonical forecasts / forecasts.db rows | after |
|---|---|---|
| 1st (21:00) | +1 / +1 | +1 / +1 |
| 2nd, no change (21:30) | +1 / +1 | +0 / +0 |
| 3rd, no change (22:00) | +1 / +1 | +0 / +0 |

Production measurement (Starter agent, before): +3,410 forecast records (+4.8 MB canonical.db) and +14 MB
model/forecasts.db per run with no new offers; after this change the same run adds 0 (one forecast per offer revision
x input key).

Checkpoint 5 (slip copy live check): 460 passed. Merge of sports/starter (archive): 480 passed. Checkpoint 6 (archive
interplay A1-A4): `python -m pytest sports/tests -q` -> 485 passed, 10 skipped.

## Archive interplay (sports/archive.py, Starter)

- An identity is archived only as a whole (every revision, incl. late rows) once its newest sighting is 72 h old;
  `records_all` (hot UNION archive) carries `late` and the source / origin columns, so every point-in-time and
  scoped read applies the same CURRENT rule. A league-wide current listing reads the hot window only (by design: an
  offer not seen for 72 h is not current).
- A returning archived identity continues its revision numbers (`archive.identity_tail`); its archived current
  revision is the reference for the late rule; content equal to the archived current one becomes a NEW revision
  (the offer was gone for 72 h - a return, not a sighting).

## Known limitations / decisions

- Pre-fix repair (R6) cannot recover the exact time a returning line came back, nor the intermediate history of
  A -> B -> A -> B sequences recorded before c0cc754 (the old rows only kept the last sighting): the repaired
  revision is dated at the last sighting of the returning content. Forecasts made in that window keep the inputs
  they were made with (B) - they are not rewritten.
- `seen_count` of pre-fix revisions over-counts sightings of returning content (bookkeeping only; not repaired).
- `source_time_unknown` is part of an offer's content hash (contract sc-1): a feed that flips between giving and not
  giving a source time creates a revision. Changing the hash would re-revision every stored offer once; left as is.
- Liquidity / book sizes / extras are excluded from the content hash (sc-1), so they keep the first sighting's values
  on a duplicate (freshness of those fields is not tracked; prices / lines / availability are).
- The slip pool / board are snapshots (prebuilt <= 90 s; UI snapshots per build). Between builds a surface can show
  the previous revision; COPY re-checks live (C5b). Board and card are built in one pass from one store read, so they
  always agree with each other.
- The books lease is shared by leagues: a second league PROCESS finds it busy and waits for the next cycle (the
  deployed service runs one ingest-loop child that takes leagues in turn, so no league is starved).
- books.db / games.db on Postgres (SPORTS_DB_DSN) use a transaction-scoped advisory lock; only the SQLite path is
  exercised by the tests (no Postgres in CI).

## Audit 2026-11-05 (S1/S2, S3, A7) - the line's real market
- Underdog period lines (`appearance_stat.stat` `period_1_points` = display "1Q Points", `period_1_2_*` = 1H) are
  stored with their real scope (q1 / first_half) since sc-1. The sheets ignored the scope and compared them with
  full-game history: the "Most consistent" 100% rows (Reaves Points 3.5 Over 0.86x, Maye pass yds 49.5, ...) were 1Q
  lines. Now: only full-game markets reach the board files, the sheets and the workspace; quarter / half lines stay on
  the player page with their period. The forecast record (results_public rp-2 and the results ledger) skips them.
- An Underdog line that LISTS one side (higher-only at a changed payout) is stored `one_sided` by adapters.ud_offers
  (contracts VARIANTS). Member pages label it by its sides ("Over only"), never Standard, and it is never the sheet's
  'current line'. Follow-up 2026-11-06: its grading scope is UNCHANGED from before the audit (a full-game one-sided
  line is graded, forecast and pickable as when it was stored `balanced`; the unlisted side stays refused by the
  slip pool's "chosen side not offered"). Narrowing that is an owner decision. Stored rows are never re-read by
  their offered sides, so a suspended side never changes a line's type, grading or record.
- di-1 (median hold) was removed 2026-11-06: it hid real full-game lines (Tank Dell rec_yds 15.5 = FanDuel 15.5,
  most deep goblins) while they stayed forecast and in the slip pool; the 1Q / 1H lines it targeted are already
  left off by scope.
- Results rp-2: one representative forecast per game x player x stat (pick'em main line before books before Kalshi,
  then the line closest to the forecast's own projection, newest first); raw forecast counts kept as raw_counts.
