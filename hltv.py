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
- context(): prop-map stats, eco-adjusted stats, last 5 maps, team vs opponent head-to-head and
  map win rates, VRS rank trend, next match from HLTV's schedule. DISPLAY ONLY: none of it goes
  into grade_leg(), and each section fails on its own without touching the others.

Safety: a database that is down trips a circuit breaker - for BREAKER_SECONDS every lookup returns
"unavailable" instantly instead of waiting on a timeout, then it quietly tries again.

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
BREAKER_SECONDS = 5 * 60  # after a connection failure, skip the database this long
_CACHE, _LOCK = {}, threading.Lock()
_DOWN_UNTIL = [0.0]


class Unavailable(RuntimeError):
    pass


def dsn():
    return (os.environ.get("HLTV_DB_DSN") or os.environ.get("HLTV_DATABASE_URL") or "").strip()


def swing_enabled():
    # ON by default - owner's decision (2026-09-30), as the hltv-scraper setup specifies.
    # For the record: on the 900-case walk-forward, HLTV swing (pre-match values) gave kills miss
    # 5.970 -> 6.023 and HS 3.730 -> 3.831. HLTV_SWING=0 turns it off.
    return os.environ.get("HLTV_SWING", "1") != "0"


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _connect():
    if time.time() < _DOWN_UNTIL[0]:
        raise Unavailable("HLTV database unreachable a moment ago - skipped (retries in a few minutes)")
    import psycopg2   # only needed when the HLTV database is configured

    kwargs = {"connect_timeout": 4, "options": "-c statement_timeout=5000"}
    try:
        con = psycopg2.connect(dsn(), **kwargs)
    except Exception:
        _DOWN_UNTIL[0] = time.time() + BREAKER_SECONDS
        raise
    con.set_session(readonly=True, autocommit=True)
    return con


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
                # database from an older scraper version (before the source column)
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


def history(nick, cutoff, team=None):
    """Admin/testing: his HLTV window numbers using ONLY maps before `cutoff` (YYYY-MM-DD), same
    3/6/12-month rule as player_numbers(). Never raises."""
    from datetime import date, timedelta
    try:
        cut = date.fromisoformat(cutoff[:10])
        con = _connect()
        try:
            with con.cursor() as cur:
                cur.execute(_SQL.replace("current_date", "%(cut)s::date").replace(
                    "ORDER BY mo.date DESC", "AND mo.date < %(cut)s::date ORDER BY mo.date DESC")
                    .replace("mo.source", "'hltv'"), {"nick": _norm(nick), "cut": cut.isoformat()})
                rows = cur.fetchall()
        finally:
            con.close()
    except Exception as e:
        return {"found": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    if not rows:
        return {"found": False}
    by = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    pid = max(by, key=lambda k: (bool(team) and _norm(team) in {_norm(r[2]) for r in by[k]}, len(by[k])))
    rows = by[pid]
    for months in WINDOWS:
        total = [r for r in rows if r[5] == "total" and r[3] >= cut - timedelta(days=int(months * 30.4))]
        if len(total) >= MIN_MAPS:
            break
    sw = [r[7] for r in total if r[7] is not None]
    rt = [r[6] for r in total if r[6] is not None]
    return {"found": True, "maps": len(total), "swing_maps": len(sw),
            "round_swing": round(sum(sw) / len(sw), 3) if sw else None,
            "rating": round(sum(rt) / len(rt), 3) if rt else None}


def grade_swing(info):
    """The round swing to hand to grade_leg(): the HLTV value when it's trustworthy, else None."""
    if info and info.get("found") and info.get("use_swing") and not info.get("team_mismatch"):
        return info.get("round_swing")
    return None


# ---------------------------------------------------------------- schedule
SCHEDULE_MAX_AGE_H = 6    # an HLTV schedule snapshot older than this is not trusted
SCHEDULE_TTL = 5 * 60


def upcoming_for(team_names, matcher, hours=72):
    """Not-yet-started matches on HLTV's schedule for any of `team_names` in the next `hours`,
    earliest first: [{"team", "opponent", "start" (datetime, UTC), "event", "format", "url"}].
    `matcher(a, b)` scores team-name similarity (polymarket.name_match). Never raises; [] when the
    database is down/unconfigured or the snapshot is older than SCHEDULE_MAX_AGE_H."""
    names = [n for n in (team_names or []) if n]
    if not dsn() or not names:
        return []
    rows = _schedule_rows()
    if not rows:
        return []
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    out = []
    for time_, live, t1, t2, event, fmt, url in rows:
        if live or time_ is None or not (now < time_ <= now + timedelta(hours=hours)):
            continue   # live matches are locked for props; past ones are over
        for mine, other in ((t1, t2), (t2, t1)):
            if other and _norm(other) != "tbd" and any(matcher(n, mine) >= 2 for n in names):
                out.append({"team": mine, "opponent": other, "start": time_, "event": event or "",
                            "format": fmt or "", "url": url})
                break
    return sorted(out, key=lambda m: m["start"])


def _schedule_rows():
    with _LOCK:
        hit = _CACHE.get("schedule")
        if hit and time.time() - hit[0] < SCHEDULE_TTL:
            return hit[1]
    rows = []
    try:
        con = _connect()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT max(scraped_at) >= now() - make_interval(hours => %s) FROM upcoming_matches",
                            (SCHEDULE_MAX_AGE_H,))
                fresh = cur.fetchone()[0]
                if fresh:
                    cur.execute("SELECT time, live, team1, team2, event, format, url FROM upcoming_matches")
                    rows = cur.fetchall()
        finally:
            con.close()
    except Exception:
        rows = []
    with _LOCK:
        _CACHE["schedule"] = (time.time(), rows)
    return rows


# ---------------------------------------------------------------- display-only context
def _map_key(name):
    return _norm(str(name or "").lower().removeprefix("de_"))


def context(nick, team=None, opponent=None, maps=(), bo3_last_date=None):
    """Everything else the scraper has for this grade, for display. Never raises; each section is
    None when it can't be computed. Cached 15 minutes."""
    if not dsn():
        return {"found": False, "note": "HLTV database not configured (HLTV_DB_DSN)"}
    maps = [m for m in (maps or []) if m]
    key = ("ctx", _norm(nick), _norm(team), _norm(opponent), tuple(_map_key(m) for m in maps))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    try:
        con = _connect()
    except Exception as e:
        return {"found": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    out, errors = {"found": True}, {}
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            team_id = _team_id(cur, team) if team else None
            opp_id = _team_id(cur, opponent) if opponent else None
            sections = {
                "prop_maps": lambda: _prop_maps(cur, ids, maps),
                "eco": lambda: _eco(cur, ids),
                "last5": lambda: _last5(cur, ids),
                "h2h": lambda: _h2h(cur, team_id, opp_id, maps),
                "ranking": lambda: _ranking(cur, team_id),
                "opp_ranking": lambda: _ranking(cur, opp_id),
                "rank_band": lambda: _rank_band(cur, ids, opp_id),
                "next_match": lambda: _next_match(cur, team),
                "backup_source": lambda: _backup(cur, ids),
            }
            for name, fn in sections.items():
                try:
                    out[name] = fn()
                except Exception as e:   # one section failing never affects the others
                    out[name] = None
                    errors[name] = f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        con.close()
    if errors:
        out["errors"] = errors
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def _player_ids(cur, nick, team):
    cur.execute("""SELECT ps.playerid, bool_or(lower(regexp_replace(t.name, '[^a-zA-Z0-9]', '', 'g')) = %(team)s),
                          count(*)
                   FROM players p JOIN player_stats ps USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                   WHERE lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = %(nick)s
                   GROUP BY ps.playerid""", {"nick": _norm(nick), "team": _norm(team)})
    rows = cur.fetchall()
    if not rows:
        return []
    best = max(rows, key=lambda r: (bool(r[1]), r[2]))   # same rule as player_numbers()
    return [best[0]]


def _team_id(cur, name):
    cur.execute("SELECT teamid FROM teams WHERE lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) = %s",
                (_norm(name),))
    row = cur.fetchone()
    return row[0] if row else None


def _prop_maps(cur, ids, maps):
    """His HLTV numbers on each prop map, last 6 months: maps, KPR (kills per round), rating, ADR."""
    if not ids or not maps:
        return None
    cur.execute("""SELECT mp.name, count(*), sum(ps.k), sum(own.score + opp.score), avg(ps.rating), avg(ps.adr)
                   FROM player_stats ps
                   JOIN maps mp USING (mapid)
                   JOIN match_overview mo USING (matchid)
                   JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid
                                   AND own.mapid = ps.mapid AND own.sideid = 0
                   JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid
                                   AND opp.mapid = ps.mapid AND opp.sideid = 0
                   WHERE ps.playerid = ANY(%s) AND ps.mapid <> 0 AND ps.sideid = 0
                     AND mo.date >= current_date - interval '6 months'
                   GROUP BY mp.name""", (ids,))
    by_key = {_map_key(r[0]): r for r in cur.fetchall()}
    out = []
    for m in maps:
        r = by_key.get(_map_key(m))
        out.append({"map": r[0] if r else str(m).removeprefix("de_").title(), "maps": r[1] if r else 0,
                    "kpr": round(r[2] / r[3], 3) if r and r[3] else None,
                    "rating": round(r[4], 2) if r and r[4] is not None else None,
                    "adr": round(r[5], 1) if r and r[5] is not None else None})
    return out


def _eco(cur, ids):
    """Eco-adjusted numbers (kills/ADR/KAST with eco rounds discounted), last 3 months."""
    if not ids:
        return None
    cur.execute("""SELECT count(ps.ek), sum(ps.ek), sum(ps.ed), avg(ps.eadr), avg(ps.ekast), sum(ps.k), sum(ps.d)
                   FROM player_stats ps JOIN match_overview mo USING (matchid)
                   WHERE ps.playerid = ANY(%s) AND ps.mapid <> 0 AND ps.sideid = 0 AND ps.ek IS NOT NULL
                     AND mo.date >= current_date - interval '3 months'""", (ids,))
    n, ek, ed, eadr, ekast, k, d = cur.fetchone()
    if not n:
        return None
    return {"maps": n, "ekd": round(ek / ed, 2) if ed else None, "kd": round(k / d, 2) if d else None,
            "eadr": round(eadr, 1) if eadr is not None else None,
            "ekast": round(ekast, 1) if ekast is not None else None}


def _execute(cur, sql, params):
    """Run a query; on a database from an older scraper version (no source column) treat every
    row as HLTV data."""
    try:
        cur.execute(sql, params)
    except Exception as e:
        if "source" not in str(e):
            raise
        cur.execute(sql.replace("mo.source <> 'hltv'", "false").replace("mo.source", "'hltv'"), params)


def _last5(cur, ids):
    if not ids:
        return None
    _execute(cur, """SELECT mo.date, mp.name, t2.name, own.score, opp.score, ps.k, ps.d, ps.rating, mo.source
                   FROM player_stats ps
                   JOIN maps mp USING (mapid)
                   JOIN match_overview mo USING (matchid)
                   JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid
                                   AND own.mapid = ps.mapid AND own.sideid = 0
                   JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid
                                   AND opp.mapid = ps.mapid AND opp.sideid = 0
                   JOIN teams t2 ON t2.teamid = opp.teamid
                   WHERE ps.playerid = ANY(%s) AND ps.mapid <> 0 AND ps.sideid = 0
                   ORDER BY mo.date DESC, ps.matchid DESC LIMIT 5""", (ids,))
    return [{"date": r[0].isoformat(), "map": r[1], "opponent": r[2], "score": f"{r[3]}-{r[4]}",
             "kd": f"{r[5]}-{r[6]}", "rating": r[7], "backup": r[8] != "hltv"} for r in cur.fetchall()]


def _winrates(cur, team_id, months=6):
    cur.execute("""SELECT mp.name, count(*), count(*) FILTER (WHERE own.score > opp.score)
                   FROM matches own
                   JOIN matches opp ON opp.matchid = own.matchid AND opp.teamid <> own.teamid
                                   AND opp.mapid = own.mapid AND opp.sideid = 0
                   JOIN maps mp ON mp.mapid = own.mapid
                   JOIN match_overview mo ON mo.matchid = own.matchid
                   WHERE own.teamid = %s AND own.sideid = 0 AND own.mapid <> 0
                     AND mo.date >= current_date - make_interval(months => %s)
                   GROUP BY mp.name""", (team_id, months))
    return {_map_key(r[0]): {"map": r[0], "played": r[1], "won": r[2]} for r in cur.fetchall()}


def _h2h(cur, team_id, opp_id, maps):
    """Series between the two teams (12 months) and each team's map win rates (6 months)."""
    if not team_id or not opp_id:
        return None
    cur.execute("""SELECT mo.date, own.score, opp.score, mo.event
                   FROM matches own
                   JOIN matches opp ON opp.matchid = own.matchid AND opp.teamid = %s
                                   AND opp.mapid = 0 AND opp.sideid = 0
                   JOIN match_overview mo ON mo.matchid = own.matchid
                   WHERE own.teamid = %s AND own.mapid = 0 AND own.sideid = 0
                     AND mo.date >= current_date - interval '12 months'
                   ORDER BY mo.date DESC""", (opp_id, team_id))
    series = [{"date": r[0].isoformat(), "score": f"{r[1]}-{r[2]}", "won": r[1] > r[2], "event": r[3]}
              for r in cur.fetchall()]
    ours, theirs = _winrates(cur, team_id), _winrates(cur, opp_id)
    keys = [_map_key(m) for m in maps] or sorted(set(ours) | set(theirs))
    map_rates = [{"map": (ours.get(k) or theirs.get(k) or {"map": k.title()})["map"],
                  "team": ours.get(k), "opponent": theirs.get(k)} for k in keys]
    return {"series": series, "wins": sum(s["won"] for s in series), "losses": sum(not s["won"] for s in series),
            "map_win_rates": map_rates}


def team_ranks():
    """{normalized team name: latest VRS rank} for every ranked team (cached 1 hour)."""
    key = ("ranks",)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    out = {}
    try:
        con = _connect()
        try:
            with con.cursor() as cur:
                cur.execute("""SELECT DISTINCT ON (r.teamid) t.name, r.rank FROM team_rankings r
                               JOIN teams t USING (teamid) ORDER BY r.teamid, r.date DESC""")
                out = {_norm(n): rk for n, rk in cur.fetchall()}
        finally:
            con.close()
    except Exception:
        return {}
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def _rank_band(cur, ids, opp_id, band=10, months=12):
    """His HLTV maps (last `months`) against teams ranked within `band` of tonight's opponent, next to
    all his maps: {"opp_rank", "maps", "kpr", "kpr_all", "ratio", "teams"}. Each team's rank is its
    most recent VRS snapshot."""
    if not ids or not opp_id:
        return None
    cur.execute("SELECT rank FROM team_rankings WHERE teamid = %s ORDER BY date DESC LIMIT 1", (opp_id,))
    r = cur.fetchone()
    if not r:
        return None
    orank = r[0]
    cur.execute("""WITH ranks AS (SELECT DISTINCT ON (teamid) teamid, rank FROM team_rankings ORDER BY teamid, date DESC)
                   SELECT sum(ps.k), sum(own.score + opp.score),
                          sum(ps.k) FILTER (WHERE abs(rk.rank - %(orank)s) <= %(band)s),
                          sum(own.score + opp.score) FILTER (WHERE abs(rk.rank - %(orank)s) <= %(band)s),
                          count(*) FILTER (WHERE abs(rk.rank - %(orank)s) <= %(band)s),
                          array_agg(DISTINCT t2.name) FILTER (WHERE abs(rk.rank - %(orank)s) <= %(band)s)
                   FROM player_stats ps
                   JOIN match_overview mo USING (matchid)
                   JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                   JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                   JOIN teams t2 ON t2.teamid = opp.teamid
                   LEFT JOIN ranks rk ON rk.teamid = opp.teamid
                   WHERE ps.playerid = ANY(%(ids)s) AND ps.sideid = 0 AND ps.mapid <> 0
                     AND mo.date >= current_date - make_interval(months => %(months)s)""",
                {"ids": ids, "orank": orank, "band": band, "months": months})
    k_all, r_all, k_b, r_b, n_b, teams = cur.fetchone()
    if not r_all or not r_b:
        return {"opp_rank": orank, "maps": 0}
    kpr, kpr_all = k_b / r_b, k_all / r_all
    return {"opp_rank": orank, "band": band, "maps": n_b, "kpr": round(kpr, 3), "kpr_all": round(kpr_all, 3),
            "ratio": round(kpr / kpr_all, 3), "teams": sorted(teams or [])[:8]}


def _ranking(cur, team_id):
    """VRS rank now and about four weeks ago (from the scraper's ranking snapshots)."""
    if not team_id:
        return None
    cur.execute("""SELECT date, rank, points FROM team_rankings WHERE teamid = %s
                   AND date >= current_date - interval '60 days' ORDER BY date DESC""", (team_id,))
    rows = cur.fetchall()
    if not rows:
        return None
    now = rows[0]
    then = next((r for r in rows if (now[0] - r[0]).days >= 28), None)
    return {"rank": now[1], "points": now[2], "as_of": now[0].isoformat(),
            "rank_4w_ago": then[1] if then else None, "change": (then[1] - now[1]) if then else None}


def _next_match(cur, team):
    """His team's next (or live) match on HLTV's schedule - a second opinion on BO3.gg's slate."""
    if not team:
        return None
    cur.execute("""SELECT time, live, team1, team2, event, format, url FROM upcoming_matches
                   WHERE (lower(regexp_replace(team1, '[^a-zA-Z0-9]', '', 'g')) = %(t)s
                       OR lower(regexp_replace(team2, '[^a-zA-Z0-9]', '', 'g')) = %(t)s)
                     AND (live OR time >= now() - interval '3 hours')
                   ORDER BY live DESC, time LIMIT 1""", {"t": _norm(team)})
    r = cur.fetchone()
    if not r:
        return None
    opponent = r[3] if _norm(r[2]) == _norm(team) else r[2]
    return {"time": r[0].isoformat() if r[0] else None, "live": r[1], "opponent": opponent,
            "event": r[4], "format": r[5], "url": r[6]}


def _backup(cur, ids):
    """How many of his maps came from the scraper's failsafe API (less complete than HLTV)."""
    if not ids:
        return None
    _execute(cur, """SELECT count(*) FILTER (WHERE mo.source <> 'hltv'), count(*)
                     FROM player_stats ps JOIN match_overview mo USING (matchid)
                     WHERE ps.playerid = ANY(%s) AND ps.mapid <> 0 AND ps.sideid = 0
                       AND mo.date >= current_date - interval '3 months'""", (ids,))
    backup, total = cur.fetchone()
    return {"backup_maps": backup, "maps": total} if backup else None


if __name__ == "__main__":
    nick = sys.argv[1] if len(sys.argv) > 1 else "ZywOo"
    team = sys.argv[2] if len(sys.argv) > 2 else None
    opp = sys.argv[3] if len(sys.argv) > 3 else None
    maps = sys.argv[4].split(",") if len(sys.argv) > 4 else ()
    print(json.dumps({"numbers": player_numbers(nick, team), "context": context(nick, team, opp, maps)},
                     indent=2, default=str))


# ---------------------------------------------------------------- Maps 1-2 series (form + settling)
# Needs the scraper's match_maps (map order) and map_headshots tables; on an older scraper database
# these return [] / None and nothing changes.
_SERIES_SQL = """
SELECT mo.matchid, mo.date, mm.number, mp.name, ps.k, h.hs, own.score, opp.score, t2.name, t.name
FROM match_maps mm
JOIN match_overview mo USING (matchid)
JOIN maps mp ON mp.mapid = mm.mapid
JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
JOIN teams t ON t.teamid = ps.teamid
JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
JOIN matches opp ON opp.matchid = mm.matchid AND opp.teamid <> ps.teamid AND opp.mapid = mm.mapid AND opp.sideid = 0
JOIN teams t2 ON t2.teamid = opp.teamid
LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
WHERE ps.playerid = ANY(%(ids)s) AND mm.number <= 2 AND mo.date >= %(since)s {extra}
ORDER BY mo.date DESC, mo.matchid DESC, mm.number
"""


def _series_rows(cur, ids, since, extra="", params=None):
    cur.execute(_SERIES_SQL.format(extra=extra), dict(params or {}, ids=ids, since=since))
    by_match = {}
    for mid, d, num, mname, k, hs, own, opp, oname, tname in cur.fetchall():
        by_match.setdefault(mid, []).append({
            "match_id": f"hltv-{mid}", "hltv_match": mid, "date": d.isoformat(), "number": num,
            "map": "de_" + _norm(mname), "kills": k, "headshots": hs, "rounds": (own or 0) + (opp or 0),
            "won": (own or 0) > (opp or 0), "opponent": oname, "team": tname, "source": "hltv"})
    return [m for maps in by_match.values() if {x["number"] for x in maps} >= {1, 2} for m in maps]


def recent_series(nick, team=None, since=None):
    """His HLTV Maps 1-2 (both maps known) on or after `since` (YYYY-MM-DD), as map rows shaped like
    BO3.gg's (match_id 'hltv-<id>', number, map, kills, headshots - None until the scraper has read
    the map-stats page -, rounds, won, opponent). [] on any problem. Cached 15 minutes."""
    if not dsn() or not nick or not since:
        return []
    key = ("series", _norm(nick), _norm(team), since)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    try:
        con = _connect()
    except Exception:
        return []
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            out = _series_rows(cur, ids, since) if ids else []
    except Exception:
        out = []
    finally:
        con.close()
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def settle_totals(nick, team, hltv_match):
    """Maps 1-2 kills and headshots for one HLTV match: {"kills", "headshots" (None until read),
    "found"}. Not cached: used by the pick log's settle."""
    if not dsn():
        return {"found": False}
    try:
        con = _connect()
    except Exception:
        return {"found": False}
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            rows = _series_rows(cur, ids, "2000-01-01", "AND mo.matchid = %(mid)s", {"mid": int(hltv_match)}) if ids else []
    except Exception:
        rows = []
    finally:
        con.close()
    if not rows:
        return {"found": False}
    m12 = sorted(rows, key=lambda r: r["number"])[:2]
    hs = [r["headshots"] for r in m12]
    return {"found": True, "kills": sum(r["kills"] for r in m12),
            "headshots": None if None in hs else sum(hs), "maps": [r["map"] for r in m12]}


def match_maps_detail(nick, team, day):
    """Admin analysis: his HLTV numbers per map for matches on `day` (or the day after - time
    zones): map number (if the scraper has the order), map, kills, deaths, headshots, rounds, won,
    margin, ADR, KAST, rating, round swing, CT and T kills. [] when unknown."""
    if not dsn():
        return []
    con = _connect()
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            if not ids:
                return []
            cur.execute("""SELECT mo.matchid, mo.date, mp.name, ps.sideid, ps.k, ps.d, ps.adr, ps.kast, ps.rating,
                                  ps.roundswing, own.score, opp.score, t2.name
                           FROM player_stats ps
                           JOIN match_overview mo USING (matchid)
                           JOIN maps mp USING (mapid)
                           JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid
                                           AND own.mapid = ps.mapid AND own.sideid = 0
                           JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid
                                           AND opp.mapid = ps.mapid AND opp.sideid = 0
                           JOIN teams t2 ON t2.teamid = opp.teamid
                           WHERE ps.playerid = ANY(%s) AND ps.mapid <> 0
                             AND mo.date BETWEEN %s::date - 1 AND %s::date + 1""", (ids, day, day))
            rows = cur.fetchall()
            order, hs = {}, {}
            try:
                cur.execute("""SELECT mm.matchid, mp.name, mm.number, h.hs FROM match_maps mm JOIN maps mp USING (mapid)
                               LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ANY(%s)
                               WHERE mm.matchid = ANY(%s)""", (ids, list({r[0] for r in rows})))
                for mid, mname, num, h in cur.fetchall():
                    order[(mid, mname)] = num
                    hs[(mid, mname)] = h
            except Exception:
                pass
    finally:
        con.close()
    maps = {}
    for mid, d, mname, side, k, dth, adr, kast, rating, swing, own, opp, oname in rows:
        m = maps.setdefault((mid, mname), {"match": mid, "date": d.isoformat(), "map": mname, "opponent": oname,
                                           "number": order.get((mid, mname)), "hs": hs.get((mid, mname)),
                                           "rounds": (own or 0) + (opp or 0), "won": (own or 0) > (opp or 0),
                                           "margin": abs((own or 0) - (opp or 0))})
        if side == 0:
            m.update(k=k, d=dth, adr=adr, kast=kast, rating=rating, swing=swing)
        else:
            m["ct_k" if side == 1 else "t_k"] = k
    return sorted(maps.values(), key=lambda m: (m["date"], m["match"], m["number"] or 9))


def settle_by_day(nick, team, day, opponent):
    """HLTV's Maps 1-2 totals for his match on `day` (+-1 day, time zones) against `opponent`
    (HLTV is the official record - preferred over BO3.gg when both have the match).
    {"found": False} when HLTV doesn't have it; kills/headshots None until scraped."""
    if not dsn():
        return {"found": False}
    try:
        con = _connect()
    except Exception:
        return {"found": False}
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            rows = _series_rows(cur, ids, "2000-01-01", "AND mo.date BETWEEN %(d)s::date - 1 AND %(d)s::date + 1",
                                {"d": day}) if ids else []
    except Exception:
        rows = []
    finally:
        con.close()
    on = [r for r in rows if not opponent or _norm(opponent) in _norm(r["opponent"]) or _norm(r["opponent"]) in _norm(opponent)]
    if not on:
        return {"found": False}
    mid = on[0]["hltv_match"]
    m12 = sorted((r for r in on if r["hltv_match"] == mid), key=lambda r: r["number"])[:2]
    hs = [r["headshots"] for r in m12]
    return {"found": True, "hltv_match": mid, "date": m12[0]["date"], "kills": sum(r["kills"] for r in m12),
            "headshots": None if None in hs else sum(hs), "maps": [r["map"] for r in m12],
            "rounds": [r["rounds"] for r in m12], "won": [r["won"] for r in m12],
            "per_map": [[r["kills"], r["headshots"]] for r in m12]}


# ---------------------------------------------------------------- map vetoes (from stored match pages)
_VETO = re.compile(r"<div>\s*(\d)\.\s*([^<]+?)\s+(removed|picked)\s+([^<]+?)\s*</div>")
_LEFT = re.compile(r"<div>\s*(\d)\.\s*([^<]+?)\s+was left over\s*</div>")
_VETO_CACHE = {}


def _vetoes(cur, match_id):
    """[(step, team_norm, 'removed'|'picked'|'left', map_key)] for one stored match page (cached)."""
    if match_id in _VETO_CACHE:
        return _VETO_CACHE[match_id]
    import gzip as _gz
    cur.execute("SELECT html_gz FROM raw_pages WHERE matchid = %s", (match_id,))
    row = cur.fetchone()
    out = []
    if row:
        html = _gz.decompress(bytes(row[0])).decode("utf-8", "replace")
        box = html[html.find("veto-box"):][:6000]
        steps = {}
        for n, t, act, m in _VETO.findall(box):
            steps.setdefault(int(n), (int(n), _norm(t), act, _map_key(m)))
        for n, m in _LEFT.findall(box):
            steps.setdefault(int(n), (int(n), None, "left", _map_key(m)))
        out = [steps[k] for k in sorted(steps)]          # one entry per step (pages can repeat the box)
    if len(_VETO_CACHE) > 20000:
        _VETO_CACHE.clear()
    _VETO_CACHE[match_id] = out
    return out


def _team_history(cur, team_name, before, n=25):
    """His team's last `n` BO3 vetoes before `before`: ban / pick counts by step role."""
    cur.execute("""SELECT DISTINCT mo.matchid, mo.date FROM match_overview mo JOIN matches m USING (matchid)
                   JOIN teams t ON t.teamid = m.teamid
                   WHERE lower(regexp_replace(t.name, '[^a-zA-Z0-9]', '', 'g')) = %s AND mo.date < %s
                   ORDER BY mo.date DESC LIMIT %s""", (_norm(team_name), before, n * 2))
    hist = {"ban1": {}, "pick": {}, "ban2": {}, "n": 0}
    me = _norm(team_name)
    for mid, _ in cur.fetchall():
        v = _vetoes(cur, mid)
        if len(v) != 7 or hist["n"] >= n:          # BO3 vetoes only (2 bans, 2 picks, 2 bans, decider)
            continue
        hist["n"] += 1
        for step, team, act, mp in v:
            if team != me:
                continue
            role = "pick" if act == "picked" else "ban1" if step <= 2 else "ban2"
            hist[role][mp] = hist[role].get(mp, 0) + 1
    return hist


def predict_maps(team_a, team_b, before, pool=None, runs=4000, seed=7):
    """P(Map 1 = m) and P(Map 2 = m) from both teams' HLTV veto history (BO3 order: A ban, B ban,
    A pick = Map 1, B pick = Map 2, A ban, B ban, decider). Who goes first is unknown: both orders."""
    import random
    if not dsn():
        return None
    key = ("veto", _norm(team_a), _norm(team_b), before, runs)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    out = _predict_maps(team_a, team_b, before, pool, runs, seed, random)
    with _LOCK:
        _CACHE[key] = (time.time(), out)
    return out


def _predict_maps(team_a, team_b, before, pool, runs, seed, random):
    con = _connect()
    try:
        with con.cursor() as cur:
            ha, hb = _team_history(cur, team_a, before), _team_history(cur, team_b, before)
    finally:
        con.close()
    if ha["n"] < 3 or hb["n"] < 3:
        return None
    pool = pool or sorted({m for h in (ha, hb) for r in ("ban1", "pick", "ban2") for m in h[r]})
    if len(pool) < 7:
        return None
    rnd = random.Random(seed)

    def choose(h, role, left):
        w = [h[role].get(m, 0) + 0.3 for m in left]
        if role == "pick":                       # a team doesn't pick what it usually bans
            w = [x / (1 + h["ban1"].get(m, 0) + h["ban2"].get(m, 0)) for x, m in zip(w, left)]
        return rnd.choices(left, weights=w)[0]
    m1, m2 = {}, {}
    for i in range(runs):
        first, second = (ha, hb) if i % 2 == 0 else (hb, ha)
        left = list(pool)
        for h, role in ((first, "ban1"), (second, "ban1")):
            left.remove(choose(h, role, left))
        a = choose(first, "pick", left); left.remove(a)
        b = choose(second, "pick", left)
        m1[a] = m1.get(a, 0) + 1
        m2[b] = m2.get(b, 0) + 1
    return {"map1": {k: v / runs for k, v in sorted(m1.items(), key=lambda x: -x[1])},
            "map2": {k: v / runs for k, v in sorted(m2.items(), key=lambda x: -x[1])},
            "history": [ha["n"], hb["n"]]}


def veto_backtest(days=30, limit=250):
    """Admin: predict Map 1 / Map 2 for recent HLTV BO3s using only vetoes from before each match."""
    con = _connect()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT mo.matchid, mo.date FROM match_overview mo
                           WHERE mo.date >= current_date - %s ORDER BY mo.date DESC LIMIT %s""", (days, limit * 3))
            ms = cur.fetchall()
            cases = []
            for mid, d in ms:
                v = _vetoes(cur, mid)
                if len(v) != 7:
                    continue
                picks = [x for x in v if x[2] == "picked"]
                if len(picks) != 2 or v[0][1] is None or v[1][1] is None:
                    continue
                cur.execute("""SELECT t.name FROM matches m JOIN teams t USING (teamid) WHERE m.matchid = %s
                               GROUP BY t.name""", (mid,))
                names = [r[0] for r in cur.fetchall()]
                a = next((n for n in names if _norm(n) == v[0][1]), None)
                b = next((n for n in names if _norm(n) == v[1][1]), None)
                if a and b:
                    cases.append((mid, d.isoformat(), a, b, picks[0][3], picks[1][3]))
                if len(cases) >= limit:
                    break
    finally:
        con.close()
    res = {"cases": 0, "map1_top1": 0, "map1_top2": 0, "map2_top1": 0, "map2_top2": 0, "either_pick_in_top2": 0, "ll": 0.0}
    import math
    for mid, d, a, b, p1, p2 in cases:
        pr = predict_maps(a, b, d)
        if not pr:
            continue
        res["cases"] += 1
        r1, r2 = list(pr["map1"]), list(pr["map2"])
        res["map1_top1"] += r1[:1] == [p1]; res["map1_top2"] += p1 in r1[:2]
        res["map2_top1"] += r2[:1] == [p2]; res["map2_top2"] += p2 in r2[:2]
        res["either_pick_in_top2"] += p1 in r1[:2] and p2 in r2[:2]
        res["ll"] += -math.log(max(pr["map1"].get(p1, 0), 0.01)) - math.log(max(pr["map2"].get(p2, 0), 0.01))
    if res["cases"]:
        res["ll"] = round(res["ll"] / (2 * res["cases"]), 3)
    return res
