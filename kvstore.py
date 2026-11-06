#!/usr/bin/env python3
"""
kvstore.py - the API cache packed into one SQLite file on the volume.

Each cached answer used to be its own .json.gz file: ~50,000 files of ~1.5 KB each, and every file
takes at least one 4 KB disk block, so 95 MB of data occupied ~240 MB of the volume. Same data,
same keys, same expiry rules - one file, zlib-compressed rows, no per-file waste.

- get(bucket, name) -> (saved_at, data) or None;  put(bucket, name, data) -> True when saved
- A miss falls back to the old .json.gz file (if any), moves it in and deletes the file, so nothing
  is lost and nothing is re-downloaded; migrate() does the rest in the background.
- Never fatal: a locked/full disk only means an answer isn't saved (like the old files).
- Thread-safe: one connection per thread (WAL: readers never wait on the writer).
"""
import gzip
import json
import os
import sqlite3
import sys
import threading
import time
import zlib
from pathlib import Path

DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
DB_PATH = DATA_DIR / ".cache" / "cache.db"
LEGACY = {"bo3": DATA_DIR / ".cache" / "bo3", "games": DATA_DIR / ".cache" / "bo3" / "games",
          "misc": DATA_DIR / ".cache"}
_local = threading.local()
_WRITE = threading.Lock()
_warned = [False]


def _con():
    con = getattr(_local, "con", None)
    if con is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        con.execute("PRAGMA auto_vacuum=INCREMENTAL")          # takes effect on a new file
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        # plain rowid table: several entries per 4 KB page (WITHOUT ROWID would push every entry
        # over ~1 KB onto its own overflow pages - the same waste as one file per entry)
        con.execute("CREATE TABLE IF NOT EXISTS kv (bucket TEXT NOT NULL, name TEXT NOT NULL, at REAL NOT NULL, "
                    "data BLOB NOT NULL, UNIQUE (bucket, name))")
        con.execute("CREATE INDEX IF NOT EXISTS kv_age ON kv (bucket, at)")
        _local.con = con
    return con


def _pack(data):
    return zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 6)


def _legacy(bucket, name):
    """An old .json.gz file for this key: move it in (keeping its age) and delete it."""
    p = LEGACY[bucket] / (name + ".json.gz")
    try:
        at = p.stat().st_mtime
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, EOFError):
        return None
    if put(bucket, name, data, at):                # bug sweep 2026-11-06 #17: the file goes only once the row is saved
        try:
            p.unlink()
        except OSError:
            pass
    return at, data


def get(bucket, name):
    try:
        row = _con().execute("SELECT at, data FROM kv WHERE bucket = ? AND name = ?", (bucket, name)).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        try:
            return row[0], json.loads(zlib.decompress(row[1]))
        except (zlib.error, ValueError):
            return None
    return _legacy(bucket, name)


def put(bucket, name, data, at=None):
    try:
        blob = _pack(data)
        with _WRITE:
            con = _con()
            con.execute("INSERT OR REPLACE INTO kv (bucket, name, at, data) VALUES (?, ?, ?, ?)",
                        (bucket, name, at or time.time(), blob))
            con.commit()
        return True
    except (sqlite3.Error, TypeError, ValueError) as e:
        if not _warned[0]:
            print(f"cache write failed ({e}) - continuing without saving", file=sys.stderr, flush=True)
            _warned[0] = True
        return False


def prune(bucket, older_than_days):
    """Delete rows of `bucket` saved more than `older_than_days` ago; returns bytes freed (approx)."""
    try:
        with _WRITE:
            con = _con()
            cut = time.time() - older_than_days * 86400
            freed = con.execute("SELECT coalesce(sum(length(data)), 0) FROM kv WHERE bucket = ? AND at < ?",
                                (bucket, cut)).fetchone()[0]
            con.execute("DELETE FROM kv WHERE bucket = ? AND at < ?", (bucket, cut))
            con.commit()
            con.execute("PRAGMA incremental_vacuum")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return freed
    except sqlite3.Error:
        return 0


def migrate(batch=500, pause=0.05):
    """Move every old .json.gz cache file into the database (background, a batch at a time so
    member look-ups are never held up). Returns files moved."""
    moved = 0
    for bucket in ("games", "bo3", "misc"):
        folder = LEGACY[bucket]
        if not folder.exists():
            continue
        names = [p.name[:-8] for p in folder.iterdir() if p.is_file() and p.name.endswith(".json.gz")]
        for i, name in enumerate(names, 1):
            if _legacy(bucket, name):
                moved += 1
            if i % batch == 0:
                time.sleep(pause)
        for p in folder.iterdir():                     # half-written temp files of the old format
            if p.is_file() and p.suffix == ".tmp":
                try:
                    p.unlink()
                except OSError:
                    pass
    try:
        with _WRITE:
            _con().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    return moved


def report():
    try:
        n, size = _con().execute("SELECT count(*), coalesce(sum(length(data)), 0) FROM kv").fetchone()
    except sqlite3.Error:
        n = size = 0
    files = sum(p.stat().st_size for p in DB_PATH.parent.glob(DB_PATH.name + "*") if p.is_file())
    return {"entries": n, "data_mb": round(size / 1e6, 1), "db_file_mb": round(files / 1e6, 1)}
