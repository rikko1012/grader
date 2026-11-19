"""Persist provider Retry-After across the separate new-sport job processes.

Only transport cooldowns are shared; no player, result, quote or model data.
"""
import os
import sqlite3
import time
from urllib.parse import urlsplit

from . import net

PP_HOST = 'partner-api.prizepicks.com'


def pace_gap_s(host):
    """Minimum seconds between two requests to `host` from ANY new-sport job process (soccer and tennis quotes).
    PrizePicks only (2026-11-19, per-league soccer tabs): SCOUT_PP_GAP_S, default 8; 0 turns it off."""
    if host != PP_HOST:
        return 0.0
    try:
        return max(0.0, float(os.environ.get('SCOUT_PP_GAP_S', '8')))
    except ValueError:
        return 8.0


def _path(sport):
    from .expansion_board import root
    path = root(sport).parent / 'request-holds.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def held_until(sport, url):
    """Epoch seconds of a shared Retry-After hold still in force on url's host, else None. No request, no wait."""
    host = urlsplit(url).hostname
    try:
        with sqlite3.connect(_path(sport), timeout=3) as con:
            con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
            row = con.execute('SELECT until FROM holds WHERE host=?', (host,)).fetchone()
    except sqlite3.Error:
        return None
    until = max(row[0] if row else 0.0, net.holds().get(host, 0.0))
    return until if until > time.time() else None


def _pace(con, host):
    """Reserve the next send slot for host across processes (same idea as net._wait, but shared through the holds
    db): read the last reserved slot, take max(now, last + gap), store it, then sleep until it."""
    gap = pace_gap_s(host)
    if not gap:
        return
    con.execute('CREATE TABLE IF NOT EXISTS pace (host TEXT PRIMARY KEY, at REAL NOT NULL)')
    con.execute('BEGIN IMMEDIATE')
    row = con.execute('SELECT at FROM pace WHERE host=?', (host,)).fetchone()
    now = time.time()
    last = row[0] if row and row[0] <= now + 4 * gap else None     # a slot far ahead = a clock jump: start afresh
    at = max(now, last + gap) if last is not None else now
    con.execute('INSERT INTO pace(host,at) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET at=excluded.at', (host, at))
    con.execute('COMMIT')
    if at > now:
        time.sleep(at - now)


def _held(sport, url, send):
    path = _path(sport)
    host = urlsplit(url).hostname
    with sqlite3.connect(path, timeout=3) as con:
        con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
        row = con.execute('SELECT until FROM holds WHERE host=?', (host,)).fetchone()
        if row:
            net.hold(host, row[0])
    if pace_gap_s(host):
        con = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            _pace(con, host)
        finally:
            con.close()
    try:
        return send()
    finally:
        with sqlite3.connect(path, timeout=3) as con:
            for name, until in net.holds().items():
                con.execute('INSERT INTO holds(host,until) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET until=max(holds.until,excluded.until)',
                            (name, until))


def get_json(sport, url, **kwargs):
    return _held(sport, url, lambda: net.get_json(url, **kwargs))


def get_text(sport, url, max_bytes=2_000_000, **kwargs):
    """A text file (CSV) through the same shared holds; refuses an answer over max_bytes."""
    def send():
        r = net.get(url, **kwargs)
        if len(r.content) > max_bytes:
            raise ValueError('response too large')
        return r.content.decode('utf-8-sig', errors='replace')
    return _held(sport, url, send)
