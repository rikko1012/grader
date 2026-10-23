"""HTTP handlers for the Scout Sports research assistant (route contract: sports/web.py ROUTES, one line each).

  GET  /api/sports/assistant/status        MEMBER  available?, today's quota, limits
  POST /api/sports/assistant/ask           MEMBER  {"question", "league": "nba"|"nfl", "page_context"?} -> run id
  GET  /api/sports/assistant/stream        MEMBER  ?run=<id>&after=<seq>: events with seq > after (resume), done flag;
                                                   &format=sse -> text/event-stream body with "id: <seq>" lines
  POST /api/sports/assistant/cancel        MEMBER  {"run_id"}
  GET  /api/sports/admin/assistant         ADMIN   config status (never key material), runner heartbeat, queue counts
Page: web/sports/assistant.html (+ assistant.js / assistant.css), served by the gateway at /sports/assistant.html.

Server-side entitlement: web.dispatch admits only a verified member / admin token (paid check done by the esports
server before it mints the token); every handler here additionally requires req.member and the assistant switched on
with a live runner, and a run is visible / cancellable only by the member who asked it (others get 404). The handlers
do no model work and never touch the canonical store except one cheap MAX(rid) read for the cache key: ask =
validate + refuse-intent screen + cache lookup + quota + enqueue; stream = read stored events. "Streaming" is
sequence-numbered polling of stored events, so a reconnect resumes exactly where it stopped (after=<last seq>) and
answers never depend on one long-lived connection.
"""
from __future__ import annotations

import json

from . import web
from .assistant import config, dates, policy, runs

POLL_MS = 400


def _available(con):
    if not config.enabled_flag():
        return False, "off"
    seen = runs.runner_seen(con)
    if not seen["alive"]:
        return False, "starting" if not seen["seen"] else "unavailable"
    return True, None


def _member(req):
    return f"{req.role}:{req.member}" if req.member else None


def _store_version():
    def load():
        with web.store_ro() as con:
            return con.execute("SELECT MAX(rid) FROM records").fetchone()[0] or 0
    return web.cached(("assist_store_version",), load, ttl=30)


def status(req):
    who = _member(req)
    if who is None:
        return 401, {"error": "member_identity_required"}
    with runs.opened() as con:
        ok, why = _available(con)
        lim = config.limits()
        return 200, {"available": ok, "reason": why, "quota": runs.quota(con, who),
                     "limits": {k: lim[k] for k in ("max_question_chars", "max_context_chars", "max_answer_chars",
                                                    "daily_questions")}}


def ask(req):
    who = _member(req)
    if who is None:
        return 401, {"error": "member_identity_required"}
    if req.method != "POST":
        return 405, {"error": "use POST"}
    d = req.json()
    if not isinstance(d, dict):
        return 400, {"error": "body must be a JSON object"}
    question, league, ctx = d.get("question"), d.get("league"), d.get("page_context")
    if not isinstance(question, str):
        return 400, {"error": "question must be text"}
    if league not in ("nba", "nfl"):
        return 400, {"error": "league must be nba or nfl"}
    if ctx is not None and not isinstance(ctx, str):
        return 400, {"error": "page_context must be text"}
    bad = policy.check_limits(question, ctx)
    if bad:
        return (413 if "too_long" in bad else 400), {"error": bad}
    question = question.strip()
    with runs.opened() as con:
        ok, why = _available(con)
        if not ok:
            return 503, {"error": "assistant_unavailable", "reason": why}
        refused = policy.screen(question)
        if refused:                                     # fixed answer: no model call, no quota
            ev = [{"type": "final", "data": {"answer": refused["answer"], "citations": {}, "flags": [
                {"kind": refused["code"], "value": None}], "dates": [], "tools_used": [], "cached": False,
                "removed_numbers": 0, "model": None, "prompt_version": config.PROMPT_VERSION}}]
            rid = runs.create_cached_run(con, who, league, question, None, ev)
            return 200, {"run_id": rid, "cached": False, "refused": refused["code"], "after": 0}
        ranges = [(r["start_utc"], r["end_utc"]) for r in dates.scan(question, league)]
        key = runs.cache_key(" ".join(question.lower().split()), league, ctx or "", ranges, _store_version(),
                             config.model_id(), config.PROMPT_VERSION)
        hit = runs.get_cache(con, key, config.limits()["cache_s"])
        if hit:
            rid = runs.create_cached_run(con, who, league, question, key, hit)
            return 200, {"run_id": rid, "cached": True, "after": 0}
        try:
            rid = runs.create_run(con, who, league, question, ctx, key)
        except runs.QuotaExceeded:
            return 429, {"error": "daily_limit_reached", "quota": runs.quota(con, who)}
        except runs.Busy:
            return 409, {"error": "question_in_progress"}
        return 202, {"run_id": rid, "cached": False, "after": 0, "poll_ms": POLL_MS, "quota": runs.quota(con, who)}


def stream(req):
    who = _member(req)
    if who is None:
        return 401, {"error": "member_identity_required"}
    rid = str(req.query.get("run") or "")
    try:
        after = max(0, int(req.query.get("after") or 0))
    except ValueError:
        return 400, {"error": "after must be a number"}
    with runs.opened() as con:
        run = runs.get_run(con, rid)
        if not run or run["member"] != who:
            return 404, {"error": "run not found"}
        evs = runs.events_after(con, rid, after, 200)
        last_stored = con.execute("SELECT MAX(seq) FROM events WHERE run_id=?", (rid,)).fetchone()[0] or 0
    last = evs[-1]["seq"] if evs else after
    done = run["state"] in runs.TERMINAL and last >= last_stored
    if req.query.get("format") == "sse":
        out = ["retry: 1000\n\n"]
        for e in evs:
            out.append(f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e['data'], default=str)}\n\n")
        if done:
            out.append(f"event: end\ndata: {json.dumps({'state': run['state']})}\n\n")
        return 200, "".join(out).encode("utf-8"), "text/event-stream; charset=utf-8"
    return 200, {"run_id": rid, "state": run["state"], "events": evs, "last_seq": last, "done": done,
                 "poll_ms": None if done else POLL_MS}


def cancel(req):
    who = _member(req)
    if who is None:
        return 401, {"error": "member_identity_required"}
    if req.method != "POST":
        return 405, {"error": "use POST"}
    rid = str((req.json() or {}).get("run_id") or "")
    with runs.opened() as con:
        run = runs.get_run(con, rid)
        if not run or run["member"] != who:
            return 404, {"error": "run not found"}
        was = runs.request_cancel(con, rid)
    return 200, {"run_id": rid, "was": was,
                 "cancel": {"running": "requested", "queued": "done"}.get(was, "not_needed")}


def admin_status(req):
    with runs.opened() as con:
        return 200, {"config": config.status(), "runner": runs.runner_seen(con), "queue": runs.queue_counts(con)}
