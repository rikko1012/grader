# cs2_scout

Pulls everything the CS2 v7.2 grader needs, fully automatically, and can grade a prop in one run.
No browser, no HLTV.

| Data | Source |
|---|---|
| Player stats, per-map stats, match history, rosters, today's matches | BO3.gg, via the `cs2api` wrapper in `./cs2api` (`bo3.py`) |
| Team win odds | Polymarket (`polymarket.py`) - public, no key |
| FACEIT account + recent FACEIT form | FACEIT Data API (`FACEIT_API_KEY`) - account found by Steam ID |
| Grading | `grader.py` (v7.2 - math unchanged) |

## Setup (one time)
```
pip install -r requirements.txt
setx FACEIT_API_KEY your_key      (then open a new terminal)
```

## Web interface
Double-click **Start CS2 Grader.bat** (or run `python server.py`) and use http://localhost:8000.
Type a player (suggestions appear as you type), the line, Kills/Headshots and line type.
You get the grade for the best side (More or Less), then the match, stats, role, checks,
head-to-head, last 5 maps and FACEIT form. Close the black window to stop it.
First look-up of a player takes about a minute (polite pacing to BO3.gg); repeats are instant.

## Commands
```
# Grade a prop (everything in one run)
python cs2_scout.py full ZywOo --maps mirage,inferno --opponent Aurora --line 36.5
python cs2_scout.py full ropz --prop hs --line 17.5 --line-type goblin

# Pieces
python cs2_scout.py slate                              # matches in the next 36 hours
python cs2_scout.py player ZywOo --maps mirage,inferno
python cs2_scout.py h2h ZywOo --opponent Aurora --line 36.5
python cs2_scout.py faceit ZywOo
python cs2_scout.py odds Vitality --opponent Aurora
```
Optional: `--team` (transfer check against the team on the prop), `--team-ml/--opp-ml` (override
Polymarket), `--bo3-slug` (if the name search picks the wrong player), `--refresh` (skip cache).
Results print to the screen and save to `output/<player>_<command>_<date>.json`.

## Which projection is live (2026-10-10)
- **Map 1, Maps 1-2 and Maps 1-3 lines**: the performance model `perf.py` on HLTV map history
  (`perf_live.py`), version tag in `MODEL_VERSIONS.md`. It uses his kills per round (half-life 120 days, measured up to
  the match day), tonight's LAN / online split, a thin-history prior, the opponent's kills conceded and, for headshots,
  his headshots per kill. Players HLTV doesn't have use the same model on their BO3.gg maps (provisional).
- **Maps 1-3** = maps 1, 2 and 3 in full, never maps 4-5 (BO5: always played; BO3 2-0: Underdog voids). The older pipeline described below is the fallback only (rates, form, H2H, Round Swing, win chance,
  Over margin).
- Gates and caps (stand-in, transfer, sample, rust, AWPer on HS, FORM / TAPE FIGHT, thin market, same-match cap) set
  the stake on every line. On the performance model they set the stake only and never change the chance. On the older
  pipeline (fallback grades only) some of them also change the chance: each of these widens the spread x1.10 (together
  at most x1.25) - roster not confirmed, sample under 5 Maps 1-2, under 10, thin data (under 20), stand-in, rust (15+
  days since the last official map), poor DPR with poor KAST or ADR (and veto uncertainty, only with predicted maps on -
  off since 2026-10-01); FORM FIGHT there also halves the confidence. Their inputs read HLTV first (`hltv_first_gates`): AWP share,
  last map date, Maps 1-2 count; BO3.gg is the backup. BO3.gg stays primary only for what HLTV's database lacks:
  upcoming schedule pages (maps / veto), team pages with benched / stand-in status, and players HLTV doesn't cover.
- Every other factor is display only until it passes a chronological test - see `FACTOR_AUDIT.md`.

## What it does automatically (older pipeline: fallback projections, and the card's context)
- **Roster (Gate C):** current team + 5-man roster from BO3.gg; stand-in if he's not on the roster;
  roster confirmed when his last map's lineup matches it.
- **Waterfall:** last 3m -> 6m -> 12m -> all-time on current team (+/-8% flag) until 10+ maps,
  overall and per prop map.
- **Stats:** KPR, DPR, ADR, HS%, KAST (last 10 maps), rating.
- **Gate A (AWPer):** AWP kills are one body shot, rifle kills need 3-4. Body-shot kills per
  body hit >= 0.30 = AWPer (HS props NO PLAY), <= 0.22 = rifler, in between = unknown.
  Checked on 8 teams: every AWPer 0.35-0.47, every rifler 0.14-0.20.
- **Role:** rating vs the 4 teammates over the same window. BO3.gg rating / 6.0 so an average
  pro is 1.00 like HLTV's (keeps the grader's 0.10 gap rule meaningful).
- **Gate B (IGL):** from `igl.csv` (player,team) - BO3.gg doesn't record IGLs. Not listed = not IGL.
- **Last 5:** KPR over the last 5 maps; 70/30 blend when it's 15%+ off the window.
- **H2H:** Maps 1-2 of each series vs the opponent (map order within the series), kills or
  headshots vs the line, hit rate in wins/losses.
- **Odds:** one series price per grade (`market_odds.select`: Thunderpick first, else Polymarket, else OddsPapi), its
  exact probability passed to the grader. On every line a price that is not a liquid exchange / sharp book (SOFT) or no
  price (NONE) caps the stake at 1.5U. Older pipeline only: the price also moves the projection (no market = 50/50,
  flagged); the performance model takes no market input.
- **FACEIT:** Steam ID from BO3.gg match data -> exact FACEIT account. Falls back to the
  name/teammate check in `faceit_resolve.py`. `show_to_members` is only true when it's certain.

Round swing comes from HLTV when the HLTV database is connected (see below); without it, round
swing is treated as 0. The Trading/Firepower support flag is still not available.

## HLTV data (optional): real Round Swing + Rating 3.0
[hltv-scraper](https://github.com/rikko1012/hltv-scraper) runs on its own schedule, gets past HLTV's
Cloudflare check, and writes HLTV match stats into PostgreSQL. `hltv.py` only reads that database,
so a grade never waits on HLTV.

- Set `HLTV_DB_DSN` to the scraper's database (Railway: `${{Postgres.DATABASE_URL}}`).
- Each grade then shows an **HLTV** card: Rating 3.0 (CT/T), Round Swing, ADR/KAST/K-D, data date.
- **Round Swing feeds the older pipeline's grade** (fallback grades only; grade_leg's swing multiplier) when the player has
  10+ HLTV maps in the window (3m -> 6m -> 12m) *and* those maps are for the same team. Otherwise it
  stays 0 exactly as before. `HLTV_SWING=0` turns it off.
- The card also shows (display only, never used in the grade): his HLTV stats on the prop maps
  (KPR, rating, ADR), eco-adjusted K/D/ADR/KAST, his last 5 HLTV maps, team vs opponent series record
  and map win rates, the team's VRS rank trend, the next match from HLTV's schedule, and a note when
  some maps came from the scraper's backup data source.
- **Schedule:** HLTV's schedule is checked on every grade. If BO3.gg has no match for his team, HLTV's
  is used first (before Polymarket and PandaScore). If BO3.gg's next match is against a different
  opponent and HLTV lists an earlier match (90+ minutes before) that BO3.gg doesn't have at all, the
  grade switches to HLTV's match and says so. Same opponent at a different time = a heads-up note
  only. A match the member picked or an opponent they typed is never overridden, and an HLTV
  schedule snapshot older than 6 hours is ignored.
- Database missing, down, or player unknown = no HLTV card, grade unchanged. Each part of the card is
  fetched separately, so one failing part doesn't hide the others. A database that's down trips a
  circuit breaker: HLTV is skipped instantly for 5 minutes instead of every grade waiting on it.

```
python hltv.py ZywOo Vitality      # what the grader will see for a player
```

## Being a good guest
BO3.gg has no official API or terms, and its robots.txt asks bots to stay out of `/api/`.
`bo3.py` identifies itself honestly, waits 1.5s between requests, and caches (finished maps
forever, everything else 12 hours). Keep it that way. If BO3.gg ever blocks it, email
support@bo3.gg about access.

## Team odds (Polymarket)
- Uses the bid/ask midpoint; falls back to last price when the spread is wider than 10c.
- Flags thin markets (under $500 liquidity) and matches that have already started (live price).
- Also returns Map 1 / Map 2 winner prices when Polymarket lists them.

## Fixing things by hand (optional, admin only)
- `faceit_ids.csv`: put the right FACEIT nickname in and set confidence to `manual`.
- `igl.csv`: add `player,team` rows for in-game leaders.
