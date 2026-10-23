# Sports scout (NBA / NFL, plus WNBA and college)

Every PrizePicks and Underdog player prop for NBA, NFL, WNBA, college basketball and college football, set beside the
**sharp market's chance** of each side. The sharp market is a price the betting market itself sets. The board ranks lines
where the pick'em line and the market disagree most. It is built the same way as the LoL side: everything lives in
`sports/`, separate data sources sit behind one scout, every forecast is saved before the game, and nothing reaches
members until the saved forecasts have been measured.

**It does not touch CS2 or LoL.** No sports module imports a CS2 or LoL module, and nothing in the CS2 app or `lol/`
imports `sports` (`sports/tests/test_sports_isolation.py` fails if that changes). Sports keeps its own cache, forecast log
and output folder: `<CS2_DATA_DIR>/sports` on the server, `sports/.data` locally, or `SPORTS_DATA_DIR`. The server, the
CS2 grader and the LoL worker are unchanged.

**Status: research and admin only, not released to members.** It gives chances, not grades or stakes. A grade or stake
is a separate decision, made once the forecast log shows how accurate those chances really are at real lines.

## Why the sharp price and not our own stats model

Every repo we looked at that tested this properly reached the same conclusion:

- `akilatrades/sports-market-maker` tested its own walk-forward NBA model on 57,111 Kalshi markets. The model used
  minutes, matchup, injuries and betting lines. It beat a player's own recent average by only ~2% Brier, and by under 1%
  for heavy-minute players, who are the ones books list. Buying the model's side lost money after costs.
- `dkori/odds-derived-ff-projections` builds its projections purely from sportsbook prices with the margin removed. It
  treats the market as the best forecast available.

So the ranking uses the market's chance. The player's ESPN history is shown beside it as a check, not as a second
opinion of equal weight. The first live record (2026-10-23) saved 3,656 NBA and 3,247 NFL forecasts, all priced from
Kalshi, with the player's history found for 98% of each. The two chances differed by 8.6 points on average in both
leagues. On ordinary lines (PrizePicks standard, Underdog) the gap was 11.1 points for NBA and 10.5 for NFL. Some players
match closely and others don't, so neither can be called right yet. The forecast log scores both on the same props once
games settle.

## Data sources (checked 2026-10-23 from an Ohio server, the same region as Render)

| Source | What it gives | Access | Status |
|---|---|---|---|
| **PrizePicks** partner API (`lines.py`) | The board: standard / goblin / demon lines, More-only flags | Public, the same API the CS2 and LoL readers use | Works (NBA ~9,000 lines, NFL ~4,000) |
| **Underdog** (`lines.py`) | The board, each side's payout modifier | Public | Works |
| **Kalshi** player props (`kalshi.py`) | "Player N+ points / yards / receptions ..." ladders. Their YES midpoint is the chance of Over | Public, US-regulated exchange, no key | Works. **The default sharp source.** NBA 10 stats, WNBA 4, NFL 10 |
| **Polymarket** player props (`polymarket.py`) | "Player: Points O/U 17.5" markets. Their YES midpoint is the chance of Over | Public prediction-market API, no key (the same read API the project already uses) | Works. NBA points / rebounds / assists / threes / steals / blocks, NFL passing / rushing / receiving yards, receptions, passing TDs / attempts / completions. Only markets with a 5¢-or-tighter bid/ask count (2026-10-23: 153 of ~3,500 NBA props) |
| **Sportsbooks** (`books/`, see `sports/books/README.md`) | FanDuel and BetRivers player props from their own public feeds; DraftKings, BetMGM and Caesars through The Odds API | Collected by one scheduled worker into Sports storage; the board reads storage only | FanDuel and BetRivers live (2026-10-23). DraftKings, BetMGM and Caesars refuse direct requests (403, not worked around) and need `ODDS_API_KEY`; Caesars is on The Odds API's paid plans only. Shown beside the model's chance as the market consensus, never folded into it |
| **Pinnacle** guest API (`pinnacle.py`) | The sharpest book's props | No key | **Refuses US locations** ("Access from United States is prohibited"). Reported as such and **never worked around**. Works only from a non-US server |
| **ESPN** public APIs (`espn.py`) | Schedule, game logs, box scores (settlement), injuries, **depth charts** (each player's role: QB1, RB2, WR1-3, TE1, starter / bench), **splits** (home / away, vs opponent, starter / reserve) | Public, no key; the API hosts publish no robots.txt | Works for every league (depth charts: NBA, WNBA, NFL). Role found for 99.7% of NFL lines on 2026-10-23. Role and splits are shown and logged, never used in a chance |
| **NBA.com** (`swar/nba_api`) | NBA stats | `stats.nba.com` times out and `cdn.nba.com` answers 403 from cloud servers | Not used: the server cannot reach it. ESPN gives the same game logs |
| **Basketball-Reference** (`sportsipy`'s source) | Game logs, splits | Its robots.txt disallows `*/gamelog/` and `*/splits/` for every bot | Not scraped |
| **Pro-Football-Reference** (`sportsipy`, `NFLDeepDive`) | Game logs, season tables | Answers a Cloudflare bot challenge | Not scraped |

Kalshi's own start times are not game starts (it listed CHI @ MIL at 23:00Z; ESPN, PrizePicks and Underdog all say
20:00Z). So Kalshi prices are matched by the game date in their market code, never by time. Polymarket's start times
match the other sources, so its prices are matched by start time.

## How a line's chance is worked out

1. **Remove the margin** from a two-sided price, proportionally: p = (1/over) / (1/over + 1/under). The exchanges
   (Kalshi, Polymarket) need no margin removal; their bid/ask midpoint is used, and only when the gap between bid and
   ask is 5¢ or less. When both quote the same line, the tighter market is used.
2. **Same line**: the pick'em line equals the sharp line, so that chance is used as it is.
3. **Different line**: a distribution is fitted so it reproduces the sharp price, then read at the pick'em line.
   - Its spread was **measured** on last season's box scores, one variance function per stat (`fit_dispersion.py` →
     `dispersion.json`): 2025-26 NBA (256 players, 17,147 games) and 2025 NFL (nflverse).
   - Counted stats use a negative binomial shape. Points and yards use a normal shape.
   - When Kalshi's steps sit on both sides of the line (15+ and 20+ around 18.5), both are read and weighted by
     closeness. That way the shape is only read between two prices the market really gave.
   - Gaps larger than the fitted range (more than 1 for counted stats, more than 3 for points and yards) are flagged and
     left off the default board.
4. **Push**: on a whole-number line, both platforms void a tie, so the chance shown is the chance given no push. The
   push chance is kept beside it.
5. **Underdog**: eff = chance × the side's payout modifier, compared with **53.45%**. That's the per-pick chance at
   which a 2-pick standard entry (published 3.5x) breaks even. Bigger entries need more per pick: 3-pick 53.6%, 5-pick
   54.9%.
6. **PrizePicks**: its payout table is in-app only, so no break-even is computed and the chance is shown alone. Goblin
   and demon lines pay amounts PrizePicks doesn't publish. They are shown, but kept out of the default ranking.

## Commands

```
python sports_scout.py leagues                 # leagues and their source ids
python sports_scout.py slate nba               # games in the next 36 hours (ESPN)
python sports_scout.py lines nfl --platform ud # the Underdog NFL board
python sports_scout.py sharp nba               # Kalshi + Polymarket (+ Pinnacle where allowed) prices
python sports_scout.py books nba               # stored sportsbook offers side by side (books worker)
python sports_scout.py books-health            # per-book connection status
python sports_scout.py edges nba               # the board, best first (standard lines, gaps in range)
python sports_scout.py edges nfl --platform pp --min 0.55
python sports_scout.py edges nba --all         # goblins, demons and far gaps too
python sports_scout.py player nba "Jalen Brunson"   # game log, averages, his lines: sharp chance vs history chance
python sports_scout.py record nba nfl --baseline    # save pre-game forecasts (+ the history baseline)
python sports_scout.py settle                  # grade saved forecasts from ESPN box scores
python sports_scout.py report                  # hit rate, Brier, log loss, game-clustered 95% interval
python sports_scout.py watch nba nfl --every 20     # record every 20 min, settle hourly, until stopped
python -m sports.fit_dispersion NBA_CSV NFL_CSV     # re-measure dispersion.json (sources in its docstring)
```
`python -m sports ...` works the same. Results also save to `<SPORTS_DATA_DIR>/output/`.

## Measuring it before anyone relies on it

`record` saves one row per offered line before its start. The log is append-only: a new row is added only when the
line, the side or the chance moves by 1 point or more. Each row keeps the sharp quote it came from, the line gap, the
model tag (`sharp-1`) and a hash of the dispersion fit.

`settle` reads ESPN's box score after the game:
- over / under / push from the box-score number;
- **void** when the player didn't play;
- **pending**, with the reason, when the evidence doesn't decide it (game not final, name not found).

An NFL player with no stat line is checked against his own game log.

`report` scores the **last pre-game forecast** of each line. The same prop offered on both platforms counts once. Push,
void and pending are excluded and counted. The report shows:
- hit rate, with a 95% interval that resamples whole games, because props in one game move together;
- the mean forecast chance, Brier score and log loss;
- breakdowns by chance band (under 55%, 55-60%, 60%+), platform, league and stat;
- the history baseline scored on the same props.

A few nights prove nothing; the interval shows how little. The rule from `EVALUATION.md` applies here too: no grade, stake
or member release until a registered comparison on untouched forecasts supports it.

## The repos that were sent, and what came of each

| Repo | Licence | Used for |
|---|---|---|
| `pseudo-r/Public-ESPN-API` | MIT | The ESPN endpoints in `espn.py` (written fresh, no code copied) |
| `EnderLocke/pyespn` | MIT | Cross-check of ESPN's game-log format; not a dependency |
| `swar/nba_api` | MIT | Not usable from a cloud server (NBA.com blocks it). ESPN covers the same game logs |
| `shufinskiy/nba_data` | Apache-2.0 | Play-by-play archives. Not needed for v1; a candidate for a later backtest |
| `declanwalpole/sportsbook-odds-scraper` | **none** (all rights reserved) | Nothing copied. DraftKings' API answers 403 from the server anyway |
| `kaarme01/kaarme-bet-scraper` | MIT | Pinnacle's guest-API endpoints (`pinnacle.py`) |
| `akilatrades/sports-market-maker` | MIT | The evidence above. Also: 2+ books for a consensus, clustered intervals, Kalshi's calibration finding |
| `isahpop/kalshi-nfl-edge` | none | Ideas only: freshness and agreement checks before a price counts, time-stamped evidence |
| `daypatell/nfl-value-board` | none | Ideas only (game-level Elo against DraftKings) |
| `kunalvij4/sharpEdge` | none | Ideas only (weighted multi-book no-vig consensus) |
| `UtterlyWu/Odd-SCRP` | MIT | OddsPortal history via a browser, game lines only. Not used |
| `jp0624/sportsbooks` | none | An arbitrage calculator on typed-in example prices. Not used |
| `dkori/odds-derived-ff-projections` | **AGPL-3.0** | The method only: no-vig, negative binomial counts, measured constants. **No code copied**: AGPL code inside a paid web service would oblige publishing that service's source |
| The Odds API repos (`odds-api`, `oddsapi_ev`, `sports-betting-intelligence`) | various | The documented feed adapter (`books/theoddsapi.py`) follows the same API |
| `sjhouston23/oddswrap` | MIT | FanDuel and Kambi (BetRivers) endpoint shapes for `books/`. Its DraftKings / BetMGM / Caesars readers imitate a Chrome network fingerprint to get past those books' blocks; that part is not used |
| `nflverse` data | CC-BY | The NFL dispersion fit (weekly player stats CSV) |
| `nflverse/nfl_data_py` | MIT | Reads nflverse's published files. Live player data stays scraper-based (ESPN), so it is not a dependency |
| `roclark/sportsipy` | MIT | Scrapes Sports-Reference sites. Not used: Basketball-Reference's robots.txt disallows game logs and splits, and Pro-Football-Reference sits behind a Cloudflare challenge |
| `Erichollman96/NFLDeepDive` | none | Scrapes Pro-Football-Reference season tables (Cloudflare). Not used |
| `jaretpr/NFL_Stats_Props_Analyzer` | MIT | ESPN box scores + The Odds API: the same two sources as `espn.py` / `books/theoddsapi.py` |
| `HenryWinNguyen/nba-muse` | none | A natural-language NBA stats query app (OpenAI + DuckDB). Not a data source |
| `PSN-01/NFL-WRS-RBS-TES-Individual-Stats`, `khirata215/NFL_Data` | none | Notebooks / helpers over nfl_data_py. Nothing new to read |

## Known limits

- **Pinnacle is unavailable from the US**, and that is respected. On the Ohio server the sharp side is Kalshi and
  Polymarket. The sportsbook consensus (`books/`) is shown beside the chance, not inside it.
- **Three books refuse direct requests.** DraftKings, BetMGM and Caesars answer a plain request with 403. Those
  refusals are not worked around (no browser, no disguised client). Their prices need The Odds API (`ODDS_API_KEY`),
  and Caesars needs one of its paid plans.
- **Exchange midpoints may lean slightly toward Over.** sports-market-maker found YES buyers dominate on Kalshi.
  `report` measures it by source.
- **College**: no Kalshi or Polymarket player props. College lines are matched only with an Odds API key (or Pinnacle outside
  the US).
- **Stats with no sharp price** (targets, turnovers in most games, fantasy score, longest play, touchdown scorers) get
  the history baseline only, never a sharp chance.
- **Name matching** is exact after normalising (accents, punctuation, Jr./III). A player two sources spell differently
  goes in `player_aliases.json`, after checking it's the same player.
- **The dispersion fit** uses whole-season variance. That's wider than the spread around a game-day line, so a moved
  line changes the chance a little less than it probably should. That's the cautious direction.
- **MLB / NHL** are not mapped yet. The league table and market maps are where they would go.
