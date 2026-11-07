# Sports storage retention plan (Starter, 5 GB disk)

Status: **PLAN.** Nothing here deletes data, changes the disk size or changes what is collected. Owner rules
(2026-10-24): the cheapest plan **without getting rid of any data**; the authorized and existing disk is **5 GB**
(Starter $7 + 5 GB = $8.25 / month) - **no expansion is authorized**; collected data and the existing collection detail
stay as they are unless the owner approves a change; compression work may continue.

**For the owner to reconcile before the final SHA:** `sports/starter` db40894 (`deploy/render.sports-api.yaml`, RUNBOOK
13.3) assumes a **10 GB** disk ($9.50 / month) and `SPORTS_STORE_MAX_MB=6000` (canonical.db only). On the existing 5 GB
disk that cap sits above the disk itself, so the disk would fill before ingestion pauses. Nothing in this plan applies
10 GB; section 3 gives the runway on 5 GB.

Compaction mechanism: `sports/archive.py` (Starter service work; arch-2 since `sports/starter` 49be244). It moves every
revision of an offer identity not seen for `SPORTS_ARCHIVE_AFTER_H` (72 h) into verified lzma files under
`<SPORTS_DATA_DIR>/archive/records/` with an index in canonical.db, re-reads and hash-checks each file before ONE
transaction removes exactly those hot rows, and keeps them readable (reads span hot + archive). Events, players,
stats, contexts, forecasts and settlements stay hot.

## 1. Measured (2026-10-24, local sandbox, real public feeds, no paid keys, nothing on Render)

Evidence: `sports/research/results/storage_2026-10-24/`. Every figure names the tree it ran on.

**a. First cycle and warm reads** (tree: PR17 506d1ae + the repair as of 6bceeff - before the split; 01:21-02:08 UTC,
Friday-night NBA games in progress). `measurements.json`, `measure.py`, `profile_rss.py`.

| Code | Wall | Peak RSS | Disk after first cycle | sc-1 records |
|---|---|---|---|---|
| PR17 head 506d1ae (espn, pp, ud, kalshi, injuries, oddsapi) | 135 s | 172 MB | 36.3 MB | - |
| repair before the memory fixes | 277 s | 301 MB | 86.2 MB | 45,677 |
| repair with the memory fixes and the main-line bridge (now optional 2d) | 267 s | 212 MB | 64.0 MB | 32,106 |

| Source (new records per read) | 8 min later | 31 min later | On games already started (8-min read) |
|---|---|---|---|
| PrizePicks | 1,849 | 2,743 | 89% (live games: the listed start time rolls forward every read) |
| Kalshi | 2,758 | 3,402 | 50% |
| Polymarket | 1,993 | 2,728 | 52% |
| Underdog | 59 | 329 | 47% |
| FanDuel sc-1 | 2,890 (all lines; 93% alternate rungs) | 219 (main lines only = 2d) | - |
| canonical.db / books.db / games.db growth | +14.4 / +1.3 / 0 MB | +14.7 / +2.1 / +0.9 MB | |

c0cc754 (a returning price is a new revision) adds at least ~6% to exchange revisions (>= 385 Kalshi / 269
Polymarket returns in that window).

**b. Memory - constrained real cycles** (`constrained.jsonl`, each row labelled with its tree): the Starter
scheduler's exact job commands and child environment (single-threaded BLAS, `MALLOC_ARENA_MAX=2`, nice 10),
concurrency 1, each job inside a cgroup limited to its scheduler cap. Latest tree - `sports/starter` db40894 (=
`sports/release-starter` da8eb2c + Starter checkpoints 5-8) + 2b + 2c, with and without 2d:

| Job (cgroup = db40894 cap) | Peak RSS | VmPeak | cgroup peak | Result |
|---|---|---|---|---|
| ingest, first cycle (300 MB) | 184 MB | 239-240 MB | 230-234 MB | done, 0 OOM |
| ingest, warm (300 MB) | 180 MB | 237-238 MB | 178 MB | done |
| ingest-kalshi (240 MB) | 75-76 MB | 176 MB | 81-82 MB | done |
| settle (120 MB) | 55-57 MB | 197 MB | 45-47 MB | done |

The earlier trees (`sports/starter` 1bd3d23 + the first split; `sports/release-starter` da8eb2c + the previous 2a/2b/2c)
gave RSS 178-194 / VmPeak 237-257 MB for ingest. `SPORTS_WORKER_MAX_MB` is applied as RLIMIT_AS (virtual address
space): at 220 the NFL ingest died of `MemoryError` at RSS 131 MB without the arena limit and ran pinned at the cap
with it. Proposed (commit 2c separates the two knobs): RSS stays governed by the scheduler's per-job caps;
**`SPORTS_WORKER_MAX_VM_MB=320`** for ingest / Kalshi / settle (every run above passed at it) with `MALLOC_ARENA_MAX=2`;
model jobs need their own measured VmPeak (`sports/starter` measured their RSS at 308-311 MB, so 320 virtual would be
too low for them - the scheduler can pass a per-job value). `sports/starter` db40894 sets `SPORTS_WORKER_MAX_MB=768`
as the backstop, above Starter's 512 MB.

**c. Retention check on the combined tree** (db40894 + 2b + 2c, data of the first + warm cycle at 03:31-03:42 UTC;
`retention_combined_db40894.txt`): 63 MB on disk, hot sc-1 records ~1,490 B each (records + indexes). An archive pass
over everything (`SPORTS_ARCHIVE_AFTER_H=0`, `verify` clean) moved 22,899 offer rows into 1.8 MB of files + a 4.1 MB
index (~260 B per row, ~5.8x; the Starter work measured 8.2x on a longer sample where identities carry more
revisions) and freed 33 MB of pages that new rows reuse before the file grows. `sports.storage_report` reads that
schema (hot bytes, free pages, archive files and index). With 2d the same cycles stored 2,216 -> 402 BetRivers sc-1
offers (FanDuel had no NBA props posted at that hour); books.db kept all 3,305 quote rows either way.

## 2. The repair commits and collection detail

| Commit | Changes what is collected? | Effect |
|---|---|---|
| 2b bounded withdrawal scan, Polymarket paging, whole-dir cap | **no** - identical rows, events and offers (tests + live / real-store comparisons) | per-scan allocations ~86-103 -> 2-25 MB; Polymarket peak 126 / 155 -> 71 / 47 MB |
| 2c virtual-memory cap knob | no | the RLIMIT_AS can be sized from VmPeak instead of the RSS budget |
| **2d main-line bridge (needs the owner's approval)** | **what sc-1 receives**: alternate rungs / promos stay only in books.db (still collected, still stored) and no longer appear in UI quotes | 01:21 sample: bridged first-read offers 12,795 -> 1,324, FanDuel revisions per read 2,852 -> 219; 03:31 sample: 2,648 -> 834 |

## 3. Runway on the authorized 5 GB (no expansion)

Inputs: the Starter work's projection for its cadence (`sports/starter` db40894 RUNBOOK 13.3: start ~1 GB; canonical
offer revisions 440-950 MB/day raw, i.e. a 72 h hot window of ~1.3-2.9 GB; accumulating after compaction ~150-330
MB/day incl. books.db and games.db, which are not compacted) and section 1. Their sample had no book read due; without
2d every FanDuel / BetRivers read adds its alternate-rung revisions to the raw rate (one FanDuel read wrote 2,852
sc-1 revisions, ~4 MB), so their range is a floor without 2d.

| From an empty disk | Low | High |
|---|---|---|
| hot window filled (day 3) | ~2.6 GB | ~4.5 GB |
| `disk_80` (4.0 GB) and the ingestion pause at `SPORTS_STORE_MAX_MB=4000` | ~day 12 | ~day 3 |
| `disk_90` (4.5 GB) | ~day 15 | ~day 3-4 |

So on 5 GB the current collection detail lasts **roughly 3 to 12 days** before ingestion pauses itself (no data is
lost; collection stops until space is made). Options, none applied - each is the owner's decision:
1. enlarge the disk (cost; the Starter work's table: $0.25 per GB-month);
2. approve 2d (removes duplicate copies only);
3. compact books.db quotes and games.db lines like sc-1 offers (compression - the Starter work's listed follow-up);
4. a shorter `SPORTS_ARCHIVE_AFTER_H` (compression; 24 h cuts the hot window to ~0.45-0.95 GB, roughly 6-17 days
   to `disk_80`; history older than a day is then read from the archive - 36 ms per record, 0.7 s per event);
5. collect fewer price ticks (e.g. no price-only revisions after the start - T1 below): a collection-detail change.

Required on 5 GB whatever is chosen: `SPORTS_STORE_MAX_MB` below the disk - **4000** with 2b's whole-directory cap
(the template's 6000 counts canonical.db only and exceeds the disk).

Forecasts: the incremental forecasting in `sports/release-starter` adds 0 rows on an unchanged run (measured by the
line-correctness and Starter work); the Starter projection carries ~10-30 MB/day for forecasts.

## 4. Freshness: collector health vs quote eligibility (for the combined review)

- **Collector health (alerts only):** `feed_stale` fires when a platform's newest sighting is older than 45 min (pick'em,
  books, Polymarket) / Kalshi 150 min. The Kalshi value moved from 90 to 150 min in `sports/starter` 33a5642 because
  Kalshi is collected hourly there. It is a lateness alert, not an eligibility rule.
- **Quote eligibility (unchanged, every tree checked):** a market quote anchors a model comparison only if it is at
  most 15 min old at the forecast time (`market.MAX_AGE_S`; Kalshi rungs `sharp2.eligible_rung`, observed <= 15 min
  before the forecast) and observed before the start. Age is measured from the provider's own quote time when it
  publishes one (`source_updated_at`, and the line-correctness work's `last_source_updated_at` for a re-confirmed
  quote), else from our fetch / sighting time. Slip legs need a sighting within 25 min (`slips/pool.py` `STALE_S`).
- **So a slower schedule makes fewer quotes eligible, never older ones.** Hourly Kalshi means a Kalshi quote can anchor
  only forecasts made within 15 min after a Kalshi read. With hourly, exclusive model forecasts, Kalshi anchors only
  when the forecast runs right after `ingest-kalshi`. A 15-min collector against the slip pool's 25-min window holds
  legs after a single late cycle (safer, not staler).
- **Source time vs fetch time** stay separate in every record: `source_updated_at` (provider time, else None with
  `source_time_unknown`) beside `observed_at` / `known_at` / `last_seen_at` (ours).
- **Policy decisions for the review (none applied):** run model forecasts right after the Kalshi read, or accept
  fewer Kalshi anchors; the slip pool's `STALE_S` comment still says "ingestion runs every 10 min".

## 5. Tiers

### Never destroyed (immutable evidence)
- sc-1 `forecast` records (all versions) and every record a forecast references (`offer_snapshot_ids`,
  `input_snapshot_ids`); `model/forecasts.db` inputs; `settlement` records with the `stat` / `event` records they
  settle against; `event`, `team`, `player`, `membership`, `context` records.
- `model/learning.db`, model artifacts / registry, challengers - evaluation provenance; `sports.db` (the 6,903
  preserved forecasts) and `sports/research/forecasts/` exports.
- Every offer revision (they may MOVE to verified archive files and stay readable); `books.db` quote history and
  `games.db` data (historical prices and lines cannot be fetched again); archive files and their manifest / index.
- `pregame_news.db` (pgn-1, `pregame_news/`): pre-game news (injury report, depth charts, pre-game game injuries and
  starter / active flags) as observed. Append-only by trigger, change-only, payload <= 4 KB, hard budget
  PREGAME_NEWS_MAX_MB (default 256): at the budget new rows are skipped and counted, never deleted.

### T1 - bound at the source (proposal; collection detail - owner's decision; not in any commit)
After an event's start, store no price-only revision of its offers (keep availability / status changes and the last
pre-start revision). Measured: 89% of PrizePicks, 50% of Kalshi, 52% of Polymarket revisions in a live-game read.
The market candidates use only pre-start quotes, settlement uses box scores, `verify` reads settled Kalshi markets live.

### T2 - bounded, regenerable copies (the only things that may be pruned)
- `cache.db` (copies of provider answers, re-fetchable): entries older than 7 days (longest TTL used is 12 h).
- `snapshots/`, `prebuilt/`, `model/api/`: rebuilt from the store and rewritten in place.

### T3 - compaction into verified archive files (sports/archive.py; nothing destroyed)
- Offer revisions of identities not seen for 72 h. Candidates for the same treatment: `books.db` quotes, `games.db`
  prop / market lines, `quarantine`, operational logs (fetch logs, ledger log, finished jobs).

## 6. Archive and read-back rules (what any compaction must keep doing)

`sports/archive.py` implements 1-4; 5-6 are this plan's additions.
1. Select deterministically (identity cold for 72 h); never forecasts, settlements, stats, events, players, contexts.
2. Write each part to a temp file, fsync, rename, fsync the directory; manifest with rows, sha256, bytes, version.
3. Read back before removing anything hot: re-read, recount, check the sha256 and that every payload re-parses to its
   stored content hash; the hot rows must be unchanged and the removal must touch exactly that many rows, in the same
   transaction as the manifest + index. Any mismatch stops the pass.
4. Verify every listed file daily (`python -m sports.archive verify`); a bad file pages.
5. Off-box copy (addition): the archive sits on the same 5 GB disk; copy new parts + manifests to an owner-chosen
   destination off the disk (none chosen yet) and check the remote sha256 - disaster recovery, not a precondition.
6. Never destroy (addition): removing a record from disk entirely needs the owner's approval per batch and a
   separately reviewed tool with a restore test.
7. Space: SQLite reuses freed pages, so files stop growing without a VACUUM (which needs about the DB's size free).

## 7. Monitoring and alerts

| Signal | Warn | Page | Source |
|---|---|---|---|
| Render disk (5 GB) | `disk_80` (4.0 GB): owner decision from section 3 this week | `disk_90` (4.5 GB) | `sports/starter` 33a5642 ops alerts |
| Whole data dir vs `SPORTS_STORE_MAX_MB` (4000) | >= 70% | >= 85% (ingestion pauses at 100%) | `sports.worker.data_mb()` (2b); `service.py`'s `store_mb` still measures canonical.db only |
| Days to cap (trailing 24 h write rate, freed pages counted) | < 14 | < 7 | `python -m sports.storage_report` |
| Revisions per hour per (kind, platform) vs trailing 7-day median | > 3x | - | a new churn source |
| Archive: last pass age / `verify` result | pass > 12 h old | any bad file | `python -m sports.archive stats` / `verify` |
| Worker child: RSS vs its cap, `MemoryError` in job errors | RSS > 80% of the cap | any `MemoryError` | ops `jobs` |
| Polymarket read `truncated` (page cap reached) | any | - | ingest result `polymarket.truncated` (2b) |

## 8. Staging measurement (replaces the projection in section 3)

With ingestion enabled on staging, on the service shell at +1 h, +24 h and +48 h:

    python -m sports.storage_report --json

Record `data_mb`, `free_mb`, `archive_files_mb`, `written_mb_24h`, `days_to_cap` and `by_kind[*].rows_24h` here, then
decide among the options in section 3 from measured numbers.
