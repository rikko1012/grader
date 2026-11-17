# Soccer / tennis implementation checkpoint — 2026-11-17

Branch: `codex/soccer-tennis-1117`, based on `a1b0a52`.
This is an unreleased implementation checkpoint, NOT completed graded-sport support.

## Implemented

- Separate source-native soccer and tennis quote adapters. Actual PrizePicks soccer and
  Underdog tennis feed structures checked on Nov 17. Snapshot probe: 1,814 accepted soccer
  lines / 233 provider players; 332 tennis lines / 97 provider players. These are captures,
  not a guarantee of current availability. PrizePicks tennis returned 429; no bypass/retry.
- Independent quote stores under `SCOUT_EXPANSION_DATA_DIR/<sport>` (default
  `SPORTS_DATA_DIR/expansion/<sport>`). Ownership guard rejects another sport. Append-only
  quote revision chains preserve A→B→A changes. 250 MiB history budget stops writing;
  no destructive retention and no automatic increase to paid resources.
- Separate opt-in scheduled collectors `SCOUT_SOCCER_ENABLED=1` and
  `SCOUT_TENNIS_ENABLED=1`. Defaults leave existing job registration unchanged.
- Member-authenticated `/api/sports/expansion/board?sport=soccer|tennis`. No upstream
  fetch or model work on request; two-entry snapshot cache, size limit, freshness rejection.
- Unlinked `/sports/expansion.html` UI with search, market, period, app, side filters and
  one card per provider player/match. Reuses `upgrade-next.css` tokens, existing fonts,
  saved theme, source-listed modifiers, no invented grades or odds.
- ESPN event inspection with tennis tournament/match distinction; separate ATP/WTA and
  soccer-competition canonical database paths. Tennis singles only; conservative completed
  set-score extraction excludes abnormal results and nonstandard set scores.
- Soccer summary parser for explicitly reported shots, shots on target, goals, assists,
  saves, fouls and cards. Completed regulation-time games only, exact event/team guards,
  participation evidence required. One actual captured match produced 258 stat records.

## Still required before calling the sport additions finished

1. Exact cross-provider event/player resolution, including international soccer competition
   coverage and tennis tour/format/surface attribution. Provider-only identities MUST NOT
   enter forecast, settlement, comparison or slip pools.
2. Scheduled bounded historical ingestion, player history endpoints and card connection.
   ESPN tennis summary attempts returned 400. A verified player-stat source for aces,
   double faults and serve/return statistics remains necessary. Do not infer these from scores.
3. Independent sport-specific projection models and frozen chronological validation using
   inputs available before each forecast. No existing sport's model, calibration or grade
   thresholds have been substituted. Soccer minutes and tennis expected service games need
   observed inputs before implementing rate-times-exposure projections.
4. Book-specific regulation/extra-time, retirement/walkover and push settlement rules;
   prospective forecast records, grading policy integration and slip eligibility tests.
5. Final navigation integration, actual member journey screenshots, server-backed browser
   tests, load/memory/bandwidth measurements, CI and exact-SHA release verification.
   Current UI tests mock the API and are not a live end-to-end certification.

## Validation so far

- 82 focused contract/scheduler/web/isolation checks passed before broader testing.
- Initial full Sports/gradecore run: 1,987 passed, 34 skipped, 2 failed (new jobs in default
  schedule). Fixed by opt-in-only registration. All 16 service-job/isolation checks passed
  afterward. This is not a claim of a clean final full-suite run.
- Latest isolated adapter/storage/soccer-stat checks: 25 passed.
- Browser component checks: desktop 1440px, phone 390px dark and light, 3 passed.
  Fixed period ordering, accessible filter labels, encoding, and excessive mobile filter height.
- Screenshots in `../outputs/soccer-tennis-ui-check/`; synthetic fixture players, not live data.
- No commit merged, no deployment triggered, no production environment or data changed.

## Source rights / availability notes

Owner states authorized use. Keep source provenance and any applicable permission records
with the deployment. OpenFootball's repository dedicates its data to the public domain.
Scraper code licenses do not themselves convey upstream data rights. Football-Data's
current public contact page restricts its free files to personal/academic use; the listed
Sackmann mirrors describe non-commercial terms. Neither is wired into a paid-site collector
by this checkpoint. No new subscriptions, paid APIs or browser-dependent collection.
