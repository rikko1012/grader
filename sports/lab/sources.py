"""Read-only data adapters for the lab. Each yields player-game rows

  {"row_key", "game_id", "player_id", "start_utc", "known_at", "home": 1|0|None, "played": bool,
   "v": {<stat or aux key>: float|None}, "refs": [source record ids]}

  ds-1       the model agent's historical tables, <SPORTS_DATA_DIR>/model/history.db (sports/model/datasets.py, schema
             ds-1: games + nba_pg / nfl_pg), opened mode=ro. Not imported: read through its documented schema so the lab
             does not depend on the model package's internals. Missing file -> source unavailable (reported).
  canonical  ESPN StatObservations in the canonical store (latest revision known at the cutoff), events for start
             times, memberships for home / away.
Only rows of games that started before `cutoff` and were known by it are returned.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import config as sconfig, store

TARGETS = {"nba": ("points", "rebounds", "assists", "threes"),
           "nfl": ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec")}
DS1_COL = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m", "pass_yds": "pass_yds",
           "rush_yds": "rush_yds", "rec_yds": "rec_yds", "rush_att": "carries", "rec": "rec"}
CANON_PART = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m", "pass_yds": "pass_yds",
              "rush_yds": "rush_yds", "rec_yds": "rec_yds", "rush_att": "rush_att", "rec": "rec"}
AUX_DS1 = {"nba": {"min": "min", "starter": "starter"},
           "nfl": {"targets": "targets", "carries": "carries", "pass_att": "pass_att", "snap_pct": "offense_pct"}}
AUX_CANON = {"nba": {}, "nfl": {"targets": "targets", "carries": "rush_att", "pass_att": "pass_att"}}


def ds1_path():
    return sconfig.DATA_DIR / "model" / "history.db"


def availability():
    out = {"canonical": store.db_path().exists(), "ds-1": ds1_path().exists()}
    return out


def _ds1(league, stat, cutoff, path=None):
    p = Path(path) if path else ds1_path()
    if not p.exists():
        raise FileNotFoundError(f"ds-1 history not built yet ({p.name}); run the model agent's dataset job")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        tbl = "nba_pg" if league == "nba" else "nfl_pg"
        col = DS1_COL[stat]
        aux = AUX_DS1[league]
        cols = ", ".join(f"p.{c} AS a_{k}" for k, c in aux.items())
        played = "p.played" if league == "nba" else "1"
        q = (f"SELECT p.game_id, p.player_id, p.home, {played} AS played, p.{col} AS val, {cols}, g.start_utc, "
             f"g.known_at FROM {tbl} p JOIN games g ON g.league = ? AND g.game_id = p.game_id "
             f"WHERE g.start_utc < ? AND g.known_at IS NOT NULL AND g.known_at <= ?")
        rows = con.execute(q, (league, cutoff, cutoff)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        v = {stat: None if r["val"] is None else float(r["val"])}
        for k in aux:
            x = r[f"a_{k}"]
            v[k] = None if x is None else float(x)
        out.append({"row_key": f"ds-1:{r['game_id']}:{r['player_id']}", "game_id": f"{league}:{r['game_id']}",
                    "player_id": f"ds-1:{league}:{r['player_id']}", "start_utc": r["start_utc"],
                    "known_at": r["known_at"], "home": None if r["home"] is None else int(r["home"]),
                    "played": bool(r["played"]), "v": v, "refs": [f"ds-1:{'nba_pg' if league == 'nba' else 'nfl_pg'}:"
                                                                 f"{r['game_id']}:{r['player_id']}"]})
    return out


def _canonical(league, stat, cutoff, path=None):
    parts = {stat: CANON_PART[stat], **AUX_CANON[league]}
    with store.opened(path, read_only=True) as con:
        events = {ev.event_id: ev for _, ev in store.events(con, league=league, as_of=cutoff, limit=100000)}
        mem = {}
        for r in con.execute("SELECT payload FROM records WHERE kind='membership' AND player_id LIKE ? AND "
                             "known_at <= ?", (f"{league}:%", cutoff)).fetchall():
            m = store._unpack(r["payload"])
            mem.setdefault(m["player_id"], []).append(m)
        rows = {}
        for key, part in parts.items():
            q = ("SELECT r.record_id, r.known_at, r.payload FROM records r JOIN (SELECT source, source_key, "
                 "MAX(revision) rv FROM records WHERE kind='stat' AND league=? AND stat=? AND known_at <= ? GROUP BY "
                 "source, source_key) z ON r.source=z.source AND r.source_key=z.source_key AND r.revision=z.rv "
                 "WHERE r.kind='stat'")
            for rec in con.execute(q, (league, part, cutoff)).fetchall():
                o = store._unpack(rec["payload"])
                ev = events.get(o["event_id"])
                if ev is None or not ev.start_utc or ev.start_utc >= cutoff or o["participant_type"] != "player":
                    continue
                rk = f"canonical:{o['event_id']}:{o['participant_id']}"
                row = rows.setdefault(rk, {"row_key": rk, "game_id": o["event_id"], "player_id": o["participant_id"],
                                           "start_utc": ev.start_utc, "known_at": rec["known_at"], "home": None,
                                           "played": True, "v": {}, "refs": [], "_ev": ev})
                row["v"][key] = o["value"]
                row["refs"].append(rec["record_id"])
                row["known_at"] = max(row["known_at"], rec["known_at"])
                if key == stat and o["participation"] != "played":
                    row["played"] = False
    out = []
    for row in rows.values():
        ev = row.pop("_ev")
        team = None
        for m in mem.get(row["player_id"], []):
            if m["valid_from"] <= row["start_utc"] and (not m.get("valid_to") or m["valid_to"] > row["start_utc"]):
                team = m["team_id"]
        if team and team == ev.home_team_id:
            row["home"] = 1
        elif team and team == ev.away_team_id:
            row["home"] = 0
        if stat not in row["v"]:
            continue
        out.append(row)
    return out


def load(source, league, stat, cutoff, path=None):
    if stat not in TARGETS[league]:
        raise ValueError(f"{stat} is not a lab target for {league} (targets: {', '.join(TARGETS[league])})")
    if source == "ds-1":
        return _ds1(league, stat, cutoff, path)
    if source == "canonical":
        return _canonical(league, stat, cutoff, path)
    raise ValueError("source must be ds-1 or canonical")
