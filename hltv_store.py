"""
hltv_store.py - the last good answer of every HLTV lookup the grader uses, kept on the data disk.

Owner 2026-10-11: "keep all the hltv data stored so these outages won't affect anything". Every successful HLTV read
(player maps and opponent maps for the performance model, player numbers, match context, recent series, schedule,
events, countries, team ranks, map pool, veto predictions, the player index, Maps 1-3 records) is saved here. When the
HLTV database is unreachable or times out, the grader uses the saved answer instead - the same numbers it had before the
blip - and it survives restarts and deploys (the disk is mounted at CS2_DATA_DIR on Render).

- save(key, value): pickled + zlib, one row per lookup key; the same key is rewritten at most every MIN_REWRITE seconds.
- load(key): (saved_at, value) or None; answers older than MAX_AGE_DAYS are not used (treated as missing).
- Never fatal: a locked / full disk only means an answer isn't saved.
- Thread-safe: one SQLite connection per thread, WAL (readers never wait on the writer).
Only this server writes the file, so unpickling it is safe.
"""
import hashlib
import os
import pickle
import re
import sqlite3
import sys
import threading
import time
import zlib
from pathlib import Path

DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
DB_PATH = DATA_DIR / ".cache" / "hltv_store.db"
MAX_AGE_DAYS = 7          # a stored answer older than this is not used (HLTV's scraper writes every ~2 h)
MIN_REWRITE = 300         # seconds: the same key is not rewritten more often (the in-memory caches refresh every 15 min)
_local = threading.local()
_WRITE = threading.Lock()
_LAST = {}                # key hash -> last write time (this process)
STATS = {"writes": 0, "hits": 0, "misses": 0, "last_hit_at": 0.0, "errors": 0}
_warned = [False]


def _con():
    con = getattr(_local, "con", None)
    if con is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("CREATE TABLE IF NOT EXISTS store (k TEXT PRIMARY KEY, at REAL NOT NULL, data BLOB NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS store_age ON store (at)")
        _local.con = con
    return con


def _k(key):
    return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
WINDOW_KINDS = {"series"}  # read with several windows: his series for the 365-day last-10 and the 90/180/365-day stake check


def _days_back(s):
    """Days from the window start s (YYYY-MM-DD...) back from today (UTC); None when unreadable."""
    from datetime import date, datetime, timezone
    try:
        return (datetime.fromtimestamp(time.time(), timezone.utc).date() - date.fromisoformat(s[:10])).days
    except ValueError:
        return None


def loose(key):
    """The key without its date / map parts: ("p", nick, team, "2025-08-17") -> ("p", nick, team). Exact keys change at UTC
    midnight (the 420-day window start) and when a veto fixes the maps; the loose key still finds the last good answer for
    the same player / team / opponent, so an outage after midnight or after the veto is covered too. A WINDOW_KINDS key keeps
    its window length in place of the date (("series", nick, team, ("days", 366))): the same window a day later is the same
    query, another window is not (2026-10-13 audit: after midnight the 365-day read was served the 90-day answer)."""
    if not isinstance(key, tuple):
        return None
    win = bool(key) and key[0] in WINDOW_KINDS
    lk = []
    for x in key:
        if isinstance(x, (tuple, list, set, frozenset)):
            continue
        if isinstance(x, str) and _DATE.match(x):
            if win:
                lk.append(("days", _days_back(x)))
            continue
        lk.append(x)
    lk = tuple(lk)
    return ("~loose",) + lk if lk != key else None


def save(key, value):
    k = _k(key)
    now = time.time()
    if now - _LAST.get(k, 0) < MIN_REWRITE:
        return
    try:
        blob = zlib.compress(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), 6)
        lk = loose(key)
        with _WRITE:
            con = _con()
            con.execute("INSERT OR REPLACE INTO store (k, at, data) VALUES (?, ?, ?)", (k, now, blob))
            if lk:
                con.execute("INSERT OR REPLACE INTO store (k, at, data) VALUES (?, ?, ?)", (_k(lk), now, blob))
            con.commit()
        _LAST[k] = now
        if len(_LAST) > 50000:
            _LAST.clear()
        STATS["writes"] += 1
    except Exception as e:
        STATS["errors"] += 1
        if not _warned[0]:
            print(f"hltv_store write failed ({type(e).__name__}) - continuing without saving", file=sys.stderr, flush=True)
            _warned[0] = True


def load(key, max_age_days=MAX_AGE_DAYS):
    """(saved_at, value) of the last good answer for this key, or None."""
    try:
        con = _con()
        row = con.execute("SELECT at, data FROM store WHERE k = ?", (_k(key),)).fetchone()
        if not row and loose(key):              # same player / team / opponent under another date or map set
            row = con.execute("SELECT at, data FROM store WHERE k = ?", (_k(loose(key)),)).fetchone()
        if row and time.time() - row[0] <= max_age_days * 86400:
            STATS["hits"] += 1
            STATS["last_hit_at"] = time.time()
            return row[0], pickle.loads(zlib.decompress(row[1]))
    except Exception:
        STATS["errors"] += 1
    STATS["misses"] += 1
    return None


def prune(older_than_days=MAX_AGE_DAYS * 2):
    try:
        with _WRITE:
            con = _con()
            n = con.execute("DELETE FROM store WHERE at < ?", (time.time() - older_than_days * 86400,)).rowcount
            con.commit()
            return n
    except sqlite3.Error:
        return 0


def report():
    try:
        n, size, oldest = _con().execute("SELECT count(*), coalesce(sum(length(data)), 0), min(at) FROM store").fetchone()
    except sqlite3.Error:
        n = size = 0
        oldest = None
    return {"entries": n, "data_mb": round(size / 1e6, 1), "oldest_h": round((time.time() - oldest) / 3600, 1) if oldest else None,
            "writes": STATS["writes"], "served_during_outages": STATS["hits"], "missing_during_outages": STATS["misses"],
            "last_served_s_ago": round(time.time() - STATS["last_hit_at"]) if STATS["last_hit_at"] else None, "errors": STATS["errors"]}
