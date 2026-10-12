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

## Activation of repaired history in CS2 Scout (grade-changing; not done)

1. The scraper writes the backfill as a new dataset revision (R5); CS2 Scout already records the revision seen by every forecast (forecasts.data_rev, since 2026-10-12).
2. Before any member grade uses the repaired history: replay the live model on a fixed set of recent board lines with the old and the new revision, report per-line changes (projection, chance, grade, stake) and the forward-scored difference; the frozen evaluation populations (lb-001, learning ledger, recaps) keep their recorded inputs and are never silently re-scored.
3. Because the missing matches are not random (online, weaker opponents, higher kills per round), complete history without an opponent-strength adjustment raises projections (round 5 C0). The repaired history and the opponent handling are tested together as a frozen candidate before activation; activation is a versioned release (MODEL_VERSIONS.md) with the dataset revision named.
