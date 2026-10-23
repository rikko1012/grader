"""The markets the sports package understands, and how each source names them.

A market is a canonical key (e.g. "pra") with the box-score parts it adds up ("pts" + "reb" + "ast") and the shape of its
outcome distribution used by fair.py ("count": negative binomial; "normal": normal with a continuity correction).

Every source's stat name is matched WHOLE and exactly (the CS2 audit C14 rule): "Points (Combo)", "1H Points",
"period_1_receiving_yds", fantasy scores, longest-play and touchdown-scorer markets match nothing and are never graded.
A stat a sharp book does not price (e.g. targets) still gets the history baseline, never a sharp chance.
"""

MARKETS = {
    "basketball": {
        "points": {"label": "Points", "parts": ("pts",), "shape": "normal"},
        "rebounds": {"label": "Rebounds", "parts": ("reb",), "shape": "count"},
        "assists": {"label": "Assists", "parts": ("ast",), "shape": "count"},
        "threes": {"label": "3-pointers made", "parts": ("fg3m",), "shape": "count"},
        "pra": {"label": "Pts+Rebs+Asts", "parts": ("pts", "reb", "ast"), "shape": "normal"},
        "pr": {"label": "Pts+Rebs", "parts": ("pts", "reb"), "shape": "normal"},
        "pa": {"label": "Pts+Asts", "parts": ("pts", "ast"), "shape": "normal"},
        "ra": {"label": "Rebs+Asts", "parts": ("reb", "ast"), "shape": "count"},
        "steals": {"label": "Steals", "parts": ("stl",), "shape": "count"},
        "blocks": {"label": "Blocks", "parts": ("blk",), "shape": "count"},
        "stocks": {"label": "Blks+Stls", "parts": ("blk", "stl"), "shape": "count"},
        "turnovers": {"label": "Turnovers", "parts": ("tov",), "shape": "count"},
    },
    "football": {
        "pass_yds": {"label": "Pass yards", "parts": ("pass_yds",), "shape": "normal"},
        "pass_att": {"label": "Pass attempts", "parts": ("pass_att",), "shape": "count"},
        "pass_cmp": {"label": "Pass completions", "parts": ("pass_cmp",), "shape": "count"},
        "pass_td": {"label": "Pass TDs", "parts": ("pass_td",), "shape": "count"},
        "pass_int": {"label": "Interceptions thrown", "parts": ("pass_int",), "shape": "count"},
        "rush_yds": {"label": "Rush yards", "parts": ("rush_yds",), "shape": "normal"},
        "rush_att": {"label": "Rush attempts", "parts": ("rush_att",), "shape": "count"},
        "rec": {"label": "Receptions", "parts": ("rec",), "shape": "count"},
        "rec_yds": {"label": "Receiving yards", "parts": ("rec_yds",), "shape": "normal"},
        "rush_rec_yds": {"label": "Rush+Rec yards", "parts": ("rush_yds", "rec_yds"), "shape": "normal"},
        "pass_rush_yds": {"label": "Pass+Rush yards", "parts": ("pass_yds", "rush_yds"), "shape": "normal"},
        "targets": {"label": "Targets", "parts": ("targets",), "shape": "count"},
    },
}

# PrizePicks stat_type (partner API, read 2026-10-23 on the NBA / NFL boards)
PP = {
    "basketball": {"Points": "points", "Rebounds": "rebounds", "Assists": "assists", "3-PT Made": "threes",
                   "Pts+Rebs+Asts": "pra", "Pts+Rebs": "pr", "Pts+Asts": "pa", "Rebs+Asts": "ra", "Steals": "steals",
                   "Blocked Shots": "blocks", "Blks+Stls": "stocks", "Turnovers": "turnovers"},
    "football": {"Pass Yards": "pass_yds", "Pass Attempts": "pass_att", "Pass Completions": "pass_cmp",
                 "Pass TDs": "pass_td", "INT": "pass_int", "Rush Yards": "rush_yds", "Rush Attempts": "rush_att",
                 "Receptions": "rec", "Receiving Yards": "rec_yds", "Rush+Rec Yds": "rush_rec_yds",
                 "Pass+Rush Yds": "pass_rush_yds", "Rec Targets": "targets"},
}
# Underdog over_under.appearance_stat.stat
UD = {
    "basketball": {"points": "points", "rebounds": "rebounds", "assists": "assists", "three_points_made": "threes",
                   "pts_rebs_asts": "pra", "pts_rebs": "pr", "pts_asts": "pa", "rebs_asts": "ra", "steals": "steals",
                   "blocks": "blocks", "blks_stls": "stocks", "turnovers": "turnovers"},
    "football": {"passing_yds": "pass_yds", "passing_att": "pass_att", "passing_comps": "pass_cmp",
                 "passing_tds": "pass_td", "passing_ints": "pass_int", "rushing_yds": "rush_yds",
                 "rushing_att": "rush_att", "receiving_rec": "rec", "receiving_yds": "rec_yds",
                 "rush_rec_yds": "rush_rec_yds", "passing_and_rushing_yds": "pass_rush_yds",
                 "receiving_tgts": "targets"},
}
# Pinnacle special "units" (guest API, Player Props category; description "<player> Total <stat>")
PIN = {
    "basketball": {"Points": "points", "Rebounds": "rebounds", "Assists": "assists", "Threes Made": "threes",
                   "Pts & Rebs & Asts": "pra", "Pts & Rebs": "pr", "Pts & Asts": "pa", "Rebs & Asts": "ra",
                   "Steals": "steals", "Blocks": "blocks", "Turnovers": "turnovers"},
    "football": {"Passing Yards": "pass_yds", "Pass Attempts": "pass_att", "Pass Completions": "pass_cmp",
                 "Touchdown Passes": "pass_td", "Interceptions": "pass_int", "Rushing Yards": "rush_yds",
                 "Rush Attempts": "rush_att", "Receptions": "rec", "Receiving Yards": "rec_yds"},
}
# The Odds API market keys (main and alternate lines; the-odds-api.com betting markets list)
ODDS_API = {
    "basketball": {"player_points": "points", "player_rebounds": "rebounds", "player_assists": "assists",
                   "player_threes": "threes", "player_points_rebounds_assists": "pra",
                   "player_points_rebounds": "pr", "player_points_assists": "pa", "player_rebounds_assists": "ra",
                   "player_steals": "steals", "player_blocks": "blocks", "player_blocks_steals": "stocks",
                   "player_turnovers": "turnovers"},
    "football": {"player_pass_yds": "pass_yds", "player_pass_attempts": "pass_att",
                 "player_pass_completions": "pass_cmp", "player_pass_tds": "pass_td",
                 "player_pass_interceptions": "pass_int", "player_rush_yds": "rush_yds",
                 "player_rush_attempts": "rush_att", "player_receptions": "rec", "player_reception_yds": "rec_yds",
                 "player_rush_reception_yds": "rush_rec_yds", "player_pass_rush_yds": "pass_rush_yds"},
}


def market(family, key):
    return MARKETS.get(family, {}).get(key)


def value(family, key, parts):
    """The market's number from box-score parts ({"pts": 20, "reb": 7, ...}); None when a part is missing."""
    m = market(family, key)
    if not m or parts is None:
        return None
    total = 0.0
    for p in m["parts"]:
        v = parts.get(p)
        if v is None:
            return None
        total += v
    return total


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _made(s):
    """'5-19' or '22/37' -> (5.0, 19.0); anything else -> (None, None)."""
    for sep in ("-", "/"):
        if isinstance(s, str) and sep in s:
            a, _, b = s.partition(sep)
            return _num(a), _num(b)
    return None, None


# ESPN box-score / game-log stat names -> parts. Basketball box scores carry labels (PTS, REB ...); game logs and football
# box scores carry names (points, totalRebounds ...). A "made-attempted" cell fills two parts.
_ESPN_ONE = {"PTS": "pts", "points": "pts", "REB": "reb", "totalRebounds": "reb", "rebounds": "reb", "AST": "ast",
             "assists": "ast", "STL": "stl", "steals": "stl", "BLK": "blk", "blocks": "blk", "TO": "tov",
             "turnovers": "tov", "MIN": "min", "minutes": "min",
             "passingYards": "pass_yds", "passingTouchdowns": "pass_td", "interceptions": "pass_int",
             "completions": "pass_cmp", "passingAttempts": "pass_att", "rushingAttempts": "rush_att",
             "rushingYards": "rush_yds", "receptions": "rec", "receivingYards": "rec_yds",
             "receivingTargets": "targets"}
_ESPN_PAIR = {"3PT": ("fg3m", "fg3a"), "threePointFieldGoalsMade-threePointFieldGoalsAttempted": ("fg3m", "fg3a"),
              "completions/passingAttempts": ("pass_cmp", "pass_att")}


def espn_parts(names, stats, group=None):
    """One ESPN stat row -> parts. group is the football box-score group ("passing", "receiving" ...): 'interceptions'
    means thrown only in the passing group (a defender's interceptions are another stat)."""
    out = {}
    for n, s in zip(names or [], stats or []):
        if n in _ESPN_PAIR:
            a, b = _made(s)
            if a is not None:
                out[_ESPN_PAIR[n][0]], out[_ESPN_PAIR[n][1]] = a, b
        elif n in _ESPN_ONE:
            if n == "interceptions" and group not in (None, "passing"):
                continue
            v = _num(s)
            if v is not None:
                out[_ESPN_ONE[n]] = v
    return out


def defaults_for(family, parts, played):
    """A player who played but is missing from a football box-score group made 0 of it (no catches, no carries)."""
    if family != "football" or not played:
        return parts
    full = {p: 0.0 for m in MARKETS["football"].values() for p in m["parts"]}
    full.update(parts)
    return full
