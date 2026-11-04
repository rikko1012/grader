# Upgrade checklist

Status of `1Time_Scout_Ultimate_Upgrade_Brief.md`. A box is ticked only with evidence.

## Phase 0
- [x] `gradecore` skeleton, registry, no-sport-import tests (main 15dacfd)
- [x] Root `CLAUDE.md`
- [x] Latest daily report read (2026-11-03): NBA 4,614 A+B, 64.44% stated / 55.42% hit; NFL 2,216 A+B, 66.66% stated / 54.69% hit. Both fail their checks. This is report retrieval, not a new daily job.
- [x] Latest daily report read (2026-11-04, `.claude/sports-reports` read-only pull 19:19 UTC; report generated 10:07:36 UTC, game-day 2026-11-04): NBA A+B n 4,773 (75 games, 9 game days, 2026-10-24..11-02) stated 64.45% / hit 55.25% [52.78, 57.55], verdict keep (failed hit_ge_stated_minus_5pts and balance_AB_and_all_le_15pts); NFL A+B n 2,216 (27 games, 5 game days, 2026-10-25..11-02) stated 66.66% / hit 54.69% [50.13, 59.38], verdict contain (hit more than 0.10 below stated). All leans: NBA 3,854-3,131-52 (hit 55.18%, stated 61.08%), NFL 1,616-1,400-14 (hit 53.58%, stated 63.47%). Containment file: both leagues contained before and after the daily run; env `SPORTS_CONTAIN_NBA=0` / `SPORTS_CONTAIN_NFL=0` reported by the ct-1 snapshot. Report retrieval only.
- [x] Results keep pushes apart: live page 2026-11-02 reads 894-790, 17 pushes; every table sums to 1,684 picks / 894 wins
- [x] Board with no Edge sorts by start time. 2026-11-04: the header arrow / aria-sort and the phone "Sort by" select now follow the sort in effect (`table.js effectiveSort`; audit P2 misleading indicator). Display-only books fallback for the Market column behind `SPORTS_MARKET_FALLBACK` (snapshot `mk.fb`, the listed books' own stale readings at the headline line; never a lean / edge / sort input). The blanks still come from the failed-market gate, not the feed.

## Phase 1
- [x] Reuse pure modules from `claude/gradecore-phase1` afadb3e; 186 original tests pass on the integration branch. New finite-number/all-push contract checks added; no live imports.
- [ ] Merge the branch after the full suite and CI pass
- [x] Saved projection adapters: cs2, lol, nba, nfl; raw probability/identity checks and gated forecast capture.
- [x] Market and settlement enrichment (2026-11-04, shadow + admin comparison only): `adapters.market()` returns the quote saved WITH the forecast (CS2 `forecasts.quote` Underdog decimal prices; LoL `lol_forecasts.sides` Underdog decimal prices; NBA/NFL the mc-1 consensus forecast of the same forward run, passed through the flag-gated capture call) as a de-vigged `MarketQuote` with overround width and age; a one-sided, unpriced or later-than-the-forecast quote is None. `adapters.settled()` attaches the saved result (`settle()`; push never a win). `engine.assess` and the admin comparison rows carry `market`; `history.cs2/lol` rows carry `market_p` / `market_at` from the same saved quotes. No blend table is fitted, so the shadow chance does not move (model weight 1). Tests: gradecore/tests/test_adapters_market.py.
- [x] Property tests run against all four saved projection adapters (test_capture.py)
- [x] Changing one number in `policy.py` changes candidate bands in all four sports in tests
- [ ] Fit blend weights and calibration on settled history, chronological, held-out legs only. Tooling ready 2026-11-04: `python -m gradecore evaluate --rows <jsonl> --fit-end .. --calibration-end .. [--out DIR]` writes `calibration_<sport>.json` + `evaluation_<stamp>.json` to `SCOUT_GRADECORE_ARTIFACTS` (never overwrites without `--replace`, `release_allowed` false). No fitted artifact exists: no settled raw-probability export is reachable offline (see the next line).
- [ ] CS2 reliability chart on held-out legs: every bin within 3 points. `python -m gradecore reliability` writes `reliability_<sport>_<stamp>.{json,md}` with the 3-point rule. Run 2026-11-04 on the only offline CS2 copy (`C:\Users\tikic\cs2_scout\picks.db`, saved 2026-10-11, 909 forecasts over 7 model versions, 77 real settled picks, no `probs_raw` / `quote` columns - raw distributions were first logged 2026-10-15): 0 eligible rows, 0 bins, verdict "not evaluable" (gradecore/reports/cs2_reliability_2026-11-04.md). The real run needs the Render copy, read-only, in the cs2-scout Shell: `python -m gradecore cs2-local --db /data/picks.db --list`, then `--model-version <frozen tag>` and `reliability --sport cs2` (owner / ops; no production write).
- [x] Over/Under calibration check added to candidate containment ct-3; at least 60 decided picks per side in the 300-pick window, each gap <=2.5 points. Opposite errors cannot cancel.
- [ ] 7-day side-by-side log on all four sports, nothing member-visible (owner sets `SCOUT_GRADECORE_SHADOW=1` on Render; the clock starts at the first captured row, `reporting.comparisons(...)["started_at"]`)
- [x] Admin comparison page built: four sports, bounded pages, raw/market/blend/calibration reasons; desktop and phone tests pass. Deployment verification is separate. 2026-11-04: rows also carry the saved market quote used (`market`), shown in the existing "Why this band" steps.
- [x] numpy in the `scout-sports-api` image: `requirements-sports.txt` line 3 `-r requirements.txt` -> `requirements.txt` line 3 `numpy>=1.26`; `Dockerfile.sports` lines 11-12 install `requirements-sports.txt` into the image. File evidence; `pip show numpy` in the running container is an owner shell check.

## Remaining brief, build order 1-4 (branch claude/remaining-brief, 2026-11-04; no live grade, chance, stake, forecast, settlement rule or flag default changed)
- [x] 1. Member wording: `web/upgrade-hub.js` Slip Studio explainer no longer carries the "does not place a bet" line (now "Tracking saves a receipt of the line and forecast you chose."). Docs: sports/RUNBOOK.md current Render resources (Standard 2 GB / 1 CPU, 5 GB disk) + security-header switches; MODEL_VERSIONS.md carries the >= 500 settled legs / clustered CI / 3-point out-of-sample rule; this file.
- [x] 2. `SCOUT_CSP` (unset/0 | `report` | `1`), `SCOUT_FRAME_ANCESTORS` (default `'self' https://whop.com https://*.whop.com`), `SCOUT_CSP_CONNECT`, `SCOUT_CSP_POLICY`, `SCOUT_CSP_REPORT_URI`, `SCOUT_HSTS` (+ `SCOUT_HSTS_MAX_AGE`) on server.py and sports/service.py; headers absent when off, bodies identical, never X-Frame-Options, Whop login redirect unchanged (tests/test_csp_headers.py, sports/tests/test_csp_headers.py).
- [x] 3. Sports table sort indicator follows the sort in effect; `SPORTS_MARKET_FALLBACK` display-only Market column fallback (sports/tests/ui_table_test.js, sports/tests/test_market_fallback.py, sports/tests/test_ui_audit.py).
- [x] 4. gp-1 shadow completion as above; `python -m gradecore` CLI (gradecore/tests/test_cli.py). Owner / ops next: `SCOUT_GRADECORE_SHADOW=1` on Render (7-day clock), the read-only CS2 export on the Render copy, the NBA/NFL gradecheck stays the daily job in the scout-sports-api Shell.
- Blocked, reported not faked: the extra odds provider (The Odds API adapter exists, key unset, event mapping not built) - no verified provider enters the book toggle or CS2/LoL market tools.

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
