"""Factor-engine values for every player on the board, written per player for the member player page (scheduler job
board-factors; SPORTS_INGEST - it reads the game store and the canonical store, writes snapshot files only).

    SPORTS_INGEST=1 python -m sports.games.board_factors build nba nfl

For each league: every open offer of a not-started ESPN game in the canonical store -> (player, game, stat) -> the
contract payload (factors.compute, sports-factors/1) computed as of now from the game store. NFL rows also get the
nflverse usage numbers the game store does not hold (snap share, air-yards share, WOPR) from the installed history
copy (model/fwd/history.db, refreshed every 12 h by model-forward; else the training history.db).

Output: <SPORTS_DATA_DIR>/snapshots/<league>/factors/<safe player id>.json, written atomically:
  {"contract", "as_of", "player_id", "event_id", "by_stat": {stat: {key: value}}, "labels": {key: [group, label]},
   "missing": {key: reason} (the stat-independent reasons once), "not_held": [labels of factors no held source has]}
snapshots.build embeds the file into the player snapshot as "engine"; nothing here changes a forecast or a chance.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .. import config, flags, store as cstore
from . import contract, factors, store as gstore

NFLV_KEYS = ("snap_share_l3", "snap_share_season", "air_yds_share", "wopr")


def out_dir(league, base=None):
    from ..snapshots import snap_root                 # same layout as the board snapshots
    return snap_root(base) / league / "factors"


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def board_pairs(con, league, now_iso):
    """{(player_id, event_id): {stats}} for open offers of not-started ESPN games, plus the events."""
    events = {e.event_id: e for _, e in cstore.events(con, league)
              if e.start_utc and e.start_utc > now_iso and e.status not in ("cancelled",)}
    pairs = defaultdict(set)
    for _, o in cstore.offers(con, league=league, limit=500000):
        if o.event_id in events and o.player_id and o.stat and o.availability == "open" and o.scope == "full_game":
            pairs[(o.player_id, o.event_id)].add(o.stat)
    return pairs, events


# ---------------------------------------------------------------------------------------------- nflverse extras
def _nflv_db():
    from ..model import datasets
    for p in (config.DATA_DIR / "model" / "fwd" / "history.db", datasets.db_path()):
        if p.exists():
            con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
            con.row_factory = sqlite3.Row
            return con
    return None


def nflverse_usage(hcon, espn_to_gsis, athlete_id, season):
    """Snap share (L3 / season), air-yards share and WOPR (mean of the last 5 games) for one ESPN athlete. -> dict."""
    gsis = espn_to_gsis.get(str(athlete_id))
    if not gsis:
        return {}
    rows = hcon.execute("SELECT season, week, offense_pct, air_yards_share, wopr FROM nfl_pg WHERE player_id = ? "
                        "ORDER BY season DESC, week DESC LIMIT 25", (gsis,)).fetchall()
    if not rows:
        return {}

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None
    snaps = [r for r in rows if r["offense_pct"] is not None]
    cur = [r for r in snaps if r["season"] == season] or [r for r in snaps if r["season"] == rows[0]["season"]]
    return {"snap_share_l3": mean([r["offense_pct"] for r in snaps[:3]]),
            "snap_share_season": mean([r["offense_pct"] for r in cur]),
            "air_yds_share": mean([r["air_yards_share"] for r in rows[:5]]),
            "wopr": mean([r["wopr"] for r in rows[:5]])}


# --------------------------------------------------------------------------------------------------------- build
def build_league(league, now=None, con=None, db=None, hcon=None):
    """-> counts. Writes one factors file per board player of `league`."""
    now = now or datetime.now(timezone.utc)
    own_con, own_db = con is None, db is None
    con = con or cstore.connect()
    db = db or gstore.connect()
    t0 = time.time()
    out = {"league": league, "players": 0, "pairs": 0, "skipped": defaultdict(int)}
    try:
        pairs, events = board_pairs(con, league, _iso(now))
        data = factors.LeagueData(db, league, now)
        reg = contract.REGISTRY[league]
        labels = {k: [s["group"], s["label"]] for k, s in reg.items() if s["status"] != "unavailable"}
        not_held = [s["label"] for s in reg.values() if s["status"] == "unavailable"]
        ath_team = {r["athlete_id"]: r["team_id"] for r in
                    db.all("SELECT athlete_id, team_id FROM sports_game_athletes WHERE league = ?", (league,))}
        nfl = config.league(league)["family"] == "football"
        espn_to_gsis, hown = {}, False
        if nfl:
            if hcon is None:
                hcon, hown = _nflv_db(), True
            if hcon is not None:
                espn_to_gsis = {r[0]: r[1] for r in
                                hcon.execute("SELECT espn_id, gsis_id FROM nfl_ids WHERE espn_id IS NOT NULL")}
        season = data.season
        root = out_dir(league)
        written = set()
        for (pid, eid), stats in sorted(pairs.items()):
            e = events[eid]
            ath, ev = pid.split(":")[-1], eid.split(":")[-1]
            home, away = e.home_team_id.split(":")[-1], e.away_team_id.split(":")[-1]
            tid = ath_team.get(ath)
            if tid not in (home, away):                 # roster not read yet / traded: the team of his latest game
                r = db.one("SELECT p.team_id FROM sports_game_players p JOIN sports_game_games g ON g.league = p.league "
                           "AND g.event_id = p.event_id WHERE p.league = ? AND p.athlete_id = ? ORDER BY g.start DESC "
                           "LIMIT 1", (league, ath))
                tid = r["team_id"] if r else tid
            if tid not in (home, away):
                out["skipped"]["player not on either team in the game store"] += 1
                continue
            opp = away if tid == home else home
            by_stat, missing = {}, {}
            extra = nflverse_usage(hcon, espn_to_gsis, ath, season) if espn_to_gsis else {}
            for stat in sorted(stats):
                p = factors.compute(db, data, ath, tid, opp, ev, e.start_utc, stat, home=(tid == home))
                vals = {k: v for k, v in p["factors"].items() if v is not None}
                vals.update({k: v for k, v in extra.items() if v is not None})
                by_stat[stat] = vals
                for k, why in (p.get("missing") or {}).items():
                    if k not in vals and reg.get(k, {}).get("status") != "unavailable":
                        missing.setdefault(k, why)
                out["pairs"] += 1
            for k in NFLV_KEYS:
                if nfl and k not in extra:
                    missing.setdefault(k, "no nflverse row for this player (history copy not installed or unmapped)")
            from ..snapshots import safe_name
            _atomic_json(root / f"{safe_name(pid)}.json",
                         {"contract": contract.CONTRACT, "as_of": _iso(now), "player_id": pid, "event_id": eid,
                          "by_stat": by_stat, "labels": labels, "missing": missing, "not_held": not_held})
            written.add(f"{safe_name(pid)}.json")
            out["players"] += 1
        for f in root.glob("*.json") if root.exists() else []:   # players no longer on the board
            if f.name not in written:
                f.unlink()
        if hown and hcon is not None:
            hcon.close()
    finally:
        if own_con:
            con.close()
        if own_db:
            db.close()
    out["skipped"] = dict(out["skipped"])
    out["build_s"] = round(time.time() - t0, 1)
    return out


def read(league, player_id, base=None):
    """The player's factors file, or None (snapshots.build embeds it)."""
    from ..snapshots import safe_name
    try:
        return json.loads((out_dir(league, base) / f"{safe_name(player_id)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.games.board_factors")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("leagues", nargs="*", default=["nba", "nfl"])
    a = p.parse_args(argv)
    flags.require("SPORTS_INGEST")
    res = {lg: build_league(lg) for lg in a.leagues}
    print(json.dumps(res, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
