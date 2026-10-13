# Thunderpick partner odds - integration status and provider questions (2026-10-13)

## Status
- **Policy `tp-first-2`** (this release; replaces `tp-first-1`, which activated itself at 13:33 UTC on price agreement
  alone). Active only when ALL hold: the event-level "Match Winner" market is enabled by the label rule (two outcomes,
  no draw price ever seen); its **meaning is tested, not assumed**: on BO3 matches Thunderpick's price must fit the
  exchange SERIES price better than a map-1 reading (per-map chance q, p = q^2(3-2q)) by >= 0.01 mean absolute error on
  >= 15 BO3 matches; the startTime timezone is **inferred** from matched fixtures; the sanity gate (price agreement,
  >= 15 matches, MAE <= 0.06, corr >= 0.85, BO2 excluded) passes. Nothing here is provider-confirmed. Live check at
  release: 13 BO3 matches, series reading MAE 0.024 vs map-1 reading 0.036 - favours "series", below the 15-match
  minimum, so Thunderpick is held out of projections (fallback to Polymarket, reason saved) until the evidence suffices.
  Every activation / deactivation / change of enabled formats is appended to `policy_log` with the evidence snapshot.
- **What Thunderpick changes, per scope.** The live projection (performance model perf-2) takes NO market input in any
  scope (Map 1, Maps 1-2, Maps 1-3): the selected price changes only the market-quality stake cap (a bookmaker price is
  SOFT -> max 1.5U; a liquid Polymarket book is HARD), the shown win chance and the model-derived per-map chances
  (match-simulation scenarios). The market-informed projection rr-1 / RR-KS (FACTOR_AUDIT round 6) runs in SHADOW on
  every forecast (shadow.market_rounds) - logged, not shown, not graded.
- Full grades take the market input from `market_odds.select` (odds-source-2), re-checked at every regrade: Thunderpick
  when `projection_odds` finds an **eligible** quote (see Eligibility), else Polymarket, else OddsPapi (live for member
  contexts, cache-only for board contexts), else no market - the reason saved (`inputs.odds_provenance.fallback_reason`,
  with the availability state). Never 50%.
- **First real forecasts using it (`tp-first-1`):** forecasts 5867-5873 (13:40:11 UTC, version `4f00a300a1`): Legacy vs
  PARIVISION, 2026-10-14 13:30 UTC (`hltv:2399386`, Thunderpick fixture `fx:3297270`). Example below.
- Moneylines: a Thunderpick block on each matched card (decimal odds, raw implied, margin, margin-adjusted probability,
  our retrieval time, availability in plain words, link); Thunderpick-only matches (no exchange) get their own card;
  Polymarket and Kalshi stay as labelled comparisons.
- Single Render instance (persistent disk); HTTPS with certificate verification; the key is only in the Render
  environment (`THUNDER_PICK_API`) and never appears in logs, diagnostics, pages, the history file or the repository.

## What the feed contains (measured, not provider-confirmed)
| item | finding |
|---|---|
| endpoint | `/esport/csgo` carried the CS2 matches (59-61 events, `competition` "Counter-Strike"); `/esport/cs2` answered 200 `[]` on every request so far. **Measured behaviour, not a provider statement** (question 1) |
| main market | event-level `name` = "Match Winner" on all events; no `drawOdds` on any event |
| per-map markets | separately labelled in `markets[]`: "Map N Winner" (two selections) and "1X2 - Map N" (three, incl. "Draw"), both on the same events as "Match Winner" |
| fields beyond the guide | `game`, `tournament`, `fixtureId` (36-37 of ~60 events). Source event id: `thunderpick:fx:<fixtureId>` when present, else `thunderpick:url:<number in matchUrl>` (namespaced since this release; older history rows hold `fx:<id>` / the bare number and are read as the same ids) |
| startTime | fits **UTC+0**: inferred from 25 matched fixtures (100% at offset 0 against HLTV start times). **Inferred, not provider-confirmed** (question 8); the evidence keeps accruing after resolution |
| matchUrl | `https://thunderpick.io/match/<id>` only; links are accepted only in exactly that form (query / fragment dropped) |
| other markets | round handicaps and totals (match and per map), pistol rounds, correct score, maps played; per-map player kill / death Over/Under markets on a few events; overtime wording ("Incl. / Excl. Overtime") only on some labels |
| quote time / status | none published - we show our retrieval time (our request's send time) and never call it the quote time |

## Market-type registry (`thunderpick.market_types()`, admin view)
Every observed market label gets an entry: level (event-level main market vs a `markets[]` entry), scope (series
winner / map N / unclassified), outcome counts, draws possible, overtime treatment (from the label text, else n/a),
enabled series formats, `enabled`, `basis`, `provider_confirmed` (**False for all** - no provider statement yet) and,
for the main market, the price agreement with exchange prices by series format (bo1 / bo3 / bo5 / bo2 / unknown, with
first / last dates). Entries are appended to the append-only `market_evidence` table as frozen JSON when a label first
appears or its classification changes (the main market's evidence snapshot at most hourly).

**Enabled in this release: only the event-level "Match Winner" market, scope series winner.** Basis: explicit label
"Match Winner" (event-level main market) with per-map markets separately labelled "Map N Winner" / "1X2 - Map N" in
the same events; exactly two outcomes; no draw price on any observed event (counted over the whole history); not
provider-confirmed. Formats: BO1, BO3, BO5 (BO2 and unknown excluded - a draw is possible / the format is unknown).
In a BO1 the series winner IS the map-1 winner (recorded on the entry and in provenance). Price agreement is
supporting evidence and a sanity gate only. A draw price observed on any "Match Winner" event disables the type.
Map markets, "1X2 - Map N" and the player markets are never used: per-map chances are derived from the series price,
never mixed from another source. Using them would be a new grading input that needs its own chronological test.

## Eligibility (availability)
A quote feeds projections only when its availability is **`current`**: its event is in its endpoint's latest
**successful** answer, that answer is **nonempty** and **<= 180 s old** (from that answer's retrieval time), the market
type is enabled, the format allowed, two valid odds, no draw price, the policy active and the canonical start in the
future. `projection_odds` and the Moneylines block (`board_block`) use the same decision. Otherwise:

| availability | meaning |
|---|---|
| `endpoint_empty` | the endpoint's latest successful answer listed no events - earlier quotes are display / history only (no holding through empty answers) |
| `not_in_latest_answer` | the event is absent from its endpoint's latest successful nonempty answer (checked by presence, even when re-matching fails) |
| `stale` | the latest successful answer is older than 180 s (failures / cooldown). A failed request neither extends nor voids anything by itself |
| `started` | canonical start <= now; canonical start = the earliest of the HLTV start, the caller's start and the converted Thunderpick start |
| `scope_unconfirmed` | the market type is not enabled (e.g. a main market other than "Match Winner") |
| `draw_listed` | a draw price is listed |
| `format_excluded` | BO2, unknown format, or HLTV and caller formats disagree |
| `invalid_odds` | the event's odds are invalid |
| `policy_inactive` | the policy gate is off (timezone, sanity gate, switch) |
| `unlinked` / `ambiguous` | no exact canonical match / two Thunderpick events on one match |

Display keeps each quote's own retrieval time and snapshot hash - never renewed - with plain words, e.g. "Last seen
14:20 UTC - Thunderpick's latest answer listed no CS2 markets; not confirmed now". Production evidence for the change:
csgo answered 60 events at 14:13:04 and 200 `[]` at 14:14:05 (no 429 before it), and three empty answers in a row
from 14:20:10; the old rule kept the previous quotes eligible until they were 180 s old, across repeated empty answers.

## Coverage (`/admin/thunderpick.json?unmatched=1`, read-only)
At 13:52 UTC: 60 events, 36 linked to a canonical match. The 24 others fail only on team names that are not HLTV
names (exact names and maintained aliases only - never fuzzy). Most are tier-3 / qualifier teams that are not on
our board. Three were spellings of HLTV teams and are now maintained aliases with same-fixture proof
(`team_aliases.json`, `dd75d0a`): **BB Team = BETBOOM** (Furia vs BB Team, 2026-10-14 16:00 = HLTV 2399387 - its board lines
were falling back to Polymarket), **MongolZ = The MongolZ**, **EA Copenhagen = EAC**. Left unmatched on purpose:
academies onto main teams (e.g. Vitality Academy), and "Proycon vs Hacha eSports" (CCT South America, 2026-10-13 16:00),
which has the same start and opponent as our "9z vs Hacha" but a different team name - possibly the same match, not
provable from the data. Open board matches without a Thunderpick price fall back to Polymarket with the reason saved.
After the aliases: 41 of 60 events linked; BetBoom vs FURIA priced from Thunderpick (`fx:3297271`, 17 forecasts by
14:02). The view's board side reads each open pick's latest saved provenance, because the stored pick label can differ
from the team the grader links. Correction (audit 2026-10-13): the 'UNO MILLE' label was **not** a PrizePicks name -
PrizePicks and Underdog list ALLE under 'ex-Keyd Stars'. `picks.team` held ALLE's BO3.gg profile team ('UNO MILLE');
UNO MILLE is a separate HLTV team (id 11555, last seen 2026-09-04). The grader played ALLE for ex-Keyd Stars (HLTV
13423) vs Galorys (12677), `hltv:2399204`, 21:00 UTC - the match Polymarket priced as `cs2-keydst-gls1`; settlement keys
on player / opponent ids and dates, not on the label.

## Real forecast (forecast 5873, pick 1350, version `4f00a300a1`, policy `tp-first-1`)
zweih (PARIVISION) kills, Maps 1-2, line 27.5 vs Legacy, 2026-10-14 13:30 UTC.

| | Polymarket source (forecast 5719) | Thunderpick source (forecast 5873) |
|---|---|---|
| market input | PARIVISION +141 / Legacy -141 (41.5%), map prices 0.475 / 0.41, quality HARD | PARIVISION 2.15 / Legacy 1.62 decimal; raw implied 46.5% / 61.7%, margin 8.24%; margin-adjusted **43.0%**; maps derived; quality SOFT |
| projection | 25.15 kills | 25.15 kills |
| Less chance | 62.6% | 62.6% |
| grade / stake | B, 0.5U Less | B, 0.5U Less |

Provenance saved with the forecast (`tp-first-1` shape): `source thunderpick`, `policy tp-first-1`, `market Match Winner`,
`scope series winner`, `match_id hltv:2399386`, `source_event_id fx:3297270`, `api_path /api/partners-odds/esport/csgo`,
`retrieved_at 2026-10-13T13:40:08Z`, `snapshot_sha256 41bdb078…`, `source_updated_at null` (the feed has none),
`fallback_reason null`; forecast `odds_source thunderpick`, `data_rev matches=28191;max_id=2399456`.
All 7 forecasts reproduce exactly from their saved inputs, and re-grading them with the Polymarket fields swapped back
gives the earlier forecasts - only `team_ml`, `opp_ml`, `map_win_probs`, `market_quality` differ.
`tp-first-2` provenance adds: `market_id` (event-level main market), `scope_basis`, `scope_provider_confirmed`,
`series_format`, `draws_possible`, `fixture_id`, `url_id`, `tournament`, `competition`, `source_team1/2`, `team_is_a`,
`flipped`, `implied_opp_raw`, `p_opp`, `quote_age_s`, `event_first_seen`, `event_last_observed`,
`endpoint_last_success_at`, `endpoint_last_nonempty_at`, `availability`, `start_raw`, `start_utc_from_source`,
`canonical_start`, `timezone_basis`, `timezone_provider_confirmed` (always false). `team_prob` is exact (6 dp); the
American odds are for display.

**Why the projection did not move:** Maps 1-2 lines use the performance model, which does not read the win
probability (`grader.py` replaces the projection with the performance lambda). The switch changes the market-quality
category (bookmaker prices are SOFT under the existing rule -> stakes capped at 1.5U) and the shown win chance. Map 1
and Maps 1-3 lines do NOT move either: the performance model drives every scope (Map 1, Maps 1-2, Maps 1-3) and takes
no market input; only lines graded on the old pipeline (performance input missing - none since 2026-10-11) use the win
probability in the projection (corrected 2026-10-13; an earlier version of this note was wrong). Activation comparison
on identical saved inputs:
204 forecasts, 0 grade / stake / side changes.

## Polling and protection
- One gateway (`request()`) for every Thunderpick request. ONE atomic reservation (SQLite `BEGIN IMMEDIATE` on the
  persistent disk) checks and takes: no cooldown, **no other request in flight** (a lease, expires after 60 s), **>= 20 s
  since the last request**, and the shared budget of 4 requests / 60 s for every caller (poller and admin probe).
  Shared by the threads and processes of one host; Render runs one instance on the disk and deploys were serial in the
  request log - two hosts could not share it.
- `request_log` is append-only: a `started` row before sending, a `completed` row after (same `attempt_id`), a
  `refused` row for every refusal (cooldown, in flight, spacing, budget, no key, authentication stop). `request()` never
  raises; a failure to write the audit never turns a 200 into an error.
- 429 -> Retry-After when given, else 60 s x 2^(n-1) x 0.8-1.2 jitter, capped at 30 min (jitter before the cap), where
  n = the 429s of the last 30 min - a persisted, decaying count that an empty 200 or a probe success does **not** reset
  (production 13:38:06 429 then 13:39:07 `[]` used to wipe it). 5xx / timeout / network -> 30 s x 2^(n-1); three
  authentication failures stop all requests and raise one alert until the key changes.
- Both endpoints polled automatically: 60 s each; slower after repeated empty answers (120 / 180 / 300 s) and while
  429s persist (120 / 240 / 300 s by the 429s of the last 30 min - it relaxes only as they age out). Schedule, cooldown,
  429 memory, budget and lease survive restarts.
- Status reads (`/admin/thunderpick.json`, the Moneylines state, `status / public_state / market_types / policy`) never
  call the provider and write nothing. The probe is a separate explicit admin action (`thunderpick.probe(name)`,
  through the same gateway); a `probe=` parameter on the status URL is ignored.
- Public state: `quotes_confirmed_at` = the retrieval time of the answer serving current quotes (None when none);
  `last_success_at` / `last_nonempty_at` of the endpoint carrying CS2 (no max over the always-empty endpoint).
- Restart: each endpoint's last nonempty answer is restored from the history with its own retrieval time and hash
  (body found by hash, else rebuilt from its stored event rows); a LATER successful empty answer makes it display-only;
  older than 180 s -> display-only (stale); older than 3 h -> not restored. Per-event first-seen times come from the
  event history. Verified for `tp-first-1` on the 14:04 UTC deploy (the new instance served the 14:03:56 snapshot until
  its own first poll at 14:04:57).

## Storage
- **Polling history** (`raw_snapshots`, `events`): bounded segment files `CS2_DATA_DIR/.cache/thunderpick_history/
  history-<time>.db`, a new one at 200 MB (`THUNDERPICK_SEGMENT_MAX_MB`); whole oldest segments are deleted when all
  segments pass 2000 MB (`THUNDERPICK_SNAPSHOT_MAX_MB`). Inside a segment rows are append-only (triggers). A status row
  per attempt; event rows for every changed answer; the gzip body of a changed answer at most once per 10 min per path.
  Restore finds the latest answer's body by hash across segments and keeps its original retrieval time and hash.
- **Never rotated or pruned** (`thunderpick.db`): gateway state (budget, lease, cooldown, 429 memory), the append-only
  `request_log`, `market_evidence`, `policy_log`, and the pre-segment history.
- **Replay never depends on the polling history:** every forecast saves the selected quote itself (odds, teams, start,
  source ids, retrieval time, snapshot sha256, availability) in its grader inputs; pruning removes supporting detail only
  (tested: restore after pruning + restart, and replay with the history deleted).

## Observed rate limiting and empty answers
| time (UTC) | path | result | context |
|---|---|---|---|
| 12:56:06, 12:57:33 | csgo | 429 | cs2 polled at <= 4 / min |
| 12:56-13:02 | cs2 | 2 x 429 of 9 | <= 4 / min |
| 13:38:06 | csgo | 429 | previous csgo 61 s earlier, one cs2 request between (about 2 requests / min in total) |
| 13:39:07 | csgo | **200 `[]`** | the next request after that 429 (61 s later); the request after it returned 59 events |
| 14:14:05 | csgo | **200 `[]`** | no 429 before it; the 14:13:04 answer had 60 events |
| 14:16:06 | csgo | 429 | no request in the preceding 60 s - our own rate does not explain it |
| 14:20:10, 14:21:10, 14:22:11 | csgo | **200 `[]`** x 3 | consecutive empty answers after 60 events at 14:19:09, no 429 before them; `cf-cache-status DYNAMIC` |

No `Retry-After` or `X-RateLimit-*` headers have been returned. 429s occur well below the documented 10 / minute.

## Remaining questions for the provider (ready to send - contains no key)
> Hi - a few questions about the CS2 partner odds feed (aggr.mightykiwi.xyz):
> 1. CS2 matches arrive on `esport/csgo`; `esport/cs2` always returns `[]`. Is `csgo` the intended identifier for CS2,
>    or should `cs2` be populated for our account?
> 2. We get occasional HTTP 429 at about 2 requests per minute (e.g. 2026-10-13 13:38:06 and 14:16:06 UTC on
>    `esport/csgo`), below the documented 10 per minute. Is the limit per IP, per key, per path or per account? We call
>    from Render (shared outbound IPs). Could you send `Retry-After` / `X-RateLimit-*` headers?
> 3. Some requests return 200 with `[]` between full answers (13:39:07 right after a 429; 14:14:05 and 14:20:10 -
>    14:22:11 UTC without one). Is an empty 200 ever a throttling or partial response?
> 4. Is `fixtureId` stable for the life of a match, and why is it missing on some events (36 of 59)? Does `matchUrl`'s
>    number ever change?
> 5. Is there a quote update time or a live / suspended / closed flag we can read? Are in-play events in this feed, and
>    are events still listed after their start?
> 6. "1X2 - Map N": what does the X (draw) outcome mean for a single CS2 map?
> 7. Can `matchUrl` use any host or path other than `https://thunderpick.io/match/<id>`?
> 8. Which timezone is `startTime` in (it matches UTC+0 so far), and does it change with daylight saving?
> 9. Does the event-level "Match Winner" market always settle on the series (match) winner, with overtime included,
>    for every format (BO1 / BO3 / BO5)? Can a BO2 event carry a draw price?
