# Thunderpick partner odds - integration status and provider questions (2026-10-13)

## Status
- **Connected, no usable markets.** The single Render instance (`srv-db5hbj2jnfac73agcj6g`, one instance because the
  service has a persistent disk) reaches `https://aggr.mightykiwi.xyz` over HTTPS with certificate verification; the key
  is accepted. The documented CS2 identifier returns **HTTP 200 with an empty array** - connectivity proven, CS2 market
  coverage NOT established.
- Nothing from Thunderpick reaches members or grading. The Moneylines tab states Thunderpick's condition in words
  (empty feed / rate-limited / unavailable / setting up) next to the attributed Polymarket and Kalshi comparisons; prop
  grading keeps its existing market input. No grade changed.
- Diagnosis controls (deployed `eaa172f`): every request goes through one gateway - a cooldown persisted on the disk
  (Retry-After honoured, else growing backoff with jitter), ONE budget for all Thunderpick paths and callers
  (4 requests / 60 s, atomic SQLite reservation on the persistent disk), automatic polling OFF, and an append-only audit
  row per request actually sent (UTC time, path, caller, instance, pid, commit, status, duration, sanitized headers incl.
  request ids, key-redacted error snippet). Status endpoints never contact the provider (verified: 0 audit rows over 16
  former polling intervals while status was read repeatedly; covered by tests).

## Evidence (UTC; credentials never included)

| time | path | caller | result | notes |
|---|---|---|---|---|
| 12:49-12:55 | `/esport/cs2` | poller (15 s) | 200, `[]` (2 of 2) | first contact from Render |
| 12:56:06 | `/esport/csgo` | admin coverage check | **429** | the first csgo request ever sent from this service; cs2 was being polled at <= 4 / min |
| 12:56-13:02 | `/esport/cs2` | poller | 6 x 200 `[]`, **2 x 429**, 1 timeout | 9 attempts in about 6 minutes (<= 4 / min) |
| 12:57:33 | `/esport/csgo` | admin coverage check | **429** | |
| 13:02-13:13 | - | - | no requests | diagnosis controls deployed; autopolling off |
| 13:13:59 | `/esport/cs2` | admin probe | **200, `[]`**, 387 ms | `cf-ray a49e9ad4582cf3c9-CMH`, `server: cloudflare`, `cf-cache-status: DYNAMIC`; no Retry-After / X-RateLimit headers |

Before 13:02 the code kept only the latest error's evidence, so the exact second and headers of the earlier 429s were not
retained (fixed: every request is now an append-only audit row). Upper bound of our traffic at the time: 4 requests per
minute on cs2 plus 2 single csgo requests - below the documented 10 per minute per IP per path. During a deploy the old
instance is stopped before the new one starts (persistent disk), so overlapping pollers are unlikely; Render's outbound
IPs are shared with other Render services in the region, which may matter if the limit counts per source IP.

## Questions for the provider (send without any key)
1. **Which identifier carries current CS2 matches: `esport/cs2` or `esport/csgo`?** `cs2` answered 200 with `[]` at
   12:49-13:14 UTC on 2026-10-13 while CS2 matches were scheduled. Is our partner account enabled for CS2?
2. **Rate limit scope:** per source IP, per key, per endpoint path or per account? Do `cs2` and `csgo` share one budget?
   We received 429 at no more than 4 requests per minute (times above); no Retry-After or X-RateLimit headers were
   returned - can they be? Our requests come from Render (shared outbound IPs): should we register an IP?
3. **Main market:** for CS2, does the event `name` ("1X2" in the guide) mean the SERIES winner? How are draws handled
   (best-of-2 series), and when is `drawOdds` present?
4. **`startTime` timezone** (UTC?) and whether it changes when a match is rescheduled.
5. **Quote timestamps / status:** is there a source update time per event or market, and a live / suspended / closed
   flag? Are live (in-play) events included in the feed?
6. **Identity:** are there stable event and market ids (beyond the number in `matchUrl`)? A tournament id beyond the
   `competition` label?
7. **Markets:** which `markets[]` names exist for CS2 (map winner, handicaps, totals, player kills / headshots)?
8. **Links:** which `matchUrl` hosts can appear (we render only `https://thunderpick.io` / `www.thunderpick.io`)?

## Next, once usable markets arrive (already authorized)
Market inventory and scope (series vs map, draws) -> canonical matching (reversed order, aliases, academies,
rematches, start-time changes; ambiguous = unmatched) -> Thunderpick-first comparison display (original odds, raw implied
and proportional margin-adjusted probabilities, margin, retrieval time, status, match link; no liquidity or execution
claims) -> one shared projection-odds source selection (Thunderpick series price for series uses, map prices only for
map uses, explicit fallback to the existing qualified source with the reason recorded, never 50%, counted once, one
snapshot per forecast, provenance saved) released as a new model version and compared with the old source on identical
inputs before activation.
