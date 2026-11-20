"""Persist provider Retry-After across the separate new-sport job processes.

Only transport cooldowns are shared; no player, result, quote or model data.
"""
import os
import sqlite3
import time
from contextlib import closing
from urllib.parse import urlsplit

from . import net

PP_HOST = 'partner-api.prizepicks.com'


def pace_gap_s(host):
    """Minimum seconds between two requests to `host` from ANY new-sport job process (soccer and tennis quotes).
    PrizePicks only (2026-11-19, per-league soccer tabs): SCOUT_PP_GAP_S, default 3; 0 turns it off. Off as well
    when the soccer per-league tabs are switched off (SCOUT_SOCCER_PP_LEAGUES=0): requests exactly as at 5036ce4."""
    if host != PP_HOST or os.environ.get('SCOUT_SOCCER_PP_LEAGUES', '1') == '0':
        return 0.0
    try:
        return max(0.0, float(os.environ.get('SCOUT_PP_GAP_S', '3')))
    except ValueError:
        return 3.0


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


EVENTS_KEEP_S = 2 * 86400


def _events_on():
    """The 429 log only exists for the soccer per-league AIMD (its only reader): soccer on, kill switch off."""
    return os.environ.get('SCOUT_SOCCER_ENABLED') == '1' and os.environ.get('SCOUT_SOCCER_PP_LEAGUES', '1') != '0'


def _events_table(con):
    con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS pp_events (id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, '
                'consumer TEXT NOT NULL, n INTEGER NOT NULL, until REAL)')


def record_429(consumer, n=1):
    """A PrizePicks consumer (soccer / tennis quotes, NBA / NFL lines ingest) got `n` HTTP 429 answers: log it, and
    copy this process's PrizePicks Retry-After hold (if any) into the shared holds so the soccer and tennis jobs honour
    it. Never raises, never waits more than ~1 s, never sends a request; a no-op unless the soccer AIMD is on."""
    if not _events_on() or n <= 0:
        return False
    try:
        until = net.holds().get(PP_HOST)
        with closing(sqlite3.connect(_path('soccer'), timeout=1)) as con, con:
            _events_table(con)
            con.execute('INSERT INTO pp_events(at, consumer, n, until) VALUES (?,?,?,?)',
                        (time.time(), str(consumer)[:20], int(n), until))
            if until:
                con.execute('INSERT INTO holds(host,until) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET '
                            'until=max(holds.until,excluded.until)', (PP_HOST, until))
        return True
    except Exception:                                   # noqa: BLE001 - reporting never breaks a caller
        return False


def pp_events_since(cursor):
    """-> [{id, at (epoch), consumer, n, until}] logged after row id `cursor` (oldest first; at most 500). Prunes rows
    older than two days. [] when the log is off or unreadable."""
    if not _events_on():
        return []
    try:
        with closing(sqlite3.connect(_path('soccer'), timeout=1)) as con, con:
            _events_table(con)
            con.execute('DELETE FROM pp_events WHERE at < ?', (time.time() - EVENTS_KEEP_S,))
            rows = con.execute('SELECT id, at, consumer, n, until FROM pp_events WHERE id > ? ORDER BY id LIMIT 500',
                               (int(cursor),)).fetchall()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return []
    return [{'id': r[0], 'at': r[1], 'consumer': r[2], 'n': r[3], 'until': r[4]} for r in rows]


def pp_429_counts():
    """Ops: PrizePicks 429 answers per consumer in the last hour / day, and the latest one (UTC)."""
    if not _events_on():
        return {}
    try:
        with closing(sqlite3.connect(_path('soccer'), timeout=1)) as con, con:
            _events_table(con)
            now = time.time()
            rows = con.execute('SELECT consumer, SUM(CASE WHEN at >= ? THEN n ELSE 0 END), SUM(n), MAX(at) '
                               'FROM pp_events WHERE at >= ? GROUP BY consumer', (now - 3600, now - 86400)).fetchall()
    except (sqlite3.Error, OSError):
        return {}
    return {c: {'last_1h': int(h or 0), 'last_24h': int(d or 0),
                'last_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)) if t else None} for c, h, d, t in rows}


def _held(sport, url, send):
    path = _path(sport)
    host = urlsplit(url).hostname
    before = net.count_429(urlsplit(url).netloc) if host == PP_HOST else 0
    with sqlite3.connect(path, timeout=3) as con:
        con.execute('CREATE TABLE IF NOT EXISTS holds (host TEXT PRIMARY KEY, until REAL NOT NULL)')
        row = con.execute('SELECT until FROM holds WHERE host=?', (host,)).fetchone()
        if row:
            net.hold(host, row[0])
    if pace_gap_s(host):
        try:                                    # fails open: a locked / broken holds db never stops the request
            con = sqlite3.connect(path, timeout=10, isolation_level=None)
            try:
                _pace(con, host)
            finally:
                con.close()
        except sqlite3.Error:
            pass
    try:
        return send()
    finally:
        with sqlite3.connect(path, timeout=3) as con:
            for name, until in net.holds().items():
                con.execute('INSERT INTO holds(host,until) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET until=max(holds.until,excluded.until)',
                            (name, until))
        if host == PP_HOST:
            record_429(sport, net.count_429(urlsplit(url).netloc) - before)


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
