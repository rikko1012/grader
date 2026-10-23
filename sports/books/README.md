# Sportsbook odds ingestion (sports/books)

Player-prop prices from DraftKings, FanDuel, BetMGM, Caesars and BetRivers for NBA and NFL. One scheduled worker
collects them into Sports storage, and everything else reads that storage. No browser, no disguised client and no
manual tabs are involved, and nothing a member does triggers a provider call. Isolated from CS2 and LoL like the rest
of `sports/`. **Production stays off:** this branch starts nothing on the server.

**Owner decision (2026-10-23): free sources only. No paid odds feed will be bought.** The live sportsbook
sources are FanDuel's and BetRivers' own public feeds. DraftKings, BetMGM and Caesars stay out: their sites refuse
this server, and the only legitimate route to their prices is a metered feed. The Odds API adapter stays in the
code, inert without `ODDS_API_KEY`; with a free Starter key it would be held to that tier's 500 credits a month.

## Sources

| Book | Own public feed | Documented feed (The Odds API) |
|---|---|---|
| FanDuel | `fanduel.py`: sbapi.oh.sportsbook.fanduel.com. Answers a plain, honestly identified request (Ohio, where FanDuel is licensed) | `fanduel` |
| BetRivers | `betrivers.py`: Kambi offering API, operator `rsiusoh` (Ohio). Answers a plain request | `betrivers` |
| DraftKings | Refused: Akamai "Access Denied" (403). Probed every 6 h, never worked around | `draftkings` |
| BetMGM | Refused: firewall block page (403) for a non-browser client. Probed every 6 h, never worked around | `betmgm` |
| Caesars | Refused: CloudFront "Request blocked" (403). Probed every 6 h, never worked around | `williamhill_us`. **The Odds API lists it on paid plans only** |

The Odds API (`theoddsapi.py`) is the documented feed. It needs `ODDS_API_KEY`, and without one it is skipped and
says why. Its documentation (read 2026-10-23) says:
- player props come one game at a time from `/v4/sports/{sport}/events/{id}/odds`;
- cost = unique markets returned × regions (the five named books are one region; empty answers cost nothing);
- `/events` is free;
- quota headers are `x-requests-remaining`, `x-requests-used` and `x-requests-last`, and the market-level
  `last_update` is the provider time;
- plans: Starter free (500 credits/month), 20K $30, 100K $59, 5M $119;
- player-prop coverage "varies" by bookmaker. **Real coverage per book is unverified until a key is set** (see
  Verification).

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

## Worker (`worker.py`)

`python -m sports.books.worker` (forever; one pass a minute) or `python sports_scout.py books-run --once`.
- **Lease:** one worker at a time; a second one finds the lease taken and does nothing.
- **Game lists** are re-read every 30 min.
- **Refresh ladder** (`schedule.py`): "full" is every 10 min inside 2 h, every 30 min inside 8 h, hourly inside 24 h,
  3-hourly inside 3 days, 12-hourly inside 7 days. Started games are never fetched.
- **Markets are batched:** one Odds API call per game; FanDuel needs one request per stat tab, BetRivers one per game.
- **Rate limits:** 1 request per second per host (`net.py`).
- **Backoff:** errors back off 60 s doubling to 30 min; a 401/403 refusal waits 6 h.
- **Odds API budget:** the densest ladder whose estimated credits fit `SPORTS_ODDS_API_MONTHLY_CREDITS` (re-planned
  daily from ESPN's real schedule, `plan.py`); every call is checked against the daily allowance and the reserve.

## Storage needed (measured 2026-10-23 evening, real FanDuel + BetRivers data)

| Measured | Value |
|---|---|
| Offers at first sight, per NBA game | FanDuel ~1,050 (83% alternate-ladder rungs), BetRivers ~630 |
| Offers at first sight, per NFL game two days out | FanDuel ~65, BetRivers ~170 (both post more nearer kickoff) |
| Share of offers whose price changed between two fetches 10-17 min apart, ~2 h before tip | FanDuel 11.8%, BetRivers 8.0% |
| Bytes per stored quote row, SQLite, including indexes | ~700 |

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
| `SPORTS_BOOKS_LADDER` | Same: refresh density of the books' own feeds (`full` / `standard` / `lean` / `minimal`) | `full` |
| `SPORTS_FANDUEL_STATE` | Same | `oh` |
| `SPORTS_BETRIVERS_OPERATOR` | Same | `rsiusoh` |
| `SPORTS_DB_DSN` | Worker **and** web service, only for the separate-service option | unset = SQLite |
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
