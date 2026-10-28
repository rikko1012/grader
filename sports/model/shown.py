"""sh-1: the chance members SAW for a forecast (REGISTER.md amendment A9a 6).

While a cal-mi-1 market is ON the board shows the calibrated chance, not the raw sm-1 chance in the forecast record.
The forecast record is immutable (its id is a hash of its content), so the shown chance is kept beside it, keyed by
forecast id, in <SPORTS_DATA_DIR>/model/shown.db:

    shown(forecast_id PK, league, stat, line, p_over, p_under, p_push,      <- the displayed (calibrated) chance
          raw_over, raw_under, raw_push,                                    <- the record's raw chance (also kept there)
          cal_version, mode, shown_at)

Written at snapshot-build time (sports/snapshots.write_cal_index) for every board quote whose OWN forecast carries a
cal-mi chance; first write wins (INSERT OR IGNORE) = the chance shown when the forecast first reached the board. Read
by the public results page (sports/results_public.py) so results score what members saw. Nothing is written while every
market is OFF (the file is never created then).
"""
from __future__ import annotations

import sqlite3

from .. import config

VERSION = "sh-1"
SCHEMA = """CREATE TABLE IF NOT EXISTS shown (forecast_id TEXT PRIMARY KEY, league TEXT, stat TEXT, line REAL,
    p_over REAL NOT NULL, p_under REAL NOT NULL, p_push REAL NOT NULL, raw_over REAL, raw_under REAL, raw_push REAL,
    cal_version TEXT, mode TEXT, shown_at TEXT)"""
CHUNK = 500


def path():
    return config.DATA_DIR / "model" / "shown.db"


def record(league, items, now):
    """items: [(forecast_id, stat, line, cal dict from board_view.calibrated)] -> rows newly written."""
    rows = []
    for fid, stat, line, ca in items:
        if not fid or not ca or ca.get("po") is None or ca.get("pu") is None:
            continue
        raw = list(ca.get("raw") or [None, None, None]) + [None, None, None]
        rows.append((fid, league, stat, line, ca["po"], ca["pu"], ca.get("pp") or 0.0, raw[0], raw[1], raw[2],
                     ca.get("v"), ca.get("mode"), now))
    if not rows:
        return 0
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(SCHEMA)
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO shown VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        return con.total_changes - before
    finally:
        con.close()


def get_many(fids):
    """{forecast_id: {"p_over", "p_under", "p_push", "v"}} for the ids that have a shown chance ({} without a file)."""
    p = path()
    ids = sorted({f for f in fids if f})
    if not ids or not p.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return {}
    out = {}
    try:
        for i in range(0, len(ids), CHUNK):
            part = ids[i:i + CHUNK]
            for fid, po, pu, pp, v in con.execute(
                    f"SELECT forecast_id, p_over, p_under, p_push, cal_version FROM shown WHERE forecast_id IN "
                    f"({','.join('?' * len(part))})", part):
                out[fid] = {"p_over": po, "p_under": pu, "p_push": pp, "v": v}
    except sqlite3.Error:
        return out
    finally:
        con.close()
    return out
