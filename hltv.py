#!/usr/bin/env python3
"""
hltv.py - HLTV numbers from the hltv-scraper database (github.com/rikko1012/hltv-scraper).

The scraper runs separately (its own schedule, gets past HLTV's Cloudflare check) and writes into a
PostgreSQL database. This module only READS that database, so a grade never waits on HLTV.

What it adds (the HLTV-only numbers BO3.gg doesn't have):
- Round Swing  -> grader_inputs()["round_swing"], used by grade_leg()'s swing multiplier. Only when
                  the player has MIN_SWING_MAPS+ HLTV maps in the window; otherwise None, exactly as
                  before. HLTV_SWING=0 turns it off.
- Rating 3.0   -> HLTV's real rating, shown next to the BO3.gg-derived one (context, not used in math).
- ADR, KAST, K/D, CT/T ratings - context.

Setup: set HLTV_DB_DSN (or DATABASE_URL-style HLTV_DATABASE_URL) to the scraper's database, ideally a
read-only user. Not set / unreachable / player unknown = {"found": False, ...} and nothing changes.

Use:  python hltv.py ZywOo [team]
"""
import json
import os
import re
import sys
import threading
import time

WINDOWS = (3, 6, 12)      # months, like the BO3.gg waterfall: first window with enough maps wins
MIN_MAPS = 10             # maps needed before a window is used
MIN_SWING_MAPS = 10       # maps with a round swing value before it feeds the grade
STALE_DAYS = 10           # newest HLTV map this much older than BO3.gg's = warning
TTL = 15 * 60
_CACHE, _LOCK = {}, threading.Lock()


def dsn():
    return (os.environ.get("HLTV_DB_DSN") or os.environ.get("HLTV_DATABASE_URL") or "").strip()


def swing_enabled():
    return os.environ.get("HLTV_SWING", "1") != "0"


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _connect():
    import psycopg2   # only needed when the HLTV database is configured

    target = dsn()
    kwargs = {"connect_timeout": 5, "options": "-c statement_timeout=8000"}
    return psycopg2.connect(target, **kwargs)


# One query per player: every candidate id with that nickname, each map (all sides) in the last
# 12 months, plus the team he played for. Windows are cut in Python.
_SQL = """
SELECT ps.playerid, p.name, t.name, mo.date, mo.matchid, s.name, ps.rating, ps.roundswing,
       ps.adr, ps.kast, ps.k, ps.d, mo.source
FROM player_stats ps
JOIN players p         ON p.playerid = ps.playerid
JOIN teams t           ON t.teamid = ps.teamid
JOIN match_overview mo ON mo.matchid = ps.matchid
JOIN sides s           ON s.sideid = ps.sideid
WHERE ps.playerid IN (SELECT playerid FROM players WHERE lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) = %(nick)s)
  AND ps.mapid <> 0
  AND mo.date >= current_date - interval '12 months'
ORDER BY mo.date DESC
"""


def player_numbers(nick, team=None, bo3_last_date=None):
    """HLTV numbers for a player; never raises. Cached 15 minutes."""
    if not dsn():
        return {"found": False, "note": "HLTV database not configured (HLTV_DB_DSN)"}
    key = (_norm(nick), _norm(team), (bo3_last_date or "")[:10])
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    try:
        result = _player_numbers(nick, team, bo3_last_date)
    except Exception as e:  # database down, schema not there yet, ... - never block a grade
        result = {"found": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    with _LOCK:
        if len(_CACHE) > 5000:
            _CACHE.clear()
        _CACHE[key] = (time.time(), result)
    return result


def _player_numbers(nick, team, bo3_last_date):
    from datetime import date, timedelta

    con = _connect()
    try:
        with con.cursor() as cur:
            try:
                cur.execute(_SQL, {"nick": _norm(nick)})
            except Exception as e:
                if "source" not in str(e):
                    raise
                con.rollback()   # database from an older scraper version (before the source column)
                cur.execute(_SQL.replace("mo.source", "'hltv'"), {"nick": _norm(nick)})
            rows = cur.fetchall()
    finally:
        con.close()
    if not rows:
        return {"found": False, "note": f"{nick} has no HLTV maps in the last 12 months"}

    # Several HLTV players can share a nickname: prefer the one who played for this team.
    by_player = {}
    for r in rows:
        by_player.setdefault(r[0], []).append(r)
    def score(pid):
        teams = {_norm(r[2]) for r in by_player[pid]}
        return (bool(team) and _norm(team) in teams, len(by_player[pid]))
    pid = max(by_player, key=score)
    rows = by_player[pid]
    today = date.today()

    window, total = None, []
    for months in WINDOWS:
        since = today - timedelta(days=int(months * 30.4))
        total = [r for r in rows if r[5] == "total" and r[3] >= since]
        if len(total) >= MIN_MAPS:
            window = months
            break
    window = window or WINDOWS[-1]
    since = today - timedelta(days=int(window * 30.4))
    sides = {s: [r for r in rows if r[5] == s and r[3] >= since] for s in ("ct", "t")}

    def avg(values, digits=2):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), digits) if values else None

    swings = [r[7] for r in total if r[7] is not None]
    kills, deaths = sum(r[10] or 0 for r in total), sum(r[11] or 0 for r in total)
    newest = max(r[3] for r in rows)
    out = {
        "found": True,
        "hltv_id": pid,
        "player": rows[0][1],
        "team": rows[0][2],                       # team on his newest HLTV map
        "window": f"{window}m",
        "maps": len(total),
        "rating": avg(r[6] for r in total),
        "round_swing": avg(swings),
        "swing_maps": len(swings),
        "adr": avg((r[8] for r in total), 1),
        "kast": avg((r[9] for r in total), 1),
        "kd": round(kills / deaths, 2) if deaths else None,
        "ct_rating": avg(r[6] for r in sides["ct"]),
        "t_rating": avg(r[6] for r in sides["t"]),
        "newest_map": newest.isoformat(),
        "backup_maps": sum(1 for r in total if r[12] != "hltv"),   # filled from the scraper's failsafe API
        "use_swing": swing_enabled() and len(swings) >= MIN_SWING_MAPS,
    }
    if team and _norm(team) not in {_norm(r[2]) for r in total}:
        out["team_mismatch"] = f"HLTV maps in this window are for {out['team']}, not {team}"
    if bo3_last_date:
        try:
            gap = (date.fromisoformat(bo3_last_date[:10]) - newest).days
            if gap > STALE_DAYS:
                out["stale"] = f"HLTV data ends {newest} - {gap} days before BO3.gg's newest map"
        except ValueError:
            pass
    return out


def grade_swing(info):
    """The round swing to hand to grade_leg(): the HLTV value when it's trustworthy, else None."""
    if info and info.get("found") and info.get("use_swing") and not info.get("team_mismatch"):
        return info.get("round_swing")
    return None


if __name__ == "__main__":
    print(json.dumps(player_numbers(sys.argv[1] if len(sys.argv) > 1 else "ZywOo",
                                    sys.argv[2] if len(sys.argv) > 2 else None), indent=2, default=str))
