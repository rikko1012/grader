"""Live inputs for perf.project() from the HLTV scraper database - the same rows the training export used:
player maps (totals side) with kills, rounds, headshots, LAN flag, excluding substitution maps (not 10 players) and
flagging 0-HS-on-12+-kills rows; opponent maps with the kills its opponents scored. By HLTV ids (namesakes resolved by
team, like the rest of the grader). Cached 15 minutes per player / team."""
import threading, time
from datetime import date, timedelta
import hltv, hltv_store, perf

TTL = 15 * 60
_LOCK = threading.Lock()
_CACHE = {}

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
SELECT mo.date, SUM(ps.k), MAX(t.score + o.score)
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
                cur.execute(_PLAYER_SQL, {"ids": ids, "since": since})
                rows = [(d.toordinal(), k, r, hs, bool(lan), not (hs == 0 and k >= 12)) for d, k, r, hs, lan, n in cur.fetchall() if n == 10]
                return ids, rows
        finally:
            con.close()
    return _cached(("p", hltv._norm(nick), hltv._norm(team), since), load)


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
                return tid, [(d.toordinal(), int(k), int(r)) for d, k, r in cur.fetchall()]
        finally:
            con.close()
    return _cached(("o", hltv._norm(opponent), since), load)


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
                cur.execute(_ROUNDS_SQL, {"tid": tid, "since": since})
                return tid, [(d.toordinal(), int(a), int(b)) for d, a, b in cur.fetchall() if a is not None and b is not None]
        finally:
            con.close()
    return _cached(("r", hltv._norm(team), since), load)


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
    out["hltv_data"] = {"player_maps": served(("p", hltv._norm(nick), hltv._norm(team), since)),
                        "opp_maps": served(("o", hltv._norm(opponent), since)) if opponent else None}
    if not om:            # no team, or a team with no maps in the window: the opponent factor is the pro average (1.00)
        out["data_quality"] = ((out["data_quality"] + "; " if out["data_quality"] != "ok" else "")
                               + ("opponent not in HLTV" if not tid else "opponent has no HLTV maps") + " - average opponent")
    return out
