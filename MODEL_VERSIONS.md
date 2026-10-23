# Grader versions (test week from the 2026-10-07 freeze)

## `aa6696d5d6` — bug-fix release (no model change): CS2 accuracy audit fixes (cs2acc-1), 2026-10-23

Base: `f663af8917`. Bug-fix release (no model change): no parameter, coefficient, band, threshold, cap or factor changed; the
cm-2 / hs-cm2 model (cm1.py, cm2.py, cm2_model.json) is unchanged. Source: research/cs2-accuracy-2y AUDIT.md (AU-1) and
UNDER_TRACE.md. Each fix has a regression test (tests/test_au1_duplicate_units.py, tests/test_cs2acc1_fixes.py).
- **D1 duplicate P1 units (evaluation only, no grade changes).** identity rule 5 (identity.RULE_VERSION 5): an hltv-<id>
  record and a BO3.gg-slug record of the same player on the same team with starts within 60 minutes are one evaluation match.
  Fixes the 8 units of MARKandLARRY 2026-10-12 (BRACE, micalis, sav, yourwombat) scored under both
  'martyrdom-vs-markandlarry-12-10-2026' and 'hltv:2399144'. Two BO3.gg slugs are never merged, each hltv id joins one slug,
  a team's same-day series 3 h apart stay two (FORZE Reload 2026-10-14 guard). The learning ledger re-keys once on the
  version change (_migrate_unit_identity; old ledger kept as ledger_v4_*).
- **Player aliases (I3).** hltv.PLAYER_ALIASES, exact names, used only when HLTV shows the aliased player on the graded team:
  Techno4K = HLTV Techno (20275, The MongolZ), brain = HLTV Brain47 (23294, Phantom Academy). dev1ce not added (owner decision).
  Lines that can change: these two players' lines (they leave the perf-2-bo3 BO3.gg fallback for the HLTV route: perf-2, and
  cm-2 / hs-cm2 for Maps 1-2 where eligible) and their HLTV numbers panel.
- **Team aliases (I5).** team_aliases.json: Orgless Aust = LFO 8 (13976), Sökerorg = LFO 10 (14084), Martyrdom = Fruju (14055),
  each verified on HLTV and BO3.gg (same fixture and event, identical five players). The mapping revision changes (recorded as
  mapping_rev). Lines that can change: matches with these spellings (opponent adjustment, H2H, match linking and settlement
  identity of those teams and their opponents). LP -> BARCA (HLTV 2399805, paiN's opponent 2026-10-21) NOT added: the roster
  is the same, but 'LP' is an exact HLTV team (13304), so a name alias cannot express a dated rename; it needs a separate rule.
- **perf-2-bo3 history window (cosmetic).** cs2_scout's BO3.gg fallback reads opponent history from 420 days before the match
  day (as perf_live), not before today. Lines that can change: perf-2-bo3 lines on matches after today (UTC), by under
  0.01 kills (edge weight e^-3.5).

## Slip builder `studio-4` (CS2 model tag unchanged: f663af8917), 2026-10-22

Slips release (no grade, chance, projection or stake changes): "build around" - settings.must_include (up to 2 board row ids,
each checked by why_excluded, the batch's both-sides rule and the other pin; left-out ids reported as pinned_left_out); every slip of
that build holds the eligible pins (stored per slip as built_around). Pins skip the batch's no-repeat / overlap caps (the member
asked for them in every slip); in-slip rules (one pick per match / player / team, never both sides of a stat) still apply.
Also: Verify refuses replaced / changed / dismissed slips (no copy text); copy text header "<Book> - n-pick entry - standard lines -
1Time grader" (no internal ids); member slip-copy logging fixed (slip_key without a board id); Studio legs on HLTV-only matches
settle (board rows carry settle_slug = BO3.gg slug else hltv-<id>); LoL batch keeps building past one failing slip.

## `f663af8917` — audit fixes (65 confirmed bugs) + hs-cm2 (HS Maps 1-2 on cm-2 kills), 2026-10-21

Base: `794a775f51`. Two kinds of change; no parameter, coefficient, band, threshold or cap changed.

**1. hs-cm2 (model change, passed its registered rule).** research/hs_cm2_2026_10_21 (protocol e7e5e12, result c83d446):
HS Maps 1-2 lines on the HLTV route take cm-2's kills (lambda, spread 1.4757 x sqrt) and HS per kill from the same cm-2
tables (cm1.hs_ratio, k_hs 25) under the live HS | kills beta-binomial (kappa 100). Nothing fitted. Hold-out (matches from
2026-10-17): HLTV-wide HS MAE gain +0.063 (95% +0.020, +0.105; mostly players with no N0 history - established players +0.015,
n.s.); P1 offered lines (580, 70 matches) log-loss gain +0.0049 (-0.0013, +0.0112). It projects HS slightly higher (Over lean).
Version `perf-cm2` (same as kills Maps 1-2); perf-2's numbers saved as `perf2`; ineligible lines stay perf-2 with the reason.
Rollback: `CM2_HS_LIVE=0` (HS only) or `CM2_LIVE=0` (all cm-2).

**2. Audit bug fixes (2026-10-21 audit: 16 reviewers, every finding re-checked by 1-3 independent verifiers; 65 confirmed).**
Grade-relevant ones (each with a regression test):
- Cards / grader: H2H no longer crashes on an HLTV-only meeting whose headshots are not scraped (HS lines that errored now
  grade); a very hot form trend no longer crashes performance-model lines (old-pipeline-only number); benched-on-profile
  players are not stand-ins (benched players ignored); the AWPer HS gate retries the player's own team for stand-ins;
  Polymarket / PandaScore opponent-only fallbacks respect the book's start; a background refresh no longer replaces a
  PandaScore-found match with SCOUT; nearby-line chips show the card's letter.
- HLTV / projection: namesake player choice uses team ids/aliases and recent maps; two HLTV teams sharing a name are no longer
  picked arbitrarily; NULL-kills/score player rows filtered (no crash to the old pipeline); a shrunken N0 evaluation is refused
  (last verified set kept); events-lookup timeouts are transient (no silent LAN/online change); context lookups serve the last
  good copy; cm-2's team id has a last-good fallback.
- Odds: Thunderpick win chance oriented by the current team index (never the opponent's); a hashed-id quote survives HLTV
  listing the match.
- Lines: one failed PrizePicks direct read no longer swaps in the scraper's older copy; Underdog records which sides are
  offered (an Under that cannot be placed is no longer staked); a PrizePicks Under that opens after grading regrades at once;
  board grades re-read their row (moved lines graded at the current line).
Everything else (server, memory caps, timeouts, settlement storage, web pages): no grade change. Full list per finding:
scratchpad audit_sorted.json; commits e48343a, 180b6e8, abdf057, 6a36212, e956815, 475514f, ed84924, 72baaa5.

LoL (not in this tag; lol-1f unchanged): live features now use the league of the match being forecast and Elo keyed by team id
(both = the tested A7.1 definition); worker stages isolated; settlement waits for complete scoreboards.

## `794a775f51` — cm-2 live: opponent lookup by the shadow's rule + HLTV blips stay transient, 2026-10-20

Fix to `8404c86d3c` (live 01:14-~01:45 UTC). Only `cs2_scout._cm2_live` changed.
- **Opponent id:** the live perf-2 read gives no opponent id when every one of the opponent's maps sits outside N0 (the pin),
  so 23 of 259 kills Maps 1-2 forecasts on `8404c86d3c` fell back to perf-2 ("own team or opponent not identified in HLTV")
  although cm-2's full-history tables have the team. cm-2 now takes the opponent exactly as the cm-2 shadow record does
  (perf_shadow.build): the live read's id, else the canonical id by name. perf-2 itself is unchanged (still N0).
- **Transient rule:** an HLTV database error during cm-2's team lookup is re-raised to `_perf_input` (retry, then keep the
  last grade) instead of quietly grading on perf-2. No tables = no lookups (perf-2, reason saved).
- No thresholds, bands, caps, stakes or the cm-2 fit changed.

## `8404c86d3c` — cm-2 projects CS2 kills Maps 1-2 live (`perf-cm2`), 2026-10-20

Owner decision 2026-10-20 (answer "1" to: promote cm-2 now for kills Maps 1-2, overriding the 300-match review rule).
This is an explicit owner override, not a passed review: cm-2's registered forward review (300 matches) has not been reached.

- **What changed:** kills Maps 1-2 lines on the HLTV route take their projection AND spread from cm-2
  (`cm2.projection`, artifact `6e586047c5b1823a`, the frozen 14-feature fit; sd = 1.4757 x sqrt(lambda)). The grade's
  `projection_source` is `perf-cm2`. The chance still goes through the same grader path (discretised normal, kills cap
  78%, push handling) and the same stake rules; no thresholds, bands or caps changed.
- **Unchanged:** HS lines, Map 1, Maps 1-3, the BO3.gg fallback (perf-2-bo3), LoL, settlement.
- **Kept beside it:** every perf-cm2 projection saves `perf2` (perf-2's version, lambda, sd, rate, opponent factor,
  rounds) and `cm2_live` (features, inputs, tables sha, artifact sha). A line cm-2 cannot forecast (environment unknown,
  team / opponent not in HLTV, tables not built, any cm-2 error) stays on perf-2 and saves `cm2_live.ineligible`.
- **Comparison continues:** the fh-1 shadow keeps replaying perf-2 on every line (perf-cm2 lines are checked against the
  saved `perf2` numbers); the cm-2 shadow record and its forward clock (`cm-002-9f1db397`) are unchanged.
- **Model tag:** cm1.py, cm2.py and cm2_model.json are now part of the model hash (picklog._model_version).
- **Pre-release impact (production fh-1 records 6102-7301, 738 kills Maps 1-2 lines):** cm-2 computed 738 / 739
  (1 player not in HLTV). Projection change: mean +0.22, median +0.10, sd 0.98 kills; 41 over 2 kills, none over 4.
  Lines at 55%+: 349 -> 371; 57%+: 255 -> 255; 63%+: 69 -> 73; Overs among 55%+: 135 -> 168. Side flips 140.
- **Evidence (supporting, not a passed review):** development MAE 6.238 -> 6.101; real-line eligible kills log-loss gain
  +0.0159 (95% +0.0058, +0.0264); forward cm-2 +0.0097 on 11 matches. The perf-3 projection fix failed its hold-out
  (research/proj_fix_2026_10_20).
- **Rollback:** set `CM2_LIVE=0` on Render (one step, no deploy), or revert this commit. Old forecasts are never rewritten.

## `c36104d079` — BO3.gg environment correction (`bo3-env-2`), 2026-10-18

Base: live tag `807a289529` (main `0fc44c2`). The only grading change is `cs2_scout.py`: the BO3.gg history rows behind
**perf-2-bo3** projections (players HLTV has no history for).

- **Before:** each historical map's environment was `(map label or match label) == "lan"`, so an absent or unexpected
  label made the map an ONLINE map.
- **Now (`hist_env`):**
  - only "lan" / "online" (or a boolean) count;
  - the map's own label comes first, then the same historical match's verified label (its tournament type);
  - tonight's environment is never used for an earlier match;
  - anything else stays unknown.
- perf.project already keeps unknown maps in the overall history and out of both the LAN-only and online-only history.
  The HLTV path already did this (`verified-history-1`); the BO3.gg path did not.
- **Changed cohort, by construction:** perf-2-bo3 forecasts whose match tonight is ONLINE and whose BO3.gg rows include at
  least one map with an unknown environment.
  - Their online-only history loses those maps; the overall rate is unchanged.
  - With all labels known, the rows are identical to the old rule's (tested).
  - For a LAN tonight, the old False and the new None are both outside the LAN subset; this was measured on the fixture,
    not assumed for every input.
- **Saved before/after on identical inputs:** every perf-2-bo3 projection now saves
  - `env_rule` (bo3-env-2);
  - `env_labels` (LAN / online / unknown map counts);
  - and, when any label is unknown, `env_rule_before` = the old rule's lambda and rate, from the same inputs at the same
    moment.
- Old forecasts and settlements are never rewritten. fh-1 snapshots store their rows as saved, so old records replay as
  they were. cm-1 and cm-2 are unaffected (HLTV tables only).
- No weights, thresholds, bonuses or stake rules changed. This is a correctness repair, not an accuracy claim.

## Unreleased correctness candidate — full audit, 2026-10-18

Based on `1735928`; normalized Git/LF model tag **`807a289529`**, builder **`studio-3`**.
This entry records a local release candidate, not a deployment. Deploy time and first production forecast are not yet known.
See `AUDIT_2026-10-18.txt` for evidence, limits and the release checks.

- Historical NULL environment stays unknown; it no longer enters the online-only rate. Player maps require ten distinct
  players; opponent concession totals require five distinct players with valid kills and round scores. Cache policy
  `verified-history-1` separates these inputs from earlier cached rows. N0/n0-r4 inclusion, weights and thresholds stay fixed.
- Full-history shadow cache keys and environment handling follow the live adapter. New snapshots record `row_policy`.
  Preserve prior snapshots and results; report the new live tag/row-policy cohort separately. No challenger is promoted.
- Maps 1–3 only voids a two-map sweep when the stored format confirms BO3. BO5/unknown-format partial data waits.
  No historical settlements are rewritten by this release.
- Copy checks enforce offered side, identity and freshness; the manual-slip keys include the event. New slip records carry
  the original grade's raw Over/Under/push distribution; saved older estimates retain their numbers and get conditional labels.
- UI repairs cover unavailable sides in Compare, first-map context/H2H, and factual IGL-list wording.

The input corrections can change grades and stakes; they are not a display-only change. The known-environment projection
identity check matched 3,000 cases against the base version. That does not establish an accuracy gain. No outcome-driven
parameter tuning was performed.

Every logged pick and forecast carries `model_version` = the first 10 hex characters of SHA-1 over
`grader.py`, `sim.py`, `cs2_scout.py`, `hltv.py`, in that order, as checked out on the server (LF line endings).
From G8 (2026-10-09) the tag also covers `perf.py`, `perf_live.py`, `scope.py` (appended in that order); from 2026-10-11 also `hltv_store.py`;
from 2026-10-13 also `market_odds.py`, `thunderpick.py` (the market input can come from Thunderpick), and from `6f6e2d39d7` also `pick_verdict.py` (side / grade / units);
from `23bea2fe37` also `bo3.py`, `roles.py`, `polymarket.py`, `oddspapi.py` (BO3.gg rows, the AWP role and the fallback market input move grades). Which source actually
supplied a forecast's win probability, and under which odds policy, is saved per forecast in `inputs.odds_provenance`.
A Windows checkout computes different (CRLF) hashes, so always compute tags from `git show <commit>:<file>`.

**Keep every exact tag when reporting.** Tags can be pooled only inside a "same grading behaviour" group, and only
on the evidence listed. To replay a forecast:
1. Check out the commit for its tag.
2. Feed the forecast's stored `inputs` (in `forecasts.inputs`, from 2026-10-08 00:54 UTC) to `grade_leg`.
3. Use the config snapshot for that tag (`model_versions.json` on the data volume).

**Odds-source policy activations (separate from model versions - a policy can switch while a model version is live).**
Measured from production records (policy_log / policy_events, the forecast log and the Thunderpick request log):

| Odds policy | Activated (UTC) | Model version live at activation | First forecast using Thunderpick | Basis | Ended |
|---|---|---|---|---|---|
| `tp-first-1` | 2026-10-13 13:33:00 (automatic) | `acbe039647` (no forecast of that version used Thunderpick) | 13:40:11, forecast 5867, version `4f00a300a1` | price agreement with exchange prices (correlation-based) | replaced by `tp-first-2` with `6f6e2d39d7` |
| `tp-first-2` | at its first activation after the `6f6e2d39d7` deploy - recorded in `policy_log` (evidence-gated: label rule + tested series meaning on >= 15 BO3 matches + inferred timezone + sanity gate) | `6f6e2d39d7` | after activation | see THUNDERPICK.md Status | - |

**Performance-model input (HLTV dataset) revisions (separate from model versions - the data can change without a code
change; every forecast records `data_rev`, and from `23bea2fe37` its `perf_input=` part names the pinned revision).**
Evidence: HLTV_COVERAGE.md ("Staged before/after replay - results", "Controlled rollback").

| Perf-model input | Live from (UTC) | Until | Definition | Status |
|---|---|---|---|---|
| top-200 scan coverage (= N0) | the 2026-10-11 Render rebuild | 2026-10-13 00:49 | matches with both teams in the scraper's VRS top 200 | approved release state |
| partial N (backfill loading) | 2026-10-13 00:49 | 04:26:15 | N0 + part of the backfill; 315 forecasts (data_rev matches 9,658 ... 27,964; complete export - an earlier partial export gave 367) | **unintended activation** - no staged revision, no approval |
| N (repaired history) | 2026-10-13 04:26:15 (first forecast with data_rev matches >= 28,000) | 2026-10-14 22:53:13 (the `23bea2fe37` deploy) | N0 + 19,590 backfill matches + every-tier scan since | **unintended activation** - live before its validation / activation gate |
| N0 pinned, rule `n0-r3` | 2026-10-14 22:53:13 (`23bea2fe37`, commit d1eef32; first answer of the new version) | the `6b6a43f2a6` deploy | as n0-r4 below, except that a match with NO raw page counted as the seed: 3 rows of a new source ('cito', dated 2026-10-14, discovered 22:24 UTC) entered without the rule (pre 8,600) | **defect found by the post-deploy check** - forecasts of `23bea2fe37` read these 3 matches (136 live forecasts by 23:07 UTC); report `23bea2fe37` separately |
| N0 pinned (`perf_live.INPUT_REV`, rule `n0-r4`) | the `6b6a43f2a6` deploy | - | a RECONSTRUCTED pre-repair input policy (not an exact historical restoration), as an INCLUSION list: the known original set kept exactly (matches first fetched before the backfill began; 8,597 on production - the 2026-10-11 seed has raw pages), plus later-fetched matches dated on or after 2026-10-12 whose both teams were in the VRS top 200 of the stored daily team_rankings snapshot on or before their first-fetch day (UTC), by HLTV team id; a missing snapshot = out; a match with no raw page = out; everything else out, incl. any match not evaluated yet (fail closed, never another dataset) | controlled rollback to the APPROVED INPUT POLICY - not a claimed accuracy gain (the 23-match comparison was inconclusive); the repaired data stays in storage. Production read-only check 2026-10-14 ~23:10 UTC: N0 = 8,642 of 28,244 matches (pre 8,597; later arrivals: 45 in, 19 outside the top 200, 19,580 older history; 3 without a raw page out), sha dc7e6a488fc8 |

**Mixed revisions (from `23bea2fe37`; rule n0-r4 from `6b6a43f2a6`).** ONLY the performance model reads N0. The last-10 / H2H records, the stake checks
(maps played, last map date, AWP share), settlement and display read the full tables (repaired history incl. the
backfill and the every-tier scan). Every grade carries `input_revisions` per component and every forecast's `data_rev`
reads `matches=...;max_id=...;max_date=...` (full tables) `;records_input=full;perf_input=N0/n0-r4:pre=..;late_in=..;
history_out=..;late_out=..;no_snapshot=..;no_teams=..;no_raw_page=..;snapshots=..;sha=..` (n0-r3: no `no_raw_page`) (+ `;STALE evaluated <time>` while a refresh
fails). The grader as a whole is NOT on "the old history".
**Limitations of N0 (why it is reconstructed, not exact).** The pre-repair scan compared lowercased team names with the VRS
list it fetched live AT SCAN TIME (its stored team_rankings only as a fallback); N0 uses that stored copy, one snapshot per
UTC day from 2026-10-11 (intra-day ranking changes are invisible; equality with the live list at each scan cannot be
verified) and HLTV team ids instead of names. Matches ingested before 2026-10-12 21:27 UTC are the known original set,
taken as approved without re-checking the rule. Details: HLTV_COVERAGE.md "Controlled rollback".
**d88cc8dd8e** was the tag of this release before the final changes (n0-r3, the 429 backoff, the outage card); it was never
deployed and no forecast carries it.

Affected forecasts = every forecast logged from 2026-10-13 00:49 UTC until the `23bea2fe37` deploy (2026-10-14 22:53:13
UTC) with data_rev matches >= 9,658; their saved inputs and grades are kept as logged (nothing was altered).
**Complete count** (2026-10-15, from the paged export /admin/forecasts_page.json: forecasts 1..26,575, gapless, cutoff
2026-10-15 01:05:40 UTC, joined to the picks export for the origin; member = a member card, board = the Prop Board's own
grade (test=2), load test = test=1). By the dataset revision each forecast records (data_rev):

| Revision read by the performance model | Model version | Logged (UTC) | Member | Board | Total |
|---|---|---|---:|---:|---:|
| partial N (matches 9,658-27,999) | `1088faf157` | 2026-10-13 00:49:50 - 04:23:06 | 203 | 112 | 315 |
| N repaired history (matches >= 28,000) | `1088faf157` | 2026-10-13 04:26:15 - 13:19:50 | 172 | 76 | 248 |
| N | `acbe039647` | 13:28:01 - 13:37:35 | 114 | 41 | 155 |
| N | `4f00a300a1` | 13:38:55 - 13:45:17 | 110 | 41 | 151 |
| N | `822a6b4505` | 13:48:42 - 13:54:44 | 111 | 41 | 152 |
| N | `17e5e85837` | 14:00:16 - 18:08:25 | 341 | 308 | 649 |
| N | `6f6e2d39d7` | 2026-10-13 18:10:12 - 2026-10-14 22:52:18 | 6,546 | 12,074 | 18,620 |
| **affected: partial N + N** | | | **7,597** | **12,693** | **20,290** |
| N0/n0-r3, 3 'cito' matches in (pre=8600) | `23bea2fe37` | 2026-10-14 22:52:52 - 23:59:31 | 102 | 582 | 684 |
| N0/n0-r3 exact (pre=8597) | `23bea2fe37` | 2026-10-15 00:14:42 - 00:35:34 | 6 | 46 | 52 |
| N0/n0-r4 | `6b6a43f2a6` | 00:38:09 - 01:00:44 (to the cutoff) | 61 | 465 (+1 load test) | 527 |

The 3 N0-scan forecasts logged in 00:49-04:26 (matches < 9,658) and the 5,019 forecasts before 00:49 are not affected (5,019 + 318 + 19,975 + 736 + 527 = 26,575).
`23bea2fe37` began logging at 22:52:52, 21 s before its first answer to a member (the new instance grades while the old
one still serves). Forecasts after the cutoff (`6b6a43f2a6` to 01:11, `a310d76b55` and later) are not in this export.

Every forecast also saves its own `odds_policy` (e.g. `odds-source-2|tp-first-2`), `mapping_rev` (team-alias / matching
revision; old mapping states kept in picks.db `mapping_revisions` for replay) and `odds_sig`; from `6f6e2d39d7` a change
of any of them is a new forecast row. **Alias changes before this were NOT versioned:** commit dd75d0a (13:54 UTC,
team_aliases.json: BB Team / MongolZ / EA Copenhagen) changed odds selection for some lines under tag `822a6b4505`.

| Tag | Commit | First committed (UTC) | Grading-behaviour group | Evidence / note |
|---|---|---|---|---|
| `110c4ed1da` | f9c2072 | 2026-10-07 03:11 | G1 frozen | The freeze: spread x1.2, continuity fix, Over margin 1.0/0.6, rest +4%, LAN/online-only stats |
| `38c4271737` | 535d0b4 | 2026-10-07 03:18 | G1 frozen? | Admin veto backtest only (hltv.py) - not re-tested for identity |
| `2dff48ab19` | 0c3a745 | 2026-10-07 03:20 | G1b | Map predictions calibrated (weight 0 in the grade); BO1 LAN/online via HLTV events - can change the environment for BO1 titles |
| `d408f04949` | 4bfb153 | 2026-10-07 03:31 | G1b | Map 1 display record (display only) |
| `34af63e159` | f105ce0 | 2026-10-07 ~04:31 | G1b | Role lookup added to hltv.py (board/slips only) - same grading |
| `d408f04949` | 5bdc59c | 2026-10-07 04:35 | G1b | hltv.py restored exactly - same tag |
| `6021f88807` | 33c01c7 | 2026-10-07 05:01 | **G2** | BUG FIX (owner-approved): last 10 in tonight's environment reaches back a year - changes inputs |
| `9abfe8cf5f` | 856f12b | 2026-10-07 05:14 | **G3** | BUG FIX completed: last 10 = usable series only - changes inputs |
| `e4a0a0d278` | acc882a | 2026-10-08 00:44 | G3 | Logging only: raw Over/Under/push and fix_v2 comparison - live output identical to 9abfe8cf5f on 6,000 cases |
| `11edec8d68` | d4397e3 | 2026-10-08 00:59 | G3 | Refactor only: Maps 1-3 mixing as named functions (mix_p_more_live / mix_three) - live output identical to the frozen grader, fix_v2 identical to e4a0a0d278, on 6,000 cases each; hand-calculated Maps 1-3 example reproduced exactly |
| `f80baad46e` | 5a3ced6 | 2026-10-08 01:02 | **G4** | BUG FIX (owner): a total exactly on a whole line is a push - the last-10 record's n leaves pushes out (form fight / tape fight). Changes grades on WHOLE lines only; x.5 lines identical to G3 |
| `beb4b5ba0c` | ea74078 | 2026-10-08 01:12 (push time below) | **G5** | Environment revision UNDER EVALUATION: no online +6% when the base rate is fully online stats (the +6% was fitted on all-maps projections). Comparisons on identical inputs: online_none (old) and online_taper (provisional). Changes grades for fully-online players only; tonight's replayable board: Over bets 26 -> 10. Not claimed to improve accuracy |
| `4a9d15b1fa` | b24c4ed | 2026-10-08 22:10 | **G6** | PROJECTION UPGRADE: performance model perf-1 (perf.py) drives Maps 1-2 kills + HS projections and spreads; Map 1 / Maps 1-3 stay on the old pipeline. Old pipeline logged beside every forecast as `legacy`. Evidence before deploy: held-out HLTV months (80% intervals 79-83%), 600 verified board lines (Maps 1-2 projection error 5.08 -> 4.83). Final confirmation: matches from 2026-10-09 on |
| `2391c7f756` | (perf-1 transparency) | 2026-10-08 22:21 | G6 | Labels only: Maps 1-2 grades falling back to the old pipeline (no HLTV data) say so; the kills cap is labelled when it changes the shown chance; raw model chance kept. Grades identical to 4a9d15b1fa |
| `25e9e825c6` | (candidates) | 2026-10-08 22:30 | G6 | Comparison-only candidates added (perf2_hs, perf-1-bo3, then perf_map1 at 22:42 - log only); grades identical to 2391c7f756 |
| `457dc4bfab` | 09bcdd3 | **2026-10-08 22:51 (live)** | **G7** | RELEASE, PROVISIONAL: perf2_hs headshot distribution live on Maps 1-2 HS; perf-1-bo3 live for players HLTV lacks; one forecast per card (simulation's overall chance = the grade's). NOT promoted: Map 1 candidate (tied/worse). Rollback: redeploy commit 8441f0f (tag 25e9e825c6). 59/59 production forecasts replay identically |
| `a64083e3c2` | 0c25277 | **2026-10-11 (live, Render)** | G8.5e (same grading) | lambda_maps12 = Maps 1-2 equivalent on every scope (reporting field). Grades identical to fa0ff604d2 - pool. Host moved from Railway to Render (cs2-scout.onrender.com); HLTV database rebuilt on Render Postgres from the Oct 8 export |
| `af09e740c8` | 413ff92 | **2026-10-11 (live, Render)** | G8.5e (same grading) | Transient-failure hardening (hltv.py / cs2_scout.py in the hash): pooled connections, half-open breaker, failed lookups never cached, HLTV blip = flagged transient grade and the board keeps its last grade or hides the line. grade_leg output on identical inputs unchanged (identity tests). Grades identical to a64083e3c2 - pool |
| `fe063dc2e9` | 1c539e2 | **2026-10-11 (live, Render)** | G8.5e (same grading) | Outage hardening follow-up (review): HLTV's own LAN/online label or transient; lost stake inputs = transient; tainted context rebuilt soon; pool 16 + probes one at a time. grade_leg output on identical inputs unchanged. Grades identical to a64083e3c2 - pool |
| `694348fd49` | f883259 | **2026-10-11 (live, Render)** | G8.5e (same grading) | Stored copy of every HLTV answer on the data disk: an HLTV outage (also across a restart) grades from the last good answer - identical output. Tag now also hashes hltv_store.py. Grades identical to a64083e3c2 - pool |
| `313626ab10` | 4724395 | **2026-10-11 (live, Render)** | **G8.5f** | OWNER-DIRECTED FIX: regional extension tag from the roster (3+ players of one country / grouped region; North America grouped); HLTV team flag only as fallback. Changes the regional series for opponents whose org flag differed from the roster (e.g. NRG Canada -> North America) - not pooled with G8.5e for regional-series cohorts |
| `e94c632358` | cd56756 | **2026-10-12 (live, Render)** | **G8.5g** | OWNER-DIRECTED FIX: regional extension search reaches 200 past opponents (was 40), last 12 months; mixed rosters get their region (3+ players); thin country tags filled with same-region series as context only (never a grade input). perf-2 grades identical (regional is context there); older-model fallback grades can change where more same-country series are now found |
| `0d6b117256` | 4c176a4 | **2026-10-12 (live, Render)** | **G8.5h** | OWNER-DIRECTED: Europe stays on country flags - no Europe region tag or European region fill-in (supersedes that part of e94c632358, which was live for minutes). 200-opponent search + 12 months kept. perf-2 grades identical (regional = context) |
| `c80ac64bc8` | 8584249 | **2026-10-12 (live, Render)** | **G8.6** | BUG FIXES (audit, each refuted independently + regression test): same-match 3U cap really holds; forfeited maps out of last-10 / KPR; HLTV id with no maps -> BO3.gg provisional, not pro average; stand-in rules on every match path; exact team matching in settlement / H2H; benched not 'same roster'; H2H rows on HLTV numbers. Changes stakes (cap) and some thin-history / stand-in grades; perf-2 projections otherwise identical |
| `a310d76b55` | ea50391 (pushed 2026-10-15 01:10:17 UTC; the new version answered from 01:11:30); server-only follow-up (tag unchanged): card / board / match table stake by the match id, graded_sides for the 'no longer offered' label | 2026-10-15 | **G8.9c** = G8.9b + outage repairs | Owner 2026-10-15: (1) odds policy odds-source-3 (E2): a Thunderpick blip with a fresh, correctly matched fallback (existing Polymarket / OddsPapi rule) grades on that fallback - not transient, provenance.blip recorded, fallback_reason "Thunderpick quote not current (feed blip) - fallback odds used", its own source and quote age, market effect as recorded; only a blip with no eligible fallback is held (E1). Was: every Thunderpick-listed match held during a blip (market_odds.select marked any fallback transient), which froze those lines on stale / old-version grades while 429 cooldowns lasted 30 min. (2) No automatic esport/cs2 probes (thunderpick.AUTO_POLL = csgo); budget, cooldown, backoff cap and Retry-After unchanged. (3) A side the feed does not offer: board / card / match table say "Unavailable—side no longer offered" (kept) or "…side not offered"; excluded from bets, best plays, the same-match cap, copying and every slip path; the grade stays recorded. Report separately from `6b6a43f2a6`. Offline end-to-end harness on the final merged code (old = the pre-audit live code): 0 unexplained differences in all 10 scenarios, 64/64 lines grade the same on old inputs; covered: fresh Thunderpick, stale with an eligible fallback (E2: graded on Polymarket, not held), stale with no eligible fallback (held, E1), a graded Under the feed then dropped ('no longer offered'), recovery (delayed by the progressive cooldown, E2 bridging). The production fallback path had not occurred naturally at release close. |
| `77d2cdf508` | c27b83e (pushed 2026-10-16) | 2026-10-16 | **G8.9c (grading code unchanged)** | Thunderpick poll pace only: after a 429 the endpoint carrying quotes stays at 150 s (inside the 180 s quote life) for 6 h instead of returning to 60 s when the 30-min cooldown ends (2026-10-15 22:11-00:08 UTC: Thunderpick usable ~37 of 120 min - every cooldown re-tripped the provider within 2-13 requests). Grader, performance model, odds selection and verdict code identical; the market input can be Thunderpick more often (fewer rate-limit gaps). Report separately from `a310d76b55` (exact-tag rule). Same commit, outside the tag: the member site shows no unsure labels (owner 2026-10-16); slip studio studio-2 builds the count asked for. |
| `9cfcbd217a` | 8672c92 (pushed 2026-10-16) | 2026-10-16 | **G8.9d** | OWNER-DIRECTED (2026-10-16): Thunderpick quote life 180 s -> 75 min (odds up to about an hour old are shown AND graded - bookmaker price, 1.5U market-quality cap), and the HLTV scraper's saved read (thunderpick_snapshots, one read per props run from GitHub, hltv-scraper 593ffeb) is taken in like the poller's own answer when it is newer. Why: Thunderpick's edge refused this server on every request from 2026-10-16 00:08 UTC (shared Render IP). More grades can carry a Thunderpick market input (and its 1.5U cap) than under `77d2cdf508`; grader / performance-model / verdict code unchanged. Report separately. |
| `6b6a43f2a6` | 1baad4c (pushed 2026-10-15 00:36:38 UTC; the new version answered from 00:38:11) | 2026-10-14 | **G8.9b** = G8.9 + two post-deploy fixes | Post-deploy checks of `23bea2fe37` (owner-approved second deploy): (1) N0 rule n0-r4 - a match with no raw page is never the original set (3 'cito' rows dated 2026-10-14 had entered without the rule; read-only production check: pre 8,597 exact, N0 8,642, 0.93 s); (2) the outage card's saved-grade lookup: the match id decides when both sides know it (Underdog listed WBT vs ENCE at 14:00, BO3.gg 12:00: 3 of 12 sampled blip cards said 'no saved grade' although the board kept one). Only performance-model projections of players / opponents with a no-raw-page match move (offline delta vs d1eef32: projection fields only, no side / grade / stake change in the fixtures; every other difference is the revision label or Thunderpick harness timing - thunderpick.py identical). Same grading behaviour as G8.9 otherwise; report the tags separately. 928 tests. Post-deploy verification: pin N0/n0-r4 pre=8597, no_raw_page 0 (the 3 'cito' rows now have raw pages); bounded smoke test 40 members, 160/160 HTTP 200, card p95 1.68 s. |
| `23bea2fe37` | d1eef32 (pushed 2026-10-14 22:51:51 UTC; the new version answered from 22:53:13; superseded by `6b6a43f2a6`) | 2026-10-14 | **G8.9** audit fixes + N0 input pin | OWNER-APPROVED AUDIT RELEASE (every fix has a regression test that failed on `6f6e2d39d7`). GRADING CHANGES: (1) performance-model input pinned to N0 (rule n0-r3, fail closed; a reconstructed pre-repair input policy, not an exact historical restoration; validated read-only on production) - controlled rollback of the unintended backfill activation, restoring the approved input policy, not a claimed accuracy gain (table above; mixed revisions labelled per component); (2) inputs: one series never counted twice in last-10 / H2H (both teams' canonical identity + same day + ordered Maps 1-2; ambiguous look-alikes pair only on identical per-map kills, otherwise quarantined; a BO3.gg series listing one map twice is quarantined and HLTV's record counts once), empty BO3.gg scoreboards no longer cached for good, tonight's LAN/online label only from the graded match, environment last-10 over the 12 months before the match, namesake / academy / AWP role / opponent / book opponent by canonical team ids + verified aliases (never substrings), LAN/online blend idempotent on cached contexts (old pipeline), stored copy keeps its query window, a failed HLTV read is transient (never a silent switch to BO3.gg rows), roster confirmation from the graded match's active lineup (unknown stays unknown), rank band by exact names; (3) grader: a performance-model line is never NO GRADE for fields that model doesn't use (sample gate 0U), 50.0% ties by the model's raw chance, no flags claiming unapplied old-pipeline steps, BO3 Maps 1-3 simulation scripts 2-1 / 1-2 only; (4) odds: a Thunderpick blip (event exists, quote stale / endpoint empty / rate limited / failing) is transient like an HLTV blip with NO time limit - board keeps the line's last grade or hides it, card notice, never logged (owner decisions 2026-10-13/14); recovery inside the unchanged budget (429: progressive backoff with Retry-After as its minimum and a persisted 429 streak, so a sustained 429 stays at the 30-min cap; a failed poll waits for both the cooldown and its rate-limit step; one request in flight; never-event endpoint probed every 30 min); fallback odds by canonical team ids + aliases (no squads / academies), live / ended Polymarket events skipped, exact stat names on every feed path incl. the PrizePicks backup copy; (5) sides: Less offered only where the platform feed offers it for that exact line (board AND card); (6) provenance only: detail.market_effect records whether the selected odds changed the projection, probability or stake; (7) end-to-end harness findings, fixed: run_player overwrote the player's slug with the team page's (the AWP hit-group check read the wrong stats - live before the audit), one org-affix rule across hltv / matchid (+ Clan, E-sport: BO3.gg 'FaZe Clan' = HLTV 'FaZe'), a genuine BO3.gg-only rematch is kept when its HLTV look-alike is already paired, and an outage card has no grade of its own: the board's saved grade of exactly this pick (player, match, platform, stat, maps, line, market type, side still offered) with its original time, marked stale, else data unavailable (no side, chance or stake); its temporary projection, chances, ladder, simulation and comparison are withheld; a kept grade is never a slip-studio / shadow-batch / recommended pick and never 'verified' on a member slip (display / slips only - no forecast is logged during an outage). Replay of 910 saved grader inputs: 0 changes in projection, probability, side, grade or stake as saved; on 5,501 perturbed variants every difference is attributed (788 NO GRADE -> graded, 26 0U tie sides), 0 unexplained. Offline end-to-end comparison on identical fixtures (scratchpad e2e/refresh.sh: history, identity, dedup, environment, odds, cold/warm caches, HLTV outage + recovery, Thunderpick stale / empty / recovery, published forecasts): 0 unexplained differences in all 10 scenarios; the 2x2 regrade shows every grade change comes from input assembly (58/58 lines); planted-change controls are detected. Not a production result. Not pooled with G8.8. |
| `6f6e2d39d7` | 411b91f | 2026-10-13 (this release; first forecast time in the release report) | G8.8 odds source `tp-first-2` | Coordinated Thunderpick release. NO projection-formula change for members: perf-2 still takes no market input in any scope. Changes: conservative eligibility (empty / missing / stale / started / BO2 quotes never used), odds re-checked at every regrade (context reuse no longer keeps a stale market input up to 30 min), one selection policy odds-source-2 (Thunderpick -> Polymarket -> OddsPapi live for members / cache for the board -> no market), exact market probability, per-map chance by series format (BO1 / BO5 change the OLD pipeline only - none since 2026-10-11), no false 'projection lowered' market flags on perf lines, pick team = the team he plays for (UNO MILLE label), forecast record with odds_policy / mapping_rev / odds_sig / git_commit, rr-1 shadow candidate logged (FACTOR_AUDIT round 6: PASS -> shadow only). Replay of 945 current-version production forecasts: 0 differences in projection, probability, side, grade or stake. CORRECTION (2026-10-14 audit): the by-format per-map chance DOES change old-pipeline BO1 / BO5 grades whenever the source lists no map prices - always with Thunderpick (that replay's inputs carried Polymarket map prices, so the by-format branch never ran): with map prices removed 48/48 saved BO5 old-pipeline projections and 7 of 47 BO5 Maps 1-3 verdicts change; old-pipeline fallback grades still occur on every scope. |
| `17e5e85837` | 370d63c | 2026-10-13 14:00:16 (first forecast; deploy 48d6b68 13:59:49) | G8.7 odds source `tp-first-1` | Thunderpick restart restore only - grading logic unchanged from `822a6b4505`. At startup each endpoint's last full snapshot is restored from the history with its own retrieval time and hash, only within the 180 s quote age, so a deploy no longer drops current quotes (13:39 UTC: 91 lines fell back to Polymarket for a minute after a restart). |
| `822a6b4505` | b680f05 | 2026-10-13 13:48:42 (first forecast; deploy 646b764 13:48:19) | G8.7 odds source `tp-first-1` | Thunderpick quote freshness only - grading logic unchanged from `4f00a300a1`. A quote is current by its own snapshot's age (<= 180 s): a 429 cooldown no longer voids a fresh quote, one empty answer between full ones no longer wipes it, a feed that stays empty clears its matches; provenance (retrieval time, snapshot sha256) is the quote's own snapshot. Effect: fewer Thunderpick -> Polymarket -> Thunderpick source flips (each flip is a logged forecast); never an older quote. |
| `4f00a300a1` | 3736157 | 2026-10-13 13:38:55 (first forecast; deploy 7fa35e4) | G8.7 odds source `tp-first-1` (policy activated 13:33:00 under `acbe039647`) | Thunderpick ingestion (fixtureId as source id) - selection logic unchanged from `acbe039647`. Policy activated on live evidence: endpoint `esport/csgo` carries CS2 (61 events, competition Counter-Strike; `esport/cs2` empty); main market 'Match Winner', no draws; startTime = UTC (25 matched events, 100% at offset 0); series meaning vs exchange prices of the same matches: 15, MAE 0.0235, corr 0.984. Effect: Maps 1-2 perf-2 projections do not use the win probability - the switch changes their market-quality category (bookmaker rule: SOFT, 1.5U cap) and the shown chance; Map 1 / Maps 1-3 (old pipeline) projections move with p_win. Activation comparison on identical saved inputs: 204 forecasts, 0 grade / stake / side changes (none above 1.5U). Evaluate by `inputs.odds_provenance.selected` / `forecasts.odds_source`. |
| `acbe039647` | 7ad66bd | 2026-10-13 13:28:01 (first forecast; deploy 9b3b550) | G8.6 (same grading until the odds policy activates) | Odds-source selector (market_odds.py) + Thunderpick ingestion/matching (thunderpick.py) join the hash. Odds policy `tp-first-1`: Thunderpick's margin-adjusted series probability replaces the Polymarket input ONLY when thunderpick.policy() is active (timezone and series-winner meaning established from evidence, exact canonical match, current pre-match two-way quote); otherwise the existing Polymarket rule, with the fallback reason saved. While inactive, grades are identical to `1088faf157` (the selector adds only `odds_provenance`, which grade_leg never reads - test_market_odds). Activation is recorded in thunderpick.db (policy_events) with an old-vs-new comparison on identical saved inputs; evaluate forecasts by `inputs.odds_provenance.selected`. |
| `1088faf157` | 0913e67 | **2026-10-12 (live, Render)** | G8.6 (same grading) | Settlement identity by HLTV team id (hltv.py in the hash): settles picks, does not grade them. Grades identical to c80ac64bc8 |
| `fa0ff604d2` | b587208 | 2026-10-11 | G8.5d (same grading) | hltv.py: pooled-endpoint connection (Neon). Grades identical to 70a8247c14 - pool |
| `70a8247c14` | ded1a9b | 2026-10-11 | G8.5c (same grading) | sim.py: Maps 1-3 scripts over three played maps (labels only; chances come from the grade's distribution). Grades identical to 6f26c18ce4 - pool |
| `6f26c18ce4` | d309c5a | 2026-10-11 | G8.5b (implementation fix) | Cached player context now carries the match day and BO format into finish_line: the G8.3 match-day rule applied on fresh grades only - cached ones used today's date |
| `ea44921fd9` | 440708d | 2026-10-11 | G8.5 (Maps 1-3 fix) | Maps 1-3 = maps 1-3 in full: HLTV model x 3 maps (held-out: kills bias -0.59, 80% range 81.9%), no BO3 47% map-3 discount, no 2-map mix; BO3 2-0 voids; BO5 maps 4-5 never count. Map 1 / Maps 1-2 unchanged |
| `c997d119be` | 01503f2, again from a0ece47 | 2026-10-10 to 2026-10-11 | G8.4 (HLTV first) | Map 1 on the HLTV performance model (tied BO3.gg-based: MAE 3.135 vs 3.137 on 186 lines); stake-check inputs HLTV first (AWP share, last map date, Maps 1-2 count), BO3.gg backup. Live again from a0ece47: the PrizePicks reader moved to prizepicks.py (not hashed) and PrizePicks lines are graded on the board - same grading |
| `dd398b4876` | 973bbc8 | 2026-10-10 ~21:30Z to the next deploy | G8.4 + PR #5 (same grading) | PR #5 put the PrizePicks reader in hltv.py (in the hash) - card context only; grades identical to c997d119be. Pool with G8.4 |
| `12f83a9da0` | 1aff90c | 2026-10-10 | G8.3 (implementation fix) | Performance model day = the match day (as in the backtest); live had used today's date, so maps played earlier today were left out until midnight UTC and projections jumped at 00:00 UTC. Member wording / stake display changes are site-only |
| `aedb7b7446` | b398261 | 2026-10-09 to 2026-10-10 | G8.2 (same grading) | Benched players (BO3.gg status 2) recorded on the player block for display (match table, team-kills panel). 595/595 production forecasts replay identically |
| `757f51be04` | c13dc7b | 2026-10-09 19:50Z | G8.2 (same grading) | Search speed-up only (hltv.py index): ~7 ms instead of ~110 ms per distinct search. 618/618 production forecasts replay identically. Rollback: 675a017 (405653024c) |
| `405653024c` | d8f7a80 | 2026-10-09 19:28Z | G8.2 (same grading) | Map predictions (context only) prefer the event's verified pool (latest finished BO3 veto at the same event), else an inferred pool labelled as such. Grades identical to 1118ff120e: 199/199 production forecasts replay identically. Rollback: 9c37c7b (1118ff120e) or bf13754 (G8.1) |
| `1118ff120e` | 56bf17a | 2026-10-09 19:15Z | **G8.2** | Consolidated release. GRADING CORRECTNESS: odd-decimal lines (e.g. 30.2) no longer get a push band - continuity cut floor + 0.5 for every non-whole line; whole and x.5 lines identical to G8.1 (replay below). Everything else is not a grading change: impossible-line validation, active map pool for map predictions (context only), search sharing, data-status wording, diagnostics, shadow M1 ("match_m1"), slip load log. Rollback: redeploy bf13754 (G8.1 `83b2b67d6b`, verified to hash to that tag) |
| `83b2b67d6b` | b9637d1 | 2026-10-08 23:35 - 2026-10-09 | **G8.1** | BUG FIX on G8: the HLTV team lookup matched exact names only, so Underdog's "Team Vitality" / "Chicken Coop Esports" found no HLTV opponent and 99 of 866 performance-model forecasts since G6 (11%) used an average opponent (Falcons, Aurora, Spirit, Chicken Coop, SINNERS, FURIA, Vitality, Ground Zero). Now exact-match name variants (leading "Team", trailing Esports / Gaming / Club), also for the namesake tie-break. Changes the opponent effect on those lines only. Rollback: commit 9d747e0 (G8) or 4cbd3cb (G7) |
| `4527c7974b` | 9b8642a | 2026-10-08 23:20 | **G8** | RELEASE: perf-2 (newcomer prior 0.93, HS shrink 25 rounds; spreads re-measured on TRAIN) on Maps 1-2 kills + HS and the BO3.gg fallback (perf-2-bo3, provisional). Held-out HLTV VALID/TEST: kills MAE -0.035/-0.033, HS MAE -0.035/-0.036 (95% intervals below 0); board lines same direction within noise. fix_v2 live on the legacy paths (Map 1, Maps 1-3, Maps 1-2 without HLTV): gap centred on the line, Maps 1-3 push removed after mixing (4 of 177 G7 forecasts change, Map 1, <= 2.9 points). Simulation samples the grade's own distribution. Slip builder batch-3 (shadow). Replay: 177/177 G7 production forecasts identical with fix_v2 off and perf-1 inputs. Rollback: redeploy commit 4cbd3cb (G7 `457dc4bfab`) |
| `c3505e489e` | none found | 2026-10-07 03:13-03:18 | unknown | 8 member grades; no commit on any branch reproduces this tag - not replayable, report separately |
| `f0fdc11ece` | none found | 2026-10-07 03:09-03:12 | unknown | 4 member grades; same - not replayable |
| untagged | before 2026-10-07 ~03:00 | - | pre-freeze | Old grader; the model_version column did not exist yet |

Comparison versions logged beside every forecast (`shadow`), from the same grading run:
- `old_spread`: c 1.55 / 1.30.
- `no_rest_bonus`: rest bonus 0.
- `fix_v2`: gap compression centred on the line; Maps 1-3 Over/Under/push mixed before the push is removed. LIVE from G8;
  from G8 the comparison is `pre_fix_v2` (the old behaviour).
- `perf1_g7` (from G8): the perf-1 projection (G7 parameters and spreads) on the same HLTV inputs - the forward test of perf-2.
- `prior_smooth` (from 2026-10-09): the smooth newcomer prior (see below). INVALID until the 2026-10-13 fix: graded by the old
  pipeline (its version was not in perf_versions, so it equals `legacy`). Valid rows carry `shadow.prior_smooth.source` ending
  `-prior-smooth`; exclude every other row and report per exact tag (CHALLENGERS.md). Same tags - picklog.py is not hashed.
- `match_m1` (from G8.2): the shared-match challenger (match_model.py, constants fitted on TRAIN) - shadow only.
- `online_none` / `online_taper` (from G5): old +6%-always behaviour / provisional taper 6% x (1 - online weight); `live_online` records each forecast's online weight and the bonus applied.
- `no_bo3`, `perf1_normal_hs` (from G7): the release without the BO3.gg fallback / with the perf-1 normal HS curve.
- `perf_map1` (from 22:42 Oct 8): the Map 1 candidate.
- `legacy` (from G6): the old projection pipeline on identical inputs.
- `live_raw`: the live model's own Over/Under/push chances before the kills cap and the Over margin.

Still open (not fixed live or in fix_v2):
- The Map 1 / BO1 model (half of the Maps 1-2 projection).
- The displayed chance mixes the model's chance with the Over margin (legacy paths only; the card explains the gap to the simulation).
- Whole-line chances are conditional on no push. That is valid, but they must not be shown as unconditional or
  multiplied into slip all-hit chances without handling pushes.

## 2026-10-09 newcomer-prior review (grading unchanged, version aedb7b7446)
- Implementation check: the live path uses perf.PARAMS (prior_m 0.93). Overall rate, environment blend and prior all use the
  same time weights (half-life 120 d, strictly earlier days); k0 300 / ke 800 are in weighted rounds. The prior reaches
  everyone through the shrinkage weight k0/(r+k0) by design. There is no newcomer-only rule applied too broadly, so there is no implementation error to fix.
- Challenger `prior_smooth` (shadow, picklog only): target m(r) = 1 - 0.07 * 800/(800 + r). (m0, T) was chosen on TRAIN kills MAE.
  On development data (VALID/TEST) it ties the current model and is not better: MAE +0.003 to +0.009, log-likelihood about +0.000 to +0.003.
  On verified board lines, log-loss is +0.0005 (kills) and +0.0014 (HS) vs current.
  The previous prior 1.0 is worse for thin-history players (test MAE +0.157, 95% +0.070 to +0.236).
  Decision: keep prior_m 0.93 live and log prior_smooth beside it on every forecast. Not promoted.
  2026-10-13: the LOGGED prior_smooth shadow was the old pipeline until the fix (316/316 Oct 8-11 forecasts = shadow.legacy);
  only rows with a `-prior-smooth` source are evidence about this challenger (CHALLENGERS.md).

## 2026-10-09 headshot calibration check (grading unchanged)
- Board HS lines, Maps 1-2 (169 lines, 51 matches, Oct 3-8 only): Overs came in 8.7 pts below predicted (95% -17.9 to +1.0).
  Not significant. Every probability band's 95% interval contains the prediction.
- Inputs check out: the Underdog HS settlement equals the HLTV count on 169/169 lines.
  Predicted HS/kill 0.523 vs actual 0.518. The +0.45 HS over-projection on those lines is mostly the kills projection (+0.67 kills, contributing +0.32 HS) plus +0.14 HS from the per-kill ratio. Neither is significant.
- HLTV held-out (2,965 player-series, Aug 13+): HS projection -0.25 (slightly LOW, 95% -0.45 to -0.05).
  Randomised PIT: middle 50% holds 52.2%, middle 80% holds 81.5% (kills: 51.0% / 82.2%).
  At synthetic lines around the projection, predicted vs actual Over rates are within 0-4 pts.
- Status: possible Over-probability overstatement on development board data; not confirmed. Broader historical checks are
  encouraging. Forward validation remains pending. The reasons board offers behave differently from the wider HLTV sample are
  unresolved, and the highest band (5 lines) can't be judged yet. No rebuild.
- Forward test (predefined): board HS Maps 1-2 evaluation snapshots logged from 2026-10-09T23:40Z, same bands
  (<0.35, 0.35-0.45, 0.45-0.55, 0.55-0.65, 0.65+ chance of Over). Current model vs previous model (perf1_g7) and prior_smooth,
  with match-grouped 95% intervals. Shown on the admin diagnostics page under "Forward check".
  The prior_smooth line there counts only rows graded by the challenger itself (shadow source ending `-prior-smooth`).
  Rows logged before the 2026-10-13 fix are excluded and shown as invalid.
