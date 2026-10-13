# Thunderpick partner odds - integration status and provider questions (2026-10-13)

## Status
- **Live and feeding projections.** Policy `tp-first-1` activated itself at **13:33 UTC** once the evidence below was
  met. Full grades take the market win probability from `market_odds.select`: Thunderpick's proportional
  margin-adjusted series price when the match links exactly and the quote is current, else the existing Polymarket
  rule with the reason saved (`inputs.odds_provenance.fallback_reason`). Never 50%.
- **First real forecasts using it:** forecasts 5867-5873 (13:40:18 UTC, version `4f00a300a1`): Legacy vs PARIVISION,
  2026-10-14 13:30 UTC (`hltv:2399386`, Thunderpick fixture `fx:3297270`). Example below. By 14:05 UTC: 273
  Thunderpick-sourced forecasts (142 lines, 7 matches), every one with its snapshot hash and no map prices from another source;
  no line switched back to Polymarket while its quote was current. Current version `17e5e85837`.
- Moneylines: a Thunderpick block comes first on each matched card (original decimal odds, raw implied, margin,
  margin-adjusted probability, our retrieval time, status, link); Polymarket and Kalshi stay as labelled comparisons.
- Single Render instance (persistent disk); HTTPS with certificate verification; the key is only in the Render
  environment (`THUNDER_PICK_API`) and never appears in logs, diagnostics, pages or the repository.

## What the feed contains (measured, not assumed)
| item | finding |
|---|---|
| endpoint | **`/esport/csgo` carries the CS2 matches** (59-61 events, `competition` "Counter-Strike"); `/esport/cs2` answers 200 `[]` on every request so far |
| main market | `name` = **"Match Winner"** on all events; no `drawOdds` on any event |
| fields beyond the guide | `game`, `tournament`, `fixtureId` (36-37 of ~60 events) - `fixtureId` is our source event id when present, else the number in `matchUrl` |
| startTime | **UTC**: 25 matched events, 100% at offset 0 against HLTV start times |
| series meaning | "Match Winner" vs exchange prices of the same matches: 15 matches, MAE 0.024, corr 0.98 -> series winner |
| matchUrl hosts | `thunderpick.io` only |
| other markets | round handicaps and totals (match and per map), map winners, "1X2 - Map N", pistol rounds, correct score, maps played; at least 21 per-map player kill / death Over/Under markets (Map 1 / Map 2, a few events) |
| quote time / status | none published - we show our retrieval time and never call it the quote time |

Only "Match Winner" is used (series scope, established). Map markets, "1X2 - Map N" and the player markets are kept
out of projections: per-map chances are derived from the series price, never mixed from another source. Using the
player markets would be a new grading input that needs its own chronological test - not part of this release.

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
14:02). The view's board side reads each open pick's latest saved provenance - a PrizePicks label can differ from the
HLTV team the grader links (PrizePicks 'UNO MILLE' = HLTV 'ex-Keyd Stars' vs Galorys, 21:00 UTC: the same match
Polymarket priced as `cs2-keydst-gls1`; Thunderpick ex-Keyd 2.47 -> 37.5% vs Polymarket 32.5%).

## Real forecast (forecast 5873, pick 1350, version `4f00a300a1`)
zweih (PARIVISION) kills, Maps 1-2, line 27.5 vs Legacy, 2026-10-14 13:30 UTC.

| | Polymarket source (forecast 5719) | Thunderpick source (forecast 5873) |
|---|---|---|
| market input | PARIVISION +141 / Legacy -141 (41.5%), map prices 0.475 / 0.41, quality HARD | PARIVISION 2.15 / Legacy 1.62 decimal; raw implied 46.5% / 61.7%, margin 8.24%; margin-adjusted **43.0%**; maps derived; quality SOFT |
| projection | 25.15 kills | 25.15 kills |
| Less chance | 62.6% | 62.6% |
| grade / stake | B, 0.5U Less | B, 0.5U Less |

Provenance saved with the forecast: `source thunderpick`, `policy tp-first-1`, `market Match Winner`,
`scope series winner`, `match_id hltv:2399386`, `source_event_id fx:3297270`, `api_path /api/partners-odds/esport/csgo`,
`retrieved_at 2026-10-13T13:40:08Z`, `snapshot_sha256 41bdb078…`, `source_updated_at null` (the feed has none),
`fallback_reason null`; forecast `odds_source thunderpick`, `data_rev matches=28191;max_id=2399456`.
All 7 forecasts reproduce exactly from their saved inputs, and re-grading them with the Polymarket fields swapped back
gives the earlier forecasts - only `team_ml`, `opp_ml`, `map_win_probs`, `market_quality` differ.

**Why the projection did not move:** Maps 1-2 lines use the performance model, which does not read the win
probability (`grader.py` replaces the projection with the performance lambda). The switch changes the market-quality
category (bookmaker prices are SOFT under the existing rule -> stakes capped at 1.5U) and the shown win chance. Map 1
and Maps 1-3 lines (older pipeline) do move with the win probability. Activation comparison on identical saved inputs:
204 forecasts, 0 grade / stake / side changes.

## Polling and protection
- Both endpoints polled automatically from the server: 60 s each, slowing to 120 / 180 / 300 s after repeated empty
  answers (cs2 is at 120 s now), >= 20 s between any two requests, one shared budget of 4 requests / 60 s for every
  caller (poller and admin probe), all persisted on the disk (schedule, cooldown, budget survive restarts).
- 429 -> Retry-After when given, else 60 s x 2^(n-1) +-20% jitter; 5xx / timeout / network -> 30 s x 2^(n-1); three
  authentication failures stop all requests and raise one alert until the key changes.
- Every request sent is an append-only audit row (time, path, caller, instance, commit, status, duration, sanitized
  headers incl. `cf-ray`). Admin status: `/admin/thunderpick.json` (last attempt, last success, next attempt, blocker).
- Freshness (`822a6b4505`): a quote is used only while its own snapshot is <= 180 s old. A cooldown does not void a fresh
  quote; one empty answer between full ones does not wipe it; a feed that stays empty clears its matches.
  Provenance is always the quote's own snapshot.
- Restart (`17e5e85837`): each endpoint's last full snapshot is restored from the history (own retrieval time and hash,
  only within 180 s). Verified on the 14:04 UTC deploy: the new instance served the old instance's 14:03:56 snapshot
  (41 matches, restored) until its own first poll at 14:04:57; no forecast changed source. Before (13:39 deploy) 91
  lines fell back to Polymarket for about a minute.

## Observed rate limiting
| time (UTC) | path | result | context |
|---|---|---|---|
| 12:56:06, 12:57:33 | csgo | 429 | cs2 polled at <= 4 / min |
| 12:56-13:02 | cs2 | 2 x 429 of 9 | <= 4 / min |
| 13:38:06 | csgo | 429 | previous csgo 61 s earlier, one cs2 request between (about 2 requests / min in total) |
| 13:39:07 | csgo | **200 `[]`** | the next request after that 429 (61 s later); the request after it returned 59 events |

No `Retry-After` or `X-RateLimit-*` headers have been returned. 429s occur well below the documented 10 / minute.

## Remaining questions for the provider (ready to send - contains no key)
> Hi - a few questions about the CS2 partner odds feed (aggr.mightykiwi.xyz):
> 1. CS2 matches arrive on `esport/csgo`; `esport/cs2` always returns `[]`. Is `csgo` the intended identifier for CS2,
>    or should `cs2` be populated for our account?
> 2. We get occasional HTTP 429 at about 2 requests per minute (e.g. 2026-10-13 13:38:06 UTC on `esport/csgo`), below
>    the documented 10 per minute. Is the limit per IP, per key, per path or per account? We call from Render (shared
>    outbound IPs). Could you send `Retry-After` / `X-RateLimit-*` headers?
> 3. Right after a 429, one request returned 200 with `[]` (13:39:07 UTC) and the next returned 59 events. Is an empty
>    200 ever used as a throttling response?
> 4. Is `fixtureId` stable for the life of a match, and why is it missing on some events (36 of 59)? Does `matchUrl`'s
>    number ever change?
> 5. Is there a quote update time or a live / suspended / closed flag we can read? Are in-play events in this feed?
> 6. "1X2 - Map N": what does the X (draw) outcome mean for a single CS2 map?
> 7. Can `matchUrl` use any host other than `thunderpick.io`?
