"""Research workspace API, /preview/workspace/api/*: the CS2 path from saved grades through tracking and settlement.

Reads saved grades and offers; never grades, logs a forecast or touches picks.db (workspace_store.py owns workspace.db). The
server runs as `python server.py`, so this module never imports server (that would load a second copy): the handler passes its
own module in as `srv`. Imported on the first allowed request; with the preview off, no thread or database file exists.

For 700 members at once: one board snapshot every 5 s over server.graded_board's single flight, shared by /board and every
check; identical bytes keep the old object, so nothing is compressed twice. Forecast lookups, history and /line are shared per
line with bounded read slots. /mine and /state live in their own byte-bounded cache, never in server._RESULTS and never through
server.encoded (its lock table is for a small fixed set of keys). That cache holds one entry per member and kind, checked against
the revision in memory, so a hit reads no database and no member can push another's entry out with invented keys; only the
newest /mine page is kept, and a read that reaches the database counts against a per-member limit.

A leg is rebuilt from the snapshot on every check: client fields are compared, never trusted."""
import gzip
import hashlib
import json
import os
import re
import threading
import time
import zlib
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import workspace_rules as rules
import workspace_store as wstore

SPORTS = ("cs2", "lol", "nba", "nfl")
SAMPLE_SPORTS = ("lol", "nba", "nfl")
BOOKS = ("underdog", "prizepicks")
SCOPE = {"1": ("map1", "Map 1"), "12": ("maps12", "Maps 1-2"), "123": ("maps13", "Maps 1-3")}
STAT = {"kills": "Kills", "hs": "Headshots"}
STAT_COL = {"kills": "kills", "hs": "headshots"}
SIDE_WORD = {"over": "Over", "under": "Under"}
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
OWNER_ID = "wm-owner-admin-token"
CHECK_TTL_S = 300
MAX_LEGS = 8
STATE_MAX = 16 * 1024
STATE_MAX_DEPTH = 12
LINE_MAX = 100000
RECHECK_PER_MIN, TRACK_PER_MIN, STATE_PER_MIN, MINE_PER_MIN = 40, 20, 30, 40
MINE_PAGE = 50
FORECAST_MAX_GAP = 0.01                 # picklog._unchanged skips a new row when the chance moved less than this
DISPLAY_MAX_GAP = 5e-4
FORECAST_TTL, PENDING_TTL = 20, 2
PENDING_WINDOW_S = 180                  # a line graded this recently and not in the pick log yet is still being written
SNAP_MAX_AGE_S = 30                     # a snapshot this old means the rebuild keeps failing: nothing is tracked on it
LINE_TTL, HISTORY_TTL = 15, 600
CONTEXT_KEYS = (("maps_played", "Maps in sample"), ("rating", "Rating"), ("kpr", "Kills per round"),
                ("kpr_last5", "Kills per round, last 10 matches, Maps 1-2"), ("adr", "Damage per round"), ("kast", "KAST"), ("hs_pct", "Headshot rate"),
                ("days_since_last_map", "Days since last map"), ("recent_avg", "Recent average, Maps 1-2"), ("recent_median", "Recent median, Maps 1-2"),
                ("recent_n", "Recent matches, Maps 1-2"))

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LEG_ID_RE = re.compile(r"^[^\x00-\x1f]{1,300}$")
_BEFORE_RE = re.compile(r"^[0-9T:.+\-Z]{10,40}$")
_HLTV_SLUG_RE = re.compile(r"hltv-(\d+)")
_FC_SLOTS = threading.BoundedSemaphore(8)
_HIST_SLOTS = threading.BoundedSemaphore(3)
_HITS = {"recheck": {}, "track": {}, "state": {}, "mine": {}}
_ST = {"store": None}
_ST_LOCK = threading.Lock()
_SNAP = {"at": 0.0, "body": None, "index": {}, "props": {}, "last": {}, "doc": None, "gen": 0, "feeds": {}}


class Lru:
    """Newest-kept cache bounded by entries and by bytes. An entry is (bytes, gzipped, etag, revision)."""

    def __init__(self, n=4096, cap=16 * 1024 * 1024):
        self.n, self.cap, self.size, self.d, self.lock = n, cap, 0, OrderedDict(), threading.Lock()

    def get(self, k):
        with self.lock:
            v = self.d.get(k)
            if v is not None:
                self.d.move_to_end(k)
            return v

    def put(self, k, v):
        with self.lock:
            old = self.d.pop(k, None)
            if old is not None:
                self.size -= len(old[0])
            self.d[k] = v
            self.size += len(v[0])
            while self.d and (len(self.d) > self.n or self.size > self.cap):
                _, o = self.d.popitem(last=False)
                self.size -= len(o[0])

    def clear(self):
        with self.lock:
            self.d.clear()
            self.size = 0


_PRIVATE = Lru()


def data_dir():
    import picklog
    return Path(os.environ.get("CS2_DATA_DIR") or Path(picklog.DB).parent)


def store():
    """The one store, opened on the first allowed request; its writer and settle threads start here and nowhere else."""
    path = data_dir() / "workspace.db"
    s = _ST["store"]
    if s is None or s.path != path:
        with _ST_LOCK:
            s = _ST["store"]
            if s is None or s.path != path:
                if s is not None:
                    s.close()
                s = _ST["store"] = wstore.Store(path)
                s.start_settler()
    return s


def reset():
    """Tests: forget the store and every cache."""
    with _ST_LOCK:
        if _ST["store"] is not None:
            _ST["store"].close()
        _ST["store"] = None
    _PRIVATE.clear()
    _SNAP.update(at=0.0, body=None, index={}, props={}, last={}, doc=None, gen=0, feeds={})
    for d in _HITS.values():
        d.clear()


# ---------------------------------------------------------------------------------------------------------------- identity

def member_key(sid):
    return "wm-" + hashlib.sha256(("workspace|" + str(sid)).encode("utf-8")).hexdigest()[:24]


def _token_in_use(h, q):
    token = q.get("token")
    ref = urlparse(h.headers.get("Referer") or "")
    if not token and ref.path == "/preview/workspace":
        token = (parse_qs(ref.query).get("token") or [None])[0]
    return h.authed(token)


def identity(h, q):
    """(hashed member id, scratch, display name). The raw Whop id never reaches the database. An admin token with no session is
    one fixed owner whose rows carry scratch=1."""
    sess = h.member()
    if sess and sess.get("user") and sess.get("ok"):
        return member_key(h.studio_member()), False, sess.get("name") or "Member"
    if h.load_test():
        return member_key(h.studio_member()), False, "Load test"
    if _token_in_use(h, q):
        return OWNER_ID, True, "Owner"
    sid = h.studio_member()
    return (member_key(sid), False, "Member") if sid else (None, False, None)


# ---------------------------------------------------------------------------------------------------------------- rows

def _iso(t):
    if t in (None, "", 0):
        return None
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    return str(t)


def _book(x):
    return x.get("book") or "underdog"


def line_type_of(x):
    if _book(x) == "prizepicks" and x.get("odds_type") in ("goblin", "demon"):
        return x["odds_type"]
    return "standard"


def offer_rev(x):
    """The identity of what the platform offers on this row now: anything the member could see change changes it."""
    fields = [_book(x), x.get("id"), x.get("line"), line_type_of(x), x.get("higher"), x.get("lower"), x.get("mod_higher"), x.get("mod_lower"),
              _offered(x, "over"), _offered(x, "under"), x.get("start")]
    return "d-" + hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()[:16]


def _offered(x, side):
    k = side + "_available"
    if k in x:
        return bool(x[k])
    return x.get("higher" if side == "over" else "lower") is not None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _slug_of(x):
    return x.get("settle_slug") or x.get("match_slug")


def event_id(x):
    """One match under any slug, by server.same_match's rule: the row's canonical id, or the id an HLTV fallback slug names."""
    if x.get("match_id"):
        return str(x["match_id"]), "canonical"
    slug = _slug_of(x)
    m = _HLTV_SLUG_RE.fullmatch(str(slug or ""))
    if m:
        return f"hltv:{m.group(1)}", "canonical"
    if slug:
        return str(slug), "derived"
    return "row-" + hashlib.sha256(f"{x.get('matchup')}|{x.get('start')}".encode()).hexdigest()[:12], "derived"


def read_rev(x):
    """The identity of Scout's read as the slip shows it: side, chance and letter. Stake is left out: the same-match cap moves it
    whenever another row in the group changes, and the slip does not show it."""
    fields = [_side(x), x.get("p"), x.get("model_grade") or x.get("grade")]
    return "r-" + hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()[:16]


def _nick(x):
    return x.get("nick") or x.get("player") or ""


def _side(x):
    return {"More": "over", "Less": "under"}.get(x.get("side"))


def _teams(x):
    a, b = x.get("team_name") or x.get("team"), x.get("opp_name") or x.get("opponent")
    return sorted([str(a or ""), str(b or "")], key=str.lower)


def _role(x):
    if x.get("awp") is True and x.get("awp_conf") in ("confirmed", "measured"):
        return "AWP"
    return "IGL" if x.get("igl") is True else ""


def _push(x):
    try:
        return (x.get("model_outcomes") or {}).get("push")
    except AttributeError:
        return None


def record_of(x):
    """One dr-1 display record from one board row: saved values only, nothing recomputed."""
    book, side, scope = _book(x), _side(x), x.get("scope") or "12"
    eid, kind = event_id(x)
    period, period_label = SCOPE.get(scope, (scope, scope))
    prop, lt = x.get("prop"), line_type_of(x)
    teams, nick = _teams(x), _nick(x)
    grade = x.get("model_grade") or x.get("grade")
    p = x.get("p")
    n, over, push = x.get("l10_n"), x.get("l10_over"), x.get("l10_push") or 0
    l10 = {"over": over, "under": n - over, "push": push, "n": n + push} if n and over is not None else None
    key = ".".join(str(k) for k in ("cs2", eid, _norm(nick), prop, period, x.get("line"))) + ("" if lt == "standard" else "." + lt)
    moved = x.get("open_line") is not None and x.get("open_line") != x.get("line")
    return {"sport": "cs2", "key": key,
            "event": {"id": eid, "id_kind": kind, "label": " vs ".join(t for t in teams if t), "teams": teams, "competition": x.get("event") or "",
                      "best_of": x.get("bo"), "start_utc": x.get("start"), "lock_utc": x.get("lock_at"), "status": "scheduled"},
            "player": {"id": x.get("player_id"), "key": _norm(nick), "name": nick, "team": x.get("team_name") or x.get("team") or "",
                       "opponent": x.get("opp_name") or x.get("opponent") or "", "role": _role(x)},
            "market": {"stat": prop, "stat_label": STAT.get(prop, prop), "period": period, "period_label": period_label, "line_type": lt},
            "offer": {"platform": book, "platform_label": LABEL.get(book, book), "offer_id": str(x.get("id")), "revision": offer_rev(x), "revision_kind": "derived",
                      "line": x.get("line"), "seen_at": _iso(x.get("seen_at") or x.get("updated_at")),
                      "over": {"offered": _offered(x, "over"), "payout": x.get("higher" if book == "underdog" else "mod_higher"), "price": x.get("price_higher")},
                      "under": {"offered": _offered(x, "under"), "payout": x.get("lower" if book == "underdog" else "mod_lower"), "price": x.get("price_lower")},
                      "moved_from": x.get("open_line") if moved else None, "moved_at": _iso(x.get("moved_at")) if moved else None},
            "scout": {"side": side, "read_rev": read_rev(x), "p_side": p, "p_over": p if side == "over" else None, "p_under": p if side == "under" else None, "p_push": _push(x),
                      "projection": x.get("proj"), "range80": None, "grade": grade if grade in ("A+", "A", "A-", "B+", "B", "B-") else None, "units": x.get("units"),
                      "stake_reason": x.get("stake_reason") or x.get("cap_reason"), "hold": None, "model_version": x.get("graded_model"), "forecast_at": x.get("graded_at"),
                      "validation": {"kind": "live"}},
            "history": {"l10": l10, "avg": x.get("l10_avg"), "recent": None},
            "state": {"kind": "ok" if not x.get("unavailable") and not x.get("hltv_retry") else "held",
                      "reason": x.get("unavailable") if isinstance(x.get("unavailable"), str) else None}}


def _feed_state(srv, book, payload, failed):
    if payload is None or failed:
        return {"state": "failed", "at": _iso((payload or {}).get("updated")), "error": "Scout could not read this feed just now"}
    if book == "prizepicks" and payload.get("feed"):
        f = payload["feed"]
        return {"state": f.get("state") or "ok", "at": _iso(f.get("at") or payload.get("updated")), "error": f.get("error")}
    at = payload.get("updated")
    state = "failed" if payload.get("error") else "ok"
    if state == "ok" and isinstance(at, (int, float)) and time.time() - at > srv.comparison.STALE_MIN * 60:
        state = "stale"
    return {"state": state, "at": _iso(at), "error": payload.get("error")}


def _build(srv):
    docs, failed = {}, set()
    for bk in BOOKS:
        try:
            docs[bk] = _SNAP["last"][bk] = srv.graded_board(bk)
        except Exception:                       # a feed blip keeps the last rows; the feed state says so
            docs[bk] = _SNAP["last"].get(bk)
            failed.add(bk)
    if not any(docs.values()):
        raise RuntimeError("no board")
    records, index, props = [], {}, {}
    for bk, p in docs.items():
        for x in (p or {}).get("rows") or []:
            rec = record_of(x)
            records.append(rec)
            index[(_book(x), str(x.get("id")))] = x
            props.setdefault((rec["event"]["id"], rec["player"]["key"], x.get("prop"), x.get("scope") or "12"), []).append((_book(x), str(x.get("id"))))
    feeds = {bk: _feed_state(srv, bk, docs.get(bk), bk in failed) for bk in BOOKS}
    times = sorted(f["at"] for f in feeds.values() if f["at"])
    version = next((p.get("version") for p in docs.values() if p), None)
    body = json.dumps({"version": version, "board_at": times[0] if times else None, "feeds": feeds, "records": records},
                      default=str, separators=(",", ":")).encode()
    if _SNAP["body"] == body:
        body = _SNAP["body"]                    # identical bytes: the same object, so nothing is compressed again
    _SNAP.update(at=time.time(), body=body, index=index, props=props, feeds=feeds, gen=_SNAP["gen"] + 1)
    return 200, body


def snapshot(srv):
    """The shared snapshot, or None when no board has ever been read. A failed rebuild keeps the last good copy."""
    try:
        srv.shared(("ws_board",), lambda: _build(srv), ttl=srv.BOARD_TTL)
    except Exception:
        pass
    return _SNAP if _SNAP["body"] is not None else None


# ---------------------------------------------------------------------------------------------------------------- forecasts

def _fc_read(srv, x):
    if not _FC_SLOTS.acquire(timeout=1.0):
        return 200, {"state": "busy"}, 1
    try:
        con = srv.picklog._read_db()
        try:
            pick = con.execute("SELECT id, status FROM picks WHERE match_slug = ? AND player_id = ? AND prop = ? AND COALESCE(scope, '12') = ? AND line = ? "
                               "AND COALESCE(line_type, 'standard') = ? AND COALESCE(book, 'underdog') = ? AND test IN (0, 2) ORDER BY id DESC LIMIT 1",
                               (_slug_of(x), x.get("player_id"), x.get("prop"), x.get("scope") or "12", x.get("line"), line_type_of(x), _book(x))).fetchone()
            f = con.execute("SELECT id, logged_at, line, pick, p_pick, p_more, grade, units, model_version FROM forecasts WHERE pick_id = ? "
                            "ORDER BY id DESC LIMIT 1", (pick["id"],)).fetchone() if pick else None
        finally:
            con.close()
    except Exception:                           # a locked or unreadable pick log is not an answer about this line
        return 200, {"state": "busy"}, 1
    finally:
        _FC_SLOTS.release()
    if f is None:
        return (200, {"state": "pending"}, PENDING_TTL) if _just_graded(x) else (200, {"state": "none"}, PENDING_TTL)
    return 200, {"state": "ok", "pick_id": pick["id"], "pick_status": pick["status"], **dict(f)}


def _just_graded(x):
    ga = _ts(x.get("graded_at"))
    return ga is not None and (datetime.now(timezone.utc) - ga).total_seconds() < PENDING_WINDOW_S


def forecast_for(srv, x):
    key = ("ws_fc", _book(x), str(x.get("id")), x.get("graded_at"))
    return srv.shared(key, lambda: _fc_read(srv, x), ttl=FORECAST_TTL)[1]


def _ts(s):
    from slip_builder import _ts as parse
    return parse(s)


def _same_stake_and_grade(x, fc):
    """The board copy carries this forecast's grade and stake. server._cap_same_result is the one step that moves them after the
    forecast is logged: it cuts a stake, keeping the logged one in units_full, and can turn the row into a 0U Lean. Display gd-1 sits
    apart in model_grade. Any other gap means the board and the forecast are different grades."""
    capped = x.get("units_full") is not None
    logged_units = x["units_full"] if capped else x.get("units")
    if abs((logged_units or 0) - (fc.get("units") or 0)) > 1e-9:
        return False
    return fc.get("grade") == x.get("grade") or (capped and x.get("grade") == "LEAN" and not x.get("units"))


def forecast_problem(srv, x, leg, fc, lock):
    """None when the saved forecast belongs to this exact match, line and period, its pick is still open, the board copy is the
    same forecast, and the leg shows what Scout says now: its side and chance change the pick the member is looking at."""
    def bad(code, text, **kw):
        return dict({"check": 2, "code": code, "text": text}, **kw)
    st = fc.get("state")
    if x.get("state") == "SCOUT" or st == "none":
        return bad("no_forecast", "No saved forecast for this line, so it cannot be tracked.")
    if st != "ok":
        return bad("forecast_pending", "The forecast for this line is still being saved.", retry=True)
    if fc.get("pick_status") != "open":
        return bad("pick_closed", "This line is closed in Scout's pick log, so it cannot be tracked.")
    logged, ok = _ts(fc.get("logged_at")), True
    try:
        same_line = float(fc["line"]) == float(x["line"]) == float(x["graded_line"])
    except (KeyError, TypeError, ValueError):
        same_line = False
    ok = (same_line and fc.get("pick") == x.get("side") and fc.get("model_version") == x.get("graded_model")
          and logged is not None and lock is not None and logged < lock
          and x.get("p") is not None and fc.get("p_pick") is not None and abs(fc["p_pick"] - x["p"]) < FORECAST_MAX_GAP
          and _same_stake_and_grade(x, fc))
    if not ok:
        if _just_graded(x):                     # the regrade is on the board and its forecast is still in the writer's queue
            return bad("forecast_pending", "The forecast for this line is still being saved.", retry=True)
        return bad("forecast_mismatch", "The saved forecast does not match this line.")
    side, shown_p = leg["side"], leg.get("shown_p")
    now_side = _side(x)
    if leg.get("read_rev") != read_rev(x):
        now = SIDE_WORD.get(now_side)
        return bad("display_mismatch", f"Scout's read changed: now {now} {x['p'] * 100:.1f}%." if now else "Scout's read on this line changed.", swap=True)
    if side == now_side and (shown_p is None or abs(shown_p - x["p"]) > DISPLAY_MAX_GAP):
        return bad("display_mismatch", "The chance on your slip is out of date.", swap=True)
    if side != now_side and shown_p is not None:
        return bad("display_mismatch", "The chance on your slip is out of date.", swap=True)
    return None


# ---------------------------------------------------------------------------------------------------------------- checks

def _legs_of(d):
    legs = d.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= MAX_LEGS:
        return None
    out = []
    for l in legs:
        if not isinstance(l, dict) or l.get("sport") not in SPORTS or l.get("side") not in ("over", "under"):
            return None
        if not isinstance(l.get("id"), str) or not _LEG_ID_RE.match(l["id"]) or not isinstance(l.get("book"), str) or len(l["book"]) > 32:
            return None
        if not isinstance(l.get("row_id"), str) or not _ID_RE.match(l["row_id"]):
            return None
        line, p = l.get("line"), l.get("shown_p")
        if isinstance(line, bool) or not isinstance(line, (int, float)) or not -LINE_MAX <= line <= LINE_MAX:    # also refuses 1e999 and NaN
            return None
        if p is not None and (isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1):
            return None
        for k in ("rev", "read_rev"):
            if l.get(k) is not None and (not isinstance(l[k], str) or len(l[k]) > 200):
                return None
        for k in ("team", "player", "event"):
            if l.get(k) is not None and (not isinstance(l[k], str) or len(l[k]) > 120):
                return None
        out.append(l)
    return out if len({l["id"] for l in out}) == len(out) else None


def _feed_leg(x, side):
    return {"side": "More" if side == "over" else "Less", "line": x.get("line"), "book": _book(x), "row_id": x.get("id"), "nick": x.get("player") or x.get("nick"),
            "prop": x.get("prop"), "scope": x.get("scope") or "12", "team": x.get("team"), "opponent": x.get("opponent"), "start": x.get("start"),
            "match_start": x.get("match_start"), "lock_at": x.get("lock_at"), "line_type": line_type_of(x)}


def _pr(check, code, text, **kw):
    return dict({"check": check, "code": code, "text": text}, **kw)


def _current(x, fc):
    side = _side(x)
    return {"row_id": str(x.get("id")), "line": x.get("line"), "rev": offer_rev(x), "read_rev": read_rev(x), "scout_side": side, "p_side": x.get("p"),
            "forecast_id": fc.get("id"), "forecast_at": fc.get("logged_at"), "model_version": x.get("graded_model"), "start_utc": x.get("start"),
            "lock_utc": x.get("lock_at")}


def check_leg(srv, leg, snap, feeds, now):
    """One real leg against the snapshot. -> {ok, problems, current} plus what the entry check needs (private _x, _hit, _fc)."""
    from slip_builder import lock_time
    book, side = leg["book"], leg["side"]
    x = snap["index"].get((book, leg["row_id"]))
    label = LABEL.get(book, book)
    if x is None:
        return {"ok": False, "problems": [_pr(1, "gone", "This line is no longer on the board.")], "current": None}
    problems = []
    feed = (snap.get("feeds") or {}).get(book) or {}
    if feed.get("state") in ("failed", "stale") or now.timestamp() - snap["at"] > SNAP_MAX_AGE_S:
        problems.append(_pr(1, "board_failed", f"Scout could not read the {label} board just now.", retry=True))
    lock = lock_time(x)
    if lock is None:
        problems.append(_pr(0, "start_unknown", "No start time on the feed for this line."))
    elif lock <= now:
        problems.append(_pr(0, "locked", "Match started."))
    rev = offer_rev(x)
    if rev != leg.get("rev") or float(leg["line"]) != float(x.get("line")):
        moved = float(leg["line"]) != float(x.get("line"))
        problems.append(_pr(1, "moved", f"Line moved from {_n(leg['line'])} to {_n(x.get('line'))}." if moved else f"This offer changed on {label}.", swap=True))
    hit = {}
    c = srv.slip_leg_check(_feed_leg(x, side), feeds, now=now, hit=hit)
    if c:
        code = c.get("code")
        if code == "locked" and not any(p["code"] == "locked" for p in problems):
            problems.append(_pr(0, "locked", "Match started."))
        elif code == "side":
            problems.append(_pr(1, "side", f"{SIDE_WORD[side]} is not offered on {label}."))
        elif code in ("retry", "regrade"):
            if not any(p["code"] == "board_failed" for p in problems):      # one outage, one sentence
                problems.append(_pr(1, "retry", "Scout could not check this line just now.", retry=True))
        elif code == "moved" and not any(p["code"] == "moved" for p in problems):
            problems.append(_pr(1, "moved", str(c.get("reason") or "Line moved") + ".", swap=True))
        elif code == "gone":
            problems.append(_pr(1, "gone", "This line is no longer on the board."))
    if x.get("unavailable") and side == _side(x) and not any(p["code"] == "side" for p in problems):
        problems.append(_pr(1, "side", f"{SIDE_WORD[side]} is no longer offered on {label}."))
    if x.get("hltv_retry"):
        problems.append(_pr(1, "grade_held", "The grade on this line is held until its data source answers.", retry=True))
    fc = forecast_for(srv, x)
    fp = forecast_problem(srv, x, leg, fc, lock)
    if fp:
        problems.append(fp)
    return {"ok": not problems, "problems": problems, "current": _current(x, fc), "_x": x, "_hit": hit, "_fc": fc}


def _n(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _same_prop(a, b):
    """Same player, stat, maps and platform on one match. A row can carry a slug only, so a match is either of its two names."""
    return ((a["nick"], a["prop"], a["scope"], a["platform"]) == (b["nick"], b["prop"], b["scope"], b["platform"])
            and (a["match_key"] == b["match_key"] or a["match_slug"] == b["match_slug"]))


def _dup_checks(legs, results, st, member):
    """Duplicates and conflicts inside the slip, then against the member's receipts (a bounded indexed read)."""
    real = []
    for l in legs:
        r = results[l["id"]]
        x = r.get("_x")
        if x is not None:
            real.append((l, r, {"nick": _nick(x), "prop": x.get("prop"), "scope": x.get("scope") or "12", "match_slug": _slug_of(x), "match_key": event_id(x)[0],
                                "platform": _book(x), "line": x.get("line"), "side": l["side"]}))
    for i, (l, r, a) in enumerate(real):
        for j, (l2, _, b) in enumerate(real):
            if i != j and _same_prop(a, b):
                same = a["line"] == b["line"] and a["side"] == b["side"]
                text = "Duplicate of another leg on this entry." if same else f"Conflicts with {SIDE_WORD[b['side']]} {_n(b['line'])} on the same prop."
                r["problems"].append(_pr(4, "duplicate" if same else "conflict", text))
    tracked = st.tracked_for(member, [a["match_key"] for _, _, a in real], [a["match_slug"] for _, _, a in real]) if real else []
    for l, r, a in real:
        for t in tracked:
            if _same_prop(a, t):
                same = t["line"] == a["line"] and t["side"] == a["side"]
                r["problems"].append(_pr(4, "duplicate_tracked" if same else "conflict_tracked",
                                         "You already tracked this pick." if same else f"You already tracked {SIDE_WORD[t['side']]} {_n(t['line'])} on this prop."))
                break
    for l, r, _ in real:
        r["ok"] = not r["problems"]


def _gate(srv):
    """gate_from_export's answer, or None when the export cannot be read: ev_status names that, never a count of zero."""
    def compute():
        ex = srv._p1_export() or {}
        tags = {x.get("graded_model") for x in (_SNAP["index"] or {}).values()}
        at = _ts(ex.get("at")) if ex.get("at") else None
        return 200, rules.gate_from_export(ex, tags, at.timestamp() if at else None)
    try:
        return srv.shared(("ws_ev_gate",), compute, ttl=900)[1]
    except Exception:
        return None


def _open_link(srv, platform, entry_legs, results):
    if not srv.PP_OPEN_LINK:
        return None
    if platform == "prizepicks":
        pl = [srv._pp_leg(_feed_leg(results[l["id"]]["_x"], l["side"]), results[l["id"]].get("_hit")) for l in entry_legs]
        if any(p is None for p in pl):
            return None
        url, _why = srv.pplink.pp_link(pl)
        return {"url": url, "label": "Open in PrizePicks"} if url else None
    links = [srv.ud_row_link(l["row_id"]) for l in entry_legs]
    return {"url": srv.pplink.UD_HOME, "label": "Open Underdog", "legs": links if any(links) else None}


def run_check(srv, member, legs, entry_types, now=None):
    """The five checks for every leg and entry. Raises LookupError when no board has been read yet."""
    now = now or datetime.now(timezone.utc)
    snap = snapshot(srv)
    if snap is None:
        raise LookupError("no board")
    st = store()
    feeds, results = {}, {}
    for l in legs:
        results[l["id"]] = ({"ok": True, "sample": True, "problems": [], "current": None} if l["sport"] != "cs2"
                            else check_leg(srv, l, snap, feeds, now))
    _dup_checks(legs, results, st, member)
    entries = {}
    by_platform = {}
    for l in legs:
        by_platform.setdefault(l["book"], []).append(l)
    gate_memo = {}

    def gate():
        if "g" not in gate_memo:
            gate_memo["g"] = _gate(srv)
        return gate_memo["g"]
    for platform, ls in by_platform.items():
        rule_legs, ev_legs = [], []
        for l in ls:
            x = results[l["id"]].get("_x")
            if x is None:
                rule_legs.append({"sport": l["sport"], "book": platform, "team": l.get("team"), "player": l.get("player"), "side": l["side"],
                                  "event": l.get("event"), "sample": l["sport"] != "cs2"})
                ev_legs.append({"name": l.get("player"), "sample": l["sport"] != "cs2", "sport": l["sport"], "line": l["line"]})
                continue
            rule_legs.append({"sport": "cs2", "book": platform, "team": x.get("team_name") or x.get("team"), "nick": _nick(x), "side": l["side"],
                              "event": event_id(x)[0], "sample": False})
            mult = x.get("higher") if l["side"] == "over" else x.get("lower")
            ev_legs.append({"name": _nick(x), "sample": False, "sport": "cs2", "match": event_id(x)[0], "line": x.get("line"), "multiplier": mult,
                            "on_side": l["side"] == _side(x), "p": x.get("p"), "tag": x.get("graded_model"), "prop": x.get("prop"), "scope": x.get("scope") or "12"})
        et = (entry_types or {}).get(platform)
        e = rules.entry_status(platform, rule_legs, et if isinstance(et, str) and len(et) <= 16 else None)
        legs_ok = all(results[l["id"]]["ok"] for l in ls)
        sample = any(results[l["id"]].get("sample") for l in ls)
        e["rule_ok"] = e["ok"]                  # the platform takes this combination; ok also needs every leg real and passing
        e["ok"] = e["rule_ok"] and legs_ok and not sample
        e["trackable"] = e["trackable"] and legs_ok
        if e["rule_ok"] and legs_ok:
            e["ev"] = rules.ev_status(e["platform"], e["entry_type"], ev_legs, gate, now.timestamp())
        else:
            e["ev"] = {"available": False, "reason": "This entry has not passed its checks."}
        if e["trackable"]:
            link = _open_link(srv, e["platform"], ls, results)
            if link:
                e["open"] = link
        entries[platform] = e
    fails = [0, 0, 0, 0, 0]
    for r in results.values():
        for p in r["problems"]:
            fails[p["check"]] += 1
    for e in entries.values():
        for p in e["problems"]:
            fails[p["check"]] += 1
    return {"checked_at": now.isoformat(timespec="seconds"), "snapshot_at": _iso(snap["at"]), "ttl_s": CHECK_TTL_S,
            "legs": results, "entries": entries, "fails": fails}


def public_check(res):
    return dict(res, legs={k: {a: b for a, b in v.items() if not a.startswith("_")} for k, v in res["legs"].items()})


# ---------------------------------------------------------------------------------------------------------------- /line

def _history(srv, x):
    """Game-by-game results from the HLTV tables, only when they reproduce the saved L10 tally. Three Postgres slots: a member
    who cannot get one within 0.5 s sees the saved tally alone."""
    n, over, push = x.get("l10_n"), x.get("l10_over"), x.get("l10_push") or 0
    if not n or over is None:
        return None
    nick, team, prop, scope = _nick(x), x.get("team_name") or x.get("team"), x.get("prop"), x.get("scope") or "12"
    stat = STAT_COL.get(prop)
    if not stat:
        return None

    def compute():
        if not _HIST_SLOTS.acquire(timeout=0.5):
            return 200, None, 1
        try:
            since = (date.today() - timedelta(days=365)).isoformat()
            if scope == "123":
                rows = srv.records._hltv_rows(nick, team, since)
                ser = [{"date": s["date"], "opponent": s.get("opponent"), "value": s["total"], "maps": s.get("maps") or [], "values": s.get("values") or []}
                       for s in srv.records._series(rows, stat)]
            else:
                by = {}
                for r in srv.hltv.recent_series(nick, team, since):
                    if r.get("number") in ((1,) if scope == "1" else (1, 2)):
                        by.setdefault(r["match_id"], []).append(r)
                need = 1 if scope == "1" else 2
                ser = [{"date": m[0]["date"], "opponent": m[0].get("opponent"), "value": sum(y[stat] for y in m), "maps": [y.get("map") for y in m],
                        "values": [y[stat] for y in m]}
                       for m in by.values() if len(m) == need and all(y.get(stat) is not None for y in m)]
            ser.sort(key=lambda s: s["date"], reverse=True)
            return 200, ser[:20]
        except Exception:
            return 200, None, 1
        finally:
            _HIST_SLOTS.release()
    line = x.get("line")
    ser = srv.shared(("ws_hist", _norm(nick), _norm(team), prop, scope), compute, ttl=HISTORY_TTL)[1]
    if not ser:
        return None
    last = ser[:n + push]
    if (len(last) != n + push or sum(s["value"] > line for s in last) != over or sum(s["value"] == line for s in last) != push):
        return None
    return ser


def _market(x):
    if _book(x) != "underdog":
        return {"available": False, "reason": "PrizePicks sends no prices."}
    po, pu = x.get("price_higher"), x.get("price_lower")
    if not po or not pu or po <= 1 or pu <= 1:
        return {"available": False, "reason": "Underdog sent no prices for both sides of this line."}
    a, b = 1 / po, 1 / pu
    return {"available": True, "p_over": round(a / (a + b), 4), "p_under": round(b / (a + b), 4), "source": "Underdog prices, margin removed"}


def _context(srv, fc):
    if not fc.get("id") or not _FC_SLOTS.acquire(timeout=1.0):
        return {}
    try:
        con = srv.picklog._read_db()
        try:
            r = con.execute("SELECT inputs FROM forecasts WHERE id = ?", (fc["id"],)).fetchone()
        finally:
            con.close()
    finally:
        _FC_SLOTS.release()
    if not r or not r["inputs"]:
        return {}
    gi = json.loads(zlib.decompress(r["inputs"]))
    out = []
    for k, label in CONTEXT_KEYS:
        v = gi.get(k) if isinstance(gi, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({"key": k, "label": label, "value": round(v, 3)})
    return {"rows": out}


def _line_body(srv, book, rid):
    x = _SNAP["index"].get((book, rid))
    if x is None:
        return 404, {"error": "not found"}
    fc = forecast_for(srv, x)
    rec = record_of(x)
    prop_key = (rec["event"]["id"], rec["player"]["key"], x.get("prop"), x.get("scope") or "12")
    books = []
    for bk, i in _SNAP["props"].get(prop_key, []):
        o = _SNAP["index"].get((bk, i))
        if o is not None:
            r = record_of(o)
            books.append({"platform": bk, "platform_label": LABEL.get(bk, bk), "row_id": i, "key": r["key"], "line": o.get("line"), "line_type": line_type_of(o),
                          "over": r["offer"]["over"], "under": r["offer"]["under"], "seen_at": r["offer"]["seen_at"], "scout_side": _side(o), "p_side": o.get("p"),
                          "grade": r["scout"]["grade"]})
    ctx = {}
    try:
        ctx = _context(srv, fc)
    except Exception:
        ctx = {}
    forecast = None
    if fc.get("state") == "ok":
        forecast = {"id": fc.get("id"), "logged_at": fc.get("logged_at"), "model_version": fc.get("model_version"), "p_saved": fc.get("p_pick")}
    return 200, {"record": rec, "forecast": forecast, "history": {"tally": rec["history"]["l10"], "avg": rec["history"]["avg"], "series": _history(srv, x)},
                 "books": books, "context": ctx, "market": _market(x)}


# ---------------------------------------------------------------------------------------------------------------- responses

def _entry(body, rev):
    """(bytes, gzipped, etag, revision). A compressed body keeps only its compressed copy, which is what 700 members can afford."""
    etag = '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
    return (gzip.compress(body, 6, mtime=0), True, etag, rev) if len(body) >= 1024 else (body, False, etag, rev)


def _send_private(h, srv, ent):
    data, gz, etag, _ = ent
    use = gz and srv.accepts_gzip(h.headers.get("Accept-Encoding"))
    hdr = {"Cache-Control": srv.MEMBER_CACHE, "Vary": "Accept-Encoding", "ETag": etag[:-1] + '-gz"' if use else etag}
    if srv.etag_matches(h.headers.get("If-None-Match"), etag):
        return h.send(304, b"", "application/json", hdr)
    if use:
        hdr["Content-Encoding"] = "gzip"
    return h.send(200, gzip.decompress(data) if gz and not use else data, "application/json", hdr)


def _read_json(h, srv, n):
    raw = h.rfile.read(n)
    try:
        d = json.loads(raw, parse_constant=srv._no_json_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return d if isinstance(d, dict) else None


def _shallow(v):
    """No deeper than STATE_MAX_DEPTH. Saved state is read and written back whole, so it must never be too deep to encode."""
    stack = [(v, 1)]
    while stack:
        x, depth = stack.pop()
        if isinstance(x, (dict, list)):
            if depth > STATE_MAX_DEPTH:
                return False
            stack.extend((y, depth + 1) for y in (x.values() if isinstance(x, dict) else x))
    return True


def _limited(h, srv, bucket, member, limit):
    """Every call counts. A repeated idem key is not free here: only a press that already has receipts answers without work, and
    that answer sits behind this limit too."""
    if srv.studio_rate_ok(member, None, bucket=_HITS[bucket], limit=limit):
        return False
    h.send(429, {"error": f"Too many requests. The limit is {limit} a minute."})
    return True


def get_me(h, srv, q, member, scratch, name):
    return h.send(200, {"member": member, "name": name, "scratch": scratch, "sample_sports": list(SAMPLE_SPORTS), "rules_version": rules.RULES_VERSION,
                        "owner": bool(scratch) or bool(h._sports_owner())})


def get_board(h, srv, q, member, scratch, name):
    snap = snapshot(srv)
    if snap is None:
        return h.send(503, {"error": "board_unavailable"})
    return h.send_shared(("ws_board",), 200, snap["body"], keep=60)


def get_line(h, srv, q, member, scratch, name):
    book, rid = q.get("book"), q.get("id")
    if book not in BOOKS or not rid or not _ID_RE.match(rid) or snapshot(srv) is None or (book, rid) not in _SNAP["index"]:
        return h.send(404, {"error": "not found"})

    def compute():
        st, doc = _line_body(srv, book, rid)
        return st, json.dumps(doc, default=str, separators=(",", ":")).encode()
    st, body = srv.shared(("ws_line", book, rid), compute, ttl=LINE_TTL)
    return h.send(st, body)


def get_mine(h, srv, q, member, scratch, name):
    before = q.get("before") or None
    if before and not _BEFORE_RE.match(before):
        return h.send(400, {"error": "bad before"})
    limit = srv.qint(q, "limit", MINE_PAGE, 1, wstore.PAGE_MAX)
    st = store()
    rev = st.rev(member)
    newest = before is None and limit == MINE_PAGE      # the one page kept: a member can never fill the cache with other pages
    ent = _PRIVATE.get((member, "mine")) if newest else None
    if ent is not None and ent[3] == rev:
        return _send_private(h, srv, ent)
    if _limited(h, srv, "mine", member, MINE_PER_MIN):  # only a read that reaches the database counts
        return None
    doc = st.mine(member, before, limit)
    doc.update(as_of=wstore.iso(), rev=rev)
    ent = _entry(json.dumps(doc, default=str, separators=(",", ":")).encode(), rev)
    if newest:
        _PRIVATE.put((member, "mine"), ent)
    return _send_private(h, srv, ent)


def get_results(h, srv, q, member, scratch, name):
    st_, body_ = srv.shared(("results",), lambda: (200, json.dumps(srv.results_payload(), default=str).encode()), ttl=300)
    return h.send_shared(("results",), st_, body_, keep=300)


def get_state(h, srv, q, member, scratch, name):
    st = store()
    rev = st.state_rev(member)
    ent = _PRIVATE.get((member, "state"))
    if ent is None or ent[3] != rev:
        ent = _entry(json.dumps(st.get_state(member), separators=(",", ":")).encode(), rev)
        _PRIVATE.put((member, "state"), ent)
    return _send_private(h, srv, ent)


def get_rules(h, srv, q, member, scratch, name):
    return h.send(200, rules.rules_payload())


def get_diagnostics(h, srv, q, member, scratch, name):
    if not (_token_in_use(h, q) or h._sports_owner()):
        return h.send(404, {"error": "not found"})
    return h.send(200, store().diagnostics())


def post_recheck(h, srv, q, member, scratch, name, n):
    d = _read_json(h, srv, n)
    legs = _legs_of(d) if d else None
    idem = (d or {}).get("idem")
    if legs is None or not isinstance(idem, str) or not srv._IDEM_RE.match(idem):
        return h.send(400, {"error": "bad check"})
    if _limited(h, srv, "recheck", member, RECHECK_PER_MIN):
        return None
    try:
        res = run_check(srv, member, legs, d.get("entry_types") if isinstance(d.get("entry_types"), dict) else {})
    except LookupError:
        return h.send(503, {"error": "check_unavailable"})
    return h.send(200, public_check(res))


def _payload(x, leg, res, e):
    """What the member saw, frozen. grade, units and stake_reason are Scout's for scout_side, whatever side the member took. The
    forecast_* fields are the saved forecast itself, beside the board's copy: the same-match cap and display gd-1 can move the
    board's after logging, and a receipt keeps both."""
    rec = record_of(x)
    fc = res["_fc"]
    return {"player": rec["player"]["name"], "team": rec["player"]["team"], "opponent": rec["player"]["opponent"], "matchup": rec["event"]["label"],
            "competition": rec["event"]["competition"], "stat_label": rec["market"]["stat_label"], "period_label": rec["market"]["period_label"],
            "platform_label": rec["offer"]["platform_label"], "scout_side": rec["scout"]["side"], "on_side": leg["side"] == rec["scout"]["side"],
            "displayed_p": leg.get("shown_p") if leg["side"] == rec["scout"]["side"] else None, "grade": rec["scout"]["grade"], "units": rec["scout"]["units"],
            "stake_reason": rec["scout"]["stake_reason"], "projection": rec["scout"]["projection"], "forecast_p": fc.get("p_pick"), "forecast_at": fc.get("logged_at"),
            "forecast_grade": fc.get("grade"), "forecast_units": fc.get("units"), "l10": rec["history"]["l10"], "feed_at": rec["offer"]["seen_at"],
            "rules_version": e.get("rules_version"), "entry_type": e.get("entry_type")}


def post_track(h, srv, q, member, scratch, name, n):
    d = _read_json(h, srv, n)
    legs = _legs_of(d) if d else None
    idem, key = (d or {}).get("idem"), (d or {}).get("entry_key")
    if legs is None or not isinstance(idem, str) or not srv._IDEM_RE.match(idem) or key not in BOOKS or any(l["book"] != key for l in legs):
        return h.send(400, {"error": "bad track"})
    if _limited(h, srv, "track", member, TRACK_PER_MIN):
        return None
    st = store()
    legs_id = json.dumps([[l["row_id"], l["side"], l["line"]] for l in legs])
    slip_id = "sl-" + hashlib.sha256(f"{member}|{idem}|{legs_id}".encode()).hexdigest()[:20]     # an idem key names one press of these legs
    prev = st.slip_receipts(member, slip_id)
    if prev:                                    # the same press again: the same receipts
        return h.send(200, {"ok": True, "slip_id": slip_id, "receipts": prev})
    et = d.get("entry_type")
    try:
        res = run_check(srv, member, legs, {key: et if isinstance(et, str) else None})
    except LookupError:
        return h.send(503, {"error": "check_unavailable"})
    e = res["entries"].get(key) or {}
    if not e.get("trackable"):
        return h.send(409, dict(public_check(res), ok=False, error="refused"))
    now = wstore.iso()
    receipts = []
    for i, l in enumerate(legs):
        r = res["legs"][l["id"]]
        x, fc = r["_x"], r["_fc"]
        rev = offer_rev(x)
        receipts.append({"receipt_id": "rc-" + hashlib.sha256(f"{member}|{key}|{l['row_id']}|{x.get('line')}|{l['side']}|{rev}".encode()).hexdigest()[:20],
                         "member": member, "slip_id": slip_id, "leg_no": i, "tracked_at": now, "sport": "cs2", "platform": key, "row_id": l["row_id"], "offer_rev": rev,
                         "side": l["side"], "line": x.get("line"), "line_type": line_type_of(x), "pick_id": fc["pick_id"], "forecast_id": fc["id"],
                         "match_slug": _slug_of(x), "match_id": x.get("match_id"), "match_key": event_id(x)[0], "nick": _nick(x), "prop": x.get("prop"),
                         "scope": x.get("scope") or "12", "start_utc": x.get("start"), "model_version": x.get("graded_model"), "scratch": scratch,
                         "payload": _payload(x, l, r, e)})
    slip = {"slip_id": slip_id, "member": member, "tracked_at": now, "platform": key, "entry_type": e.get("entry_type"), "sports": e.get("sports"),
            "rules_version": e.get("rules_version"), "checks": {"checked_at": res["checked_at"], "fails": res["fails"]}, "scratch": scratch}
    out = st.track(slip, receipts)
    if not out["ok"]:
        for l, x in zip(legs, receipts):
            for dup in out["duplicate"]:
                if dup["receipt_id"] == x["receipt_id"]:
                    res["legs"][l["id"]]["problems"].append(_pr(4, "duplicate_tracked" if dup["same"] else "conflict_tracked", "You already tracked this pick."
                                                                if dup["same"] else "You already tracked this prop."))
                    res["legs"][l["id"]]["ok"] = False
        return h.send(409, dict(public_check(res), ok=False, error="refused"))
    return h.send(200, {"ok": True, "slip_id": slip_id, "receipts": out["receipts"]})


def post_state(h, srv, q, member, scratch, name, n):
    if n > STATE_MAX:
        h.rfile.read(n)
        return h.send(413, {"error": f"state over {STATE_MAX} bytes"})
    d = _read_json(h, srv, n)
    if d is None or isinstance(d.get("rev"), bool) or not isinstance(d.get("rev"), int):
        return h.send(400, {"error": "bad state"})
    patch = {}
    for k, kind in (("slip", dict), ("prefs", dict), ("views", list)):
        if k in d:
            if d[k] is not None and (not isinstance(d[k], kind) or not _shallow(d[k])):
                return h.send(400, {"error": f"bad {k}"})
            patch[k] = d[k]
    if not patch:
        return h.send(400, {"error": "nothing to save"})
    if _limited(h, srv, "state", member, STATE_PER_MIN):
        return None
    out = store().put_state(member, d["rev"], patch)
    if not out["ok"]:
        return h.send(409, dict(out["state"], error="stale"))
    return h.send(200, {"rev": out["rev"]})


GET = {"me": get_me, "board": get_board, "line": get_line, "mine": get_mine, "results": get_results, "state": get_state, "rules": get_rules,
       "diagnostics": get_diagnostics}
POST = {"recheck": post_recheck, "track": post_track, "state": post_state}


def handle(h, srv, method, path, q, n=0):
    """Every /preview/workspace/api/* route. The caller has already passed Handler._workspace_on."""
    name = path[len(srv.WORKSPACE_API):]
    fn = (GET if method == "GET" else POST).get(name)
    if fn is None:
        if method == "GET":
            return h.send(404, {"error": "not found"})
        h.rfile.read(n)
        return h.send(403, {"error": "forbidden"})
    member, scratch, who = identity(h, q)
    if not member:
        if method == "POST":
            h.rfile.read(n)
        return h.send(401, {"error": "members_only"})
    try:
        if method == "GET":
            return fn(h, srv, q, member, scratch, who)
        return fn(h, srv, q, member, scratch, who, n)
    except wstore.Busy:
        return h.send(503, {"error": "busy"}, headers={"Retry-After": "2"})
