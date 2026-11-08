"""Research workspace API, /preview/workspace/api/*: all four sports from saved grades through tracking and settlement.

Reads saved grades and offers; never grades, logs a forecast or touches picks.db (workspace_store.py owns workspace.db). The
server runs as `python server.py`, so this module never imports server (that would load a second copy): the handler passes its
own module in as `srv`. Imported on the first allowed request; with the preview off, no thread or database file exists.

CS2 lives here. LoL is workspace_lol.py and NBA and NFL are workspace_sports.py: neither imports its sport, and each sport keeps its
own board snapshot, so one sport going down never touches another. A member sees LoL and NBA/NFL exactly when the site's own gates
let them see those pages (access).

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
import sys
import threading
import time
import zlib
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import board_parity
import letter_containment as lc
import slip_quality as sq
import workspace_h2h
import workspace_rules as rules
import workspace_store as wstore

SPORTS = ("cs2", "lol", "nba", "nfl")
LEAGUES = ("nba", "nfl")
BOOKS = ("underdog", "prizepicks")
SCOPE = {"1": ("map1", "Map 1"), "12": ("maps12", "Maps 1-2"), "123": ("maps13", "Maps 1-3")}
STAT = {"kills": "Kills", "hs": "Headshots"}
STAT_COL = {"kills": "kills", "hs": "headshots"}
SIDE_WORD = {"over": "Over", "under": "Under"}
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
OWNER_ID = "wm-owner-admin-token"
TOKEN_PAGES = ("/preview/workspace", "/", "/index.html", "/lol", "/sports", "/sports/", "/sports/results")     # the pages that call this API
CHECK_TTL_S = 300
MAX_LEGS = 8
STATE_MAX = 16 * 1024
LEG_PAGE = 120                          # workspace_sports.SETTLE_PAGE: what one settler pass can read from the sports API
WATCH_MAX = 100
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
_HITS = {"recheck": {}, "track": {}, "state": {}, "mine": {}, "genie": {}, "generate": {}, "advice": {}, "report": {}}
_ST = {"store": None}
_ST_LOCK = threading.Lock()
_SNAP = {"at": 0.0, "body": None, "index": {}, "props": {}, "last": {}, "doc": None, "gen": 0, "feeds": {}}
HITS_TTL, HITS_FILL_TTL, HITS_BUDGET_S = 60, 20, 1.0
_HITS_DOC = {"body": None, "at": 0.0, "ttl": HITS_TTL, "lock": threading.Lock()}


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


def _sources():
    """One reader per sport for the settler: each answers from that sport's own settled results. A pass with no request seen yet
    has no server module to read through, so it reads nothing."""
    def lol(rows):
        import workspace_lol
        return workspace_lol.settle(_ST["srv"], rows) if _ST.get("srv") else {}

    def league(lg):
        def read(rows):
            import workspace_sports
            return workspace_sports.settle(_ST["srv"], lg, rows) if _ST.get("srv") and _ST.get("sports") else {}
        read.page = LEG_PAGE
        return read
    return {"lol": lol, "nba": league("nba"), "nfl": league("nfl")}


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
                s.sources = _sources()
                s.start_settler()
    return s


def reset():
    """Tests: forget the store and every cache."""
    with _ST_LOCK:
        if _ST["store"] is not None:
            _ST["store"].close()
        _ST["store"] = None
        _ST["srv"] = None
        _ST["sports"] = False
    _PRIVATE.clear()
    _SNAP.update(at=0.0, body=None, index={}, props={}, last={}, doc=None, gen=0, feeds={})
    _HITS_DOC.update(body=None, at=0.0, ttl=HITS_TTL, key=None, cursor=0, rows={}, sigs={}, open=None, built_for=None)
    _hits_reset()
    _SLIP_POOL.clear()
    _BOOK_LINES_GOOD.clear()
    _LINE_TYPE_CACHE.clear()
    for d in _HITS.values():
        d.clear()
    for name in ("workspace_lol", "workspace_sports", "workspace_genie", "workspace_books"):
        mod = sys.modules.get(name)
        if mod is not None:
            mod.reset()


# ---------------------------------------------------------------------------------------------------------------- identity

def member_key(sid):
    return "wm-" + hashlib.sha256(("workspace|" + str(sid)).encode("utf-8")).hexdigest()[:24]


def access(h, srv, q):
    """Which sports this member sees: the same gates as the /lol page and the sports token. CS2 is every member of this page."""
    owner = bool(_token_in_use(h, q) or h._sports_owner())
    import workspace_sports
    league = workspace_sports.on(h, q, owner)
    if league:
        _ST["sports"] = True                    # the settler reads the sports API only once a member has been let in to it
    return {"cs2": True, "lol": bool(srv.lol_public() or owner), "nba": league, "nfl": league}


def _token_in_use(h, q):
    token = q.get("token")
    ref = urlparse(h.headers.get("Referer") or "")
    if not token and ref.path in TOKEN_PAGES:
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
    fields = [_side(x), x.get("p"), _shown_grade(x)]
    return "r-" + hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()[:16]


def _shown_grade(x):
    """The letter the member sees on this row: none while CS2 letters are held (letter_containment lc-1, display only) - the
    read's identity then carries no letter either, so the hash never encodes a held letter."""
    if x.get("side") and x.get("state") != "SCOUT" and lc.contained("cs2", x.get("prop")):
        return "held"
    return x.get("model_grade") or x.get("grade")


def _nick(x):
    return x.get("nick") or x.get("player") or ""


def _side(x):
    return {"More": "over", "Less": "under"}.get(x.get("side"))


def _teams(x):
    a, b = x.get("team_name") or x.get("team"), x.get("opp_name") or x.get("opponent")
    return sorted([str(a or ""), str(b or "")], key=str.lower)


# N11 (site audit 2026-11-06): the board search finds a team by its other exact spellings: the book's own name for the row's team and
# opponent (the board shows BO3.gg / HLTV's name when it has one), and the maintained aliases (team_aliases.json and
# comparison.VERIFIED_TEAM_ALIASES, each pair with its proof). Whole names only, compared after the matchid normalisation (case, accents,
# punctuation): never a substring or a guess, so an academy's name never joins its main team. Search text only: no identity, link,
# grade or settlement reads it. The alias file is re-read when it changes (checked at most once a minute).
_TEAM_AKA = {"sig": None, "map": {}, "checked": 0.0}


def _akey(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _team_aka_map():
    now = time.time()
    if _TEAM_AKA["sig"] is not None and now - _TEAM_AKA["checked"] < 60:
        return _TEAM_AKA["map"]
    _TEAM_AKA["checked"] = now
    path = Path(__file__).resolve().parent / "team_aliases.json"
    try:
        sig = path.stat().st_mtime_ns
    except OSError:
        sig = 0
    if sig == _TEAM_AKA["sig"]:
        return _TEAM_AKA["map"]
    pairs = []
    try:
        pairs += [(e["name"], e["hltv_name"]) for e in json.loads(path.read_text(encoding="utf-8")).get("aliases", [])
                  if isinstance(e, dict) and e.get("name") and e.get("hltv_name")]
    except Exception:
        pass
    try:
        import comparison
        pairs += [(a, b) for a, b, _proof in comparison.VERIFIED_TEAM_ALIASES]
    except Exception:
        pass
    groups = {}
    for a, b in pairs:
        ka, kb = _akey(a), _akey(b)
        if not ka or not kb or ka == kb:
            continue
        g = groups.get(ka, set()) | groups.get(kb, set()) | {str(a), str(b)}
        for n in g:
            groups[_akey(n)] = g
    _TEAM_AKA.update(sig=sig, map={k: tuple(sorted(v)) for k, v in groups.items()})
    return _TEAM_AKA["map"]


def _team_aka(x, shown):
    """The other exact spellings of the row's two teams (the book's own names, then maintained aliases), for the board search."""
    m, seen, out = _team_aka_map(), {_akey(s) for s in shown if s}, []
    for name in (x.get("team"), x.get("opponent"), *shown):
        if not name:
            continue
        for n in (name, *m.get(_akey(name), ())):
            k = _akey(n)
            if k and k not in seen:
                seen.add(k)
                out.append(str(n))
    return out[:8]


def _role(x):
    # the board row's role tag (role_display.py): explicit Liquipedia roles only, the same text as the board and the card.
    # Never the AWP chip (a kill-share / HS% reading) - that stays a slip / stake input, not a role.
    return x.get("role_label") or ""


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
    grade = _shown_grade(x)
    held = grade == "held"                      # lc-1: no letter, 0U; the chance and the side stay
    p = x.get("p")
    n, over, push = x.get("l10_n"), x.get("l10_over"), x.get("l10_push") or 0
    l10 = {"over": over, "under": n - over, "push": push, "n": n + push} if n and over is not None else None
    key = ".".join(str(k) for k in ("cs2", eid, _norm(nick), prop, period, x.get("line"))) + ("" if lt == "standard" else "." + lt)
    moved = x.get("open_line") is not None and x.get("open_line") != x.get("line")
    return {"sport": "cs2", "key": key,
            "event": {"id": eid, "id_kind": kind, "label": " vs ".join(t for t in teams if t), "teams": teams, "competition": x.get("event") or "",
                      "best_of": x.get("bo"), "start_utc": x.get("start"), "lock_utc": x.get("lock_at"), "status": "scheduled"},
            "player": {"id": x.get("player_id"), "key": _norm(nick), "name": nick, "team": x.get("team_name") or x.get("team") or "",
                       "opponent": x.get("opp_name") or x.get("opponent") or "", "role": _role(x),
                       **({"aka": aka} if (aka := _team_aka(x, (x.get("team_name") or x.get("team"), x.get("opp_name") or x.get("opponent")))) else {})},
            "market": {"stat": prop, "stat_label": STAT.get(prop, prop), "period": period, "period_label": period_label, "line_type": lt},
            "offer": {"platform": book, "platform_label": LABEL.get(book, book), "offer_id": str(x.get("id")), "revision": offer_rev(x), "revision_kind": "derived",
                      "line": x.get("line"), "seen_at": _iso(x.get("seen_at") or x.get("updated_at")),
                      "over": {"offered": _offered(x, "over"), "payout": x.get("higher" if book == "underdog" else "mod_higher"), "price": x.get("price_higher")},
                      "under": {"offered": _offered(x, "under"), "payout": x.get("lower" if book == "underdog" else "mod_lower"), "price": x.get("price_lower")},
                      "moved_from": x.get("open_line") if moved else None, "moved_at": _iso(x.get("moved_at")) if moved else None},
            "scout": {"side": side, "read_rev": read_rev(x), "p_side": p, "p_over": p if side == "over" else None, "p_under": p if side == "under" else None, "p_push": _push(x),
                      **({"p_raw": x["p_raw"]} if x.get("cal_v") and x.get("p_raw") is not None else {}),
                      "projection": x.get("proj"), "range80": None, "grade": grade if grade in ("A+", "A", "A-", "B+", "B", "B-") else None,
                      "units": 0.0 if held else x.get("units"),
                      "stake_reason": None if held else x.get("stake_reason") or x.get("cap_reason"), "hold": None, "model_version": x.get("graded_model"), "forecast_at": x.get("graded_at"),
                      "validation": {"kind": "live"}, **({"letter_held": True} if held else {})},
            "history": {"l5": None, "l10": l10, "l20": None, "avg": x.get("l10_avg"), "recent": None}, "market_chance": None,
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
    cells = _parity_cells(srv, {x.get("graded_model") for p in docs.values() for x in (p or {}).get("rows") or []}) if board_parity.rows_on() else {}
    for bk, p in docs.items():
        for x in (p or {}).get("rows") or []:
            rec = record_of(x)                           # 4.16 parity numbers, only while a SCOUT_PARITY_* row flag is on;
            board_parity.attach(rec, rated=_parity_rated(x, rec, cells))   # EV / band / size only where the slip would rate it
            records.append(rec)
            index[(_book(x), str(x.get("id")))] = x
            props.setdefault((rec["event"]["id"], rec["player"]["key"], x.get("prop"), x.get("scope") or "12"), []).append((_book(x), str(x.get("id"))))
    feeds = {bk: _feed_state(srv, bk, docs.get(bk), bk in failed) for bk in BOOKS}
    times = sorted(f["at"] for f in feeds.values() if f["at"])
    version = next((p.get("version") for p in docs.values() if p), None)
    body = json.dumps({"version": version, "board_at": times[0] if times else None, "feeds": feeds, "records": records,
                       "letter_hold": lc.hold_info("cs2")}, default=str, separators=(",", ":")).encode()
    if _SNAP["body"] == body:
        body = _SNAP["body"]                    # identical bytes: the same object, so nothing is compressed again
    _SNAP.update(at=time.time(), body=body, index=index, props=props, feeds=feeds, gen=_SNAP["gen"] + 1)
    if _HITS_FILL["srv"] is not None:              # site audit N3: once /hits is read here, each new board is filled in the background
        _hits_kick(srv, _SNAP)
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
    if x.get("cal_v"):                          # ec-1: the board prints the calibrated grade / stake; the log keeps the model's own
        return abs((x.get("units_raw") or 0) - (fc.get("units") or 0)) <= 1e-9 and fc.get("grade") == x.get("grade_raw")
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
          and x.get("p") is not None and fc.get("p_pick") is not None and abs(fc["p_pick"] - x.get("p_raw", x["p"])) < FORECAST_MAX_GAP
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
    return {"ok": not problems, "problems": problems, "current": _current(x, fc), "_x": x, "_hit": hit, "_fc": fc,
            "_ident": {"nick": _nick(x), "prop": x.get("prop"), "scope": x.get("scope") or "12", "match_slug": _slug_of(x), "match_key": event_id(x)[0], "platform": _book(x), "line": x.get("line"), "side": side},
            "_rule": {"sport": "cs2", "book": book, "team": x.get("team_name") or x.get("team"), "nick": _nick(x), "side": side, "event": event_id(x)[0]},
            "_ev": {"name": _nick(x), "sport": "cs2", "match": event_id(x)[0], "line": x.get("line"), "multiplier": x.get("higher") if side == "over" else x.get("lower"), "on_side": side == _side(x),
                    "p": x.get("p"), "tag": x.get("graded_model"), "prop": x.get("prop"), "scope": x.get("scope") or "12", "line_type": line_type_of(x)}}


def _n(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _same_prop(a, b):
    """Same player, stat, maps and platform on one match. A row can carry a slug only, so a match is either of its two names."""
    return ((a["nick"], a["prop"], a["scope"], a["platform"]) == (b["nick"], b["prop"], b["scope"], b["platform"])
            and (a["match_key"] == b["match_key"] or a["match_slug"] == b["match_slug"]))


def _dup_checks(legs, results, st, member):
    """Duplicates and conflicts inside the slip, then against the member's receipts (a bounded indexed read)."""
    real = [(l, results[l["id"]], results[l["id"]]["_ident"]) for l in legs if results[l["id"]].get("_ident")]
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


def _gate(srv, tags=None):
    """gate_from_export's answer, or None when the export cannot be read: ev_status names that, never a count of zero.
    tags: the model versions to read (default: the current board snapshot's). The board build passes the rows it is building,
    so the first build after a restart is rated too; the steady-state tag set is the same key, so both share one read."""
    if tags is None:
        tags = {x.get("graded_model") for x in (_SNAP["index"] or {}).values()}
    tags = frozenset(t for t in tags if t)

    def compute():
        ex = getattr(srv, "_p1_export_printed", srv._p1_export)() or {}      # ec-1: bands at the printed chance
        at = _ts(ex.get("at")) if ex.get("at") else None
        return 200, rules.gate_from_export(ex, tags, at.timestamp() if at else None)
    try:
        return srv.shared(("ws_ev_gate", tuple(sorted(tags))), compute, ttl=900)[1]
    except Exception:
        return None


def _slip_header(ev, entry_type, ev_legs):
    """SCOUT_SLIP_HEADER (default off): gradecore/slip.py's payout / P(all) / expected return / quarter-Kelly for an entry whose
    EV is already available. Any failure leaves the header out; the entry, its checks and its EV are unchanged either way."""
    try:
        from gradecore import slip as gslip         # imported only with the flag on: a flag-off process never loads it
        return rules.slip_header(ev, entry_type, ev_legs, gslip.evaluate)
    except Exception:
        return None


SLIP_HEADER_V2_FLAG = "SCOUT_SLIP_HEADER_V2"
UD_FLEX4_REASON = "Underdog's pages list two 4-pick flex payouts."
SPECIAL_LINE_REASON = "Special-line payouts are set on the PrizePicks lineup screen."
UD_MULTIPLIER_REASON = "Underdog does not publish how multipliers combine."


def slip_header_v2_on(env):
    """SCOUT_SLIP_HEADER_V2 (default off): the spec 4.3 header in the slip rail."""
    return str((env or {}).get(SLIP_HEADER_V2_FLAG) or "").strip().lower() in ("1", "true", "yes", "on")


def _v2_leg(platform, r):
    """One checked leg -> gradecore.slip_header's leg: the chance the board prints for this side (CS2 prints one for Scout's
    side only), its match, and why the book prices this pick elsewhere (goblin / demon, an Underdog payout multiplier)."""
    x = r.get("_ev") or {}
    sport = x.get("sport") or "cs2"
    p = x.get("p") if (x.get("on_side") or sport != "cs2") else None
    unpriced = None
    if str(x.get("line_type") or "standard").lower() not in ("standard", "normal"):
        unpriced = SPECIAL_LINE_REASON
    elif platform == "underdog" and x.get("multiplier") is not None and x.get("multiplier") != 1.0:
        unpriced = UD_MULTIPLIER_REASON
    return {"p": p, "match_key": None if x.get("match") is None else f"{sport}:{x['match']}", "unpriced": unpriced}


V2_KEEP_UNRATED = ("entry", "n", "payout", "tiers", "break_even", "break_even_american")
V2_DROP = ("kelly_units", "kelly_fraction")       # no Kelly on the member slip until the owner approves it (one stake per line)


def _v2_unrated(t, reason):
    """An entry type the live EV check refuses: its payout and break-even only, the check's own reason, no chance or EV."""
    return dict({k: t[k] for k in V2_KEEP_UNRATED if k in t}, rated=False, reason=reason)


def _v2_build(platform, legs):
    from gradecore import slip_header as gsh    # imported only with the flag on: a flag-off process never loads it
    head = gsh.header(legs, platform)
    if head:
        for t in head["types"].values():
            for k in V2_DROP:
                t.pop(k, None)
    return head


def _slip_header_v2(platform, ls, results, ev_for=None, srv=None):
    """SCOUT_SLIP_HEADER_V2: payout, chance all hit, average return and per-leg break-even for every entry type of this platform
    (so the Power / Flex toggle repaints without a request), from server-checked legs only: 2+ legs, every leg passing its
    checks. An entry type is RATED only where the live EV check (rules.ev_status via ev_for(entry type), the same held-letter,
    sport, whole-line, multiplier, same-match and settled-results steps) gives an EV; otherwise it shows that check's reason.
    One build per leg set for CHECK_TTL_S (srv.shared), not one per member. Any failure leaves the header out; the entry, its
    checks and its EV are unchanged either way."""
    try:
        rs = [results[l["id"]] for l in ls]
        if len(rs) < 2 or not all(r.get("ok") and r.get("_ev") for r in rs):
            return None
        legs = [_v2_leg(platform, r) for r in rs]
        if srv is not None and hasattr(srv, "shared"):
            key = ("ws_slip_h2", platform, json.dumps(legs, sort_keys=True, default=str))
            head = srv.shared(key, lambda: (200, _v2_build(platform, legs)), ttl=CHECK_TTL_S)[1]
        else:
            head = _v2_build(platform, legs)
        if not head:
            return None
        head = json.loads(json.dumps(head))           # the shared copy is never changed
        from gradecore import payouts as _pay
        for name in [k for k in head["types"] if not _pay.in_app(platform, k, len(rs))]:
            head["types"].pop(name)                   # PrizePicks Flex 2-pick: not an entry the app offers
        if platform == "underdog" and len(rs) == 4 and "flex" in head["types"]:
            head["types"]["flex"] = {"entry": "flex", "n": 4, "rated": False, "reason": UD_FLEX4_REASON}
        if ev_for is not None:
            for name, t in list(head["types"].items()):
                ev = ev_for(name) or {}
                if not ev.get("available"):
                    head["types"][name] = _v2_unrated(t, ev.get("reason") or "This entry can't be rated.")
        return head
    except Exception:
        return None


def _adapter(sport):
    if sport == "lol":
        import workspace_lol
        return workspace_lol
    import workspace_sports
    return workspace_sports


def _open_link(srv, platform, entry_legs, results):
    if not srv.PP_OPEN_LINK:
        return None
    if platform == "prizepicks":
        # All IDs come from freshly checked server rows. No projection ID from
        # the browser is trusted, and every sport uses Classic's exact builder.
        pl = [srv._pp_leg(_feed_leg(results[l["id"]]["_x"], l["side"]), results[l["id"]].get("_hit"))
              if l['sport']=='cs2' else _adapter(l['sport']).entry_projection(results[l['id']]['_x'],l['side'])
              for l in entry_legs]
        if any(p is None for p in pl):
            return None
        url, _why = srv.pplink.pp_link(pl)
        return {"url": url, "label": "Open in PrizePicks"} if url else None
    links = [srv.ud_row_link(l["row_id"]) if l["sport"] == "cs2" else _adapter(l["sport"]).entry_link(results[l["id"]]["_x"], l["side"]) for l in entry_legs]
    return {"url": srv.pplink.UD_HOME, "label": "Open Underdog", "legs": links if any(links) else None}


def _check_one(srv, l, snap, feeds, now, allowed):
    """One leg by its sport. A sport this member cannot see answers like a line that is not on the board."""
    if allowed is not None and not allowed.get(l["sport"]):
        return {"ok": False, "problems": [_pr(1, "gone", "This line is no longer on the board.")], "current": None}
    if l["sport"] == "cs2":
        if snap is None:
            return {"ok": False, "problems": [_pr(1, "retry", "Scout could not check this line just now.", retry=True)], "current": None}
        return check_leg(srv, l, snap, feeds, now)
    if l["sport"] == "lol":
        return _adapter("lol").check_leg(srv, l, now)
    return _adapter(l["sport"]).check_leg(srv, l, l["sport"], now)


def run_check(srv, member, legs, entry_types, now=None, allowed=None):
    """The five checks for every leg and entry, each leg by its own sport. Raises LookupError when a CS2 leg has no board at all."""
    now = now or datetime.now(timezone.utc)
    snap = snapshot(srv) if any(l["sport"] == "cs2" for l in legs) else None
    if snap is None and all(l["sport"] == "cs2" for l in legs):
        raise LookupError("no board")
    st = store()
    feeds, results = {}, {}
    for l in legs:
        results[l["id"]] = _check_one(srv, l, snap, feeds, now, allowed)
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
            r = results[l["id"]]
            if r.get("_rule") is None:          # gone from the board: the rules still read what the slip says it held
                rule_legs.append({"sport": l["sport"], "book": platform, "team": l.get("team"), "player": l.get("player"), "side": l["side"], "event": l.get("event")})
                ev_legs.append({"name": l.get("player"), "sport": l["sport"], "line": l["line"]})
                continue
            rule_legs.append(r["_rule"])
            ev_legs.append(r["_ev"])
        et = (entry_types or {}).get(platform)
        e = rules.entry_status(platform, rule_legs, et if isinstance(et, str) and len(et) <= 16 else None)
        legs_ok = all(results[l["id"]]["ok"] for l in ls)
        e["rule_ok"] = e["ok"]                  # the platform takes this combination; ok also needs every leg passing
        e["ok"] = e["rule_ok"] and legs_ok
        e["trackable"] = e["trackable"] and legs_ok
        if e["rule_ok"] and legs_ok:
            ev_legs = [_held_leg(l) for l in ev_legs]
            e["ev"] = rules.ev_status(e["platform"], e["entry_type"], ev_legs, gate, now.timestamp())
            if e["ev"].get("available") and rules.slip_header_on(os.environ):
                head = _slip_header(e["ev"], e["entry_type"], ev_legs)
                if head:
                    e["ev"]["header"] = head
        else:
            e["ev"] = {"available": False, "reason": "This entry has not passed its checks."}
        if slip_header_v2_on(os.environ) and e["rule_ok"] and legs_ok:
            memo = {e["entry_type"]: e["ev"]}         # ev_legs here already carry the lc-1 hold (_held_leg above)

            def ev_for(name, _p=e["platform"], _l=ev_legs, _m=memo):
                if name not in _m:
                    _m[name] = rules.ev_status(_p, name, _l, gate, now.timestamp())
                return _m[name]
            head2 = _slip_header_v2(platform, ls, results, ev_for, srv)
            if head2:
                e["slip_header"] = head2
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
    return {"checked_at": now.isoformat(timespec="seconds"), "snapshot_at": _iso(snap["at"]) if snap else None, "ttl_s": CHECK_TTL_S,
            "legs": results, "entries": entries, "fails": fails}


def _held_leg(l):
    """lc-1: a CS2 / LoL leg whose letter is held carries the banner as "held" - workspace_rules gives it no rating (no EV, no
    slip header), the same step as a sport with no validated record."""
    sport = l.get("sport") or "cs2"
    if sport in lc.SPORTS and lc.contained(sport, l.get("prop") or l.get("stat")):
        return dict(l, held=lc.banner(sport))
    return l


def public_check(res):
    return dict(res, legs={k: {a: b for a, b in v.items() if not a.startswith("_")} for k, v in res["legs"].items()})


# ---------------------------------------------------------------------------------------------------------------- /line

def _map_stats(maps):
    """Player stats over his HLTV maps in the last year (every map, not only Maps 1-2 pairs): display only, read from the rows the
    history already fetched, so no extra query."""
    rows = sorted((r for r in maps if r.get("kills") is not None and r.get("rounds")), key=lambda r: str(r.get("date") or ""), reverse=True)
    if not rows:
        return None
    hs = [r for r in rows if r.get("headshots") is not None]
    won = [r for r in rows if r.get("won") is not None]
    last = rows[:20]
    g = "Last 12 months (HLTV)"
    out = [{"label": "Maps played", "value": len(rows), "group": g},
           {"label": "Kills per map", "value": round(sum(r["kills"] for r in rows) / len(rows), 1), "group": g},
           {"label": "Kills per map, last 20 maps", "value": round(sum(r["kills"] for r in last) / len(last), 1), "group": g}]
    if hs:
        out.append({"label": "Headshots per map", "value": round(sum(r["headshots"] for r in hs) / len(hs), 1), "group": g})
    if won:
        out.append({"label": "Maps won", "value": f"{sum(1 for r in won if r['won'])} of {len(won)}", "group": g})
    return out


def _peek_shared(srv, key):
    """A shared answer already computed and still fresh, or None - never computes (srv._RESULTS is srv.shared's own store)."""
    res, lock = getattr(srv, "_RESULTS", None), getattr(srv, "_RG", None)
    if res is None or lock is None:
        return None
    with lock:
        hit = res.get(key)
    if not hit or len(hit) < 4 or hit[3] <= time.time() or hit[1] != 200:
        return None
    return hit[2]


def _history(srv, x, full=False, peek=False):
    """Game-by-game results from the HLTV tables, only when they reproduce the saved L10 tally. Three Postgres slots: a member
    who cannot get one within 0.5 s sees the saved tally alone. peek=True (with full): the cached year only, no read."""
    n, over, push = x.get("l10_n"), x.get("l10_over"), x.get("l10_push") or 0
    if not full and (not n or over is None):
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
                ser = [({"date": s["date"], "opponent": s.get("opponent"), "value": s["total"], "maps": s.get("maps") or [], "values": s.get("values") or []},
                        s.get("match_id")) for s in srv.records._series(rows, stat)]
            else:
                by, maps = {}, []
                for r in srv.hltv.recent_series(nick, team, since):
                    maps.append(r)
                    if r.get("number") in ((1,) if scope == "1" else (1, 2)):
                        by.setdefault(r["match_id"], []).append(r)
                need = 1 if scope == "1" else 2
                ser = [({"date": m[0]["date"], "opponent": m[0].get("opponent"), "value": sum(y[stat] for y in m), "maps": [y.get("map") for y in m],
                         "values": [y[stat] for y in m]}, m[0].get("match_id"))
                       for m in by.values() if len(m) == need and all(y.get(stat) is not None for y in m)]
            ser.sort(key=lambda s: s[0]["date"], reverse=True)
            # each game's match id rides beside the series (never inside it: the /line bytes stay as they were); closing lines use it
            return 200, {"ser": [s for s, _ in ser], "mids": [m for _, m in ser], "stats": _map_stats(maps) if scope != "123" else None}
        except Exception:
            return 200, None, 1
        finally:
            _HIST_SLOTS.release()
    line = x.get("line")
    key = ("ws_hist", _norm(nick), _norm(team), prop, scope)
    if peek:                                           # the cached year only (no read): an extra book's board build past its budget
        return _peek_shared(srv, key) if full else None
    doc = srv.shared(key, compute, ttl=HISTORY_TTL)[1]
    if full:
        return doc
    ser = (doc or {}).get("ser", [])[:20]
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


def _saved(srv, fc):
    """The saved forecast row's grader inputs, shadow record and projection (one bounded read): {} when there is none."""
    if not fc.get("id") or not _FC_SLOTS.acquire(timeout=1.0):
        return {}
    try:
        con = srv.picklog._read_db()
        try:
            r = con.execute("SELECT inputs, shadow, lambda FROM forecasts WHERE id = ?", (fc["id"],)).fetchone()
        finally:
            con.close()
    finally:
        _FC_SLOTS.release()
    if not r:
        return {}
    out = {"lambda": r["lambda"]}
    if r["inputs"]:
        out["gi"] = json.loads(zlib.decompress(r["inputs"]))
    try:
        out["shadow"] = json.loads(r["shadow"]) if r["shadow"] else None
    except (TypeError, ValueError):
        out["shadow"] = None
    return out


def _context(srv, fc, saved=None):
    saved = _saved(srv, fc) if saved is None else saved
    gi = saved.get("gi")
    if not gi:
        return {}
    out = []
    for k, label in CONTEXT_KEYS:
        v = gi.get(k) if isinstance(gi, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({"key": k, "label": label, "value": round(v, 3), "group": "Saved with this forecast"})
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
    ctx, saved = {}, {}
    try:
        saved = _saved(srv, fc)
        ctx = _context(srv, fc, saved)
    except Exception:
        ctx = {}
    forecast = None
    if fc.get("state") == "ok":
        forecast = {"id": fc.get("id"), "logged_at": fc.get("logged_at"), "model_version": fc.get("model_version"), "p_saved": fc.get("p_pick")}
    ser = _history(srv, x)                             # the chart: only when it reproduces the saved L10
    full = _history(srv, x, full=True) or {}           # the cached year (one read serves both): H2H and stats
    if full.get("stats"):
        ctx = {"rows": full["stats"] + (ctx or {}).get("rows", [])}
    hist = {"tally": rec["history"]["l10"], "avg": rec["history"]["avg"], "series": ser}
    closing = _closing(srv, x, full) if os.environ.get("SCOUT_CLOSING_LINES") == "1" else None
    if closing:
        hist["closing"] = closing                      # SCOUT_CLOSING_LINES: only when a past game has a saved closing line
    if os.environ.get("SCOUT_LINE_HISTORY") == "1":     # lh-1 (workspace_linehist.py): every app's moves + closing markers, display only
        _line_history(srv, x, prop_key, full, hist)
    return 200, {"record": rec, "forecast": forecast, "history": hist,
                 "h2h": workspace_h2h.h2h(full.get("ser"), rec["player"]["opponent"], x.get("line")) if full.get("ser") else None,
                 "books": books, "context": ctx, "market": _market(x), "map_chances": _map_chances(x, saved)}


def _closing(srv, x, full):
    """L10 vs closing (SCOUT_CLOSING_LINES): the player's last 10 results, each against the closing line this platform's board
    showed at that match's lock (closing_lines.py, saved once, append-only). One bounded indexed read per player / stat / maps /
    platform, shared by every member for closing_lines.CACHE_TTL seconds. None = no block (no game has a saved closing line)."""
    try:
        import closing_lines as cl
        if not full or not full.get("ser") or not full.get("mids"):
            return None
        args = (cl.norm(_nick(x)), x.get("prop"), str(x.get("scope") or "12"), _book(x), line_type_of(x))
        rows = srv.shared(("ws_closing",) + args, lambda: (200, cl.rows_for(*args)), ttl=cl.CACHE_TTL)[1]
        return cl.l10_vs_closing(full["ser"], full["mids"], rows or [], platform_label=LABEL.get(_book(x), _book(x)))
    except Exception:
        return None


def _line_history(srv, x, prop_key, full, hist):
    """SCOUT_LINE_HISTORY: the card's line history (one line per app) and closing markers. Never breaks /line."""
    try:
        import workspace_linehist
        import closing_lines as cl
        sib = [_SNAP["index"].get(k) for k in _SNAP["props"].get(prop_key, [])]
        args = (cl.norm(_nick(x)), x.get("prop"), str(x.get("scope") or "12"), _book(x), line_type_of(x))
        lh = workspace_linehist.block(srv, x, sib, full, args)
        if lh:
            hist["line_history"] = lh
        if "closing" not in hist:                      # L10 vs closing beside L10 vs current, from saved closing lines
            c = _closing(srv, x, full)
            if c:
                hist["closing"] = c
    except Exception:
        pass


def _map_chances(x, saved=None):
    """HLTV map chances for the row's match: hltv_mapchances.map_chances_display, the same cached answer as the classic card
    (waits at most 0.5 s). None = no block. grade_use (map_weighted.saved_status, from the saved forecast this card shows):
    "HLTV map chances used" + capture time when they fed that grade, otherwise "Shadow only - not in this grade" with the vm-2
    projection logged on that forecast beside the live one."""
    try:
        import mapchances
        block = mapchances.for_match(x.get("match_id"))
    except Exception:
        return None
    if not saved or saved.get("gi") is None:
        return block
    try:
        import map_weighted
        use = map_weighted.saved_status(saved.get("gi"), saved.get("shadow"), saved.get("lambda"))
        if use.get("used") and block is None:
            block = map_weighted.block_from_record(saved["gi"].get("map_weighted"))
        if block is not None:
            block = dict(block, grade_use=use)
            if use.get("used"):
                block.update(weight=1, note=None)
    except Exception:
        pass
    return block


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
    import workspace_alerts
    import workspace_market_edge
    return h.send(200, {"member": member, "name": name, "scratch": scratch, "sports": access(h, srv, q), "rules_version": rules.RULES_VERSION,
                        "owner": bool(scratch) or bool(h._sports_owner()),
                        "features": {"workspace_v2": os.environ.get("SCOUT_WORKSPACE_V2") == "1",
                                     "public_record": os.environ.get('SCOUT_PUBLIC_RECORD') == '1',
                                     "background_alerts": bool(workspace_alerts.configured()),
                                     "market_tools": os.getenv('SCOUT_MARKET_TOOLS')=='1', "book_links": bool(srv.PP_OPEN_LINK),
                                     **({"ui_tokens": True} if os.getenv('SCOUT_UI_TOKENS', '').strip() == '1' else {}),
                                     **({"genie": True} if os.environ.get("SCOUT_GENIE") == "1" else {}),
                                     **({"genie_persona": True} if os.environ.get("SCOUT_GENIE") == "1" and os.environ.get("SCOUT_GENIE_PERSONA") == "1" else {}),
                                     **_extra_books(), **board_parity.features(),
                                     **({"pickem_ev": True} if os.getenv("SCOUT_PICKEM_EV", "").strip() == "1" else {}),
                                     **(workspace_market_edge.features() if workspace_market_edge.enabled() else {}),
                                     **({"board_h2h": _board_columns(srv)} if workspace_h2h.board_enabled() else {})}})


def _board_columns(srv=None):
    """SCOUT_BOARD_H2H: the break-even the board's Edge column compares with. Underdog standard 2-pick at a 1.0x pick only, from the
    gradecore payout table; any other book, entry or multiplier has no published per-pick break-even here, so it shows no edge.
    edge_cells: the CS2 model-version x stat x period chance bands that pass the slip's settled-results check
    (rules.edge_cells, keyed tag|stat|period like the hub row); the board shows an edge only inside them, so it never shows
    an edge the slip refuses. LoL, NBA and NFL never get one (rules.EV_NOT_VALIDATED). Any failure: no cells, no edge."""
    from gradecore import payouts
    cells = {}
    if srv is not None and not lc.contained("cs2"):        # lc-1: a held leg has no rating, so no edge either
        try:
            for k, bands in rules.edge_cells(_gate(srv), time.time()).items():
                tag, prop, scope = k.rsplit("|", 2)
                cells[f"{tag}|{prop}|{SCOPE.get(scope, (scope,))[0]}"] = bands
        except Exception:
            cells = {}
    return {"ud_standard_break_even": payouts.break_even("underdog", "standard", 2), "entry": "Underdog standard 2-pick",
            "source": payouts.UD_SOURCE, "read_at": payouts.UD_READ_AT,
            "edge_cells": cells, "min_chance": rules.EV_MIN_CHANCE}


def _parity_cells(srv, tags=None):
    """SCOUT_PARITY_* rows: rules.edge_cells, raw tag|prop|scope keys (leg_edge_ok's). Held CS2 letters or any failure: none,
    so no row is rated. Read once per board build, from the same cached gate the slip check uses."""
    if lc.contained("cs2"):
        return {}
    try:
        return rules.edge_cells(_gate(srv, tags), time.time())
    except Exception:
        return {}


def _parity_rated(x, rec, cells):
    """True only where the live slip check would rate this single CS2 leg (rules.leg_edge_ok: half-point line, saved chance
    at 57%+ on Scout's side, a 1.0x pick, a chance band that passed the settled-results check) and its letter is not held."""
    if not cells or rec["scout"].get("letter_held") or not rec["scout"].get("side"):
        return False
    side = rec["scout"]["side"]
    leg = {"sport": "cs2", "line": x.get("line"), "p": x.get("p"), "on_side": True,
           "multiplier": x.get("higher") if side == "over" else x.get("lower"), "tag": x.get("graded_model"),
           "prop": x.get("prop"), "scope": x.get("scope") or "12"}
    return rules.leg_edge_ok(leg, cells)


def _extra_books():
    """SCOUT_BOOK_<PROVIDER> (each default off): the extra books the board's book toggle offers. All off = no key at all."""
    import book_providers
    if not book_providers.enabled_ids():
        return {}
    return {"extra_books": book_providers.features()}


def get_book_board(h, srv, q, member, scratch, name):
    """One extra book's display lines for one sport (workspace_books). Provider off = the unknown-route answer."""
    import book_providers
    import workspace_books
    pid, sport = q.get("book"), _sport_q(h, srv, q)
    if not isinstance(pid, str) or pid not in book_providers.providers() or not book_providers.enabled(pid):
        return h.send(404, {"error": "not found"})
    if sport is None or sport not in book_providers.providers()[pid]["sports"]:
        return h.send(404, {"error": "not found"})
    book_providers.start(data_dir())
    st, body = workspace_books.board(srv, sport, pid)
    return h.send(st, body, "application/json")


BOOK_LINES_TTL = 30


BOOK_LINES_KEEP_S = 600                               # a last good count stands in for a blipped feed this long, then it is left out
_BOOK_LINES_GOOD = {}                                 # (pid, sport) -> (count, epoch) - the last count read from a healthy feed


def _book_lines(srv, sports):
    """{as_of, books: {pid: {sport: open lines}}} for every enabled extra book on each of these sports it lists, counted from the same
    shared book-board build members read (workspace_books.board, single flight, TTL 30 s; the count is noted beside the build, so
    nothing is parsed twice), so this adds no provider request. Only lines whose match has not started count (the board's own rule).
    A failed build or an unhealthy feed is unknown, never 0 (transient-failure rule): the last good count stands in for up to
    BOOK_LINES_KEEP_S, otherwise that book and sport are left out and the hub keeps what it had. One shared build per sport set."""
    import book_providers
    import workspace_books

    def compute():
        out, now = {}, time.time()
        for pid in book_providers.enabled_ids():
            for sport in book_providers.providers()[pid]["sports"]:
                if sport not in sports:
                    continue
                try:
                    n = workspace_books.open_lines(srv, sport, pid, now)
                except Exception:                          # noqa: BLE001 - one book failing never hides the others
                    n = None
                if n is not None:
                    _BOOK_LINES_GOOD[(pid, sport)] = (n, now)
                else:
                    g = _BOOK_LINES_GOOD.get((pid, sport))
                    n = g[0] if g and now - g[1] <= BOOK_LINES_KEEP_S else None
                if n is not None:
                    out.setdefault(pid, {})[sport] = n
        return 200, json.dumps({"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), "books": out}, separators=(",", ":")).encode()
    return srv.shared(("ws_book_lines", tuple(sports)), compute, ttl=BOOK_LINES_TTL)


def get_book_lines(h, srv, q, member, scratch, name):
    """Which extra books have lines on which of this member's sports right now: the hub's book toggle shows a book on a sport tab
    only when it has lines there (owner 2026-11-05: no FanDuel / BetRivers on the CS2 or LoL tab). Built only for the sports this
    member sees (one shared build per sport set). A count of 0 is a real answer from a healthy feed; a book or sport left out is
    unknown right now. Every provider off = the unknown-route answer."""
    import book_providers
    if not book_providers.enabled_ids():
        return h.send(404, {"error": "not found"})
    book_providers.start(data_dir())
    allowed = access(h, srv, q)
    sports = tuple(s for s in SPORTS if allowed.get(s))
    try:
        st, body = _book_lines(srv, sports)
        doc = json.loads(body)
    except Exception:
        return h.send(503, {"error": "Book lines unavailable"})
    books = {pid: {s: n for s, n in v.items() if s in sports and isinstance(n, int) and n >= 0} for pid, v in (doc.get("books") or {}).items()}
    return h.send(200, {"as_of": doc.get("as_of"), "books": {pid: v for pid, v in books.items() if v}})


def get_market_tools(h, srv, q, member, scratch, name):
    """SCOUT_MARKET_TOOLS: the Tools route's +EV feed and arbitrage sheet (workspace_markets), one shared build for everyone."""
    if os.getenv('SCOUT_MARKET_TOOLS')!='1':return h.send(404,{'error':'not found'})
    import workspace_markets
    import book_providers
    book_providers.start(data_dir())
    st, body = workspace_markets.shared(srv)
    if st != 200:
        return h.send(503, {"error": "market_tools_unavailable"})
    return h.send(200, workspace_markets.for_member(body, access(h, srv, q), owner=bool(scratch) or bool(h._sports_owner())))


def get_pickem_ev(h, srv, q, member, scratch, name):
    """SCOUT_PICKEM_EV (default off): the pick'em +EV board (workspace_ev), one shared build per parameters and sports seen."""
    import workspace_ev
    if not workspace_ev.enabled():
        return h.send(404, {"error": "not found"})
    try:
        st, body = workspace_ev.shared(srv, q, access(h, srv, q))
    except Exception:                                  # noqa: BLE001 - the board stays usable without it
        return h.send(503, {"error": "pickem_ev_unavailable"})
    if st != 200:
        return h.send(503, {"error": "pickem_ev_unavailable"})
    return h.send(200, body)


def get_market_edge(h, srv, q, member, scratch, name):
    """SCOUT_MARKET_EDGE: the board's Market edge column and best line per side for one sport (workspace_market_edge), one shared
    build per sport every 30 s for everyone. Flag off = the unknown-route answer."""
    import workspace_market_edge as wme
    if not wme.enabled():
        return h.send(404, {"error": "not found"})
    sport = _sport_q(h, srv, q)
    if sport is None:
        return h.send(404, {"error": "not found"})
    try:
        st, body = wme.shared(srv, sport)
    except Exception:                                  # noqa: BLE001 - the board stays usable without it
        return h.send(503, {"error": "market_edge_unavailable"})
    if st != 200:
        return h.send(503, {"error": "market_edge_unavailable"})
    return h.send_shared(("ws_market_edge", sport), 200, body, keep=60)


def get_alert_delivery(h, srv, q, member, scratch, name):
    import workspace_alerts
    return h.send(200,workspace_alerts.subscription(data_dir(),member))


def post_alert_delivery(h, srv, q, member, scratch, name, n):
    if n>2048:
        h.rfile.read(n)
        return h.send(413,{'error':'request too large'})
    d=_read_json(h,srv,n)
    if not d or not isinstance(d.get('enabled'),bool):return h.send(400,{'error':'enabled must be true or false'})
    if _limited(h,srv,'state',member,STATE_PER_MIN):return None
    sess=h.member() or {}
    if d['enabled'] and (scratch or not sess.get('ok') or not sess.get('user')):
        return h.send(403,{'error':'Sign in with Whop to enable personal notifications.'})
    import workspace_alerts
    try:
        out=workspace_alerts.subscription(data_dir(),member,d['enabled'],sess.get('user'),
                                         [s for s,on in access(h,srv,q).items() if on])
    except ValueError:
        return h.send(503,{'error':'Whop push is not configured yet.'})
    workspace_alerts.start(srv)
    return h.send(200,out)


def _sport_q(h, srv, q):
    """The sport a read asks for, or None when this member does not see it. CS2 is the default."""
    sport = q.get("sport") or "cs2"
    if sport not in SPORTS or (sport != "cs2" and not access(h, srv, q)[sport]):
        return None
    return sport


def get_board(h, srv, q, member, scratch, name):
    sport = _sport_q(h, srv, q)
    if sport is None:
        return h.send(404, {"error": "not found"})
    if sport == "cs2":
        snap = snapshot(srv)
        if snap is None:
            return h.send(503, {"error": "board_unavailable"})
        return h.send_shared(("ws_board",), 200, snap["body"], keep=60)
    snap = _adapter("lol").snapshot(srv) if sport == "lol" else _adapter(sport).snapshot(srv, sport)
    if snap is None:
        return h.send(503, {"error": "board_unavailable"})
    return h.send_shared(("ws_board", sport), 200, snap["body"], keep=60)


def _line_shared(h, srv, q):
    """(status, body bytes) of one /line read from the shared single-flight copy, or None when this member cannot see it."""
    book, rid, sport = q.get("book"), q.get("id"), _sport_q(h, srv, q)
    if sport is None or not isinstance(book, str) or not book or len(book) > 40 or not isinstance(rid, str) or not rid or not _ID_RE.match(rid):
        return None
    return _line_for(srv, sport, book, rid)


def _line_for(srv, sport, book, rid):
    """(status, body bytes) of one line's research doc (shared single flight), or None when it is not on a board now."""
    import book_providers
    if book not in BOOKS and book in book_providers.providers() and str(rid).startswith("x-"):
        # audit 2026-11-05 A2/B4: an extra book's line (Sleeper, Dabble) opens the board player's research doc at its own line.
        # card-why 2026-11-06: NBA / NFL too; a FanDuel / BetRivers row of the sports board itself (no "x-" id) keeps its own doc
        p = book_providers.providers()[book]
        if not book_providers.enabled(book) or sport not in p["sports"]:
            return None
        import workspace_books
        fn = lambda: workspace_books.line_doc(srv, sport, book, rid)
    elif sport == "cs2":
        if book not in BOOKS or snapshot(srv) is None or (book, rid) not in _SNAP["index"]:
            return None
        fn = lambda: _line_body(srv, book, rid)
    elif sport == "lol":
        mod = _adapter("lol")
        snap = mod.snapshot(srv)
        if snap is None or (book, rid) not in snap["index"]:
            return None
        fn = lambda: mod.line_doc(srv, book, rid)
    else:
        mod = _adapter(sport)
        snap = mod.snapshot(srv, sport)
        if snap is None or (book, rid) not in snap["index"]:
            return None
        fn = lambda: mod.line_doc(srv, sport, book, rid)

    def compute():
        st, doc = fn()
        return st, json.dumps(doc, default=str, separators=(",", ":")).encode()
    return srv.shared(("ws_line", sport, book, rid), compute, ttl=LINE_TTL)


def get_line(h, srv, q, member, scratch, name):
    got = _line_shared(h, srv, q)
    if got is None:
        return h.send(404, {"error": "not found"})
    st, body = got
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
    sport = _sport_q(h, srv, q)
    if sport is None:
        return h.send(404, {"error": "not found"})
    if sport == "cs2":
        st_, body_ = srv.shared(("results",), lambda: (200, json.dumps(srv.results_payload(), default=str).encode()), ttl=300)
        return h.send_shared(("results",), st_, body_, keep=300)
    mod = _adapter(sport)

    def compute():
        try:
            doc = mod.results(srv) if sport == "lol" else mod.results(srv, sport)
        except mod.Down:                        # not written yet, or the sports API down: the page says so, never a zero
            return 503, b'{"error":"results_unavailable"}'
        return 200, json.dumps(doc, default=str, separators=(",", ":")).encode()
    st_, body_ = srv.shared(("ws_results", sport), compute, ttl=300)
    return h.send_shared(("ws_results", sport), st_, body_, keep=300) if st_ == 200 else h.send(st_, body_)


def get_state(h, srv, q, member, scratch, name):
    st = store()
    rev = st.state_rev(member)
    ent = _PRIVATE.get((member, "state"))
    if ent is None or ent[3] != rev:
        ent = _entry(json.dumps(st.get_state(member), separators=(",", ":")).encode(), rev)
        _PRIVATE.put((member, "state"), ent)
    return _send_private(h, srv, ent)


def _tally_of(ser, line, n):
    """Over, under and push at this line over the newest n results, the same count the research panel makes from the same rows."""
    s = ser[:n]
    if not s:
        return None
    return {"over": sum(g["value"] > line for g in s), "under": sum(g["value"] < line for g in s), "push": sum(g["value"] == line for g in s), "n": len(s)}


_HITS_EMPTY = b'{"rows":{},"complete":false}'


def _hits_key(snap):
    items = list(snap['index'].items())
    key = tuple((book, rid, x.get('line'), x.get('l10_n'), x.get('l10_over'), x.get('l10_push')) for (book, rid), x in items)
    cols = workspace_h2h.board_enabled()              # SCOUT_BOARD_H2H: the H2H and Season columns ride on this same shared pass
    return items, (("h2h",) + key if cols else key), cols


def _hits_doc(srv, snap):
    """L5 and L20 of the CS2 board. A board row only carries L10, so the rest comes from the same HLTV rows /line reads, only when
    they reproduce the saved L10. One pass at a time, HITS_BUDGET_S at most, one history slot at a time: a member never waits on
    it, later passes go on from what the earlier ones left in the shared history cache, and rows it has not reached show L10.
    Members' reads go through _hits_request (the background filler below runs these passes); this stays the one pass."""
    d = _HITS_DOC
    items, key, cols = _hits_key(snap)
    if d.get('key') == key and d["body"] is not None and time.time() - d["at"] < d["ttl"]:
        return d["body"]
    if not d["lock"].acquire(blocking=False):
        return d["body"] or _HITS_EMPTY
    try:
        return _hits_pass(srv, items, key, cols, HITS_BUDGET_S, snap.get('index'))[0]
    finally:
        d["lock"].release()


def _hits_pass(srv, items, key, cols, budget, board=None):
    """One budgeted pass under _HITS_DOC["lock"] -> (body, complete, rows still without a history). `board` (the board index the
    pass read) is published in the same update as the copy (built_for), so a read can tell which board a copy belongs to."""
    d = _HITS_DOC
    # owner 2026-11-06 (phone screenshot, most CS2 rows L10 only): a moved line used to wipe every filled row and restart the
    # pass, so on a busy board it rarely finished. Rows still on the board at the same line are KEPT; only new or moved rows
    # are read, missing ones first, and the pass walks that short list.
    if d.get('key') != key:
        live = {f"{book}|{rid}": (x.get('line'), x.get('l10_n'), x.get('l10_over'), x.get('l10_push')) for (book, rid), x in items}
        sigs = d.get('sigs') or {}
        kept = {k: v for k, v in (d.get('rows') or {}).items() if k in live and sigs.get(k) == live[k]}
        d.update(key=key, cursor=0, rows=kept, sigs={k: sigs[k] for k in kept})
    t0, rows, complete, missing, failed = time.time(), dict(d.get('rows', {})), True, 0, None
    todo = [it for it in items if f"{it[0][0]}|{it[0][1]}" not in rows]
    start = 0
    for i in range(start, len(todo)):
        (book, rid), x = todo[i]
        if i > start and time.time() - t0 > budget:
            complete = False
            break
        # owner 2026-11-04: L5 is never empty when L10 is shown, and L20 counts the games he has (e.g. 14/17), so the newest
        # HLTV results count even when they do not reproduce the saved L10 exactly
        try:                                # review of N3: one row that raises is left out (shows L10), never the whole fill
            ser = (_history(srv, x, full=True) or {}).get("ser")
            if ser:
                row = {"line": x.get('line'), "l5": _tally_of(ser, x.get("line"), 5), "l20": _tally_of(ser, x.get("line"), 20)}
                if cols:
                    row.update(h2h=workspace_h2h.board_h2h(ser, x.get("opp_name") or x.get("opponent"), x.get("line")),
                               season=workspace_h2h.board_season(ser, x.get("line")))
                d.setdefault('sigs', {})[f"{book}|{rid}"] = (x.get('line'), x.get('l10_n'), x.get('l10_over'), x.get('l10_push'))
                rows[f"{book}|{rid}"] = row
            else:
                missing += 1
        except Exception as e:              # noqa: BLE001
            missing += 1
            failed = failed or e
    if failed is not None:
        _hits_log(key, failed)
    d['rows'] = rows
    body = json.dumps({"rows": rows, "complete": complete}, separators=(",", ":")).encode()
    if d["body"] == body:
        body = d["body"]                    # identical bytes: the same object, so nothing is compressed again
    d.update(body=body, at=time.time(), ttl=HITS_TTL if complete else HITS_FILL_TTL, built_for=board)
    return body, complete, missing


# Owner site audit 2026-11-06 N3: L5 / L20 / H2H / SZN cells stayed blank on many CS2 rows until the player was opened (and the
# "+N more props" rows started blank): the pass above only ran inside a member's /hits read, HITS_BUDGET_S of work at most once
# every HITS_FILL_TTL s, so a full board took tens of minutes to fill. Now ONE background thread runs the passes back to back
# (HITS_SLICE_S each, publishing after every slice) from the first /hits read on, and every new board build hands it the new
# board at once, so the hit rates arrive with the board. A member's read never does that work: it returns the copy as it stands
# (the very first read after a start waits at most HITS_BUDGET_S for the first slice). One history slot at a time (the other
# _HIST_SLOTS stay free for cards being opened), the same shared history cache, no new query: safe at 700 members.
HITS_SLICE_S = 2.0          # the filler publishes what it has read at least this often
HITS_IDLE_S = 300           # with the board filled and no new board, the filler thread ends after this long (a read restarts it)
HITS_RETRY_S, HITS_RETRIES = 5.0, 2     # rows left without a history (a busy slot) are tried again this soon, this many times
_HITS_FILL = {"thread": None, "gen": 0, "srv": None, "snap": None, "kicked": None, "done_key": None, "done_at": 0.0,
              "logged": None, "wake": threading.Event(), "first": threading.Event(), "lock": threading.Lock()}


def _hits_log(key, e):
    """A hit-rate row or slice that raised: logged once per board (its exception type and a short message), never per row."""
    f = _HITS_FILL
    if f["logged"] == key:
        return
    f["logged"] = key
    sys.stderr.write(f"  hit rates: a row could not be filled: {type(e).__name__}: {str(e)[:200]}\n")


def _hits_fill(gen):
    f = _HITS_FILL
    retries = 0
    while f["gen"] == gen:
        f["wake"].clear()
        srv, snap = f["srv"], f["snap"]
        idx = snap.get("index") if snap else None
        if idx is None:
            break
        try:
            with _HITS_DOC["lock"]:
                if f["gen"] != gen:
                    break
                items, key, cols = _hits_key({"index": idx})
                # the first slice after a start is HITS_BUDGET_S, so the first read (which waits that long) gets its rows
                budget = HITS_BUDGET_S if _HITS_DOC["body"] is None else HITS_SLICE_S
                _, complete, missing = _hits_pass(srv, items, key, cols, budget, idx)
        except Exception as e:                         # noqa: BLE001 - a failed slice keeps the copy as it stood; tried again
            complete, missing = False, 0
            _hits_log(("slice", id(idx)), e)
            time.sleep(HITS_RETRY_S)
        f["first"].set()
        if not complete:
            time.sleep(0.05)                           # hand the GIL back between slices
            continue
        if missing and retries < HITS_RETRIES:
            retries += 1
            if f["wake"].wait(HITS_RETRY_S):
                retries = 0
            continue
        retries = 0
        with f["lock"]:
            f["done_key"], f["done_at"] = key, time.time()
        if not f["wake"].wait(HITS_IDLE_S):
            with f["lock"]:
                if not f["wake"].is_set() and f["gen"] == gen:
                    f["thread"] = None
                    return
    with f["lock"]:
        if f["gen"] == gen:
            f["thread"] = None


def _hits_kick(srv, snap):
    """Hand the filler this board (no read here). Wakes it only for a board whose rows it has not filled, or filled HITS_TTL ago.
    Review of N3: each 5-s CS2 board build is a new index object, so the old identity check woke the filler on every build; a
    build with the same rows (board key) as the copy just finished is marked filled at once instead. A member's read of a board
    already handed over returns at the first check (no per-request work)."""
    f = _HITS_FILL
    idx = snap.get("index")
    with f["lock"]:
        f["srv"], f["snap"] = srv, snap
        t = f["thread"]
        if f["kicked"] is idx and (_HITS_DOC.get("built_for") is idx or (t is not None and t.is_alive())):
            return
        f["kicked"] = idx
        key = _hits_key(snap)[1]
        if key == f["done_key"] and key == _HITS_DOC.get("key") and time.time() - f["done_at"] < HITS_TTL:
            _HITS_DOC["built_for"] = idx                # the same rows as the copy just finished: nothing to read
            return
        f["wake"].set()
        t = f["thread"]
        if t is None or not t.is_alive():
            t = f["thread"] = threading.Thread(target=_hits_fill, args=(f["gen"],), name="ws-hits-fill", daemon=True)
            t.start()


def _hits_reset():
    f = _HITS_FILL
    with f["lock"]:
        f["gen"] += 1
        f.update(thread=None, srv=None, snap=None, kicked=None, done_key=None, done_at=0.0, logged=None)
        f["wake"].set()
        f["wake"] = threading.Event()
        f["first"] = threading.Event()


def _hits_open(body):
    """The copy marked complete:false (made once per copy): the browser reads it again in HITS_RETRY instead of BOARD_KEEP."""
    d = _HITS_DOC
    c = d.get("open")
    if c and c[0] is body:
        return c[1]
    tail = b'"complete":true}'
    out = body[:-len(tail)] + b'"complete":false}' if body.endswith(tail) else body
    d["open"] = (body, out)
    return out


def _hits_answer(srv, snap):
    """(shared key, body) of a member's /hits read: the filler's copy as it stands. No history is read on the request thread.
    Review of N3: a copy filled from an earlier board (the filler has not finished the board just built) says complete:false,
    so new or moved rows are read again in HITS_RETRY, not BOARD_KEEP (45 s)."""
    _hits_kick(srv, snap)
    if _HITS_DOC["body"] is None:                      # only the first reads after a start wait: about the old in-request budget
        _HITS_FILL["first"].wait(max(0.0, HITS_BUDGET_S) + 0.5)
    board = _HITS_DOC.get("built_for")                 # read before the copy: a copy published in between is taken as not this board's
    body = _HITS_DOC["body"]
    if body is None:
        return ("ws_hits_open",), _HITS_EMPTY
    if board is not snap.get("index"):
        return ("ws_hits_open",), _hits_open(body)
    return ("ws_hits",), body


def _hits_request(srv, snap):
    return _hits_answer(srv, snap)[1]


def get_hits(h, srv, q, member, scratch, name):
    snap = snapshot(srv)
    if snap is None:
        return h.send(503, {"error": "board_unavailable"})
    key, body = _hits_answer(srv, snap)
    return h.send_shared(key, 200, body, keep=60)


def get_stated(h, srv, q, member, scratch, name):
    """CS2 stated against observed, by letter and by market, from the same stored export as Results."""
    st_, body = stated_body(srv)
    return h.send_shared(("ws_stated",), st_, body, keep=300)


def stated_body(srv):
    """(status, body) of the shared CS2 stated-against-observed build: one build every 300 s for the stated route and the Ask tab's
    tier record (workspace_genie.tier_record) together."""
    def compute():
        import results_stated
        return 200, json.dumps(results_stated.stated_observed(getattr(srv, "_p1_export_printed", srv._p1_export)()), default=str, separators=(",", ":")).encode()
    return srv.shared(("ws_stated",), compute, ttl=300)


def get_rules(h, srv, q, member, scratch, name):
    return h.send(200, rules.rules_payload())


def get_diagnostics(h, srv, q, member, scratch, name):
    if not (_token_in_use(h, q) or h._sports_owner()):
        return h.send(404, {"error": "not found"})
    return h.send(200, store().diagnostics())


def _lol_review_names(path, doc):
    """Owner audit 2026-11-06 (T8): LoL comparison rows carry the data center's ids (lol:player:N, lol:series:N). Put the player's
    name and the series' teams in their place, read from the same lol.db (one bounded read per page of at most 200 rows); an id
    with no saved name stays as it is."""
    import sqlite3
    rows = doc.get('rows') or []
    pids = sorted({r['player'] for r in rows if str(r.get('player') or '').startswith('lol:player:')})
    sids = sorted({r['event'] for r in rows if str(r.get('event') or '').startswith('lol:series:')})
    if not (pids or sids) or not Path(path).exists():
        return doc
    names, series = {}, {}
    try:
        with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.execute('PRAGMA query_only=ON')
            if pids:
                names = dict(db.execute('SELECT eid, label FROM entities WHERE eid IN (%s) AND label IS NOT NULL AND label != ""'
                                        % ','.join('?' * len(pids)), pids).fetchall())
            if sids:
                series = {sid: (a, b) for sid, a, b in db.execute('SELECT sid, team1_name, team2_name FROM c_series WHERE sid IN (%s)'
                                                                   % ','.join('?' * len(sids)), sids).fetchall()}
    except sqlite3.Error:
        return doc
    for r in rows:
        if names.get(r.get('player')):
            r['player'] = names[r['player']]
        a, b = series.get(r.get('event')) or (None, None)
        if a and b:
            r['event'] = f'{a} vs {b}'
    return doc


def get_grade_review(h, srv, q, member, scratch, name):
    if not (_token_in_use(h, q) or h._sports_owner()):
        return h.send(404, {'error': 'not found'})
    sport = q.get('sport', 'cs2')
    if sport not in SPORTS:
        return h.send(400, {'error': 'unknown sport'})
    try:
        before = int(q['before']) if q.get('before') else None
        if sport in ('cs2', 'lol'):
            from gradecore.reporting import comparisons
            path = Path(srv.LOL_CENTER) / 'lol.db' if sport == 'lol' else Path(srv.picklog.DB)
            doc = comparisons(path, sport, before=before)
            if sport == 'lol':
                _lol_review_names(path, doc)
        else:
            import urllib.request
            from urllib.parse import urlencode
            base = os.getenv('SPORTS_API_BASE', '').rstrip('/')
            # owner audit 2026-11-06 (T8): the sports service admits /api/sports/admin/* only with an st1 token of role admin
            # (sports/service.py; X-Admin-Token opens /admin/ops alone), so every NBA / NFL request here was refused. The
            # esports server is the token issuer, through server.py's one sports hook (workspace_sports.admin_token): short-lived,
            # minted only on this owner-only route.
            import workspace_sports
            token = workspace_sports.admin_token(srv)
            if not base.startswith('https://') or not token:
                return h.send(503, {'error': 'Sports report connection unavailable'})
            query = {'league': sport}
            if before is not None: query['before'] = before
            request = urllib.request.Request(base + '/api/sports/admin/model/gradecore-comparison?' + urlencode(query),
                                             headers={'Authorization': 'Bearer ' + token})
            # Never forward credentials through redirects.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs): return None
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
                raw = response.read(512001)
                if len(raw) > 512000: raise ValueError('response exceeds budget')
                doc = json.loads(raw)
        return h.send(200, doc)
    except ValueError:
        return h.send(400, {'error': 'invalid report request'})
    except Exception:
        return h.send(503, {'error': 'Comparison report unavailable'})


# ---------------------------------------------------------------------------------------------------------------- Slip Studio (sq-1)
# slip_quality.py chooses the legs; these routes feed it the shared snapshots. One pool per sport, rebuilt only when that sport's
# snapshot bytes or the band table change (a per-sport lock: a crowd waits for one build). One batch per option set, shared for
# GEN_TTL s over srv.shared's single flight, so 700 members pressing Generate run a handful of searches, each bounded by
# slip_quality.NODE_LIMIT. The band table is rebuilt at most every CAL_TTL s from the stored P1 export and the LoL settlements.

GEN_PER_MIN, ADVICE_PER_MIN = 20, 60
GEN_TTL, CAL_TTL = 20, 900
EXCLUDE_MAX = 60
SLIP_SCOPE = {"map1": "1", "maps12": "12", "maps13": "123"}
_SLIP_POOL = {}
_SLIP_LOCKS = {s: threading.Lock() for s in SPORTS}
LOL_SETTLED_SQL = (
    "SELECT f.stat, f.p_over, f.p_under, f.model_side, f.sid, f.series_start, s.over_hit FROM lol_forecasts f "
    "JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts WHERE forecast_at < series_start "
    "GROUP BY platform, market_key, line) z ON z.id = f.id "
    "JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv ON "
    "lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
    "JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v "
    "WHERE s.status = 'graded' AND f.side_offered = 1")


def _lol_settled(srv):
    """LoL settled pre-match forecasts on offered sides (lol/center/learning.dashboard's join), read-only."""
    import workspace_lol
    con = workspace_lol._open(srv, "lol.db")
    try:
        return [dict(r) for r in con.execute(LOL_SETTLED_SQL).fetchall()]
    finally:
        con.close()


def _slip_table(srv):
    """The sq-1 band table, or None when nothing settled can be read (legs then rank at their published chance)."""
    def compute():
        units = sq.units_from_p1(srv._p1_export() or {})
        try:
            units += sq.units_from_lol(_lol_settled(srv))
        except Exception:                       # no LoL record on this host: LoL legs rank at their published chance
            pass
        return 200, sq.calibration_table(units)
    try:
        return srv.shared(("ws_slip_cal",), compute, ttl=CAL_TTL)[1]
    except Exception:
        return None


def _sport_snap(srv, sport):
    if sport == "cs2":
        return snapshot(srv)
    return _adapter("lol").snapshot(srv) if sport == "lol" else _adapter(sport).snapshot(srv, sport)


def _slip_pool(srv, sport, table):
    """(every pool leg of one sport, research legs included and flagged; exclusion counts), or None with no snapshot."""
    snap = _sport_snap(srv, sport)
    if snap is None:
        return None
    body = snap["body"]
    with _SLIP_LOCKS[sport]:
        hit = _SLIP_POOL.get(sport)
        if hit and hit[0] is body and hit[1] is table:
            return hit[2], hit[3]
        recs = []
        for (book, _rid), x in list((snap.get("index") or {}).items()):
            if book not in sq.PLATFORMS:
                continue
            try:
                recs.append(record_of(x) if sport == "cs2" else json.loads(x["rec"]))
            except (KeyError, TypeError, ValueError):
                continue
        legs, why = sq.pool(recs, feeds={sport: snap.get("feeds") or {}}, research_ok=True, table=table)
        _SLIP_POOL[sport] = (body, table, legs, why)
        return legs, why


def _gen_opts(q, allowed):
    """The member's Generate settings -> (options, None), or (None, the reason in member words)."""
    platform = q.get("platform") or "prizepicks"
    if platform not in sq.PLATFORMS:
        return None, "Choose PrizePicks or Underdog."
    et = q.get("type") or sq.ENTRY_TYPES[platform][0]
    if et not in sq.ENTRY_TYPES[platform]:
        return None, "Choose a supported entry type."
    try:
        count, size = int(q.get("slips") or 8), int(q.get("picks") or 2)
    except ValueError:
        return None, "Choose 1-12 slips and 2-6 picks per slip."
    if not 1 <= count <= sq.MAX_SLIPS or not sq.MIN_PICKS <= size <= sq.MAX_PICKS:
        return None, "Choose 1-12 slips and 2-6 picks per slip."
    if platform == "underdog" and et == "flex" and size < 3:
        return None, "Underdog Flex needs at least 3 picks."
    if platform == "prizepicks" and et == "flex" and size < 3:
        return None, "PrizePicks Flex needs at least 3 picks."            # Flex 2-pick: help center only, not in the app
    asked = [s for s in (q.get("sports") or "cs2").split(",") if s]
    if not asked or len(asked) > len(SPORTS) or any(s not in SPORTS for s in asked):
        return None, "Choose at least one sport."
    sports = tuple(s for s in SPORTS if s in asked and allowed.get(s))
    if not sports:
        return None, "Choose at least one sport."
    mix, games, repeats = q.get("mix") or "any", q.get("games") or "spread", q.get("repeats") or "allow"
    lines = q.get("lines") or sq.LINE_RULE_DEFAULT        # owner 2026-11-05: goblins stay out unless the member allows them
    if mix not in ("any", "mixed", "single") or games not in ("spread", "stacks") or repeats not in ("allow", "none") or lines not in sq.LINE_RULES:
        return None, "Choose supported generator settings."
    if mix == "mixed" and platform == "underdog":
        return None, "Underdog does not publish mixed-sport entries. Choose PrizePicks or one sport."
    if mix == "mixed" and len(sports) < 2:
        return None, "Mixed sports needs at least two sports."
    if games == "stacks" and not sq.same_match_allowed(platform):
        return None, "PrizePicks keeps its same-match limits in its app. Scout builds same-match legs on Underdog only."
    exclude = tuple(sorted({x for x in (q.get("exclude") or "").split(",") if x}))
    if len(exclude) > EXCLUDE_MAX or any(len(x) > 200 or not _LEG_ID_RE.match(x) for x in exclude):
        return None, "Too many lines to leave out. Generate again."
    research = q.get("research") == "1" and any(s in sq.RESEARCH_SPORTS for s in sports)
    return {"platform": platform, "entry_type": et, "count": count, "size": size, "sports": sports, "mix": mix, "same_match": games == "stacks",
            "no_repeat": repeats == "none", "lines": lines, "research": research, "exclude": exclude}, None


def _ev_leg(l):
    return {"name": l["player"], "sport": l["sport"], "match": l["event"], "line": l["line"], "multiplier": l["multiplier"], "on_side": True, "p": l["p"],
            "tag": l["model_version"], "prop": l["stat"], "scope": SLIP_SCOPE.get(l["period"], l["period"]), "line_type": l["line_type"]}


def _wire_slip(s, srv, now):
    out = {"platform": s["platform"], "entry_type": s["entry_type"], "why": s["why"], "same_match": s["same_match"],
           "legs": [{"sport": l["sport"], "book": l["book"], "row_id": l["row_id"], "side": l["side"], "line": l["line"], "line_type": l["line_type"],
                     "player": l["player"], "p": l["p"], "rank": l["rank"], "rank_p": round(l["rank_p"], 4), "research": l["research"],
                     "record": "Research estimate" if l["research"] else sq.band_text(l["band"], l["sport"], l["line_type"])} for l in s["legs"]]}
    if s["all_hit"] is not None:
        out["all_hit"] = round(s["all_hit"], 4)
    if s["payout"]:
        out["payout"] = s["payout"]
        ev = rules.ev_status(s["platform"], s["entry_type"], [_ev_leg(l) for l in s["legs"]], lambda: _gate(srv), now.timestamp())
        if ev.get("available"):                 # a verified payout AND chances that passed the settled-results gate (CS2 only today)
            out["ev"] = {"ev": ev["ev"], "hit_chance": ev["hit_chance"]}
    return out


def _ranked(legs):
    """Copies, best first, each ranked on its own platform."""
    legs = sorted(legs, key=lambda l: (-l["rank_p"], -l["p"], l["key"]))
    out, seen = [], {}
    for l in legs:
        seen[l["book"]] = seen.get(l["book"], 0) + 1
        out.append(dict(l, rank=seen[l["book"]]))
    return out


def _generate(srv, o):
    now = datetime.now(timezone.utc)
    table = _slip_table(srv)
    legs, why, missing = [], {}, []
    for sport in o["sports"]:
        got = _slip_pool(srv, sport, table)
        if got is None:
            missing.append(sport)
            continue
        for l in got[0]:
            st = sq._ts(l["start"])
            if st is None or (st - now).total_seconds() < sq.LEAD_S:
                why["started"] = why.get("started", 0) + 1
            elif l["research"] and not o["research"]:
                why["research"] = why.get("research", 0) + 1
            elif l["key"] in o["exclude"]:
                why["excluded"] = why.get("excluded", 0) + 1
            else:
                legs.append(l)
        for k, v in got[1].items():
            why[k] = why.get(k, 0) + v
    legs = _ranked(legs)
    b = sq.build(legs, dict(o, break_even=True))      # owner audit 2026-11-06: only slips that clear their payout's break-even
    mine = [l for l in legs if l["book"] == o["platform"]]
    doc = {"version": sq.VERSION, "built_at": now.isoformat(timespec="seconds"), "options": dict(o, sports=list(o["sports"]), exclude=len(o["exclude"])),
           "pool": {"legs": len(mine), "matches": len({(l["sport"], l["event"]) for l in mine}), "excluded": why, "unavailable": missing,
                    "banded": bool(table and table.get("cells"))},
           "slips": [_wire_slip(s, srv, now) for s in b["slips"]], "shortage": b["shortage"], "search": b["search"]}
    return 200, json.dumps(doc, default=str, separators=(",", ":")).encode()


def get_generate(h, srv, q, member, scratch, name):
    o, why = _gen_opts(q, access(h, srv, q))
    if o is None:
        return h.send(400, {"error": why})
    if _limited(h, srv, "generate", member, GEN_PER_MIN):
        return None
    key = ("ws_gen", o["platform"], o["entry_type"], o["count"], o["size"], o["sports"], o["mix"], o["same_match"], o["no_repeat"], o["lines"],
           o["research"], hashlib.sha256("\n".join(o["exclude"]).encode()).hexdigest()[:16] if o["exclude"] else "")
    try:
        st_, body = srv.shared(key, lambda: _generate(srv, o), ttl=GEN_TTL)
    except Exception:
        return h.send(503, {"error": "Scout could not build slips just now. Try again."})
    return h.send(st_, body)


def _advice_legs(d):
    legs = _legs_of(d)
    if legs is None:
        return None
    for l in legs:
        for k in ("stat", "period", "player_key"):
            if l.get(k) is not None and (not isinstance(l[k], str) or len(l[k]) > 120):
                return None
    return legs


_LINE_TYPES = ("standard", "goblin", "demon")
_LINE_TYPE_CACHE = {}


def _line_types(srv, sport):
    """{(platform, offer_id): line_type} from the sport's shared board, rebuilt once per board build (same parse workspace_books
    reads). The adder needs it for slip legs outside Scout's pool, so a goblin the member added still counts toward "at most 1 goblin"."""
    try:
        import workspace_books
        recs, s = workspace_books.records(srv, sport)
    except Exception:                                  # noqa: BLE001 - a board blip falls back to the leg's own line_type
        return {}
    if recs is None:
        return {}
    c = _LINE_TYPE_CACHE.get(sport)
    if c and c[0] is recs:
        return c[1]
    out = {}
    for r in recs:
        o, m = r.get("offer") or {}, r.get("market") or {}
        lt = m.get("line_type")
        if lt in _LINE_TYPES and o.get("platform") and o.get("offer_id"):
            out[(o["platform"], str(o["offer_id"]))] = lt
    _LINE_TYPE_CACHE[sport] = (recs, out)
    return out


def post_slip_advice(h, srv, q, member, scratch, name, n):
    """The adder: per leg of the member's slip (in its order), same-player / same-match / conflict facts, the band record, and the
    best replacement on the same platform that fits the other legs. Reads the shared pools only; never changes the slip."""
    d = _read_json(h, srv, n)
    legs = _advice_legs(d) if d else None
    if legs is None:
        return h.send(400, {"error": "bad slip"})
    if _limited(h, srv, "advice", member, ADVICE_PER_MIN):
        return None
    allowed, table, now = access(h, srv, q), _slip_table(srv), datetime.now(timezone.utc)
    pool_legs = []
    for sport in [s for s in SPORTS if allowed.get(s) and any(l["sport"] == s for l in legs)]:
        got = _slip_pool(srv, sport, table)
        if got:
            pool_legs += [x for x in got[0] if not x["research"] and ((sq._ts(x["start"]) or now) - now).total_seconds() >= sq.LEAD_S]
    pool_legs = _ranked(pool_legs)
    by_key = {l["key"]: l for l in pool_legs}
    types = {s: _line_types(srv, s) for s in {l["sport"] for l in legs}}
    slip = [{"id": l["id"], "sport": l["sport"], "book": l["book"], "row_id": l["row_id"], "side": l["side"], "line": l["line"], "player": l.get("player"),
             "player_key": l.get("player_key"), "team": l.get("team"), "event": l.get("event"), "stat": l.get("stat"), "period": l.get("period"),
             "line_type": types[l["sport"]].get((l["book"], l["row_id"])) or (l.get("line_type") if l.get("line_type") in _LINE_TYPES else "standard")}
            for l in legs]
    et = d.get("entry_types") if isinstance(d.get("entry_types"), dict) else {}
    lines = d.get("lines") if d.get("lines") is not None else sq.LINE_RULE_DEFAULT
    if lines not in sq.LINE_RULES:
        return h.send(400, {"error": "Choose supported generator settings."})
    out = {}
    for platform in dict.fromkeys(l["book"] for l in slip):
        mine = [x for x in slip if x["book"] == platform]
        t = et.get(platform)
        for m, r in zip(mine, sq.advise(mine, by_key, pool_legs, t if isinstance(t, str) and len(t) <= 16 else None, lines)):
            if m["sport"] in lc.SPORTS and lc.contained(m["sport"], m.get("stat")):     # lc-1: a held letter has no rating
                r = dict(r, notes=[{"code": "contained", "text": lc.banner(m["sport"])}] + list(r.get("notes") or []))
            out[m["id"]] = r
    return h.send(200, {"version": sq.VERSION, "legs": [dict(out[l["id"]], id=l["id"]) for l in legs]})


def post_recheck(h, srv, q, member, scratch, name, n):
    d = _read_json(h, srv, n)
    legs = _legs_of(d) if d else None
    idem = (d or {}).get("idem")
    if legs is None or not isinstance(idem, str) or not srv._IDEM_RE.match(idem):
        return h.send(400, {"error": "bad check"})
    if _limited(h, srv, "recheck", member, RECHECK_PER_MIN):
        return None
    try:
        res = run_check(srv, member, legs, d.get("entry_types") if isinstance(d.get("entry_types"), dict) else {}, allowed=access(h, srv, q))
    except LookupError:
        return h.send(503, {"error": "check_unavailable"})
    return h.send(200, public_check(res))


def post_book_line(h, srv, q, member, scratch, name, n):
    d=_read_json(h,srv,n)
    legs=_legs_of(d) if d else None
    if not srv.PP_OPEN_LINK:return h.send(404,{'error':'not found'})
    if not legs or len(legs)!=1 or legs[0]['book']!='underdog':
        return h.send(400,{'error':'Use a checked 2–6 pick entry for PrizePicks.'})
    if _limited(h,srv,'recheck',member,RECHECK_PER_MIN):return None
    l=legs[0]
    result=_check_one(srv,l,snapshot(srv) if l['sport']=='cs2' else None,{},datetime.now(timezone.utc),access(h,srv,q))
    if not result.get('ok'):return h.send(409,{'error':'This line changed, started or could not be verified. Refresh it first.'})
    url=srv.ud_row_link(l['row_id']) if l['sport']=='cs2' else _adapter(l['sport']).entry_link(result['_x'],l['side'])
    if not srv.pplink.ud_link_ok(url):return h.send(409,{'error':'The provider did not supply a verified link for this market.'})
    return h.send(200,{'url':url,'label':'Open in Underdog'})


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
            # lc-1: while the letter is held the member's receipt keeps no letter or stake (the pick log keeps both)
            "forecast_grade": "LEAN" if rec["scout"].get("letter_held") else fc.get("grade"),
            "forecast_units": 0.0 if rec["scout"].get("letter_held") else fc.get("units"), "l10": rec["history"]["l10"], "feed_at": rec["offer"]["seen_at"],
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
        res = run_check(srv, member, legs, {key: et if isinstance(et, str) else None}, allowed=access(h, srv, q))
    except LookupError:
        return h.send(503, {"error": "check_unavailable"})
    e = res["entries"].get(key) or {}
    if not e.get("trackable"):
        return h.send(409, dict(public_check(res), ok=False, error="refused"))
    now = wstore.iso()
    receipts = []
    for i, l in enumerate(legs):
        r = res["legs"][l["id"]]
        x = r["_x"]
        if l["sport"] == "cs2":
            fc = r["_fc"]
            f = {"sport": "cs2", "row_id": l["row_id"], "offer_rev": offer_rev(x), "side": l["side"], "line": x.get("line"), "line_type": line_type_of(x), "pick_id": fc["pick_id"], "forecast_id": fc["id"],
                 "match_slug": _slug_of(x), "match_id": x.get("match_id"), "match_key": event_id(x)[0], "nick": _nick(x), "prop": x.get("prop"), "scope": x.get("scope") or "12", "start_utc": x.get("start"),
                 "model_version": x.get("graded_model"), "payload": _payload(x, l, r, e)}
        elif l["sport"] == "lol":
            f = _adapter("lol").receipt_fields(x, l, r, e)
        else:
            f = _adapter(l["sport"]).receipt_fields(x, l, l["sport"], r, e)
        rid = "rc-" + hashlib.sha256(f"{member}|{key}|{l['row_id']}|{f['line']}|{l['side']}|{f['offer_rev']}".encode()).hexdigest()[:20]
        receipts.append(dict(f, receipt_id=rid, member=member, slip_id=slip_id, leg_no=i, tracked_at=now, platform=key, scratch=scratch))
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
    if n > 128 * 1024:
        h.rfile.read(n)
        return h.send(413, {"error": "saved batch over 131072 bytes"})
    d = _read_json(h, srv, n)
    if d is None or isinstance(d.get("rev"), bool) or not isinstance(d.get("rev"), int):
        return h.send(400, {"error": "bad state"})
    patch = {}
    for k, kind in (("slip", dict), ("prefs", dict), ("views", list), ("watch", list), ("studio", dict)):
        if k in d:
            if d[k] is not None and (not isinstance(d[k], kind) or not _shallow(d[k])):
                return h.send(400, {"error": f"bad {k}"})
            patch[k] = d[k]
    ordinary = {k: v for k, v in patch.items() if k != "studio"}
    if len(json.dumps(ordinary).encode()) > STATE_MAX:
        return h.send(413, {"error": "saved preferences or draft too large"})
    studio = patch.get("studio")
    if studio is not None:
        slips = studio.get("slips")
        if (studio.get("version") != 1 or not isinstance(slips, list) or len(slips) > 12
                or any(not isinstance(s, dict) or not isinstance(s.get("legs"), list)
                       or not 2 <= len(s["legs"]) <= 8 for s in slips)):
            return h.send(400, {"error": "bad studio batch"})
    if len(patch.get("watch") or ()) > WATCH_MAX:
        return h.send(400, {"error": f"a watchlist holds {WATCH_MAX} players"})
    if not patch:
        return h.send(400, {"error": "nothing to save"})
    if _limited(h, srv, "state", member, STATE_PER_MIN):
        return None
    out = store().put_state(member, d["rev"], patch)
    if not out["ok"]:
        return h.send(409, dict(out["state"], error="stale"))
    return h.send(200, {"rev": out["rev"]})


def get_genie(h, srv, q, member, scratch, name):
    import workspace_genie
    return h.send(200, {"quota": workspace_genie.quota(data_dir(), member), "suggestions": list(workspace_genie.SUGGESTIONS),
                        **({"intro": workspace_genie.PERSONA_INTRO} if workspace_genie.persona_on() else {})})


def post_genie(h, srv, q, member, scratch, name, n):
    """One payload-grounded question about one line (workspace_genie). Charged to the member's daily cap only when answered."""
    import workspace_genie as genie
    if n > 4096:
        h.rfile.read(n)
        return h.send(413, {"error": "request too large"})
    d = _read_json(h, srv, n)
    if not d:
        return h.send(400, {"error": "body must be a JSON object"})
    question, side = d.get("question"), d.get("side")
    if not isinstance(question, str) or not question.strip():
        return h.send(400, {"error": "question must be text"})
    if len(question) > genie.QUESTION_MAX:
        return h.send(413, {"error": "question_too_long", "max": genie.QUESTION_MAX})
    if side not in ("over", "under"):
        return h.send(400, {"error": "side must be over or under"})
    if _limited(h, srv, "genie", member, genie.PER_MIN):
        return None
    qq = dict(q, sport=d.get("sport") or "cs2", book=d.get("book"), id=d.get("id"))
    got = _line_shared(h, srv, qq)
    if got is None or got[0] != 200:
        return h.send(404 if got is None or got[0] == 404 else 503, {"error": "not found" if got is None or got[0] == 404 else "line_unavailable"})
    doc = json.loads(got[1])
    rec = doc.get("record") or {}
    rev = d.get("revision")
    if rev is not None and rev != (rec.get("offer") or {}).get("revision"):
        return h.send(409, {"error": "offer_changed"})
    sport = qq["sport"]
    try:                                        # site audit N9: every book's line and the letter's settled record, both read-only
        import workspace_books
        doc["extra_books"] = workspace_books.prop_lines(srv, sport, rec)
    except Exception:                           # noqa: BLE001 - the answer stands on the line's own payload
        doc["extra_books"] = []
    try:
        doc["tier_record"] = genie.tier_record(srv, sport, rec)
    except Exception:                           # noqa: BLE001
        doc["tier_record"] = None
    out = genie.answer(doc, question.strip(), side)
    if out["matched"]:
        try:
            ok, quota = genie.reserve(data_dir(), member)
        except genie.sqlite3.Error:
            return h.send(503, {"error": "busy"}, headers={"Retry-After": "2"})
        if not ok:
            return h.send(429, {"error": "daily_limit_reached", "quota": quota})
    else:
        quota = genie.quota(data_dir(), member)
    return h.send(200, dict(out, quota=quota, key=rec.get("key"), side=side))


def _record_for(h, srv, sport, book, rid):
    """The board's current display record for one line, or None (not on the board now, or not this member's sport)."""
    if _sport_q(h, srv, {"sport": sport}) is None:
        return None
    if sport == "cs2":
        snap = snapshot(srv)
        x = snap["index"].get((book, rid)) if snap else None
        return record_of(x) if x is not None else None
    mod = _adapter(sport)
    snap = mod.snapshot(srv) if sport == "lol" else mod.snapshot(srv, sport)
    ix = snap["index"].get((book, rid)) if snap else None
    return json.loads(ix["rec"]) if ix and ix.get("rec") else None


def post_report_problem(h, srv, q, member, scratch, name, n):
    """SCOUT_PARITY_REPORT: a member's "Report a problem" on one board line -> data_issues in the workspace store. Rate limited;
    the line must be on the board now. Nothing on the board changes: the owner reviews the rows."""
    if not board_parity.on("report"):              # not reached: handle() registers the route only while the flag is on
        h.rfile.read(n)
        return h.send(403, {"error": "forbidden"})
    if n > 4096:
        h.rfile.read(n)
        return h.send(413, {"error": "request too large"})
    f, err = board_parity.clean_report(_read_json(h, srv, n))
    if err:
        return h.send(400, {"error": err})
    if _limited(h, srv, "report", member, board_parity.REPORT_PER_MIN):
        return None
    rec = _record_for(h, srv, f["sport"], f["platform"], f["offer_id"])
    if rec is None:
        return h.send(404, {"error": "This line is no longer on the board."})
    out = board_parity.save_report(store(), member, scratch, f, rec, wstore.iso())
    return h.send(200, {"ok": True, "duplicate": out["duplicate"]})


GET = {"me": get_me, "board": get_board, "line": get_line, "mine": get_mine, "results": get_results, "state": get_state, "rules": get_rules,
       "diagnostics": get_diagnostics, "hits": get_hits, "stated": get_stated, "alert-delivery":get_alert_delivery,
       "market-tools":get_market_tools, "grade-review":get_grade_review, "generate": get_generate, "book-board": get_book_board,
       "book-lines": get_book_lines, "pickem-ev": get_pickem_ev, "market-edge": get_market_edge}
POST = {"recheck": post_recheck, "track": post_track, "state": post_state, "alert-delivery":post_alert_delivery,
        "book-line":post_book_line, "slip-advice": post_slip_advice}
FLAGGED_POST = {"report-problem": ("report", post_report_problem)}   # SCOUT_PARITY_REPORT only: off = the unknown-route answer


GENIE = {"GET": get_genie, "POST": post_genie}       # SCOUT_GENIE=1 only: off, "genie" is the unknown-route answer


def handle(h, srv, method, path, q, n=0, prefix=None):
    """Every workspace API route, under /preview/workspace/api/ or /api/upgrade/. The caller has already passed that prefix's gate."""
    name = path[len(prefix or srv.WORKSPACE_API):]
    fn = (GET if method == "GET" else POST).get(name)
    if fn is None and name == "genie" and os.environ.get("SCOUT_GENIE") == "1":
        fn = GENIE["GET" if method == "GET" else "POST"]
    if fn is None and method == "POST" and name in FLAGGED_POST and board_parity.on(FLAGGED_POST[name][0]):
        fn = FLAGGED_POST[name][1]
    if fn is None:
        if method == "GET":
            return h.send(404, {"error": "not found"})
        h.rfile.read(n)
        return h.send(403, {"error": "forbidden"})
    _ST["srv"] = srv
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
