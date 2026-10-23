# CS2 accuracy (2-year window) - phase 1: data coverage

Built 2026-10-23 UTC. Window: match dates **2024-10-22 .. 2026-10-21** (HLTV `match_overview.date`, a UTC date). The research
cutoff is 2026-10-22 00:00 UTC. Everything below is read-only: SELECT-only bounded queries on the production HLTV Postgres,
bounded or paged admin JSON endpoints, and local files. Nothing in production was written. No website was scraped.

Codex already did a two-year HLTV inventory (`scratchpad/codex_2y/`: REPORT.txt, inventory.json, audit.json, map_audit.csv).
This document does not redo it. It (a) cross-checks Codex's headline counts, (b) covers what Codex did not, and (c) finds why
overtime rounds are missing and recovers them from archived pages already on disk.

- Full tables: `inventory/coverage_tables.md`
- Headline numbers: `inventory/coverage_numbers.json`
- Every dataset (path, source, query, rows, sha256): `inventory/MANIFEST.json`

## 0. Cross-check of Codex's inventory

Method: my own month-paged export of the production DB (`inventory/export_hltv.py` -> `hltv_window.sqlite`), counted on
Codex's window (2024-10-23..2026-10-22).

| Codex headline | Codex | Mine | Verdict |
|---|---:|---:|---|
| matches | 20,879 | 20,879 | agree |
| maps | 47,044 | 47,044 | agree |
| stored round outcomes (`map_rounds`) | 837,642 | 837,642 | agree |
| team-round economy rows | 1,937,484 | 1,937,484 | agree |
| map-level player rows (`map_player_stats`, total side) | 470,501 | 470,501 | agree (`player_stats` has 470,511: one map has a scoreboard but no map-stats detail) |
| maps without round outcomes, all overtime | 5,466 | 5,466 of 5,467 OT maps | agree |
| maps in forfeit-flagged series | 429 | 429 | agree |
| round sequences reconciling with score | 41,212 | 41,577 | **definitional**: 41,577 = 47,044 - 5,466 OT - 1 mismatch. Codex's 41,212 also drops its 429 forfeit-series exclusions (counts overlap) |
| P1 decided props / matches | 2,922 / 170 | 2,922 / 170 | agree |
| f663af8917 P1 props / matches | 230 / 15 | 230 / 15 | agree |
| "only 726 raw mapstats HTML bodies remain; OT 220400 has neither body" | true for the **DB** | 726 bodies per kind in the DB, **but all 123,693 pages are in the verified archive on disk**; 220400 parses OK (28 rounds, 4 OT) | **Codex missed the archive** (see section 3) |

Codex's window starts one day later than ours. Ours (2024-10-22..2026-10-21): 20,848 matches / 46,970 maps / 836,400 DB
rounds / 1,934,424 economy rows / 469,761 player-map rows.

## 1. Matches and complete series

| Item | Detail |
|---|---|
| Source | HLTV Postgres `match_overview`, `matches` (mapid 0 = series row), `match_maps`, `scrape_queue` (format, found_at) |
| Range in DB | 2024-01-09 .. 2026-10-22, all `source='hltv'`, parser_version 7 |
| In window | **20,848 matches; 20,787 complete series** |
| Complete series | 2 series rows, played maps = series score - forfeited maps (BO1: HLTV stores the map round score, e.g. 13-7, as the series row), every map has a 5+5 scoreboard and 10 headshot rows |
| Excluded | 57 with a map lacking a full 5+5 scoreboard (the scraper flags these: 60 window matches carry `data_issues` code `player_count`); 4 where played maps != series score; 270 carry a forfeit flag (kept, flagged; 266 have 1 forfeited map, 9 have 2 across the whole DB) |
| Duplicates | none by key. 45 same-day / same-team-pair / same-score pairs are all distinct matches (different maps, mostly ECL BO1 double-headers). Linking by team + date alone is unsafe, so always check maps and opponent. |
| Formats | BO1 2,269, BO3 18,340, BO5 235, BO2/other 4 |
| Outside window | 2024-10-01..21 and 2026-10-22+ rows are in the local export too (the latter are the forward set: do not use for development) |
| Scrape backlog | 28 listed matches `waiting` (no player statistics published yet); never stored |
| Trust | High for what is stored (Codex: kills/team ids agree exactly with the legacy table; Oct 12 audit: 95.8% kills exact vs BO3.gg). Not proof of worldwide coverage. |

**Ingestion era.** Every stored row was re-ingested in October 2026, so `raw_pages.fetched_at` and `scrape_queue.found_at`
are all 2026-10-11 or later. There is no record of what was in the DB at an earlier forecast time.

| Cohort | Matches in window |
|---|---:|
| Oct 11-12 rebuild under the old rule (both teams in the 2026-10-11 VRS top-200) | 7,286 |
| Oct 13 backfill, all tiers (went live unapproved; perf input pinned to N0, see memory note hltv-coverage-gap) | 13,224 |
| Oct 14+ regular scan, all tiers | 338 |

**Tier.** No historical tier exists in the DB: `team_rankings` starts 2026-10-11 (13 daily snapshots), and HLTV stars exist only
for upcoming matches. The proxy below is the worse team's VRS rank in the 2026-10-11 snapshot. It uses later information, so it
is for inventory only, never a feature.

| Quarter | both top-30 | worse 31-100 | worse 101-200 | a team >200 / unranked | all | LAN |
|---|---:|---:|---:|---:|---:|---:|
| 2024Q4 (from Oct 22) | 72 | 140 | 42 | 993 | 1,247 | 452 |
| 2025Q1 | 110 | 328 | 155 | 1,990 | 2,583 | 322 |
| 2025Q2 | 116 | 306 | 135 | 1,557 | 2,114 | 488 |
| 2025Q3 | 115 | 339 | 215 | 1,903 | 2,572 | 583 |
| 2025Q4 | 142 | 320 | 257 | 1,793 | 2,512 | 865 |
| 2026Q1 | 161 | 490 | 492 | 2,190 | 3,333 | 1,203 |
| 2026Q2 | 172 | 357 | 519 | 1,591 | 2,639 | 963 |
| 2026Q3 | 173 | 616 | 972 | 1,200 | 2,961 | 836 |
| 2026Q4 (to Oct 21) | 71 | 206 | 325 | 285 | 887 | 449 |

Monthly matches, complete series, formats, LAN and cohort are in coverage_tables.md, table 1.

**Before ~2025-07 (the top-200 era).** In 2024-10..2025-06, 1,404 of 5,944 window matches (24%) come from the old top-200 rule.
The other 76% exist only because of the Oct 13 all-tier backfill. Ranks are today's ranks, so older months under-count teams
that have since dropped out, and the old rule thins history for teams that were ranked then but are not now. External
completeness was last measured on 2026-10-12, before the backfill: 48% of board players' BO3.gg maps were present
(HLTV_COVERAGE.md). It has not been re-measured since. BO3.gg tier labels are cached only for that audit's 9,416 matches
(`scratchpad/coverage/cov1.pkl` TT: tournament tier / tier_rank).

## 2. Individual maps

| Item | Detail |
|---|---|
| Source | `match_maps` (veto order, mapstatsid, start_ct, picked_by), `matches` (per-map, per-side scores), `map_vetoes` |
| In window | **46,970 maps; 46,909 with a full 5+5 scoreboard and 10 HS rows; 5,455 overtime (>24 rounds)** |
| Fields | map, number, mapstatsid 100%; start_ct 100%; picked_by 79% (NULL = decider, by design); per-side CT/T round scores present |
| Gaps | 61 maps without a full scoreboard (49 of them in the >200 tier) |
| Trust | High |

## 3. Round-by-round outcomes (real per-round records)

| Item | Detail |
|---|---|
| Source A | production `map_rounds` (round, half, winnerid, winner_side ct/t, win_type elimination/bomb/defuse/time), from the map-stats overview page round history |
| Source B (new) | `rounds_archive.sqlite`, built by `inventory/rounds_from_archive.py` from the **archived overview pages on disk** (`C:\Users\tikic\cs2_scout\exports\hltv_mapstats_pages_2026-10-15`, 123,693 verified pages, second copy on D:), plus the 726 newer bodies still in the DB |
| DB coverage | 41,515 / 46,970 window maps complete (88.4%); **0 rows for every overtime map** (5,454 of 5,455) |
| Combined coverage | **46,968 / 46,970 maps (99.996%) with a complete, validated round sequence; 1,009,833 rounds incl. 42,585 OT rounds**. Overtime maps recovered: 5,453 / 5,455 |
| Validation | Every map: running-score icon titles count up exactly; per-team wins equal the HLTV map score; on all 42,623 maps where the scraper stored rounds, my rows are **identical** (0 conflicts). This proves the parser before trusting its OT rows. |
| Rejected | 2 OT maps where the page disagrees with the stored score (page 15-15 vs DB 17-19; page 12-12 vs DB 13-16). Their rounds are kept only in `rounds_rejected`. |
| Absent at source | Overtime **economy** (HLTV's economy page has only the 2 regulation tables: 48 cells on all 20 OT pages checked, no overtime element). Per-round kills, damage, player alive counts, plant site, clutch identity: not on the page. |
| Trust | High (cross-validated three ways). Ingestion times are not original-forecast-time availability. |

### Why overtime rounds are missing: the exact cause

`hltv_scraper/parsers/mapstats.py::_rounds`, lines 101-103, in both the deployed 593ffeb and current default-branch head d67ccbd:

```python
rows = soup.select(".round-history-team-row")
if len(rows) != 2:
    return []
```

An overtime page has two round-history boxes: `div.round-history-con` for regulation, and
`div.round-history-con.round-history-overtime` with 3-round halves. That is 4 team rows, so the function returns no rounds at all
and `save_mapstats_overview` silently stores none. Nothing is logged as an error, so `mapstats_page_status` stays clean.

Economy is a different case: `parsers/economy.py` reads every `table.equipment-categories`, but HLTV publishes no overtime
economy table. Economy stopping at round 24 is a source limit, not a bug.

### Repair / backfill plan (proposed, not done; scraper repo + owner approval)

1. **Parser fix.** Iterate every `.round-history-con` box in page order. Number rounds and halves continuously (OT halves = 3,
   4, ...). Validate the running-score `title` and the final score against `matches`. Raise (do not silently return []) on 4+
   rows that cannot be paired. Bump `MAPSTATS_VERSION` 1 -> 2. `rounds_from_archive.py::parse` is a tested reference
   implementation: identical on 42,623 regulation maps, 5,598 OT maps recovered.
2. **Backfill from the archive, not HLTV.** Most `html_gz` is NULL in the DB, so a version bump alone cannot reparse it. Use the
   archive's `restore_mapstats.py` (fills NULL bodies; dry-run default; this writes ~7 GB back, the reverse of the Oct 16 storage
   fix), or better a reparse command that reads the local archive tars and writes only `map_rounds`. Zero HLTV requests.
3. **Treat it as a dataset revision.** It changes no grade input that cm1/cm2/perf read today (they use final scores), but any
   round-based feature must record the revision.
4. **Until then,** research uses `rounds_archive.sqlite`. New OT maps after 2026-10-23 keep their HTML in the DB (~20 MB/day), so
   the same script recovers them.

## 4. Player stats per map

| Field | Source | Window availability |
|---|---|---|
| kills, deaths, ADR, KAST, rating, round swing, eco-adjusted k/d/ADR/KAST | `player_stats` (total / CT / T) | 469,771 player-maps, 100% non-null |
| headshots | `map_headshots`, `map_player_stats.hs` | 469,761 (10 rows on 46,909 maps) |
| assists, flash assists, traded deaths, opening kills / deaths, multi-kill rounds, clutches won, eco-adjusted HS / traded deaths / opening k/d | `map_player_stats` (total / CT / T) | 469,761; KAST / ADR missing on 2-6 rows |
| side split CT/T | both tables | 46,909 maps |
| AWP share / weapon kills, roles | `player_weapon_kills` (one rolling 90-day window per player), `player_roles` (Liquipedia, current) | current snapshot only, no history |
| BO3.gg extras (hits, shots, damage, trade kills, weapons_value, multikills by size) | BO3.gg `players_stats` per game (bo3.py `_ROW`) | not stored in our DB. Production cache only (local `.cache/bo3/games` is empty); ~14.5k scoreboards cached from the Oct 12 audit (`scratchpad/coverage/cov3.pkl`) |

**Data-quality flag.** `map_headshots` holds 113,719 window rows under placeholder mapstatsids 1..11,385, which are not played
maps. The `player_series` view and the grader join on the real mapstatsid, so these are ignored there. Any consumer that sums
`map_headshots` by matchid would double count.

## 5. Round-level player events, economy, equipment, side

| Item | Status |
|---|---|
| Team economy per round (start equipment value, buy class pistol/eco/half_buy/full_buy, won) | **present, regulation only**: `map_round_economy`, 46,966 window maps = 2 x min(rounds, 24). OT absent at source. |
| Side per round | present (`winner_side`; economy `side`; `match_maps.start_ct`) |
| Win type | present (elimination / bomb / defuse / time) |
| Round-level player events (kill feed, who killed whom, opening duel per round, damage per round, utility, trades by round, plant site, players alive) | **absent.** No demos or round-level player feed in any store; HLTV map-stats pages do not carry them. |
| Per-player equipment per round | **absent** |

## 6. Historical real prop lines with observation times

| Source | Content | First / last | Count | Observation time? |
|---|---|---|---|---|
| **hltv-scraper `props-data` branch `history.csv`** (`git show origin/props-data`, commit c00584f; copied to `data/props_data/`) | every UD / PP line when first seen and on each move | Underdog seen 2026-10-02 19:13 .. 10-23 04:21; PrizePicks 2026-10-10 21:12 .. 10-23 04:21 UTC | 11,104 rows; 10,330 line ids (UD 7,784 / PP 2,546); standard 9,593 line ids (Maps 1-2 8,655, Map 1 822, Maps 1-3 107, Map 2 9) | **yes, `seen_at` UTC** (scrape time). `start_time` is **New York local time** (exactly +4 h vs prop_grades' UTC start on 7,555 joined lines; a few dozen differ by other offsets, i.e. start-time edits) |
| HLTV DB `prop_grades` | closing line (last seen before start) graded vs HLTV | UD starts 10-02 22:00 .. ; PP 10-11 01:00 .. 10-23 | 7,594 (graded 6,913; void 209; no_match 169; pending 303) | no capture time (only updated_at) |
| HLTV DB `prop_lines` | the board now (open/prev line, moves, first_seen, other book) | current | 280 | first_seen / moved_at |
| production `ud_moves` (Render, `/admin/ud_moves`) | each new UD line and line/payout change | 2026-10-11 04:52 .. 10-23 04:31 UTC | 7,654 rows | yes (`at`) |
| Railway `ud_moves` (`scratchpad/ud_moves.json`) | same, Railway era | 2026-10-04 22:31 .. 10-08 22:54 UTC | 23,808 rows | yes |
| forecasts `quote` (board quote the forecast used: prices, payouts, anchors with source_updated_at / board_at) | per forecast | from 2026-10-15 02:44 UTC (a310d76b55) | 46,186 of 80,675 rows | yes |
| PrizePicks history JSONL on Render (`/admin/pp_history`) | openings, moves, removals | not pulled: the endpoint reads the whole file per call and returns only the last N | unknown | yes |
| LoL `lines_archive.jsonl` | LoL only, not CS2 | - | - | - |

**Gaps:**
- No offered line exists before 2026-10-02.
- No PrizePicks line before 2026-10-10.
- Observation times are scrape times (hourly props workflow plus worker), not provider timestamps.
- Goblin, demon and alt lines are mixed into history.csv (737 rows); filter `odds_type='standard'`.

## 7. Forecast logs and settlements, by model version tag

| Source | Range | Count |
|---|---|---|
| Render `forecasts` (`forecasts_lite.json`, paged, cutoff id 80,675) | 2026-10-11 04:56:45 .. 2026-10-23 04:44:56 UTC | 80,675 rows (11,347 first, 69,328 update); 100% have saved grader inputs (not pulled; `inputs=1` pages exist) |
| Railway forecast log (`scratchpad/forecasts_now2.json`) | 2026-10-07 21:11 .. 10-08 23:24 | 5,102 |
| Railway picks (pick row = first forecast) (`picks_after_recheck.json`) | picks 2026-09-28 .. 10-08 23:56 | 9,521 (7,643 with no version tag, before 2026-10-07) |
| **Lost** | 2026-10-10 16:40 .. 2026-10-11 04:56 UTC (Railway export to Render start) | forecasts and settlements not recoverable |
| Settlements: Render picks export (`picks_now.json`, to 2026-10-20 00:40) | | 8,305 picks: 7,264 settled / 110 push / 272 void / 659 open |
| Settlements: P1 book-line units (`p1_units.json`, `/admin/learning/p1_units.json`, at 2026-10-23 04:29:55) | starts 10-11 11:30 .. 10-22 22:00 | **2,922 units / 170 matches** (UD 1,817 / PP 1,105; kills 1,530 / HS 1,392; Maps 1-2 2,605 / Map 1 277 / Maps 1-3 40) |
| fh-1 shadow records (`fh1_records.json`) | 2026-10-15 18:35 .. 10-23 04:31 | 11,319 (cm-1 / cm-2 values inside each record) |

Rows, picks, first/last log time, quote and inputs by tag are in table 7a. P1 units by tag are in 7c (e.g. 794a775f51 737/44,
9cfcbd217a 609/34, 6f6e2d39d7 302/17, f663af8917 230/15). Picks status by tag is in 7e/7f.

The current full picks table was **not** pulled: `/admin/picks.json` is an unbounded export. A complete settlement refresh after
2026-10-20 needs a bounded / paged picks endpoint, or settlement recomputed from the HLTV DB. Settled values (`actual`) are
HLTV-derived and can be re-checked from section 4.

## 8. Bookmaker / market snapshots

| Source | Store | Range / count | Notes |
|---|---|---|---|
| Thunderpick (production) | Render `/data/.cache/thunderpick_history` segments + gateway DB | oldest segment 2026-10-13 18:10 UTC; 88 MB; policy tp-first-2 active since 10-13 18:11:12; csgo endpoint 3,063 requests / 2,483 non-empty | Match Winner only enabled; feed has no quote time (time = our retrieval); timezone inferred, not provider-confirmed. Raw event/market rows are not exportable through a bounded endpoint (status only). |
| Thunderpick (scraper copy) | HLTV DB `thunderpick_snapshots` | 2026-10-16 06:19 .. 10-23 04:21 UTC: 114 ok + 15 rate-limited reads, 7,031 events | full gzip bodies (round handicap, total rounds per map, etc.) |
| Polymarket + Kalshi (production) | Render `ml_snapshots.db` | 535,376 quote rows; ml_forecasts 237,887 rows / 724 matches (last 2026-10-23 04:44) | **Quote snapshots are PAUSED: `SnapshotCapReached` 626 MB > 500 MB cap.** No new quote rows are being written now (live ops issue, flagged to the owner). No bounded export endpoint. |
| Polymarket + Kalshi (history) | `scratchpad/ml_research/dataset.pkl` (+ 921 MB raw cache) | scheduled starts 2025-10-15 .. 2026-10-11: 3,468 Polymarket + 2,058 Kalshi rows, 3,543 matches | price at the cutoff, plus 24 h / 6 h moves; built against a pre-backfill HLTV snapshot (368 teams) |
| Market input recorded per forecast | `forecasts.odds_source` | thunderpick 38,668, polymarket 34,117, none 7,890 | |
| OddsPapi | none | - | **absent**: in-memory 10-minute cache only, nothing persisted |

## Unavailable fields (explicit)

- Per-round player events (kills, deaths, damage, utility, trades, alive counts, plant site): not in any store or archive.
- Overtime round economy: not published by HLTV.
- Historical tier or ranking before 2026-10-11, and historical HLTV stars.
- Provider-side line timestamps; any offered prop line before 2026-10-02 (PP before 10-10).
- Forecasts and settlements from 2026-10-10 16:40 to 10-11 04:56 UTC.
- What the HLTV DB contained at any past forecast time: all rows were re-ingested Oct 11-13.
- OddsPapi history.
- Polymarket / Kalshi quotes after the snapshot cap was reached.

## Data periods (proposed for REGISTER)

- **Development (already inspected):** every match dated up to 2026-10-21.
  - All real-line data (2026-10-02 .. 10-21) has been looked at by earlier studies; treat it as development, not holdout.
- **Forward confirmation:** matches starting at or after 2026-10-22 00:00 UTC, untouched.
  - Rows dated 2026-10-22+ present in the local exports must not enter development fits.
