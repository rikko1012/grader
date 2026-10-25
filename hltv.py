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

import hltv_store

WINDOWS = (3, 6, 12)      # months, like the BO3.gg waterfall: first window with enough maps wins
MIN_MAPS = 10             # maps needed before a window is used
MIN_SWING_MAPS = 10       # maps with a round swing value before it feeds the grade
STALE_DAYS = 10           # newest HLTV map this much older than BO3.gg's = warning
TTL = 15 * 60
BREAKER_SECONDS = 5 * 60  # after a connection failure, skip the database this long ...
PROBE_SECONDS = 20        # ... except one probe connection this often (half-open): a blip after a restart used to cost
                          # five minutes of every grade on the old pipeline (2026-10-11 saadzin: Under on one board, Over on the other)
_CACHE, _LOCK = {}, threading.Lock()
_DOWN_UNTIL = [0.0]
_LAST_PROBE = [0.0]
FAIL_TTL = 30             # a lookup that FAILED (database unreachable / timed out) is retried after this long; the previous good
                          # value is served meanwhile - never 15-60 min of 'no data' from one blip (2026-10-11 audit)
POOL_MAX = 16             # pooled connections (Render Postgres allows ~100): 6 fast + 3 cold member slots + 3 board workers + warm start
POOL_WAIT = 8             # seconds a lookup waits for a free pooled connection before giving up as 'busy'
IDLE_MAX = 600            # an idle pooled connection older than this is reopened
CONNECT_TIMEOUT = 8       # seconds (the public Render URL handshakes in 1-3 s; 4 s tripped the breaker on a slow one)
_STATE = {"host": None, "last_error": None, "last_error_at": 0.0, "plain_connect": False, "probing": False, "no_user_timeout": False}
_TAINTED = set()          # id(real connection) whose statement_timeout was changed - reset when handed back


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


def is_transient(e):
    """True when the database was unreachable, timed out or busy (connection refused / dropped, statement timeout, pool
    busy, the breaker) - as opposed to 'player not found'. Matched on the class hierarchy, so psycopg2 subclasses
    (AdminShutdown, QueryCanceled, ...) count. A transient failure must never become a different projection."""
    names = {c.__name__ for c in type(e).__mro__}
    if names & {"Unavailable", "OperationalError", "InterfaceError", "QueryCanceled", "TimeoutError", "ConnectionError"}:
        return True
    msg = str(e).lower()
    return "timeout" in msg or "timed out" in msg or "connection" in msg


def err_text(e):
    """Error text safe for members: a transient failure is just 'HLTV database unreachable' (no host, port or driver chatter)."""
    return f"{type(e).__name__}: HLTV database unreachable" if is_transient(e) else f"{type(e).__name__}: lookup failed"


_META = {}                # key -> {"state": live | memory copy | stored copy | failed, "as_of": when the data was read, "n"}


def _remember(key, value, ok=True, ttl=None):
    """Cache a lookup result. ok=False (the lookup FAILED): the previous good value stays and is served meanwhile, and the
    lookup is tried again after FAIL_TTL seconds - with no previous value the failure itself is kept only FAIL_TTL.
    Returns what is now cached. (2026-10-11 audit: one blip used to be remembered as 'no data' for 15-60 minutes.)
    read_status(key) says which it was (2026-10-13 audit: a served copy carries its source and age)."""
    ttl = TTL if ttl is None else ttl
    if ok:
        with _LOCK:
            if len(_CACHE) > 5000:
                _CACHE.clear()
                _META.clear()
            _CACHE[key] = (time.time(), value, True)
            _META[key] = {"state": "live", "as_of": time.time(), "n": _size(value)}
        hltv_store.save(key, value)            # kept on the data disk: an outage (or a restart during one) uses it
        return value
    with _LOCK:
        prev, meta = _CACHE.get(key), dict(_META.get(key) or {})
    good = prev if prev and (len(prev) < 3 or prev[2]) else None
    state = (meta.get("state") if meta.get("state") == "stored copy" else "memory copy") if good else "failed"
    as_of = meta.get("as_of") if good else None
    if good is None:                           # nothing in memory (restart, first look-up): the last good answer on disk
        stored = hltv_store.load(key)
        if stored is not None:
            good = (stored[0], stored[1], True)
            state, as_of = "stored copy", stored[0]
    keep = good[1] if good else value
    with _LOCK:
        if len(_CACHE) > 5000:
            _CACHE.clear()
            _META.clear()
        _CACHE[key] = (time.time() - ttl + FAIL_TTL, keep, bool(good))
        _META[key] = {"state": state, "as_of": as_of, "n": _size(keep) if good else None}
    return keep


def _size(v):
    return len(v) if isinstance(v, (list, tuple)) else None


def read_status(key):
    """How the cached answer under `key` was obtained: {"state": "live" (read from the database) | "memory copy" / "stored
    copy" (the last good answer of the same query, served because the database failed) | "failed" (no good answer
    anywhere), "as_of": ISO time the data was read, "age_min", "n": rows}. None = never read (or not configured)."""
    with _LOCK:
        m = dict(_META.get(key) or {})
    if not m:
        return None
    at = m.pop("as_of", None)
    m["as_of"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)) if at else None
    m["age_min"] = round((time.time() - at) / 60, 1) if at else None
    return m


FLIGHT_WAIT = POOL_WAIT + CONNECT_TIMEOUT + 6   # longest one read can take: pool wait, connect, 5 s statement timeout
CONTEXT_FLIGHT_WAIT = POOL_WAIT + CONNECT_TIMEOUT + 14 * 6   # context(): identity lookups + 10 sections on one connection
_FLIGHTS = {}
MISS = object()


class _Flight(threading.Event):
    outcome = None                             # ("ok", value) | ("err", exception) - the leader's own result


def single_flight(key, read, peek, wait=None):
    """One database read per key at a time, shared (2026-10-13 audit: concurrent grades of one player each ran the same
    failing query - 200 waiting grades = 200 database calls). The first caller runs read(); callers arriving meanwhile wait
    for it (FLIGHT_WAIT s at most, or `wait`) and take peek() - its answer, the last good one or the failure kept FAIL_TTL -
    never a read of their own. With the FAIL_TTL failure cache: one retry per key per FAIL_TTL seconds, whatever the load.
    2026-10-21 audit: when the leader's read raised (or cached nothing), the waiting callers get the leader's own result /
    error instead of each running read() themselves; only a leader slower than the wait lets a caller read on its own."""
    with _LOCK:
        ev = _FLIGHTS.get(key)
        lead = ev is None
        if lead:
            ev = _FLIGHTS[key] = _Flight()
    if not lead:
        done = ev.wait(FLIGHT_WAIT if wait is None else wait)
        got = peek()
        if got is not MISS:
            return got
        if done and ev.outcome is not None:
            kind, val = ev.outcome
            if kind == "err":
                raise val
            return val
    try:
        val = read()
        if lead:
            ev.outcome = ("ok", val)
        return val
    except Exception as e:
        if lead:
            ev.outcome = ("err", e)
        raise
    finally:
        if lead:
            with _LOCK:
                _FLIGHTS.pop(key, None)
            ev.set()


def _peek(key, ttl=None):
    """single_flight's peek for _remember-cached lookups."""
    with _LOCK:
        hit = _CACHE.get(key)
    return hit[1] if hit and time.time() - hit[0] < (TTL if ttl is None else ttl) else MISS


_LAST_FORCED = [0.0]


def allow_probe():
    """May this caller retry against the database right now? With the breaker off: yes. With it on: only one forced probe at a
    time and at most one per 2 s - every other caller gets False and must not sleep or wait (700 members retrying at once make
    one connection attempt, not 700, and member slots are not tied up)."""
    with _LOCK:
        if time.time() >= _DOWN_UNTIL[0]:
            return True
        if _STATE["probing"] or time.time() - _LAST_FORCED[0] < 2:
            return False
        _LAST_FORCED[0] = time.time()
        _LAST_PROBE[0] = 0.0
        return True


def breaker_on():
    return time.time() < _DOWN_UNTIL[0]


def _host():
    from urllib.parse import urlsplit
    try:
        return urlsplit(dsn()).hostname
    except ValueError:
        return None


def _cursor_factory():
    """Cursors that notice a session-level SET statement_timeout, so the pool can reset it before reuse."""
    try:
        import psycopg2.extensions
    except Exception:
        return None

    class _Cursor(psycopg2.extensions.cursor):
        def execute(self, query, vars=None):
            q = query if isinstance(query, str) else str(query)
            if q.lstrip()[:21].lower().startswith("set statement_timeout"):
                _TAINTED.add(id(self.connection))
            t0 = time.time()
            try:
                return super().execute(query, vars)
            except Exception as e:
                if "statement timeout" in str(e).lower() or type(e).__name__ == "QueryCanceled":
                    _STATE["timeouts"] = _STATE.get("timeouts", 0) + 1     # admin status: slow queries, not outages
                    _STATE["last_timeout_at"] = time.time()
                    _STATE["last_timeout_query"] = q.strip()[:80]
                raise
            finally:
                _STATE["slowest_ms"] = max(_STATE.get("slowest_ms", 0), round((time.time() - t0) * 1000))
    return _Cursor


def _raw_connect():
    """One real connection (breaker + half-open probe). _connect() pools these."""
    now = time.time()
    probing = False
    if now < _DOWN_UNTIL[0]:
        with _LOCK:
            probing = now - _LAST_PROBE[0] >= PROBE_SECONDS and not _STATE["probing"]
            if probing:
                _LAST_PROBE[0] = now      # this caller probes the database; everyone else keeps skipping
                _STATE["probing"] = True
        if not probing:
            raise Unavailable("HLTV database unreachable a moment ago - skipped (retries in a few seconds)")
    try:
        return _raw_connect_now()
    finally:
        if probing:
            _STATE["probing"] = False


def _raw_connect_now():
    import psycopg2   # only needed when the HLTV database is configured

    kw = {"connect_timeout": CONNECT_TIMEOUT, "application_name": "cs2-scout", "keepalives": 1, "keepalives_idle": 30,
          "keepalives_interval": 10, "keepalives_count": 3}
    if not _STATE["no_user_timeout"]:
        kw["tcp_user_timeout"] = 10000      # a server that vanished mid-query fails the client in 10 s, not minutes
    cf = _cursor_factory()
    if cf is not None:
        kw["cursor_factory"] = cf
    _STATE["host"] = _host()
    con = None
    try:
        plain = _STATE["plain_connect"]
        try:
            if plain:
                con = psycopg2.connect(dsn(), **kw)
            else:
                try:
                    con = psycopg2.connect(dsn(), options="-c statement_timeout=5000", **kw)
                except psycopg2.OperationalError as e:
                    # a pooled endpoint (Neon's PgBouncer, 2026-10-11) refuses startup options: connect plainly and set the
                    # statement timeout per session instead - remembered, not re-detected on every connection
                    if "unsupported startup parameter" not in str(e):
                        raise
                    _STATE["plain_connect"] = plain = True
                    con = psycopg2.connect(dsn(), **kw)
        except psycopg2.OperationalError as e:
            if "tcp_user_timeout" not in str(e) or _STATE["no_user_timeout"]:
                raise
            _STATE["no_user_timeout"] = True     # an old libpq: remembered, connect without it
            kw.pop("tcp_user_timeout", None)
            con = psycopg2.connect(dsn(), **({"options": "-c statement_timeout=5000"} if not plain else {}), **kw)
        con.set_session(readonly=True, autocommit=True)
        if plain:
            with con.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
            _TAINTED.discard(id(con))
    except Exception as e:
        if con is not None:                    # a failure after the socket opened: never leak the real connection
            try:
                con.close()
            except Exception:
                pass
        _DOWN_UNTIL[0] = time.time() + BREAKER_SECONDS
        _LAST_PROBE[0] = time.time()          # the failed attempt is the last probe
        _STATE.update(last_error=f"{type(e).__name__}: {str(e)[:200]}", last_error_at=time.time())   # admin-only (status())
        raise
    _DOWN_UNTIL[0] = 0.0                  # reachable again: the breaker is off
    return con


class _Pooled:
    """A pooled connection as the call sites use it: `con = hltv._connect() ... con.close()`. close() hands the real
    connection back to the pool (or drops it when it broke)."""
    __slots__ = ("raw",)

    def __init__(self, raw):
        self.raw = raw

    def cursor(self, *a, **k):
        return self.raw.cursor(*a, **k)

    def set_session(self, **k):
        pass                                   # set once per real connection (_raw_connect)

    def close(self):
        raw, self.raw = self.raw, None
        if raw is None:
            return
        try:
            bad = bool(raw.closed) or raw.get_transaction_status() != 0   # 0 = idle; anything else = broken mid-query
        except Exception:
            bad = True
        _CONNS.put(raw, bad=bad)

    def __getattr__(self, k):
        return getattr(self.raw, k)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _Pool:
    """At most POOL_MAX real connections, reused (LIFO). One TLS handshake per pooled connection instead of one per
    lookup (2026-10-11 audit: hundreds of fresh connections at start-up tripped the breaker on the public Render URL)."""

    def __init__(self):
        self.idle, self.n, self.cv = [], 0, threading.Condition()

    def get(self):
        deadline = time.time() + POOL_WAIT
        while True:
            with self.cv:
                while not self.idle and self.n >= POOL_MAX:
                    left = deadline - time.time()
                    if left <= 0:
                        raise Unavailable(f"HLTV database busy - all {POOL_MAX} pooled connections in use")
                    self.cv.wait(left)
                if not self.idle:
                    self.n += 1                # a new connection (counted now, opened below)
                    break
                raw, since = self.idle.pop()   # still counted in n: checked out, so the pool never over-opens
            # the liveness check runs OUTSIDE the lock (2026-10-21 audit: a SELECT 1 on a silently dead path blocked every
            # get(), put() and /health for up to tcp_user_timeout per idle connection)
            age = time.time() - since
            if age < IDLE_MAX and not getattr(raw, "closed", False):
                if age < 30 or self._alive(raw):   # a quiet spell: make sure the server kept it
                    return raw
            try:                               # too old / closed by the server: reopen
                raw.close()
            except Exception:
                pass
            with self.cv:
                self.n -= 1
                self.cv.notify()
        try:
            return _raw_connect()
        except Exception:
            with self.cv:
                self.n -= 1
                self.cv.notify()
            raise

    @staticmethod
    def _alive(raw):
        try:
            with raw.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            return False

    def put(self, raw, bad=False):
        if not bad and id(raw) in _TAINTED:    # a long admin query changed the session timeout: back to 5 s
            try:
                with raw.cursor() as cur:
                    cur.execute("SET statement_timeout = 5000")
            except Exception:
                bad = True
            _TAINTED.discard(id(raw))          # after the reset (the reset itself goes through the tainting cursor)
        if bad:
            _TAINTED.discard(id(raw))
            try:
                raw.close()
            except Exception:
                pass
        with self.cv:
            if bad:
                self.n -= 1
            else:
                self.idle.append((raw, time.time()))
            self.cv.notify()

    def stats(self):
        with self.cv:
            return {"open": self.n, "idle": len(self.idle), "max": POOL_MAX}


_CONNS = _Pool()


def _connect():
    """A pooled read-only autocommit connection with a 5 s statement timeout. Raises Unavailable (breaker on / pool busy)
    or the driver's own error; both count as transient (is_transient)."""
    return _Pooled(_CONNS.get())


def public_status():
    """For /health (unauthenticated): only whether the database answers and how busy the pool is."""
    return {"configured": bool(dsn()), "breaker_on": time.time() < _DOWN_UNTIL[0], "pool": _CONNS.stats()}


def status():
    """Admin status: where the HLTV database is (host name only - never the credentials), the breaker, the last error, the pool."""
    host = _STATE["host"] or _host() or ""
    return {"configured": bool(dsn()), "host": host, "breaker_on": time.time() < _DOWN_UNTIL[0],
            # Render: the Internal Database URL (host without a domain, private network) is the Railway-like setup; the public
            # ".render.com" URL goes through TLS + a proxy and is where the 2026-10-11 connection failures happened
            "public_url": host.endswith(".render.com") or host.endswith(".neon.tech"),
            "last_error": _STATE["last_error"], "last_error_s_ago": round(time.time() - _STATE["last_error_at"]) if _STATE["last_error_at"] else None,
            "pool": _CONNS.stats(), "plain_connect": _STATE["plain_connect"], "cache_entries": len(_CACHE),
            "statement_timeouts": _STATE.get("timeouts", 0), "last_timeout_s_ago": round(time.time() - _STATE["last_timeout_at"]) if _STATE.get("last_timeout_at") else None,
            "last_timeout_query": _STATE.get("last_timeout_query"), "slowest_query_ms": _STATE.get("slowest_ms", 0),
            "stored_copy": hltv_store.report()}


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
WHERE ps.playerid IN (SELECT playerid FROM players WHERE lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) = ANY(%(nicks)s))
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

    def read():
        try:
            result = _player_numbers(nick, team, bo3_last_date)
        except Exception as e:  # database down, schema not there yet, ... - never block a grade
            result = {"found": False, "error": err_text(e), "transient": is_transient(e)}
        return _remember(key, result, ok=not result.get("error"))
    return single_flight(key, read, lambda: _peek(key))


def _player_numbers(nick, team, bo3_last_date):
    from datetime import date, timedelta

    con = _connect()
    try:
        with con.cursor() as cur:
            try:
                cur.execute(_SQL, {"nicks": player_nicks(nick)})
            except Exception as e:
                if "source" not in str(e):
                    raise
                # database from an older scraper version (before the source column)
                cur.execute(_SQL.replace("mo.source", "'hltv'"), {"nicks": player_nicks(nick)})
            rows = cur.fetchall()
    finally:
        con.close()
    if not rows:
        return {"found": False, "note": f"{nick} has no HLTV maps in the last 12 months"}

    # Several HLTV players can share a nickname: prefer the one who played for this team.
    by_player = {}
    for r in rows:
        by_player.setdefault(r[0], []).append(r)
    tv = set(team_name_variants(team)) if team else set()     # exact variants: 'Chicken Coop Esports' = 'Chicken Coop'
    own = _norm(nick)                                          # an alias-only candidate (PLAYER_ALIASES) needs this team
    by_player = {pid: rs for pid, rs in by_player.items() if _norm(rs[0][1]) == own or tv & {_norm(r[2]) for r in rs}}
    if not by_player:
        return {"found": False, "note": f"{nick} has no HLTV maps in the last 12 months"}

    def score(pid):
        teams = {_norm(r[2]) for r in by_player[pid]}
        return (bool(tv & teams), len(by_player[pid]))
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
    if team and not (tv & {_norm(r[2]) for r in total}):
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
                    .replace("mo.source", "'hltv'"), {"nicks": [_norm(nick)], "cut": cut.isoformat()})
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
    tv = set(team_name_variants(team)) if team else set()
    pid = max(by, key=lambda k: (bool(tv & {_norm(r[2]) for r in by[k]}), len(by[k])))
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
    rows, ok = [], True
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
        ok = False
    out = _remember("schedule", rows, ok, ttl=SCHEDULE_TTL)
    _STATE["schedule_failed"] = lookup_failed("schedule")   # a context built now may have missed an HLTV-only match (cs2_scout)
    return out


# ---------------------------------------------------------------- display-only context
def _map_key(name):
    return _norm(str(name or "").lower().removeprefix("de_"))


def lookup_failed(key):
    """True when the cached value under `key` is a failure being served for FAIL_TTL (no earlier good value)."""
    with _LOCK:
        hit = _CACHE.get(key)
    return bool(hit) and len(hit) > 2 and not hit[2]


def context(nick, team=None, opponent=None, maps=(), bo3_last_date=None, fresh=False, start=None, playing_for=None):
    """Everything else the scraper has for this grade, for display. Never raises; each section is
    None when it can't be computed. Cached 15 minutes. start / playing_for: the graded match's start and the team he plays
    it for - the schedule sections (next match, tonight's LAN / online) read that team's schedule, and tonight's label is
    the graded match's own (its opponent and start), never the team's next match against someone else."""
    if not dsn():
        return {"found": False, "note": "HLTV database not configured (HLTV_DB_DSN)"}
    maps = [m for m in (maps or []) if m]
    sched = playing_for or team
    key = ("ctx", _norm(nick), _norm(team), _norm(opponent), tuple(_map_key(m) for m in maps))
    if start:
        key += (str(start)[:16],)
    if _norm(sched) != _norm(team):
        key += ("for", _norm(sched))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL and not fresh:   # fresh: a retry must not read the cached failure back
            return hit[1]
    return single_flight(key, lambda: _context_read(key, nick, team, opponent, maps, sched, start), lambda: _peek(key),
                         wait=CONTEXT_FLIGHT_WAIT)


def _context_read(key, nick, team, opponent, maps, sched, start):
    try:
        con = _connect()
    except Exception as e:                   # the previous good context (if any) is served; the failure itself only FAIL_TTL s
        return _remember(key, {"found": False, "error": err_text(e), "transient": is_transient(e)}, ok=False)
    out, errors = {"found": True}, {}
    try:
        with con.cursor() as cur:
            try:                             # 2026-10-21 audit: a failed identity lookup is a failed read like the connect
                ids = _player_ids(cur, nick, team)
                team_id = canonical_team_id(cur, team) if team else None
                opp_id = canonical_team_id(cur, opponent) if opponent else None
            except Exception as e:           # (the previous good context is served), never an exception past single_flight
                return _remember(key, {"found": False, "error": err_text(e), "transient": is_transient(e)}, ok=False)
            sections = {
                "prop_maps": lambda: _prop_maps(cur, ids, maps),
                "eco": lambda: _eco(cur, ids),
                "last5": lambda: _last5(cur, ids),
                "h2h": lambda: _h2h(cur, team_id, opp_id, maps),
                "ranking": lambda: _ranking(cur, team_id),
                "opp_ranking": lambda: _ranking(cur, opp_id),
                "rank_band": lambda: _rank_band(cur, ids, opp_id),
                "next_match": lambda: _next_match(cur, sched),
                "environment": lambda: _environment(cur, ids, sched, opponent, start),
                "backup_source": lambda: _backup(cur, ids),
            }
            for name, fn in sections.items():
                if out.get("transient"):             # the database is timing out: the rest would hold this connection 5 s each
                    out[name] = None
                    continue
                try:
                    out[name] = fn()
                except Exception as e:   # one section failing never affects the others
                    out[name] = None
                    errors[name] = err_text(e)
                    if is_transient(e):
                        out["transient"] = True   # unreachable / timed out: retried in FAIL_TTL s, not remembered for TTL
    finally:
        con.close()
    if errors:
        out["errors"] = errors
    return _remember(key, out, ok=not out.get("transient"))


_NO_TID = object()


# Verified player-name aliases: BO3.gg / board nick (normalised) -> the HLTV nick (normalised) of the SAME player. Exact
# names only, never fuzzy. An alias candidate is used only when HLTV shows him playing for the graded team (so a BO3.gg
# namesake on another team never gets this player's history); the player's own nick is always looked up as well.
# 2026-10-23 accuracy audit I3 (proof: same match, same team, HLTV scoreboard totals = the settled BO3.gg totals):
#   Techno4K = HLTV 'Techno' (id 20275, The MongolZ), brain = HLTV 'Brain47' (id 23294, Phantom Academy; HLTV 'brain'
#   5615 is a different player with no maps since 2024). dev1ce / device is NOT here (owner decision pending).
PLAYER_ALIASES = {"techno4k": "techno", "brain": "brain47"}


def player_nicks(nick):
    """[the normalised nick, then its verified HLTV alias if any]."""
    n = _norm(nick)
    a = PLAYER_ALIASES.get(n)
    return [n] + ([a] if a and a != n else [])


def _player_ids(cur, nick, team, team_id=_NO_TID):
    """The HLTV id of this nick (namesakes: the one who played for `team`). 2026-10-21 audit: 'played for the team' is the
    team's canonical HLTV id (exact name, verified alias, affix variant - canonical_team_id) or an exact name variant, and
    among unconfirmed candidates the most maps in the last 12 months decides (player_numbers' window; all-time rows only
    break a tie) - an alias spelling ('DENDELE CS') no longer hands the projection a veteran namesake with more old maps."""
    tid = (resolve_team_id(cur, team) if team else None) if team_id is _NO_TID else team_id
    nicks = player_nicks(nick)
    cur.execute("""SELECT ps.playerid, bool_or(lower(regexp_replace(t.name, '[^a-zA-Z0-9]', '', 'g')) = ANY(%(teams)s)
                                               OR ps.teamid = %(tid)s),
                          count(*) FILTER (WHERE mo.date >= current_date - interval '12 months'), count(*),
                          bool_or(lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = %(nick)s)
                   FROM players p JOIN player_stats ps USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                   LEFT JOIN match_overview mo ON mo.matchid = ps.matchid
                   WHERE lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = ANY(%(nicks)s)
                   GROUP BY ps.playerid""", {"nick": nicks[0], "nicks": nicks, "teams": team_name_variants(team) or [""],
                                             "tid": tid if tid is not None else -1})
    # an alias-only candidate (PLAYER_ALIASES) counts only when he played for this team
    rows = [r[:4] for r in cur.fetchall() if len(r) < 5 or r[4] or r[1]]
    if not rows:
        return []
    best = max(rows, key=lambda r: (bool(r[1]),) + tuple(r[2:]))   # player_numbers' rule: on the team, then 12-month maps
    return [best[0]]


# the same org affixes as matchid._AFFIX (2026-10-14 audit: one team identity rule - BO3.gg's 'FaZe Clan' is HLTV 'FaZe')
_TEAM_AFFIX = re.compile(r"^(team)\s+|\s+(esports|e-sports|esport|e-sport|gaming|club|clan|team)$", re.I)


def team_key(name):
    """A team name's canonical identity: matchid.team_key - the HLTV team id via the exact name, a verified alias
    (team_aliases.json / comparison.VERIFIED_TEAM_ALIASES) or an org-affix variant - once its lookup tables are loaded. None
    when unknown, ambiguous (two HLTV teams) or not loaded yet. Memory only."""
    try:
        import matchid
        return matchid.team_key(name) if name and matchid.ready() else None
    except Exception:
        return None


def same_team(a, b):
    """Two team names are the same team. Canonical identity first: when both names resolve (team_key), the ids decide - a
    renamed team or another spelling through its verified alias is the same team, two different HLTV teams never are, even
    with a shared name variant (2026-10-13 audit: 'Nova Esports' / 'Nova Gaming'). Otherwise the exact name variants
    (team_name_variants). Never a substring - 'Inner Circle' is not 'Inner Circle Academy', 'NAVI' is not 'NAVI Junior',
    'MOUZ' is not 'MOUZ NXT' (settlement, 2026-10-11 audit)."""
    if not a or not b:
        return False
    ka, kb = team_key(a), team_key(b)
    if ka and kb:
        return ka == kb
    va, vb = set(team_name_variants(a)) or {_norm(a)}, set(team_name_variants(b)) or {_norm(b)}   # 1-letter names: as written
    return bool(va & vb - {""})


def _opp_rows(nick, team, rows, opponent):
    """opponent_rows with its own short-lived connection (settle helpers have closed theirs)."""
    if not opponent or not rows:
        return list(rows)
    try:
        con = _connect()
        try:
            with con.cursor() as cur:
                return opponent_rows(cur, rows, opponent)[0]
        finally:
            con.close()
    except Exception:
        return [r for r in rows if same_team(opponent, r["opponent"])]


def resolve_team_id(cur, name):
    """The HLTV team id for a book / pick team name (canonical_team_id: exact name, verified alias, org-affix variants).
    None when it can't be resolved exactly."""
    if not name:
        return None
    try:
        return canonical_team_id(cur, name)
    except Exception:              # a failed lookup is no identity: the caller falls back to exact names
        return alias_team_id(name)


def canonical_team_id(cur, name):
    """The HLTV team id of a team name in the order matchid.team_key resolves: the exact name, then a verified alias
    (team_aliases.json - an alias beats an affix-stripped guess: 'Apogee Esports' is Betclic, not a team called Apogee), then
    the org-affix variants. None when unresolved. A database failure raises (never 'team not in HLTV' for a blip)."""
    vs = team_name_variants(name)
    if not vs:
        return None
    # a normalised variant has no spaces, so it is looked up as it is (exactly). Two HLTV teams under one name = unresolved,
    # as in matchid._resolve (2026-10-21 audit: the first row of an unordered scan was taken)
    tid = _team_lookup(cur, vs[0])
    if tid is not None:
        return None if tid is _AMBIGUOUS else tid
    tid = alias_team_id(name)
    if tid:
        return tid
    for v in vs[1:]:
        tid = _team_lookup(cur, v)
        if tid is not None:
            return None if tid is _AMBIGUOUS else tid
    return None


def alias_team_id(name):
    """The HLTV team id the maintained alias table (matchid / team_aliases.json) gives this exact name, else None. Memory
    only (no database read)."""
    try:
        import matchid
        k = matchid.team_key(name) if name and matchid.ready() else None
        if k and str(k).startswith("team:"):
            return int(str(k).split(":", 1)[1])
    except Exception:
        pass
    return None


def opponent_rows(cur, rows, opponent):
    """Series rows against `opponent`, by canonical identity (2026-10-12 owner review): the opponent's HLTV team id when it
    resolves - a renamed team keeps its id, an alias spelling resolves to the same id, an academy is a different id - and
    the exact-name rule only when it doesn't. Never a substring."""
    if not opponent:
        return list(rows), "no opponent"
    oid = resolve_team_id(cur, opponent)
    if oid is not None and any(r.get("opponent_id") is not None for r in rows):
        return [r for r in rows if r.get("opponent_id") == oid], "team id"
    return [r for r in rows if same_team(opponent, r["opponent"])], "name variants"


def team_name_variants(name):
    """Exact name first, then without a leading "Team" / trailing "Esports" / "Gaming" / "Club" (Underdog writes
    "Team Vitality", "Chicken Coop Esports"; HLTV has "Vitality", "Chicken Coop"). Exact matches only - never fuzzy."""
    out, cur = [], str(name or "").strip()
    while cur and _norm(cur) not in out:
        out.append(_norm(cur))
        nxt = _TEAM_AFFIX.sub("", cur).strip()
        if nxt == cur:
            break
        cur = nxt
    return [v for v in out if len(v) >= 2]


_AMBIGUOUS = object()


def _team_lookup(cur, v):
    """The HLTV team id whose normalised name is exactly `v`; _AMBIGUOUS when several teams share it; None when none."""
    cur.execute("SELECT min(teamid), count(DISTINCT teamid) FROM teams "
                "WHERE lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) = %s", (v,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _AMBIGUOUS if len(row) > 1 and (row[1] or 0) > 1 else row[0]


def _team_id(cur, name):
    for v in team_name_variants(name):                 # 2026-10-09: variants (11% of lines had no HLTV opponent)
        tid = _team_lookup(cur, v)
        if tid is not None:
            return None if tid is _AMBIGUOUS else tid  # two HLTV teams under this name: unresolved, never a guess
    return None


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


_LAST5_SQL = """SELECT mo.date, mp.name, t2.name, own.score, opp.score, ps.k, ps.d, ps.rating, mo.source, ps.matchid, mm.number
                   FROM player_stats ps
                   JOIN maps mp USING (mapid)
                   JOIN match_overview mo USING (matchid)
                   JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid
                                   AND own.mapid = ps.mapid AND own.sideid = 0
                   JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid
                                   AND opp.mapid = ps.mapid AND opp.sideid = 0
                   JOIN teams t2 ON t2.teamid = opp.teamid
                   LEFT JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                   WHERE ps.playerid = ANY(%s) AND ps.mapid <> 0 AND ps.sideid = 0
                   ORDER BY mo.date DESC, ps.matchid DESC, mm.number ASC NULLS LAST, mp.name LIMIT 5"""


def _last5(cur, ids):
    """His last 5 HLTV maps: newest series first, each series' maps in the order they were played (HLTV's map number -
    2026-10-11: one series' maps were listed out of order, e.g. Ancient, Nuke, Cache for Nuke 1, Ancient 2, Cache 3)."""
    if not ids:
        return None
    try:
        _execute(cur, _LAST5_SQL, (ids,))
    except Exception as e:
        if "match_maps" not in str(e) and "number" not in str(e):
            raise
        # an older scraper database without map numbers: same rows, played order unknown (number None)
        _execute(cur, "\n".join(l for l in _LAST5_SQL.split("\n") if "match_maps" not in l)
                 .replace("ps.matchid, mm.number", "ps.matchid, NULL").replace("mm.number ASC NULLS LAST, ", ""), (ids,))
    rows = cur.fetchall()
    rows.sort(key=lambda r: (-r[0].toordinal(), -(r[9] or 0), r[10] if r[10] is not None else 99))   # same order, in Python too
    return [{"date": r[0].isoformat(), "map": r[1], "opponent": r[2], "score": f"{r[3]}-{r[4]}",
             "kd": f"{r[5]}-{r[6]}", "rating": r[7], "backup": r[8] != "hltv", "match": r[9], "number": r[10]} for r in rows]


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


_INDEX = {"at": 0.0, "rows": [], "busy": False}
INDEX_FAIL_WAIT = 300     # a failed index load is retried after this long


def _load_index():
    """Every player with HLTV maps in the last 12 months: nick, latest team, maps (last 90 days / year)."""
    try:
        con = _connect()
        try:
            with con.cursor() as cur:
                cur.execute("SET statement_timeout = 60000")
                cur.execute("""SELECT p.name, t.name, mo.date, count(*) FILTER (WHERE mo.date >= current_date - 90),
                                      count(*)
                               FROM player_stats ps JOIN players p USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                               JOIN match_overview mo ON mo.matchid = ps.matchid
                               WHERE ps.sideid = 0 AND mo.date >= current_date - 365
                               GROUP BY p.playerid, p.name, t.name, mo.date""")
                best = {}
                for nick, team, d, recent, maps in cur.fetchall():
                    k = _norm(nick)
                    b = best.setdefault(k, {"nick": nick, "team": team, "last": d, "recent": 0, "maps": 0})
                    b["recent"] += recent
                    b["maps"] += maps
                    if d > b["last"]:
                        b.update(team=team, last=d, nick=nick)
        finally:
            con.close()
        rows = [dict(v, key=k, loose=_loose(k)) for k, v in best.items() if k]   # loose key once per load, not per search
        rows.sort(key=lambda r: (-r["recent"], -r["maps"]))
        if rows:
            _INDEX.update(at=time.time(), rows=rows)
            hltv_store.save(("index",), rows)
    except Exception:
        if not _INDEX["rows"]:                     # restart during an outage: the last good index from disk
            stored = hltv_store.load(("index",))
            if stored:
                _INDEX["rows"] = stored[1]
        # retried after INDEX_FAIL_WAIT whether or not rows are loaded (2026-10-21 audit: a 60 s query that timed out was
        # restarted by the very next search, back to back, holding a pooled connection)
        _INDEX["at"] = time.time() - 3600 + INDEX_FAIL_WAIT
    finally:
        _INDEX["busy"] = False


_LOOSE = str.maketrans("013457", "oieast")


def _loose(s):
    """Typos members make: 'monesy' = 'm0NESY', 'simple' = 's1mple'."""
    return _norm(s).translate(_LOOSE)


def team_of(nick):
    """His latest team in HLTV's player list (index), or None."""
    k = _norm(nick)
    return next((r["team"] for r in _INDEX["rows"] if r["key"] == k), None)


def canonical_nick(q):
    """The real HLTV nick for a typed name (exact, then loose match; most active player first)."""
    rows = _INDEX["rows"]
    k, lk = _norm(q), _loose(q)
    hit = next((r for r in rows if r["key"] == k), None) or next((r for r in rows if _loose(r["key"]) == lk), None)
    return hit["nick"] if hit else None


_CLOSE = {"rows": None, "keys": {}}


def suggest_nicks(q, limit=5):
    """'Did you mean ...?' for a name no player has: up to `limit` real HLTV nicks (the in-memory index - no database call
    here): the loose match ('monesy' -> m0NESY), names starting with / containing it, then the closest spellings (most
    active player first). Never the typed name itself. [] when the index isn't loaded."""
    import difflib
    k, lk = _norm(q), _loose(q)
    hits = search_players(q, limit * 2) or []           # also starts the index load if it is due
    rows = _INDEX["rows"]
    if not k or not rows:
        return []
    out = []

    def add(n):
        if n and _norm(n) != k and n not in out:
            out.append(n)
    add(canonical_nick(q))
    for h in hits:
        add(h.get("nick"))
    if len(out) < limit:
        if _CLOSE["rows"] is not rows:                  # loose key -> most active nick, once per index load
            keys = {}
            for r in rows:
                keys.setdefault(r.get("loose") or _loose(r["key"]), r["nick"])
            _CLOSE.update(rows=rows, keys=keys)
        keys = _CLOSE["keys"]
        for m in difflib.get_close_matches(lk, list(keys), n=limit * 2, cutoff=0.6):
            add(keys[m])
    return out[:limit]


def search_players(q, limit=8):
    """Player search from the HLTV database (in memory, reloaded hourly in the background).
    None when the index isn't available (HLTV database down) - the caller falls back to BO3.gg."""
    if not dsn():
        return None
    with _LOCK:                                         # test-and-set: one load at a time
        due = time.time() - _INDEX["at"] > 3600 and not _INDEX["busy"]
        if due:
            _INDEX["busy"] = True
    if due:
        threading.Thread(target=_load_index, daemon=True, name="hltv-index").start()
    rows = _INDEX["rows"]
    if not rows:
        return None
    k = _norm(q)
    if not k:
        return []
    lk = _loose(q)
    starts, has = [], []
    for r in rows:                                        # one pass; loose keys precomputed at index load
        key, lo = r["key"], r.get("loose") or _loose(r["key"])
        if key.startswith(k) or lo.startswith(lk):
            starts.append(r)
        elif k in key or lk in lo:
            has.append(r)
    return [{"slug": None, "nick": r["nick"], "name": r["team"], "country": "", "image": None}
            for r in (starts + has)[:limit]]


def event_env_from_title(title):
    """LAN / online for a match title that contains an HLTV event name (hltv.org/events, scraper):
    "Rebels vs NAVI Junior (BO1) - Roman Imperium Cup Group B" -> Roman Imperium Cup IX -> "lan"."""
    if not dsn() or not title:
        return None
    key = ("events_all",)
    with _LOCK:
        hit = _CACHE.get(key)
    if not hit or time.time() - hit[0] > 3600:
        rows, ok = [], True
        try:
            con = _connect()
            try:
                with con.cursor() as cur:
                    cur.execute("SELECT name, lan FROM events WHERE lan IS NOT NULL")
                    rows = cur.fetchall()
            finally:
                con.close()
        except Exception:
            ok = False
        hit = (time.time(), _remember(key, rows, ok, ttl=3600))
    t = _norm(title)
    core = lambda k: re.sub(r"(season)?([0-9]+|[ivx]+)$", "", k)
    best = None
    for name, lan in hit[1]:
        n = _norm(name)
        # the title often drops the year ("PGL Bucharest Open Qualifier #1" for "PGL Bucharest 2027 Open Qualifier 1"):
        # try the name without its year too, so the qualifier's own row (longer) wins over the main event's
        for k in {core(n), core(re.sub(r"(19|20)[0-9]{2}", "", n))}:
            if len(k) >= 8 and k in t and (best is None or len(k) > best[0]):
                best = (len(k), lan, str(name))
    if best is None:
        return None
    # a qualifier / online-stage title matched only by its main event's name: the main event's LAN flag is not this match's
    # (2026-10-11 audit: 'PGL Bucharest Open Qualifier #1' was read as LAN from 'PGL Bucharest 2027') - qualifiers are online
    if _QUAL_WORDS.search(title) and not _QUAL_WORDS.search(best[2]):
        return "online"
    return "lan" if best[1] else "online"


# words that mark a qualifier / online stage of an event (the same words the HLTV environment fallback reads as online)
_QUAL_WORDS = re.compile(r"qualifier|qualification|\bopen\b|closed|\bonline\b|cup #|weekly|ladder", re.I)


def team_country(name):
    """(code, country name) of a team's flag on HLTV (hltv-scraper teams.country), or None.
    The whole table is cached for an hour; None until the scraper has stored countries."""
    key = ("countries",)
    with _LOCK:
        hit = _CACHE.get(key)
    if not hit or time.time() - hit[0] > 3600:
        out, ok = {}, True
        try:
            con = _connect()
            try:
                with con.cursor() as cur:
                    cur.execute("SELECT name, country, country_name FROM teams WHERE country IS NOT NULL OR country_name IS NOT NULL")
                    out = {_norm(n): (c, cn) for n, c, cn in cur.fetchall()}
            finally:
                con.close()
        except Exception:
            ok = False
        hit = (time.time(), _remember(key, out, ok, ttl=3600))
    return hit[1].get(_norm(name)) if name else None


def team_ranks():
    """{normalized team name: latest VRS rank} for every ranked team (cached 1 hour)."""
    key = ("ranks",)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    out, ok = {}, True
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
        ok = False
    return _remember(key, out, ok, ttl=3600)


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


def _environment(cur, ids, team, opponent=None, start=None):
    """LAN vs online: his kills per round in each over the last 12 months (all maps) and whether
    tonight's event is LAN (from matches already played at that event). Needs match_overview.lan
    (hltv-scraper parser v6). With the graded match's opponent (and start): tonight is that match's event only."""
    if not ids:
        return None
    cur.execute("""SELECT mo.lan, sum(ps.k), sum(own.score + opp.score), count(*)
                   FROM player_stats ps JOIN match_overview mo USING (matchid)
                   JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid          -- played maps only (not the "All" row)
                   JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                   JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                   WHERE ps.playerid = ANY(%(ids)s) AND ps.sideid = 0 AND mo.date >= current_date - 365 AND mo.lan IS NOT NULL
                   GROUP BY mo.lan""", {"ids": ids})
    env = {}
    for lan, k, r, n in cur.fetchall():
        if r:
            env["lan" if lan else "online"] = {"maps": n, "kpr": round(k / r, 3)}
    nm = _next_match(cur, team, opponent, start) if opponent else _next_match(cur, team)
    tonight = None
    if nm and nm.get("event"):                       # HLTV's own label first: hltv.org/events (scraper)
        try:
            cur.execute("""SELECT lan FROM events WHERE lan IS NOT NULL
                           AND (lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) = %(e)s
                                OR %(e)s LIKE lower(regexp_replace(name, '[^a-zA-Z0-9]', '', 'g')) || '%%')
                           ORDER BY length(name) DESC LIMIT 1""", {"e": _norm(nm["event"])})
            row = cur.fetchone()
            if row is not None:
                tonight = "lan" if row[0] else "online"
        except Exception as e:
            if is_transient(e):                      # 2026-10-21 audit: a timeout is a failed section, never another label
                raise
            cur.connection.rollback()                # events table not there yet
    if tonight is None and nm and nm.get("event"):
        e = _norm(nm["event"])                         # same event, stage names may differ ("... Stage 1")
        cur.execute("""SELECT lan FROM match_overview WHERE lan IS NOT NULL AND date >= current_date - 60
                       AND (lower(regexp_replace(event, '[^a-zA-Z0-9]', '', 'g')) LIKE %(e)s || '%%'
                            OR %(e)s LIKE lower(regexp_replace(event, '[^a-zA-Z0-9]', '', 'g')) || '%%')
                       ORDER BY date DESC LIMIT 1""", {"e": e})
        row = cur.fetchone()
        tonight = tonight or (None if not row else ("lan" if row[0] else "online"))
        if tonight is None and re.search(r"qualifier|online|\bopen\b|closed|cup #|weekly|ladder|esea|challenger league|cct (online|south|north|europe)",
                                         nm["event"], re.I):
            tonight = "online"                       # qualifiers and online leagues are played online
    if not env and tonight is None:
        return None
    allk = sum(v["kpr"] * v["maps"] for v in env.values()) / max(1, sum(v["maps"] for v in env.values())) if env else None
    side = env.get(tonight) if tonight else None
    return {**env, "tonight": tonight, "event": (nm or {}).get("event"),
            "ratio": round(side["kpr"] / allk, 3) if side and allk else None, "ratio_maps": side["maps"] if side else 0}


def _next_match(cur, team, opponent=None, start=None):
    """His team's next (or live) match on HLTV's schedule - a second opinion on BO3.gg's slate. With `opponent`: the match
    against that team (exact name variants / aliases) starting within comparison.JOIN_START_MIN of `start` (when given) -
    the graded match itself, or None (2026-10-13 audit: tonight's LAN / online label was taken from the team's next / live
    match against anyone)."""
    if not team:
        return None
    vs = team_name_variants(team) or [_norm(team)]      # 'Nordic Partners Gaming' = HLTV's 'Nordic Partners' (exact variants)
    sql = """SELECT time, live, team1, team2, event, format, url FROM upcoming_matches
             WHERE (lower(regexp_replace(team1, '[^a-zA-Z0-9]', '', 'g')) = ANY(%(t)s)
                 OR lower(regexp_replace(team2, '[^a-zA-Z0-9]', '', 'g')) = ANY(%(t)s))
               AND (live OR time >= now() - interval '3 hours')
             ORDER BY live DESC, time"""
    if opponent:
        cur.execute(sql, {"t": vs})
        rows = cur.fetchall()
    else:
        cur.execute(sql + " LIMIT 1", {"t": vs})
        r = cur.fetchone()
        rows = [r] if r else []
    out = [{"time": r[0].isoformat() if r[0] else None, "live": r[1], "opponent": r[3] if _norm(r[2]) in vs else r[2],
            "event": r[4], "format": r[5], "url": r[6], "_at": r[0]} for r in rows]
    if opponent:
        out = [m for m in out if same_team(opponent, m["opponent"])]
        st = _utc_dt(start)
        if st is not None:
            from comparison import JOIN_START_MIN
            out = sorted((m for m in out if m["_at"] is not None and abs((_utc_dt(m["_at"]) - st).total_seconds()) <= JOIN_START_MIN * 60),
                         key=lambda m: abs((_utc_dt(m["_at"]) - st).total_seconds()))
    if not out:
        return None
    out[0].pop("_at")
    return out[0]


def _utc_dt(t):
    """A datetime / ISO string as an aware UTC datetime (naive = UTC); None when unreadable."""
    from datetime import datetime, timezone
    if t is None:
        return None
    if not isinstance(t, datetime):
        try:
            t = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


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
SELECT mo.matchid, mo.date, mm.number, mp.name, ps.k, h.hs, own.score, opp.score, t2.name, t.name, mo.lan, t2.teamid,
       (SELECT count(*) FROM map_rounds rr WHERE rr.mapstatsid = mm.mapstatsid)
FROM match_maps mm
JOIN match_overview mo USING (matchid)
JOIN maps mp ON mp.mapid = mm.mapid
JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
JOIN teams t ON t.teamid = ps.teamid
JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
JOIN matches opp ON opp.matchid = mm.matchid AND opp.teamid <> ps.teamid AND opp.mapid = mm.mapid AND opp.sideid = 0
JOIN teams t2 ON t2.teamid = opp.teamid
LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
WHERE ps.playerid = ANY(%(ids)s) AND mm.number <= {maxn} AND mo.date >= %(since)s {extra}
ORDER BY mo.date DESC, mo.matchid DESC, mm.number
"""


def _series_rows(cur, ids, since, extra="", params=None, maxn=2, need=(1, 2)):
    cur.execute(_SERIES_SQL.format(extra=extra, maxn=int(maxn)), dict(params or {}, ids=ids, since=since))
    by_match = {}
    for row in cur.fetchall():
        mid, d, num, mname, k, hs, own, opp, oname, tname, lan = row[:11]
        oid = row[11] if len(row) > 11 else None
        nr = row[12] if len(row) > 12 else None
        # 2026-10-25 audit (research/esports_accuracy_2026_10_25): a map page captured before the map finished (241953,
        # hltv:2399658: 24 round rows of a 29-round OT map) settled 19 units on its partial scoreboard. A map whose stored
        # round history is shorter than its final score is PARTIAL: settlement waits for it (never settles on it).
        partial = {"partial": True} if nr and nr < (own or 0) + (opp or 0) else {}   # key only when partial: rows unchanged
        by_match.setdefault(mid, []).append({"opponent_id": oid, **partial,
            "match_id": f"hltv-{mid}", "hltv_match": mid, "date": d.isoformat(), "number": num,
            "map": "de_" + _norm(mname), "kills": k, "headshots": hs, "rounds": (own or 0) + (opp or 0),
            "won": (own or 0) > (opp or 0), "opponent": oname, "team": tname, "source": "hltv",
            "env": None if lan is None else ("lan" if lan else "online")})
    return [m for maps in by_match.values() if {x["number"] for x in maps} >= set(need) for m in maps]


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

    def read():
        out, ok = [], True
        try:
            con = _connect()
            try:
                with con.cursor() as cur:
                    ids = _player_ids(cur, nick, team)
                    out = _series_rows(cur, ids, since) if ids else []    # no HLTV player: a good, empty answer
            finally:
                con.close()
        except Exception:
            out, ok = [], False            # unreachable / timed out / cut off mid-read: never a partial answer; the last good
        return _remember(key, out, ok)     # rows stay (memory, else the stored copy), retried in FAIL_TTL s
    return single_flight(key, read, lambda: _peek(key))


def settle_totals(nick, team, hltv_match):
    """Maps 1-2 kills and headshots for one HLTV match: {"kills", "headshots" (None until read),
    "found"}. Not cached: used by the pick log's settle."""
    if not dsn():
        return {"found": False}
    try:
        con = _connect()
    except Exception as e:
        return {"found": False, "transient": is_transient(e)}
    bad = False
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            rows = _series_rows(cur, ids, "2000-01-01", "AND mo.matchid = %(mid)s", {"mid": int(hltv_match)}) if ids else []
    except Exception as e:
        rows, bad = [], is_transient(e)
    finally:
        con.close()
    if not rows:
        return {"found": False, "transient": bad}     # unreachable: the pick log never voids on that (2026-10-11 audit)
    m12 = sorted(rows, key=lambda r: r["number"])[:2]
    if any(r.get("partial") for r in m12):          # a map captured before it finished: wait (2026-10-25 audit)
        return {"found": True, "kills": None, "headshots": None, "partial": True, "maps": [r["map"] for r in m12]}
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
    except Exception as e:
        return {"found": False, "transient": is_transient(e)}
    bad = False
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            rows = _series_rows(cur, ids, "2000-01-01", "AND mo.date BETWEEN %(d)s::date - 1 AND %(d)s::date + 1",
                                {"d": day}) if ids else []
    except Exception as e:
        rows, bad = [], is_transient(e)
    finally:
        con.close()
    if not rows and bad:
        return {"found": False, "transient": True}
    on = _opp_rows(nick, team, rows, opponent)
    if not on:
        return {"found": False}
    if len({r["hltv_match"] for r in on}) > 1:      # a rematch inside the +-1 day window: never guess which one
        return {"found": False, "ambiguous": True}
    mid = on[0]["hltv_match"]
    m12 = sorted((r for r in on if r["hltv_match"] == mid), key=lambda r: r["number"])[:2]
    if any(r.get("partial") for r in m12):          # a map captured before it finished: wait (2026-10-25 audit)
        return {"found": False, "wait": True, "partial": True}
    hs = [r["headshots"] for r in m12]
    return {"found": True, "hltv_match": mid, "date": m12[0]["date"], "kills": sum(r["kills"] for r in m12),
            "headshots": None if None in hs else sum(hs), "maps": [r["map"] for r in m12],
            "rounds": [r["rounds"] for r in m12], "won": [r["won"] for r in m12],
            "per_map": [[r["kills"], r["headshots"]] for r in m12]}


def settle_scope(nick, team, day, opponent, scope):
    """HLTV result for a Maps 1-3 ("123") or Map 1 ("1") line: {"found", "kills", "headshots", "maps",
    "rounds", "won", "per_map"} - Maps 1-3 counts every map played (2 or 3); "wait" while map 3 of a
    1-1 series isn't in yet."""
    if not dsn():
        return {"found": False}
    try:
        con = _connect()
    except Exception as e:
        return {"found": False, "transient": is_transient(e)}
    bad = False
    try:
        with con.cursor() as cur:
            ids = _player_ids(cur, nick, team)
            rows = _series_rows(cur, ids, "2000-01-01", "AND mo.date BETWEEN %(d)s::date - 1 AND %(d)s::date + 1",
                                {"d": day}, maxn=3, need=(1,)) if ids else []
    except Exception as e:
        rows, bad = [], is_transient(e)
    finally:
        con.close()
    if not rows and bad:
        return {"found": False, "transient": True}
    on = _opp_rows(nick, team, rows, opponent)
    if not on:
        return {"found": False}
    mid = on[0]["hltv_match"]
    ms = sorted((r for r in on if r["hltv_match"] == mid), key=lambda r: r["number"])
    if scope == "1":
        ms = ms[:1]
    if any(r.get("partial") for r in ms):           # a map captured before it finished: wait (2026-10-25 audit)
        return {"found": True, "wait": True, "partial": True}
    if scope != "1":
        if len(ms) == 2 and ms[0]["won"] != ms[1]["won"]:
            return {"found": True, "wait": True}       # 1-1 after two maps: map 3 not in yet
        if len(ms) < 2:
            return {"found": True, "wait": True}
    hs = [r["headshots"] for r in ms]
    return {"found": True, "hltv_match": mid, "date": ms[0]["date"], "kills": sum(r["kills"] for r in ms),
            "headshots": None if None in hs else sum(hs), "maps": [r["map"] for r in ms],
            "rounds": [r["rounds"] for r in ms], "won": [r["won"] for r in ms],
            "per_map": [[r["kills"], r["headshots"]] for r in ms]}


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


_POOL = {"at": 0.0, "maps": None}


def map_key(m):
    """'de_dust2' / 'Dust 2' -> 'dust2' (BO3.gg and HLTV map names compared on one key)."""
    k = _norm(m)
    return k[2:] if k.startswith("de") and len(k) > 4 else k


def active_map_pool(days=30, min_share=0.03):
    """The CURRENT competitive pool = maps played in pro matches in the last `days` days (>= min_share of maps), as
    map_key()s; cached 6 h; None when HLTV isn't available. 2026-10-09: veto windows reach back 150 days, so a map
    that left the pool (Overpass in July, Train earlier) could still be predicted - the pool now follows play."""
    if not dsn():
        return None
    if _POOL["maps"] is not None and time.time() - _POOL["at"] < 6 * 3600:
        return _POOL["maps"]
    try:
        con = _connect()
        try:
            with con.cursor() as cur:
                cur.execute("SET statement_timeout = 30000")
                cur.execute("""SELECT mp.name, count(*) FROM match_maps mm JOIN match_overview mo USING (matchid)
                               JOIN maps mp ON mp.mapid = mm.mapid
                               WHERE mo.date >= current_date - make_interval(days => %s) GROUP BY mp.name""", (days,))
                rows = cur.fetchall()
        finally:
            con.close()
        tot = sum(n for _, n in rows) or 1
        maps = {map_key(m) for m, n in rows if n / tot >= min_share}
        if len(maps) >= 5:
            _POOL.update(at=time.time(), maps=maps)
            hltv_store.save(("map_pool", days, min_share), maps)
    except Exception:
        if _POOL["maps"] is None:                  # restart during an outage: the last good pool from disk
            stored = hltv_store.load(("map_pool", days, min_share))
            if stored:
                _POOL.update(at=time.time() - 6 * 3600 + FAIL_TTL, maps=stored[1])
    return _POOL["maps"]


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


def predict_maps(team_a, team_b, before, pool=None, runs=4000, seed=7, pool_keys=None):
    """P(Map 1 = m) and P(Map 2 = m) from both teams' HLTV veto history (BO3 order: A ban, B ban,
    A pick = Map 1, B pick = Map 2, A ban, B ban, decider). Who goes first is unknown: both orders."""
    import random
    if not dsn():
        return None
    key = ("veto", _norm(team_a), _norm(team_b), before, runs, tuple(sorted(pool_keys or ())))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    try:
        out = _predict_maps(team_a, team_b, before, pool, runs, seed, random, pool_keys)
    except Exception as e:
        if not is_transient(e):
            raise
        return _remember(key, None, ok=False)      # the last good prediction (memory, then disk) during an outage
    return _remember(key, out)


def _predict_maps(team_a, team_b, before, pool, runs, seed, random, pool_keys=None):
    con = _connect()
    try:
        with con.cursor() as cur:
            ha, hb = _team_history(cur, team_a, before), _team_history(cur, team_b, before)
    finally:
        con.close()
    if ha["n"] < 3 or hb["n"] < 3:
        return None
    pool = pool or sorted({m for h in (ha, hb) for r in ("ban1", "pick", "ban2") for m in h[r]})
    # the pool: this EVENT's verified pool (a finished BO3 veto at the same event lists all 7 maps) when given;
    # otherwise, for live predictions, an INFERRED pool (maps played in pro matches in the last 30 days) - a fallback
    # that can lag a pool change or differ from a particular event's pool, so it is labelled as inferred
    from datetime import date as _date
    pool_source = "veto history"
    if pool_keys:
        pool = [m for m in pool if map_key(m) in set(pool_keys)]
        pool_source = "event veto"
    elif before is None or str(before) >= str(_date.today()):
        act = active_map_pool()
        if act:
            pool = [m for m in pool if map_key(m) in act]
            pool_source = "inferred (maps played in the last 30 days)"
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
    # blended 50/50 with a flat prior over the pool (2026-10-07: 288 HLTV BO3s, vetoes from before each
    # match - raw log-loss 1.877, flat 1.869, blended 1.764; the raw shares were overconfident)
    def calib(m, n):
        return {k: round(0.5 * m.get(k, 0) / runs + 0.5 / n, 4) for k in pool if k in m or True}
    c1 = calib(m1, len(pool))
    c2 = calib(m2, len(pool) - 1)
    return {"map1": dict(sorted(c1.items(), key=lambda x: -x[1])), "map2": dict(sorted(c2.items(), key=lambda x: -x[1])),
            "raw": {"map1": {k: v / runs for k, v in m1.items()}, "map2": {k: v / runs for k, v in m2.items()}},
            "history": [ha["n"], hb["n"]], "calibration": "50% veto model + 50% flat", "pool_source": pool_source}


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
        pr = dict(pr.get("raw") or pr)                     # score the raw veto model; blends below
        res["cases"] += 1
        r1, r2 = list(pr["map1"]), list(pr["map2"])
        res["map1_top1"] += r1[:1] == [p1]; res["map1_top2"] += p1 in r1[:2]
        res["map2_top1"] += r2[:1] == [p2]; res["map2_top2"] += p2 in r2[:2]
        res["either_pick_in_top2"] += p1 in r1[:2] and p2 in r2[:2]
        res["ll"] += -math.log(max(pr["map1"].get(p1, 0), 0.01)) - math.log(max(pr["map2"].get(p2, 0), 0.01))
        for mix in (0.2, 0.35, 0.5, 0.65, 0.8, 1.0):        # blended with a flat prior over the pool
            n1, n2 = 7, 6
            q1 = (1 - mix) * pr["map1"].get(p1, 0) + mix / n1
            q2 = (1 - mix) * pr["map2"].get(p2, 0) + mix / n2
            res.setdefault("ll_mix", {}).setdefault(mix, 0.0)
            res["ll_mix"][mix] += -math.log(q1) - math.log(q2)
    if res["cases"]:
        res["ll"] = round(res["ll"] / (2 * res["cases"]), 3)
        res["ll_mix"] = {str(k): round(v / (2 * res["cases"]), 4) for k, v in (res.get("ll_mix") or {}).items()}
    return res
