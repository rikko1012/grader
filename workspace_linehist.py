"""SCOUT_LINE_HISTORY (lh-1, owner flag, default OFF): line history + closing-line markers in the CS2 player card.

Spec 4.6 (competitor teardown): one line per app over time under the existing chart, closing-line markers on the past-results
bars, "L10 vs closing" and the average closing line. Borrowed from Upside / Outlier (line chart, closing markers); improved by
reading only what Scout itself stored (no new data source), keeping every move and close with its time, and showing the
member's own time zone in the browser.

Stores it READS (never writes, never creates):
- PrizePicks: prizepicks.HISTORY (pp_lines.jsonl, every observed open / move / id change / removal, keyed by market_key).
- Underdog: .cache/ud_moves.db (underdog._log_moves: every new line and every change of line or payout).
- Any book: the board row's own opening line / first sighting / last move (open_line, first_seen, moved_at) when the stores
  above hold nothing for it.
- Closing lines: closing_lines.db (closing_lines.rows_for, saved once at lock), through the same server.shared key the
  existing L10-vs-closing block uses, so both share one read.

Capacity (700 members): nothing here is a new route. The block is built inside /line's shared single-flight copy (one build per
row per LINE_TTL for every member). The two move stores are tailed incrementally into one bounded in-memory index (only new
bytes / rows are read; one refresher at a time, the others use the index as it stands; offers whose start is more than
KEEP_PAST_S ago are dropped). Never a grade input: nothing here is read by the grader, stakes, forecasts or settlement.
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FLAG = "SCOUT_LINE_HISTORY"
VERSION = "lh-1"
REFRESH_S = 30                 # the move stores are tailed at most this often
KEEP_PAST_S = 2 * 86400        # offers that started longer ago than this leave the index
PER_KEY_MAX = 80               # moves kept per offer
KEYS_MAX = 30000               # offers kept in the index
READ_MAX_BYTES = 64 << 20      # one tail pass reads at most this much of the PrizePicks log
READ_MAX_ROWS = 200000         # ... and at most this many Underdog rows
MARKS_MAX = 20                 # closing markers for the newest 20 bars (the chart's largest window)

LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
_LT_LABEL = {"goblin": "Goblin", "demon": "Demon"}

_LOCK = threading.Lock()
_IDX = {"pp": {}, "ud": {}, "pp_pos": 0, "pp_path": None, "ud_rowid": 0, "ud_path": None, "at": 0.0}


def enabled():
    return os.environ.get(FLAG) == "1"


def reset():
    """Tests: forget the index."""
    with _LOCK:
        _IDX.update(pp={}, ud={}, pp_pos=0, pp_path=None, ud_rowid=0, ud_path=None, at=0.0)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _ts(s):
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if t is not None else None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _book(x):
    return x.get("book") or "underdog"


def _line_type(x):
    return x["odds_type"] if _book(x) == "prizepicks" and x.get("odds_type") in ("goblin", "demon") else "standard"


def _ud_key(player, prop, scope, start):
    return (_norm(player), prop or "", str(scope or "12"), str(start or ""))


def _pp_key(x):
    import prizepicks
    return prizepicks.market_key(x)


# ------------------------------------------------------------------------------------------------ the move-store index

def _add(store, key, ev, start_t):
    lst = store.get(key)
    if lst is None:
        if len(store) >= KEYS_MAX:
            store.pop(next(iter(store)))
        lst = store[key] = {"start": start_t, "ev": []}
    lst["ev"].append(ev)
    if len(lst["ev"]) > PER_KEY_MAX:
        del lst["ev"][0]


def _prune(store, now):
    cut = now - KEEP_PAST_S
    for k in [k for k, v in store.items() if v["start"] is not None and v["start"] < cut]:
        store.pop(k, None)


def _tail_pp(now):
    import prizepicks
    path = Path(prizepicks.HISTORY)
    if str(path) != _IDX["pp_path"]:
        _IDX.update(pp={}, pp_pos=0, pp_path=str(path))
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < _IDX["pp_pos"]:                       # the log was replaced: start again
        _IDX.update(pp={}, pp_pos=0)
    if size == _IDX["pp_pos"]:
        return
    cut = now - KEEP_PAST_S
    with open(path, "rb") as f:
        f.seek(_IDX["pp_pos"])
        chunk = f.read(READ_MAX_BYTES)
    end = chunk.rfind(b"\n")
    if end < 0:
        return                                      # a half-written last line: read it next time
    for raw in chunk[:end].split(b"\n"):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict) or not e.get("market"):
            continue
        st = _ts(e.get("start"))
        if st is not None and st < cut:
            continue
        at = _ts(e.get("at"))
        if at is None:
            continue
        _add(_IDX["pp"], e["market"], {"at": at, "line": _num(e.get("line")), "event": e.get("event"),
                                       "open_line": _num(e.get("open_line")), "from_line": _num(e.get("from_line"))}, st)
    _IDX["pp_pos"] += end + 1


def _tail_ud(now):
    path = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / ".cache" / "ud_moves.db"
    if str(path) != _IDX["ud_path"]:
        _IDX.update(ud={}, ud_rowid=0, ud_path=str(path))
    if not path.exists():
        return
    cut = now - KEEP_PAST_S
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return
    try:
        rows = con.execute("SELECT rowid, at, player, prop, scope, start, line, higher, lower FROM moves WHERE rowid > ? "
                           "ORDER BY rowid LIMIT ?", (_IDX["ud_rowid"], READ_MAX_ROWS)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    for rid, at, player, prop, scope, start, line, hi, lo in rows:
        _IDX["ud_rowid"] = rid
        if scope is None:                           # logged before the scope column: never guessed into a scope
            continue
        st, t = _ts(start), _ts(at)
        if t is None or (st is not None and st < cut):
            continue
        _add(_IDX["ud"], _ud_key(player, prop, scope, start), {"at": t, "line": _num(line), "higher": _num(hi), "lower": _num(lo)}, st)


def refresh(now=None, force=False):
    """Tail both stores (at most every REFRESH_S). A caller that finds another refresh running uses the index as it stands."""
    now = time.time() if now is None else now
    if not force and now - _IDX["at"] < REFRESH_S:
        return
    if not _LOCK.acquire(blocking=False):
        return
    try:
        if not force and now - _IDX["at"] < REFRESH_S:
            return
        for fn in (_tail_pp, _tail_ud):
            try:
                fn(now)
            except Exception:
                pass
        _prune(_IDX["pp"], now)
        _prune(_IDX["ud"], now)
        _IDX["at"] = now
    finally:
        _LOCK.release()


def _stored(x):
    book = _book(x)
    try:
        if book == "prizepicks":
            v = _IDX["pp"].get(_pp_key(x))
        elif book == "underdog":
            v = _IDX["ud"].get(_ud_key(x.get("player"), x.get("prop"), x.get("scope"), x.get("start")))
        else:
            v = None
    except Exception:
        v = None
    return list(v["ev"]) if v else []


# ------------------------------------------------------------------------------------------------ one app's line over time

def app_series(x, now=None):
    """One app's moves for one offer: [{at, line, kind}] oldest first (kind: open / move / payout / pulled / seen)."""
    now = time.time() if now is None else now
    pts = []
    ev = _stored(x)
    first_seen, open_line = _ts(x.get("first_seen")), _num(x.get("open_line"))
    for e in ev:
        line = e.get("line")
        if e.get("event") == "removed":
            pts.append({"at": e["at"], "line": None, "kind": "pulled"})
            continue
        if e.get("event") == "id_change" or line is None:
            continue
        if not pts and e.get("open_line") is not None and e["open_line"] != line:
            pts.append({"at": first_seen if first_seen is not None and first_seen < e["at"] else e["at"], "line": e["open_line"], "kind": "open"})
        prev = next((p["line"] for p in reversed(pts) if p["line"] is not None), None)
        if prev is None:
            kind = "open"
        elif prev != line:
            kind = "move"
        elif "higher" in e:
            kind = "payout"                         # Underdog logs a payout change with the same line
        else:
            continue
        pts.append({"at": e["at"], "line": line, "kind": kind, **({"higher": e["higher"], "lower": e["lower"]} if kind == "payout" or "higher" in e else {})})
    if not pts:                                     # nothing stored for it: the row's own opening line and last move
        moved = _ts(x.get("moved_at"))
        if open_line is not None and first_seen is not None:
            pts.append({"at": first_seen, "line": open_line, "kind": "open"})
        if open_line is not None and open_line != _num(x.get("line")) and moved is not None:
            pts.append({"at": moved, "line": _num(x.get("line")), "kind": "move"})
    cur = _num(x.get("line"))
    # Underdog's updated_at is when Underdog last changed the line (its seen_at is Scout's read time since 2026-11-05): kept as before
    ud = (x.get("book") or "underdog") == "underdog"
    seen = _ts((x.get("updated_at") if ud else None) or x.get("seen_at") or x.get("updated_at")) or now
    last = next((p for p in reversed(pts) if p["line"] is not None), None)
    if cur is not None and (last is None or last["line"] != cur or pts[-1]["line"] is None):
        pts.append({"at": max(seen, pts[-1]["at"]) if pts else seen, "line": cur, "kind": "move" if last else "seen"})   # seen: no stored opening for it
    pts.sort(key=lambda p: p["at"])
    return [dict(p, at=_iso(p["at"])) for p in pts]


# ------------------------------------------------------------------------------------------------ closing markers

def _hltv_key(mid):
    s = str(mid or "")
    m = re.fullmatch(r"hltv[-:](\d+)", s)
    return f"hltv:{m.group(1)}" if m else s or None


def closing_marks(ser, mids, closing_rows, stale=None):
    """Each of the newest MARKS_MAX past games with the closing line saved for THAT match on this app (stale closings left off,
    as in L10 vs closing). {"marks": [{date, opponent, closing}], "avg_l10": mean of the last 10 games' closings}."""
    by = {}
    for c in closing_rows or []:
        k = _hltv_key(c.get("event_id"))
        if k and k not in by and _num(c.get("line")) is not None and not (stale and stale(c)):
            by[k] = _num(c["line"])
    marks, l10 = [], []
    for i, (s, mid) in enumerate(list(zip(ser or [], mids or []))[:MARKS_MAX]):
        cl = by.get(_hltv_key(mid))
        if cl is None:
            continue
        marks.append({"date": s.get("date"), "opponent": s.get("opponent"), "closing": cl})
        if i < 10:
            l10.append(cl)
    return {"marks": marks, "avg_l10": round(sum(l10) / len(l10), 2) if l10 else None, "n_l10": len(l10)}


# ------------------------------------------------------------------------------------------------ the /line block

def block(srv, x, siblings, full, closing_args=None, now=None):
    """The card's line-history block, or None (nothing stored at all). siblings = every app's row for this prop and maps."""
    now = time.time() if now is None else now
    refresh(now)
    apps = []
    for o in [x] + [s for s in siblings or [] if s is not None and s is not x]:
        book, lt = _book(o), _line_type(o)
        if any(a["platform"] == book and a["line_type"] == lt for a in apps):
            continue
        pts = app_series(o, now)
        if not pts:
            continue
        label = LABEL.get(book, book) + (f" {_LT_LABEL[lt]}" if lt in _LT_LABEL else "")
        apps.append({"platform": book, "line_type": lt, "label": label, "current": _num(o.get("line")), "points": pts,
                     "moves": sum(1 for p in pts if p["kind"] == "move"), "selected": o is x})
    marks = {"marks": [], "avg_l10": None, "n_l10": 0}
    if closing_args and full and full.get("ser") and full.get("mids"):
        try:
            import closing_lines as cl
            rows = srv.shared(("ws_closing",) + tuple(closing_args), lambda: (200, cl.rows_for(*closing_args)), ttl=cl.CACHE_TTL)[1]
            marks = closing_marks(full["ser"], full["mids"], rows or [], stale=cl.is_stale)
        except Exception:
            pass
    if not apps and not marks["marks"]:
        return None
    lock = _ts(x.get("lock_at") or x.get("start"))
    return {"version": VERSION, "apps": apps, "closing_marks": marks["marks"], "avg_closing_l10": marks["avg_l10"],
            "closing_n_l10": marks["n_l10"], "closing_platform": LABEL.get(_book(x), _book(x)), "lock_at": _iso(lock), "as_of": _iso(now)}
