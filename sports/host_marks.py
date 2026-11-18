"""One pacing mark and one rate-limit hold per exchange host for EVERY process on the box.

net.py's request gap and Retry-After holds live in one process, but several scheduler children ask the same host from
the same address: ingest-kalshi and the soccer / tennis quotes jobs (Kalshi), the NBA / NFL ingest and the tennis
quotes job (Polymarket). This file is the part they share: <SPORTS_DATA_DIR>/request-marks.db, one row per host with
the time of the last reserved send and the time before which nobody sends. Request metadata only - no quote, player,
result or model data - so the soccer / tennis stores stay isolated.

Never a reason for a request to fail: an unreadable or locked file means the caller falls back to its own process's
pacing. Off switch: SPORTS_SHARED_HOLDS=0.
"""
import os
import sqlite3
import time

from . import config

HOSTS = frozenset({"api.elections.kalshi.com", "gamma-api.polymarket.com"})
HOLD_429_S = 15 * 60      # a 429 with no Retry-After: nobody asks that host again for this long
MAX_QUEUE_S = 30          # a stored send time further ahead than this is a clock change, not a queue


def _open():
    if os.environ.get("SPORTS_SHARED_HOLDS") == "0":
        return None
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DATA_DIR / "request-marks.db", timeout=3, isolation_level=None)
    con.execute("CREATE TABLE IF NOT EXISTS marks (host TEXT PRIMARY KEY, sent_at REAL NOT NULL DEFAULT 0,"
                " hold_until REAL NOT NULL DEFAULT 0)")
    return con


def reserve(host, gap, delay=0.0):
    """Take the next send slot on `host`: at least `gap` seconds after the slot any process took last, and no sooner
    than `delay` seconds from now. -> seconds to sleep before sending (`delay` when the file cannot be used)."""
    con = None
    try:
        con = _open()
        if con is None:
            return delay
        con.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = con.execute("SELECT sent_at FROM marks WHERE host=?", (host,)).fetchone()
        last = row[0] if row and row[0] <= now + MAX_QUEUE_S else 0.0
        at = max(now + delay, last + gap)
        con.execute("INSERT INTO marks(host,sent_at) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET sent_at=excluded.sent_at",
                    (host, at))
        con.execute("COMMIT")
        return at - now
    except (sqlite3.Error, OSError):
        return delay
    finally:
        if con is not None:
            con.close()


def hold(host, until):
    """Nobody on the box sends to `host` before epoch `until`. Never shortens a hold."""
    con = None
    try:
        con = _open()
        if con is not None:
            con.execute("INSERT INTO marks(host,hold_until) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET "
                        "hold_until=max(marks.hold_until,excluded.hold_until)", (host, float(until)))
    except (sqlite3.Error, OSError):
        pass
    finally:
        if con is not None:
            con.close()


def held_until(host):
    """Epoch seconds before which no process sends to `host` (0.0: no hold, or the file cannot be read)."""
    con = None
    try:
        con = _open()
        row = con.execute("SELECT hold_until FROM marks WHERE host=?", (host,)).fetchone() if con is not None else None
        return float(row[0]) if row else 0.0
    except (sqlite3.Error, OSError):
        return 0.0
    finally:
        if con is not None:
            con.close()
