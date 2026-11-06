"""Research workspace, NBA and NFL: the sports board, research, checks, settlement and results, read over HTTP from the sports API.

This module never imports sports (sports/tests/test_sports_isolation.py). Every read goes through server.py's one sports hook,
Handler._sports_route, with a stand-in for the request: in proxy and inprocess modes the hook answers the read itself; in the
default service mode it answers not_here and names the sports service, and the read goes there with a token minted by the same
hook. The token stays in this process: it is never logged and never reaches a page.

Letters, chances and leans come from the published sports row. Held rows stay held; released research letters carry no
staking recommendation. This adapter never upgrades the model's validation status or derives a letter from a probability.

For 700 members: one snapshot per league every BOARD_TTL s, single flight, revalidated by ETag so an unchanged board costs one
small request per part and no parse. A live pick check is shared per exact leg for CHECK_TTL s behind CHECK_SLOTS. The index keeps
each record as bytes: at most MAX_RECORDS records per league are held."""
import board_parity
import gzip
import hashlib
import workspace_h2h
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

LEAGUES = ("nba", "nfl")
PICKEM = ("underdog", "prizepicks")
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
BOOK_LABEL = {"fanduel": "FanDuel", "betrivers": "BetRivers", "kalshi": "Kalshi", "polymarket": "Polymarket", "pinnacle": "Pinnacle", "draftkings": "DraftKings",
              "betmgm": "BetMGM", "caesars": "Caesars", "bovada": "Bovada", "fanatics": "Fanatics", "espnbet": "ESPN BET", "hardrockbet": "Hard Rock", "bet365": "bet365"}
PERIOD = {"full_game": "Full game", "1h": "First half", "2h": "Second half", "1q": "First quarter"}
HOLD_LABEL = "Research estimate"
HOLD_DETAIL = "{LG} grades are held until forward results clear review."
SERVICE_USER = "workspace-reader"       # the hook's member for reads no one member owns: shared boards, settled results
BOARD_TTL, CHECK_TTL, PLAYER_TTL, RESULTS_TTL = 20, 5, 120, 300
CALL_TIMEOUT, CHECK_TIMEOUT = 5.0, 3.0
RESP_MAX = 16 * 1024 * 1024
MAX_RECORDS = 6000
STALE_S = 30 * 60                       # the board's own build older than this: lines are shown with their time, nothing is tracked
CHECK_SLOTS, PLAYER_SLOTS = 6, 3
SETTLE_LEGS = 8                         # sports/slips/validate MAX_LEGS: one settle read per this many legs
SETTLE_PAGE = 120                       # legs the store hands over per tier per pass; two tiers read at most SETTLE_CALLS
SETTLE_CALLS = 32
BREAK_S = 3.0                           # after the sports API fails, nothing asks it for this long: members get the answer at once
RESEARCH_MAX_GAMES = 30
_PID_RE = re.compile(r"^(nba|nfl):player:[a-z]+:[A-Za-z0-9._-]{1,40}$")
_REV_RE = re.compile(r":r\d+:[0-9a-f]+$")
_CHECK = threading.BoundedSemaphore(CHECK_SLOTS)
_PLAYER = threading.BoundedSemaphore(PLAYER_SLOTS)
_TOKEN = {"token": None, "exp": 0.0, "base": None}
_TOKEN_LOCK = threading.Lock()
_BREAK = {"until": 0.0}
_NOT_MODIFIED = object()


def _empty():
    return {"at": 0.0, "body": None, "index": {}, "props": {}, "feeds": {}, "etags": {}, "as_of": None, "gen": 0, "injuries": {}}


_SNAP = {lg: _empty() for lg in LEAGUES}


def reset():
    """Tests: forget every board and the token."""
    for lg in LEAGUES:
        _SNAP[lg] = _empty()
    _TOKEN.update(token=None, exp=0.0, base=None)
    _BREAK["until"] = 0.0


class Down(Exception):
    """The sports API did not answer usefully. Nobody is told a result: the caller keeps what it has and says so."""


# ---------------------------------------------------------------------------------------------------------------- the hook

class _Hook:
    """The parts of a request Handler._sports_route reads. allowed() is true because the workspace already passed its member."""
    headers = {}

    def __init__(self):
        self.out = None

    def member(self):
        return {"user": SERVICE_USER}

    def allowed(self):
        return True

    def authed(self, token):
        return False

    def send(self, code, body=b"", ctype="application/json", headers=None):
        self.out = (code, body, ctype, headers or {})


def _hook(srv, path, query=None):
    qs = urlencode(query or {})
    hk = _Hook()
    srv.Handler._sports_route(hk, urlparse(path + ("?" + qs if qs else "")), dict(query or {}))
    if hk.out is None:
        raise Down("no answer")
    return hk.out


def _json(body):
    if isinstance(body, (bytes, bytearray)):
        try:
            return json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
    return body


def _token(srv):
    now = time.time()
    if _TOKEN["token"] and _TOKEN["exp"] - 60 > now:
        return _TOKEN["token"], _TOKEN["base"]
    with _TOKEN_LOCK:
        if _TOKEN["token"] and _TOKEN["exp"] - 60 > now:
            return _TOKEN["token"], _TOKEN["base"]
        st, body, _, _ = _hook(srv, "/api/sports/token")
        doc = _json(body) or {}
        if st != 200 or not doc.get("token"):
            raise Down(f"token {st}")
        _TOKEN.update(token=doc["token"], exp=float(doc.get("exp") or 0), base=(doc.get("api_base") or "").rstrip("/"))
    return _TOKEN["token"], _TOKEN["base"]


def _service(srv, path, query, etag, timeout):
    tok, base = _token(srv)
    if not base:
        raise Down("no sports service")
    url = base + path + ("?" + urlencode(query) if query else "")
    head = {"Authorization": f"Bearer {tok}", "Accept": "application/json", "Accept-Encoding": "gzip"}
    if etag:
        head["If-None-Match"] = etag
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=timeout) as r:
            raw = r.read(RESP_MAX + 1)
            if len(raw) > RESP_MAX:
                raise Down("response over the cap")
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return 200, raw, r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None, etag
        if e.code in (401, 403):                # a token refused now: the next read mints a new one
            _TOKEN.update(token=None, exp=0.0)
        if e.code >= 500:
            _BREAK["until"] = time.time() + BREAK_S
        return e.code, e.read(65536), None
    except (urllib.error.URLError, OSError, ValueError, EOFError) as e:
        _BREAK["until"] = time.time() + BREAK_S
        raise Down(type(e).__name__) from None


def call(srv, path, query=None, etag=None, timeout=CALL_TIMEOUT):
    """One GET on the sports API. -> the parsed answer, or _NOT_MODIFIED with an etag. Anything but a 200 raises Down, naming it."""
    if time.time() < _BREAK["until"]:
        raise Down("waiting after a failure")
    try:
        st, body, _, hdrs = _hook(srv, path, query)
    except Down:
        raise
    except Exception as e:                      # the hook itself failing is that one sports read failing
        raise Down(type(e).__name__) from None
    got = None
    if st == 404 and (_json(body) or {}).get("error") == "not_here":
        st, body, got = _service(srv, path, query, etag, timeout)
        if st == 304:
            return _NOT_MODIFIED, etag
    if st != 200:
        raise Down(f"{path} {st}")
    doc = _json(body)
    if doc is None:
        raise Down("not json")
    return doc, got or hdrs.get("ETag")


def on(h, q, owner):
    """May this member see NBA and NFL here: the same answer the sports token route gives them. With the flags off, sports is
    never imported, and an owner's admin token is one more way in only when sports is switched on at all."""
    import os
    if h._sports_on("/api/sports/token", q):
        return True
    live = any(os.environ.get(k, "").strip().lower() in ("1", "true", "yes", "on", "owner") for k in ("SPORTS_UI", "SPORTS_API"))
    return bool(live and owner)


# ---------------------------------------------------------------------------------------------------------------- rows

def offer_id(record_id, platform):
    x = str(record_id or "")
    if x.startswith("offer:"):
        x = x[len("offer:"):]
    if x.startswith(platform + ":"):
        x = x[len(platform) + 1:]
    return _REV_RE.sub("", x)


def book_label(p):
    return LABEL.get(p) or BOOK_LABEL.get(str(p).split(":")[-1]) or str(p).split(":")[-1].title()


BOOK_SHORT = {"fanduel": "FD", "betrivers": "BR", "kalshi": "KA", "polymarket": "PM", "pinnacle": "PN", "draftkings": "DK", "betmgm": "MG", "caesars": "CZ", "bovada": "BV", "fanatics": "FN", "espnbet": "EB",
              "hardrockbet": "HR", "bet365": "36"}


def book_short(p):
    return BOOK_SHORT.get(str(p).split(":")[-1]) or book_label(p)[:2].upper()


def _kind(p):
    return "multiplier" if p == "underdog" else "modifier" if p == "prizepicks" else "decimal"


def _nn(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _market_kind(model):
    return model in ("mc-1", "sharp-1", "sharp-2") or str(model or "").startswith("mkt-")


def scout_at(q, fc):
    """The Scout view at this quote's line (web/sports/table.js scoutAt): its own forecast, else the line read; the calibrated
    chance on the quote wins. The forecast id is only set for a forecast made for this offer."""
    f = fc.get(q.get("f") or "")
    sc = None
    if f and not _market_kind(f.get("m")) and _nn(f.get("po")) and _nn(f.get("pu")):
        sc = {"po": f["po"], "pu": f["pu"], "pp": f.get("pp") or 0.0, "mu": f.get("mu"), "iv": f.get("iv"), "model": f.get("m"), "fid": q.get("f"), "at": f.get("at"), "md": f.get("md")}
    elif q.get("lr") and _nn(q["lr"].get("po")) and _nn(q["lr"].get("pu")):
        lr, s = q["lr"], fc.get(q["lr"].get("f") or "") or {}
        sc = {"po": lr["po"], "pu": lr["pu"], "pp": lr.get("pp") or 0.0, "mu": s.get("mu"), "iv": s.get("iv"), "model": s.get("m") or "sm-1", "fid": None, "at": s.get("at"), "md": s.get("md")}
    ca = q.get("ca")
    if sc and ca and _nn(ca.get("po")) and _nn(ca.get("pu")):
        sc.update(po=ca["po"], pu=ca["pu"], pp=ca["pp"] if _nn(ca.get("pp")) else sc["pp"])
    return sc


def market_at(mk, q):
    """The market chance at this offer's own line, never another line's number (web/sports/table.js marketAt)."""
    if not mk:
        return None
    if q.get("r") in (mk.get("at") or {}):
        c = mk["at"][q["r"]]
    elif mk.get("c") and mk.get("l") == q.get("l"):
        c = mk["c"]
    else:
        return None
    return c if c and (c.get("n") or 0) > 0 and _nn(c.get("po")) and _nn(c.get("pu")) else None


def lean_of(sc, line):
    if not sc:
        return None
    if sc["po"] > 0.5 and (sc["mu"] is None or sc["mu"] > line):
        return "over"
    if sc["pu"] > 0.5 and (sc["mu"] is None or sc["mu"] < line):
        return "under"
    return None


def lean_and_hold(m, q, sc, mkc):
    """(lean, held text): what the sports board shows on this quote. A held row has no lean; cf-2's server signal decides the rest.
    A contained market's text is the sports page's own label, so the page here says the fact in HOLD_DETAIL instead."""
    if not sc:
        return None, None
    x = q.get("x") or {}
    if x.get("b"):
        return None, x.get("t") if x.get("b") != "gate" else ""
    if m.get("nv"):
        return None, ""
    if x.get("v") == "cf-2":
        return x.get("ln") or None, None
    lean = lean_of(sc, q.get("l"))
    if lean and mkc:
        d = (sc["po"] - mkc["po"]) if lean == "over" else (sc["pu"] - mkc["pu"])
        if d <= 1e-9:
            lean = None
    return lean, None


def _tally(a):
    return {"over": a[0], "under": a[1], "push": a[2], "n": a[3]} if a and a[3] else None


def _board_columns(v, line, opp):
    """SCOUT_BOARD_H2H: the sheet's H2H and Season counts at this line (sports/board_view.h2h_season_lines), display only."""
    if line is None:
        return {"h2h": None, "season": None}
    h, s = _tally((v.get("linesH2h") or {}).get(line_key(line))), _tally((v.get("linesSeason") or {}).get(line_key(line)))
    return {"h2h": dict(h, opponent=opp) if h else None, "season": dict(s, season=str(v.get("season"))) if s and v.get("season") is not None else None}


def line_key(x):
    x = float(x)
    return str(int(round(x))) if abs(x - round(x)) < 1e-9 else str(round(x, 1))


def _team(v):
    return (v or {}).get("abbr") or (v or {}).get("name") or ""


def _hash(*fields):
    return hashlib.sha256(json.dumps(fields, default=str).encode()).hexdigest()[:16]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def offer_rev(q):
    return "d-" + _hash(q.get("r"), q.get("l"), q.get("v"), q.get("o"), q.get("m"), q.get("d"))


def read_rev(lean, sc, hold, grade=None):
    fields = (lean, None if not sc else sc["po"], None if not sc else sc["pu"], hold)
    return "r-" + _hash(*(fields + (grade,) if grade is not None else fields))


def _side_prices(q, i):
    offered = bool((q.get("o") or [0, 0])[i])
    mult = (q.get("m") or [None, None])[i]
    dec = (q.get("d") or [None, None])[i]
    return {"offered": offered, "payout": dec if dec is not None else mult, "price": dec}


def record_of(lg, doc, m, q, view):
    """One display record from one quote of one market row: the row's values only, nothing recomputed."""
    ev = (doc.get("events") or {}).get(m.get("e")) or {}
    home, away = _team(ev.get("home")), _team(ev.get("away"))
    team = m.get("tm") or ""
    tid = m.get("tid")
    opp = away if tid and tid == (ev.get("home") or {}).get("team_id") else home if tid and tid == (ev.get("away") or {}).get("team_id") else ""
    scope = (m.get("k") or "").rsplit("|", 1)[-1] or "full_game"
    p = q.get("p")
    fc = doc.get("fc") or {}
    sc = scout_at(q, fc)
    mkc = market_at(m.get("mk"), q)
    lean, held = lean_and_hold(m, q, sc, mkc)
    held = None if held is None else held or HOLD_DETAIL.format(LG=lg.upper())
    # Letters are published by Sports, never reconstructed from probabilities here.
    # The owner's containment switch can only hold research letters (sports/containment.py ct-1a);
    # release comes only from the daily verdict. Stakes stay 0. Sanity/market holds still win.
    signal = q.get("x") or {}
    grade = signal.get("gr") if held is None and lean and signal.get("v") == "cf-2" else None
    if grade not in ("A", "B", "C", "Lean"):
        grade = None
    if held is not None:
        reason = held
    elif not sc:
        reason = "A matching forecast is not available for this line yet."
    elif not lean and mkc and lean_of(sc, q.get("l")):
        reason = "The market already prices the model's preferred side at least as highly."
    elif not lean:
        reason = "The forecast does not give a clear direction at this line."
    else:
        reason = "A published grade is not available for this forecast yet."
    hold = None if grade else {"label": HOLD_LABEL, "reason": reason}
    line = q.get("l")
    v = view.get(m.get("k")) or {}
    a = (v.get("lines") or {}).get(line_key(line)) if line is not None else None
    rid = "s-" + hashlib.sha256(f"{p}|{offer_id(q.get('r'), p)}".encode()).hexdigest()[:24]
    pkey = _norm(m.get("pid"))
    period_label = PERIOD.get(scope, scope.replace("_", " ").title())
    side_p = None if not sc or not lean else (sc["po"] if lean == "over" else sc["pu"])
    rec = {"sport": lg, "key": ".".join(str(k) for k in (lg, m.get("e"), pkey, m.get("st"), scope, line)),
           "event": {"id": m.get("e"), "id_kind": "canonical", "label": f"{away} at {home}" if away and home else f"{team or lg.upper()} game", "teams": [away, home], "competition": lg.upper(),
                     "best_of": None, "start_utc": ev.get("start_utc"), "lock_utc": ev.get("start_utc"), "status": "started" if ev.get("started") or ev.get("locked") else "scheduled"},
           "player": {"id": m.get("pid"), "key": pkey, "name": m.get("n") or "", "team": team, "opponent": opp, "role": m.get("pos") or ""},
           "market": {"stat": m.get("st"), "stat_label": (doc.get("stats") or {}).get(m.get("st"), m.get("st")), "period": scope, "period_label": period_label, "line_type": "standard"},
           "offer": {"platform": p, "platform_label": book_label(p), "offer_id": rid, "revision": offer_rev(q), "revision_kind": "exact", "line": line, "seen_at": q.get("t"),
                     "over": _side_prices(q, 0), "under": _side_prices(q, 1), "moved_from": None, "moved_at": None},
           "scout": {"side": lean, "read_rev": read_rev(lean, sc, held, grade), "p_side": side_p, "p_over": sc["po"] if sc else None, "p_under": sc["pu"] if sc else None, "p_push": sc["pp"] if sc else None,
                     "projection": sc.get("mu") if sc else None, "range80": sc.get("iv") if sc else None, "grade": grade, "units": 0 if grade else None, "stake_reason": "Research grade; stakes remain 0." if grade else None,
                     "hold": hold, "model_version": sc.get("model") if sc else None, "forecast_at": sc.get("at") if sc else None,
                     "validation": {"kind": "research", "label": "Research grade" if grade else HOLD_LABEL,
                                    "detail": "Research grade — not forward-validated. Stakes remain 0." if grade else reason}},
           "history": {"l5": _tally(a[4:8] if a else None), "l10": _tally(a[:4] if a else None), "l20": _tally((v.get("lines20") or {}).get(line_key(line))) if line is not None else None, "avg": None, "recent": None,
                       **(_board_columns(v, line, opp) if workspace_h2h.board_enabled() else {})},
           "market_chance": {"p_over": mkc["po"], "p_under": mkc["pu"], "sources": mkc.get("n")} if mkc else None,
           "state": {"kind": "ok", "reason": None}}
    x = {"r": q.get("r"), "p": p, "l": line, "o": q.get("o"), "m": q.get("m"), "g": q.get("g"), "ud": q.get("ud"), "fid": sc.get("fid") if sc else None, "po": sc["po"] if sc else None,
         "pu": sc["pu"] if sc else None, "pp": sc["pp"] if sc else None, "mu": sc.get("mu") if sc else None, "model": sc.get("model") if sc else None, "at": sc.get("at") if sc else None,
         "lean": lean, "start": ev.get("start_utc"), "started": bool(ev.get("started") or ev.get("locked")), "e": m.get("e"), "pid": m.get("pid"), "name": m.get("n") or "", "team": team, "st": m.get("st"),
         "scope": scope, "rev": rec["offer"]["revision"], "read_rev": rec["scout"]["read_rev"], "rid": rid, "pkey": pkey, "variant":q.get('v')}
    return rec, x


def _price(v):
    """A book's side as it reads: a decimal price, or an exchange's bid and ask."""
    return f"{v[0]} / {v[1]}" if isinstance(v, (list, tuple)) and len(v) == 2 else v


def _book_rows(mk, line):
    """Every book of this market, split by whether it prices this exact line: a different line is never lined up beside it."""
    same, other = [], []
    for b in (mk or {}).get("bk") or []:
        row = {"platform": b.get("p"), "platform_label": book_label(b.get("p")), "line": b.get("l"), "kind": b.get("kind"), "chance": b.get("c"), "over": _price(b.get("over")), "under": _price(b.get("under")),
               "seen_at": b.get("t"), "in_consensus": bool(b.get("in"))}
        (same if b.get("l") == line else other).append(row)
    return same, other


# ---------------------------------------------------------------------------------------------------------------- snapshots

def _feeds(as_of, platforms):
    state, at = "ok", as_of
    t = _ts(as_of)
    if t is None or (datetime.now(timezone.utc) - t).total_seconds() > STALE_S:
        state = "stale"
    return {p: {"state": state, "at": at, "error": None} for p in platforms}


def _ts(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _part(srv, lg, part, etag):
    try:
        return call(srv, "/api/sports/board", {"league": lg, "part": part}, etag=etag)
    except Down:
        if part == "primary":
            raise
        return {}, None                         # secondary markets or hit rates missing: the primary board still stands


def _build(srv, lg):
    s = _SNAP[lg]
    aging = any(f["state"] == "ok" for f in s["feeds"].values()) and all(f["state"] != "ok" for f in _feeds(s["as_of"], list(s["feeds"])).values())
    head, tag = call(srv, "/api/sports/board", {"league": lg, "part": "primary"}, etag=None if aging else s["etags"].get("primary"))    # an unchanged file that aged out is rebuilt to say so
    if head is _NOT_MODIFIED and s["body"] is not None:
        v_doc, v_tag = _part(srv, lg, "view", s["etags"].get("view"))
        if v_doc is _NOT_MODIFIED or not v_doc:
            s["at"] = time.time()
            return 200, s["body"]
        head, tag = _part(srv, lg, "primary", None)
    else:
        v_doc, v_tag = _part(srv, lg, "view", None)
    if head is _NOT_MODIFIED:
        head, tag = _part(srv, lg, "primary", None)
    more, m_tag = _part(srv, lg, "more", None)
    view = (v_doc or {}).get("rows") or {}
    records, index, props, platforms = [], {}, {}, {}
    for doc in (head, more or {}):
        for m in doc.get("markets") or []:
            for q in m.get("q") or []:
                if len(records) >= MAX_RECORDS or q.get("l") is None:
                    continue
                d = doc if doc.get("events") else head
                rec, x = record_of(lg, d, m, q, view)
                if not rec["event"]["start_utc"]:
                    continue
                board_parity.attach(rec)                # 4.16 parity numbers, only while a SCOUT_PARITY_* row flag is on
                records.append(rec)
                x["rec"] = json.dumps(rec, default=str, separators=(",", ":")).encode()
                x["books"] = _book_rows(m.get("mk"), q.get("l"))
                index[(q["p"], x["rid"])] = x
                props.setdefault((x["e"], x["pkey"], x["st"], x["scope"]), []).append((q["p"], x["rid"]))
                platforms.setdefault(q["p"], {"label": book_label(q["p"]), "short": book_short(q["p"]), "slip": q["p"] in PICKEM, "kind": _kind(q["p"]),
                                              **({"types": [["standard", "Standard"], ["flex", "Flex"]] if q["p"] == "underdog" else [["power", "Power"], ["flex", "Flex"]]} if q["p"] in PICKEM else {})})
    as_of = head.get("as_of") or head.get("built_at")
    feeds = _feeds(as_of, [p for p in PICKEM if p in platforms])
    body = json.dumps({"sport": lg, "version": head.get("version"), "board_at": as_of, "feeds": feeds, "platforms": platforms, "records": records}, default=str, separators=(",", ":")).encode()
    if s["body"] == body:
        body = s["body"]
    _SNAP[lg] = dict(s, at=time.time(), body=body, index=index, props=props, feeds=feeds, as_of=as_of, gen=s["gen"] + 1, injuries=head.get("injuries") or {},
                     etags={"primary": tag, "view": v_tag, "more": m_tag})
    return 200, body


def snapshot(srv, lg):
    """The shared snapshot of one league, or None when no board has ever been read. A failed read keeps the last good copy."""
    try:
        srv.shared(("ws_sports", lg), lambda: _build(srv, lg), ttl=BOARD_TTL)
    except Exception:
        pass
    return _SNAP[lg] if _SNAP[lg]["body"] is not None else None


# ---------------------------------------------------------------------------------------------------------------- checks

REFUSAL = {"gone": (1, "gone", "This line is no longer on the board."), "changed": (1, "moved", "This offer changed."), "not_open": (1, "side", None), "side_not_offered": (1, "side", None),
           "not_gradable": (1, "not_gradable", "Scout cannot settle this line."), "started": (0, "locked", "Game started."), "start_unknown": (0, "start_unknown", "No start time on the feed for this line."),
           "stale": (1, "stale", "This line has not been re-read recently."), "forecast_mismatch": (2, "forecast_mismatch", "The saved forecast does not match this line."),
           "display_mismatch": (2, "display_mismatch", "The chance on your slip is out of date."), "not_pickem": (1, "gone", "This line is not on a pick'em board.")}


def live_check(srv, lg, x, side):
    """sports/picks.py check on this exact leg, right now. Shared per leg: 700 members on one line cost one read every CHECK_TTL s."""
    key = ("ws_sp_chk", lg, x["r"], side, x["fid"], x["po"], x["pu"], x["pp"])

    def compute():
        if not _CHECK.acquire(timeout=1.0):
            return 200, {"state": "retry"}, 1
        try:
            item = {"platform": x["p"], "record_id": x["r"], "side": side, "forecast_id": x["fid"], "surface": "board",
                    "shown": {"p_over": x["po"], "p_under": x["pu"], "p_push": x["pp"]} if x["fid"] else None}
            doc, _ = call(srv, "/api/sports/picks/check", {"picks": json.dumps([item], separators=(",", ":"))}, timeout=CHECK_TIMEOUT)
            res = (doc.get("results") or [None])[0]
            return (200, res, CHECK_TTL) if res else (200, {"state": "retry"}, 1)
        except Down:
            return 200, {"state": "retry"}, 1
        finally:
            _CHECK.release()
    return srv.shared(key, compute, ttl=CHECK_TTL)[1]


def check_leg(srv, leg, lg, now):
    """One NBA or NFL leg: the snapshot first, then the sports check itself. -> {ok, problems, current} plus what the entry needs."""
    from workspace_api import SIDE_WORD, _n, _pr, DISPLAY_MAX_GAP
    snap = snapshot(srv, lg)
    book, side = leg["book"], leg["side"]
    if snap is None:
        return {"ok": False, "problems": [_pr(1, "retry", "Scout could not check this line just now.", retry=True)], "current": None}
    x = snap["index"].get((book, leg["row_id"]))
    label = LABEL.get(book, book)
    if x is None:
        return {"ok": False, "problems": [_pr(1, "gone", "This line is no longer on the board.")], "current": None}
    if book not in PICKEM:
        return {"ok": False, "problems": [_pr(1, "gone", f"{book_label(book)} is shown for comparison. Slips cover PrizePicks and Underdog.")], "current": None}
    problems = []
    if (snap["feeds"].get(book) or {}).get("state") != "ok" or now.timestamp() - snap["at"] > 120:
        problems.append(_pr(1, "board_failed", f"Scout could not read the {lg.upper()} board just now.", retry=True))
    lock = _ts(x["start"])
    if lock is None:
        problems.append(_pr(0, "start_unknown", "No start time on the feed for this line."))
    elif lock <= now or x["started"]:
        problems.append(_pr(0, "locked", "Game started."))
    if x["rev"] != leg.get("rev") or float(leg["line"]) != float(x["l"]):
        moved = float(leg["line"]) != float(x["l"])
        problems.append(_pr(1, "moved", f"Line moved from {_n(leg['line'])} to {_n(x['l'])}." if moved else f"This offer changed on {label}.", swap=True))
    if not (x["o"] or [0, 0])[0 if side == "over" else 1]:
        problems.append(_pr(1, "side", f"{SIDE_WORD[side]} is not offered on {label}."))
    if not x["g"]:
        problems.append(_pr(1, "not_gradable", "Scout cannot settle this line."))
    if not x["fid"]:
        problems.append(_pr(2, "no_forecast", "No saved forecast for this line, so it cannot be tracked."))
    else:
        shown_p, now_p = leg.get("shown_p"), x["po"] if side == "over" else x["pu"]
        if leg.get("read_rev") != x["read_rev"] or shown_p is None or abs(shown_p - now_p) > DISPLAY_MAX_GAP:
            problems.append(_pr(2, "display_mismatch", f"Scout's read changed: now {SIDE_WORD[side]} {now_p * 100:.1f}%.", swap=True))
    if not problems:
        res = live_check(srv, lg, x, side)
        if res.get("state") == "retry":
            problems.append(_pr(1, "retry", "Scout could not check this line just now.", retry=True))
        elif not res.get("ok"):
            chk, code, text = REFUSAL.get(res.get("code"), (1, "gone", None))
            problems.append(_pr(chk, code, text or res.get("text") or "This line cannot be tracked.", **({"swap": True} if code in ("moved", "display_mismatch", "forecast_mismatch") else {}),
                                **({"retry": True} if code == "stale" else {})))
    offered = (x["m"] or [None, None])[0 if side == "over" else 1]
    return {"ok": not problems, "problems": problems,
            "current": {"row_id": x["rid"], "line": x["l"], "rev": x["rev"], "read_rev": x["read_rev"], "scout_side": x["lean"], "p_side": None if x["po"] is None else (x["po"] if side == "over" else x["pu"]),
                        "forecast_id": x["fid"], "forecast_at": x["at"], "model_version": x["model"], "start_utc": x["start"], "lock_utc": x["start"]},
            "_x": x, "_ident": {"nick": x["pkey"], "prop": x["st"], "scope": x["scope"], "match_slug": x["e"], "match_key": x["e"], "platform": book, "line": x["l"], "side": side},
            "_rule": {"sport": lg, "book": book, "team": f"{lg}:{x['team']}" if x["team"] else "", "nick": x["name"], "side": side, "event": x["e"]},
            "_ev": {"name": x["name"], "sport": lg, "match": x["e"], "line": x["l"], "multiplier": offered, "on_side": side == x["lean"],
                    "p": None if x["po"] is None else (x["po"] if side == "over" else x["pu"]), "tag": x["model"], "prop": x["st"], "scope": x["scope"]}}


def payload(x, leg, res, e):
    """Freeze the displayed research letter only on its published side; never release stakes."""
    rec = json.loads(x["rec"])
    p_shown = x["po"] if leg["side"] == "over" else x["pu"]
    grade = rec["scout"].get("grade") if leg["side"] == x["lean"] else None
    return {"player": rec["player"]["name"], "team": rec["player"]["team"], "opponent": rec["player"]["opponent"], "matchup": rec["event"]["label"], "competition": rec["event"]["competition"],
            "stat_label": rec["market"]["stat_label"], "period_label": rec["market"]["period_label"], "platform_label": rec["offer"]["platform_label"], "scout_side": x["lean"], "on_side": leg["side"] == x["lean"],
            "displayed_p": p_shown, "grade": grade, "units": 0 if grade else None, "stake_reason": "Research grade; stakes remain 0." if grade else None,
            "research": True, "hold": (rec["scout"].get("hold") or {}).get("label"), "projection": x["mu"], "forecast_p": p_shown,
            "forecast_at": x["at"], "forecast_grade": grade, "forecast_units": 0 if grade else None, "l10": rec["history"]["l10"], "feed_at": rec["offer"]["seen_at"], "rules_version": e.get("rules_version"), "entry_type": e.get("entry_type")}


def receipt_fields(x, leg, lg, res, e):
    side, oid = leg["side"], offer_id(x["r"], x["p"])
    mult = (x["m"] or [None, None])[0 if side == "over" else 1]
    return {"sport": lg, "row_id": x["rid"], "offer_rev": x["rev"], "side": side, "line": x["l"], "line_type": "standard", "pick_id": None, "forecast_id": x["fid"], "match_slug": x["e"], "match_id": x["e"],
            "match_key": x["e"], "nick": x["pkey"], "prop": x["st"], "scope": x["scope"], "start_utc": x["start"], "model_version": x["model"], "payload": payload(x, leg, res, e),
            "settle_key": f"{lg}|{x['p']}|{oid}|{side}|{x['l']}", "settle_ref": {"league": lg, "platform": x["p"], "offer_id": oid, "side": side, "line": x["l"], "team": x["team"], "multiplier": mult}}


def entry_link(x, side):
    return x.get("ud")


def entry_projection(x, side):
    if x.get('p')!='prizepicks':return None
    return {'book':'prizepicks','projection_id':offer_id(x.get('r'),'prizepicks'),'side':side,
            'line':x.get('l'),'odds_type':x.get('variant')}


# ---------------------------------------------------------------------------------------------------------------- settlement

RESULT = {"win": "hit", "loss": "miss", "push": "push", "void": "void"}


def settle(srv, lg, rows, now=None):
    """sports/slips/settle's leg results for these keys: each leg at its own line from the canonical settlement. Asked only after
    the start, at most SETTLE_CALLS reads a pass; the rest wait their turn. Down raises, and every result stays as it was."""
    now = now or datetime.now(timezone.utc)
    due = {}
    for w in rows:
        t = _ts(w.get("start_utc"))
        if t is not None and t <= now:
            due.setdefault(w["ref"]["platform"], []).append(w)
    out, calls = {}, 0
    for platform, legs in sorted(due.items()):
        for i in range(0, len(legs), SETTLE_LEGS):
            if calls >= SETTLE_CALLS:
                return out
            calls += 1
            part = legs[i:i + SETTLE_LEGS]
            slip = {"league": lg, "platform": platform, "legs": [{"platform": platform, "offer_id": w["ref"]["offer_id"], "side": w["ref"]["side"], "line": w["ref"]["line"], "team": w["ref"].get("team"),
                                                                  "multiplier": w["ref"].get("multiplier")} for w in part]}
            doc, _ = call(srv, "/api/sports/slips/settle", {"slip": json.dumps(slip, separators=(",", ":"))})
            for w, lr in zip(part, doc.get("leg_results") or []):
                res = RESULT.get(lr.get("status"))
                out[w["settle_key"]] = {"result": res, "actual": lr.get("value"), "source": "espn", "settled_at": None, "final": res is not None,
                                        "mark": _hash(lr.get("status"), lr.get("value"), lr.get("settlement_id"), lr.get("revision"))}
    return out


# ---------------------------------------------------------------------------------------------------------------- research

def _player(srv, lg, pid, path):
    """One player's file, shared across members per player. No slot within a second: the page shows what it already holds."""
    def compute():
        if not _PLAYER.acquire(timeout=1.0):
            return 200, None, 1
        try:
            doc, _ = call(srv, path, {"league": lg, "id": pid})
            return 200, trim(doc)
        except Down:
            return 200, None, 5
        finally:
            _PLAYER.release()
    return srv.shared(("ws_sp_file", path, lg, pid), compute, ttl=PLAYER_TTL)[1]


def trim(doc):
    """Only what research reads, so a cached player costs little."""
    if "games" in doc:
        return {"games": [{"start_utc": g.get("start_utc"), "opponent": _team(g.get("opponent")), "home": g.get("home"), "played": g.get("participation") == "played", "values": g.get("values") or {},
                           "season": g.get("season")} for g in (doc.get("games") or [])[:RESEARCH_MAX_GAMES]]}
    f = doc.get("factors") or {}
    return {"position": doc.get("position"), "status": doc.get("status"), "injury": doc.get("injury"), "next_event": doc.get("next_event"), "context": f.get("context") or [], "stats": f.get("stats") or [],
            "engine": f.get("engine")}


def series_of(hist, stat):
    """Played games with a value for this stat, newest first: the same games the board's hit rates count."""
    out = []
    for g in (hist or {}).get("games") or []:
        v = (g.get("values") or {}).get(stat)
        if g.get("played") and v is not None:
            out.append({"date": str(g.get("start_utc") or "")[:10], "opponent": g.get("opponent") or "", "value": v})
    return out


def context_of(x, player, lg):
    rows = []

    def add(label, value, group="Matchup"):
        if value not in (None, "", []):
            rows.append({"label": label, "value": value, "group": group})
    add("Position", (player or {}).get("position"))
    rec = json.loads(x["rec"])
    add("Opponent", rec["player"]["opponent"])
    inj = (player or {}).get("injury")
    if inj:
        add("Injury report", " · ".join(str(v) for v in (inj.get("status"), inj.get("detail")) if v))
    for c in (player or {}).get("context") or []:
        if c.get("key") == "teammates_out":
            add(c.get("label"), c.get("value"))
            for i in (c.get("items") or [])[:6]:
                add(i.get("player"), i.get("status"))
        elif isinstance(c.get("value"), bool):
            add(c.get("label"), "Yes" if c["value"] else "No")
        else:
            add(c.get("label"), c.get("value"))
    for s in (player or {}).get("stats") or []:
        for w in s.get("windows") or []:
            if w.get("window") and w.get("mean") is not None:
                add(f"{s.get('label')}, {w['window']}", w["mean"], "Averages")
    for r in ((player or {}).get("engine") or {}).get("rows") or []:
        if r.get("label") and r.get("value") is not None:
            add(r.get("label"), r.get("value"), "Model inputs")
    return {"rows": rows[:200]}


def line_doc(srv, lg, book, rid):
    snap = _SNAP[lg]
    x = snap["index"].get((book, rid))
    if x is None:
        return 404, {"error": "not found"}
    rec = json.loads(x["rec"])
    pid = x["pid"] if _PID_RE.match(str(x["pid"] or "")) else None
    hist = _player(srv, lg, pid, "/api/sports/history") if pid else None
    player = _player(srv, lg, pid, "/api/sports/player") if pid else None
    series = series_of(hist, x["st"]) if x["scope"] == "full_game" else None
    books = []
    for bk, i in snap["props"].get((x["e"], x["pkey"], x["st"], x["scope"]), []):
        o = snap["index"].get((bk, i))
        if o is not None:
            r = json.loads(o["rec"])
            books.append({"platform": bk, "platform_label": r["offer"]["platform_label"], "row_id": i, "key": r["key"], "line": o["l"], "line_type": "standard", "over": r["offer"]["over"], "under": r["offer"]["under"],
                          "seen_at": r["offer"]["seen_at"], "scout_side": o["lean"], "p_side": r["scout"]["p_side"], "grade": r["scout"]["grade"]})
    same, other = x.get("books") or ([], [])
    mk = rec.get("market_chance")
    forecast = {"id": x["fid"], "logged_at": x["at"], "model_version": x["model"], "p_saved": None} if x["fid"] else None
    tally = rec["history"]["l10"]
    avg = round(sum(s["value"] for s in series[:10]) / len(series[:10]), 1) if series else None
    return 200, {"record": rec, "forecast": forecast, "history": {"tally": tally, "avg": avg, "series": series[:20] if series else None}, "books": books, "market_books": {"same": same, "other": other},
                 "h2h": workspace_h2h.h2h(series, rec["player"]["opponent"], x["l"]) if series else None,
                 "context": context_of(x, player, lg),
                 "market": {"available": True, "p_over": mk["p_over"], "p_under": mk["p_under"], "source": f"{mk['sources']} market sources at this line"} if mk else {"available": False, "reason": "No market price at this exact line."}}


# ---------------------------------------------------------------------------------------------------------------- results

def _public(srv):
    def compute():
        try:
            doc, _ = call(srv, "/api/sports/results/public")
            return 200, doc
        except Down as e:
            return 503, {"error": str(e)}
    return srv.shared(("ws_sp_public",), compute, ttl=RESULTS_TTL)


def _line(name, leans, stated=None, games=None):
    n = leans.get("n") or 0
    return {"name": name, "picks": n, "wins": leans.get("wins"), "losses": leans.get("losses"), "pushes": leans.get("pushes"), "observed": leans.get("hit_rate"), "printed": stated, "matches": games}


def _letters(d):
    """The research grades the page's own Results show: none while the league is contained or the grades are withheld. The public file
    keeps no stated chance per letter, so that cell stays empty."""
    grades = d.get("grades") or {}
    if (d.get("model_gate") or {}).get("contained") or grades.get("withheld"):
        return []
    return [{"name": g.get("grade"), "picks": g.get("n") or 0, "wins": g.get("wins"), "losses": g.get("losses"), "pushes": g.get("pushes"), "observed": g.get("hit_rate"), "printed": None}
            for g in grades.get("rows") or [] if g.get("n")]


def results(srv, lg):
    """The sports Results file for one league. Not ready or down raises Down: the page says so, and never shows a zero for it."""
    st, doc = _public(srv)
    if st != 200:
        raise Down("results")
    d = (doc.get("leagues") or {}).get(lg)
    if not d:
        raise Down("no league")
    leans = d.get("leans") or {}
    wl = [b for b in d.get("bands") or [] if b.get("stated") is not None and b.get("n")]
    n = sum((b.get("wins") or 0) + (b.get("losses") or 0) for b in wl)
    stated = round(sum(b["stated"] * ((b.get("wins") or 0) + (b.get("losses") or 0)) for b in wl) / n, 4) if n else None
    by_band = [{"name": b.get("band"), "picks": b.get("n") or 0, "wins": b.get("wins"), "losses": b.get("losses"), "pushes": b.get("pushes"), "printed": b.get("stated"), "observed": b.get("hit_rate")} for b in d.get("bands") or [] if b.get("n")]
    by_stat = [_line(k, v.get("leans") or {}, None, v.get("games")) for k, v in sorted((d.get("stats") or {}).items()) if (v.get("leans") or {}).get("n")]
    hh = next((h for h in (d.get("head_to_head") or {}).get("rows") or [] if h.get("stat") == "all"), None)
    return {"sport": lg, "label": f"{lg.upper()} research record", "as_of": doc.get("as_of"), 'calendar':d.get('calendar'),
            "population": "Saved forward Scout forecasts with a lean, each scored at its own line from the final box score. These are research estimates, with grades held.",
            "rule": "Over when its chance of Over is above 50% and its projection is above the line, Under mirrored. Hit rate is wins over wins plus losses. Pushes stay apart, voids and unresolved props stay out.",
            "totals": {"picks": leans.get("n") or 0, "wins": leans.get("wins"), "losses": leans.get("losses"), "pushes": leans.get("pushes"), "observed": leans.get("hit_rate"), "ci": leans.get("hit_rate_ci95"),
                       "printed": stated, "matches": d.get("games"), "void": d.get("void"), "match_word": "games"},
            "groups": {"chance": by_band, "stat": by_stat}, "letters": _letters(d), "gate": (d.get("gate") or {}).get("text"),
            "market": {"pairs": hh.get("pairs"), "games": hh.get("games"), "scout_brier": hh.get("scout_brier"), "market_brier": hh.get("market_brier"), "market": hh.get("market")} if hh else None}
