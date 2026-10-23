# Sports game data and factors (sports/games)

Every NBA / NFL game's box score and play-by-play, and the pre-game context and markets around it, collected into our
own store by the scheduled worker. A versioned factor contract (`sports-factors/1`) is computed from that store. No
browser, nothing a member does calls a provider, and it is isolated from CS2 and LoL. **Production stays off.**

## Sources: each stat from one place

| Data | Source (one per stat) | Notes |
|---|---|---|
| Box scores (every team and player stat, starter, did-not-play + reason) | ESPN game summary | split `a-b` pairs, ESPN's names |
| Play-by-play (NBA shot location, points tried, participants; NFL down / distance / yards to go before and after, yards, yards after catch, penalty, turnover, drive) | ESPN game summary | |
| NFL play participants with roles (passer, receiver, rusher, tackler, sackedBy ...) | ESPN core API plays | one request per NFL game |
| Venue indoor / grass | ESPN core API venue | once per venue |
| Spread, total, moneyline (the odds ESPN shows; provider named per row, DraftKings on 2026-10-23) | ESPN pickcenter | snapshot on change |
| ESPN matchup predictor, injury report, BPI / FPI | ESPN | snapshots |
| Player-prop **lines** from DraftKings, provenance **"ESPN-supplied DraftKings"** | ESPN core API `odds/100/propBets` | lines only (see below) |
| Win chance, margin distribution (blowout chance), NBA team totals, 1H, overtime, NFL TDs / sacks / turnovers | Kalshi game markets | ladders stored whole |
| Kickoff weather, outdoor US NFL venues | National Weather Service (api.weather.gov) | ESPN carries no weather |
| Sportsbook prices | FanDuel, BetRivers own feeds (`sports/books`) | unchanged |
| Exchange prop prices | Kalshi, Polymarket (`sports/kalshi.py`, `polymarket.py`) | unchanged |
| Pick'em lines | PrizePicks, Underdog | unchanged |

Box-score totals are never recomputed from play-by-play. `derive.py` only adds what play-by-play alone holds. The game
spread and total come from pickcenter only. The Kalshi ladders are used only for what nothing else publishes.

### ESPN-supplied DraftKings lines: what was verified (2026-10-23)
- **Event:** every item's competition `$ref` is checked against the requested game. Items for another game are dropped.
- **Stat and period:** matched whole by type name (`markets.ESPN_DK`). "1st Half" / "1st Quarter" types keep their
  period and are never matched to a full-game line. Longest-play, touchdown-scorer and kicking types map to no market
  and are kept with their raw type only.
- **Line:** `current.target` / `open.target`. A "Milestone" rung "N+" is stored as over N - 0.5.
- **Prices:** none. Every prop appears twice (its two sides) with no odds and no side label, so these rows are never
  de-vigged, never given a probability and never counted in a price consensus.
- **Timestamps:** `lastUpdated` has minute precision. 779 of 11,362 NBA rows had a `lastUpdated` 0-3 h after our
  observation time, so it is stored as given and staleness is measured from our `observed_at` / `last_seen_at`.
- **Cross-check:** against FanDuel / BetRivers standard lines stored about 2.5 h earlier, for the same game, player and
  stat (1,781 comparisons): 74% identical, 25% one point apart, 1% two apart, one three apart.
- This is not a DraftKings connection. DraftKings' own site refuses this server and is not contacted.

## Storage (`store.py`)
SQLite `<SPORTS_DATA_DIR>/games.db`, or PostgreSQL with `SPORTS_DB_DSN` (same DSN as the books store; every table
is `sports_game_*`):

| Table | Holds |
|---|---|
| `sports_game_games` | per game: teams, score, periods, venue, indoor / grass, attendance, officials, **`ended_at`** (last play's wall clock) |
| `sports_game_teams`, `sports_game_players` | box scores (JSON stats under ESPN's names) |
| `sports_game_plays` | play-by-play |
| `sports_game_derived` | play-by-play-only numbers per player / team and game (`derive.py`) |
| `sports_game_snapshots` | **immutable** context: pickcenter, predictor, injuries (per game and league-wide), BPI / FPI, weather |
| `sports_game_market_lines` | **immutable** Kalshi game-market history (a row when bid / ask / last / status changes) |
| `sports_game_prop_lines` | **immutable** ESPN-supplied DraftKings line history (a row when the line or opening line changes) |
| `sports_game_line_state` | latest row and last-seen time per market / prop / snapshot |
| `sports_game_athletes` | athlete id -> name, position, team (rosters) |
| `sports_game_fetches`, `sports_game_settings` | every request; schedule bookkeeping and the tick lease |

Measured size (2026-10-23): 1,333 NBA games and 380 NFL games took 233 MB in SQLite, about 140 KB per NBA game with
play-by-play. That is roughly 180 MB per NBA season and 45 MB per NFL season. One pre-game day of context adds about
25k prop-line rows and 2k Kalshi rows (a row only on change).

## Collection (`collect.py`), run by the one scheduled worker
`python -m sports.books.worker` runs the books job, then `collect.tick`, each pass (`--no-games` for books only).
- finals every 30 min
- pre-game context and ESPN-supplied lines: every 30 min inside 36 h, every 6 h inside 7 days
- Kalshi every 15 min
- injuries every 30 min
- BPI / FPI and rosters daily
- NWS weather with the NFL context

One tick at a time (a lease) and host pacing from `net.py`. A source's `Retry-After` is obeyed in full: up to 60 s it
is slept, and a longer one is a hold saved in `sports_game_settings` (`hold:<host>`), so nothing is sent to that
host before then, even after a restart.
Backfill: `python sports_scout.py games-backfill nba --season 2026 2027` (ESPN names NBA seasons by their end year)
and `games-backfill nfl --season 2025 2026`. It stops at the first run of unplayed games.

## Play-by-play derivations (`derive.py`)
- **NBA:** shot zones (rim / short mid / long mid / corner 3), pull-up and spot-up jumpers, assisted makes, on-court
  seconds and the score while on (lineups from substitutions; period starters inferred), and live misses while on
  court (a rebound-chance **proxy**).
- **NFL:** target depth (short / deep) and direction, red-zone and inside-10 targets, completed air yards (a
  **proxy**: incomplete passes' air yards are not published), red-zone / inside-5 carries, run direction, success,
  EPA, QB dropbacks / scrambles / sacks; per team: snaps, neutral pass rate, neutral pace, success, EPA, explosive
  rate, red-zone trips and TDs.
- **EPA** uses our own expected-points table (`ep_nfl.json`, `games-derive nfl --refit-ep`), **frozen on earlier
  seasons**. The committed table is fitted on the 2025 season only (285 games, through 2026-02-09T03:22Z). League
  average EPA per play (+0.03) and the team order are sanity checks, not proof of calibration.

## Factor contract `sports-factors/1` (`contract.py`, `factors.py`)
`python sports_scout.py factors-contract nba` prints the registry: every factor on the owner's list with its group,
label, **one** source, status (`collected` / `derived` / `proxy` / `unavailable`) and how it is computed.
`python sports_scout.py factors nba "Jalen Brunson" points` prints one payload:
```
{"contract": "sports-factors/1", "as_of", "league", "event_id", "athlete_id", "team_id", "opp_id", "market",
 "factors": {key: value}, "missing": {key: reason}}
```
**No look-ahead:**
- A game's result is used only if the game **ended** before `as_of` (`ended_at`; otherwise start + 3.5 h NBA /
  4.5 h NFL).
- Snapshots and line histories are used only if **observed** before `as_of`.
- EPA factors are withheld unless the EP table's training games all ended before `as_of`.
- Known limit: box scores are ESPN's final versions as fetched, so a later stat correction is not dated.
- Historical pre-game lines and injuries exist only from when we started observing them (2026-10-23).

**Unavailable from any source we hold:**
- snap share, route participation, TPRR, alignment
- coverage shells / man-zone, defender matchups, run scheme, true pressure, Next Gen Stats
- potential assists, true rebound chances
- DVOA (proprietary)

The named proxies stand in where one exists.

**Use:** `scout.board()` adds `row["factors"]` (display) and `record.save` logs the values in the forecast's new
`factors` column. **Neither changes `chance` (`sharp-1`).** No factor-based model exists yet. Any future one will be a
separately tagged, **shadow-only** experiment, logged and scored beside `sharp-1`, never shown as the chance and
never replacing it.

## Environment variables (names only)
`SPORTS_DATA_DIR` (folder), `SPORTS_DB_DSN` (PostgreSQL, separate-service option only). No keys: ESPN, Kalshi and
NWS are public.

## Verification
```
python -m pytest sports/tests -q                                      # offline, real trimmed samples
SPORTS_DATA_DIR=/tmp/s python sports_scout.py games-backfill nfl --season 2026 --limit 5
SPORTS_DATA_DIR=/tmp/s python sports_scout.py games-tick --leagues nba,nfl
SPORTS_DATA_DIR=/tmp/s python sports_scout.py games-derive nfl         # or --refit-ep --train-seasons 2025
SPORTS_DATA_DIR=/tmp/s python sports_scout.py games-status
SPORTS_DATA_DIR=/tmp/s python sports_scout.py factors nfl "DK Metcalf" rec_yds
```
