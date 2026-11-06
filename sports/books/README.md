# Sportsbook odds ingestion (sports/books)

Player-prop prices from DraftKings, FanDuel, BetMGM, Caesars and BetRivers for NBA and NFL. The ONE scheduled
collector (`python -m sports.worker loop`, source `books`) runs this package's job; everything else reads storage.
Each successful game read is written to the immutable quote history here (`books.db`) **and**, from the same answer,
to the canonical sc-1 store (`bridge.py`) that the board / UI snapshots and the model read - no second request. No browser, no disguised client and no
manual tabs are involved, and nothing a member does triggers a provider call. Isolated from CS2 and LoL like the rest
of `sports/`. **Production stays off:** this branch starts nothing on the server.

**Owner decision (2026-10-23): free sources only. No paid odds feed will be bought.** The live sportsbook
sources are FanDuel's and BetRivers' own public feeds. DraftKings, BetMGM and Caesars stay out: their sites refuse
this server, and the only legitimate route to their prices is a metered feed. The Odds API adapter stays in the
code, disabled by default even if an old key is saved. Re-enabling it requires `SPORTS_ODDSAPI=1` and a valid
`ODDS_API_KEY`; its credit ledger still enforces the configured free allowance. The scheduled `books` source
continues collecting direct FanDuel and BetRivers quotes into the canonical store. No new account or subscription
is needed for these feeds, and historical Odds API records remain intact.

This is a replacement for the failed collector, not equivalent five-book coverage: DraftKings, BetMGM and Caesars
remain unavailable unless a permitted feed is separately verified. Preserve book/jurisdiction/observation times;
never relabel FanDuel prices as another book or use prediction-market prices as sportsbook odds.

Free-provider review (2026-10-28): Odds-API.io pauses new free keys (`https://odds-api.io/pricing/free`). ParlayAPI
advertises 1,000 monthly credits and 3 credits per player-props request
(`https://api.parlay-api.com/free-sports-odds-api`), about 333 calls/month total. Its public status endpoint answers,
but authenticated NBA/NFL prop coverage is not verified here. It is a possible sparse supplement, not a replacement
for frequent direct-feed refreshes. No paid plan or signup has been performed.

## Sources

| Book | Own public feed | Documented feed (The Odds API) |
|---|---|---|
| FanDuel | `fanduel.py`: sbapi.oh.sportsbook.fanduel.com. Answers a plain, honestly identified request (Ohio, where FanDuel is licensed) | `fanduel` |
| BetRivers | `betrivers.py`: Kambi offering API, operator `rsiusoh` (Ohio). Answers a plain request | `betrivers` |
| DraftKings | Refused: Akamai "Access Denied" (403). Probed every 6 h, never worked around | `draftkings` |
| BetMGM | Refused: firewall block page (403) for a non-browser client. Probed every 6 h, never worked around | `betmgm` |
| Caesars | Refused: CloudFront "Request blocked" (403). Probed every 6 h, never worked around | `williamhill_us`. **The Odds API lists it on paid plans only** |

The Odds API is polled in ONE place: the canonical collector's `oddsapi` source (`sources.oddsapi_ingest`, credit
ledger `ledger.py`, events and players resolved to sc-1). `theoddsapi.py` keeps the parser it uses; the provider is not
registered in `providers.PROVIDERS`, so this worker never calls it (it used to - a duplicate poller of the same
credits). It needs `ODDS_API_KEY`, and without one it is skipped and says why. Its documentation (read 2026-10-23) says:
- player props come one game at a time from `/v4/sports/{sport}/events/{id}/odds`;
- cost = unique markets returned × regions (the five named books are one region; empty answers cost nothing);
- `/events` is free;
- quota headers are `x-requests-remaining`, `x-requests-used` and `x-requests-last`, and the market-level
  `last_update` is the provider time;
- plans: Starter free (500 credits/month), 20K $30, 100K $59, 5M $119;
- player-prop coverage "varies" by bookmaker. **Real coverage per book is unverified until a key is set** (see
  Verification).

## OddsPapi through the CS2 account (`oddspapi.py`): built, OFF until verified

The site already holds an OddsPapi key for CS2 (`ODDSPAPI_API_KEY`, Railway name `odds_papi_api`). OddsPapi lists
DraftKings, BetMGM, Caesars, FanDuel and BetRivers. Sports can read up to 3 of them per league in **one** request
(`/v4/odds-by-tournaments`), DraftKings, BetMGM and Caesars first: these are the books whose own sites refuse this
server. **The provider is off by default** and starts only when all three of these are set on the worker's service:
`SPORTS_ODDSPAPI=1`, the key, and `SPORTS_ODDSPAPI_MONTHLY_REQUESTS` > 0.

**Sharing the account with CS2.** CS2's `oddspapi.py` is not changed and the key is not copied anywhere.
1. **Allocation:** Sports has an explicit monthly allocation, `SPORTS_ODDSPAPI_MONTHLY_REQUESTS`. Each billable call
   first takes one request of it with a single conditional UPDATE in the Sports store, so all Sports workers
   together never exceed it.
2. **Account check:** before every billable call, Sports reads the whole account with `GET /v4/account` (not metered
   per the docs). The call is made only if the account would still have more than CS2's floor left. That floor is
   max(`SPORTS_ODDSPAPI_CS2_FLOOR`, default 50, CS2's unused share), where CS2's share = limit - Sports allocation.
   Sports stops first; it never spends CS2's share or the account's last requests.
3. **Fail closed:** if that fresh account read fails, times out, or carries no numeric `request_limit` /
   `request_count` on an active subscription, no billable request is sent. The earlier reading is never reused.
4. **No backoff on refusal:** a refusal by this guard is not a provider failure, and it is retried at the next due
   time.

**Spending:**
- The catalog (tournaments, participants, player-prop markets) costs 3 requests, cached 30 / 30 / 7 days.
- Each league read costs 1 request.
- Read cadence = Sports allocation left ÷ days left in the month ÷ leagues with games, never more often than every
  3 h.

**Retry-After:** a hold. No OddsPapi request is sent before the provider's time, and it survives a restart.

**Unverified until the owner's check** (run on the service that holds the key):
- which books this account entitles to player props for sports 11 (basketball) and 14 (American football);
- OddsPapi's player-prop market names (unknown names map to nothing and are listed, never guessed into a stat);
- whether `request_count` resets monthly.

```
python sports_scout.py books-oddspapi account     # not metered: entitlements, quota, the guard's numbers (no key printed)
SPORTS_ODDSPAPI=1 SPORTS_ODDSPAPI_MONTHLY_REQUESTS=<n> python sports_scout.py books-oddspapi catalog   # <= 3 requests
SPORTS_ODDSPAPI=1 SPORTS_ODDSPAPI_MONTHLY_REQUESTS=<n> python sports_scout.py books-verify --providers oddspapi  # 1 per league
```
Leave `SPORTS_ODDSPAPI` unset until those three answers are in and the allocation is agreed against CS2's usage
(see `request_count` in `account`).

## Adapter contract (`base.py`) - replaceable without touching Scout

```
class Provider:            name, books, leagues, kind ("feed" | "direct")
    configured() -> (bool, reason)          never fetches
    events(lg)   -> FetchResult(events=[Event(provider_event_id, league, name, home, away, start)], ...)
    props(lg, e) -> FetchResult(offers=[offer], requests, credits_used, credits_remaining, http_status, error, blocked)
```
An **offer** is one priced side of one market at one line:
- `provider, book, jurisdiction, league, provider_event_id, event_name, home, away, start`
- `player, player_key, provider_player_id, market` (canonical, `sports/markets.py`), `stat_label`
- `period` (`game` = full game incl. OT, `regulation` = excl. OT), `line, side` (`over` | `under`)
- `price_american, price_decimal, market_type` (`standard` | `alternate` | `promo`), `status` (`open` | `suspended`)
- `provider_market_id, provider_selection_id, provider_updated_at` (None when the provider publishes none)
- `offer_key`

Providers never write storage or pick schedules. A new provider is a subclass registered in `providers.PROVIDERS`.

## Storage (`store.py`)

SQLite `<SPORTS_DATA_DIR>/books.db` by default (on Render: `CS2_DATA_DIR=/data` → `/data/sports/books.db`), or
PostgreSQL when `SPORTS_DB_DSN` is set. Tables (DDL in `store.SCHEMA`, created on first connect; no migration tool
needed):

| Table | Holds |
|---|---|
| `sports_book_quotes` | **Immutable** history: a row when an offer first appears, when its price or status changes, and a `removed` row when a complete read of its game no longer lists it. `observed_at` = our observation time, `provider_updated_at` = the provider's time |
| `sports_book_offer_state` | Per offer: latest quote id, status, `first_seen_at`, `last_seen_at` (staleness is measured from this) |
| `sports_book_events` | Provider game → canonical `event_key` (`espn:<ESPN event id>`, or `unmatched:<provider>:<id>`) |
| `sports_book_fetches` | Every fetch: provider, scope (`events` / `props` / `probe`), times, HTTP status, error, offers, new quotes, removals, credits used / remaining |
| `sports_book_provider_state` | Health and backoff: cooldown, failure streak, last success / error, blocked, credits remaining |
| `sports_book_schedule`, `sports_book_lease`, `sports_book_settings` | Next due time per game, the single-worker lease, the chosen Odds API ladder |

**Is PostgreSQL required?** Only if the worker runs as a separate Render service. A Render disk attaches to one
service, so a separate worker cannot share the web service's SQLite file. Run the worker inside the web service
(supervised like the LoL worker) and SQLite on the existing disk is enough. Postgres size: see "Storage needed" below.

## Read contract (`compare.py`) - what the board, comparison views and models use

- `current(db, league, now, event_key=None, player_key=None, market=None, period="game")`: latest open or suspended
  quote per offer, with `seen_at`, `age_s`, `fresh` and `quote_id`.
- `two_sided(quotes)`: a book's Over **and** Under, both open and fresh, same provider, book, game, player, stat,
  period, market type and **the same line**, with `p_over` (proportional no-vig) and `quote_ids`. Nothing else is
  paired.
- `consensus(pairs)`: mean no-vig `p_over` over **standard** pairs from **2+ distinct books** (a book counted once:
  its newest pair), with `books`, `by_book` and `quote_ids`. This is the market view, kept apart from the model's
  chance.
- `board_index(db, league)` + `lookup(index, player_key, market, line, start)`: the entry for a pick'em line's
  **exact** line only. `cli.attach(lg, rows)` adds it to board rows as `row["books"]`.

`record.save` stores `row["books"]` (consensus, books and quote ids) in the forecast's `book_consensus` column. The
model's `chance` (`sharp-1`) is **unchanged**: the book consensus is shown and logged beside it, never folded into it.

## Bridge to sc-1 (`bridge.py`)

`bridge.sink(con, resolver_for)` is handed to `worker.run_once` by the canonical collector. Per game read:
- one sc-1 `OfferSnapshot` per (feed, book, game, player, stat, period, market type, **line**); its two sides are
  `SideQuote`s with the book's decimal prices. A side the book does not list is **not offered** (never invented);
- platform = the book for its own feed (`fanduel`, `betrivers`), `oddspapi:<book>` for OddsPapi;
- `source_updated_at` = the provider's own change time (BetRivers changedDate, OddsPapi changedAt), else `None` with
  `source_time_unknown`; `observed_at` / `known_at` = our observation time;
- event `espn:<id>` -> `<league>:espn:<id>`; players resolved exactly by the canonical resolver (unresolved = stored
  with `player_id` None and a reason, never graded);
- after a **complete** read only, that game's offers of the platform that the read no longer lists get a `removed`
  revision (never after a failed read, never on other games).
The model uses these as single-source candidates `mkt-fanduel` / `mkt-betrivers` only (REGISTER.md amendment A4);
mc-1 / mi-1 keep their registered sources. The UI shows them in every row's quotes.

## Worker (`worker.py`)

Scheduled: the sports scheduler's light `books` job (`python -m sports.worker books nba nfl`, every minute on a game day, 2026-11-06) checks `pending()` (books.db only, no request) and calls `run_once` when something is due; with `SPORTS_BOOKS_JOB=0` the canonical collector calls it each cycle as before. Manual: `python -m sports.books.worker` (ONE pass:
books, then the game-data tick) or `python sports_scout.py books-run --once`. There is no second forever-loop.
- **Lease:** one worker at a time; a second one finds the lease taken and does nothing.
- **Game lists** are re-read every 30 min.
- **Refresh ladder** (`schedule.py`): the books' own feeds use "direct" (2026-11-06): every 3 min inside 2 h, every
  5 min inside 8 h, every 8 min inside 24 h, 3-hourly inside 3 days, 12-hourly inside 7 days, so a quote stays inside
  the market chance's 15-min rule and the Tools +EV 30-min rule. ("full" - 10 / 30 / 60 min - remains one of the Odds
  API plan's choices.) Started games are never fetched. book_providers.json carries the same ladder
  (sports/tests/test_books_schedule_sync.py).
- **Markets are batched:** one Odds API call per game; FanDuel needs one request per stat tab, BetRivers one per game.
- **Rate limits:** 1 request per second per host (`net.py`).
- **Backoff:** errors back off 60 s doubling to 30 min; a 401/403 refusal waits 6 h.
- **Retry-After:** obeyed in full; every Retry-After becomes a hold on that host. `net.py` checks the current hold
  right before **every** send: after pacing, after backoff and on each retry, so a hold another request set meanwhile
  also counts. A call waits out a hold only within its own 60 s wait budget; beyond that it fails without sending.
  The worker saves holds (`hold:<host>` setting, so a restart honours them too), and the provider cools down until
  exactly that time. It is never retried early.
- **Odds API budget:** not spent here (see Sources). The credit ledger of the canonical `oddsapi` source is
  `ledger.py`; `plan.py` remains the estimate tool (`books-plan`).

## Storage needed (measured 2026-10-23 evening, real FanDuel + BetRivers data)

| Measured | Value |
|---|---|
| Offers at first sight, per NBA game | FanDuel ~1,050 (83% alternate-ladder rungs), BetRivers ~630 |
| Offers at first sight, per NFL game two days out | FanDuel ~65, BetRivers ~170 (both post more nearer kickoff) |
| Share of offers whose price changed between two fetches 10-17 min apart, ~2 h before tip | FanDuel 11.8%, BetRivers 8.0% |
| Bytes per stored quote row, SQLite, including indexes | ~700 |
| Re-reading unchanged prices (2026-11-06, 17-game slate, local) | books.db +0.0 MB, canonical +0.0 MB per pass (only last_seen moves) |
| A changed price, both stores (same measurement, 12% of 28.6k sides changed) | books.db ~0.3 KB + canonical sc-1 revision ~0.8 KB |

Estimate for ~9 NBA and ~2 NFL games a day, both direct books, alternates included. A changed price is a new row
(immutable history):

| `SPORTS_BOOKS_LADDER` | Quote rows / day | Per 30 days |
|---|---|---|
| `full` | ~120,000 | ~2.5 GB |
| `standard` | ~50,000 | ~1 GB |
| `lean` | ~30,000 | ~0.6 GB |

The Odds API adds little on top: standard markets only, by default. PostgreSQL rows are larger than SQLite's, so
allow ~1.5x. Suggested start: `standard`, with 5 GB of space for a few months of history. Nothing is pruned
automatically, because the history is immutable by design. Retention (e.g. compacting rows older than N days to the
first and last price per offer) is a decision to make before it is needed.

## Environment variables (names only)

| Name | Where | Default |
|---|---|---|
| `ODDS_API_KEY` | The service that runs the worker. Render env, `sync: false` | unset = feed skipped |
| `SPORTS_ODDS_API_MONTHLY_CREDITS` | Same | 500 (Starter) |
| `SPORTS_ODDS_API_RESERVE` | Same | 10% of the month |
| `SPORTS_ODDS_API_ALTERNATES` | Same | `0` (standard markets only) |
| `SPORTS_BOOKS_LADDER` | Same: refresh density of the books' own feeds (`direct` / `full` / `standard` / `lean` / `minimal`) | `direct` |
| `SPORTS_BOOKS_JOB` | Same: `0` folds the books back into the ingest-<league> cycles (rollback of the light job) | `1` |
| `SPORTS_FANDUEL_STATE` | Same | `oh` |
| `SPORTS_BETRIVERS_OPERATOR` | Same | `rsiusoh` |
| `SPORTS_DB_DSN` | Worker **and** web service, only for the separate-service option | unset = SQLite |
| `SPORTS_ODDSPAPI` | The worker's service: `1` turns the OddsPapi provider on (after the owner's check) | unset = off |
| `ODDSPAPI_API_KEY` (or `odds_papi_api`) | The CS2 key, already on the site; read, never copied | - |
| `SPORTS_ODDSPAPI_MONTHLY_REQUESTS` | Sports' share of that account per month | 0 = nothing |
| `SPORTS_ODDSPAPI_CS2_FLOOR` | Requests always left to CS2 on the account | 50 |
| `SPORTS_DATA_DIR` | Optional storage folder | `<CS2_DATA_DIR>/sports` |

## Verification

```
python -m pytest sports/tests -q                      # offline: parsers on real sanitized samples, store, worker, compare
SPORTS_DATA_DIR=/tmp/s python sports_scout.py books-run --once --leagues nba,nfl   # one real pass
SPORTS_DATA_DIR=/tmp/s python sports_scout.py books-health                         # per-book status table
SPORTS_DATA_DIR=/tmp/s python sports_scout.py books nba --player "Payton Pritchard" # stored offers side by side
SPORTS_DATA_DIR=/tmp/s python sports_scout.py edges nba                            # board with the "books" column
python sports_scout.py books-plan                                                   # requests / credits per day
```
With `ODDS_API_KEY` set, the same `books-run --once` collects DraftKings, BetMGM and Caesars (paid plan) through the
feed, and `books-health` shows which books and markets actually returned data.

## Contract changes since d393292 (for the Scout integration branch)

Additive unless marked **breaking**:
- **breaking:** `sports/oddsapi.py` is removed. Its parsing moved to `books/theoddsapi.py`, and the board no longer
  calls The Odds API live; it reads storage. `scout.sharp_quotes()` now returns Pinnacle + Kalshi + Polymarket.
- `sports/polymarket.py` (new sharp source) and `sports/books/` (new package).
- `edges.build(lg, offers, quotes, injuries=None, depth=None)`. Rows gain `role` (ESPN depth chart) and `books`
  (see `cli.attach`).
- `espn._event` adds `home/away["nick"]`. `espn.scoreboard` now asks one day per request (ESPN refuses date ranges),
  and it also feeds settlement and the slate. New in `espn`: `teams`, `depth_index`, `parse_depth`, `splits`,
  `parse_splits`.
- `record.py` forecasts gain `role` and `book_consensus` (JSON: consensus, books, quote ids), added automatically
  to an existing log.
- `markets.ODDS_API["football"]` gains `player_pass_rush_yds`.
- New CLI commands: `books`, `books-health`, `books-plan`, `books-run`, `books-verify`.
- (66644ee) `record.export` / `record.import_`, CLI `export-forecasts` / `import-forecasts`; the first real log is in
  `sports/research/forecasts/` (MANIFEST.json: count, time range, schema, sha256).
- (game data) new package `sports/games/` - see its README: game store (`sports_game_*` tables), factor contract
  `sports-factors/1`, board rows gain `factors`, forecasts gain a `factors` column (added automatically). The worker
  (`python -m sports.books.worker`) now also runs the game-data tick each pass (`--no-games` to skip). Kalshi
  pacing is 2 s.
- (integration repair on PR17) ONE collector: `sports.worker` sources gain `books` (this worker's `run_once` +
  `bridge.sink` -> sc-1) and `games` (`collect.tick`); `python -m sports.books.worker` is one pass only; the
  `theoddsapi` provider is unregistered (Odds API polled only by `sources.oddsapi_ingest`); `books-oddspapi capture`
  makes only the unmetered `/v4/account` read; ledger `oddspapi` monthly = `SPORTS_ODDSPAPI_MONTHLY_REQUESTS`
  (default 0). sc-1 gains platforms `fanduel`, `betrivers`, `oddspapi:<book>`; forecasts gain `mkt-fanduel` /
  `mkt-betrivers` (amendment A4).
- (OddsPapi / Retry-After) new provider `oddspapi` (off by default; kind `feed_league`, read per league by the
  worker), `FetchResult.not_before` / `budget_denied` / `unmapped`, `net.HttpError.not_before` / `held`,
  `net.hold()` / `net.holds()`, CLI `books-oddspapi account|catalog`, health table gains an OddsPapi column. A
  Retry-After longer than 60 s is now a persisted hold instead of a capped sleep.

## Board market view (mv-1, `sports/market_view.py`) and capture-only review

Every board row carries `mk`: every book that prices the identical market (same game, player, stat, scope, ordinary
line) with its own chance at its own line (two-sided no-vig per book; Kalshi / Polymarket YES midpoint; one-sided =
price only), its line and observed age, and the headline **Market (N books)** = the mc-1 rule (frozen filters,
equal-weight log-odds per source, nearest line moved with dispersion.json) over every VERIFIED source incl. FanDuel /
BetRivers (owner decision 2026-10-23); a quote's own platform is never a source for it. Registered mc-1 / mi-1
forecasts keep REGISTER.md 10. The manifest's `sources` block lists per source: offers on the board, last read,
verified flag, and for capture-only sources the stored inventory.

Capture-only review (Thunderpick / OddsPapi), on the service that holds the key:
```
python -m sports.books.cli inventory thunderpick nba      # 1 request per candidate path (ledger-paced), raw gz + sha256 + key paths
python -m sports.books.cli inventory thunderpick nfl
python -m sports.books.cli inventory oddspapi nba         # unmetered /v4/account only
python -m sports.books.cli inventory-show thunderpick nba # re-print the stored summary, no request
```
The format is proven only when, for one known game, the stored answer shows a player-stat market whose selections
carry the player's name, Over and Under as separate selections, one shared numeric line, a price per selection and the
event's teams / start. Only then is a parser written against the stored raw answer and VERIFIED flipped.
