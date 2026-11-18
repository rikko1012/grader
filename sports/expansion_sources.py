"""Persist provider Retry-After across the separate new-sport job processes.

Only transport cooldowns are shared; no player, result, quote or model data.
"""
import sqlite3
from urllib.parse import urlsplit

from . import net


def _held(sport, url, send):
    from .expansion_board import root
    path = root(sport).parent / 'request-holds.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    host = urlsplit(url).hostname
    with sqlite3.connect(path, timeout=3) as con:
        con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
        row = con.execute('SELECT until FROM holds WHERE host=?', (host,)).fetchone()
        if row:
            net.hold(host, row[0])
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
