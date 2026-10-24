"""Member pick tracking handlers (sports service process; route lines in sports/web.py ROUTES). sports/picks.py pk-1.

  POST /api/sports/picks/track   {"picks": [{platform, record_id, side, forecast_id?, shown?, surface}]} (<= 8)
                                 live check per pick (bounded read-only store pool, 2 s budget) -> spooled, never
                                 written here; answer per pick {ok, pick_id} or {ok: false, code, text}
  GET  /api/sports/picks/mine    the member's own picks (picks.db read-only, newest first, <= 200) + results
  GET  /api/sports/admin/picks   admin aggregate (counts, hit rate, Brier / log-loss, game-clustered 95% intervals)
Stakes are always 0; grade letters are never returned (validation state text instead); no EV / returns.
"""
from __future__ import annotations

import sqlite3
import threading

from . import picks, web

_READ_SLOTS = threading.BoundedSemaphore(4)


def track(req):
    body = req.json() if req.method == "POST" else {}
    if not isinstance(body, dict):
        return 400, {"error": "bad_request", "detail": "JSON object expected"}
    items = body.get("picks") if "picks" in body else [body]
    if not isinstance(items, list) or not 1 <= len(items) <= picks.MAX_PER_REQUEST:
        return 400, {"error": "bad_request", "detail": f"1-{picks.MAX_PER_REQUEST} picks per request"}
    mh = picks.member_hash(req.member)
    out = []
    with web.store_ro(budget_s=2) as con:
        for it in items:
            if not isinstance(it, dict):
                return 400, {"error": "bad_request", "detail": "each pick is an object"}
            try:
                rec = picks.check(con, it)
            except picks.Refused as e:
                with picks._LOCK:
                    picks.STATS["refused"] += 1
                out.append({"ok": False, "code": e.code, "text": e.text, "record_id": it.get("record_id"),
                            "side": it.get("side")})
                continue
            except (ValueError, TypeError) as e:
                return 400, {"error": "bad_request", "detail": str(e)[:160]}
            pid = picks.pick_id(mh, rec["record_id"], rec["side"])
            queued = picks.spool(dict(rec, pick_id=pid, member=mh))
            out.append({"ok": queued, "pick_id": pid, "record_id": rec["record_id"], "side": rec["side"],
                        "tracked_at": rec["tracked_at"], "stake_units": 0,
                        **({} if queued else {"code": "busy", "text": "Pick tracking is busy - try again"})})
    return 200, {"version": picks.VERSION, "results": out, "stake_units": 0}


def mine(req):
    if not _READ_SLOTS.acquire(timeout=1.0):
        raise web.Busy("pick reads saturated")
    try:
        try:
            con = picks.connect_ro()
        except FileNotFoundError:
            return 200, {"version": picks.VERSION, "picks": [], "total": 0, "shown": 0, "stake_units": 0}
        try:
            return 200, picks.mine(con, req.member)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return 200, {"version": picks.VERSION, "picks": [], "total": 0, "shown": 0, "stake_units": 0}
            raise
        finally:
            con.close()
    finally:
        _READ_SLOTS.release()


def admin(req):
    def load():
        try:
            con = picks.connect_ro()
        except FileNotFoundError:
            return {"version": picks.VERSION, "all": None, "note": "no picks recorded yet", "spool": picks.stats()}
        try:
            return dict(picks.aggregate(con), spool=picks.stats())
        finally:
            con.close()
    return 200, web.cached(("picks_admin",), load, ttl=60)
