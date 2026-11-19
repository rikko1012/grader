"""Soccer / tennis store upkeep (drain-1119): lossless, bounded, run by the sport's own history job under its lock.

Every isolated competition store (<expansion>/<sport>/<competition>/canonical.db) uses the shared NBA / NFL schema
(store.MIGRATIONS, additive only - unchanged). Three of its indexes are never used by these stores:
  records_ident          exact duplicate of records_ident_rev (kind, source, source_key, revision) - dropped only
                         where records_ident_rev exists (it is skipped when duplicate revisions block it)
  records_current_stat   NBA / NFL per-market offer lists (kind, league, stat, ...) WHERE late = 0
  records_current_league NBA / NFL league-wide current listings
Measured on a 143 MB tennis store (atp_challenger, 69.7k rows): 7.8 + 9.0 + 8.2 MB = 19.6 % of the vacuumed file;
VACUUM removes another ~10 % of page fragmentation (random text-key inserts). records_league is kept (the store
guard in expansion.isolated_database reads it on every write), the offer-only partial indexes cost nothing here.

tidy(): per file, largest first, once (meta 'expansion_tidy'): DROP the three indexes, auto_vacuum = INCREMENTAL,
VACUUM in place, WAL checkpoint. In WAL mode VACUUM is one write transaction - web readers keep reading their
snapshot (tested: no reader blocked); the only writer of these files is the history job itself, which runs this.
A file is skipped (left for a later run) when its estimated time does not fit the budget, or when the disk could not
hold its temporary WAL (~ the file's size) with a margin. The storage BUDGET is deliberately not a reason to skip: a
store over the budget is exactly the one that must shrink (the earlier 1.4x-budget guard skipped every file once a
sport sat there, so it could never shrink); the WAL is transient and checkpointed away (TRUNCATE, journal_size_limit
0) before the job ends - the history job is the only writer, and it checks the budget only before its own writes.
Every read query keeps its index (EXPLAIN-tested).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

REDUNDANT = ("records_ident", "records_current_stat", "records_current_league")
MARK = "expansion_tidy"
VERSION = "tidy-1"
VACUUM_MB_S = float(os.environ.get("SCOUT_EXPANSION_VACUUM_MB_S", "8"))   # conservative (HDD measured 20-35 MB/s)
DISK_FLOOR_MB = 1024           # free disk kept beyond a VACUUM's temporary WAL


def _size(path):
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(str(path) + suffix)
        except OSError:
            pass
    return total


def _indexes(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='records'")}


def is_tidy(con):
    try:
        row = con.execute("SELECT v FROM meta WHERE k = ?", (MARK,)).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def tidy_file(path):
    """Drop the redundant indexes, switch to incremental auto-vacuum and VACUUM one store. -> report dict."""
    path = Path(path)
    before = _size(path)
    t0 = time.monotonic()
    con = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    try:
        con.execute("PRAGMA busy_timeout=5000")
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone():
            return {"file": path.parent.name, "skipped": "no records table"}
        have = _indexes(con)
        dropped = []
        for ix in REDUNDANT:
            if ix not in have:
                continue
            if ix == "records_ident" and "records_ident_rev" not in have:
                continue                                # the only (kind, source, source_key, revision) index: kept
            con.execute(f"DROP INDEX {ix}")
            dropped.append(ix)
        con.execute("PRAGMA auto_vacuum = INCREMENTAL")
        con.execute("VACUUM")
        con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (MARK, VERSION))
        _shrink_wal(con)
    finally:
        con.close()
    return {"file": path.parent.name, "dropped": dropped, "mb_before": round(before / 1048576, 2),
            "mb_after": round(_size(path) / 1048576, 2), "seconds": round(time.monotonic() - t0, 2)}


def _shrink_wal(con, tries=3):
    """Checkpoint the VACUUM's WAL away (it holds ~ the whole file): TRUNCATE, retried briefly while a web reader
    still reads an older snapshot; journal_size_limit 0 makes any later checkpoint on this connection truncate too."""
    try:
        con.execute("PRAGMA journal_size_limit = 0").fetchall()
    except sqlite3.Error:
        pass
    for i in range(tries):
        try:
            busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if not busy or not busy[0]:
                return True
        except sqlite3.Error:
            pass
        time.sleep(0.2 * (i + 1))
    return False


def pending(directory):
    """Untidied stores of one sport: [(bytes, path)], largest first (a read-only meta probe per file)."""
    todo = []
    for p in Path(directory).glob("*/canonical.db"):
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
            try:
                done = is_tidy(con)
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if not done:
            todo.append((_size(p), p))
    todo.sort(key=lambda x: -x[0])
    return todo


def release(path, max_pages=20000):
    """Give freed pages back to the file system (auto_vacuum = INCREMENTAL stores only; bounded). -> pages freed."""
    con = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    try:
        con.execute("PRAGMA busy_timeout=5000")
        if con.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
            return 0
        free = con.execute("PRAGMA freelist_count").fetchone()[0]
        if free:
            con.execute(f"PRAGMA incremental_vacuum({int(min(free, max_pages))})").fetchall()   # one page per step
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        return min(free, max_pages)
    finally:
        con.close()


def tidy(directory, budget_s, free_bytes=None):
    """Tidy this sport's untidied stores, largest first, inside budget_s seconds. free_bytes(): free disk bytes
    (default: the store's file system) - a file needs ~ its own size free for the VACUUM's WAL, plus DISK_FLOOR_MB.
    -> {"files": [reports], "pending": untidied stores left, "skipped", "seconds"}."""
    t0 = time.monotonic()
    out = {"files": [], "pending": 0, "skipped": []}
    if free_bytes is None:
        def free_bytes():
            return shutil.disk_usage(str(directory)).free
    todo = pending(directory)
    for size, p in todo:
        left = budget_s - (time.monotonic() - t0)
        if size / 1048576 / VACUUM_MB_S > left:
            out["skipped"].append({"file": p.parent.name, "reason": "time", "mb": round(size / 1048576, 1)})
            continue
        try:
            free = free_bytes()
        except OSError:
            free = None
        if free is not None and free < 2 * size + DISK_FLOOR_MB * 1048576:
            out["skipped"].append({"file": p.parent.name, "reason": "disk", "mb": round(size / 1048576, 1)})
            continue
        try:
            out["files"].append(tidy_file(p))
        except sqlite3.Error as e:
            out["skipped"].append({"file": p.parent.name, "reason": type(e).__name__})
    out["pending"] = len(todo) - len(out["files"])
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out
