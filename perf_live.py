"""Live inputs for perf.project() from the HLTV scraper database - the same rows the training export used:
player maps (totals side) with kills, rounds, headshots, LAN flag, excluding substitution maps (not 10 players) and
flagging 0-HS-on-12+-kills rows; opponent maps with the kills its opponents scored. By HLTV ids (namesakes resolved by
team, like the rest of the grader). Cached 15 minutes per player / team."""
import hashlib, json, threading, time
from datetime import date, timedelta
import hltv, hltv_store, perf

TTL = 15 * 60
ROW_POLICY = "verified-history-1"
_LOCK = threading.Lock()
_CACHE = {}

# 2026-10-13 CONTROLLED ROLLBACK of the performance-model input (owner rule: a dataset that reached live grading before its
# validation and activation gate goes back to the last approved model-input revision - this restores the agreed release
# state, it is no finding that incomplete history predicts better). The HLTV backfill (HLTV_COVERAGE.md) was written straight
# into the live tables - 19,590 matches first fetched 2026-10-12 21:27-21:35 and 2026-10-13 00:23-04:24 UTC - and read by
# live grades from 04:26:15 UTC with no staged revision and no approval; since then the regular scan also takes every tier.
# N0 restores the APPROVED INPUT POLICY (the pre-repair coverage) after an unintended activation - it is not a proven
# accuracy improvement: the 23-match comparison was inconclusive (HLTV_COVERAGE.md). It is a RECONSTRUCTED policy, not an
# exact historical restoration (the pre-repair scanner logged no per-match eligibility):
#   IN  - every match first fetched before the backfill began: the KNOWN original set, kept exactly (production
#         2026-10-14: 8,597 matches, all discovered in scrape_queue before it; the 2026-10-11 seed has raw pages too);
#       - a match first fetched later, DATED on or after NEW_FROM (a fresh result the regular scan lists), whose both teams
#         are in the VRS top OLD_TOP_N of the dated team_rankings snapshot on or before its first-fetch day (UTC), by HLTV
#         team id - the pre-repair scan rule on the scraper's own STORED rankings (daily copies of the VRS list its scan
#         read live; never a later snapshot, never today's list for an older match).
#   OUT - older-dated history first fetched after the start (the backfill and every later all-tier history, whichever
#         window or job fetched it), lower-tier new matches, a later arrival with no snapshot on or before its day (missing
#         snapshot = out), no parsed teams or no overview, a match with NO raw page (r4, 2026-10-14 post-deploy check: r3
#         counted those as the seed, but the seed has raw pages - the only ones were 3 rows of a new source 'cito' dated
#         that day, discovered 22:24 UTC, which r3 let in without the rule), and any match not evaluated yet (FAIL
#         CLOSED: an inclusion list).
# Not reproducible exactly: the old scan compared names (lowercased) against the live list AT SCAN TIME - this uses the
# daily snapshot (intra-day ranking changes are invisible) and team ids (a team whose VRS and HLTV names differ was missed
# by the old scan, not here). r2 (fetch windows) misjudged 5 matches on production: 4 fresh top-200 results the regular
# scan fetched inside a backfill window (out) and 1 backfill-listed old match fetched after the windows (in); r3 decides by
# the match's own date instead. ONLY the performance model's queries read N0; the repaired data stays in storage, and
# settlement, display, the last-10 / H2H records and the stake checks read the full tables - the grader uses MIXED
# revisions and every forecast records both (input_revision, picklog data_rev).
INPUT_REV = "N0"
INPUT_RULE = "n0-r4"      # the rule above (r1: exclusion list + backfill-only fallback; r2: fetch windows instead of dates;
                          # r3: a match with no raw page counted as the seed)
BACKFILL_FROM = "2026-10-12 21:27:00+00"
NEW_FROM = "2026-10-12"   # the backfill's day: a match dated before it and first fetched after the start is history
OLD_TOP_N = 200
INCL_TTL = 10 * 60        # re-evaluated this often (one query set, ~0.5 s): a newly fetched match is out until then
_W = {"from": BACKFILL_FROM, "top": OLD_TOP_N, "new_from": NEW_FROM}
_PRE_SQL = """
SELECT mo.matchid FROM match_overview mo
LEFT JOIN (SELECT matchid, min(fetched_at) AS f FROM raw_pages GROUP BY matchid) x ON x.matchid = mo.matchid
WHERE x.f < %(from)s
"""
# set-based (2026-10-14: the first form took 6.7-9.9 s on production - over the app's 5 s statement timeout, so N0 would
# never have loaded: the snapshot lookup ran once per later arrival). The snapshot is resolved once per first-fetch DAY
# (MATERIALIZED - otherwise the planner re-runs it per row), and only fresh later arrivals are judged, by one join each
_LATE_SQL = """
WITH first AS (SELECT matchid, min(fetched_at) AS f FROM raw_pages GROUP BY matchid HAVING min(fetched_at) >= %(from)s),
     snaps AS MATERIALIZED (SELECT DISTINCT date FROM team_rankings),
     days AS MATERIALIZED (SELECT x.d, (SELECT max(s.date) FROM snaps s WHERE s.date <= x.d) AS snap
                           FROM (SELECT DISTINCT (f AT TIME ZONE 'UTC')::date AS d FROM first) x),
     late AS (SELECT l.matchid, mo.date AS md, dd.snap
              FROM first l JOIN days dd ON dd.d = (l.f AT TIME ZONE 'UTC')::date
              LEFT JOIN match_overview mo ON mo.matchid = l.matchid),
     cand AS (SELECT matchid, snap FROM late WHERE md >= %(new_from)s AND snap IS NOT NULL),
     mt AS (SELECT DISTINCT m.matchid, m.teamid FROM matches m JOIN cand c ON c.matchid = m.matchid),
     judged AS (SELECT c.matchid, count(DISTINCT mt.teamid) AS teams,
                       count(DISTINCT mt.teamid) FILTER (WHERE r.rank <= %(top)s) AS top
                FROM cand c LEFT JOIN mt ON mt.matchid = c.matchid
                LEFT JOIN team_rankings r ON r.teamid = mt.teamid AND r.date = c.snap
                GROUP BY c.matchid)
SELECT l.matchid,
       CASE WHEN l.md IS NULL OR l.md < %(new_from)s THEN 'history'
            WHEN l.snap IS NULL THEN 'no_snapshot'
            WHEN coalesce(j.teams, 0) = 0 THEN 'no_teams'
            WHEN j.top < j.teams THEN 'outside_top'
            ELSE 'in' END AS why,
       l.snap
FROM late l LEFT JOIN judged j ON j.matchid = l.matchid
UNION ALL
SELECT mo.matchid, 'no_raw_page', NULL::date FROM match_overview mo
WHERE NOT EXISTS (SELECT 1 FROM raw_pages r WHERE r.matchid = mo.matchid)
"""
_SNAP_SQL = "SELECT date, count(*) FROM team_rankings GROUP BY date ORDER BY date"
_IN = {"val": None, "next": 0.0, "loading": False, "error": None, "loaded_at": None, "stale_since": None}
_IN_COND = threading.Condition()


def _load_n0():
    """{"pre", "late_in" (frozensets of the match ids IN N0), "counts", "snapshots", "snapshots_used", "sha", "rule"} from
    the database. Any failure raises - there is no partial or approximate N0."""
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            cur.execute(_PRE_SQL, _W)
            pre = frozenset(int(r[0]) for r in cur.fetchall())
            cur.execute(_LATE_SQL, _W)
            late = [(int(m), str(w), s) for m, w, s in cur.fetchall()]
            cur.execute(_SNAP_SQL)
            snaps = [(str(d), int(n)) for d, n in cur.fetchall()]
    finally:
        con.close()
    counts = {k: 0 for k in ("in", "history", "outside_top", "no_snapshot", "no_teams", "no_raw_page")}
    for _, w, _s in late:
        counts[w] = counts.get(w, 0) + 1
    sha = hashlib.sha256(json.dumps([sorted(pre), sorted((m, w) for m, w, _ in late)]).encode()).hexdigest()[:12]
    return {"pre": pre, "late_in": frozenset(m for m, w, _ in late if w == "in"), "counts": counts, "snapshots": snaps,
            "snapshots_used": sorted({str(s) for _, w, s in late if s is not None}), "sha": sha, "rule": INPUT_RULE}


def n0():
    """The performance model's input revision N0 (the match ids it may read). Shared: one evaluation at a time (700 members
    at once make one query set); a current one is returned at once, a due one is re-evaluated by one caller while the others
    keep the previous one. A failed evaluation keeps the last VERIFIED evaluation of this exact rule - memory, then the stored
    copy on disk - visibly marked stale (input_revision says STALE; newer matches stay out, they were never evaluated), and
    is tried again after hltv.FAIL_TTL. With none anywhere it raises hltv.Unavailable: the projection is then temporarily
    unavailable (transient) - never graded on the full tables or on an approximate set."""
    with _IN_COND:
        while True:
            val, now = _IN["val"], time.time()
            if val is not None and (now < _IN["next"] or _IN["loading"]):
                return val
            if not _IN["loading"]:
                if val is None and now < _IN["next"]:      # failed a moment ago: one caller tries again after FAIL_TTL
                    raise hltv.Unavailable("HLTV database unreachable - performance-model input revision not available")
                _IN["loading"] = True
                break
            if not _IN_COND.wait(timeout=hltv.POOL_WAIT + 30):
                raise hltv.Unavailable("HLTV database unreachable - performance-model input revision still loading")
    err, stored = None, None
    try:
        new = _load_n0()
    except Exception as e:      # noqa: BLE001 - kept below; never a projection on the full tables
        new, err = None, e
    if new is None:
        stored = hltv_store.load(("perf_n0", INPUT_REV, INPUT_RULE))
    with _IN_COND:
        _IN["loading"] = False
        if new is not None:
            _IN.update(val=new, next=time.time() + INCL_TTL, error=None, loaded_at=time.time(), stale_since=None)
        else:
            _IN.update(next=time.time() + hltv.FAIL_TTL, error=f"{type(err).__name__}: {hltv.err_text(err)}")
            if _IN["val"] is None and stored is not None and (stored[1] or {}).get("rule") == INPUT_RULE:
                _IN.update(val=stored[1], loaded_at=stored[0])
            if _IN["val"] is not None and _IN["stale_since"] is None:
                _IN["stale_since"] = time.time()
        _IN_COND.notify_all()
        val = _IN["val"]
    if new is not None:
        hltv_store.save(("perf_n0", INPUT_REV, INPUT_RULE), new)
    if val is None:
        raise hltv.Unavailable("HLTV database unreachable - performance-model input revision not available") from err
    return val


def _in_n0(mid, s):
    return mid in s["pre"] or mid in s["late_in"]


def input_revision():
    """The performance-model input revision for forecasts (data_rev) and the admin status - None until it is loaded."""
    v = _IN["val"]
    if v is None:
        return None
    c, sn = v["counts"], v.get("snapshots_used") or []
    rev = (f"{INPUT_REV}/{v['rule']}:pre={len(v['pre'])};late_in={len(v['late_in'])};history_out={c.get('history', 0)};"
           f"late_out={c.get('outside_top', 0)};no_snapshot={c.get('no_snapshot', 0)};no_teams={c.get('no_teams', 0)};"
           f"no_raw_page={c.get('no_raw_page', 0)};"
           f"snapshots={sn[0] + '..' + sn[-1] if sn else '-'};sha={v['sha']}")
    if _IN["stale_since"]:
        rev += ";STALE evaluated " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_IN["loaded_at"] or 0))
    return rev


def input_status():
    v = _IN["val"] or {}
    return {"revision": INPUT_REV, "rule": INPUT_RULE, "pinned": input_revision(), "stale": bool(_IN["stale_since"]),
            "policy": "reconstructed pre-repair input policy (not an exact historical restoration)",
            "backfill_from": BACKFILL_FROM, "new_from": NEW_FROM, "old_top_n": OLD_TOP_N, "counts": v.get("counts"),
            "matches_in_n0": (len(v["pre"]) + len(v["late_in"])) if v else None,
            "ranking_snapshots": v.get("snapshots"), "snapshots_used": v.get("snapshots_used"),
            "evaluated_at": _IN["loaded_at"], "last_error": _IN["error"],
            "applies_to": "performance-model queries only (player maps, opponent maps, rr-1 team rounds)",
            "full_tables": "last-10 / H2H records, stake checks, settlement, display"}

_PLAYER_SQL = """
WITH pm AS (SELECT matchid, mapid, count(*) AS n FROM player_stats
            WHERE sideid = 0 AND matchid IN (SELECT matchid FROM player_stats WHERE playerid = ANY(%(ids)s))
            GROUP BY matchid, mapid HAVING count(*) = 10 AND count(DISTINCT playerid) = 10)
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
HAVING count(*) = 5 AND count(DISTINCT ps.playerid) = 5 AND count(ps.k) = 5
   AND MIN(ps.k) >= 0 AND MIN(t.score) >= 0 AND MIN(o.score) >= 0 AND MAX(t.score + o.score) > 0
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


def player_key(nick, team, since):
    return ("p", hltv._norm(nick), hltv._norm(team), since, INPUT_REV, INPUT_RULE, ROW_POLICY)


def opponent_key(opponent, since):
    return ("o", hltv._norm(opponent), since, INPUT_REV, INPUT_RULE, ROW_POLICY)


def player_maps(nick, team, since):
    def load():
        s = n0()                      # the revision first: without it nothing is read (fail closed)
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
        if raw and not any(_in_n0(mid, s) for *_, mid in raw):
            return None, []           # known only from outside N0 (the backfill): not in HLTV under N0 - BO3.gg fallback, as before
        return ids, [(d.toordinal(), k, r, hs, None if lan is None else bool(lan), not (hs == 0 and k >= 12))
                     for d, k, r, hs, lan, n, mid in raw if n == 10 and _in_n0(mid, s)]
    return _cached(player_key(nick, team, since), load)


def opp_maps(opponent, since):
    def load():
        s = n0()
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
        if raw and not any(_in_n0(mid, s) for *_, mid in raw):
            return None, []           # known only from outside N0: 'opponent not in HLTV' under N0, as before the backfill
        return tid, [(d.toordinal(), int(k), int(r)) for d, k, r, mid in raw if _in_n0(mid, s)]
    return _cached(opponent_key(opponent, since), load)


def team_rounds(team, since):
    """A team's HLTV maps as (day_ordinal, rounds won, rounds lost) - SHADOW input of the rr-1 market candidate
    (perf_market.py, 2026-10-13): never read by the live projection. Same cache / last-good fallback as the rest."""
    def load():
        s = n0()
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
        if raw and not any(_in_n0(mid, s) for *_, mid in raw):
            return None, []
        return tid, [(d.toordinal(), int(a), int(b)) for d, a, b, mid in raw if a is not None and b is not None and _in_n0(mid, s)]
    return _cached(("r", hltv._norm(team), since, INPUT_REV, INPUT_RULE), load)


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
    rev = input_revision()
    out["hltv_data"] = {"player_maps": served(player_key(nick, team, since)),
                        "opp_maps": served(opponent_key(opponent, since)) if opponent else None,
                        "input_revision": rev, "row_policy": ROW_POLICY, "input_revision_stale": bool(rev and "STALE" in rev)}
    if not om:            # no team, or a team with no maps in the window: the opponent factor is the pro average (1.00)
        out["data_quality"] = ((out["data_quality"] + "; " if out["data_quality"] != "ok" else "")
                               + ("opponent not in HLTV" if not tid else "opponent has no HLTV maps") + " - average opponent")
    if out["hltv_data"]["input_revision_stale"]:    # the last verified N0 while its re-evaluation fails: newer matches are out
        out["data_quality"] = ((out["data_quality"] + "; " if out["data_quality"] != "ok" else "")
                               + "HLTV history revision N0 from its last good evaluation (refresh failing - newer matches not counted)")
    return out
