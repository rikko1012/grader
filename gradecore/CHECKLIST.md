# Upgrade checklist

Status of `1Time_Scout_Ultimate_Upgrade_Brief.md`. A box is ticked only with evidence.

## Phase 0
- [x] `gradecore` skeleton, registry, no-sport-import tests (main 15dacfd)
- [x] Root `CLAUDE.md`
- [x] Latest daily report read (2026-11-03): NBA 4,614 A+B, 64.44% stated / 55.42% hit; NFL 2,216 A+B, 66.66% stated / 54.69% hit. Both fail their checks. This is report retrieval, not a new daily job.
- [x] Results keep pushes apart: live page 2026-11-02 reads 894-790, 17 pushes; every table sums to 1,684 picks / 894 wins
- [x] Board with no Edge sorts by start time. Stale-feed fallback not built: the blanks come from the failed-market gate, not the feed

## Phase 1
- [x] Reuse pure modules from `claude/gradecore-phase1` afadb3e; 186 original tests pass on the integration branch. New finite-number/all-push contract checks added; no live imports.
- [ ] Merge the branch after the full suite and CI pass
- [x] Saved projection adapters: cs2, lol, nba, nfl; raw probability/identity checks and gated forecast capture. Market and settlement enrichment remain separate work.
- [x] Property tests run against all four saved projection adapters (test_capture.py)
- [x] Changing one number in `policy.py` changes candidate bands in all four sports in tests
- [ ] Fit blend weights and calibration on settled history, chronological, held-out legs only
- [ ] CS2 reliability chart on held-out legs: every bin within 3 points
- [x] Over/Under calibration check added to candidate containment ct-3; at least 60 decided picks per side in the 300-pick window, each gap <=2.5 points. Opposite errors cannot cancel.
- [ ] 7-day side-by-side log on all four sports, nothing member-visible
- [x] Admin comparison page built: four sports, bounded pages, raw/market/blend/calibration reasons; desktop and phone tests pass. Deployment verification is separate.
- [ ] numpy confirmed in the `scout-sports-api` image

## Existing features: preserve, extend only where incomplete

The owner explicitly requested skipping completed work (2026-11-03). Existing
features below do not need a second implementation. Live UI evidence is from
9b86770 (PR43); backend and browser regression evidence is recorded with that PR.

- [x] Cross-sport account drafts and server rechecks (one platform per entry)
- [x] Separate requested slip count and 2–6 picks per slip; 8/8 distinct slips verified live
- [x] Generated batches saved to the account and restored after reload
- [x] Unified research shell, sport filters, prop drawer, mobile navigation
- [x] History chart and L5/L10/L20 columns (coverage depends on recorded stats)
- [x] Exact-event/stat/period line comparison
- [x] My Picks and sport-filtered performance record
- [x] Saved views and in-page saved-filter alerts
- [x] Prop Genie surface and existing grounded assistant tests
- [ ] Switch over to shared grading, only after prospective validation
- [ ] Correlated shared-core slip math and verified payout/EV/Kelly presentation
- [x] Public aggregate record and four-sport 30-day calendars built; mobile/desktop, retry, escaping and private-route tests pass. Activated only with SCOUT_PUBLIC_RECORD=1.
- [ ] Closing-line history and complete cohort filters
- [ ] Background alert delivery (current alerts require the page open)
- [ ] Genie integrated and verified across all four sports
- [x] Existing branded share image preserved; checked-entry requirement and no implicit tracking are covered by test_workspace_v2_browser.py.
- [ ] Operational EV/arb feed with verified rules and validated probabilities; extra verified platform

Existing UI does not certify completion of every interaction in the new brief.
Finish and test the missing connections rather than adding duplicate screens.

## Integration work, not yet deployed (2026-11-03)

- Owner-only comparison API and Tools screen built for four sports, bounded pagination, read-only databases, escaped content. Desktop and 390px phone interaction tests pass. It displays published chance/letter and candidate chance/band, with expandable raw/market/blend/calibration and grade reasons.
- Background alert cooldown no longer discards unsent revisions. A deferred revision is checked again for current availability before delivery. Consent, access and restart-dedup tests pass. Line differences are indexed once per tick, not rescanned for every member and prop. Whop experience access returned 200; opt-in capability configuration is saved for the next deploy. Actual notification delivery is not yet verified and nobody was enrolled.
- Offline historical conversion selects the last playable forecast at least 30 minutes before start, isolates frozen model versions, preserves pushes, excludes artificial rows and uses latest settlement corrections. No fitted result or improved accuracy is claimed yet.
- Sports/shared-core regression run: 1,225 passed, 34 skipped. Later history/version changes require focused checks. Combined API/core/alerts run: 512 passed before the latest additions. Full esports/browser run remains in progress.
- Release still requires chronological real-data evaluation, matching calibration artifacts, seven prospective days, and live verification. Existing public grades are unchanged.
- Real-data evaluation exposed and fixed two exporter defects: CS2 test=2 is a real automatic board forecast (test=1 is artificial); LoL outcomes join by immutable settlement unit and then verify canonical map-scope identity. LoL's frozen lol-1f/f59d290ec261 cohort selects 941 observations: fit 410, calibration 290, test 79, purged 162. The 76 decided held-out forecasts are below the 500 requirement; no improvement or promotion is established.
- Final four-sport 700-member fixture load: 19,600 requests, zero non-200 responses, all 144,200 expected receipts. p95 3,387 ms and p99 6,059 ms across the burst; this is not a measured production latency claim.

## Written decisions
- **Accuracy and profit are separate.** Calibration says stated chances match results; profit needs results above break-even. Neither proves the other, and each is reported on its own.
- **Coverage is reported with accuracy.** Every grade change shows the accuracy change and how many letters changed. Fewer grades is not an accuracy gain.
- **Unknown payouts stay unknown.** Verified official PrizePicks base payout tiers are shown as a reference, not an entry-specific promise. Goblin/demon overrides and live EV/stakes still require the applicable contest payout and validated probabilities.
- **Width direction in the new brief:** shrink toward the market as width grows. The reused `blend.py` implements that direction. It remains disconnected from live grades until chronological fitting and held-out comparison pass; a wide market can separately cap a stake through `gates`.
- **No official NBA/NFL announcement** while the daily check fails.
