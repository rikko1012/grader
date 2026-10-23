"""Sports HTTP routing core - runs INSIDE THE SPORTS SERVICE PROCESS (sports/service.py), never in an esports request
thread in production (SPORTS_API_MODE=service, the default; see sports/RUNBOOK.md).

ROUTE TABLE CONTRACT (stable - other agents add exactly ONE line per route to ROUTES and nothing else in this file)
-----------------------------------------------------------------------------------------------------------------
    "<path>": ("<module>:<function>", <ACCESS>, (<methods>,)),

  path     exact path under /api/sports/ ; a key ending in "/*" is a prefix route (req.rest = the remainder).
           Admin routes live under /api/sports/admin/ and must use ADMIN (tests/test_service_routes.py enforces it).
  handler  "sports.api_<NAME>:<function>" - imported lazily on first use, so a broken agent module only breaks its own
           routes (503 sports_unavailable for those paths); never import another agent's module.
  ACCESS   MEMBER (valid st1 token, paid entitlement checked by the esports server) or ADMIN (token role admin).
           There is NO public data route: the only unauthenticated path is the service's own /api/sports/health.
  methods  ("GET",) or ("GET", "POST"). POST bodies are JSON, <= 64 KB (service.py refuses larger unread).

  Handler signature:   def fn(req: Request) -> (status, body) | (status, body, content_type)
      body = dict / list (sent as JSON) or bytes (with content_type).
      req.query (dict of first values), req.method, req.body (bytes), req.member (pseudonymous "m_..." id),
      req.role ("member" | "admin"), req.rest (prefix routes), req.json() (parsed POST body or {}).
  Keep handlers cheap: answer from precomputed snapshots; use web.cached(key, fn) (single-flight TTL cache) for shared
  answers and web.store_ro() (bounded read-only connection pool with a hard per-query time budget) for store reads.
  Never write the store from a handler - the worker process owns writes. Member-specific answers must not be put in the
  shared cache under a key that lacks req.member.

Also: handle(path, query, allowed, admin) - the legacy in-process entry (SPORTS_API_MODE=inprocess, dev / tests only;
measured +51% esports p99 at 700 members, foundation 2026-10-23) - and page bytes for /sports.
"""
from __future__ import annotations

import importlib
import json
import os
import queue
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import capabilities, flags, store

MEMBER, ADMIN = "member", "admin"

# ------------------------------------------------------------------------------------------------------- ROUTE TABLE
ROUTES = {
    "/api/sports/status": ("sports.web:status", MEMBER, ("GET",)),
    "/api/sports/offers": ("sports.web:offers", MEMBER, ("GET",)),
    "/api/sports/admin/coverage": ("sports.web:admin_coverage", ADMIN, ("GET",)),
    # ---- agent routes below: ONE line each, handler in sports/api_<NAME>.py ----
    "/api/sports/slips/rules": ("sports.api_slips:rules_view", MEMBER, ("GET",)),
    "/api/sports/slips/pool": ("sports.api_slips:pool_view", MEMBER, ("GET",)),
    "/api/sports/slips/build": ("sports.api_slips:build", MEMBER, ("GET", "POST")),
    "/api/sports/slips/validate": ("sports.api_slips:validate_view", MEMBER, ("GET", "POST")),
    "/api/sports/slips/settle": ("sports.api_slips:settle_view", MEMBER, ("GET", "POST")),
    "/api/sports/admin/slips/build": ("sports.api_slips:admin_build", ADMIN, ("GET", "POST")),
    "/api/sports/admin/slips/log": ("sports.api_slips:admin_log", ADMIN, ("GET",)),
    "/api/sports/manifest": ("sports.api_board:manifest", MEMBER, ("GET",)),
    "/api/sports/slate": ("sports.api_board:slate", MEMBER, ("GET",)),
    "/api/sports/board": ("sports.api_board:board", MEMBER, ("GET",)),
    "/api/sports/forecast": ("sports.api_board:forecast", MEMBER, ("GET",)),
    "/api/sports/player": ("sports.api_board:player", MEMBER, ("GET",)),
    "/api/sports/history": ("sports.api_board:history", MEMBER, ("GET",)),
    "/api/sports/results": ("sports.api_board:results", MEMBER, ("GET",)),
}
# ---------------------------------------------------------------------------------------------------- end of table

CACHE_S = 30
CACHE_MAX = 256
REQUEST_BUDGET_S = float(os.environ.get("SPORTS_REQUEST_BUDGET_S", "4"))     # hard per-query / per-wait budget
DB_POOL = max(1, int(os.environ.get("SPORTS_DB_POOL", "4")))                 # concurrent read-only store connections
DB_WAIT_S = float(os.environ.get("SPORTS_DB_WAIT_S", "1.0"))
_CACHE = {}
_LOCK = threading.Lock()
_FLIGHTS = {}
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PLACEHOLDER = (b"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
               b"<title>Scout Sports</title><body style='font-family:system-ui;margin:2rem'><h1>Scout Sports</h1>"
               b"<p>Sports is being built and is not available yet.</p></body>")


class Busy(RuntimeError):
    """The bounded store pool (or a single-flight wait) is saturated: answered 503 sports_busy, never queued forever."""


@dataclass
class Request:
    path: str
    query: dict = field(default_factory=dict)
    method: str = "GET"
    body: bytes = b""
    member: str | None = None
    role: str | None = None
    rest: str = ""

    def json(self):
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


# --------------------------------------------------------------------------------------------- single-flight cache
def cached(key, fn, ttl=None):
    """TTL cache with SINGLE-FLIGHT loading: when many members ask for the same expired key at once, one thread runs
    the load and the rest wait (at most REQUEST_BUDGET_S) for its answer (measured 2026-10-23: without it, 466
    simultaneous misses ran 466 store queries and pushed the web process to 271 MB). Bounded to CACHE_MAX entries."""
    ttl = CACHE_S if ttl is None else ttl
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        flight = _FLIGHTS.get(key)
        leader = flight is None
        if leader:
            flight = _FLIGHTS[key] = {"event": threading.Event(), "value": None, "error": None}
    if not leader:
        flight["event"].wait(REQUEST_BUDGET_S)
        if flight["error"] is not None:
            raise flight["error"]
        if flight["event"].is_set():
            return flight["value"]
        raise Busy("sports answer not ready within the request budget")
    try:
        val = fn()
        flight["value"] = val
        with _LOCK:
            if len(_CACHE) >= CACHE_MAX:
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:CACHE_MAX // 4]:
                    _CACHE.pop(k, None)
            _CACHE[key] = (time.time(), val)
        return val
    except Exception as e:                              # noqa: BLE001 - followers get the same failure
        flight["error"] = e
        raise
    finally:
        with _LOCK:
            _FLIGHTS.pop(key, None)
        flight["event"].set()


_cached = cached                                        # foundation name (tests)


# ------------------------------------------------------------------------------------- bounded read-only store pool
class _Pool:
    def __init__(self, size):
        self.size = size
        self.slots = threading.BoundedSemaphore(size)
        self.idle = queue.LifoQueue()
        self.lock = threading.Lock()
        self.stats = {"size": size, "in_use": 0, "peak_in_use": 0, "opened": 0, "waits_timed_out": 0,
                      "queries_interrupted": 0}
        self.path = None

    def reset(self):
        while True:
            try:
                self.idle.get_nowait().close()
            except queue.Empty:
                break
            except Exception:                           # noqa: BLE001
                pass

    @contextmanager
    def get(self, budget_s=None):
        if not self.slots.acquire(timeout=DB_WAIT_S):
            with self.lock:
                self.stats["waits_timed_out"] += 1
            raise Busy("sports store pool saturated")
        con = None
        with self.lock:
            self.stats["in_use"] += 1
            self.stats["peak_in_use"] = max(self.stats["peak_in_use"], self.stats["in_use"])
        try:
            path = store.db_path()
            if self.path != path:                        # SPORTS_DATA_DIR changed (tests): drop old connections
                self.reset()
                self.path = path
            try:
                con = self.idle.get_nowait()
            except queue.Empty:
                con = store.connect(read_only=True)
                with self.lock:
                    self.stats["opened"] += 1
            deadline = time.monotonic() + (budget_s or REQUEST_BUDGET_S)

            def guard():
                return 1 if time.monotonic() > deadline else 0
            con.set_progress_handler(guard, 2000)
            try:
                yield con
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e):
                    with self.lock:
                        self.stats["queries_interrupted"] += 1
                    raise Busy("store query over the request budget") from None
                raise
            con.set_progress_handler(None, 0)
            self.idle.put(con)
            con = None
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:                       # noqa: BLE001
                    pass
            with self.lock:
                self.stats["in_use"] -= 1
            self.slots.release()


_POOL = _Pool(DB_POOL)


def store_ro(budget_s=None):
    """with web.store_ro() as con: ... - read-only, at most SPORTS_DB_POOL at once, each statement interrupted after
    the request budget (-> Busy -> 503 sports_busy). Raises FileNotFoundError while the worker has not created it."""
    return _POOL.get(budget_s)


def pool_stats():
    with _POOL.lock:
        return dict(_POOL.stats)


def cache_stats():
    with _LOCK:
        return {"entries": len(_CACHE), "max": CACHE_MAX, "in_flight": len(_FLIGHTS), "ttl_s": CACHE_S}


# ------------------------------------------------------------------------------------------------- built-in routes
def _json(code, obj):
    return code, json.dumps(obj, default=str).encode("utf-8"), "application/json"


def _offer_view(rid, o):
    ok, why = capabilities.gate(o)
    return {"record_id": rid, "platform": o.platform, "offer_id": o.offer_id, "league": o.league,
            "event_id": o.event_id, "event_ref": o.event_ref, "player_id": o.player_id, "player": o.player_name,
            "team": o.team, "stat": o.stat, "source_stat": o.source_stat, "scope": o.scope, "line": o.line,
            "variant": o.variant, "promotional": o.promotional, "availability": o.availability,
            "sides": [{"side": s.side, "offered": s.offered, "price_format": s.price_format, "price": s.price,
                       "bid": s.bid, "ask": s.ask, "multiplier": s.multiplier} for s in o.sides],
            "listed_start_utc": o.listed_start_utc, "listed_time_kind": o.listed_time_kind,
            "source_updated_at": o.source_updated_at, "observed_at": o.observed_at,
            "source_time_unknown": o.source_time_unknown, "gradable": ok, "not_gradable_because": why}


def status(req):
    def load():
        out = {"flags": flags.state()}
        try:
            with store_ro() as con:
                lvl = con.execute("SELECT v FROM meta WHERE k='schema_level'").fetchone()
                out["store"] = {"ready": True, "schema_level": int(lvl[0]) if lvl else None}
        except FileNotFoundError:
            out["store"] = {"ready": False}
        return out
    return 200, cached(("status",), load)


def offers(req):
    q = req.query
    league = q.get("league")
    if league not in ("nba", "nfl"):
        return 400, {"error": "league must be nba or nfl"}
    try:
        limit = max(1, min(500, int(q.get("limit") or 200)))
    except ValueError:
        return 400, {"error": "limit must be a number"}
    key = ("offers", league, q.get("stat"), q.get("platform"), q.get("event_id"), q.get("player_id"), limit)

    def load():
        with store_ro() as con:
            rows = store.offers(con, league=league, stat=q.get("stat") or None, platform=q.get("platform") or None,
                                event_id=q.get("event_id") or None, player_id=q.get("player_id") or None,
                                limit=limit)
        return {"league": league, "count": len(rows), "offers": [_offer_view(rid, o) for rid, o in rows]}
    return 200, cached(key, load)


def admin_coverage(req):
    from . import coverage

    def load():
        with store_ro(budget_s=max(REQUEST_BUDGET_S, 20)) as con:
            return coverage.report(con)
    return 200, cached(("admin_coverage",), load, ttl=120)


# ------------------------------------------------------------------------------------------------------- dispatch
_HANDLERS = {}


def _resolve(target):
    fn = _HANDLERS.get(target)
    if fn is None:
        mod, _, name = target.partition(":")
        fn = getattr(importlib.import_module(mod), name)
        _HANDLERS[target] = fn
    return fn


def match(path):
    """-> (route_key, entry, rest) or (None, None, "")."""
    entry = ROUTES.get(path)
    if entry is not None:
        return path, entry, ""
    best = None
    for k in ROUTES:
        if k.endswith("/*") and path.startswith(k[:-1]) and (best is None or len(k) > len(best)):
            best = k
    if best:
        return best, ROUTES[best], path[len(best) - 1:]
    return None, None, ""


def dispatch(req: Request):
    """-> (status, body_bytes, content_type). Access is enforced HERE for every route (no blur-hiding): a request
    without a verified member role never reaches a handler. Every failure is a JSON error."""
    key, entry, rest = match(req.path)
    if entry is None:
        return _json(404, {"error": "not found"})
    target, access, methods = entry
    if req.method not in methods:
        return _json(405, {"error": "method_not_allowed", "allowed": list(methods)})
    if req.role not in (MEMBER, ADMIN):
        return _json(401, {"error": "members_only"})
    if access == ADMIN and req.role != ADMIN:
        return _json(403, {"error": "admin_only"})
    if access not in (MEMBER, ADMIN):                   # a table typo never opens a route
        return _json(403, {"error": "route_access_invalid"})
    req.rest = rest
    try:
        out = _resolve(target)(req)
    except FileNotFoundError:
        return _json(503, {"error": "sports_store_not_ready"})
    except Busy:
        return _json(503, {"error": "sports_busy"})
    except (ValueError, json.JSONDecodeError) as e:
        if req.method == "POST":
            return _json(400, {"error": "bad_request", "detail": str(e)[:120]})
        raise
    code, body = out[0], out[1]
    ctype = out[2] if len(out) > 2 else "application/json"
    if not isinstance(body, (bytes, bytearray)):
        return _json(code, body)
    return code, bytes(body), ctype


def page():
    f = WEB_DIR / "sports.html"
    return f.read_bytes() if f.exists() else PLACEHOLDER


def handle(path, query, allowed, admin):
    """Legacy in-process entry (SPORTS_API_MODE=inprocess - dev / tests only). allowed() / admin() are the esports
    server's own server-side checks."""
    if path == "/sports":
        if not flags.on("SPORTS_UI"):
            return _json(404, {"error": "not found"})
        return 200, page(), "text/html; charset=utf-8"
    if not flags.on("SPORTS_API"):
        return _json(404, {"error": "not found"})
    key, entry, _ = match(path)
    if entry is None:
        return _json(404, {"error": "not found"})
    if entry[1] == ADMIN:
        role = ADMIN if admin() else None
        if role is None:
            return _json(403, {"error": "admin_only"})
    else:
        if not allowed():
            return _json(401, {"error": "members_only"})
        role = ADMIN if admin() else MEMBER
    try:
        return dispatch(Request(path=path, query=dict(query), role=role, member="m_inprocess"))
    except Exception as e:                              # noqa: BLE001
        sys.stderr.write(f"  sports route failed: {type(e).__name__}: {str(e)[:200]}\n")
        return _json(503, {"error": "sports_unavailable"})
