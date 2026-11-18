# Soccer / tennis release status â€” 2026-11-17

Branch: `codex/soccer-tennis-1117`, based on `a1b0a52`.
This release adds isolated lines and verified historical research. It does not
claim to finish validated soccer/tennis grading, settlement or slip generation.
Prospective learning and existing-sport review details: `EXPANSION_LEARNING.md`.

## Implemented

- PrizePicks soccer and Underdog tennis source-native lines, exact offered sides,
  source timestamps and payout modifiers. Unknown prices stay missing. No fantasy
  score, live, combined-player, promotion or unknown-period substitution.
- Separate quote stores and immutable revision chains for each new sport. Separate
  canonical history databases for every soccer competition and ATP/WTA tour. No
  existing sport's dataset or grading path is used as a fallback.
- Opt-in collectors and bounded historical ingestion: at most 16 source requests
  and 150 seconds per history run, with resumable 90-day backfill. Source
  Retry-After holds survive process restarts. Existing jobs are unchanged when
  `SCOUT_SOCCER_ENABLED` and `SCOUT_TENNIS_ENABLED` are absent.
- History is matched by full player identity, exact opponent/team pair, tour or
  competition and start time; ambiguous matches remain unmatched. Soccer identity uses the match roster or a freshly verified team roster with
  an exact fixture/team match; a current roster never proves participation. Data availability times are never backdated.
- Tennis completed-set arithmetic supplies games won/played, sets won/played and
  normal tiebreak counts. A bounded theScore fallback supplies explicitly reported aces, double faults,
  break points won, points won and tiebreaks won. Both players and their ordered
  set scores must match ESPN before saving anything. Source comparison percentages
  are not imported as serve probabilities. TBD,
  doubles, retirements and inconsistent/incomplete set results are excluded.
- Soccer summary stats are regulation-only, with explicit participation evidence.
  The parser refuses a summary from another event or competition. This matters:
  ESPN's player-log endpoint returned Champions League despite an EPL filter.
- New pages use current Scout design tokens, light/dark theme, player roles,
  grouped market/period controls, search, app and side filters, and on-demand
  L5/L10/L20 history. Pagination limits rendered cards. Stale/started lines leave
  the board. Changed quote revisions invalidate a pending history comparison.
- Member-authenticated board/history routes. No provider access on member reads.
  Unchanged refreshes return a compact confirmation instead of all line payloads.
  Optional nav links are shown only with the corresponding environment flag.

- Real sportsbook odds: BetRivers Ohio soccer and tennis native markets, including
  the soccer draw and set-specific tennis labels; DraftKings three-way soccer
  via ESPN current prices. A separate tab defaults to source-marked main markets
  with at least two outcomes; one-sided ladders and other markets require the
  explicit alternate-market toggle. No inferred missing side or fabricated price.
  Checked time and provider change time remain distinct. No cross-book joins or
  arbitrage eligibility is implied by native market display.
- Detail requests rotate through the upcoming slate (two per sport per quote run);
  full listings provide main match prices. Only freshly returned detail markets
  are displayed. Odds revisions are namespaced and saved in each sport quote store.

## Verified evidence

- Live-source verification in isolated local files: 1,552 soccer offers and 325
  tennis offers in the saved source snapshots. First two roster batches linked
  272 soccer offers; tennis linked 49. These counts are dated verification,
  not guaranteed ongoing coverage or unique player counts.
- Read-back verified Neymar's regulation shots and Carlos Alcaraz's 4 aces from
  an independently score-matched completed game. Tennis saved 50 theScore count
  observations in addition to ESPN score totals. Brazilian soccer saved explicit
  player match stats. FotMob now adds exact attempt counts, tackles, minutes,
  touches, clearances, xG and xA after competition/team/time/score/player checks.
  A real stored game read-back returned 28 minutes/pass/tackle observations,
  with missing fields left absent. Match-specific starting roles are recorded
  only when ESPN explicitly lists eleven unique starters; current rosters are not
  used as starter evidence. These are facts, not an evaluated projection model.
- Future soccer fixtures lack league links: verified ESPN league UID mapping
  fixes their omission. Team schedules prioritize relevant recent matches.
- 62 focused adapter, authenticated HTTP and browser checks passed. The wider
  Sports run found the new cross-service test in the wrong directory; moving it
  to sports/tests preserves the existing isolation gate, which now passes.
- The 700-member existing Sports load check: 5,600 requests, zero errors or busy
  responses; p95 1.196s, p99 2.179s on this Windows machine (not production).
- New-board cold-snapshot test: 700 members, 1,400 requests against 1,800 offers
  per sport; zero errors/retries, initial p99 1.060s, refresh p99 1.047s. Shared
  encoding removed repeated per-member work; a refresh was 232 bytes, full gzip
  response 19,450 bytes. Cache expires at the first quote expiry or game start.
- Screenshots: `../outputs/soccer-tennis-ui-check/`; 1440px and 390px, current
  light/dark styling, real local HTTP handlers with synthetic member fixtures.
- CI on 747b2d4 passed all three required jobs. New odds/detail work requires
  its own final checks. Production enablement is still pending. Render access
  is now confirmed; both new-sport flags have been saved on Sports with Save only,
  pending the checked deployment. No collector has been enabled in a running build yet.

## Required before member grading / tracked settlement

1. Soccer expected minutes and confirmed-role inputs; tennis surface, format and
   service/return inputs with actual timestamps. Available score totals are not
   a replacement for unavailable player stats.
2. Sport-specific projection models, frozen chronological evaluation and forward
   validation at real offered lines. Existing sport calibration/letters must not
   be copied. The common gradecore policy remains the target for grade decisions.
3. Platform-specific retirement, walkover, extra-time, participation and push rules,
   and immutable pre-start forecast receipts. Provider-only matches must not enter
   settlement, exact-line book comparison or generated slip pools.
4. Final CI/load checks and exact-SHA release; turn on each new sport separately
   and verify source health, persistent reads and existing-sport performance.

## Activation / rollback

On the existing Sports service: `SCOUT_SOCCER_ENABLED=1`,
`SCOUT_TENNIS_ENABLED=1` register the corresponding quote/history jobs. On the
existing esports web service the same flags expose the ordinary sport selectors. Both default off.
Soccer and tennis use `/#scout/board?sport=soccer` (or `tennis`) in the main
Scout interface, under existing Sports membership checks. Old expansion-page
links redirect there. Rollback is each flag off; saved records are preserved.
No paid resource change is needed or authorized by this release.

## Source rights / availability

Owner states authorized use. No paid APIs, new subscriptions or browser-dependent
collection. OpenFootball is CC0 but lacks player prop stats. Scraper code licenses
are distinct from data permissions. Restricted historical mirrors are not copied
into this release. The listed TML 2026 CSV currently has limited early-season
coverage; it is not treated as a current ATP/WTA statistics feed. No claim is made
that the owner's full source catalog has been integrated.

## 20:21 UTC live-source verification

1,566 soccer player lines; 489 BetRivers and 9 DraftKings soccer markets;
291 Underdog tennis lines and 76 BetRivers tennis markets. PrizePicks tennis
returned an HTTP failure in this particular run, and no stale PP rows were
carried forward. Counts are time-specific source observations, not coverage
guarantees. 70 focused data/identity/isolation checks and 31 combined odds,
source-parser, authenticated HTTP and desktop/mobile checks passed.

## Challenger and result-review follow-up

365Scores now supplies singles Challenger and WTA125 score/stats records in
separate level stores. Exact level, player pair, time and ordered scores are
checked; main-tour, doubles and retirement results cannot cross into them.
The final pre-learning Sports suite passed 1,585 tests (34 skipped). Shared-core
and new-learning checks passed 508 tests. The broader local esports suite passed
4,680 and failed 23 old UI assertions; all 23 also failed on untouched main.
These numbers do not replace final CI, current-tree checks or live verification.

## Main-interface correction (2026-11-17)

The separate member page was the wrong integration. Soccer and tennis now use
the existing main board, player drawer, market/period filters, history charts
and Stats tab. Only offers with a verified current-event/player identity and
a saved exact-stat history appear in the default board. Coverage counts disclose
how many current offers still await history. L5/L10/L20 use actual sample sizes;
missing data never becomes a zero. Full-match soccer cards explicitly distinguish
regulation-time historical statistics.

History workers materialize a bounded research file in each isolated sport store.
Member requests read that file over the existing private Sports connection;
they never query providers or scan canonical databases. Stats include source-backed
role/lineup context, recent exact-stat averages and tennis surface/format when
present. History jobs now run every five minutes as bounded light jobs with the
existing 120 MB cap and shared memory admission. Learning jobs remain isolated.

Main sportsbook winner prices appear in the existing Moneylines tab. Soccer keeps
all three outcomes including Draw. Alternates and stale prices are excluded from
that feed. No artificial winner, model chance, grade or stake is supplied.

New-sport forecast/settlement validation is still incomplete: they are excluded
from generated/tracked slips and Public Record, not represented as validated
grades. CS2/LoL/NBA/NFL continue their existing grading paths. No paid-resource
changes are part of this correction.

## Kalshi match-winner prices (2026-11-18, reference only)

`sports/expansion_kalshi.py`, called last by each sport's existing quotes job.

- **Ingested:** match winner only, for the two series read live: `KXEPLGAME` (Premier League, 3-way after
  90 minutes plus stoppage time) and `KXATPMATCH` (ATP singles). WTA and Challenger series are listed with
  `verified: False` and are never requested. No totals, no player props.
- **Matching:** only against Scout's own ESPN fixtures (`current-matches.json`): exact normalized full or short
  names (or a hand-checked entry in `sports/kalshi_expansion_aliases.json`), both sides, a scheduled fixture
  that has not started, exactly one fixture per event and one event per fixture. Abbreviations are never used.
  Kalshi's dates are only a sanity window (soccer -1..+1 days, tennis -1..+4) and the start shown is ESPN's.
  Unmatched, ambiguous, one-sided or wide (over 5 cents) events are dropped and counted in `sources.kalshi`
  (`unmatched` lists up to 10 event titles, including matches ESPN's catalogue does not reach yet).
- **Storage / display:** `board.json` key `reference_prices` and `quotes.db` platform `ref:kalshi`, inside the
  sport's own expansion folder. Shown on the Moneylines tab as a separate "Kalshi" card (bid/ask midpoint, in
  percent, no pick). Not read by forecasts, learning or `gradecore/`; tests pin that.
- **Switch:** `SCOUT_SOCCER_KALSHI=0` / `SCOUT_TENNIS_KALSHI=0` turn it off (on otherwise; only read inside the
  quotes job, which needs `SCOUT_<SPORT>_ENABLED=1`).
- **Budget per quotes run:** at most 3 requests (1 today), one try each, 10 s timeout, 12 s for the step, the
  shared 2 s gap for the host; no request when the catalogue has no scheduled fixture for the series within
  8 days; an answer over 2 MB is refused; at most 150 rows. A 429 stops Kalshi for max(Retry-After, 15 min)
  and a 403 for 6 h, persisted in `request-holds.db` for both sports and never retried around.
- **Real run 2026-11-18 22:00 UTC (temp data folder):** tennis quotes job: 1 Kalshi request, 4 `KXATPMATCH`
  events, 4 matched to ESPN ATP Finals fixtures (4.2 KB of reference rows), job peak RSS 39.8 MB, 4.2 s.
  Soccer quotes job: no Kalshi request, because ESPN's catalogue held no scheduled Premier League fixture yet
  (the next matches were three days out); peak RSS 59.0 MB. No 429 or 403.
- **Not verified:** a live Premier League match end to end (parser and matcher are tested on the recorded
  answer only); the Kalshi rate limit on the production host, where the NBA/NFL `ingest-kalshi` job uses the
  same host from another process (the shared pacing below is tested, not run there); any series marked
  `verified: False`.

### Review fixes (2026-11-18, both exchange sources)

- **One pacing mark and rate-limit hold per host for the whole box** (`sports/host_marks.py`,
  `<SPORTS_DATA_DIR>/request-marks.db`, request metadata only). `ingest-kalshi`, the NBA/NFL ingest's Polymarket
  source and the soccer / tennis quotes jobs now take turns on Kalshi (2 s) and Polymarket (1 s), and a
  Retry-After or bare 429 (15 min) seen by any of them stops all of them without a request. This reaches into
  the NBA/NFL collectors' request path (not their data or grading): while a hold is in force `ingest-kalshi`
  stores no new Kalshi quotes. A soccer / tennis 403 hold (6 h) is NOT published - it stays in
  `expansion/request-holds.db`. Off switch: `SPORTS_SHARED_HOLDS=0`. Tested with fakes and three real local
  processes; not run against Kalshi or Polymarket and not on the production host.
- **Download limit:** the 2 MB limit now stops the download (streamed), not only the parsing. Checked against
  a local test server sending 39 MB chunked: stopped after about 4 MB sent, process RSS +2 MB.
- **Run time:** no reference step starts later than 40 s into a quotes run (`error: no_time`); the lines are
  saved and the reference prices wait for the next run.
- **Storage:** `ref:` revisions in `quotes.db` older than 14 days are deleted on each save (line and game-odds
  revisions never are). Real growth per day was not measured.
- **Ops:** the reference sources' last status is in `reference-status.json` in the sport's folder and in admin
  ops as `expansion_reference` (`ok`, `error`, `held_until`, `truncated`). There is no alert for it.

## Polymarket tennis match-winner prices (2026-11-18, reference only)

`sports/expansion_polymarket.py`, called by the tennis quotes job after Kalshi. Tennis only; soccer was not checked.

- **Ingested:** the match-winner (`moneyline`) market of singles matches in Polymarket's `atp` series (10365: ATP
  Finals and Challengers) and `wta` series (10366). Doubles series are never requested. Set, total and handicap
  markets are not stored (many sit at 1c / 99c). A market type not seen on 2026-11-18 (for example a
  "match completed" market) is listed in `sources.polymarket.unparsed_types` and not parsed.
- **Quote rule:** both sides quoted, bid/ask at most 5 cents apart, at least $500 resting, market active and
  accepting orders, and the match not started by Polymarket's own start time and live / ended flags. The row
  holds bid, ask and midpoint per player - no American or decimal odds. Traded volume is small, so a midpoint
  can be a market maker's quote; it is labelled a reference.
- **Matching:** the Kalshi rule and code: Scout's own scheduled fixture in `current-matches.json` (ATP series
  -> `atp` / `atp_challenger`, WTA series -> `wta` / `wta125`), exact normalized full names on both sides, one
  fixture per event and one event per fixture - plus Polymarket's start within 2 hours of the fixture's. When
  one match is catalogued under two levels, it links only if exactly one tournament name equals Polymarket's.
  Everything else is dropped and counted (`unmatched_count`, first 10 names in `unmatched`, `ambiguous`,
  `dropped_wide`, `dropped_thin`, `started`). The start shown is the fixture's.
- **Storage / display:** `board.json` key `reference_prices` (beside Kalshi's rows) and `quotes.db` platform
  `ref:polymarket`, in the tennis expansion folder only. A new revision is stored only when the bid or ask
  changes. Shown on the tennis Moneylines tab as a separate "Polymarket" card (midpoint in percent, no pick,
  "not tradable from the US"). Not read by forecasts, learning or `gradecore/`; tests pin that.
- **Switch:** `SCOUT_TENNIS_POLYMARKET=0` turns it off (on otherwise, inside the tennis quotes job only).
- **Budget per quotes run:** at most 6 requests of 25 events (about 1.1 MB each as sent, trimmed to under 1 KB
  an event page by page), one try each, 12 s timeout, 18 s for the step, the shared 1 s gap for the host; no
  request for a tour with no scheduled fixture within 8 days; an answer over 2 MB is refused; at most 150
  rows. A 429 stops it for max(Retry-After, 15 min) and a 403 for 6 h (`request-holds.db`); any other error
  ends the step for that run. Never retried around.
- **Real run 2026-11-18 22:15 UTC (temp data folder, catalogue from a real history run a minute earlier):**
  4 Polymarket requests (3 ATP pages, 1 WTA), 56 events, 34 matched and stored (26 Challenger, 5 WTA 125,
  3 ATP Finals; 40.8 KB of rows), 6 dropped as wide, 5 already started, 11 unmatched. Whole tennis quotes job:
  peak RSS 44.1 MB, 6.8 s, no 429 or 403. The 38 exchange cards (34 Polymarket, 4 Kalshi) were built from that
  board by the Moneylines code.
- **Not verified:** the same job on the production host and under its 80 MB cap; a second run (that an
  unchanged price adds no revision is tested, not run live); a Grand Slam week (more events than 6 pages of 25
  would be cut and reported as `truncated`); why 11 events did not match (names or start times differing from
  the catalogue were not examined one by one); the card at 390 px and desktop in a browser (the browser tests
  cannot run in this environment; the card reuses the Kalshi card's markup with a longer label); transfer of
  roughly 3 MB per run as sent, every 180 s.
