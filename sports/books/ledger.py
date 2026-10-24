"""Per-source request / credit ledger for the market sources (worker only): request pacing, monthly credit budgets,
cool-downs after 429. One SQLite file <SPORTS_DATA_DIR>/books/ledger.db, shared by every worker thread / process on the
host (BEGIN IMMEDIATE reservation, as the esports Thunderpick gateway does).

Budgets (environment overrides, defaults = the free tiers we know):
  oddsapi      SPORTS_ODDSAPI_MONTHLY_CREDITS=500  reserve SPORTS_ODDSAPI_RESERVE=50 ; cost = markets x regions per
               event-odds call (the API's documented rule), 0 for /events; the answer's x-requests-remaining header,
               when present, overrides our count downward
  thunderpick  10 requests / minute / path (partner guide) -> we use <= 4 / minute
  oddspapi     SPORTS_ODDSPAPI_MONTHLY_REQUESTS=0 (account shared with CS2; spent only through books/oddspapi.py's
               guard), >= 2 s apart
  polymarket   public; >= 1 s apart
A refused reservation is recorded with its reason; nothing retries automatically past a budget.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone

from .. import config

LIMITS = {
    "oddsapi": {"monthly": int(os.environ.get("SPORTS_ODDSAPI_MONTHLY_CREDITS", "500")),
                "reserve": int(os.environ.get("SPORTS_ODDSAPI_RESERVE", "50")), "gap_s": 1.0, "per_min": 30},
    "thunderpick": {"monthly": None, "reserve": 0, "gap_s": 15.0, "per_min": 4},
    # the OddsPapi account is shared with CS2: Sports' share defaults to 0 and is spent only by the guarded provider
    # (books/oddspapi.py: atomic allocation + fresh whole-account check + CS2 floor); capture is unmetered
    "oddspapi": {"monthly": int(os.environ.get("SPORTS_ODDSPAPI_MONTHLY_REQUESTS", "0")), "reserve": 0,
                 "gap_s": 2.0, "per_min": 20},
    "polymarket": {"monthly": None, "reserve": 0, "gap_s": 1.0, "per_min": 50},
}


def _path():
    return config.DATA_DIR / "books" / "ledger.db"


def connect():
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, at REAL, "
                "month TEXT, cost REAL, outcome TEXT, detail TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS state (source TEXT PRIMARY KEY, cooldown_until REAL, remaining REAL)")
    return con


def _month(t=None):
    return datetime.fromtimestamp(t or time.time(), tz=timezone.utc).strftime("%Y-%m")


def reserve(source, cost=1.0, detail="", wait_s=30.0):
    """-> (ok, reason). Pacing (gap / per-minute) is WAITED for up to wait_s; a monthly budget, a provider-reported
    low balance or a cool-down refuses at once."""
    t_end = time.time() + wait_s
    while True:
        ok, why = _reserve(source, cost, detail)
        if ok or not why or not (why.startswith("gap") or why.endswith("/min")) or time.time() >= t_end:
            return ok, why
        time.sleep(min(LIMITS[source]["gap_s"], 2.0))


def _reserve(source, cost=1.0, detail=""):
    lim = LIMITS[source]
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        now = time.time()
        st = con.execute("SELECT cooldown_until, remaining FROM state WHERE source=?", (source,)).fetchone()
        if st and st[0] and st[0] > now:
            why = f"cooldown {st[0] - now:.0f}s"
        else:
            last = con.execute("SELECT MAX(at) FROM log WHERE source=? AND outcome='sent'", (source,)).fetchone()[0]
            recent = con.execute("SELECT COUNT(*) FROM log WHERE source=? AND outcome='sent' AND at > ?",
                                 (source, now - 60)).fetchone()[0]
            used = con.execute("SELECT COALESCE(SUM(cost), 0) FROM log WHERE source=? AND outcome='sent' AND month=?",
                               (source, _month(now))).fetchone()[0]
            why = None
            if last and now - last < lim["gap_s"]:
                why = f"gap {lim['gap_s']}s"
            elif recent >= lim["per_min"]:
                why = f"{lim['per_min']}/min"
            elif lim["monthly"] is not None and used + cost > lim["monthly"] - lim["reserve"]:
                why = f"monthly budget {used:.0f}+{cost:.0f} > {lim['monthly']}-{lim['reserve']}"
            elif st and st[1] is not None and lim["monthly"] is not None and st[1] - cost < lim["reserve"]:
                why = f"provider says {st[1]:.0f} credits remain"
        if why is None or not (why.startswith("gap") or why.endswith("/min")):      # pacing waits are not logged
            con.execute("INSERT INTO log (source, at, month, cost, outcome, detail) VALUES (?,?,?,?,?,?)",
                        (source, now, _month(now), cost if why is None else 0, "sent" if why is None else "refused",
                         (why or detail)[:200]))
        con.execute("COMMIT")
        return why is None, why
    finally:
        con.close()


def report(source, status=None, remaining=None):
    """After a request: 429 -> cool-down (60 s doubling, cap 30 min); a provider-reported remaining credit count."""
    con = connect()
    try:
        now = time.time()
        if status == 429:
            n = con.execute("SELECT COUNT(*) FROM log WHERE source=? AND outcome='429' AND at > ?",
                            (source, now - 1800)).fetchone()[0]
            con.execute("INSERT INTO log (source, at, month, cost, outcome) VALUES (?,?,?,0,'429')",
                        (source, now, _month(now)))
            until = now + min(1800, 60 * 2 ** n)
            con.execute("INSERT INTO state (source, cooldown_until) VALUES (?, ?) ON CONFLICT(source) DO UPDATE SET "
                        "cooldown_until=excluded.cooldown_until", (source, until))
        if remaining is not None:
            con.execute("INSERT INTO state (source, remaining) VALUES (?, ?) ON CONFLICT(source) DO UPDATE SET "
                        "remaining=excluded.remaining", (source, float(remaining)))
    finally:
        con.close()


def usage():
    con = connect()
    try:
        m = _month()
        out = {}
        for s, lim in LIMITS.items():
            used = con.execute("SELECT COALESCE(SUM(cost),0), COUNT(*) FROM log WHERE source=? AND outcome='sent' AND "
                               "month=?", (s, m)).fetchone()
            refused = con.execute("SELECT COUNT(*) FROM log WHERE source=? AND outcome='refused' AND month=?",
                                  (s, m)).fetchone()[0]
            out[s] = {"month": m, "used": used[0], "requests": used[1], "refused": refused, "limit": lim["monthly"],
                      "reserve": lim["reserve"]}
        return out
    finally:
        con.close()
