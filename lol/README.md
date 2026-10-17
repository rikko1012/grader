# LoL scout

League of Legends data for the LoL grader (kills / assists / deaths on Map 1, Maps 1-2, Maps 1-3), built the same way
as the CS2 side: separate data sources behind one scout, a history database a scheduled job fills and the scout only
reads, polite pacing and caching, and a projection that is measured chronologically before anything uses it.

**It does not touch CS2.** Everything is in `lol/`. No LoL module imports a CS2 module, and no CS2 module imports LoL;
`lol/tests/test_lol_isolation.py` fails if that ever changes. LoL keeps its own cache file, history database and output
folder (`<CS2_DATA_DIR>/lol` on the server, `lol/.data` locally), and the server, the CS2 grader and the CS2 tests are
unchanged.

## Data sources (checked 2026-10-17)

| Source | What the grader gets | Access | Status |
|---|---|---|---|
| **LoL Esports API** (`esports-api.lolesports.com`, `lolesports.py`) | Schedule / slate, best-of, leagues, tournaments, completed matches, rosters | Public site key, no login | Works |
| **LoL Esports livestats feed** (`feed.lolesports.com`, `lolesports.py`) | Every player's kills, deaths, assists, CS, gold, champion and role per game; team kills; game length | No login | Works. **No LPL** (7 LPL matches since 2025), so LPL needs Leaguepedia |
| **Leaguepedia Cargo** (`lol.fandom.com/api.php`, `leaguepedia.py`) | Every league's scoreboards (incl. LPL), official winner, exact game length, game order, schedule, rosters, player renames | **Bot password needed**: anonymous queries are refused (`ratelimited`) | Code ready; needs your credentials (below) |
| **Polymarket** (series `10311`, `odds.py`) | Series price, each game's winner price, *Games Total O/U* (chance map 3 / 4 is played), total kills per game | Public | Works |
| **PrizePicks** (partner API, league `121`, `lines.py`) | LoL lines: `MAPS 1-2 / 1-3 Kills` (standard / goblin / demon) | Public partner API, like CS2 | Works |
| **Underdog** (`sport_id=LOL`, `lines.py`) | `Kills / Assists on Maps 1+2(+3)` with payout multipliers | Public | Works |

**Cloudflare:** nothing on this path is behind Cloudflare. The LoL Esports API and feed, Polymarket, the PrizePicks partner
API and Underdog all answer a plain request. Leaguepedia's wiki *pages* show a Cloudflare check, but the grader doesn't
need them: the API gives everything, including the table schemas. Leaguepedia's API block is a **login rate limit**, not
Cloudflare, so a browser would not lift it. The bot password does. If a source ever does sit behind Cloudflare, its
fetching belongs in the scheduled sync job (the HLTV scraper's pattern), never in a member's look-up.

## Setup

Nothing new to install (`requests`, `numpy` and `psycopg2-binary` are already in `requirements.txt`).

**Leaguepedia bot password** (one time, needed for the LPL and the fullest history):
1. Log in to lol.fandom.com and confirm your email address in your Fandom account settings.
2. Open `https://lol.fandom.com/wiki/Special:BotPasswords`, type a bot name (e.g. `lolscout`), tick **Basic rights**, create.
3. Set two environment variables (never put them in a file):
   - `LEAGUEPEDIA_USER` = the login name the page shows, e.g. `YourName@lolscout`
   - `LEAGUEPEDIA_BOT_PASSWORD` = the password it shows (once)

Optional: `LOL_DB_DSN` = a PostgreSQL connection string for the history (otherwise a SQLite file). `LOL_DATA_DIR` moves
LoL's folder.

## Commands

```
python lol_scout.py sync                      # fill the history (LoL Esports; + Leaguepedia when configured)
python lol_scout.py sync --since 2025-01-01   # backfill further back
python lol_scout.py status                    # what the history holds, which sources are configured
python lol_scout.py slate                     # LoL matches in the next 36 hours
python lol_scout.py player Faker              # role, team, per-game averages, Map 1 / Maps 1-2 / Maps 1-3 totals
python lol_scout.py h2h Faker --opponent "Gen.G" --line 7.5
python lol_scout.py odds T1 --opponent "Gen.G"
python lol_scout.py lines                     # the whole PrizePicks + Underdog LoL board
python lol_scout.py full Faker                # all of the above + projection and P(over / under) for each of his lines
python lol_scout.py full Faker --line 7.5 --scope 12
python -m lol.backtest                        # the model's chronological test (lol/EVALUATION.md)
```
`python -m lol ...` works the same. Results also save to `<LOL_DATA_DIR>/output/`.

## Keeping the history fresh

Run `python lol_scout.py sync` every couple of hours. A match it already holds is skipped, so a run only fetches new
games. `lol/deploy/lol-sync.yml` is a ready GitHub Actions schedule (the HLTV scraper's set-up), copy it to
`.github/workflows/` and add the secrets it lists. It's not active until you do.

## Data center (`lol/center`, runs on the server; not released to members)

One low-priority process the server starts (`python -m lol.center.worker`; the server never imports `lol`) keeps:
- an **append-only quote archive** of every Underdog (`sport_id=LOL`) and PrizePicks (league 121) LoL offer: each poll
  recorded, then open / change / removed / reopen events with line, offered sides, payouts and times (`DATA_DIR/lol/center/lol.db`);
- **Leaguepedia ingestion** with explicit fields, deterministic order, bounded pages, one shared pacer, checkpoints, raw
  responses and every source-row revision kept, internal ids (`lol:player:…`, `lol:series:…`) with source mappings,
  renames through PlayerRedirects, ambiguous names / rematches quarantined, game order from the schedule's ids. It uses
  the same `LEAGUEPEDIA_USER` / `LEAGUEPEDIA_BOT_PASSWORD`; without them it keeps retrying at a backed-off pace;
- each platform's published LoL settlement rules with their sources and whether each is verified (`center/rules.py`).

Admin only: `/admin/lol.json` (worker status), `/admin/lol/quotes.json?after=&limit=` (archive, paged),
`/admin/lol/coverage.json`, `/admin/lol/quarantine.json`, `/admin/lol/links.json`. `LOL_DATA=0` turns it off.

## How the pieces fit

- `history.py`: the database (`lol_games`, `lol_player_games`, `lol_sync_state`; SQLite or Postgres). Each row keeps its
  source; a game both sources hold is counted once (Leaguepedia kept), and a player's identity is his source id, so two
  pros with the same name never merge.
- `sync.py`: LoL Esports and Leaguepedia into the history. The feed has no per-game winner. It's inferred from
  inhibitors / towers / gold and kept **only** when the inferred wins add up to the official series score.
- `teams.py` + `team_aliases.json`: team identity across sources (exact names, generic words dropped, maintained alias
  groups; never substring, so an academy team is never its main team). Add a group when a book spells a team differently.
- `model.py`: the projection (one implementation for the backtest and live). `backtest.py` fits it on a TRAIN period and
  reports a later TEST period; `model_fit.json` is that fit.
- `lines.py` also appends every line it sees (new / moved / removed) to `<LOL_DATA_DIR>/lines_archive.jsonl`, so the
  model can later be checked against lines that were really offered.

## Known limits

- **LPL** has no history until Leaguepedia is configured, so LPL players show "not in the stored history".
- **Maps 1-3 in a best-of-3**: map 3 may not be played. The scout flags it and shows Polymarket's chance it is.
- The projection gives a probability, not a grade or a stake. Grades and units are a separate decision once its
  accuracy against real lines is known (see `EVALUATION.md`).
