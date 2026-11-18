"""Persist provider Retry-After across the separate new-sport job processes.

Only transport cooldowns are shared; no player, result, quote or model data.
"""
import sqlite3
from urllib.parse import urlsplit

from . import net


def _path(sport):
    from .expansion_board import root
    path = root(sport).parent / 'request-holds.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _held(sport, url, send):
    path = _path(sport)
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


def get_limited(sport, url, limit, **kwargs):
    """get_json that refuses an answer over `limit` bytes before parsing it (a parsed body costs several times its
    size, and the quote jobs run under a small memory cap)."""
    def send():
        r = net.get(url, **kwargs)
        declared = r.headers.get('Content-Length')
        if (declared and declared.isdigit() and int(declared) > limit) or len(r.content) > limit:
            raise ValueError('oversize response refused')
        return r.json()
    return _held(sport, url, send)


def held_until(sport, host):
    """Epoch seconds before which no request goes to `host` (0.0 when there is no hold)."""
    with sqlite3.connect(_path(sport), timeout=3) as con:
        con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
        row = con.execute('SELECT until FROM holds WHERE host=?', (host,)).fetchone()
    return max(float(row[0]) if row else 0.0, net.holds().get(host, 0.0))


def hold(sport, host, until):
    """Persist a cooldown the source did not put in a Retry-After header (a bare 429, a 403, a pacing mark), so the
    other sport's job - a separate process - honours it too. Never shortens an existing hold."""
    net.hold(host, until)
    with sqlite3.connect(_path(sport), timeout=3) as con:
        con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
        con.execute('INSERT INTO holds(host,until) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET until=max(holds.until,excluded.until)',
                    (host, float(until)))
