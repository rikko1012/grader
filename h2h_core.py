"""Roster-core head-to-head (hc-1) for CS2, from HLTV match rosters. Display only.

Owner 2026-11-20: "3 people = a team's core: if a player played against 3 players on the same team then that is an H2H, not only
their team versus that team". Teams also switch orgs (3DMAX's five became Envy on 2026-10-15), so the rule never reads team names:

- Tonight's opponent's CURRENT players are the five HLTV player ids of its newest HLTV series (its canonical HLTV team id:
  hltv.resolve_team_id), when that series is at most LINEUP_DAYS old. A benched player does not play, so he is not in that lineup.
  Fewer than 5 ids or no recent series (an org whose new roster has not played under it yet): no core, no core H2H - the
  exact-opponent meetings alone, as before.
- A past series of his is an H2H when at least MIN_CORE (3) of those five played on the side AGAINST him in it, whatever that
  side was called then and whichever team he played for. Series where they were his teammates never count.
- The meetings with the same org (today's rule, any lineup) still count: the owner said "not only". The card marks each meeting
  as a roster meeting (3+ of tonight's players) or an org meeting (fewer).
- Team core meetings: his CURRENT core (3+ of his current teammates, from his newest HLTV series) against tonight's core in a
  series he did not play. Listed on the card with the score, never with a value of his ("-").

Nothing here feeds a grade. The live CS2 kills grade (cm-2 performance model) does not read H2H; the older pipeline's roster-matched
H2H input (cs2_scout.h2h, grader h2h_series) is untouched. The walk-forward test of a roster-core factor is in
research/h2h_core_2026_11_20 (FROZEN_DEFINITIONS.md, RESULTS.md).

Reads: one cached read per opponent (CORE_TTL), one per player for the team core meetings; single-flight per key, at most
SLOTS reads at a time, the last good answer is kept through a failed read and a failure is never cached (memory:
transient-failure-rule)."""
import threading
import time
from datetime import date, timedelta

MIN_CORE = 3        # owner: "3 people = a team's core"
LINEUP = 5
LINEUP_DAYS = 60    # tonight's lineup = the team's newest series no older than this
DAYS = 365          # H2H window, as cs2_scout.H2H_DAYS (owner 2026-10-06: last 12 months)
CORE_TTL = 1800     # a lineup changes between series, never within minutes
FAIL_RETRY = 30
SLOTS = threading.BoundedSemaphore(2)
SLOT_WAIT = 0.5


# ---------------------------------------------------------------- pure rules (tested on real HLTV rows)
def current_lineup(rows):
    """rows: (playerid, maps_played) of one team's newest series -> the five ids who played the most maps there, or None when fewer
    than five played (an incomplete record is no roster)."""
    rows = sorted(((int(p), int(n or 0)) for p, n in rows or [] if p is not None), key=lambda r: (-r[1], r[0]))
    ids = [p for p, _ in rows[:LINEUP]]
    return ids if len(ids) == LINEUP else None


def core_matches(rows, current_ids, min_core=MIN_CORE):
    """rows: (matchid, teamid, team_name, date, player_ids) per side of a series -> {matchid: {"side", "team", "date", "faced"}} for
    the sides on which at least min_core of current_ids played. Five ids cannot reach 3 on both sides of one series."""
    cur = {int(x) for x in current_ids or ()}
    out = {}
    if len(cur) < min_core:
        return out
    for mid, tid, name, d, ids in rows or ():
        faced = sorted(cur & {int(x) for x in ids or ()})
        if len(faced) >= min_core:
            out[int(mid)] = {"side": int(tid), "team": name, "date": str(d)[:10], "faced": faced}
    return out


def hltv_mid(mid):
    """'hltv-2399012' / 2399012 -> 2399012; anything else -> None."""
    s = str(mid or "")
    s = s[5:] if s.startswith("hltv-") else s
    return int(s) if s.isdigit() else None


def tag(mids, oids, core):
    """Per series (parallel lists: his series' match ids and the HLTV team id he played against): the core meeting it is, or None.
    A series counts only when tonight's core was on the side he faced (oids[i] == that side)."""
    out = []
    cm = (core or {}).get("matches") or {}
    names = (core or {}).get("names") or {}
    for i, mid in enumerate(mids or ()):
        m = cm.get(hltv_mid(mid))
        oid = oids[i] if oids and i < len(oids) else None
        if m and oid is not None and int(oid) == m["side"]:
            out.append({"team": m["team"], "faced": [names.get(p, str(p)) for p in m["faced"]], "n": len(m["faced"])})
        else:
            out.append(None)
    return out


def team_core_meetings(mine, theirs, played, scores=None):
    """His current core's series against tonight's core that he did not play: [{date, team, opponent, maps_won, maps_lost, faced}],
    newest first. mine / theirs: core_matches of his teammates / of tonight's opponent; played: the HLTV match ids he played."""
    scores = scores or {}
    out = []
    for mid, a in (mine or {}).items():
        b = (theirs or {}).get(mid)
        if not b or b["side"] == a["side"] or mid in played:
            continue
        sc = scores.get(mid) or {}
        out.append({"date": a["date"], "team": a["team"], "opponent": b["team"], "hltv_match": mid,
                    "maps_won": sc.get(a["side"]), "maps_lost": sc.get(b["side"]), "faced": b.get("faced_names") or b["faced"]})
    out.sort(key=lambda m: (m["date"], m["hltv_match"]), reverse=True)
    return out


# ---------------------------------------------------------------- HLTV reads
_LINEUP_SQL = """
SELECT ps.playerid, count(*) FROM player_stats ps
WHERE ps.sideid = 0 AND ps.teamid = %(tid)s AND ps.matchid = (
    SELECT m.matchid FROM matches m JOIN match_overview mo USING (matchid)
    WHERE m.teamid = %(tid)s AND m.sideid = 0 AND mo.date >= %(fresh)s
    ORDER BY mo.date DESC, m.matchid DESC LIMIT 1)
GROUP BY ps.playerid
"""
_CORE_SQL = """
SELECT ps.matchid, ps.teamid, t.name, mo.date, array_agg(DISTINCT ps.playerid)
FROM player_stats ps JOIN match_overview mo USING (matchid) JOIN teams t ON t.teamid = ps.teamid
WHERE ps.playerid = ANY(%(ids)s) AND ps.sideid = 0 AND mo.date >= %(since)s
GROUP BY ps.matchid, ps.teamid, t.name, mo.date
HAVING count(DISTINCT ps.playerid) >= %(k)s
"""


def _lineup_of(cur, tid, today=None):
    """The five of the team's newest HLTV series within LINEUP_DAYS: an org whose last series is older (a new roster that has not
    played under it yet, an inactive team) has no current lineup here, so no roster-core H2H (never an old roster's)."""
    fresh = ((today or date.today()) - timedelta(days=LINEUP_DAYS)).isoformat()
    cur.execute(_LINEUP_SQL, {"tid": tid, "fresh": fresh})
    return current_lineup(cur.fetchall())


def _names(cur, ids):
    cur.execute("SELECT playerid, name FROM players WHERE playerid = ANY(%(ids)s)", {"ids": list(ids)})
    return {int(p): n for p, n in cur.fetchall()}


def _core_of(cur, ids, since):
    cur.execute(_CORE_SQL, {"ids": list(ids), "since": since, "k": MIN_CORE})
    return core_matches(cur.fetchall(), ids)


def read_opponent(cur, opponent, today=None):
    """{"team_id", "lineup": [{id, name}], "names", "matches"} for tonight's opponent, or None when HLTV has no exact team / lineup."""
    import hltv
    tid = hltv.resolve_team_id(cur, opponent)
    if tid is None:
        return None
    ids = _lineup_of(cur, tid, today)
    if not ids:
        return None
    since = ((today or date.today()) - timedelta(days=DAYS)).isoformat()
    names = _names(cur, ids)
    return {"team_id": int(tid), "lineup": [{"id": p, "name": names.get(p)} for p in ids], "names": names,
            "matches": _core_of(cur, ids, since)}


def read_team_core(cur, nick, team, opp_core, today=None):
    """His current core's meetings with tonight's core that he did not play (team_core_meetings), [] when there are none."""
    import hltv
    if not opp_core or not opp_core.get("matches"):
        return []
    pids = hltv._player_ids(cur, nick, team)
    if not pids:
        return []
    pid = int(pids[0])
    since = ((today or date.today()) - timedelta(days=DAYS)).isoformat()
    cur.execute("""SELECT ps.teamid FROM player_stats ps JOIN match_overview mo USING (matchid)
                   WHERE ps.playerid = %(p)s AND ps.sideid = 0 ORDER BY mo.date DESC, ps.matchid DESC LIMIT 1""", {"p": pid})
    r = cur.fetchone()
    if not r:
        return []
    mates = [p for p in (_lineup_of(cur, r[0], today) or []) if p != pid]
    if len(mates) < MIN_CORE:
        return []
    mine = _core_of(cur, mates, since)
    cur.execute("""SELECT DISTINCT ps.matchid FROM player_stats ps JOIN match_overview mo USING (matchid)
                   WHERE ps.playerid = %(p)s AND mo.date >= %(since)s""", {"p": pid, "since": since})
    played = {int(x[0]) for x in cur.fetchall()}
    theirs = opp_core["matches"]
    both = [m for m, a in mine.items() if m in theirs and theirs[m]["side"] != a["side"] and m not in played]
    scores = {}
    if both:
        cur.execute("""SELECT matchid, teamid, mapid, score FROM matches WHERE matchid = ANY(%(m)s) AND sideid = 0 AND mapid <> 0""",
                    {"m": both})          # mapid 0 is HLTV's "All" row (the series), not a map
        maps = {}
        for mid, tid, mapid, sc in cur.fetchall():
            maps.setdefault((int(mid), int(mapid)), {})[int(tid)] = sc or 0
        for (mid, _), s in maps.items():
            if len(s) == 2:
                (ta, sa), (tb, sb) = s.items()
                won = ta if sa > sb else tb if sb > sa else None
                d = scores.setdefault(mid, {ta: 0, tb: 0})
                if won is not None:
                    d[won] = d.get(won, 0) + 1
    names = opp_core.get("names") or {}
    theirs_named = {m: dict(v, faced_names=[names.get(p, str(p)) for p in v["faced"]]) for m, v in theirs.items()}
    return team_core_meetings(mine, theirs_named, played, scores)


# ---------------------------------------------------------------- cache (single-flight, last good kept, failures never cached)
_LOCK = threading.Lock()
_CACHE = {}        # key -> (at, value, ok)
_INFLIGHT = {}
MAX_KEYS = 2000


def _norm(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _cached(key, read, ttl=CORE_TTL, wait=SLOT_WAIT):
    """read() under single-flight; a fresh answer within ttl; on a failure the last good answer (or None), retried in FAIL_RETRY."""
    return _cached_state(key, read, ttl, wait)[0]


def _cached_state(key, read, ttl=CORE_TTL, wait=SLOT_WAIT):
    """(answer, known): known is False while no read of this key has ever succeeded (failed, no free slot, still running) - the
    answer None then means 'not read', never 'no core'."""
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < (ttl if hit[2] else FAIL_RETRY):
            return hit[1], hit[3]
        ev = _INFLIGHT.get(key)
        leader = ev is None
        if leader:
            ev = _INFLIGHT[key] = threading.Event()
    if not leader:
        ev.wait(wait + 6)
        with _LOCK:
            hit = _CACHE.get(key)
        return (hit[1], hit[3]) if hit else (None, False)
    val, ok = None, False
    try:
        if SLOTS.acquire(timeout=wait):
            try:
                val, ok = read(), True
            finally:
                SLOTS.release()
    except Exception:
        val, ok = None, False
    with _LOCK:
        prev = _CACHE.get(key)
        known = ok or bool(prev and prev[3])
        if not ok:
            val = prev[1] if prev else None          # never a cached failure: the last good answer, retried soon
        if len(_CACHE) > MAX_KEYS:
            _CACHE.clear()
        _CACHE[key] = (time.time(), val, ok, known)
        _INFLIGHT.pop(key, None)
    ev.set()
    return val, known


def _with_cursor(fn):
    import hltv
    if not hltv.dsn():
        return None
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            return fn(cur)
    finally:
        con.close()


def opponent_core_state(opponent, peek=False):
    """(core, known) for tonight's opponent (read_opponent), cached. known False = not read yet / the read failed: the caller must
    not present the exact-name meetings as the answer (the board leaves the cell for a later pass). peek=True: memory only."""
    if not _norm(opponent):
        return None, True
    key = ("opp", _norm(opponent), date.today().isoformat())
    if peek:
        with _LOCK:
            hit = _CACHE.get(key)
        return (hit[1], hit[3]) if hit else (None, False)
    return _cached_state(key, lambda: _with_cursor(lambda cur: read_opponent(cur, opponent)))


def opponent_core(opponent):
    """Tonight's opponent's roster core (read_opponent), cached; None when there is none or it could not be read."""
    return opponent_core_state(opponent)[0]


def peek_core(opponent):
    """opponent_core from memory only (no read): a board build past its budget."""
    return opponent_core_state(opponent, peek=True)[0]


def team_core(nick, team, opponent):
    """His current core's meetings with tonight's core without him (read_team_core), cached; [] when none / unknown."""
    oc = opponent_core(opponent)
    if not oc or not _norm(nick):
        return []
    return _cached(("team", _norm(nick), _norm(team), _norm(opponent), date.today().isoformat()),
                   lambda: _with_cursor(lambda cur: read_team_core(cur, nick, team, oc))) or []


def public_core(core):
    """The part of the core a card shows: tonight's five (names) and where the lineup came from."""
    if not core:
        return None
    return {"players": [p.get("name") or str(p.get("id")) for p in core.get("lineup") or []], "min": MIN_CORE}
