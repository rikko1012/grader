"""The sports API answer cache: one SQLite file (config.CACHE_DB), zlib-compressed rows - the same idea as kvstore.py and
lol/cache.py, kept separate so sports never reads or writes another cache.

- get(bucket, name, max_age_s=None) -> data or None (None also when older than max_age_s)
- put(bucket, name, data)
- fetch(bucket, name, max_age_s, loader) -> cached data, else loader() saved and returned
Never fatal: a locked / full / read-only disk only means an answer is not saved. Thread-safe (one connection per thread).
"""
import json
import sqlite3
import sys
import threading
import time
import zlib

from . import config

_local = threading.local()
_WRITE = threading.Lock()
_warned = [False]


def _con():
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != config.CACHE_DB:
        config.CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(config.CACHE_DB, timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS kv (bucket TEXT NOT NULL, name TEXT NOT NULL, at REAL NOT NULL, "
                    "data BLOB NOT NULL, UNIQUE (bucket, name))")
        _local.con, _local.path = con, config.CACHE_DB
    return con


def get(bucket, name, max_age_s=None):
    try:
        row = _con().execute("SELECT at, data FROM kv WHERE bucket = ? AND name = ?", (bucket, name)).fetchone()
    except sqlite3.Error:
        return None
    if not row or (max_age_s is not None and time.time() - row[0] > max_age_s):
        return None
    try:
        return json.loads(zlib.decompress(row[1]))
    except (zlib.error, ValueError):
        return None


def put(bucket, name, data):
    try:
        blob = zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 6)
        with _WRITE:
            con = _con()
            con.execute("INSERT OR REPLACE INTO kv (bucket, name, at, data) VALUES (?, ?, ?, ?)",
                        (bucket, name, time.time(), blob))
            con.commit()
    except (sqlite3.Error, TypeError, ValueError, OSError) as e:
        if not _warned[0]:
            print(f"sports cache write failed ({e}) - continuing without saving", file=sys.stderr, flush=True)
            _warned[0] = True


def fetch(bucket, name, max_age_s, loader, refresh=False):
    if not refresh:
        hit = get(bucket, name, max_age_s)
        if hit is not None:
            return hit
    data = loader()
    put(bucket, name, data)
    return data
