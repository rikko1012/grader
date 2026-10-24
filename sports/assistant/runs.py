"""assistant.db - the assistant's own small SQLite file under SPORTS_DATA_DIR (never the canonical store, never an
esports file).

  runs     one row per question: member, state queued -> running -> done | failed | cancelled, cancel flag, usage
  events   the ordered answer stream: (run_id, seq) with seq = 1, 2, 3 ... A client resumes with ?after=<last seq>;
           events are immutable once written, so a reconnect replays exactly what was missed, never a re-generation
  quota    per member per UTC day: questions reserved at enqueue (atomic, refunded on failure) and model tokens used
  cache    identical-question answers (key = question + league + page context + resolved dates + store version +
           model + prompt version) for SPORTS_ASSIST_CACHE_S; a hit is a new run whose events are copied, no model call
           and no quota

Writers: the web process writes only small control rows (enqueue, cancel flag, cached copies); the runner writes the
answer events. WAL + busy timeout keep the two apart. Runs and events older than RETAIN_DAYS are purged by the runner.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .. import config as sconfig
from ..contracts import now_utc

RETAIN_DAYS = 7
TERMINAL = ("done", "failed", "cancelled")
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, member TEXT NOT NULL, league TEXT NOT NULL,
         question TEXT NOT NULL, page_context TEXT, state TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
         cache_key TEXT, cached INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT,
         heartbeat_at TEXT, finished_at TEXT, error TEXT, usage_in INTEGER NOT NULL DEFAULT 0,
         usage_out INTEGER NOT NULL DEFAULT 0, model TEXT, quota_day TEXT)""",
    "CREATE INDEX IF NOT EXISTS runs_state ON runs (state, created_at)",
    "CREATE INDEX IF NOT EXISTS runs_member ON runs (member, state)",
    """CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
         data TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (run_id, seq))""",
    """CREATE TABLE IF NOT EXISTS quota (member TEXT NOT NULL, day TEXT NOT NULL, questions INTEGER NOT NULL DEFAULT 0,
         tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (member, day))""",
    """CREATE TABLE IF NOT EXISTS runner (name TEXT PRIMARY KEY, last_seen REAL NOT NULL, status TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS cache (cache_key TEXT PRIMARY KEY, created REAL NOT NULL, events TEXT NOT NULL,
         hits INTEGER NOT NULL DEFAULT 0)""",
]


class QuotaExceeded(RuntimeError):
    pass


class Busy(RuntimeError):
    """The member already has a question in progress."""


def path():
    return sconfig.DATA_DIR / "assistant.db"


def connect(p=None):
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=3, check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=3000")
    for s in SCHEMA:
        con.execute(s)
    return con


@contextmanager
def opened(p=None):
    con = connect(p)
    try:
        yield con
    finally:
        con.close()


def utc_day(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def cache_key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:40]


# --------------------------------------------------------------------------------------------------------- quota
def quota(con, member, day=None):
    day = day or utc_day()
    r = con.execute("SELECT questions, tokens FROM quota WHERE member=? AND day=?", (member, day)).fetchone()
    lim = sconfig_limits()
    q, t = (r["questions"], r["tokens"]) if r else (0, 0)
    return {"day": day, "questions_used": q, "questions_limit": lim["daily_questions"], "tokens_used": t,
            "tokens_limit": lim["daily_tokens"], "remaining_questions": max(0, lim["daily_questions"] - q)}


def sconfig_limits():
    from . import config
    return config.limits()


def _reserve(con, member, day):
    lim = sconfig_limits()
    con.execute("INSERT OR IGNORE INTO quota (member, day) VALUES (?, ?)", (member, day))
    cur = con.execute("UPDATE quota SET questions = questions + 1 WHERE member=? AND day=? AND questions < ? AND "
                      "tokens < ?", (member, day, lim["daily_questions"], lim["daily_tokens"]))
    if cur.rowcount != 1:
        raise QuotaExceeded("daily assistant limit reached")


# ---------------------------------------------------------------------------------------------------------- runs
def create_run(con, member, league, question, page_context=None, key=None):
    """Enqueue one question (reserves one question of the member's daily quota; refuses a second concurrent run)."""
    day = utc_day()
    rid = "ar-" + uuid.uuid4().hex[:20]
    con.execute("BEGIN IMMEDIATE")
    try:
        if con.execute("SELECT 1 FROM runs WHERE member=? AND state IN ('queued','running')", (member,)).fetchone():
            raise Busy("a question is already in progress")
        _reserve(con, member, day)
        con.execute("INSERT INTO runs (run_id, member, league, question, page_context, state, cache_key, created_at, "
                    "quota_day) VALUES (?,?,?,?,?,?,?,?,?)",
                    (rid, member, league, question, page_context, "queued", key, now_utc(), day))
        _append(con, rid, "status", {"state": "queued"})
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return rid


def create_cached_run(con, member, league, question, key, events):
    rid = "ar-" + uuid.uuid4().hex[:20]
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("INSERT INTO runs (run_id, member, league, question, state, cache_key, cached, created_at, "
                    "finished_at) VALUES (?,?,?,?,?,?,1,?,?)", (rid, member, league, question, "done", key, now_utc(),
                                                               now_utc()))
        for e in events:
            data = dict(e["data"])
            if e["type"] == "final":
                data["cached"] = True
            _append(con, rid, e["type"], data)
        con.execute("UPDATE cache SET hits = hits + 1 WHERE cache_key=?", (key,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return rid


def get_run(con, run_id):
    r = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def _append(con, run_id, typ, data):
    con.execute("INSERT INTO events (run_id, seq, type, data, at) SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM "
                "events WHERE run_id=?", (run_id, typ, json.dumps(data, default=str), now_utc(), run_id))
    return con.execute("SELECT MAX(seq) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]


def append(con, run_id, typ, data):
    con.execute("BEGIN IMMEDIATE")
    try:
        seq = _append(con, run_id, typ, data)
        con.execute("UPDATE runs SET heartbeat_at=? WHERE run_id=?", (now_utc(), run_id))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return seq


def events_after(con, run_id, after=0, limit=200):
    rows = con.execute("SELECT seq, type, data, at FROM events WHERE run_id=? AND seq > ? ORDER BY seq LIMIT ?",
                       (run_id, int(after), int(limit))).fetchall()
    return [{"seq": r["seq"], "type": r["type"], "data": json.loads(r["data"]), "at": r["at"]} for r in rows]


def request_cancel(con, run_id):
    """Queued -> cancelled at once; running -> flag the runner polls on every stream event and between tools."""
    con.execute("BEGIN IMMEDIATE")
    try:
        r = con.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if r is None:
            con.execute("ROLLBACK")
            return None
        if r["state"] == "queued":
            con.execute("UPDATE runs SET state='cancelled', cancel_requested=1, finished_at=? WHERE run_id=?",
                        (now_utc(), run_id))
            _append(con, run_id, "cancelled", {"by": "member", "before_start": True})
            _refund(con, run_id)
        elif r["state"] == "running":
            con.execute("UPDATE runs SET cancel_requested=1 WHERE run_id=?", (run_id,))
        con.execute("COMMIT")
        return r["state"]
    except Exception:
        con.execute("ROLLBACK")
        raise


def cancel_requested(con, run_id):
    r = con.execute("SELECT cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return bool(r and r[0])


def claim(con):
    """The runner takes the oldest queued run (atomic: two runners never take the same run)."""
    con.execute("BEGIN IMMEDIATE")
    try:
        r = con.execute("SELECT run_id FROM runs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if r is None:
            con.execute("COMMIT")
            return None
        con.execute("UPDATE runs SET state='running', started_at=?, heartbeat_at=? WHERE run_id=? AND state='queued'",
                    (now_utc(), now_utc(), r["run_id"]))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return get_run(con, r["run_id"])


def _refund(con, run_id):
    r = con.execute("SELECT member, quota_day, cached FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if r and r["quota_day"] and not r["cached"]:
        con.execute("UPDATE quota SET questions = MAX(0, questions - 1) WHERE member=? AND day=?",
                    (r["member"], r["quota_day"]))


def finish(con, run_id, state, usage=None, error=None, model=None, refund=False):
    usage = usage or {}
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("UPDATE runs SET state=?, finished_at=?, error=?, usage_in=?, usage_out=?, model=? WHERE run_id=?",
                    (state, now_utc(), error, usage.get("input_tokens", 0), usage.get("output_tokens", 0), model,
                     run_id))
        r = con.execute("SELECT member, quota_day FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if r and r["quota_day"]:
            con.execute("UPDATE quota SET tokens = tokens + ? WHERE member=? AND day=?",
                        (usage.get("input_tokens", 0) + usage.get("output_tokens", 0), r["member"], r["quota_day"]))
        if refund:
            _refund(con, run_id)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def fail_stale(con, timeout_s):
    """A run the runner stopped heart-beating (crash, restart) is failed with a final event and its question refunded
    - never silently left 'running', never re-generated behind the member's back."""
    cutoff = datetime.fromtimestamp(time.time() - timeout_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = [r["run_id"] for r in con.execute("SELECT run_id FROM runs WHERE state='running' AND "
                                              "COALESCE(heartbeat_at, started_at) < ?", (cutoff,)).fetchall()]
    for rid in stale:
        append(con, rid, "error", {"code": "runner_interrupted", "message": "The answer was interrupted. Ask again."})
        finish(con, rid, "failed", error="runner_interrupted", refund=True)
    return stale


# --------------------------------------------------------------------------------------------------------- cache
def get_cache(con, key, ttl_s):
    r = con.execute("SELECT created, events FROM cache WHERE cache_key=?", (key,)).fetchone()
    if r is None or time.time() - r["created"] > ttl_s:
        return None
    return json.loads(r["events"])


def put_cache(con, key, events):
    con.execute("INSERT OR REPLACE INTO cache (cache_key, created, events, hits) VALUES (?,?,?,0)",
                (key, time.time(), json.dumps(events, default=str)))


def purge(con, days=RETAIN_DAYS):
    cutoff = datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = [r[0] for r in con.execute("SELECT run_id FROM runs WHERE created_at < ? AND state IN "
                                     "('done','failed','cancelled')", (cutoff,)).fetchall()]
    for rid in old:
        con.execute("DELETE FROM events WHERE run_id=?", (rid,))
        con.execute("DELETE FROM runs WHERE run_id=?", (rid,))
    con.execute("DELETE FROM cache WHERE created < ?", (time.time() - 86400,))
    con.execute("DELETE FROM quota WHERE day < ?", (cutoff[:10],))
    return len(old)


# -------------------------------------------------------------------------------------------------------- runner
def runner_beat(con, status, name="assistant"):
    """The runner says it is alive (and whether it is enabled). The web process reads this instead of the key."""
    con.execute("INSERT OR REPLACE INTO runner (name, last_seen, status) VALUES (?,?,?)",
                (name, time.time(), json.dumps(status, default=str)))


def runner_seen(con, max_age_s=120, name="assistant"):
    r = con.execute("SELECT last_seen, status FROM runner WHERE name=?", (name,)).fetchone()
    if r is None:
        return {"seen": False, "age_s": None, "status": None, "alive": False}
    age = time.time() - r["last_seen"]
    st = json.loads(r["status"])
    return {"seen": True, "age_s": round(age, 1), "status": st, "alive": age <= max_age_s and bool(st.get("enabled"))}


def queue_counts(con):
    return {r["state"]: r["n"] for r in con.execute("SELECT state, COUNT(*) n FROM runs GROUP BY state").fetchall()}
