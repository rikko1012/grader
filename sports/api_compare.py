"""Compare tool share-link check (compare agent, cmp-1). ONE read-only member route, registered in sports/web.py:

  GET /api/sports/compare/check?league=nba&items=<platform>~<offer id>~<line>[,...]   (at most MAX_ITEMS;
  the offer id with %, ~ and , percent-escaped - web/sports/compare.js shareItem)

A shared comparison link carries offer ids + the line each prop had when it was shared. This answers, per item, from
the worker-built board snapshots (board.json + board_more.json - never the store, no model work):
  ok       the offer is on the board at the same line, its game has not started  -> current record id + market key
  moved    same offer, different line now ("line moved 20.5 -> 21.5")             -> refused
  pulled   the offer is no longer on the board                                     -> refused
  started  its game has started (or is live / final)                               -> refused
  bad      the item is not a valid <platform>~<offer id>~<line>
The parsed index is cached per file change (mtime + size), so a request costs one dict lookup per item.
"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import unquote
from datetime import datetime, timezone

from . import api_board

RULE = "cmp-1"
MAX_ITEMS = 6
_ITEM = re.compile(r"^([a-z0-9_:.-]{2,40})~([^~,\s]{1,200})~(-?\d{1,4}(?:\.\d{1,2})?)$")
_REV = re.compile(r":r\d+:[0-9a-f]+$")
_LIVE = re.compile(r"in_progress|final|post|live")
_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()
_INDEX = {}                          # league -> (stamp, index)


def offer_id(record_id: str, platform: str) -> str:
    """"offer:<platform>:<offer_id>:r<n>:<hash>" -> offer_id (= web/sports/picks.js offerIdOf)."""
    x = str(record_id or "")
    if x.startswith("offer:"):
        x = x[len("offer:"):]
    if x.startswith(platform + ":"):
        x = x[len(platform) + 1:]
    return _REV.sub("", x)


def build_index(boards):
    """[board.json obj, board_more.json obj] -> {(platform, offer_id): {r, l, k, e, start, status, started_flag}}."""
    idx = {}
    for b in boards:
        if not b:
            continue
        events = b.get("events") or {}
        for m in b.get("markets") or []:
            ev = events.get(m.get("e")) or {}
            for q in (m.get("q") or []) + (m.get("kn") or []):
                key = (q.get("p"), offer_id(q.get("r"), q.get("p") or ""))
                idx[key] = {"r": q.get("r"), "l": q.get("l"), "k": m.get("k"), "e": m.get("e"),
                            "start": ev.get("start_utc"), "status": ev.get("status"), "started": ev.get("started")}
    return idx


def _load(lg):
    root = api_board._root() / lg
    paths = [root / "board.json", root / "board_more.json"]
    stamp = []
    for p in paths:
        try:
            st = p.stat()
            stamp.append((st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append(None)
    if stamp[0] is None:
        raise api_board.NotReady("board.json")
    stamp = tuple(stamp)
    with _LOCK:
        hit = _INDEX.get(lg)
    if hit and hit[0] == stamp:
        return hit[1]
    with _BUILD_LOCK:                   # single flight: one parse of both boards per file change, not one per request
        with _LOCK:
            hit = _INDEX.get(lg)
        if hit and hit[0] == stamp:
            return hit[1]
        boards = [json.loads(api_board._read(p)) if s is not None else None for p, s in zip(paths, stamp)]
        idx = build_index(boards)
        del boards
        with _LOCK:
            _INDEX[lg] = (stamp, idx)
    return idx


def _started(row, now):
    if row.get("started") is True:
        return True
    # the board's lock rule (snapshots._locked): any status but scheduled is locked (bug sweep 2026-11-06 #30: this
    # matched live/closed statuses only, so 'unknown' with a future start answered 'ok' while the board refused picks)
    if row.get("status") and str(row["status"]).lower() != "scheduled":
        return True
    s = row.get("start")
    if not s:
        return False
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t <= now


_CLOSED = {"postponed": "Game postponed", "cancelled": "Game cancelled", "suspended": "Game suspended",
           "delayed": "Game delayed"}


def _closed_text(row):
    """Why a shared prop's game no longer takes picks - a postponed game is not 'started' (was: 'post' in the live
    pattern matched 'postponed')."""
    st = str(row.get("status") or "").lower()
    if st in _CLOSED:
        return _CLOSED[st]
    if st and st != "scheduled" and not _LIVE.search(st) and row.get("started") is not True:
        return "Game closed to new picks"   # locked by its status (e.g. 'unknown'), not by a start
    return "Game has started"


def _ln(x):
    return str(int(x)) if float(x) == int(x) else str(x)


def check_items(idx, raw, now=None):
    """raw item strings -> per-item answers (pure; tested directly)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for s in raw:
        m = _ITEM.match(s)
        if not m:
            out.append({"item": s[:200], "status": "bad", "text": "Not a valid shared prop"})
            continue
        p, oid, line = m.group(1), unquote(m.group(2)), float(m.group(3))
        base = {"item": s, "p": p, "o": oid, "l": line}
        row = idx.get((p, oid))
        if row is None:
            out.append({**base, "status": "pulled", "text": "No longer on the board (pulled or settled)"})
        elif _started(row, now):
            out.append({**base, "status": "started", "text": _closed_text(row), "r": row["r"], "k": row["k"]})
        elif row["l"] is None or abs(float(row["l"]) - line) > 1e-9:
            out.append({**base, "status": "moved", "now_line": row["l"], "r": row["r"], "k": row["k"],
                        "text": "Line moved " + _ln(line) + " -> " + (_ln(row["l"]) if row["l"] is not None else "none")})
        else:
            out.append({**base, "status": "ok", "r": row["r"], "k": row["k"], "e": row["e"]})
    return out


def check(req):
    lg = api_board._league(req)
    if not lg:
        return 400, {"error": "league must be nba or nfl"}
    raw = [x for x in (req.query.get("items") or "").split(",") if x]
    if not raw:
        return 400, {"error": "items required"}
    if len(raw) > MAX_ITEMS:
        return 400, {"error": f"at most {MAX_ITEMS} items"}
    try:
        idx = _load(lg)
    except api_board.NotReady:
        return 503, {"error": "snapshot_not_ready"}
    items = check_items(idx, raw)
    return 200, {"rule": RULE, "league": lg, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "items": items, "ok": sum(1 for i in items if i["status"] == "ok")}
