"""SCOUT_CLOSING_LINES (owner flag, default off): the closing line of every CS2 board offer, saved once at lock.

What it does
- observe(book, rows): every graded-board rebuild (server.graded_board, at most one per 5 s per platform, plus one per
  background board pass) hands its rows here. For each offer whose lock has not passed, the last line seen is kept in
  memory (bounded). When the lock passes, that last pre-lock line is written ONCE to closing_lines.db. Rows are never
  updated or deleted (SQLite triggers refuse it): append-only, like the forecast logs.
- clv_report(): admin diagnostics only. Each pick-log line against its platform's closing line for the same player /
  stat / maps / line type / match. CLV = closing - line for Over (More), line - closing for Under (Less): positive = the
  closing line moved toward the logged side (the convention of picklog.set_closing). Line points only: no returns.
- l10_vs_closing(): the research drawer. The player's last 10 results, each against the closing line saved for THAT
  match on the same platform. Nothing is returned when no game has a saved closing line.
- move_note() (SCOUT_LINE_MOVE_NOTES, wording only): a settled prop whose line was bumped between opening and close says so -
  "Opened 15.5 (Over won) · closed 16 (push)" when the two lines read differently, the board's "bumped from" chip when both
  read the same. Built from the open_line already saved on the closing row; the counted result never changes.

Never a grade input: nothing here is read by the grader, the stake, the forecast log or settlement. Flag off: observe()
returns at once, no database is created and no response gains a field.

Capacity (700 members): writes happen only on the single board-rebuild thread, one small transaction per lock; member
reads (drawer) are one indexed, LIMITed query per player / stat / maps / platform, shared by every member through
server.shared for CACHE_TTL seconds. A restart loses only the in-memory last-seen lines of offers that had not locked;
their next observation before lock restores them.
"""
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

FLAG = "SCOUT_CLOSING_LINES"
VERSION = "cl-1"
PENDING_MAX = 20000            # offers held in memory before lock (a full board is ~2-3 thousand)
DONE_MAX = 40000               # identities already written (skip repeat inserts while a started row stays on the board)
STALE_S = 30 * 60              # last pre-lock sighting this long before lock = flagged stale in diagnostics
DRAWER_LIMIT = 60              # closing rows read for one player / stat / maps / platform
REPORT_DAYS = 30
REPORT_CLOSING_MAX = 50000     # bounded diagnostics reads
REPORT_PICKS_MAX = 100000
CACHE_TTL = 300
MOVE_FLAG = "SCOUT_LINE_MOVE_NOTES"   # settled rows say when the line was bumped (wording only; needs SCOUT_CLOSING_LINES too)
LOOKUP_MAX = 20000             # closing rows remembered for settled-row notes (append-only: a hit never changes)
MISS_TTL = 300                 # an ident with no closing row is asked again after this (while its start is recent)
FINAL_AFTER_S = 12 * 3600      # ... and never again once it started this long ago (a closing row is written around lock or never)
IN_CHUNK = 200
LEG_WINDOW_S = 12 * 3600       # a studio leg without its ident: its closing row locked within this of the leg's start
BOOK_LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}

_LOCK = threading.Lock()       # the in-memory pending / done maps
_WLOCK = threading.Lock()      # one writer
_PENDING = OrderedDict()       # ident -> last pre-lock observation
_DONE = OrderedDict()
_SCHEMA_OK = [None]
_REPORT = {"at": 0.0, "doc": None, "lock": threading.Lock()}
_LOOKUP = OrderedDict()        # ident (or a studio-leg key) -> (row or None, cached at)
_LOOKUP_LOCK = threading.Lock()
_HLTV_SLUG_RE = re.compile(r"hltv-(\d+)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS closing_lines (
  id INTEGER PRIMARY KEY, ident TEXT UNIQUE NOT NULL, version TEXT, sport TEXT, book TEXT, event_id TEXT,
  match_slug TEXT, settle_slug TEXT, player_key TEXT, player TEXT, team TEXT, opponent TEXT, prop TEXT, scope TEXT,
  line_type TEXT, line REAL, open_line REAL, first_line REAL, over_offered INTEGER, under_offered INTEGER,
  over_payout REAL, under_payout REAL, over_price REAL, under_price REAL, offer_id TEXT, lock_utc TEXT, start_utc TEXT,
  first_seen_at TEXT, last_seen_at TEXT, captured_at TEXT, observations INTEGER);
CREATE INDEX IF NOT EXISTS ix_cl_player ON closing_lines(player_key, prop, scope, book, line_type, lock_utc);
CREATE INDEX IF NOT EXISTS ix_cl_lock ON closing_lines(lock_utc);
CREATE TRIGGER IF NOT EXISTS cl_no_update BEFORE UPDATE ON closing_lines
  BEGIN SELECT RAISE(ABORT, 'closing_lines is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cl_no_delete BEFORE DELETE ON closing_lines
  BEGIN SELECT RAISE(ABORT, 'closing_lines is append-only'); END;
"""
COLS = ("ident", "version", "sport", "book", "event_id", "match_slug", "settle_slug", "player_key", "player", "team", "opponent",
        "prop", "scope", "line_type", "line", "open_line", "first_line", "over_offered", "under_offered", "over_payout",
        "under_payout", "over_price", "under_price", "offer_id", "lock_utc", "start_utc", "first_seen_at", "last_seen_at",
        "captured_at", "observations")


def enabled():
    return os.environ.get(FLAG) == "1"


def db_path():
    return Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / "closing_lines.db"


def _connect():
    p = db_path()
    con = sqlite3.connect(str(p), timeout=10)
    con.row_factory = sqlite3.Row
    if _SCHEMA_OK[0] != str(p):
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.commit()
        _SCHEMA_OK[0] = str(p)
    return con


def _reader():
    """None when nothing was ever saved (a read never creates the database)."""
    p = db_path()
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def reset():
    """Tests: forget the in-memory state."""
    with _LOCK:
        _PENDING.clear()
        _DONE.clear()
    _SCHEMA_OK[0] = None
    _REPORT.update(at=0.0, doc=None)
    with _LOOKUP_LOCK:
        _LOOKUP.clear()


# ------------------------------------------------------------------------------------------------ identity

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def book_of(x):
    return x.get("book") or "underdog"


def line_type_of(x):
    if book_of(x) == "prizepicks" and x.get("odds_type") in ("goblin", "demon"):
        return x["odds_type"]
    return "standard"


def event_id(x):
    """workspace_api.event_id's rule (kept identical): the canonical id, else the HLTV id a fallback slug names, else the slug."""
    if x.get("match_id"):
        return str(x["match_id"])
    slug = x.get("settle_slug") or x.get("match_slug")
    m = _HLTV_SLUG_RE.fullmatch(str(slug or ""))
    if m:
        return f"hltv:{m.group(1)}"
    return str(slug) if slug else None


def _player_key(x):
    return norm(x.get("nick") or x.get("player"))


def ident(x):
    eid = event_id(x)
    pk = _player_key(x)
    if not eid or not pk or not x.get("prop"):
        return None
    return "|".join(("cs2", book_of(x), eid, pk, str(x.get("prop")), str(x.get("scope") or "12"), line_type_of(x)))


def _ts(s):
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if t is not None else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _offered(x, side):
    k = side + "_available"
    if k in x:
        return bool(x[k])
    return x.get("higher" if side == "over" else "lower") is not None


# ------------------------------------------------------------------------------------------------ capture

def observe(book, rows, now=None):
    """One board rebuild's rows. Returns the number of closing lines written by this call (0 with the flag off)."""
    if not enabled():
        return 0
    try:
        return _observe(book, rows, time.time() if now is None else now)
    except Exception:          # never breaks the board
        return 0


def _observe(book, rows, now):
    with _LOCK:
        for x in rows or ():
            if not isinstance(x, dict):
                continue
            x = x if x.get("book") else dict(x, book=book)
            if x.get("alt") and book_of(x) != "prizepicks":
                continue                                     # Underdog alternates: not the market's line
            line, lock = _num(x.get("line")), _ts(x.get("lock_at") or x.get("start") or x.get("match_start"))
            if line is None or lock is None or now >= lock:
                continue                                     # only pre-lock sightings count
            k = ident(x)
            if k is None or k in _DONE:
                continue
            ub = book_of(x) == "underdog"
            prev = _PENDING.pop(k, None)
            _PENDING[k] = {
                "ident": k, "version": VERSION, "sport": "cs2", "book": book_of(x), "event_id": event_id(x),
                "match_slug": x.get("match_slug"), "settle_slug": x.get("settle_slug"), "player_key": _player_key(x),
                "player": x.get("nick") or x.get("player"), "team": x.get("team_name") or x.get("team"),
                "opponent": x.get("opp_name") or x.get("opponent"), "prop": x.get("prop"), "scope": str(x.get("scope") or "12"),
                "line_type": line_type_of(x), "line": line, "open_line": _num(x.get("open_line")),
                "first_line": prev["first_line"] if prev else line,
                "over_offered": int(_offered(x, "over")), "under_offered": int(_offered(x, "under")),
                "over_payout": _num(x.get("higher" if ub else "mod_higher")), "under_payout": _num(x.get("lower" if ub else "mod_lower")),
                "over_price": _num(x.get("price_higher")), "under_price": _num(x.get("price_lower")),
                "offer_id": str(x.get("market_id") or x.get("id") or "") or None, "lock_utc": _iso(lock),
                "start_utc": x.get("start") or x.get("match_start"),
                "first_seen_at": prev["first_seen_at"] if prev else _iso(now), "last_seen_at": _iso(now),
                "observations": (prev["observations"] if prev else 0) + 1, "_lock": lock}
        while len(_PENDING) > PENDING_MAX:
            _PENDING.popitem(last=False)
        due = [k for k, v in _PENDING.items() if v["_lock"] <= now]
        batch = [_PENDING.pop(k) for k in due]
    if not batch:
        return 0
    try:
        return _write(batch, now)
    except Exception:
        with _LOCK:                                          # a failed write keeps them for the next pass (never lost)
            for e in batch:
                if e["ident"] not in _PENDING and len(_PENDING) < PENDING_MAX:
                    _PENDING[e["ident"]] = e
        raise


def flush(now=None):
    """Write every offer whose lock has passed (no new sightings). Flag off: nothing."""
    if not enabled():
        return 0
    return _observe(None, (), time.time() if now is None else now)


def _write(batch, now):
    stamp = _iso(now)
    vals = [tuple(stamp if c == "captured_at" else e.get(c) for c in COLS) for e in batch]
    with _WLOCK:
        con = _connect()
        try:
            cur = con.executemany(f"INSERT OR IGNORE INTO closing_lines ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})", vals)
            con.commit()
            n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(vals)
        finally:
            con.close()
    with _LOCK:
        for e in batch:
            _DONE[e["ident"]] = 1
        while len(_DONE) > DONE_MAX:
            _DONE.popitem(last=False)
    return n


def pending_count():
    with _LOCK:
        return len(_PENDING)


# ------------------------------------------------------------------------------------------------ drawer: L10 vs closing

def rows_for(player_key, prop, scope, book, line_type, limit=DRAWER_LIMIT):
    """Newest closing rows for one player / stat / maps / platform / line type (one indexed, bounded read)."""
    con = _reader()
    if con is None:
        return []
    try:
        return [dict(r) for r in con.execute(
            "SELECT event_id, line, open_line, lock_utc, last_seen_at FROM closing_lines WHERE player_key = ? AND prop = ? AND scope = ? "
            "AND book = ? AND line_type = ? ORDER BY lock_utc DESC LIMIT ?",
            (player_key, prop, str(scope), book, line_type, int(limit)))]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _hltv_key(mid):
    """'hltv-123' (BO3.gg / HLTV series rows) and 'hltv:123' (canonical event ids) name the same HLTV match."""
    s = str(mid or "")
    m = re.fullmatch(r"hltv[-:](\d+)", s)
    return f"hltv:{m.group(1)}" if m else s or None


def is_stale(c):
    """The saved line's last pre-lock sighting came more than STALE_S before lock (the diagnostics' stale rule)."""
    lk, ls = _ts(c.get("lock_utc")), _ts(c.get("last_seen_at"))
    return lk is not None and ls is not None and lk - ls > STALE_S


def l10_vs_closing(ser, mids, closing_rows, window=10, platform_label=None, notes=None, book=None):
    """The newest `window` results, each against the closing line saved for that match. None when no game has one.
    notes (default: move_notes_on()): a game whose line was bumped from its opening line carries `move` (move_note)."""
    if not ser or not mids or not closing_rows:
        return None
    notes = move_notes_on() if notes is None else notes
    by, opened = {}, {}
    for c in closing_rows:
        k = _hltv_key(c.get("event_id"))
        if k and k not in by and _num(c.get("line")) is not None and not is_stale(c):
            by[k] = c["line"]          # a closing line last seen more than STALE_S before lock is left off, never labelled
            opened[k] = c.get("open_line")
    games, over, under, push = [], 0, 0, 0
    for s, mid in list(zip(ser, mids))[:window]:
        cl = by.get(_hltv_key(mid))
        v = _num(s.get("value"))
        if cl is None or v is None:
            continue
        if v > cl:
            over += 1
            at = "Over"
        elif v < cl:
            under += 1
            at = "Under"
        else:
            push += 1
            at = "Push"
        g = {"date": s.get("date"), "opponent": s.get("opponent"), "value": s.get("value"), "closing": cl, "at": at}
        mv = move_note(opened.get(_hltv_key(mid)), cl, v, book) if notes else None
        if mv:
            g["move"] = mv         # wording only: "at" and every count above stay on the closing line
        games.append(g)
    if not games:
        return None
    return {"window": window, "n": len(games), "over": over, "under": under, "push": push, "games": games,
            "platform_label": platform_label, "source": "Closing line saved at lock"}


# ------------------------------------------------------------------------------------------------ settled rows: the line move

def move_notes_on():
    """SCOUT_LINE_MOVE_NOTES and SCOUT_CLOSING_LINES both on (server.DEFAULT_ON turns both on; "0" on Render turns one off)."""
    return enabled() and os.environ.get(MOVE_FLAG) == "1"


def fmt_line(v):
    """15.5 -> "15.5", 16.0 -> "16"."""
    return ("%.2f" % float(v)).rstrip("0").rstrip(".")


def at_line(line, actual):
    """The result at one line: "Over won", "Under won" or "push"."""
    return "Over won" if actual > line else "Under won" if actual < line else "push"


def move_note(open_line, closing, actual, book=None, stale=False):
    """A settled prop whose line moved between opening and close, or None (nothing to say: no opening line known, no closing
    line, not settled, not moved, or a stale closing row). kind "flip" = the two lines read differently (a push at either one
    counts): the full "Opened X (...) · closed Y (...)" sentence. kind "bump" = both lines read the same: the board's chip only.
    Wording only: the result shown beside it, and every count, stay on the line they use today."""
    o, c, a = _num(open_line), _num(closing), _num(actual)
    if o is None or c is None or a is None or o == c or stale:
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


def in_band(line, row):
    """The settled line sits on the market's own move (between its opening and closing line): an alternate or another ladder
    line that maps to the same identity never borrows its note."""
    o, c, ln = _num(row.get("open_line")), _num(row.get("line")), _num(line)
    return o is not None and c is not None and ln is not None and min(o, c) <= ln <= max(o, c)


def row_note(row, line, actual, book):
    """move_note for one settled line against one closing row (None off the band or on a stale row)."""
    if not row or not in_band(line, row):
        return None
    return move_note(row.get("open_line"), row.get("line"), actual, book, stale=is_stale(row))


def _cached(k, now):
    """(True, row-or-None) when the lookup cache answers for k, else (False, None). A hit is kept (append-only rows); a miss
    marked final (its lock is long past: no closing row can still be written) is kept too; any other miss expires after
    MISS_TTL."""
    with _LOOKUP_LOCK:
        e = _LOOKUP.get(k)
        if e is None:
            return False, None
        row, at, final = e
        if row is None and not final and now - at > MISS_TTL:
            del _LOOKUP[k]
            return False, None
        _LOOKUP.move_to_end(k)
        return True, row


def _remember(k, row, now, final=False):
    with _LOOKUP_LOCK:
        _LOOKUP[k] = (row, now, bool(final))
        _LOOKUP.move_to_end(k)
        while len(_LOOKUP) > LOOKUP_MAX:
            _LOOKUP.popitem(last=False)


def _final(start, now, extra=0.0):
    """A miss for an offer that started more than FINAL_AFTER_S (+ extra) ago is final: the closing row is written around lock
    or never (a restart drops the pending sighting), so asking again cannot find one."""
    st = _ts(start)
    return st is not None and now - st > FINAL_AFTER_S + extra


class Reader:
    """One read-only connection for a whole request, opened on first use (none when every answer is cached). close() always."""

    def __init__(self):
        self.con, self.tried = None, False

    def get(self):
        if not self.tried:
            self.tried = True
            self.con = _reader()
        return self.con

    def close(self):
        if self.con is not None:
            self.con.close()
            self.con = None


def lookup_idents(idents, now=None, starts=None, reader=None):
    """{ident: closing row} for the idents that have one. Indexed reads (the UNIQUE ident), IN_CHUNK at a time, behind a
    bounded cache: a saved row is append-only, so a hit is kept; a miss is asked again after MISS_TTL, unless starts
    ({ident: start}) shows the offer started more than FINAL_AFTER_S ago (then the miss is kept). No database = {}.
    reader: a shared Reader (the caller closes it); else one connection is opened and closed here."""
    now = time.time() if now is None else now
    starts = starts or {}
    out, need = {}, []
    for k in dict.fromkeys(i for i in idents if i):
        hit, row = _cached(k, now)
        if hit:
            if row is not None:
                out[k] = row
        else:
            need.append(k)
    if not need:
        return out
    own = reader is None
    rd = Reader() if own else reader
    found = {}
    try:
        con = rd.get()
        if con is None:
            return out
        for i in range(0, len(need), IN_CHUNK):
            part = need[i:i + IN_CHUNK]
            for r in con.execute(f"SELECT ident, line, open_line, lock_utc, last_seen_at FROM closing_lines WHERE ident IN "
                                 f"({','.join('?' * len(part))})", part):
                found[r["ident"]] = dict(r)
    except sqlite3.Error:
        return out
    finally:
        if own:
            rd.close()
    for k in need:
        _remember(k, found.get(k), now, final=k not in found and _final(starts.get(k), now))
        if k in found:
            out[k] = found[k]
    return out


def leg_key(player_key, prop, scope, book, line_type, slug, start):
    """The lookup-cache key of find_for_leg (None when the leg cannot be looked up)."""
    st = _ts(start)
    if not slug or st is None or not player_key or not prop:
        return None
    return ("leg", player_key, prop, str(scope), book, line_type, slug, int(st // 60))


def leg_cached(key, now=None):
    """(True, row-or-None) when find_for_leg would answer `key` from the cache (no read), else (False, None)."""
    return _cached(key, time.time() if now is None else now) if key else (True, None)


def find_for_leg(player_key, prop, scope, book, line_type, slug, start, now=None, reader=None):
    """A studio leg whose ident has no closing row: the closing row for the same player / stat / maps / platform / line type that
    locked within LEG_WINDOW_S of the leg's start and was saved under the leg's EXACT match slug (never a fuzzy match). One
    indexed (ix_cl_player), bounded read behind the lookup cache (a miss past the window + FINAL_AFTER_S is kept). None unless
    exactly one row matches. reader: a shared Reader for every fallback of one request (the caller closes it)."""
    now = time.time() if now is None else now
    k = leg_key(player_key, prop, scope, book, line_type, slug, start)
    if k is None:
        return None
    hit, row = _cached(k, now)
    if hit:
        return row
    st = _ts(start)
    own = reader is None
    rd = Reader() if own else reader
    try:
        con = rd.get()
        if con is None:
            return None
        rows = [dict(r) for r in con.execute(
            "SELECT ident, line, open_line, lock_utc, last_seen_at, match_slug, settle_slug FROM closing_lines WHERE player_key = ? "
            "AND prop = ? AND scope = ? AND book = ? AND line_type = ? AND lock_utc >= ? AND lock_utc <= ? LIMIT 20",
            (player_key, prop, str(scope), book, line_type, _iso(st - LEG_WINDOW_S), _iso(st + LEG_WINDOW_S)))]
    except sqlite3.Error:
        return None
    finally:
        if own:
            rd.close()
    same = [r for r in rows if slug in (r.get("match_slug"), r.get("settle_slug"))]
    row = same[0] if len(same) == 1 else None
    _remember(k, row, now, final=row is None and _final(start, now, LEG_WINDOW_S))
    return row


# ------------------------------------------------------------------------------------------------ admin diagnostics: CLV

def _clv(line, closing, pick):
    if pick == "More":
        return closing - line
    if pick == "Less":
        return line - closing
    return None


def _clustered(vals_by_match):
    """Mean and a match-clustered 95% interval of the per-line CLV."""
    vals = [v for vs in vals_by_match.values() for v in vs]
    n = len(vals)
    if not n:
        return None, None
    mean = sum(vals) / n
    if len(vals_by_match) < 2:
        return round(mean, 3), None
    g = len(vals_by_match)
    ss = sum(sum(v - mean for v in vs) ** 2 for vs in vals_by_match.values())
    se = (ss * g / (g - 1)) ** 0.5 / n
    return round(mean, 3), [round(mean - 1.96 * se, 3), round(mean + 1.96 * se, 3)]


def clv_report(picks_reader, now=None, days=REPORT_DAYS):
    """Pick-log lines vs saved closing lines, by group (board grades / member research) and platform. Bounded reads."""
    now = time.time() if now is None else now
    since = _iso(now - days * 86400)
    out = {"version": VERSION, "flag": FLAG, "days": days, "generated": _iso(now), "closing_saved": 0, "closing_stale": 0,
           "pending_in_memory": pending_count(), "stale_after_min": STALE_S // 60, "groups": [],
           "note": "CLV = closing minus line for Over, line minus closing for Under (line points; + = the closing line moved toward the logged side). Closing = the last line seen before lock."}
    con = _reader()
    if con is None:
        return out
    try:
        crow = [dict(r) for r in con.execute(
            "SELECT book, event_id, match_slug, settle_slug, player_key, prop, scope, line_type, line, lock_utc, last_seen_at, start_utc "
            "FROM closing_lines WHERE lock_utc >= ? ORDER BY lock_utc DESC LIMIT ?", (since, REPORT_CLOSING_MAX))]
    finally:
        con.close()
    out["closing_saved"] = len(crow)
    idx = {}
    for c in crow:
        stale = is_stale(c)
        out["closing_stale"] += int(stale)
        base = (c["book"], c["player_key"], c["prop"], str(c["scope"]), c["line_type"])
        for m in {c.get("match_slug"), c.get("settle_slug")} - {None, ""}:
            idx.setdefault(base + ("slug", m), (c, stale))
        st = _ts(c.get("start_utc"))
        if st is not None:
            idx.setdefault(base + ("start", int(st // 60)), (c, stale))
    pcon = picks_reader()
    q = ("SELECT id, book, player, prop, scope, line_type, match_slug, match_start, line, pick, units, test{} FROM picks "
         "WHERE match_start >= ? AND (test IS NULL OR test != 1) ORDER BY id DESC LIMIT ?")
    try:
        try:
            picks = [dict(r) for r in pcon.execute(q.format(", on_board"), (since, REPORT_PICKS_MAX))]
        except sqlite3.OperationalError:          # a pick log from before picklog's on_board column
            picks = [dict(r) for r in pcon.execute(q.format(""), (since, REPORT_PICKS_MAX))]
    finally:
        pcon.close()
    groups = {}
    unmatched = no_platform = 0
    for p in picks:
        if not p.get("book"):
            no_platform += 1
            continue
        lt = p.get("line_type") or "standard"
        lt = lt if lt in ("goblin", "demon") else "standard"
        base = (p["book"], norm(p["player"]), p["prop"], str(p.get("scope") or "12"), lt)
        hit = idx.get(base + ("slug", p.get("match_slug")))
        st = _ts(p.get("match_start"))
        if hit is None and st is not None:
            hit = idx.get(base + ("start", int(st // 60)))
        if hit is None or _num(p.get("line")) is None:
            unmatched += 1
            continue
        c, stale = hit
        v = _clv(float(p["line"]), float(c["line"]), p.get("pick"))
        if v is None:
            continue
        # board provenance survives a member opening the line (picklog resets test 2 -> 0 then; on_board keeps it)
        gname = "board" if p.get("test") == 2 or (p.get("on_board") or 0) in (1, 2) else "member"
        g = groups.setdefault((gname, p["book"]), {"by_match": {}, "staked": {}, "lean": {}, "pos": 0, "zero": 0, "neg": 0, "stale": 0})
        mk = c.get("event_id") or p.get("match_slug")
        g["by_match"].setdefault(mk, []).append(v)
        (g["staked"] if (p.get("units") or 0) > 0 else g["lean"]).setdefault(mk, []).append(v)
        g["pos" if v > 0 else "neg" if v < 0 else "zero"] += 1
        g["stale"] += int(stale)
    for (gname, book), g in sorted(groups.items()):
        mean, ci = _clustered(g["by_match"])
        sm, sci = _clustered(g["staked"])
        lm, lci = _clustered(g["lean"])
        n = g["pos"] + g["zero"] + g["neg"]
        out["groups"].append({"group": gname, "book": book, "n": n, "matches": len(g["by_match"]), "clv_mean": mean, "clv_ci95": ci,
                              "moved_toward": g["pos"], "unchanged": g["zero"], "moved_against": g["neg"], "stale_closing": g["stale"],
                              "staked_n": sum(len(v) for v in g["staked"].values()), "staked_clv_mean": sm, "staked_clv_ci95": sci,
                              "lean_n": sum(len(v) for v in g["lean"].values()), "lean_clv_mean": lm, "lean_clv_ci95": lci})
    out["picks_unmatched"] = unmatched
    out["picks_without_platform"] = no_platform
    return out


def report_cached(picks_reader, ttl=CACHE_TTL):
    """clv_report, rebuilt by one caller at most every `ttl` seconds. None with the flag off."""
    if not enabled():
        return None
    with _REPORT["lock"]:
        if _REPORT["doc"] is None or time.time() - _REPORT["at"] > ttl:
            try:
                _REPORT.update(doc=clv_report(picks_reader), at=time.time())
            except Exception as e:      # noqa: BLE001 - diagnostics say so, never fail the page
                return {"version": VERSION, "error": f"closing-line report failed: {type(e).__name__}"}
        return _REPORT["doc"]
