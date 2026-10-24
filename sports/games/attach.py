"""Board hook: each pick'em row gains "factors" - the contract payload (contract.py) for its player, stat and game,
computed from the game store as of now. Read-only: no provider is called except ESPN's cached team list and
scoreboard (the same cached reads the board already makes). A row whose player or game is not in the store gets
"factors": None. Factors are shown and logged beside the chance; they never change it.
"""
from datetime import datetime, timedelta, timezone

from .. import espn
from . import factors, store
from .store import dt

# board abbreviations ESPN spells differently (PrizePicks / Underdog -> ESPN)
ESPN_ABBR = {"NYK": "NY", "GSW": "GS", "SAS": "SA", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH", "LA": "LAR"}
START_TOLERANCE_H = 4


def _team_ids(lg):
    by = {}
    for t in espn.teams(lg):
        by[t["abbr"]] = str(t["id"])
    for board, ours in ESPN_ABBR.items():
        if ours in by:
            by.setdefault(board, by[ours])
    return by


def compact(payload):
    """What a forecast row keeps: contract, as_of and the values (the missing-reasons are in the registry)."""
    return None if not payload else {"contract": payload["contract"], "as_of": payload["as_of"],
                                     "event_id": payload["event_id"], "athlete_id": payload["athlete_id"],
                                     "f": payload["factors"]}


def attach(lg, rows, db=None, now=None):
    if not rows:
        return rows
    now = now or datetime.now(timezone.utc)
    own = db is None
    db = db or store.connect()
    try:
        data = factors.LeagueData(db, lg["key"], now)
        aths = {}
        for a in db.all("SELECT athlete_id, player_key, team_id FROM sports_game_athletes WHERE league = ?",
                        (lg["key"],)):
            aths.setdefault(a["player_key"], []).append(a)
        tid = _team_ids(lg)
        games = espn.scoreboard(lg, (now - timedelta(days=1)).date(), (now + timedelta(days=8)).date())
        memo = {}
        for r in rows:
            r["factors"] = None
            team = tid.get(r.get("team") or "")
            cands = aths.get(r.get("player_key")) or []
            if team and len(cands) > 1:
                cands = [a for a in cands if a["team_id"] == team]
            if len(cands) != 1 or not r.get("start") or not r.get("market"):
                continue
            a = cands[0]
            team = team or a["team_id"]
            t = dt(r["start"])
            gm = next((e for e in games if e.get("start") and team in (str((e.get("home") or {}).get("id")),
                                                                         str((e.get("away") or {}).get("id")))
                       and abs((dt(e["start"]) - t).total_seconds()) <= START_TOLERANCE_H * 3600), None)
            if not gm:
                continue
            home = str(gm["home"]["id"]) == team
            opp = str(gm["away"]["id"] if home else gm["home"]["id"])
            k = (a["athlete_id"], r["market"], gm["id"])
            if k not in memo:
                memo[k] = factors.compute(db, data, a["athlete_id"], team, opp, gm["id"], gm["start"], r["market"],
                                          home=home)
            r["factors"] = memo[k]
    finally:
        if own:
            db.close()
    return rows
