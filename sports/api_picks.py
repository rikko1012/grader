"""Member pick tracking handlers (sports service process; route lines in sports/web.py ROUTES). sports/picks.py pk-1.

  POST /api/sports/picks/track   {"picks": [{platform, record_id, side, forecast_id?, shown?, surface}]} (<= 8)
                                 live check per pick (bounded read-only store pool, 2 s budget) -> spooled, never
                                 written here; answer per pick {ok, pick_id} or {ok: false, code, text}
  GET  /api/sports/picks/mine    the member's own picks (picks.db read-only, newest first, <= 200) + results;
                                 ?league=nba|nfl filters; test records hidden (admin token: &include_test=1);
                                 record = W-L-P per league (no money)
  GET  /api/sports/admin/picks   admin aggregate (counts, hit rate, Brier / log-loss, game-clustered 95% intervals)
Test picks (picks.scratch_reason): an admin token's picks, picks posted with "test": true, and every pick while
SPORTS_PICKS_SCRATCH=1 (the owner-only preview) are recorded with scratch=true and aggregated apart from member picks.
Stakes are always 0; the research grade shown when tracked (grade_shown) is returned with each pick, labelled
research, plus a per-grade hit-rate table (by_grade); no EV / returns.
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
    if not all(isinstance(it, dict) for it in items):
        return 400, {"error": "bad_request", "detail": "each pick is an object"}
    mh = picks.member_hash(req.member)
    checked = []
    with web.store_ro(budget_s=2) as con:          # check EVERY pick first: a malformed one refuses the request
        for it in items:                           # before anything is spooled (was: earlier picks tracked, then 400)
            try:
                checked.append((it, picks.check(con, it), None))
            except picks.Refused as e:
                checked.append((it, None, e))
            except (ValueError, TypeError) as e:
                return 400, {"error": "bad_request", "detail": str(e)[:160]}
    out = []
    for it, rec, e in checked:
        if e is not None:
            with picks._LOCK:
                picks.STATS["refused"] += 1
            out.append({"ok": False, "code": e.code, "text": e.text, "record_id": it.get("record_id"),
                        "side": it.get("side")})
            continue
        pid = picks.pick_id(mh, rec["record_id"], rec["side"])
        why = picks.scratch_reason(req.role, it)
        queued = picks.spool(dict(rec, pick_id=pid, member=mh, scratch=why is not None, scratch_reason=why))
        out.append({"ok": queued, "pick_id": pid, "record_id": rec["record_id"], "side": rec["side"],
                    "tracked_at": rec["tracked_at"], "stake_units": 0, "test": why is not None,
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
        q = req.query or {}
        league = q.get("league") if q.get("league") in ("nba", "nfl") else None
        include_test = q.get("include_test") in ("1", "true") and req.role == "admin"   # test records: admin only
        try:
            return 200, picks.mine(con, req.member, league=league, include_test=include_test)
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
