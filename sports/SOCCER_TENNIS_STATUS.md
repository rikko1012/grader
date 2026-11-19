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

## PrizePicks soccer per-league tabs (2026-11-19, ppsoccer-1119)

PrizePicks moved soccer from the single SOCCER tab (league 82) into one tab per league (member report: Liga MX,
MLS and Serie A props missing). The soccer quotes job now reads every mapped tab:

- Exact table `expansion_offers.PP_SOCCER_LEAGUES`: 82 SOCCER (mixed, no pin, read exactly as before), 14 EPL eng.1,
  6 MLS usa.1, 536 Liga MX mex.1, 534 Serie A ita.1, 531 La Liga esp.1, 533 Bundesliga ger.1, 535 Ligue 1 fra.1,
  79 UCL uefa.champions. A tab is read only when `/leagues` (read at most hourly; cached list trusted 24 h, then
  every mapped tab is tried) lists it with projections under the exact same name. 1H / 2H child tabs, SOCCERSZN (262)
  and WORLD CUP TRNY (457) are never read. A new soccer-icon tab with lines is only reported
  (`pp_soccer_tab_unmapped`): HANDBALL carries the same icon.
- Per-league rows are filtered by numeric `league_id`, keep standard lines only, and carry `pp_league`,
  `competition_code` (identity resolves only against a fixture of exactly that competition) and `team_aliases` (the
  game's own full team names, exact equality: "Atlético de San Luis", "Seattle Sounders FC"). Tab-82 rows have none of
  these keys, so their revisions and forecasts are byte-identical to 5036ce4.
- Rate limits: tab 82 first every run; at most `SCOUT_SOCCER_PP_MAX_CALLS` (5) PrizePicks requests a run including
  `/leagues`, none started after `SCOUT_SOCCER_PP_BUDGET_S` (35 s), `SCOUT_PP_GAP_S` (8 s) between any two PrizePicks
  requests of the soccer and tennis jobs (shared through request-holds.db), and the first 429 / Retry-After hold /
  failed request stops every further PrizePicks request that run. Tabs whose last good read had no standard line are
  re-read every 15 min. A tab not read in a run keeps its last good rows with their own `observed_at`; the 300 s rule
  hides them exactly when they go stale. An empty answer from a tab `/leagues` lists with lines is a failed read.
- Grading is unchanged: MLS and Liga MX full-match lines stop at `extra_time_scope_unverified` (usa.1 / mex.1 are not
  regulation-only leagues), passes / tackles / crosses / clearances / dribbles have forecast inputs only for the six
  FotMob leagues, `shots_assisted` has no collector. Goal + Assist and fantasy-score labels are unmapped, as before.
- Ops (GET): `expansion_pp_soccer` = per tab `listed`, `parsed`, `kept`, `on_board`, `last_ok_at`, `last_error`,
  `poll`; plus `calls`, `stopped`, `unmapped_with_lines`, `renamed`.
- Kill switch: `SCOUT_SOCCER_PP_LEAGUES=0` = the single tab-82 request exactly as at 5036ce4. `SCOUT_SOCCER_PP_MLS`
  is retired (MLS is tab 6 in the table); it still works only with the kill switch on, as before.

