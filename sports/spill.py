"""A throwaway on-disk SQLite file for one season-wide build (2026-11-08 review): the public Results file
(sports/results_public.py) and the results ledger (sports/snapshots.write_results) read every settled forecast of the
season. Holding those rows in Python grew ~0.6 MB (Results) / ~2 MB (ledger) per game, so both jobs would be killed over
their memory caps partway through a season. Rows now go to this file as they stream, the per-key choices and orderings
run in SQL, and the Python side keeps only per-game numbers.

The file lives in <DATA_DIR>/tmp (the service's persistent disk, ~95 GB free), is opened without a journal or fsync
(it is rebuilt from the store on every run) and is deleted when the build ends; a file left by a killed run is deleted
by the next run once it is older than STALE_S. Never holds anything that is not in the store."""
from __future__ import annotations

import os
import pickle
import sqlite3
import time
import uuid

from . import config

STALE_S = 6 * 3600
CACHE_KB = 2048                                          # SQLite page cache per spill file (memory stays flat)


def _dir():
    d = config.DATA_DIR / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep(prefix):
    """Delete spill files of this prefix left by killed runs (older than STALE_S)."""
    now = time.time()
    try:
        for p in _dir().glob(f"{prefix}-*.db*"):
            try:
                if now - p.stat().st_mtime > STALE_S:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


class Spill:
    """with Spill("results-public") as sp: sp.con is a private SQLite connection on a fresh file."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.path = None
        self.con = None

    def __enter__(self):
        _sweep(self.prefix)
        self.path = _dir() / f"{self.prefix}-{os.getpid()}-{uuid.uuid4().hex[:8]}.db"
        self.con = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        for pragma in ("journal_mode=OFF", "synchronous=OFF", f"cache_size=-{CACHE_KB}", "temp_store=FILE"):
            self.con.execute(f"PRAGMA {pragma}")
        self.con.execute("BEGIN")
        return self

    def __exit__(self, *exc):
        try:
            if self.con is not None:
                self.con.close()
        finally:
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    os.unlink(str(self.path) + suffix)
                except OSError:
                    pass
        return False

    def execute(self, sql, params=()):
        return self.con.execute(sql, params)

    def executemany(self, sql, rows):
        return self.con.executemany(sql, rows)


def pack(obj):
    """A row dict as bytes: pickle keeps every key in its order and every value exactly (floats bit for bit)."""
    return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)


def unpack(blob):
    return pickle.loads(blob)
