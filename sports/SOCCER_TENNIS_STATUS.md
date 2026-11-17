# Soccer / tennis release status — 2026-11-17

Branch: `codex/soccer-tennis-1117`, based on `a1b0a52`.
This release adds isolated lines and verified historical research. It does not
claim to finish validated soccer/tennis grading, settlement or slip generation.

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
existing esports web service the same flags expose nav links. Both default off.
The new page is `/sports/expansion.html?sport=soccer` (or `tennis`) under existing
Sports membership checks. Rollback is each flag off; saved records are preserved.
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
