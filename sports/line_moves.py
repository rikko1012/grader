"""Opening and closing line of a stored NBA / NFL offer, for the settled-pick note (SCOUT_LINE_MOVE_NOTES, owner 2026-11-07: "it
should clearly state line movement and closing line"). Wording only: nothing here is read by a grade, a chance, a stake, a
settlement or a record - a settled pick keeps the result it settled with at its OWN line.

What is read (read-only, bounded):
  - every stored revision of the offer identity (platform, offer_id), oldest first, from records_all (hot + archived:
    revision, line, late, known_at, event_id - sports/archive.py; an archived row's line comes from its verified file through
    the archive's bounded LRU, the same read store.revisions makes) - at most REVS_MAX;
  - the canonical event's start (store.latest "event" / "espn": events are never archived, and a delayed game carries its new
    start), so the closing line is cut at the game's REAL start, never at a start a pick saved when it was tracked.
Opening = the offer's first stored revision (revision 1, not late, known before the start): the first line Grader recorded
for this offer. Closing = the last non-late revision known at or before the start. Nothing is said (None) when any of that is
not known, when more revisions exist than were read, or when the offer shows a different line after the start (a live or
late line on the same id: the close is then not certain). Behind a bounded per-process cache: an answer for a game that
started more than FINAL_S ago is kept, anything else is read again after MISS_TTL.

SCOUT_LINE_MOVE_NOTES on this service: on unless set to "0" (the same default the esports server's DEFAULT_ON gives it).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

from . import store

FLAG = "SCOUT_LINE_MOVE_NOTES"
KEYS_MAX = 60                 # offers per request
REVS_MAX = 1000               # revisions read per offer (more = the closing line is not known)
CACHE_MAX = 20000
MISS_TTL = 300
FINAL_S = 12 * 3600
_CACHE = OrderedDict()        # (platform, offer_id) -> ((open, close) or None, cached at, final)
_LOCK = threading.Lock()


def enabled():
    return os.environ.get(FLAG, "1").strip() != "0"


def reset():
    with _LOCK:
        _CACHE.clear()


def _ts(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def of_rows(rows, start):
    """(opening, closing) from an offer's revisions [{revision, line, late, known_at}] (oldest first) and its game's start, or None."""
    st = _ts(start)
    if st is None or not rows or len(rows) >= REVS_MAX:
        return None
    first = rows[0]
    if first["revision"] != 1 or first["late"] or first["line"] is None:
        return None
    live = [r for r in rows if not r["late"] and r["line"] is not None and _ts(r["known_at"]) is not None]
    pre = [r for r in live if _ts(r["known_at"]) <= st]
    if not pre or pre[0] is not first:
        return None                                   # first stored after the start: no pre-game opening line
    close = float(pre[-1]["line"])
    if any(float(r["line"]) != close for r in live if _ts(r["known_at"]) > st):
        return None                                   # a different line after the start: the close is not certain
    return float(first["line"]), close


def _one(con, platform, offer_id):
    """-> ((open, close) or None, start timestamp or None)."""
    rows = [dict(r) for r in con.execute(
        "SELECT revision, line, late, known_at, event_id FROM records_all WHERE kind = 'offer' AND source = ? AND "
        "source_key = ? ORDER BY revision LIMIT ?", (platform, offer_id, REVS_MAX))]
    eid = next((r["event_id"] for r in reversed(rows) if r.get("event_id")), None)
    ev = store.latest(con, "event", "espn", eid) if eid else None
    start = getattr(ev, "start_utc", None)
    return of_rows(rows, start), _ts(start)


def offer_moves(keys, opener, now=None):
    """{(platform, offer_id): (opening, closing)} for the offers whose game has started and whose opening line is known.
    opener: a zero-argument context manager giving a read-only store connection (web.store_ro), entered only when an answer is
    not cached; at most KEYS_MAX offers are read per call. A read that fails or runs over the request budget leaves the rest
    unanswered (never an error)."""
    now = time.time() if now is None else now
    out, need = {}, []
    for k in dict.fromkeys(tuple(k) for k in keys):
        with _LOCK:
            e = _CACHE.get(k)
        if e is not None and (e[2] or now - e[1] <= MISS_TTL):
            if e[0] is not None:
                out[k] = e[0]
        else:
            need.append(k)
    if not need:
        return out
    try:
        with opener() as con:
            for k in need[:KEYS_MAX]:
                try:
                    oc, st = _one(con, *k)
                except (sqlite3.Error, RuntimeError):     # RuntimeError: web.Busy (the request budget) - answer what is known
                    break
                passed = st is not None and st <= now
                oc = oc if passed else None               # before the start the closing line is not known yet
                if oc is not None:
                    out[k] = oc
                with _LOCK:
                    _CACHE[k] = (oc, now, passed and now - st > FINAL_S)
                    _CACHE.move_to_end(k)
                    while len(_CACHE) > CACHE_MAX:
                        _CACHE.popitem(last=False)
    except (FileNotFoundError, sqlite3.Error, RuntimeError):
        pass
    return out


def annotate(views, keys, opener, now=None):
    """Sports My picks (sports/api_picks.mine): a settled pick whose offer was bumped between its opening and closing line, its
    own line between the two, gains `line_move` (closing_lines.move_note). keys: the (platform, offer_id) of each view. Its
    result and every count stay exactly as settled."""
    want = [(v, k) for v, k in zip(views, keys) if (v.get("result") or {}).get("status") in ("win", "loss", "push")
            and isinstance((v.get("result") or {}).get("value"), (int, float)) and isinstance(v.get("line"), (int, float))]
    if not want:
        return
    got = offer_moves([k for _, k in want], opener, now)
    for v, k in want:
        mv = note(got.get(k), v["line"], v["result"]["value"], k[0])
        if mv:
            v["line_move"] = mv


# The note's wording is the esports server's closing_lines.move_note, word for word (the sports package never imports an
# esports module - sports/tests/test_sports_isolation.py); sports/tests/test_line_moves.py checks the two read the same.
BOOK_LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def fmt_line(v):
    """15.5 -> "15.5", 16.0 -> "16"."""
    return ("%.2f" % float(v)).rstrip("0").rstrip(".")


def at_line(line, actual):
    return "Over won" if actual > line else "Under won" if actual < line else "push"


def move_note(open_line, closing, actual, book=None):
    """kind "flip" (the two lines read differently, a push at either counts): "Opened X (...) · closed Y (...)"; kind "bump"
    (both read the same): the board's "bumped from" chip only; None when there is nothing to say."""
    o, c, a = _num(open_line), _num(closing), _num(actual)
    if o is None or c is None or a is None or o == c:
        return None
    ao, ac, up = at_line(o, a), at_line(c, a), c > o
    O, C = fmt_line(o), fmt_line(c)
    arrow, d = ("▲", "up") if up else ("▼", "down")
    who = f"{BOOK_LABEL.get(book, book)} opened this line at {O}" if book else f"This line opened at {O}"
    tip = f"Bumped {d}: {who}; it closed at {C}."
    if ao == ac:
        chip = f"{arrow} bumped from {O}"
        return {"kind": "bump", "dir": d, "opened": o, "closing": c, "at_open": ao, "at_close": ac, "text": chip, "chip": chip,
                "tip": f"{tip} {ao[0].upper() + ao[1:]} at both lines."}
    return {"kind": "flip", "dir": d, "opened": o, "closing": c, "at_open": ao, "at_close": ac,
            "text": f"Opened {O} ({ao}) · closed {C} ({ac})", "chip": f"{arrow} bumped", "tip": tip}


def note(oc, line, actual, platform):
    """move_note for one settled pick at its own line, only when that line sits on the offer's own move (between its opening and
    closing line: another line never borrows the note), or None."""
    if not oc:
        return None
    o, c, ln = _num(oc[0]), _num(oc[1]), _num(line)
    if o is None or c is None or ln is None or not min(o, c) <= ln <= max(o, c):
        return None
    return move_note(o, c, actual, platform)
