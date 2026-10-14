"""Live inputs for perf.project() from the HLTV scraper database - the same rows the training export used:
player maps (totals side) with kills, rounds, headshots, LAN flag, excluding substitution maps (not 10 players) and
flagging 0-HS-on-12+-kills rows; opponent maps with the kills its opponents scored. By HLTV ids (namesakes resolved by
team, like the rest of the grader). Cached 15 minutes per player / team."""
import hashlib, json, threading, time
from datetime import date, timedelta
import hltv, hltv_store, perf

TTL = 15 * 60
_LOCK = threading.Lock()
_CACHE = {}

# 2026-10-13 CONTROLLED ROLLBACK of the performance-model input (owner rule: a dataset that reached live grading before its
# validation and activation gate goes back to the last approved model-input revision - this restores the agreed release
# state, it is no finding that incomplete history predicts better). The HLTV backfill (HLTV_COVERAGE.md) was written straight
# into the live tables - 19,590 matches first fetched 2026-10-12 21:27-21:35 and 2026-10-13 00:23-04:24 UTC - and read by
# live grades from 04:26:15 UTC with no staged revision and no approval; since then the regular scan also takes every tier.
# N0 = the approved coverage: the backfill's matches left out, and a match first fetched after the backfill began kept only
# under the pre-repair scan rule (both teams in the VRS top OLD_TOP_N of the latest team_rankings snapshot on or before its
# first fetch, by HLTV team id). ONLY these queries (the performance model's inputs) are pinned: the repaired data stays in
# storage, and settlement, display and every other reader see the full tables.
INPUT_REV = "N0"
BACKFILL_FROM = "2026-10-12 21:27:00+00"
BACKFILL_WINDOWS = (("2026-10-12 21:27:00+00", "2026-10-12 21:36:00+00"), ("2026-10-13 00:23:00+00", "2026-10-13 04:25:00+00"))
OLD_TOP_N = 200
EXCL_TTL = 30 * 60        # later arrivals are checked against the old rule this often (the scan runs every 2 h)
_W = {"from": BACKFILL_FROM, "w1a": BACKFILL_WINDOWS[0][0], "w1b": BACKFILL_WINDOWS[0][1],
      "w2a": BACKFILL_WINDOWS[1][0], "w2b": BACKFILL_WINDOWS[1][1], "top": OLD_TOP_N}
_IN_BACKFILL = "(l.f >= %(w1a)s AND l.f < %(w1b)s) OR (l.f >= %(w2a)s AND l.f < %(w2b)s)"
_EXCL_SQL = f"""
WITH first AS (SELECT matchid, min(fetched_at) AS f FROM raw_pages GROUP BY matchid HAVING min(fetched_at) >= %(from)s),
     snaps AS (SELECT DISTINCT date FROM team_rankings)
SELECT l.matchid, ({_IN_BACKFILL}) AS backfill
FROM first l
WHERE ({_IN_BACKFILL})
   OR EXISTS (SELECT 1 FROM (SELECT DISTINCT teamid FROM matches m WHERE m.matchid = l.matchid) t
              WHERE NOT EXISTS (SELECT 1 FROM team_rankings r
                                WHERE r.teamid = t.teamid AND r.rank <= %(top)s
                                  AND r.date = COALESCE((SELECT max(date) FROM snaps WHERE date <= l.f::date),
                                                        (SELECT min(date) FROM snaps))))
"""
# the backfill alone (the query the 2026-10-13 replay ran on production): used only when the old-rule query itself fails
_BACKFILL_SQL = f"""
SELECT l.matchid, TRUE FROM (SELECT matchid, min(fetched_at) AS f FROM raw_pages GROUP BY matchid) l WHERE {_IN_BACKFILL}
"""
_EX = {"val": None, "next": 0.0, "loading": False, "error": None, "loaded_at": None}
_EX_COND = threading.Condition()


def _load_excluded():
    """{"ids": frozenset, "backfill", "late_out", "late_rule", "sha"} - the match ids outside N0, from the database."""
    def run(sql):
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute(sql, _W)
                return cur.fetchall()
        finally:
            con.close()
    try:
        rows, rule = run(_EXCL_SQL), f"top{OLD_TOP_N}@first-fetch"
    except Exception as e:
        if hltv.is_transient(e):
            raise
        rows, rule = run(_BACKFILL_SQL), f"unavailable ({type(e).__name__}) - backfill only"
    bf = sorted(int(m) for m, b in rows if b)
    late = sorted(int(m) for m, b in rows if not b)
    sha = hashlib.sha256(json.dumps([bf, late]).encode()).hexdigest()[:12]
    return {"ids": frozenset(bf) | frozenset(late), "backfill": len(bf), "late_out": len(late), "late_rule": rule, "sha": sha}


def excluded():
    """The HLTV match ids outside the pinned revision. Shared: one load at a time (700 members at once make one query);
    a current set is returned at once, a stale one is refreshed by one caller while the others keep using it; a failed
    load keeps the last good set (memory, then the stored copy on disk) and is tried again after hltv.FAIL_TTL. Raises
    hltv.Unavailable when no set exists anywhere - the projection is then transient, never graded on the full tables."""
    with _EX_COND:
        while True:
            val, now = _EX["val"], time.time()
            if val is not None and (now < _EX["next"] or _EX["loading"]):
                return val
            if not _EX["loading"]:
                if val is None and now < _EX["next"]:      # failed a moment ago: one caller tries again after FAIL_TTL
                    raise hltv.Unavailable("HLTV database unreachable - performance-model input revision not available")
                _EX["loading"] = True
                break
            if not _EX_COND.wait(timeout=hltv.POOL_WAIT + 30):
                raise hltv.Unavailable("HLTV database unreachable - performance-model input revision still loading")
    err = None
    try:
        new = _load_excluded()
    except Exception as e:      # noqa: BLE001 - kept below; never a projection on the full tables
        new, err = None, e
    if new is None:
        stored = hltv_store.load(("perf_excl", INPUT_REV))
    with _EX_COND:
        _EX["loading"] = False
        if new is not None:
            _EX.update(val=new, next=time.time() + EXCL_TTL, error=None, loaded_at=time.time())
        else:
            _EX.update(next=time.time() + hltv.FAIL_TTL, error=f"{type(err).__name__}: {hltv.err_text(err)}")
            if _EX["val"] is None and stored is not None:
                _EX.update(val=stored[1], loaded_at=stored[0])
        _EX_COND.notify_all()
        val = _EX["val"]
    if new is not None:
        hltv_store.save(("perf_excl", INPUT_REV), new)
    if val is None:
        raise hltv.Unavailable("HLTV database unreachable - performance-model input revision not available") from err
    return val


def input_revision():
    """The performance-model input revision for forecasts (data_rev) and the admin status - None until it is loaded."""
    v = _EX["val"]
    if v is None:
        return None
    return f"{INPUT_REV}:backfill={v['backfill']};late_out={v['late_out']};rule={v['late_rule']};sha={v['sha']}"


def input_status():
    v = _EX["val"] or {}
    return {"revision": INPUT_REV, "pinned": input_revision(), "backfill_windows": BACKFILL_WINDOWS, "old_top_n": OLD_TOP_N,
            "backfill_matches": v.get("backfill"), "later_matches_outside_old_rule": v.get("late_out"),
            "rule": v.get("late_rule"), "loaded_at": _EX["loaded_at"], "last_error": _EX["error"]}

_PLAYER_SQL = """
WITH pm AS (SELECT matchid, mapid, count(*) AS n FROM player_stats
            WHERE sideid = 0 AND matchid IN (SELECT matchid FROM player_stats WHERE playerid = ANY(%(ids)s))
            GROUP BY matchid, mapid)
SELECT mo.date, ps.k, own.score + opp.score, h.hs, mo.lan, pm.n
FROM player_stats ps
JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
JOIN match_overview mo ON mo.matchid = ps.matchid
JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
JOIN pm ON pm.matchid = ps.matchid AND pm.mapid = ps.mapid
WHERE ps.playerid = ANY(%(ids)s) AND ps.sideid = 0 AND mo.date >= %(since)s
"""
_OPP_SQL = """
SELECT mo.date, SUM(ps.k), MAX(t.score + o.score), t.matchid
FROM matches t
JOIN matches o ON o.matchid = t.matchid AND o.mapid = t.mapid AND o.sideid = 0 AND o.teamid <> t.teamid
JOIN match_overview mo ON mo.matchid = t.matchid
JOIN match_maps mm ON mm.matchid = t.matchid AND mm.mapid = t.mapid
JOIN player_stats ps ON ps.matchid = t.matchid AND ps.mapid = t.mapid AND ps.sideid = 0 AND ps.teamid = o.teamid
WHERE t.teamid = %(tid)s AND t.sideid = 0 AND mo.date >= %(since)s
GROUP BY mo.date, t.matchid, t.mapid
"""


_ROUNDS_SQL = """
SELECT mo.date, t.score, o.score
FROM matches t
JOIN matches o ON o.matchid = t.matchid AND o.mapid = t.mapid AND o.sideid = 0 AND o.teamid <> t.teamid
JOIN match_overview mo ON mo.matchid = t.matchid
WHERE t.teamid = %(tid)s AND t.sideid = 0 AND mo.date >= %(since)s
"""
# the same queries plus each row's match id - the N0 pin above drops the rows outside the pinned revision
_PLAYER_SQL_N0 = _PLAYER_SQL.replace("mo.lan, pm.n\nFROM", "mo.lan, pm.n, ps.matchid\nFROM")
_ROUNDS_SQL_N0 = _ROUNDS_SQL.replace("t.score, o.score\nFROM", "t.score, o.score, t.matchid\nFROM")


_FAILED = object()        # a failed read with no good answer anywhere, kept FAIL_TTL s: one retry per key, shared
_SERVED = {}              # key -> (state, as_of): "live" | "memory copy" | "stored copy" - reported by projection()


def _cached(key, fn):
    """15-minute memory cache; every good answer is also kept on the data disk (hltv_store). When the HLTV database is
    unreachable the last good answer is used - memory first, then disk - so an outage or a restart during one never
    changes a projection (owner 2026-10-11). Raises only when there is no good answer anywhere. 2026-10-13 audit: one
    read per key at a time (hltv.single_flight) and a failure with no good answer retried once per FAIL_TTL s for
    everyone (concurrent grades each re-ran the failing query); served() says which answer was used and how old it is."""
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return _value(hit[1])
    return hltv.single_flight(("perf",) + key, lambda: _read(key, fn, hit), lambda: _peek(key))


def _value(v):
    if v is _FAILED:
        raise hltv.Unavailable("HLTV database unreachable (tried under 30 s ago)")
    return v


def _peek(key):
    with _LOCK:
        hit = _CACHE.get(key)
    return _value(hit[1]) if hit and time.time() - hit[0] < TTL else hltv.MISS


def _read(key, fn, hit):
    try:
        val = fn()
    except Exception as e:
        if not hltv.is_transient(e):
            raise
        if hit and hit[1] is not _FAILED:      # the last good answer in memory, retried again in FAIL_TTL s
            st = _SERVED.get(key) or ("memory copy", None)
            with _LOCK:
                _CACHE[key] = (time.time() - TTL + hltv.FAIL_TTL, hit[1])
                _SERVED[key] = ("stored copy" if st[0] == "stored copy" else "memory copy", st[1])
            return hit[1]
        stored = hltv_store.load(("perf",) + key)
        with _LOCK:
            _CACHE[key] = (time.time() - TTL + hltv.FAIL_TTL, _FAILED if stored is None else stored[1])
            if stored is not None:
                _SERVED[key] = ("stored copy", stored[0])
        if stored is None:
            raise
        return stored[1]
    with _LOCK:
        if len(_SERVED) > 20000:
            _SERVED.clear()
        _CACHE[key] = (time.time(), val)
        _SERVED[key] = ("live", time.time())
    hltv_store.save(("perf",) + key, val)
    return val


def served(key):
    """{"state", "as_of", "age_min"} of the answer _cached last gave for key - None when never read."""
    st = _SERVED.get(key)
    if not st:
        return None
    return {"state": st[0], "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st[1])) if st[1] else None,
            "age_min": round((time.time() - st[1]) / 60, 1) if st[1] else None}


def player_maps(nick, team, since):
    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                ids = hltv._player_ids(cur, nick, team)
                if not ids:
                    return None, []
                cur.execute(_PLAYER_SQL_N0, {"ids": ids, "since": since})
                raw = cur.fetchall()
        finally:
            con.close()
        ex = excluded()["ids"]
        if raw and all(mid in ex for *_, mid in raw):
            return None, []           # known only from the backfill: not in HLTV under N0 (BO3.gg fallback, as before it)
        return ids, [(d.toordinal(), k, r, hs, bool(lan), not (hs == 0 and k >= 12))
                     for d, k, r, hs, lan, n, mid in raw if n == 10 and mid not in ex]
    return _cached(("p", hltv._norm(nick), hltv._norm(team), since, INPUT_REV), load)


def opp_maps(opponent, since):
    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                # canonical id: exact name, verified alias, affix variants (2026-10-13 audit: an aliased opponent - 'NIP' - got
                # the average opponent); a failed read still raises (never 'opponent not in HLTV')
                tid = hltv.canonical_team_id(cur, opponent)
                if not tid:
                    return None, []
                cur.execute(_OPP_SQL, {"tid": tid, "since": since})
                raw = cur.fetchall()
        finally:
            con.close()
        ex = excluded()["ids"]
        if raw and all(mid in ex for *_, mid in raw):
            return None, []           # known only from the backfill: 'opponent not in HLTV' under N0, as before it
        return tid, [(d.toordinal(), int(k), int(r)) for d, k, r, mid in raw if mid not in ex]
    return _cached(("o", hltv._norm(opponent), since, INPUT_REV), load)


def team_rounds(team, since):
    """A team's HLTV maps as (day_ordinal, rounds won, rounds lost) - SHADOW input of the rr-1 market candidate
    (perf_market.py, 2026-10-13): never read by the live projection. Same cache / last-good fallback as the rest."""
    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                tid = hltv.canonical_team_id(cur, team)      # the same identity as opp_maps
                if not tid:
                    return None, []
                cur.execute(_ROUNDS_SQL_N0, {"tid": tid, "since": since})
                raw = cur.fetchall()
        finally:
            con.close()
        ex = excluded()["ids"]
        if raw and all(mid in ex for *_, mid in raw):
            return None, []
        return tid, [(d.toordinal(), int(a), int(b)) for d, a, b, mid in raw if a is not None and b is not None and mid not in ex]
    return _cached(("r", hltv._norm(team), since, INPUT_REV), load)


def round_shares(team, opponent, day, since):
    """{team, opp: time-weighted round-win share before `day` (perf_market.round_share), maps counts} - or {"error"}
    (never a 0.5 stand-in for a failed read: the shadow candidate is then not computed)."""
    import perf_market
    try:
        out = {}
        for k, nm in (("team", team), ("opp", opponent)):
            tid, rows = team_rounds(nm, since) if nm else (None, [])
            out[k] = perf_market.round_share(rows, day)
            out[k + "_maps"] = sum(1 for r in rows if r[0] < day)
            out[k + "_id"] = tid
        return out
    except Exception as e:          # noqa: BLE001 - shadow only: the live projection never depends on it
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def g7_fields(p):
    """The perf-1 (G7) projection on the same inputs - logged as the comparison "perf1_g7", never shown."""
    return {k: p[k] for k in ("version", "lambda", "lambda_kills", "hs_per_kill", "sd", "spread_c", "spread_c_kills", "rate",
                              "opp_factor", "rounds_per_map", "data_quality")}


def projection(nick, team, opponent, lan, scope="12", prop="kills", day=None):
    """perf.project() on live HLTV data. day: match-day ordinal (default today) - history strictly before it."""
    if not hltv.dsn():
        return {"error": "HLTV database not configured"}
    day = day or date.today().toordinal()
    since = (date.fromordinal(day) - timedelta(days=420)).isoformat()
    ids, pm = player_maps(nick, team, since)
    if not ids:
        return {"error": f"player {nick!r} ({team}) not found in HLTV"}
    tid, om = opp_maps(opponent, since) if opponent else (None, [])
    out = perf.project(pm, om, lan, day, scope, prop)
    out["round_share"] = round_shares(team, opponent, day, since)     # shadow candidate input only (rr-1)
    out["g7"] = g7_fields(perf.project(pm, om, lan, day, scope, prop, perf.PARAMS_PERF1, perf.SPREAD_PERF1, "perf-1"))
    out.update(hltv_player_ids=ids, hltv_opp_team=tid, player_maps=len(pm), opp_maps=len(om),
               opp_known=bool(om), lan_known=lan is not None,      # known = HLTV has his maps (a team id alone is no data)
               newest_map_day=max((r[0] for r in pm if r[0] < day), default=None))   # his last HLTV map before the match day
    # which answers the projection used: read now, or the last good copy (memory / stored) served during an outage, + age
    out["hltv_data"] = {"player_maps": served(("p", hltv._norm(nick), hltv._norm(team), since, INPUT_REV)),
                        "opp_maps": served(("o", hltv._norm(opponent), since, INPUT_REV)) if opponent else None,
                        "input_revision": input_revision()}
    if not om:            # no team, or a team with no maps in the window: the opponent factor is the pro average (1.00)
        out["data_quality"] = ((out["data_quality"] + "; " if out["data_quality"] != "ok" else "")
                               + ("opponent not in HLTV" if not tid else "opponent has no HLTV maps") + " - average opponent")
    return out
