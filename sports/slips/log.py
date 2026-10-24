"""Slip records: recommendations ONCE per batch, loaded / copied slips separately, slip settlements with revisions.

Write path (the service handlers never write the canonical store - ROUTE_CONTRACT):
  1. handler -> spool(kind, entry): a BOUNDED in-memory queue (SPOOL_QUEUE entries; full = the entry is dropped and
     counted, never blocking a request). Batches are de-duplicated in memory (a cached build answered to many members
     is spooled once).
  2. one writer thread appends JSON lines to <SPORTS_DATA_DIR>/slips_spool/<UTC date>.jsonl (append-only; a day file
     stops growing at SPOOL_DAY_MAX_MB, dropped entries counted). Nothing else is written by the service.
  3. the WORKER (python -m sports.slips ingest) loads the spool into <SPORTS_DATA_DIR>/slips.db - idempotent
     (INSERT OR IGNORE on batch_id / event_id), resumable (per-file byte offsets in meta).
  4. the WORKER (python -m sports.slips settle) settles every logged slip from the canonical leg settlements and stores a
     new slip-settlement revision only when the result changed (a corrected leg result supersedes, the old row stays).

Each batch row keeps: builder / policy / rules versions, settings, snapshot id + time, every slip with every leg's
offer record id, forecast id, model version, probabilities, the payout schedule shown (payout assumptions) and the
INTERNAL expectation (joint.expectation - never served to members). Loaded / copied rows keep the slip as the member
had it, the check result and the pseudonymous member id hashed again (sha256 prefix).
"""
from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import config

SPOOL_QUEUE = 2000
SPOOL_DAY_MAX_MB = 50
KINDS = ("batch", "loaded", "copied")
STATS = {"spooled": 0, "dropped_queue_full": 0, "dropped_day_cap": 0, "duplicate_batches": 0, "written": 0,
         "write_errors": 0}
_Q = queue.Queue(maxsize=SPOOL_QUEUE)
_SEEN = {}
_SEEN_MAX = 5000
_LOCK = threading.Lock()
_WRITER = None


def spool_dir() -> Path:
    return config.DATA_DIR / "slips_spool"


def db_path() -> Path:
    return config.DATA_DIR / "slips.db"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def member_hash(member):
    return hashlib.sha256(f"slips|{member}".encode()).hexdigest()[:16] if member else None


def spool(kind, entry):
    """Queue one record for the spool file. Never blocks; never raises. -> True if queued."""
    if kind not in KINDS:
        raise ValueError(kind)
    if kind == "batch":
        bid = entry.get("batch_id")
        with _LOCK:
            if bid in _SEEN:
                STATS["duplicate_batches"] += 1
                return False
            if len(_SEEN) >= _SEEN_MAX:
                _SEEN.clear()
            _SEEN[bid] = 1
    rec = {"kind": kind, "logged_at": _now(), **entry}
    rec.setdefault("event_id", "ev-" + hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode())
                   .hexdigest()[:20])
    try:
        _Q.put_nowait(rec)
    except queue.Full:
        with _LOCK:
            STATS["dropped_queue_full"] += 1
        return False
    with _LOCK:
        STATS["spooled"] += 1
    _ensure_writer()
    return True


def _ensure_writer():
    global _WRITER
    with _LOCK:
        if _WRITER is None or not _WRITER.is_alive():
            _WRITER = threading.Thread(target=_write_loop, name="slips-spool", daemon=True)
            _WRITER.start()


def _write_loop():
    while True:
        rec = _Q.get()
        batch = [rec]
        while len(batch) < 200:
            try:
                batch.append(_Q.get_nowait())
            except queue.Empty:
                break
        try:
            flush(batch)
        except Exception:                               # noqa: BLE001 - logging never breaks the service
            with _LOCK:
                STATS["write_errors"] += len(batch)
        finally:
            for _ in batch:
                _Q.task_done()


def flush(batch):
    d = spool_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
    size = f.stat().st_size if f.exists() else 0
    lines = []
    for r in batch:
        s = json.dumps(r, separators=(",", ":"), default=str) + "\n"
        if size + len(s) > SPOOL_DAY_MAX_MB * 1e6:
            with _LOCK:
                STATS["dropped_day_cap"] += 1
            continue
        size += len(s)
        lines.append(s)
    if lines:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("".join(lines))
        with _LOCK:
            STATS["written"] += len(lines)


def drain(timeout=5.0):
    """Tests / shutdown: wait until the queue is written."""
    end = time.time() + timeout
    while time.time() < end:
        if _Q.unfinished_tasks == 0:
            return True
        time.sleep(0.02)
    return False


def stats():
    with _LOCK:
        return dict(STATS, queue=_Q.qsize())


# ------------------------------------------------------------------------------------------- worker side: slips.db
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, logged_at TEXT NOT NULL, league TEXT,
         platform TEXT, builder_version TEXT, policy_version TEXT, snapshot_id TEXT, payload TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS slips (slip_key TEXT PRIMARY KEY, slip_id TEXT NOT NULL, source TEXT NOT NULL,
         source_id TEXT NOT NULL, logged_at TEXT NOT NULL, platform TEXT, entry_type TEXT, league TEXT,
         payload TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, logged_at TEXT NOT NULL,
         member TEXT, slip_id TEXT, ok INTEGER, payload TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS settlements (slip_key TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
         payout_multiple REAL, settled_at TEXT NOT NULL, supersedes INTEGER, payload TEXT NOT NULL,
         PRIMARY KEY (slip_key, revision))""",
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
    # immutability: recommendations, events and settlements are never rewritten
    "CREATE TRIGGER IF NOT EXISTS batches_ro BEFORE UPDATE ON batches BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS slips_ro BEFORE UPDATE ON slips BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS events_ro BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS settle_ro BEFORE UPDATE ON settlements BEGIN SELECT RAISE(ABORT, 'immutable'); END",
]


def connect(path=None):
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    for s in SCHEMA:
        con.execute(s)
    con.commit()
    return con


def _slip_rows(con, source, source_id, logged_at, league, slips):
    for s in slips:
        key = f"{source}:{source_id}:{s['slip_id']}"
        con.execute("INSERT OR IGNORE INTO slips VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, s["slip_id"], source, source_id, logged_at, s.get("platform"), s.get("entry_type"), league,
                     json.dumps(s, default=str)))


def ingest(con=None, directory=None):
    """Load every spool line not loaded yet. -> counts. Idempotent and resumable (byte offset per file)."""
    own = con is None
    con = con or connect()
    d = Path(directory) if directory else spool_dir()
    c = {"lines": 0, "batches": 0, "events": 0, "bad": 0}
    try:
        for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
            row = con.execute("SELECT v FROM meta WHERE k=?", (f"offset:{f.name}",)).fetchone()
            off = int(row[0]) if row else 0
            with open(f, "rb") as fh:
                fh.seek(off)
                data = fh.read()
            end = data.rfind(b"\n") + 1                 # only complete lines
            for raw in data[:end].splitlines():
                c["lines"] += 1
                try:
                    r = json.loads(raw)
                except ValueError:
                    c["bad"] += 1
                    continue
                if r.get("kind") == "batch":
                    cur = con.execute("INSERT OR IGNORE INTO batches VALUES (?,?,?,?,?,?,?,?)",
                                      (r["batch_id"], r["logged_at"], r.get("league"), r.get("platform"),
                                       r.get("builder_version"), r.get("policy_version"), r.get("snapshot_id"),
                                       json.dumps(r, default=str)))
                    c["batches"] += cur.rowcount
                    _slip_rows(con, "batch", r["batch_id"], r["logged_at"], r.get("league"), r.get("slips") or [])
                elif r.get("kind") in ("loaded", "copied"):
                    cur = con.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                                      (r["event_id"], r["kind"], r["logged_at"], r.get("member"),
                                       (r.get("slip") or {}).get("slip_id"), 1 if r.get("ok") else 0,
                                       json.dumps(r, default=str)))
                    c["events"] += cur.rowcount
                    if r.get("kind") == "copied" and r.get("ok") and r.get("slip"):
                        _slip_rows(con, "copied", r["event_id"], r["logged_at"], r.get("league"), [r["slip"]])
                else:
                    c["bad"] += 1
            con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"offset:{f.name}", str(off + end)))
            con.commit()
    finally:
        if own:
            con.close()
    return c


def latest_settlement(con, slip_key):
    return con.execute("SELECT * FROM settlements WHERE slip_key=? ORDER BY revision DESC LIMIT 1",
                       (slip_key,)).fetchone()


def record_settlement(con, slip_key, result, settled_at=None):
    """Store a slip result; a different result than the latest = a new revision that names the one it supersedes.
    -> "inserted" | "unchanged" | "revision" """
    prev = latest_settlement(con, slip_key)
    core = {k: result.get(k) for k in ("status", "payout_multiple", "payout_known", "active", "removed", "losses",
                                       "reverted_to")}
    core["legs"] = [[x["status"], x["value"], x["revision"]] for x in result.get("leg_results") or []]
    core = json.dumps(core, sort_keys=True, default=str)
    if prev is not None and json.loads(prev["payload"]).get("_core") == core:
        return "unchanged"
    rev = 1 if prev is None else prev["revision"] + 1
    payload = dict(result, _core=core)
    con.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?,?)",
                (slip_key, rev, result["status"], result.get("payout_multiple"), settled_at or _now(),
                 prev["revision"] if prev is not None else None, json.dumps(payload, default=str)))
    con.commit()
    return "inserted" if prev is None else "revision"


def settle_all(store_con, con=None):
    """Worker job: settle every logged slip whose result is not final-and-unchanged. -> counts."""
    from . import settle
    own = con is None
    con = con or connect()
    c = {"slips": 0, "pending": 0, "inserted": 0, "revision": 0, "unchanged": 0}
    try:
        for row in con.execute("SELECT slip_key, payload FROM slips").fetchall():
            s = json.loads(row["payload"])
            c["slips"] += 1
            res = settle.settle_slip(s, settle.current_settlements(store_con, s["legs"]))
            if res["status"] == "pending":
                c["pending"] += 1
                continue
            c[record_settlement(con, row["slip_key"], res)] += 1
    finally:
        if own:
            con.close()
    return c
