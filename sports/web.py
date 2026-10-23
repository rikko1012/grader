"""Sports web routes, called ONLY through server.py's single flag-guarded hook (Handler._sports_route).

handle(path, query, allowed, admin) -> (status, body, content_type)
  /sports                         the member page (SPORTS_UI) - web/sports.html (UI agent), else a placeholder
  /api/sports/status              flags + store state                                  (SPORTS_API, members)
  /api/sports/offers              typed offers from the canonical store, capability per offer   (SPORTS_API, members)
                                  ?league=nba|nfl &stat= &platform= &event_id= &player_id= &limit<=500
  /api/sports/admin/coverage      coverage.report()                                    (SPORTS_API, admin token)

Rules: read-only store connection (mode=ro) - the web process never writes or ingests; answers cached in-process for
CACHE_S with a bounded entry count; every failure is a JSON error (server.py turns an exception into 503 for /sports
paths only). No sports credential is read here.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import capabilities, flags, store

CACHE_S = 30
CACHE_MAX = 256
_CACHE = {}
_LOCK = threading.Lock()
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PLACEHOLDER = (b"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
               b"<title>Scout Sports</title><body style='font-family:system-ui;margin:2rem'><h1>Scout Sports</h1>"
               b"<p>Sports is being built and is not available yet.</p></body>")


_FLIGHTS = {}


def _cached(key, fn):
    """TTL cache with SINGLE-FLIGHT loading: when many members ask for the same expired key at once, one thread runs
    the store query and the rest wait for its answer (measured 2026-10-23: without it, 466 simultaneous misses ran 466
    store queries and pushed the web process to 271 MB)."""
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
        flight = _FLIGHTS.get(key)
        leader = flight is None
        if leader:
            flight = _FLIGHTS[key] = {"event": threading.Event(), "value": None, "error": None}
    if not leader:
        flight["event"].wait(30)
        if flight["error"] is not None:
            raise flight["error"]
        if flight["event"].is_set():
            return flight["value"]
        raise TimeoutError("sports answer not ready")
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


def _offers(q):
    league = q.get("league")
    if league not in ("nba", "nfl"):
        return _json(400, {"error": "league must be nba or nfl"})
    try:
        limit = max(1, min(500, int(q.get("limit") or 200)))
    except ValueError:
        return _json(400, {"error": "limit must be a number"})
    key = ("offers", league, q.get("stat"), q.get("platform"), q.get("event_id"), q.get("player_id"), limit)

    def load():
        with store.opened(read_only=True) as con:
            rows = store.offers(con, league=league, stat=q.get("stat") or None, platform=q.get("platform") or None,
                                event_id=q.get("event_id") or None, player_id=q.get("player_id") or None,
                                limit=limit)
        return {"league": league, "count": len(rows), "offers": [_offer_view(rid, o) for rid, o in rows]}
    try:
        return _json(200, _cached(key, load))
    except FileNotFoundError:
        return _json(503, {"error": "sports_store_not_ready"})


def handle(path, query, allowed, admin):
    if path == "/sports":
        if not flags.on("SPORTS_UI"):
            return _json(404, {"error": "not found"})
        f = WEB_DIR / "sports.html"
        return 200, (f.read_bytes() if f.exists() else PLACEHOLDER), "text/html; charset=utf-8"
    if not flags.on("SPORTS_API"):
        return _json(404, {"error": "not found"})
    if path == "/api/sports/admin/coverage":
        if not admin():
            return _json(403, {"error": "admin_only"})
        from . import coverage
        try:
            with store.opened(read_only=True) as con:
                return _json(200, coverage.report(con))
        except FileNotFoundError:
            return _json(503, {"error": "sports_store_not_ready"})
    if not allowed():
        return _json(401, {"error": "members_only"})
    if path == "/api/sports/status":
        def load():
            out = {"flags": flags.state()}
            try:
                with store.opened(read_only=True) as con:
                    lvl = con.execute("SELECT v FROM meta WHERE k='schema_level'").fetchone()
                    out["store"] = {"ready": True, "schema_level": int(lvl[0]) if lvl else None}
            except FileNotFoundError:
                out["store"] = {"ready": False}
            return out
        return _json(200, _cached(("status",), load))
    if path == "/api/sports/offers":
        return _offers(query)
    return _json(404, {"error": "not found"})
