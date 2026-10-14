# HLTV coverage audit and scraper repair requirements (2026-10-12)

For the separate hltv-scraper service (github.com/rikko1012/hltv-scraper, audited at a1a377b). Read-only audit; nothing in the scraper or the database was changed. Backfilled history changes model inputs without a code change: it must arrive as a recorded dataset revision and be compared before it is used for grading (see "Activation" at the end).

## Findings

```
HLTV DB COVERAGE AUDIT - 2026-10-12 (read-only; no repo/DB changes)
Work dir: scratchpad/coverage/  (cov1.py collect, cov2.py match, cov3.py scoreboards, cov4.py coverage + (a)/(b), cov5.py consistency + selection)
Full tables: coverage_tables.txt, consistency_selection.txt. Scraper code: coverage/scraper (public repo, main = a1a377b).

DATA: 244 board players (Oct 3-12 board units), each player's BO3.gg match list (5 pages), window = 420 d before his latest board day.
Deduplicated: 9,416 BO3.gg matches, 19,018 played maps (>= 13 rounds). Match to HLTV DB: same day +-1, exact team-name variants
(hltv.team_name_variants), or one name + identical map scores (33 matches). 14,524 BO3.gg scoreboards (all but 15 from cache).

=== 1. ROOT CAUSE + (a)/(b) SPLIT ===
Root cause (code): hltv_scraper/pipeline.py relevant(): a listed result is queued only if BOTH team names are in the CURRENT
VRS top-N (watch/update/backfill default --top 200; names lowercased, exact). The Oct 11 rebuild backfill used the Oct 11
ranking for all history. DB check: 98-100% of stored matches in every quarter since 2024 have both teams in today's top-200.
Consequences: (i) any match vs a team ranked >200 / unranked / renamed / disbanded is never queued; (ii) a team that enters
the top-200 later gets no history (watch only reads new results); (iii) collect_results() stops silently when an older
results page fails ("keeping the matches found so far"), truncating backfills; (iv) the listing is not persisted - nothing
records matches that were listed but skipped (scrape_queue holds only queued ids: 8,597, all 'done'; upcoming_matches is a
replaced snapshot).
HLTV direct check: hltv.org returns a Cloudflare challenge (HTTP 403) to a plain fetch; not bypassed, so no live sample.
Substitute evidence: the scraper's own Sep 30 HLTV schedule snapshot (data branch upcoming.csv, 207 rows): of 58 matches with
named teams, 35 are in the DB and 23 are missing; all 23 missing have >= 1 team outside the top-200 (19 one, 4 neither);
0 missing with both teams top-200. These 23 were listed by HLTV itself -> type (b). Today's schedule (184 rows): 10 named
matches already have a non-top-200 team (Elisa BreakOut, CCT, Stake Pulse Beat, ESL CL Oceania/APAC) -> will be skipped.

Coverage by VRS band of the worse-ranked team: 1-30 99.8% | 31-100 97.8% | 101-200 90.8% | 201+ 6.5% | unranked 0.1%.

Missing maps (9,780 maps in 5,396 BO3.gg matches with no HLTV match; +68 maps missing inside matched matches):
  F1 a team outside top-200, SAME BO3 tournament has other matches in our DB (HLTV covers it) ... 7,849 maps 80.3%  -> (b) confirmed
  B1 both teams top-200 now, same tournament in DB (crawl gap/name/old rank) .................... 219   2.2%  -> (b) confirmed
  F2 a team outside top-200, event series seen on HLTV (DB / events / schedule) .................. 948   9.7%  -> (b) probable (104 ESEA)
  B2 both teams top-200 now, tournament not in DB (mostly ESEA Advanced S55 regular season, Exort,
     EPL regular season) .............................................................................. 178   1.8%  -> uncertain
  U  a team outside top-200, event never seen on HLTV in any of our sources (Gamers Club Liga,
     Dust2.dk Ligaen, Elisa Suomi, CCT Contenders/Open Qualifiers, national qualifiers) .............. 586   6.0%  -> uncertain
ESTIMATE: (b) on HLTV but missing from our DB = 82.5% confirmed lower bound, ~91% central (range 85-97%).
          (a) not covered by HLTV = at most ~9% (U + B2 + ESEA part of F2 = 868 maps), realistically 3-9%.
Caveat: HLTV may list a low-tier match without a stats page (validate.py waits 'no_player_stats'); such matches would be
(b) in listing but unusable - the repair must count them separately.
Note: DB has some ESEA Advanced (2-11 matches per season, mostly season end), 0 ESEA Advanced S55 Europe; United21 54,
BetBoom Storm 27, CCT SA 114, ESL CL SA 156 / APAC 113, Dust2.* 19, Gamers Club 0 - all are HLTV-covered series that the
filter thins out. DETONATE is now in the DB (VRS 196, entered top-200 recently) but has 48.7% of its maps (no back history).

=== 2. COVERAGE (HLTV-DB maps / BO3.gg maps, deduplicated maps) ===
Overall 9,170 / 19,018 = 48.2% (player-weighted: 33,475 / 56,776 = 59.0% of board players' own maps incl. his player_stats row;
the audit5 75% figure was unit-weighted and counted one-name matches).
Month: 2025-08 41% | 09 32% | 10 32% | 11 39% | 12 35% | 2026-01 42% | 02 42% | 03 45% | 04 42% | 05 56% | 06 56% | 07 64% | 08 70% | 09 85% | 10 85%
  (recent months better because today's top-200 names match recent rosters; older history decays)
BO3.gg tier_rank: 1: 90.8% (2,177) | 2: 84.6% (887) | 3: 47.0% (9,136) | 4: 31.6% (6,818)
Environment: LAN 67.8% (6,036) | online 39.1% (12,982).  Tier x env: t1 LAN 92%, t1 online 78%, t3 online 45%, t4 online 26%.
Format: BO1 36.7% (1,114) | BO3 48.4% (17,551) | BO5 77.3% (353)
Worst event series (>= 40 maps): Gamers Club Liga 0%, Elisa Suomi 0%, Exort 4%, United21 4%, ESEA Advanced EU 7%, ESEA Elite 10%,
  Dust2.dk Ligaen 10%, CCT South American 11%, European Pro Play 14%, BetBoom Storm 24%, CCT European Play 24%, CCT SA 28%,
  ESL Challenger SA 33%, CCT European 35%, ESL CL North America 35%, NODWIN Clutch Play 37%, ESL CL Asia 43%, ESL CL Europe 47%.
Board players: median 65.3%; 82 of 244 below 50%, 37 below 25%, 6 at zero (MaxOff, sATOR1, skrr, Purely, Daniel, Max).
30 worst-covered board teams (own matches; VRS; BO3 maps; HLTV maps; cov):
  STATE 128 416/96 23.1% | BORRACHEIROS 188 71/23 32.4% | MASONIC 173 374/130 34.8% | Fire Flux 124 201/74 36.8% |
  aimclub 79 231/89 38.5% | ALKA 175 168/66 39.3% | Procyon 151 228/95 41.7% | Isurus 183 188/82 43.6% | Yawara 150 242/108 44.6% |
  DETONATE 196 39/19 48.7% | UNiTY 72 14/7 50.0% | Just Swing 168 123/64 52.0% | Chicken Coop 166 78/41 52.6% |
  Bounty Hunters 96 358/189 52.8% | Spirit Academy 119 86/47 54.7% | Kaleido 167 115/63 54.8% | PCIFIC 148 141/78 55.3% |
  ShindeN 82 301/170 56.5% | Lilmix 187 106/60 56.6% | Galorys 87 298/176 59.1% | Enjoy 157 115/68 59.1% |
  Nuclear TigeRES 46 409/244 59.7% | 9z 14 304/184 60.5% | Overtake Sector 158 48/30 62.5% | Chinggis Warriors 59 189/123 65.1% |
  K27 36 379/248 65.4% | Betclic 56 263/173 65.8% | SINNERS 44 361/239 66.2% | Low Cortisol BR 189 9/6 66.7% | Sangal 39 139/94 67.6%
  (39 of 72 board teams below 75%; in every one the missing maps are overwhelmingly vs opponents ranked >200/unranked.)

=== 3. SOURCE CONSISTENCY (9,170 maps in both; 8,115 with both scoreboards; 80,125 player-maps name-matched, 1,039 unmatched) ===
Player kills/map: exact 95.8%, within 1 98.7%, mean BO3-HLTV -0.009, mean |d| 0.137 (t1 96.5% exact, t2 96.0, t3 95.6, t4 95.2)
Player HS/map:    exact 97.5%, within 1 99.0%, mean -0.004 (t1 98.0%, t4 97.1%)
Team kills/map:   exact 85.2%, within 1 97.0%, mean -0.053 (off-by-one is the typical difference)
Rounds per map identical 99.2% (70 diffs: BO3.gg shows unfinished/odd scores e.g. 10-10, 8-9 - BO3.gg-side errors)
Map order identical 99.5% (44 diffs). Final score pair differs on 70 maps.
-> No systematic difference by tier; the sources agree. Inconsistency does NOT explain why BO3.gg back-fill hurt.

=== 4. SELECTION EFFECTS (board players' own maps: present 33,475 vs missing 23,301) ===
                         present   missing
online share              45.5%     75.8%
tier 1-2 share            40.9%      6.1%
opponent VRS <= 200       66.1%     15.7%
opp VRS rank (ranked)     62.5     173.9
map win rate              54.2%     58.4%
rounds per map            21.75     21.42   (overtime 12.6% vs 11.3%)
BO1 share                  4.5%      6.1%
kills per round           0.676     0.698   (HS share equal 51.5%)
Within-player (202 players with >= 10 maps each side): missing-map KPR - present-map KPR = +0.051 (median +0.049, positive for
85% of players), ~ +2.3 kills per 2-map series; within tier 3-4 only: +0.040 (79% positive).
KPR by opponent: VRS 1-100 0.666 | 101-200 0.691 | 201+ 0.710 | unranked 0.697.
-> Missing history is NOT missing at random: it is the player's matches vs weak/unranked online opponents, where he kills more
and wins more. The current HLTV sample is biased toward strong opponents (it under-states KPR for lines vs weak opponents),
and naively appending BO3.gg maps without opponent adjustment over-states it for lines vs strong opponents - consistent with
the round5 result. Incomplete history is a selected sample, not a random subsample; the fix is complete data + opponent
strength handled in the model, not accepting the gap.

=== 5. SCRAPER REPAIR REQUIREMENTS === (see final report)
```

## Requirements

```
SCRAPER REPAIR REQUIREMENTS (hltv-scraper, for CS2 Scout) - 2026-10-12

R1 CRAWL SCOPE: queue every non-forfeit listed result, all tiers
 - pipeline.relevant(): drop the "both teams in top-N" rule. Run watch / update / backfill with --top 0 (the default in
   cli.py for update/watch is 200 and backfill.yml input top=200; change all three). If load forces a limit, the fallback
   rule is EITHER team in a tracked set = VRS rank <= 400 OR any board team OR any team on the HLTV schedule in the last 30 d,
   matched by HLTV team id, not lowercased name. The match always counts when either side qualifies.
 - Scale: the scraper's own backfill note says a 12-month listing is ~130 results pages (~13,000 matches); 420 d is about
   150 pages / ~15,000 matches; the DB has 5,586 since 2025-08-18, so expect ~9-10k new matches, ~5 pages each
   (match + 2.3 maps x overview/economy), ~50k fetches. Spread over 8 machines like the existing backfill.
 - Window: rolling 420 d + 30 d margin (today: from 2025-07-14). Keep the existing 2024-2025 rows.
 - collect_results(): an older results page that fails must not end the run silently. Store the offset it reached
   (scraper_state 'listing_cursor:<start>:<end>'), mark the run incomplete and resume from that offset next run.
   Stop paging during catch-up only when a page has no listed match id missing from results_listing (R4), not when it has
   "no new relevant" one.

R2 PER-TEAM AND PER-EVENT BACKFILL
 - team_backfill(teamid PK, start_date, end_date, requested_at, reason ['board','new_in_tracked','opponent','manual'],
   last_run_at, pages_read, listed, queued, stored, status). Read hltv.org/results?team=<id>&startDate=<today-450>
   (all offsets) for every team on the board (teams and opponents in prop_lines and upcoming_matches for the next 48 h)
   and for every team the first time it appears in a listing or ranking. Run nightly for board teams; run within 1 h of a
   new team appearing on the board.
 - Transfers: for each board player, also read his former teams' results for the window, using HLTV's team history
   (hltv.org/results?player=<id> if that filter works).
 - Events: for every event in hltv.org/events/archive overlapping the window, read hltv.org/results?event=<id>.
   Store events(eventid, name, start_date, end_date, lan, location, prize, tier, team_count). Re-read events that ended in
   the last 14 d every night; reconcile all events in the window every week.

R3 PERSIST THE LISTING (makes the (a)/(b) split auditable)
 - results_listing(matchid PK, url, team1_name, team2_name, team1_id, team2_id, score1, score2, format, stars, eventid,
   event_name, match_date, source ['results','team','event','schedule','failsafe'], first_listed_at, last_listed_at,
   skipped_reason NULL|'forfeit'|...). Insert every listed row, whether or not it is queued. Never delete rows.
 - upcoming_history(matchid, team1, team2, event, format, stars, time, first_seen_at, last_seen_at). Append-only. It keeps
   the replaced upcoming_matches snapshot so matches listed on HLTV can be audited later.
 - Final match states in scrape_queue: 'done' | 'no_stats_on_hltv' (listed, but stats still missing 7 d after the match;
   counts as HLTV-uncovered stats, not as an outage) | 'failed' (keeps retrying, alerts).

R4 INGESTION TIMESTAMPS: never overwrite, survive rebuilds
 - match_ingest(matchid PK, first_listed_at, first_fetched_at, first_parsed_at, first_revision_id, last_fetched_at,
   last_parsed_at, last_revision_id, parser_version). first_* are written once: INSERT ... ON CONFLICT DO UPDATE sets only
   last_*. A BEFORE UPDATE trigger raises if any first_* value changes. Add the same for mapstats_pages: first_fetched_at,
   and keep fetched_at as the latest fetch.
 - raw_pages: today ON CONFLICT sets fetched_at = now() (db.py line ~318). Keep the original in first_fetched_at and add
   refetched_at.
 - Rebuild / restore / reparse / requeue_missing must carry the first_* columns over (pg_dump/restore includes them). A
   rebuild that cannot keep them sets ingest_time_reliable = false and never invents new values.
 - Lost history: every match stored before 2026-10-11 10:29Z has fetched_at/found_at reset (8,578 of 8,597 raw_pages
   fetched 2026-10-11; scrape_queue.found_at min 2026-10-11 10:29). Mark those ingest_time_reliable = false.
   Point-in-time replays must use match_date + an explicit lag for these rows, labelled approximate.

R5 DATASET REVISIONS
 - dataset_revisions(revision_id serial PK, job ['watch','backfill','team_backfill','event_sweep','reparse','restore',
   'correction'], started_at, finished_at, git_commit, parser_version, args jsonb, matches_added, matches_updated,
   maps_added, rows_changed, complete bool, notes).
 - Every stats table (match_overview, matches, match_maps, player_stats, map_player_stats, map_headshots) gets
   created_revision_id and updated_revision_id.
 - stat_corrections(matchid, mapstatsid, playerid, field, old_value, new_value, revision_id, at): written whenever
   requeue_for_correction or a reparse changes a stored number.
 - CS2 Scout logs the revision_id and max(created_at) it graded with on every card/forecast, so a grade can be replayed
   "as of" that revision.

R6 COVERAGE CHECK JOB (`hltv-scraper coverage`)
 - Daily at 06:00 UTC and at the end of every backfill/team_backfill run. Writes coverage_report(run_at, revision_id, scope,
   key, listed, stored, with_player_stats, with_headshots, no_stats_on_hltv, ratio). Scopes: month, event, event tier/stars,
   LAN/online, format, team (every board team), board player.
 - Denominator = results_listing (HLTV's own listing) for matches finished more than 48 h ago.
 - CS2 Scout side, weekly: rerun this BO3.gg cross-check (coverage/cov2+cov4 logic) for board players. Show the result in
   admin diagnostics, list every BO3.gg-only event, and put HLTV-uncovered events in an exceptions file with evidence
   (no HLTV event page / not listed).
 - Alert (scraper-alert issue) when any acceptance threshold below is broken.

R7 ACCEPTANCE TESTS (all must pass before the grader treats the DB as complete; tests in tests/test_coverage.py + a live job)
 a. Listing: the 420-d listing read every results offset (complete = true on the revision). results_listing count >=
    pages x 100 - 100.
 b. Stored / listed for non-forfeit matches finished more than 48 h ago: >= 99% have match_overview + player_stats for every
    played map, >= 98% have map_headshots. no_stats_on_hltv is reported separately and stays <= 2%.
 c. BO3.gg cross-check, board players' 420-d maps, after excepted (HLTV-uncovered) events:
    tier 1 >= 98%, tier 2 >= 97%, tier 3 >= 95%, tier 4 >= 90%, online >= 93%, LAN >= 97%.
    Every board team >= 85%. Every board player with >= 20 BO3.gg maps >= 90%.
    Opponent band "201+/unranked" >= 85% (today 0.1-6.5%).
    Today: 90.8 / 84.6 / 47.0 / 31.6; online 39.1; LAN 67.8.
 d. Named regressions: 420-d history present for DETONATE, BORRACHEIROS, ex-Keyd Stars, Lilmix, STATE, Just Swing, MASONIC,
    Fire Flux, Procyon, Yawara. Matches present for ESEA Advanced S55 Europe (or the event excepted with evidence), United21,
    BetBoom Storm, CCT South America, ESL Challenger League SA / APAC, Dust2.dk Ligaen, Gamers Club Liga (or excepted).
 e. Schedule replay: every named match in upcoming_history that finished more than 48 h ago is stored or in a final state.
    In the Sep 30 snapshot that is 23 of 58 missing today; the target is 0.
 f. Consistency (regression guard, same method as consistency_selection.txt): on maps in both sources, per-player kills
    exact >= 95%, within 1 >= 98.5%, |mean BO3-HLTV| <= 0.05 kills/map; HS exact >= 97%; rounds identical >= 99%;
    map order identical >= 99%. Today: 95.8 / 98.7 / -0.009; HS 97.5; rounds 99.2; order 99.5.
 g. Timestamps: test that an update / reparse / requeue / restore never changes first_* columns, using the trigger and a
    dump-restore round trip in CI.
 h. Revisions: every row written in a run carries that run's revision_id, and dataset_revisions.complete is false
    whenever a listing page failed.

CADENCE SUMMARY: watch every 2 h (existing, --top 0). Team backfill nightly + on new board team. Event sweep nightly for
events ended in the last 14 d, full 420-d reconciliation weekly. Coverage job daily 06:00 UTC + after each backfill.
BO3.gg cross-check weekly (CS2 Scout).
```

## Corrected coverage figures (2026-10-12 second pass, scratchpad/coverage2 - supersedes the 82.5% / ~91% / 85-97% above)

Universe: BO3.gg finished / forfeit-win maps with 13+ rounds of 244 board players, 420 days before each player's last board
day (2025-08-09 .. 2026-10-10).

- **48.2% = 9,170 / 19,018** = distinct maps (deduplicated across players) stored in our HLTV DB / all such maps.
- **59.0% = 33,475 / 56,776** = (board player, map) pairs whose map AND that player's player_stats row are stored / all
  pairs (2.99 board players per map). Higher only by weighting: coverage is 31.4% on maps with one board player, 83.6%
  with six or more.
- Missing: 5,396 matches / 9,848 maps, three mutually exclusive groups:

| group | matches | maps | share of missing maps |
|---|---:|---:|---:|
| (i) demonstrably listed by HLTV (its own "past matches" boxes on 8,743 stored pages: 20,654 listed ids, 11,939 not stored) | 3,166 | 6,867 | 69.7% |
| (ii) found only via BO3.gg, HLTV event known to us, match not seen listed | 1,937 | 2,632 | 26.7% |
| (iii) HLTV availability unknown (event never seen) | 291 | 346 | 3.5% |

  Counter-evidence: 1,003 of (ii) and 132 of (iii) fall inside a stored team's listed span that does not list them
  (control: 3,946 / 3,946 stored matches in such spans are listed) - mostly ESEA Advanced / Elite regular season, Exort,
  United21, EPL, Gamers Club, Dust2.dk.
- Bounds on "HLTV lists it, we lack it": lower 69.7% (direct evidence only); evidence-adjusted 85.1%; upper 96.5% (all but
  never-seen events). "Listed" is not "publishes stats". Our coverage of what HLTV lists: 49.1-57.2% of maps.
- The earlier figures treated "event present in our DB" as confirmation (checked: F1 6,270 / 7,849 listed; B1 3 / 219;
  U 277 / 586). The 23-match schedule sample proves availability for those 23 only.
- **Target denominator** = the eligible HLTV set: matches HLTV lists for the window, not forfeits, played > 48 h ago,
  with published map stats - available only from the scraper's own full listing (result_manifest in the repair below).

## Executable repair (scraper_repair/ in this repo)

`scraper_repair/0001-Coverage-repair-every-listed-match-resumable-listing.patch` (one commit on base a1a377b of
github.com/rikko1012/hltv-scraper, default branch claude/hltv-scraper-recreation-t4oiso) and `scraper_repair/HANDOFF.txt`
(affected code with line numbers, deployment, runbook, acceptance tests A1-A8). The patch removes the both-teams-in-today's-
top-200 rule (pipeline.relevant; optional --top now means EITHER team, by HLTV team id), makes listings resumable and never
silently partial, adds the append-only result_manifest, write-once first_fetched_at, dataset_revisions with per-row
revision ids, and staged revisions in their own schema (hltv_rev_N: seeded from public, filled by the backfill, sealed
immutable, promoted only by an explicit owner-approved command). Its PostgreSQL tests have not run yet (no PostgreSQL in
this session): a green tests.yml on the pushed branch is the first gate. **Do not merge it into the default branch before
promotion** - run everything with `--ref repair/coverage-staged`; merging first puts --top 0 on the live scan and changes
the tables the grader reads before the comparison.

## Staged before / after comparison (before any member grade uses repaired history)

Revisions (both sealed, immutable, replayable): **N0** = `revision create` + `seal` with no backfill (the baseline: the
seed copy of public at that moment); **N** = seed + the backfill, accepted (A1-A8) and sealed. Comparisons read them
read-only through `options=-c search_path=hltv_rev_<n>` and record data_rev = the schema.

Same code version (live 1088faf157), same eligible forecasts: every board unit's primary forecast (EVALUATION.md section 1)
with match start in the fixed comparison window, plus every forward unit after the seal; identical rows in every arm,
exclusions listed with counts.

- **A** current model on N0 (current history).
- **B** current model on N (repaired history) - isolates the effect of coverage.
- **C** N plus a separately frozen and separately tested opponent-strength candidate - only after A vs B is reported;
  never bundled into B and never credited to it.

Report per stat (kills / headshots separately, in their own units), per coverage band (history share added by N), per
board team and for established vs thin players: projection bias and MAE; log-loss, Brier and calibration of the published
P(Over | no push); match-grouped 95% intervals; distinct matches; the number of lines whose side, grade or stake would
change. Activation (promote N, then a versioned release naming the revision) needs the owner's approval on that evidence;
the frozen evaluation populations (lb-001, ledger, recaps) keep their recorded inputs.

## Activation of repaired history in CS2 Scout (grade-changing; not done)

1. The scraper writes the backfill as a new dataset revision (R5); CS2 Scout already records the revision seen by every forecast (forecasts.data_rev, since 2026-10-12).
2. Before any member grade uses the repaired history: replay the live model on a fixed set of recent board lines with the old and the new revision, report per-line changes (projection, chance, grade, stake) and the forward-scored difference; the frozen evaluation populations (lb-001, learning ledger, recaps) keep their recorded inputs and are never silently re-scored.
3. Because the missing matches are not random (online, weaker opponents, higher kills per round), complete history without an opponent-strength adjustment raises projections (round 5 C0). The repaired history and the opponent handling are tested together as a frozen candidate before activation; activation is a versioned release (MODEL_VERSIONS.md) with the dataset revision named.

## Staged before/after replay - results (2026-10-13, read-only)
**The staging did not happen.** The backfill was written straight into the live `public` tables (no `hltv_rev_*`
schemas, no `dataset_revisions` / `match_ingest` / `result_manifest`): 19,590 matches first fetched 2026-10-12
21:27-21:35 and 2026-10-13 00:23-04:24 UTC. Live grades used the repaired history from 04:26:15 UTC (first forecast with
data_rev matches >= 28,000); 367 forecasts between 00:49 and 04:26 were graded on PARTIAL history while it loaded
(data_rev 9,658 ... 27,964). Coverage: 19,574 -> 61,671 maps (42,097 added), 4,489 players with history only from the
backfill; open failures: 28 matches waiting in scrape_queue, 11 map pages with errors (6 economy, 5 overview), 285
player-count warnings. Old matches gained no pages in the backfill, so the old revision N0 = live tables minus those
19,590 matches (exact up to stat corrections, which the database does not record).

**Method.** Same code (current grader + perf-2), same forecasts: every settled line's latest forecast >= 30 min before
start (EVALUATION.md section 1; 1,163 lines, 23 matches, Oct 11-13); same player / opponent HLTV ids as saved; history as
of the forecast time (grading day, matches fetched by then; the 2026-10-11 database seed counts as old history). Only the
history differs. Check: N0 reproduces 382 of 383 saved projections of the same grading versions (|d| <= 0.01).
Scripts: scratchpad replay_hist.py / replay_report.py.

| (95% by match) | kills: A old N0 | kills: B repaired N | B-A | HS: A | HS: B | B-A |
|---|---|---|---|---|---|---|
| lines / matches | 616 / 23 | | | 531 / 23 | | |
| bias (proj - actual) | +0.93 | +1.33 | | +0.58 | +0.85 | |
| MAE | 5.579 | 5.576 | -0.003 (-0.164..+0.174) | 3.994 | 3.975 | -0.019 (-0.107..+0.077) |
| log-loss of P(Over) | | | +0.0013 (-0.0133..+0.0154) | | | -0.0015 (-0.0179..+0.0146) |
| Brier | | | +0.0010 (-0.0062..+0.0078) | | | -0.0010 (-0.0089..+0.0066) |
| side / grade / stake changed | | | 307 lines (stake 205) | | | 216 (stake 126) |

By band (kills): 20-50% of history added -> log-loss +0.020 (+0.002..+0.037), Brier +0.010 (+0.001..+0.018), MAE
+0.17 (worse); >= 50% added -> MAE -0.47 (-1.27..+0.26), not significant; established -> +0.016 (-0.135..+0.170); thin
-> -0.53 (-1.74..+0.30); returning 12 lines / 3 matches (no conclusion). Only 16 lines had no old history at all.
Mechanism: projected kills rise 1.4% on average (3.3% where >= 50% of history was added) - the added maps are mostly
against weaker / online opposition and the rate has no opponent-strength adjustment (round 5 C0, as predicted above).
Calibration: B moves more lines into the 0.6-0.7 Over bin (107 vs 73), which hit 41%.

Market candidate rr-1 evaluated SEPARATELY (same lines, Polymarket prices): on N vs B - kills MAE -0.061 (-0.211..+0.065),
HS MAE -0.122 (-0.205..-0.041), HS log-loss -0.013 (-0.026..-0.001); on N0 vs A - kills -0.116 (-0.262..+0.014), HS -0.115
(-0.266..+0.015). The formula effect is about the same on either history; it is not part of the A/B difference.

**Recommendation.** The repaired history is a coverage gain, not a demonstrated accuracy gain: 23 matches, no MAE /
log-loss / Brier improvement, a larger Over bias, a significantly worse kills probability where 20-50% of history was
added, and half of all lines change side / grade / stake. Do not treat its live use as an approved activation. Revert
live projections to the N0 view (exclude the backfill matches from the perf-2 queries, data revision named) until
arm C - repaired history WITH an opponent-strength adjustment, frozen and tested on the large historical set - passes,
plus forward scoring of both views. Keep the repaired data for coverage, settlement and the shadow logs.

## Controlled rollback of the performance-model input (2026-10-14, release `8f091e14cc`)

**Owner rule.** A dataset that reached live grading before its planned validation and activation gate is pinned back to the
last approved model-input revision as a controlled rollback. That restores the agreed release state; it is NOT a finding that
incomplete history predicts better. A staged revision stays staged; a deliberately approved activation is not reversed on a
23-match comparison.

**What was live.** perf_live's queries had no revision filter: the performance model read whatever was in the live `public`
tables. The backfill (19,590 matches, first fetched 2026-10-12 21:27-21:35 and 2026-10-13 00:23-04:24 UTC) was written there
directly - no `hltv_rev_*` schema, no `dataset_revisions`, no approval (the "Activation" section above was never run) - so
live grades used partial repaired history from 00:49 UTC (367 forecasts) and the full repaired history from 04:26:15 UTC
(first forecast with data_rev matches >= 28,000). Since then the regular scan also takes every tier (the owner's scraper
report: "New matches from every tier are picked up by each scan"; the scraper repository itself was not inspected from here).
That is an **unintended activation**, so the rollback applies.

**What is pinned (perf_live `INPUT_REV = "N0"`, rule `n0-r2`, 2026-10-14).** It restores the APPROVED INPUT POLICY after
the unintended activation - it is not a finding that the old coverage predicts better (the comparison above is
inconclusive). Only the performance model's three queries (player maps, opponent maps, the rr-1 shadow's team rounds),
through an INCLUSION list that fails closed:
- IN: matches first fetched (min `raw_pages.fetched_at`) before the backfill began (2026-10-12 21:27 UTC) or with no raw page
  (the 2026-10-11 seed); and matches first fetched later, outside the two backfill windows, whose both teams are in the VRS
  top 200 of the dated `team_rankings` snapshot on or before their first-fetch day (UTC), by HLTV team id - the pre-repair
  scan rule on STORED historical rankings (daily snapshots exist from 2026-10-11; a later snapshot or today's list is never
  used for an older match);
- OUT: the backfill, later all-tier arrivals, a later arrival with no snapshot or no parsed teams, and ANY match not
  evaluated yet (a match fetched after the last evaluation waits for the next one, every 10 minutes);
- a player / opponent known only from outside N0 is "not in HLTV" again (BO3.gg fallback / average opponent, as before).
No approximate set exists: a failed evaluation serves the last VERIFIED evaluation of the same rule (memory, then the stored
copy), marked STALE in the revision and in the projection's data note (newer matches stay out), and with none anywhere the
projection is temporarily unavailable (transient) - never the full tables. One evaluation for everyone (single-flight). The
queries were validated on real Postgres (a Neon test branch of the scraper schema: every class, the UTC day boundary, and
an independent cross-check 324 in / 146 out). The r1 query (same windows and rule, exclusion form) ran read-only on
production on 2026-10-14 (by the other session, its own check): 0.3 s, 19,590 backfill matches, 10 later all-tier
arrivals; team_rankings holds daily snapshots 2026-10-11 .. 10-14 (350-351 rows each).
**Not pinned - MIXED REVISIONS:** storage (nothing is deleted), settlement, display, the last-10 / H2H records and the
stake-check reads (maps played, last map date, AWP share) keep reading the full tables (repaired history). Each grade
carries `input_revisions` per component; each forecast's `data_rev` = full-table count + `;records_input=full;perf_input=
N0/n0-r2:...`; `/admin/hltv_status` -> `perf_input` shows counts, snapshots, staleness and the last error. The original
forecasts keep their logged inputs.
**Limitations.** Daily snapshots only (the pre-repair scan compared against the ranking at scan time; intra-day changes are
invisible); that `team_rankings` is the exact VRS list the pre-repair scraper used is not verified; matches ingested
before 2026-10-12 21:27 UTC are taken as approved without re-checking the rule; the final r2 queries still need their
read-only production run before the deploy.

**Reconciliation of the comparison above** (recomputed from the saved per-line results, replay_units.json; same 616 kills /
531 HS lines, 23 matches):
1. "+0.40 kills" = the mean projection on the identical cohort: 29.492 -> 29.890 (+0.399). Because lines and actuals are
   identical, the mean signed error rises by exactly the same amount (+0.935 -> +1.333). Over recommendations: Over side
   346 -> 393 of 616 lines, staked Overs 188 -> 234 (HS: projection +0.270; Over side 233 -> 266, staked Overs 108 -> 126).
2. "Half the lines changed" (kills 307 of 616): side 99, grade 254, stake 205; side & grade 46, side & stake 44, grade &
   stake 205 (every stake change is also a grade change), all three 44, grade only 47. By the size of the chance move:
   < 2 pts 42, 2-5 pts 104, >= 5 pts 161. 51 of the 99 side flips are between two 0U Leans; stake moves median 0.5U, 23 by
   >= 1U. HS: 216 of 531 (side 75, grade 160, stake 126; < 2 pts 34, 2-5 pts 74, >= 5 pts 108; 53 of 75 side flips 0U).
3. Absolute scores (old N0 / new N, paired N - N0, 95% by match, 23 distinct matches): kills MAE 5.5794 / 5.5762, -0.0032
   (-0.193, +0.180); log-loss of P(Over) 0.6809 / 0.6822, +0.0013 (-0.016, +0.016); Brier 0.2446 / 0.2456, +0.0010 (-0.007,
   +0.008). HS MAE 3.9939 / 3.9749, -0.0190 (-0.106, +0.079); log-loss 0.6360 / 0.6345, -0.0015 (-0.019, +0.016); Brier
   0.2299 / 0.2288, -0.0010 (-0.009, +0.007). No difference is demonstrated in either direction.
4. The 20-50% added-history subgroup is EXPLORATORY: the plan pre-specified reporting "per coverage band" but not these cut
   points, and they were chosen with the results in view.
5. Contamination: both arms re-grade each forecast from its own saved inputs (line, odds, roster, environment identical) with
   the same code; only the HLTV history rows differ (as of the forecast time). N0 reproduces 382 of 383 saved projections of
   the same grading versions; on all 1,147 lines N0 matches the saved projection on 646 because older forecasts were saved
   under older code (a code difference, not a history one - both arms use today's code).

**Next.** The complete history stays the basis for the next candidate. Opponent handling is tested separately (arm C:
repaired history + an opponent-strength adjustment, frozen and tested on the large historical set, then forward-scored
beside N0) - the top-200 selection bias is not kept permanently because a small sample favoured it. Promotion = a versioned
release naming the revision, with the owner's approval.
