"""av-1 availability hold (owner 2026-11-09: "make sure every projection accounts for ... whether a player is playing").

A player ESPN rules out for a game gets no side, letter, stake or slip leg on that game's lines: snapshots.guard_row puts
the hold {"b": code, "t": text, "v": "av-1"} on every quote of his row (the same q["x"] shape as the vg-1 / sg-2 holds,
so board, card, sheets, slip pool and tracked picks all follow it). The projection, chances and the logged forecast do
not change - this is an availability fact, not a model factor.

Currently listed (one rule for display, the hold, the live teammate-absence input and the shadow inputs):
  ESPN sends no removal entry, so an injury record is CURRENT only while it is in the newest report read: its own
  last_seen_at (store: the newest sighting; the payload's observed_at is the FIRST sighting) is within LISTED_WINDOW_S of
  the newest last_seen_at of any injury record of the league. A failed or stale feed moves every record together, so
  the last state is kept (transient rule); nothing is ever invented from missing data.

Hold rules (all must hold; anything missing = no hold):
  report   status out / injured_reserve / suspended, currently listed, newest read within REPORT_MAX_AGE_H
           + ESPN's own designation for that SAME entry (pregame_news archive: fantasyStatus, returnDate; matched on
           ESPN's entry date): a ruling (RULED: OUT, IR, PUP / NFI, suspension, reserve lists ...) with a return date,
           and the game is before that return date. NFL's early-week "Out" with fantasyStatus QUESTIONABLE, IR-R
           (designated to return) and a bare "Out" with no designation are not rulings.
  inactive NFL official game-day list (pregame_news lineup, ~90 min before kickoff): newest flag active is False or
           didNotPlay is True for this player and this game.
Questionable / Doubtful / Probable / Day-to-day never hold a line.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

VERSION = "av-1"
LISTED_WINDOW_S = 15 * 60
REPORT_MAX_AGE_H = 24
HOLD_STATUSES = ("out", "injured_reserve", "suspended")
# ESPN fantasyStatus values that are a ruling (checked on the live reports 2026-11-11). Anything else - QUESTIONABLE,
# DOUBTFUL, GTD, IR-R (designated to return), or NO designation at all - never holds: ESPN's NFL report keeps a bare
# "Out" with no designation or return date on players who already returned (a QB announced as Sunday's starter).
RULED = ("OUT", "OFS", "IR", "INACTIVE", "SUSP", "SUSPENDED", "PUP", "PUP-R", "PUP-P", "NFI", "NFI-R", "NFI-P")
TEXT = {"out": "Ruled out (ESPN injury report)",
        "injured_reserve": "Injured reserve (ESPN injury report)",
        "suspended": "Suspended (ESPN injury report)",
        "inactive": "Inactive (official game-day list)"}
CODES = tuple(f"av_{k}" for k in TEXT)


def _dt(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def on():
    """SPORTS_AVAIL_HOLD=0 = rollback (no hold; display expiry and the corrected inputs stay)."""
    return os.environ.get("SPORTS_AVAIL_HOLD", "1").strip().lower() not in ("0", "false", "no", "off")


def seen_at(meta_last_seen, observed_at, cap=None):
    """The newest sighting of a record (last_seen_at, else its first observed_at), never later than cap."""
    t = _dt(meta_last_seen) or _dt(observed_at)
    c = _dt(cap)
    if t is not None and c is not None and t > c:
        t = c
    return t


def current(recs, now=None):
    """recs: [(record, last_seen_at)] of ONE league's injury records (any present value). -> (newest read iso or None,
    {subject_id: record} of the records in the newest read, or {} when the newest read is older than REPORT_MAX_AGE_H
    before now). The rule is relative to the newest read: an outage keeps the last state."""
    seen = [(c, seen_at(ls, getattr(c, "observed_at", None), now)) for c, ls in recs]
    seen = [(c, t) for c, t in seen if t is not None]
    if not seen:
        return None, {}
    newest = max(t for _, t in seen)
    n = _dt(now)
    if n is not None and (n - newest).total_seconds() > REPORT_MAX_AGE_H * 3600:
        return _iso(newest), {}
    lo = newest - timedelta(seconds=LISTED_WINDOW_S)
    return _iso(newest), {c.subject_id: c for c, t in seen if t >= lo and c.subject_id}


def _local_date(start_utc):
    """The game's US calendar date (ESPN return dates are US dates)."""
    t = _dt(start_utc)
    if t is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                   # noqa: BLE001 - no tz database: US Eastern standard time
        return (t - timedelta(hours=5)).date().isoformat()


def _same_entry(a, b):
    ta, tb = _dt(a), _dt(b)
    return ta is not None and tb is not None and abs((ta - tb).total_seconds()) < 60


def hold(rec, desig, start_utc, inactive=None):
    """-> hold code (CODES) or None for one player and one game.
    rec = his CURRENT injury record (current()) or None; desig = pregame_news payload of his ESPN entry or None;
    inactive = his newest game-day lineup flags for this game or None."""
    if not on():
        return None
    if isinstance(inactive, dict) and (inactive.get("active") is False or inactive.get("did_not_play") is True):
        return "av_inactive"
    if rec is None or getattr(rec, "present", None) != "yes" or rec.status not in HOLD_STATUSES:
        return None
    if not isinstance(desig, dict) or not _same_entry(desig.get("espn_date"), rec.as_of):
        return None                                     # ESPN's designation for THIS entry unknown: no hold
    fs = str(desig.get("fantasy_status") or "").strip().upper()
    if not (fs in RULED or fs.startswith("RESERVE")):
        return None
    rd = str(desig.get("return_date") or "").strip()[:10]
    day = _local_date(start_utc)
    if not rd or day is None or day >= rd:
        return None                                     # no return date, or ESPN expects him back by this game
    return f"av_{rec.status}"


def x_of(code):
    return {"b": code, "t": TEXT[code[3:]], "v": VERSION}


def text(code):
    return TEXT.get(str(code or "")[3:])


# ------------------------------------------------------------------------------------- pregame_news archive (read-only)
def _news_con():
    from .pregame_news import db_path
    p = db_path()
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def _payloads(con, ids, with_event=False):
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        for subject, event, payload in con.execute(
                f"SELECT subject, event, payload FROM news WHERE id IN ({','.join('?' * len(part))})", part):
            try:
                p = json.loads(payload)
            except ValueError:
                continue
            yield ((subject, event) if with_event else subject), p


def designations(league, event_ids=()):
    """-> ({player_id: ESPN injury payload (fantasy_status, return_date, espn_date)} of the players currently listed in
    the archive, {(player_id, raw ESPN event id): lineup flags} for NFL events in event_ids). Any failure -> ({}, {})."""
    try:
        con = _news_con()
    except Exception:                                   # noqa: BLE001
        return {}, {}
    if con is None:
        return {}, {}
    inj, lineup = {}, {}
    try:
        # newest row per subject through the (sport, event) index: league-wide news has event '', a game's has its id
        absent = {r[0] for r in con.execute("SELECT subject FROM current WHERE sport = 'sports' AND kind = 'injury' "
                                            "AND scope = ? AND absent = 1", (f"league:{league}",))}
        ids = [r[0] for r in con.execute(
            "SELECT MAX(id) FROM news WHERE sport = 'sports' AND event = '' AND kind = 'injury' AND scope = ? "
            "GROUP BY subject", (f"league:{league}",))]
        for subject, payload in _payloads(con, ids):
            if subject not in absent and isinstance(payload, dict) and not payload.get("absent"):
                inj[subject] = payload
        raw = sorted({str(e).rsplit(":", 1)[-1] for e in event_ids if e})
        if league == "nfl" and raw:
            for k in range(0, len(raw), 200):
                part = raw[k:k + 200]
                ids = [r[0] for r in con.execute(
                    f"SELECT MAX(id) FROM news WHERE sport = 'sports' AND event IN ({','.join('?' * len(part))}) "
                    "AND kind = 'lineup' GROUP BY subject, event", part)]
                for (subject, event), payload in _payloads(con, ids, with_event=True):
                    if isinstance(payload, dict) and not payload.get("absent"):
                        lineup[(subject, str(event))] = payload
    except Exception:                                   # noqa: BLE001 - a failed read holds nothing
        return {}, {}
    finally:
        con.close()
    return inj, lineup
