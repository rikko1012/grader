"""Player role tags for display (owner 2026-11-07: roles on every player card - PG / SG / SF / PF / C, QB / RB / WR / TE).

DISPLAY ONLY. A separate "role" field beside the snapshot's position / pos, which the sports models read and which stay as
they are. Never a model input.

Sources, in order:
  1. ESPN's depth chart position (espn.parse_depth "pos": NBA/WNBA pg/sg/sf/pf/c; NFL qb/rb/wr1-3/te), read CACHE-ONLY
     from the copy the pregame-news job keeps fresh (cache "espn:depth", 6-h refresh). A depth row counts only when its
     team is the player's team: a namesake on another team is ignored.
  2. ESPN's roster position (Player.position: G / F / C / G-F; QB / RB / WR / TE / K ...), shown as given ("G-F" -> "G/F").
Neither = None, and the page shows nothing."""
from . import cache, names

DEPTH_LEAGUES = ("nba", "wnba", "nfl")
DEPTH_MAX_AGE_S = 24 * 3600


def depth_cached(league):
    """The league's depth chart index from the shared cache only - never an ESPN read (a miss would be ~30 requests).
    A missing or older copy = {} (roster positions only)."""
    if league not in DEPTH_LEAGUES:
        return {}
    try:
        hit = cache.get("espn:depth", league, DEPTH_MAX_AGE_S)
        if hit is None:
            hit = cache.get("espn:depth_partial", league, DEPTH_MAX_AGE_S)
        return hit if isinstance(hit, dict) else {}
    except Exception:
        return {}


def _roster(pos):
    p = str(pos or "").strip().upper()
    return p.replace("-", "/") or None


def role(league, player_name, team_abbr, roster_pos, depth_idx=None):
    """The role text for one player of one team, or None."""
    row = (depth_idx or {}).get(names.key(player_name or "")) if player_name else None
    if row and team_abbr and row.get("pos") and names.team_abbr(league, row.get("team")) == names.team_abbr(league, team_abbr):
        p = str(row["pos"]).strip().upper()
        if league == "nfl":
            p = p.rstrip("0123456789")               # WR2 -> WR (the slot number is the depth chart's, not a position)
        if p:
            return p
    return _roster(roster_pos)
