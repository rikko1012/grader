"""Measure each market's spread (the variance function fair.py uses) on last season's box scores -> dispersion.json.

    python -m sports.fit_dispersion NBA_CSV NFL_CSV

NBA_CSV: sportsdataverse ESPN player box scores, 2025-26 season
         https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/player_box_2026.csv
NFL_CSV: nflverse weekly player stats, 2025 season
         https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2025.csv

Method (one season, regular season + playoffs): every player-season with enough games gives a mean m and a sample
variance v of the market's number. Fitted by games-weighted least squares through the origin:
  count  shape: v - m = b*m^2           (negative binomial; b = 0 is Poisson)
  normal shape: v = a*m + b*m^2         (b < 0 refits with b = 0)
A season's variance also holds the player's role changing during the season, so it is WIDER than the spread around a
game-day line that already knows tonight's minutes / role. Wider means fair.py moves a chance less per point of line
gap - the cautious direction. WNBA / college use the same family's numbers (not measured on their own data).
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

from . import config, markets

NBA_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/"
           "player_box_2026.csv")
NFL_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2025.csv"

NBA_COLS = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "three_point_field_goals_made",
            "stl": "steals", "blk": "blocks", "tov": "turnovers"}
NFL_COLS = {"pass_yds": "passing_yards", "pass_att": "attempts", "pass_cmp": "completions", "pass_td": "passing_tds",
            "pass_int": "passing_interceptions", "rush_yds": "rushing_yards", "rush_att": "carries",
            "rec": "receptions", "rec_yds": "receiving_yards", "targets": "targets"}
# which NFL players a market's props are written for: (games filter, player filter on the per-game averages)
NFL_POOL = {"pass": (lambda g: g["pass_att"] >= 10, lambda avg: True),
            "rush": (lambda g: True, lambda avg: avg["rush_att"] >= 8),
            "rec": (lambda g: True, lambda avg: avg["targets"] >= 3),
            "skill": (lambda g: True, lambda avg: avg["rush_att"] + avg["targets"] >= 5)}
NFL_MARKET_POOL = {"pass_yds": "pass", "pass_att": "pass", "pass_cmp": "pass", "pass_td": "pass", "pass_int": "pass",
                   "pass_rush_yds": "pass", "rush_yds": "rush", "rush_att": "rush", "rec": "rec", "rec_yds": "rec",
                   "targets": "rec", "rush_rec_yds": "skill"}
MIN_GAMES = {"basketball": 20, "football": 6}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def fit(groups, shape):
    """groups: [(mean, var, n)] -> (a, b)."""
    if shape == "count":
        num = sum(n * (v - m) * m * m for m, v, n in groups)
        den = sum(n * m ** 4 for m, v, n in groups)
        return 1.0, max(0.0, num / den) if den else 0.0
    s11 = sum(n * m * m for m, v, n in groups)
    s12 = sum(n * m ** 3 for m, v, n in groups)
    s22 = sum(n * m ** 4 for m, v, n in groups)
    t1 = sum(n * v * m for m, v, n in groups)
    t2 = sum(n * v * m * m for m, v, n in groups)
    det = s11 * s22 - s12 * s12
    a, b = ((t1 * s22 - t2 * s12) / det, (s11 * t2 - s12 * t1) / det) if det else (t1 / s11, 0.0)
    if b < 0:
        a, b = t1 / s11, 0.0
    return a, b


def _moments(values):
    n = len(values)
    m = sum(values) / n
    v = sum((x - m) ** 2 for x in values) / (n - 1)
    return m, v, n


def nba_groups(path):
    games = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("did_not_play") == "true" or _f(r.get("minutes")) <= 0 or r.get("season_type") not in ("2", "3"):
                continue
            games[r["athlete_id"]].append({**{k: _f(r[c]) for k, c in NBA_COLS.items()}, "min": _f(r["minutes"])})
    out = defaultdict(list)
    for gs in games.values():
        if len(gs) < MIN_GAMES["basketball"] or sum(g["min"] for g in gs) / len(gs) < 20:
            continue                                   # props are written for rotation players
        for mk in markets.MARKETS["basketball"]:
            vals = [markets.value("basketball", mk, g) for g in gs]
            m, v, n = _moments(vals)
            if m > 0:
                out[mk].append((m, v, n))
    return out


def nfl_groups(path):
    games = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("season_type") not in ("REG", "POST"):
                continue
            games[r["player_id"]].append({k: _f(r[c]) for k, c in NFL_COLS.items()})
    out = defaultdict(list)
    for gs in games.values():
        for mk, pool in NFL_MARKET_POOL.items():
            gfilter, pfilter = NFL_POOL[pool]
            sel = [g for g in gs if gfilter(g)]
            if len(sel) < MIN_GAMES["football"]:
                continue
            avg = {k: sum(g[k] for g in sel) / len(sel) for k in NFL_COLS}
            if not pfilter(avg):
                continue
            m, v, n = _moments([markets.value("football", mk, g) for g in sel])
            if m > 0:
                out[mk].append((m, v, n))
    return out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit(__doc__)
    result = {"_fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
              "_sources": {"basketball": NBA_URL, "football": NFL_URL},
              "_method": "games-weighted least squares on player-season mean/variance (fit_dispersion.py docstring)"}
    for family, groups in (("basketball", nba_groups(argv[0])), ("football", nfl_groups(argv[1]))):
        result[family] = {}
        for mk, spec in markets.MARKETS[family].items():
            g = groups.get(mk) or []
            if len(g) < 10:
                continue
            a, b = fit(g, spec["shape"])
            result[family][mk] = {"shape": spec["shape"], "a": round(a, 4), "b": round(b, 4), "players": len(g),
                                  "games": sum(n for _, _, n in g)}
    config.DISPERSION_FILE.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
