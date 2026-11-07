"""Scheduled, time-bounded game-store backfill (scheduler job games-backfill; SPORTS_INGEST).

    SPORTS_INGEST=1 python -m sports.games.backfill_job [--budget 150] [--leagues nba nfl]

The game store (sports/games/store.py) fills from live settlement only, so on a new service the factor engine
(usage, pace, defense vs position, shot zones, on/off, red-zone and air-yard shares - sports/games/factors.py) would
rest on a handful of games. This job loads whole seasons with the same per-game read as `games-backfill`
(collect.ingest_game: ESPN summary -> box score + play-by-play + derived rows), oldest-missing first within a season,
until the time budget is spent; every run resumes where the last stopped (a stored game is skipped).

Order: each league's CURRENT season, then the PREVIOUS season (the factor engine's early-season window and its league
baselines use both). A finished previous season is marked done and never re-listed; the current season is re-listed
at most every RELIST_H (new games after that are the live collector's).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from .. import config, flags, net
from . import collect, espn_game, store

LEAGUES = ("nba", "nfl")
TYPES = (2, 3)                       # regular season, postseason
RELIST_H = 6
DEFAULT_BUDGET_S = 150


PACK_PER_RUN = 500
COMPACT_MIN_FREE_MB = 32


def compact(db):
    """Give the space freed by packing back to the disk (SQLite VACUUM), once every legacy play row is packed and at
    least COMPACT_MIN_FREE_MB is free. A busy database is left for the next run. -> what happened."""
    import os
    import sqlite3
    if os.environ.get("SPORTS_DB_DSN"):
        return "postgres: not needed"
    if db.one("SELECT 1 AS x FROM sports_game_plays LIMIT 1"):
        return "waiting: legacy play rows left"
    free = db.one("PRAGMA freelist_count")
    size = db.one("PRAGMA page_size")
    free_mb = (list(free.values())[0] * list(size.values())[0]) / 1e6 if free and size else 0
    if free_mb < COMPACT_MIN_FREE_MB:
        return f"skipped: {free_mb:.0f} MB free"
    try:
        db.commit()
        db.execute("VACUUM")
        return f"vacuumed: {free_mb:.0f} MB returned"
    except sqlite3.OperationalError as e:
        return f"busy, next run: {str(e)[:80]}"


def _seasons(lg, now):
    cur = collect.season_year(lg, now)
    return [(cur, False), (cur - 1, True)]          # (season, finished)


def _key(league, season, t):
    return f"backfill:{league}:{season}:{t}"


def run(db, leagues=LEAGUES, budget_s=DEFAULT_BUDGET_S, now=None, clock=time.monotonic, ingest=None, lister=None):
    """-> counts per league / season. ingest(db, lg, event_id) and lister(lg, season, type) are injectable (tests)."""
    now = now or datetime.now(timezone.utc)
    ingest = ingest or collect.ingest_game
    lister = lister or espn_game.season_event_ids
    t_end = clock() + budget_s
    out = {}
    for league in leagues:
        lg = config.league(league)
        for season, finished in _seasons(lg, now):
            for t in TYPES:
                if clock() >= t_end:
                    out["budget"] = "spent"
                    return out
                k = _key(league, season, t)
                mark = store.setting(db, k)
                if mark:
                    m = json.loads(mark)
                    if m.get("done") or (now - datetime.fromisoformat(m["listed_at"])) < timedelta(hours=RELIST_H):
                        continue
                c = out.setdefault(f"{league}:{season}:{t}", {"stored": 0, "have": 0, "not_final": 0, "errors": 0})
                try:
                    ids = lister(lg, season, t)
                except net.HttpError as e:
                    c["errors"] += 1
                    c["list_error"] = str(e)[:120]
                    continue
                unplayed, complete = 0, True
                for ev in ids:
                    if unplayed >= collect.STOP_AFTER_UNPLAYED:   # ESPN lists a season in date order: the rest is future
                        break
                    if clock() >= t_end:
                        complete = False
                        break
                    if store.has_game(db, lg["key"], ev):
                        c["have"] += 1
                        continue
                    r = ingest(db, lg, ev)
                    if r == "stored":
                        c["stored"] += 1
                        unplayed = 0
                    elif r == "not final":
                        c["not_final"] += 1
                        unplayed += 1
                    else:
                        c["errors"] += 1
                        unplayed = 0
                if complete:
                    # a finished season is done for good only when no game in the pass failed: a held / failed read
                    # (ESPN Retry-After, outage) is transient and must never become a permanent "done" (owner rule)
                    store.set_setting(db, k, json.dumps({"listed_at": now.isoformat(),
                                                         "done": bool(finished and c["errors"] == 0),
                                                         "games": len(ids), "errors": c["errors"]}))
                else:
                    out["budget"] = "spent"
                    return out
    out["budget"] = "left"
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.games.backfill_job")
    p.add_argument("--budget", type=float, default=DEFAULT_BUDGET_S)
    p.add_argument("--leagues", nargs="*", default=list(LEAGUES))
    a = p.parse_args(argv)
    flags.require("SPORTS_INGEST")                   # writes the game store
    db = store.connect()
    try:
        res = {"packed_games": store.pack_plays(db, limit=PACK_PER_RUN)}   # legacy row-table plays -> packed
        res["compacted"] = compact(db)
        res.update(run(db, a.leagues, a.budget))
    finally:
        db.close()
    print(json.dumps(res, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
