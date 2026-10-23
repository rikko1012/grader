# AU-1 - audit of the real offered P1 lines (identity, settlement, duplicates, freshness, leakage)

Registered in `REGISTER.md` (commit 9d8333a) before anything was computed. Diagnostic only: nothing fitted, no live code
changed, no production write. Scripts: `audit/slim_forecasts.py`, `audit/build.py`, `audit/analyze.py`; result file
`audit/results/analysis.json`; bug test `audit/bug_tests/test_au1_duplicate_units.py`.

## Data

| Source | What | Notes |
|---|---|---|
| P1 units export | `learning/p1_units.json`, stored cycle at **2026-10-23T04:29:55Z**: 2,922 units / 170 match keys (169 real matches, see D1), starts 2026-10-11 11:30 to 2026-10-22 22:00 UTC; Underdog 1,817 / PrizePicks 1,105; kills 12 1,360, HS 12 1,245, kills 1 150, HS 1 127, Maps 1-3 40 | the UT-1 paged pull (limit 2000, 0.3 s). Mixed tags (794a775f51 737, 9cfcbd217a 609, 6f6e2d39d7 302, c36104d079 287, a310d76b55 273, f663af8917 230, others 484). History revision: N0 pinned 2,266, N backfill 332, N0 before backfill 311, partial 13. **230 units / 15 matches start after the 2026-10-22 00:00 cutoff**: they are read here for data checks only, and no model is compared on them |
| Saved forecasts | each unit's forecast row with decoded grader inputs (`forecasts_page.json inputs=1`, UT-1 pull, cutoff id 79170, 0 missing) | the identity fields: `perf.hltv_player_ids`, `round_share.team_id`, `hltv_opp_team`, odds query names, `hltv_error`; also `quote`, `data_rev`, `cm2_live.inputs` |
| Forecast log | all 80,675 forecast rows without inputs (inventory pull), streamed down to id / pick / time / line / quote fields | repeated forecasts and later lines of the same pick or the same book line id |
| HLTV | read-only local copy of the production DB (inventory, 2026-10-23T04:38Z), matches 2026-10-05..10-25 | settlement uses the production read (`hltv._SERIES_SQL`: `player_stats` kills, `map_headshots` HS, final scores including OT). `map_player_stats` is the cross-check. Codex's key/score reconciliation was not redone |
| Scraper archive | `prop_grades` (last line per book line id, with the scraper's own HLTV grading) | joined by the quote's book line id from 2026-10-15, by exact nick + start + market + maps before that |

Metrics: favoured-side hit rate (p > 0.5 means Over; the 12 units at exactly 0.5 are left out of the hit rate only), log loss
and Brier of P(Over). The intervals are 95% match-grouped bootstrap intervals (B = 2000, seed 20261022). Effects are given as (a) affected
minus unaffected and (b) the cohort without the affected units minus the whole cohort (paired on the same resample).
**When a class sits in 1-3 matches, its bootstrap interval cannot be interpreted.** Those rows give counts only.

**Baseline (all 2,922 units):** hit **51.17%** (2,910 with a side) against a mean printed favoured-side chance of 55.90%. Log loss
0.7018, Brier 0.2541.

## Summary

| Class | Units / matches | Hit on them | Log loss | Effect of removing them on the P1 cohort (hit; log loss) |
|---|---|---|---|---|
| **Settlement**: our settled total vs HLTV | 2,904 checked, **0 differ** | - | - | none |
| **Leakage**: any input not knowable before the forecast | 0 | - | - | none |
| D1 one prop counted twice (two match keys) - **bug** | 8 / 1 | 12.5% | 0.730 | +0.11 pts; -0.0001 |
| I1 projection on a namesake's history (bug already fixed 10-21) | 2 / 1 | 0% | 1.064 | +0.04 pts; -0.0002 |
| I3 HLTV player-name alias missed, so the player fell back to BO3.gg | 19 / 10 | 52.6% | 0.711 | -0.01 pts [-0.17, +0.16]; -0.0001 [-0.0007, +0.0005] |
| I4 BO3.gg fallback although HLTV has the player (N0 pin) | 67 / 14 | **25.4%** | **0.836** | **+0.61 pts [+0.15, +1.19]; -0.0031 [-0.0063, -0.0008]** |
| I5 match never linked to HLTV (team alias / missing) | 18 / 2 | 16.7% | 0.783 | +0.21 pts; -0.0005 |
| **All identity/duplicate defects (X1 = I1-I5, D1)** | **106 / 25** | **28.3%** | **0.809** | **+0.86 pts [+0.31, +1.55]; -0.0040 [-0.0073, -0.0014]** |
| X2 = X1 without I4 | 39 / 13 | 33.3% | 0.762 | +0.24 pts [-0.07, +0.63]; -0.0008 [-0.0021, +0.0002] |
| F3 line moved after the P1 forecast (outcome re-scored at the closing line) | 93 / 55 | 41.9% | 0.721 | re-scored at the closing line: hit -0.03 pts [-0.17, +0.07], log loss +0.0002 [-0.0003, +0.0009] (2 outcomes flip) |

Bottom line: the defects are real but small. The clean cohort (2,816 units / 166 matches) hits **52.03%** while printing 55.77%.
Data and implementation problems explain about 1 point of the ~4.7-point gap between printed and realised. Settlement
and leakage are clean. Most of the defect effect comes from the BO3.gg-fallback route (I4 + I3 + D1 + I5 are all
perf-2-bo3 lines). As a whole, that route hit 28.3% on 106 units / 25 matches, a development observation.
H18 had already described this cohort as tier-3 heavy underdogs; it is not a pre-match rule.

## 1. Identity

Each unit's resolved HLTV player id (from the saved inputs) was checked against the match scoreboard, and its team id and opponent id against
the match's two teams. Where the forecast's id is not on the scoreboard, the real player was found by nick among that match's 10
players. Results for the 2,904 units with an HLTV match:

* **2,809 units: forecast id = the player on the scoreboard**, on the forecast's team (0 team mismatches). The opponent id
  matched the match's other team in every unit that had one (0 mismatches). The HLTV match date equals the start date in all 2,897
  hltv-keyed units. 0 forecast ids or pick ids are used by two units.
* **I1 namesake projection (2 units, 1 match: Lucky / Envy vs HEROIC, 2026-10-15, a310d76b55).** The projection read HLTV id
  13843 (a Danish 'Lucky', last map 2026-07-19 for Washed / Denmark). The player was 13497 (220 maps in the last 12 months).
  His first Envy map was that same day, so no candidate was "on the team", and the old tie-break (all-time maps: 440 vs
  ~431 then) picked the veteran. **This is fixed in f663af8917** (180b6e8, 2026-10-21 audit: among unconfirmed candidates, the most maps
  in the last 12 months decides). On today's data that rule picks 13497 (220 vs 27). No new test: the rule is SQL in `hltv._player_ids`.
* **I2 / I3 player-name aliases (19 units, 10 matches).** In each case the player's nick on BO3.gg differs from his HLTV nick, so the N0 lookup
  finds nobody and the line is graded on perf-2-bo3 (BO3.gg maps). dev1ce = HLTV 'device' (6 units; a known gap and an owner decision);
  **Techno4K = HLTV 'Techno' (9 units, The MongolZ; new)**; brain = HLTV 'Brain47' (4 units). In the brain case the lookup
  resolves to HLTV id 5615 'brain', who has no maps in 420 days (I2). The settled totals are right in all 19 (they equal HLTV for the real player).
* **I4 BO3.gg fallback while HLTV has the exact nick (67 units, 14 matches).** `perf_live.player_maps` reads N0 only. A player whose HLTV maps all lie outside N0 is "not found in HLTV", so perf-2-bo3 grades the line. In this group, HLTV
  had 6-274 earlier maps of the player in the 420-day window (e.g. iDISBALANCE 274, MITHPUTTINI 161, Lcs 118, rx 106). The
  fallback returns before `_cm2_live`, so **cm-2 never sees these players**, even after the cm-2 releases (13 kills Maps 1-2 units after 10-20 01:14,
  5 HS Maps 1-2 units after 10-21 19:18). `_cm2_live` already resolves an opponent outside N0 on the full database
  (`perf_shadow._team_id`), but takes the player only from the N0 read. MODEL_VERSIONS lists "the BO3.gg fallback (perf-2-bo3)" as
  unchanged by cm-2, so this follows the spec. It is a design gap, not an implementation bug. **Effect:** hit 25.4% (17/67) against
  51.8%, a difference of -26.4 pts [-40.5, -10.0]; log loss +0.137 [+0.054, +0.214]. Favoured side: Over 41 (hit 10), Under 26 (hit 7).
* **I5 match never linked to HLTV (18 units, 2 matches; their settlement cannot be checked against HLTV).** 'rooster-vs-orgless-aust-12-10-2026' is HLTV
  2399145 'Rooster vs LFO 8': 10 units saved no player identity (perf-2-bo3 / early a64083e3c2 rows), so no join is possible.
  'martyrdom-vs-markandlarry-12-10-2026' (8 units) is HLTV 2399144 'MARKandLARRY vs Fruju'. Those 8 units are
  duplicates of hltv-keyed units (D1), and their twins' totals equal HLTV.
* **Team-name aliases seen:** 'LFO 8' (Orgless Aust), 'LFO 10' (Sökerorg, FORZE Reload 17:00 on 10-14; resolved here
  by hand to 2399301: KusMe 43 = 22+21, Lack1 23 = 13+10 match HLTV), 'Fruju' (Martyrdom), 'LOS NINOS' (Los Niños, accent),
  and **the LP vs BARCA case**. HLTV renamed the LP roster BARCA (team 14107) for 2026-10-21; PrizePicks listed 'LP vs paiN',
  and HLTV 2399805 is paiN vs BARCA. **0 P1 units come from it:** none of its 20 PrizePicks line ids reached our forecast log. The scraper
  graded the 5 LP players on 2399805 and left the paiN players pending ("HLTV has this player's team vs BARCA").
* **Stand-ins / roster:** 33 units (17 matches) carry stand-in or not-on-listed-team flags: hit 53.1%, no difference. 26 units / 6 matches
  are a player's first HLTV match for this team: 50.0%. 172 units / 37 matches have `roster_confirmed False`: 45.3% against 51.5%,
  -6.2 pts [-15.0, +2.9]. These are risk markers, not errors. Benched players never appear as settled units.

## 2. Settlement

* **2,904 of 2,904 checkable units: the settled actual equals HLTV's total** for the line's scope, map numbers 1 / 1-2 / 1-3
  by HLTV map order. 2,809 were checked on the forecast's id, 69 by exact nick, 15 by alias (device / Techno), 4 by prefix
  (Brain47) and 7 by the hand-linked LFO 10 match. `map_player_stats` agrees with `player_stats` / `map_headshots` on every unit, and the scraper's
  independent `prop_grades` actual agrees on all 2,593 joined units. 0 units have a player who missed a map in scope.
* **Overtime:** 792 units / 44 matches have an OT map inside the scope, and their totals include OT (HLTV full-map scoreboard).
  Hit 48.6% against 52.1%, -3.5 pts [-10.1, +2.9]: descriptive only (see CX-OT). The data cannot show whether each book counts OT. The
  scraper's grading includes OT too, and every line settled the same way.
* **Forfeits / remakes / incomplete scoreboards:** 0 P1 matches with `forfeited_maps` > 0 or a `data_issues` row.
* **Maps 1-3:** 40 units, all in BO5 series (20 in 5-map series; maps 4-5 correctly excluded). No Maps 1-3 line on a BO3 in
  P1, so the BO3 2-0 void rule was never exercised. BO1 matches (261 units) carry only Map 1 lines. 16 Map 1 lines are
  on BO3s.
* **Pushes / voids:** no P1 unit has actual == line, and `over == (actual > line)` holds for all 2,922 (asserted). Voids are not in the
  export, so they could not be audited from P1. In the scraper archive, voids are 'did not play' / 'did not play map N'.
* **Edge, 0 P1 cases:** `picklog.scope_result` voids a line when the HLTV match has any forfeited map. That includes a forfeited map 3 for a Maps 1-2
  line whose maps 1-2 were played. Whether the books void in that case is not established, so it is noted, not flagged.

## 3. Duplicates

* **D1 - bug (8 units, 1 match).** BRACE, micalis, sav and yourwombat (MARKandLARRY, 2026-10-12 09:00) are scored twice: once under
  'martyrdom-vs-markandlarry-12-10-2026' and once under 'hltv:2399144'. 6 of the 8 pairs carry the identical line and chance.
  `identity.assign` links an hltv-<id> record to a BO3.gg slug only when both name the same normalised team pair
  ('Martyrdom' and 'Fruju' do not). `learning.latest_rows` drops a copy only when the two units share pick ids, and they cannot, because
  the pick key contains the slug. The cohort is inflated by 8 units and 1 match. Failing test:
  `audit/bug_tests/test_au1_duplicate_units.py::test_same_player_same_start_under_two_match_keys_is_one_unit`. A guard
  test that keeps FORZE Reload's two series on 2026-10-14 (14:00 and 17:00) apart passes.
  **Fix proposal (separate version, re-key per `_migrate_unit_identity`):** add an identity.assign rule 5 (RULE_VERSION 5).
  An 'hltv-<id>' record and a BO3.gg-slug record that share a player (pick `slug` / `player_id`) and his team name, with starts
  within 60 minutes, are one match (`hltv:<id>`). Two BO3.gg slugs are still never merged, and each hltv id joins at most
  one slug. 60 minutes is shorter than any series, so same-day series 3 hours apart stay separate.
* **Across books:** P1's unit is player x canonical match x stat x scope with no book in the key. Of the 2,134 units with a saved
  quote, 1,037 were offered on both books at forecast time and count once. On 524 of them the other book's line differed; P1
  keeps whichever book's forecast changed last. Both-book units hit 52.5% against 50.4%, not different.
* **Repeated forecasts:** a pick has a median of 7 saved forecasts (1,147 picks have 10 or more). P1 takes one: the latest one logged at least 30 minutes before the
  start. 1,248 P1 picks have later forecasts inside the last 30 minutes, which are correctly excluded. No HLTV match appears under two keys apart from D1.

## 4. Freshness

* **Lead time** (start minus forecast time): median 66 minutes, 10th-90th percentile 34-341 minutes, range 30-1,347. A forecast row is written only
  when something changes, so the lead is the time since the forecast last changed. The line and the inputs were still current at T-30.
* **Feed age at logging** (from 2026-10-15): board read at most 5.0 minutes before the forecast, every target anchor `feed_state ok`.
  HLTV inputs are at most 15 minutes old (2,198 units with the field). Thunderpick quotes are at most 75 minutes old (the configured quote life); 184 units
  were graded during a Thunderpick blip. perf lines take no market input, so that changes stake caps only: hit 53.8%, no difference. No stale-feed
  forecast was found. Before 10-15 there is no quote, so feed age is unknown.
* **Lines that moved after the forecast:** 93 units / 55 matches. In 78 the scraper's last line differs (name join, mostly before 10-15).
  In 18 a later forecast on the same book line id carries a new line. Re-scored at the closing line, only 2 outcomes change.
  The effect is -0.03 pts [-0.17, +0.07] in hit rate and +0.0002 in log loss. 49 of the 78 moves went toward our projection.
* **Long leads (descriptive):** forecasts logged more than 3 hours before the start (652 units / 65 matches) hit 45.1% against 52.9%: -7.8 pts
  [-13.7, -1.6], log loss +0.028 [+0.006, +0.051]. The Over rate swings between bands (0.38 at 3-6 hours, 0.59 above 6 hours), so the result is
  driven by a few matches. No staleness or leakage mechanism was found behind it. A test would need its own registration.

## 5. Leakage (all 0)

| Check | Units checked | Violations |
|---|---|---|
| perf history: newest map day < match day (`newest_map_day`) | 2,816 | 0 (1,399 used a map from the day before) |
| cm-2: feature day = match day; opponent-adjustment refit before the match day | 600 | 0 |
| cm-2 history: the player's map count in its inputs vs HLTV maps dated in [D-420, D) | 600 | 0 above the pre-day count (266 equal; 334 lower because of later ingestion or the completeness filter) |
| realised per-map numbers in the saved inputs (`per_map`) | 2,922 | 0 |
| HLTV reads (`hltv_data.as_of`, `hltv_series.as_of`) or odds `retrieved_at` after the forecast time | 2,922 | 0 |
| forecast logged after the Thunderpick-listed start (102 units differ from our start by more than 5 minutes) | 1,926 | 0 |
| HLTV match dated before the forecast day; scraper `found_at` before the forecast | 2,897 | 0 |

No saved field holds a realised value. Realised rounds, the final veto and the closing line are absent (the line is the line offered when
the forecast was logged; `map_probs` is a prediction). Elo comes from the cm-2 tables at the start of the match day; that is by construction and was not recomputed here. The model coefficients (cm-2
fitted through 2026-09-30; perf-2 constants tuned before Oct 2026) predate every P1 match.

## Fix proposals and follow-ups (none applied; each needs its own version / registration)

1. **D1** (bug): identity.assign rule 5 as above, with RULE_VERSION 5 and the ledger re-key. The test is ready.
2. **I4 routing** (design gap): when the N0 read finds no player, resolve him on the full database (as `_cm2_live` already does for
   the opponent) and let cm-2 forecast Maps 1-2. This is a model-routing change on a cohort whose history is development data, so it must go through
   a registered shadow first, not ship as a bug fix.
3. **Player aliases** (I3): a verified BO3.gg player id to HLTV player id table, like the team aliases. Techno4K = Techno and brain
   = Brain47 are new; dev1ce stays with the owner's decision.
4. **Team aliases** (I5 / LP): verify and add 'LFO 8' = Orgless Aust, 'LFO 10' = Sökerorg, 'Fruju' = Martyrdom and LP becoming BARCA (2026-10-21) to
   the maintained alias list. The scraper's 10 pending paiN lines on 2399805 need the same alias.
5. **Forfeit edge** (0 cases): check the books' rules before changing `_forfeited`'s whole-line void.
