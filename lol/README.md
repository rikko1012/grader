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
python lol_scout.py sync                      # fill the history: the last ~8 months (LoL Esports; + Leaguepedia when configured)
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

Render's worker owns the serving history. `python lol_scout.py sync` uses that same canonical ingestion path locally.
The legacy `lol/deploy/lol-sync.yml` template predates this consolidation; do not enable it as a second production writer.
Use `LOL_BACKFILL_DAYS` to choose the collection window before process start. Unsupported legacy `--since`, `--until`,
`--leagues` and `--force` options are rejected instead of being silently ignored.

### GitHub history reaches Scout

PR checks run the offline LoL and CS2 tests, including a disposable PostgreSQL service. They do not fetch providers.
The manual `lol-full-history` job collects from January 2025 through the same data-center pipeline and retains its
resumable source database. It publishes every current source record (including explicit removals) to `lol_source_inbox`
in the repository's `LOL_DB_DSN` database, using that secret as `LOL_HANDOFF_DSN`. A missing destination is a failure,
not a successful artifact-only collection. `LOL_DB_DSN` is deliberately empty inside the collector, so it cannot
replace Scout's serving tables with an isolated partial snapshot.

Scout's worker reads the inbox automatically through its existing `LOL_DB_DSN`, imports bounded batches with a
transactional cursor, preserves source revisions, and rebuilds/exports the combined history. Source ids are passed
across; local canonical ids are never copied between databases. Older collector observations cannot undo newer local
corrections. Merely being absent from a batch does not delete a record. The status response exposes `history_handoff`
and `last_history_import`, alongside the normal export/read-back counts.

This path preserves all collected fields. Missing identities or incomplete games still follow the existing quarantine
and scope checks before becoming grading inputs. It does not promote a model or open the sealed evaluation period.
The collector can finish a bounded run with partial coverage; coverage and checkpoints are saved for resumption.

Already-running jobs use the code they started with; updating this PR does not migrate their data or replace that
running process. A live handoff is verified only after this branch is deployed and the revised collector publishes.

## One LoL extension: data center + scout (consolidated 2026-10-17; not released to members)

- **One ingestion path**: the data center (`lol/center`) - the worker on the server (`python -m lol.center.worker`,
  started by server.py, which never imports `lol`), or `python lol_scout.py sync` locally. It reads LoL Esports (no
  login, no LPL) and Leaguepedia (bot login `LEAGUEPEDIA_USER` = `Name@grader`, `LEAGUEPEDIA_BOT_PASSWORD`) with
  explicit fields, bounded pages, one shared pacer, checkpoints, raw responses and every source-row revision kept.
- **One identity**: internal ids (`lol:player:N`, `lol:team:N`, `lol:series:N`, `lol:game:N`) with source mappings
  and history; renames through PlayerRedirects; a LoL Esports id and a Leaguepedia page of the same player / team are
  linked through the series both sources hold (or merged, older id kept); ambiguity goes to quarantine.
- **One history**: the data center's canonical tables are exported to `lol_games` / `lol_player_games`
  (`center/export.py`) - the only writer; the scout, the backtest and the live forecasts read it. One source per real
  series (Leaguepedia preferred; LoL Esports fills gaps). The 8-month window comes first (`LOL_BACKFILL_DAYS`, 245).
- **One forecast format**: `center/forecast.py` saves an append-only pre-match record per offered line (line, offered
  sides, times, inputs, model + fit hash, P(over/under/push), side offered, format check, slip eligibility) through
  lol-1's interface (`model.project` / `model.probs`, with the A1 baseline as one of its forms). The admin board, match
  tables, cards and the slip check read those records; saved forecasts are settled by `center/settle.py` once the
  series completes (versioned; corrections never rewrite a forecast).
- **Markets**: an append-only archive of every Underdog (`sport_id=LOL`) and PrizePicks (league 121) LoL line. Maps 1-2
  lines are best-of-3 markets and Maps 1-3 lines best-of-5 (owner); a Maps 1-3 total counts the games played among 1-3
  (owner, same as CS2). Combined-player lines and fantasy points are archived, never forecast.
- **Admin only**: `/admin/lol` (board, match tables, cards, status), `/admin/lol.json`, `/admin/lol/quotes.json`,
  `/admin/lol/{coverage,quarantine,links,board}.json`, `/admin/lol/{forecasts,settlements,history}.json` (paged).
  `LOL_DATA=0` turns the worker off.
- **Model selection**: `python -m lol.backtest compare [--write]` runs lol/EVALUATION.md amendments A1-A3 (B0 vs lol-1
  on the same 8-month data, targets, cutoff and scopes) and writes the selected fit to `lol/model_fit.json`.

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
