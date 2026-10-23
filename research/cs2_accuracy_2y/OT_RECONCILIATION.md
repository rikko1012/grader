# Overtime-round restore: reconciliation of every count

Checked 2026-10-23 (~14:30 UTC). The check was read-only. It used bounded SELECTs on the production HLTV Postgres (READ ONLY session,
120 s statement_timeout), the 2026-10-15 page archive and the night-scraper snapshots. Nothing was written and nothing was
changed, including the page-status rows. Script: `ot_reconcile/reconcile.py`. Outputs (scratch): `close_otcounts/ot_reconcile.json`,
`close_otcounts/ot_maps.csv` (one row per OT map). Parser used for the re-parse: scraper clone `hltv-scraper-night` at 021025e
(overview parser v2, the version that wrote the rows).

## Definitions used by every count below

| Term | Rule | Source |
|---|---|---|
| map | `match_maps` row with a mapstatsid whose match is in `match_overview`. Forfeited maps have no mapstatsid, so they are never maps. | `match_maps`, `match_overview` |
| OT map | exactly 2 team rows (`matches.sideid = 0`, that mapid) with scores summing to > 24. All 62,590 maps have 2 scored teams. | `matches` |
| date | `match_overview.date` (UTC date) | |
| complete | the number of `map_rounds` rows equals the final score total, rounds run 1..n with no gap, each team's round wins equal its map score, and every row has the map's own matchid | `map_rounds` vs `matches` |
| forfeits / data_issues | **never excluded** in any count here. Two-year window: 64 OT maps belong to forfeit-flagged series and 23 to matches with a `data_issues` row. All of them are counted. | `match_overview.forfeited_maps`, `data_issues` |
| page kind | overview pages only (they hold the round history). The economy pages were not touched. | `mapstats_pages.kind` |

## The reconciliation table

| Count (who, when) | Date range | Eligibility | Source | Value | How it links |
|---|---|---|---|---:|---|
| All-time OT maps | 2024-01-09 .. 2026-10-22 (all stored) | OT map | production | **7,292** | = 1,821 (before 2024-10-22) + 5,455 (inventory window) + 16 (2026-10-22) |
| Inventory window OT maps | 2024-10-22 .. 2026-10-21 | OT map | `hltv_window.sqlite` export, now production | **5,455** | 4 of these fall on 2024-10-22 |
| Codex window OT maps | 2024-10-23 .. 2026-10-22 | OT map | Codex export | **5,467** | = 5,455 - 4 (2024-10-22) + 16 (2026-10-22) |
| Codex "missing" | Codex window | OT map with 0 `map_rounds` rows | Codex export, pre-import | **5,466** | = 5,467 - 1. The 1 map with rows was 241953 (24 regulation rows of 29, partial). Parser v1 stored nothing on any page with an OT box. |
| Inventory "DB has 0 rows" | inventory window | same | export, pre-import | 5,454 of 5,455 | same cause, same exception (241953) |
| Inventory "recovered" | inventory window | archive page or in-DB HTML parses, running score counts up, wins = map score | `rounds_archive.sqlite` `pages.status='ok'` | **5,453** / 5,455; 42,585 OT rounds | 2 rejected (`score_mismatch`), both from in-DB HTML: 241953 (page 12-12, DB 13-16, 2026-10-17) and 242485 (page 15-15, DB 17-19, 2026-10-21). Each page was captured before its map finished. |
| Before import, 06:19 UTC (snapshot `before.json`) | all time | OT map | production | 7,292 = 7,290 none + 2 partial | the 2 partial maps are 176812 and 241953 (24 rows each) |
| 06:43 UTC (`before2.json`) | all time | OT map with rows | production | 1,157 (1,155 complete + the 2 partial) | the 1,155 were written by the cloud scan before the import started (06:43:56) |
| Import (`data_revisions` id 1, 06:59:53 UTC; `commit.log`) | archive pages = maps up to 2026-10-15 + in-DB HTML | no rows yet, page passes the check | archive tars + `mapstats_pages.html_gz` | **4,921 OT maps, 156,473 rows, 38,369 OT rounds** | 4,922 `to_write_overtime` - 1 `stored_meanwhile` (the scan wrote it first, with equal rows) = 4,921 |
| 07:00 UTC (`after.json`): "7,289 of 7,290 complete" | all time | OT map | production | **7,289 of 7,292** | The real denominator is 7,292. 7,290 was the 06:19 "none" count; it leaves out the 2 maps that were partial then. At 07:00: 7,289 complete + 2 partial (176812, 241953) + 1 none (242485). |
| Now (~14:30 UTC) | all time | OT map | production | **7,291 complete / 7,292**; 231,794 rows, 56,786 OT rounds | after 07:00 a scan re-downloaded finished pages for 241953 (07:26:22) and 242485 (07:26:19) and both became complete. Only 176812 is left. |
| Now, inventory window | 2024-10-22 .. 2026-10-21 | OT map | production | **5,455 / 5,455 complete**; 42,602 OT rounds | = inventory 42,585 + 5 (241953, 29-24) + 12 (242485, 36-24) |
| Now, Codex window | 2024-10-23 .. 2026-10-22 | OT map | production | **5,467 / 5,467 complete** | |

### Who wrote the 7,291 complete OT maps

The table has no writer column. The split comes from the snapshots and page states; each row is shown with its arithmetic.

| Writer | Maps | Derivation |
|---|---:|---|
| Archive import (first write) | 4,921 | data_revisions / commit log |
| Cloud scan, by 06:43 | 1,155 | before2.json |
| Cloud scan, 06:43-07:00 | 1,213 | 6,134 new maps between the snapshots - 4,921 import |
| Scans after 07:00 | 2 | 241953 (completed), 242485 |
| **Total** | **7,291** | 4,921 + 1,155 + 1,213 + 2 |

Cloud-side pages: 4,193 overview pages were re-downloaded from HLTV, with `fetched_at` from 06:23:38 to 07:26:22 UTC (4,190 OT + 3
regulation). Another 66 complete OT maps were read from HTML already in the DB (no HLTV request). That gives 2,370 cloud first writes
= 2,304 re-downloads + 66.

The two writers overlap on **1,886 maps**. The import wrote those maps first. A cloud scan then re-downloaded the page and rewrote the
rows (scans DELETE and INSERT). The count is 4,921 - 3,035 maps whose page is still archive-only, which equals 4,190 - 2,304. The
rewritten rows match the import's exactly; see check 4.

How the import's own counts reconcile with the archive (7,205 OT pages in the archive; the 87 OT maps after 2026-10-15 are not in
it): 4,922 to write + 1 check_failed (176812) + 1,168 already equal (the scan was first) + 1,114 newer page in the DB = 7,205.
Database source: 1,838 equal + 2 check_failed (241953, 242485) = 1,840 pages = 726 older bodies + 1,114 cloud downloads present at
load time. The import wrote 0 rows from the database source.

### The excluded maps: 3, not 5

The import's `check_failed` report (`commit_report.csv`) lists exactly **3** maps, and no other map was left out. Every one of the
7,292 OT maps is accounted for above.

| mapstatsid | date | Why | State now |
|---|---|---|---|
| 176812 | 2024-06-02 | The archived page's round history reads 12-12. The match page score is 13-12 (25 rounds), so one round is missing from HLTV's own history. | partial: 24 regulation rows. The regular scan keeps retrying it (status attempts 3, last 12:30 UTC, `RoundsMismatch`). The fault is on HLTV's page. |
| 241953 | 2026-10-17 | the in-DB page was captured mid-match (12-12 against 13-16) | complete since 07:26 UTC (fresh page) |
| 242485 | 2026-10-21 | the in-DB page was captured mid-match (15-15 against 17-19) | complete since 07:26 UTC (fresh page) |

The other two numbers that can look like exclusions are not. `stored_meanwhile` = 1 means a scan wrote that map first with equal
rows, so it was never skipped. The inventory's 2 `score_mismatch` maps are 241953 and 242485, already counted above.

## Verification results (production, bounded SELECTs)

| # | Check | Result |
|---|---|---|
| 1 | stored rounds = final score total for every OT map (plus 1..n, wins per team = map score, one matchid) | **7,291 / 7,292 pass**. The 1 failure is 176812 (24 of 25; HLTV's page lacks the round). Regulation: 55,298 / 55,298 complete. |
| 2 | no duplicate (mapstatsid, round) keys | **0 duplicates** across 1,347,188 rows. The `map_rounds_pkey` UNIQUE (mapstatsid, round) index also enforces it. 0 round rows lack a `match_maps` map. |
| 3 | every imported mapstatsid is in the archive manifest and maps to exactly one archived page | **pass.** The manifest has 61,849 overview entries for 61,849 distinct ids (0 ids with more than one page). All 3,035 still-archive-only imported maps are in it with the same matchid. Every OT map in the manifest (7,205) has a matchid equal to the DB's. The import wrote 0 maps from the database source, so all 4,921 came from manifest pages (md5 checked on read). |
| 4 | no map has rows from both writers that disagree | **pass, independent of who wrote last.** Re-parsing all 7,205 archived OT pages (md5 verified): 7,204 equal the stored rows; the 1 that differs is 176812, where the page disagrees with the score. Re-parsing all 4,256 OT pages held in the DB (cloud downloads + older bodies): 4,256 equal. On the 4,169 maps that have both an archived and a re-downloaded page, both parses equal the stored rows (4,169 / 4,169). Since 07:00, only 241953 changed (the partial rows were replaced by complete ones). 0 regulation maps changed since 06:19 (55,283 compared by md5). |
| 5 | OT economy | `map_round_economy` has **0 rows with round > 24**. 7,291 of the 7,292 OT maps have regulation economy rows. OT economy is **not present in the inspected sources (HLTV economy pages in our archive and DB)**. Whether it exists anywhere else was not examined. |

## Page-status noise from the cloud re-download (reported, not changed)

`mapstats_page_status` now holds 486 rows (480 overview + 6 economy). **474 overview rows** come from the cloud session: `updated_at`
from 06:38:55 to 07:17:35 UTC, attempts 1 each. They are browser and worker errors: 283 greenlet "Cannot switch to a different
thread", 87 "Maximum page limit (1) reached", and 104 other `BrowserContext.new_page` / `Route.continue_` /
`Page.set_extra_http_headers` errors. For all 474, the overview page is now at parser v2, the map has its rounds, and the retry
wait has passed. They are stale failure records for pages that later succeeded (this checker's interpretation). The other 6
overview rows are older: 3 UniqueViolation and 2 deadlocks from Oct 11-13, plus 176812's RoundsMismatch. The 6 economy rows are
"N rounds on the economy page, N+1 on the match page" (5) and a UniqueViolation from Oct 13 (1). No rows were deleted or edited.
