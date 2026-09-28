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

## What it does automatically
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
- **Odds:** Polymarket series price -> American odds -> grader. No market = 50/50, flagged.
- **FACEIT:** Steam ID from BO3.gg match data -> exact FACEIT account. Falls back to the
  name/teammate check in `faceit_resolve.py`. `show_to_members` is only true when it's certain.

Not available without HLTV: round swing (treated as 0) and the Trading/Firepower support flag.

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
