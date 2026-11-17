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
  competition and start time; ambiguous matches remain unmatched. Soccer roster
  membership must belong to that event. Data availability times are never backdated.
- Tennis completed-set arithmetic supplies games won/played, sets won/played and
  normal tiebreak counts. No aces or double faults are invented from scores. TBD,
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

## Verified evidence

- Public-feed collection into isolated local files: tennis 37 matches / 457
  processed records; soccer 29 matches / 2,081 processed records, no collection
  errors after fixing soccer's unsupported date-range request. These are processed
  records, not a claim that every row is unique or every listed market has history.
- 40 focused checks passed; the new authenticated HTTP test then passed outside
  Windows' restricted local-network context.
- Six local HTTP/browser tests passed, including the real esports Handler and
  real Sports service, card history and nav at 1440px and 390px. Fixtures are
  synthetic and this is not a live member-session certification.
- Screenshots: `../outputs/soccer-tennis-ui-check/`.
- Full Sports/shared-grader and esports/browser suites currently running;
  results are written to `../outputs/soccer-tennis-full-*.xml`.
- Render dashboard and GitHub browser session are signed out. No new feature
  flags, production data or deployment were changed during this implementation.

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
