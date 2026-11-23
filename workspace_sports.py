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
import card_grade_inputs
import gzip
import hashlib
import workspace_h2h
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

LEAGUES = ("nba", "nfl")
PICKEM = ("underdog", "prizepicks")
LABEL = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
BOOK_LABEL = {"fanduel": "FanDuel", "betrivers": "BetRivers", "kalshi": "Kalshi", "polymarket": "Polymarket", "pinnacle": "Pinnacle", "draftkings": "DraftKings",
              "betmgm": "BetMGM", "caesars": "Caesars", "bovada": "Bovada", "unibet_au": "Unibet (AU)", "pointsbet_au": "PointsBet (AU)", "fanatics": "Fanatics", "espnbet": "ESPN BET", "hardrockbet": "Hard Rock", "bet365": "bet365"}
PERIOD = {"full_game": "Full game", "1h": "First half", "2h": "Second half", "1q": "First quarter",
          "first_half": "First half", "second_half": "Second half", "q1": "First quarter", "q2": "Second quarter",
          "q3": "Third quarter", "q4": "Fourth quarter"}        # sports scopes (contracts SCOPES) + the older short keys
ORDINARY_V = {None, "", "standard", "balanced", "main", "ladder_rung"}


def line_type_of(q):
    """The line's real type for members (audit 2026-11-05 S1/S2): 'standard' only for an ordinary line that lists
    both sides; an Underdog higher-only line is 'over only', never Standard; goblin / demon / alternate as named."""
    v, o = q.get("v"), q.get("o") or [1, 1]
    if v == "ladder_rung" and q.get("p") not in PICKEM:
        return "alternate"
    if v == "one_sided" or (v in ORDINARY_V and q.get("p") == "underdog" and len(o) == 2 and o[0] != o[1]):
        return "over only" if o and o[0] else "under only"
    return "standard" if v in ORDINARY_V else str(v).replace("_", " ")


def one_sided_label(q):
    """Owner 2026-11-06 (sports cap-2): "Over only" / "Under only" for a STORED one-sided line (variant one_sided), else
    None. Such a line is shown with its line and price and never graded, staked, put on a slip or tracked. A two-sided
    line whose other side is suspended right now keeps its grading (its type is the stored variant)."""
    if q.get("v") != "one_sided":
        return None
    o = q.get("o") or [0, 0]
    return "Over only" if o and o[0] else "Under only"


def full_game_key(k):
    return ((k or "").rsplit("|", 1)[-1] or "full_game") == "full_game"
HOLD_LABEL = "Research estimate"
HOLD_DETAIL = "{LG} grades are switched off."     # only under the owner's kill switch SPORTS_CONTAIN_<LG>=1 (ct-1b)
SERVICE_USER = "workspace-reader"       # the hook's member for reads no one member owns: shared boards, settled results
BOARD_TTL, CHECK_TTL, PLAYER_TTL, RESULTS_TTL = 20, 5, 120, 300
HISTORY_TTL = 600                       # bandwidth 2026-11-13: a player's game log changes only when a game ends; /api/sports/history was
                                        # re-read every 120 s per player by every book board build (96k reads, 716 MB a day)
INTERNAL_RETRY_S = 300                  # the private-network address refused a connection: the public URL for this long
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
_INTERNAL = {"down_until": 0.0, "fallbacks": 0, "ok": 0, "public": 0, "last_ok_at": None, "last_error": None, "last_error_at": None}


def internal_base():
    """Bandwidth 2026-11-13: on Render the sports service is reached over the private network (Connect -> Internal:
    scout-sports-api:10000), which Render does not bill as bandwidth; through the public URL every server-to-server read was
    billed twice (the sports service's HTTP egress and this service's outbound). SPORTS_API_INTERNAL=0 turns it off; a URL
    there is used as written. Off Render (no RENDER env): '' - the public base as before."""
    v = (os.environ.get("SPORTS_API_INTERNAL") or "").strip()
    if v.lower() in ("0", "off", "false", "no"):
        return ""
    if v:
        return v.rstrip("/")
    return "http://scout-sports-api:10000" if os.environ.get("RENDER") else ""
_NOT_MODIFIED = object()


def _empty():
    return {"at": 0.0, "body": None, "index": {}, "props": {}, "feeds": {}, "etags": {}, "as_of": None, "gen": 0, "injuries": {}}


_SNAP = {lg: _empty() for lg in LEAGUES}


def reset():
    """Tests: forget every board and the token."""
    for lg in LEAGUES:
        _SNAP[lg] = _empty()
    _MOVES.clear()
    with _SETTLED_LOCK:
        _SETTLED_MOVES.clear()
    _TOKEN.update(token=None, exp=0.0, base=None)
    _BREAK["until"] = 0.0
    _INTERNAL.update(down_until=0.0, fallbacks=0, ok=0, public=0, last_ok_at=None, last_error=None, last_error_at=None)


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


class _AdminHook(_Hook):
    """The owner's own server-to-server read (workspace_api grade-review, owner audit 2026-11-06 T8): the hook mints an st1
    token of role admin, which the sports service requires on /api/sports/admin/*. Used only on an owner-only route."""

    def member(self):
        return {"user": "owner-grade-review"}

    def authed(self, token):
        return True


def admin_token(srv):
    """A short-lived st1 admin token minted by server.py's one sports hook (never cached, never logged), or None when the
    hook cannot mint one (SPORTS_TOKEN_SECRET not set, sports down). Call ONLY after the owner check."""
    hk = _AdminHook()
    try:
        srv.Handler._sports_route(hk, urlparse("/api/sports/token"), {})
    except Exception:                               # noqa: BLE001 - the caller says the report is unavailable
        return None
    if hk.out is None or hk.out[0] != 200:
        return None
    tok = (_json(hk.out[1]) or {}).get("token")
    return tok if isinstance(tok, str) and tok.startswith("st1.") else None


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


def _open(url, head, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=timeout)


def _open_routed(base, path_q, head, timeout):
    """The private-network address first (when on), else / after a refused connection the public base. Only a failure to
    connect falls back (5 min, never the breaker); an HTTP answer from the internal address is the service's answer."""
    ib = internal_base()
    if ib and time.time() >= _INTERNAL["down_until"]:
        try:
            r = _open(ib + path_q, head, timeout)
        except urllib.error.HTTPError:
            _INTERNAL["ok"] += 1                    # the service answered over the private network (a 304 / 4xx is its answer)
            _INTERNAL["last_ok_at"] = time.time()
            raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            now = time.time()
            _INTERNAL.update(down_until=now + INTERNAL_RETRY_S, fallbacks=_INTERNAL["fallbacks"] + 1, last_error_at=now,
                             last_error=f"{type(e).__name__}: {str(e)[:120]}")
            sys.stderr.write(f"  sports private network failed ({_INTERNAL['last_error']}); public URL for {INTERNAL_RETRY_S} s\n")
        else:
            _INTERNAL["ok"] += 1
            _INTERNAL["last_ok_at"] = time.time()
            return r
    if ib:
        _INTERNAL["public"] += 1
    return _open(base + path_q, head, timeout)


def transport_status(now=None):
    """/health: how server-to-server sports reads travel. state ok = private network answering; warn = the private address
    failed and no private read has succeeded since (reads are on the public URL, billed as bandwidth); off = not on Render or
    SPORTS_API_INTERNAL=0; idle = no read yet."""
    now = time.time() if now is None else now
    ib = internal_base()
    ok_at, err_at = _INTERNAL["last_ok_at"], _INTERNAL["last_error_at"]
    if not ib:
        state = "off"
    elif err_at and (not ok_at or ok_at < err_at):
        state = "warn"
    elif ok_at:
        state = "ok"
    else:
        state = "idle"
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None
    mode = "off" if not ib else "public" if _INTERNAL["down_until"] > now else "private"
    return {"state": state, "mode": mode, "internal": ib or None, "internal_ok": _INTERNAL["ok"], "public_reads": _INTERNAL["public"],
            "public_fallback": _INTERNAL["fallbacks"], "fallbacks": _INTERNAL["fallbacks"], "last_ok_at": iso(ok_at), "last_error": _INTERNAL["last_error"],
            "last_error_at": iso(err_at), "public_until": iso(_INTERNAL["down_until"]) if _INTERNAL["down_until"] > now else None}


def _service(srv, path, query, etag, timeout):
    tok, base = _token(srv)
    if not base:
        raise Down("no sports service")
    path_q = path + ("?" + urlencode(query) if query else "")
    head = {"Authorization": f"Bearer {tok}", "Accept": "application/json", "Accept-Encoding": "gzip"}
    if etag:
        head["If-None-Match"] = etag
    try:
        with _open_routed(base, path_q, head, timeout) as r:
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
              "hardrockbet": "HR", "bet365": "36", "unibet_au": "UA", "pointsbet_au": "PA"}


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


# B8 (audit 2026-11-05): ONE team code per team on every surface - the same table as sports/names.py TEAM_ABBR and
# web/sports/labels.js TEAM_ABBR (this module never imports sports; sports/tests/test_audit_sports_1105.py keeps the
# three equal). The board's event teams carry ESPN's codes (logo URLs use them); members always read UTA, never UTAH.
TEAM_ABBR = {
    "nba": {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"},
    "nfl": {"WSH": "WAS"},
}


def team_code(lg, abbr):
    if not abbr:
        return abbr
    a = str(abbr).strip().upper()
    return TEAM_ABBR.get(lg, {}).get(a, a)


def _team(v, lg=None):
    a = (v or {}).get("abbr")
    return team_code(lg, a) if a and lg else (a or (v or {}).get("name") or "")


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
    out = {"offered": offered, "payout": dec if dec is not None else mult, "price": dec}
    ask = _exchange_ask(q.get("k"), i)
    if offered and ask is not None:
        out["ask"] = ask                    # an exchange's own price to buy this side, 0-1 (owner 2026-11-12: every row shows its odds)
    return out


def _exchange_ask(k, i):
    """An exchange's own price to buy one side, from its YES quote [bid, ask]: Over = the YES ask, Under = 1 - the YES bid (the
    NO ask). None when that number is missing or outside 0-1. Read from the quote as it is, never from another line."""
    if not isinstance(k, (list, tuple)) or len(k) != 2:
        return None
    v = k[1] if i == 0 else (None if k[0] is None else 1 - k[0])
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v < 1:
        return None
    return round(v, 4)


# owner 2026-11-12 ("make sure they all have grades"): a sports row without a letter names why in a few plain words on the row
# (hold.short); the full sentence stays in hold.reason. The rules themselves are unchanged (sports/board_view, availability).
HOLD_SHORT = {"cap": "Priced far from even", "gate": "Letters switched off", "ratio_low": "Projection far below line",
              "ratio_high": "Projection far above line", "l10_contra": "Last 10 games disagree", "floor": "Not enough player data",
              "certain": "Forecast out of range", "thin": "Fewer than 3 games", "stale": "No game in the last year",
              "small_low": "Projection far below line", "av_out": "Ruled out", "av_injured_reserve": "Injured reserve",
              "av_suspended": "Suspended", "av_inactive": "Inactive",
              # os-2 (grade_rules.side_hold): PrizePicks does not list Scout's side on this line (sweep 2026-11-23: was a bare "No grade")
              "side": "Side not offered"}


def no_forecast(nf, stat_label):
    """(reason, short) for a line with no Scout forecast, from the sports pipeline's own reason (quote nf, snapshots
    no_forecast_reason). Model coverage and a line newer than the last forecast run are told apart; nothing is guessed."""
    nf = str(nf or "")
    if nf.startswith("Market not supported"):
        return f"Scout's model does not cover {stat_label}.", "Stat not graded"
    if nf.startswith("Line not modelled"):
        return "Scout does not grade this kind of line.", "Line not graded"
    if nf.startswith("No Scout forecast:"):
        return "Scout has no forecast for this player and stat: " + nf.split(":", 1)[1].strip(), "No forecast"
    if "after the last forecast run" in nf:
        return "This line appeared after Scout's last forecast run (forecasts run every 30 minutes).", "New line"
    return "Scout has no forecast for this line.", "No forecast"


def record_of(lg, doc, m, q, view):
    """One display record from one quote of one market row: the row's values only, nothing recomputed."""
    ev = (doc.get("events") or {}).get(m.get("e")) or {}
    home, away = _team(ev.get("home"), lg), _team(ev.get("away"), lg)
    team = team_code(lg, m.get("tm")) or ""
    tid = m.get("tid")
    opp = away if tid and tid == (ev.get("home") or {}).get("team_id") else home if tid and tid == (ev.get("away") or {}).get("team_id") else ""
    # the player's own team as the event names it (ESPN's abbreviation): the board's team logo (audit 2026-11-05), display only
    # raw ESPN abbreviation (GS, UTAH...), not the display code: ESPN's logo files are named by it
    tabbr = _team(ev.get("home")) if tid and tid == (ev.get("home") or {}).get("team_id") else _team(ev.get("away")) if tid and tid == (ev.get("away") or {}).get("team_id") else ""
    scope = (m.get("k") or "").rsplit("|", 1)[-1] or "full_game"
    p = q.get("p")
    fc = doc.get("fc") or {}
    one = one_sided_label(q)
    sc = None if one else scout_at(q, fc)       # owner 2026-11-06: a one-sided line is shown, never graded
    mkc = market_at(m.get("mk"), q)
    lean, held = (None, None) if one else lean_and_hold(m, q, sc, mkc)
    held = None if held is None else held or HOLD_DETAIL.format(LG=lg.upper())
    # Letters are published by Sports, never reconstructed from probabilities here.
    # ct-1b (owner 2026-11-08): NBA and NFL letters are released; only the owner's kill switch SPORTS_CONTAIN_<LG>=1
    # holds them (sports/containment.py; the quote then carries x.b "gate"). Stakes stay 0. Sanity/market holds still win.
    signal = q.get("x") or {}
    grade = signal.get("gr") if held is None and lean and signal.get("v") == "cf-2" else None
    if grade not in ("A", "B", "C", "Lean"):
        grade = None
    short = None
    if one:
        reason = f"{one}: Scout grades lines that offer both sides."
    elif held is not None:
        reason = held
        short = "Letters switched off" if not signal.get("b") or m.get("nv") else HOLD_SHORT.get(signal.get("b"), "No grade")
    elif not sc:
        reason, short = no_forecast(q.get("nf"), (doc.get("stats") or {}).get(m.get("st"), m.get("st")))
    elif not lean and mkc and lean_of(sc, q.get("l")):
        reason, short = "The market already prices the model's preferred side at least as highly.", "No edge over books"
    elif not lean:
        reason, short = "The forecast does not give a clear direction at this line.", "No clear side"
    else:
        reason, short = "A published grade is not available for this forecast yet.", "No grade yet"
    hold = None if grade else {"label": one or HOLD_LABEL, "reason": reason, "short": one or short or "No grade"}
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
           "player": {"id": m.get("pid"), "key": pkey, "name": m.get("n") or "", "team": team, "opponent": opp, "role": m.get("ro") or m.get("pos") or "", "team_abbr": tabbr},
           "market": {"stat": m.get("st"), "stat_label": (doc.get("stats") or {}).get(m.get("st"), m.get("st")), "period": scope, "period_label": period_label, "line_type": line_type_of(q)},
           "offer": {"platform": p, "platform_label": book_label(p), "offer_id": rid, "revision": offer_rev(q), "revision_kind": "exact", "line": line, "seen_at": q.get("t"),
                     "over": _side_prices(q, 0), "under": _side_prices(q, 1), "moved_from": None, "moved_at": None},
           "scout": {"side": lean, "read_rev": read_rev(lean, sc, held, grade), "p_side": side_p, "p_over": sc["po"] if sc else None, "p_under": sc["pu"] if sc else None, "p_push": sc["pp"] if sc else None,
                     "projection": sc.get("mu") if sc else None, "range80": sc.get("iv") if sc else None, "grade": grade, "units": 0 if grade else None, "stake_reason": "Stakes remain 0." if grade else None,
                     "hold": hold, "model_version": sc.get("model") if sc else None, "forecast_at": sc.get("at") if sc else None,
                     "validation": {"kind": "research", "label": "Scout grade" if grade else (one or HOLD_LABEL),
                                    "detail": "Scout grade. Stakes remain 0." if grade else reason}},
           "history": {"l5": _tally(a[4:8] if a else None), "l10": _tally(a[:4] if a else None), "l20": _tally((v.get("lines20") or {}).get(line_key(line))) if line is not None else None, "avg": None, "recent": None,
                       **(_board_columns(v, line, opp) if workspace_h2h.board_enabled() else {})},
           "market_chance": {"p_over": mkc["po"], "p_under": mkc["pu"], "sources": mkc.get("n")} if mkc else None,
           "state": {"kind": "ok", "reason": None}}
    if signal.get("bp") and grade in ("A", "B", "C"):
        # bp-1 (NBA_BEST_PROP, display only): Sports marked this quote the player's featured best prop. The key is absent
        # otherwise, so with the flag off the record is main's exactly.
        rec["scout"]["featured"] = True
    x = {"r": q.get("r"), "p": p, "l": line, "o": q.get("o"), "m": q.get("m"), "g": q.get("g"), "ud": q.get("ud"), "fid": sc.get("fid") if sc else None, "po": sc["po"] if sc else None,
         "pu": sc["pu"] if sc else None, "pp": sc["pp"] if sc else None, "mu": sc.get("mu") if sc else None, "model": sc.get("model") if sc else None, "at": sc.get("at") if sc else None,
         "lean": lean, "start": ev.get("start_utc"), "started": bool(ev.get("started") or ev.get("locked")), "e": m.get("e"), "pid": m.get("pid"), "name": m.get("n") or "", "team": team, "st": m.get("st"),
         "scope": scope, "rev": rec["offer"]["revision"], "read_rev": rec["scout"]["read_rev"], "rid": rid, "pkey": pkey, "variant":q.get('v'),
         "line_type": rec["market"]["line_type"]}
    # "Behind the grade" (card_grade_inputs.py; server side only, never in the board body): the forecast a line read came from, the
    # hold's own version tag (av-1 / cap-3 ...), the calibration and grade policy the quote names
    for k, v in (("lf", (q.get("lr") or {}).get("f") if sc and not sc.get("fid") else None), ("hv", signal.get("v") if signal.get("b") else None),
                 ("cal", (q.get("ca") or {}).get("v") if sc else None), ("gp", signal.get("gp"))):
        if v:
            x[k] = v
    return rec, x


def _price(v):
    """A book's side as it reads: a decimal price, or an exchange's bid and ask."""
    return f"{v[0]} / {v[1]}" if isinstance(v, (list, tuple)) and len(v) == 2 else v


def _book_rows(mk, line):
    """Every book of this market, split by whether it prices this exact line: a different line is never lined up beside it."""
    same, other = [], []
    for b in (mk or {}).get("bk") or []:
        row = {"platform": b.get("p"), "platform_label": book_label(b.get("p")), "line": b.get("l"), "kind": b.get("kind"), "chance": b.get("c"), "over": _price(b.get("over")), "under": _price(b.get("under")),
               "variant": b.get("v"), "seen_at": b.get("t"), "in_consensus": bool(b.get("in"))}
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
        return None, None                       # secondary markets or hit rates missing: None = this part's read failed


KEEP_MIN = 20       # site audit N2: a pick'em platform that held at least this many lines and comes back with none is a failed read
PART_KEEP_S = 10 * 60   # a secondary part (more markets / hit rates) down longer than this past its last good read: built without it


def _keep_last(s, counts):
    """Owner site audit 2026-11-06 N2 (NBA board went to "0 players · 0 lines" until Refresh): a refresh that came back without a
    pick'em platform's lines (PrizePicks or Underdog from many lines to none, the whole board empty included) keeps the last good
    snapshot instead of replacing it, while that snapshot is under STALE_S old (its own board time); after that the new answer is
    taken as it is, so a slate that really emptied never freezes the board. Transient-failure rule: a failed read never becomes
    the board. (A secondary part that failed never holds the primary lines back: see _part_or_last.)"""
    if s["body"] is None:
        return False
    t = _ts(s["as_of"])
    if t is None or (datetime.now(timezone.utc) - t).total_seconds() > STALE_S:
        return False
    return any(n >= KEEP_MIN and not counts.get(p) for p, n in (s.get("counts") or {}).items() if p in PICKEM)


def _part_or_last(s, name, doc, now):
    """-> (this build's copy of a secondary part, the time it was read). Site audit N2 review: when the more-markets or hit-rate
    part fails, the primary lines read in the same build are still served at once (a moved or pulled line is never held back);
    only the failed part is the last good copy, read under PART_KEEP_S ago (hit rates are keyed by line, so a kept copy never
    puts one line's counts on another). Past that the board is built without it, as before. A failed read is never kept.
    The last good copy is held compressed (_pack: a few hundred KB per part, not the parsed doc), unpacked only on a failure."""
    if doc is not None:
        return doc, now
    last = (s.get("parts") or {}).get(name)
    if last and now - last[1] < PART_KEEP_S:
        return json.loads(gzip.decompress(last[0])), last[1]
    return None, None


def _pack(s, name, doc, at):
    """(compressed doc, read time) of a part for the next build; a part kept from earlier keeps its own blob (no re-pack)."""
    last = (s.get("parts") or {}).get(name)
    if last and last[1] == at:
        return last
    return gzip.compress(json.dumps(doc, default=str, separators=(",", ":")).encode(), 1), at


# Site audit N7 (Filters > Line moved showed 0 of 5,641 NBA lines): the sports board carries no opening line, so this server
# notes the first standard line it saw per platform / event / player / stat / period and the time it last changed. A line that
# differs from that first sighting carries moved_from (that line) and moved_at, as the CS2 board does from its own open_line.
# Only moves Scout itself observed (a restart starts again from the lines then on the board); display only, never a grade
# input. Bounded: markets whose start is more than MOVES_KEEP_S past leave, and at most MOVES_MAX per league are held.
MOVES_KEEP_S = 6 * 3600
MOVES_MAX = 4 * 6000
_MOVES = {}


def _move_key(rec, x):
    return (x["p"], x["e"], x["pkey"], x["st"], x["scope"]) if rec["market"]["line_type"] == "standard" else None


def _note_move(lg, rec, x, now_iso, seen):
    k = _move_key(rec, x)
    if k is None or seen.get(k, 0) != 1:             # two standard lines on one platform market: no single opening line
        return
    line = x["l"]
    moves = _MOVES.setdefault(lg, {})
    m = moves.get(k)
    if m is None:
        m = moves[k] = {"open": line, "line": line, "at": None, "start": x.get("start")}
    elif m["line"] != line:
        m.update(line=line, at=now_iso, start=x.get("start"))
    if m["open"] != line:
        rec["offer"]["moved_from"], rec["offer"]["moved_at"] = m["open"], m["at"]


def _prune_moves(lg, now):
    moves = _MOVES.get(lg) or {}
    cut = now - timedelta(seconds=MOVES_KEEP_S)
    for k in [k for k, m in moves.items() if (_ts(m.get("start")) or now) < cut]:
        moves.pop(k, None)
    if len(moves) > MOVES_MAX:
        for k in sorted(moves, key=lambda k: str(moves[k].get("start") or ""))[:len(moves) - MOVES_MAX]:
            moves.pop(k, None)


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
    t_read = time.time()
    v_doc, v_at = _part_or_last(s, "view", v_doc, t_read)
    more, m_at = _part_or_last(s, "more", more, t_read)
    if v_at != t_read:
        v_tag = None                                # a kept part is read in full again next build
    if m_at != t_read:
        m_tag = None
    view = (v_doc or {}).get("rows") or {}
    records, index, props, platforms, counts, seen, built = [], {}, {}, {}, {}, {}, []
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for doc in (head, more or {}):
        for m in doc.get("markets") or []:
            if not full_game_key(m.get("k")):
                continue                                # S1/S2: quarter / half lines are not full-game board rows
            for q in m.get("q") or []:
                if len(records) >= MAX_RECORDS or q.get("l") is None:
                    continue
                d = doc if doc.get("events") else head
                rec, x = record_of(lg, d, m, q, view)
                if not rec["event"]["start_utc"]:
                    continue
                board_parity.attach(rec)                # 4.16 parity numbers, only while a SCOUT_PARITY_* row flag is on
                records.append(rec)
                built.append((rec, x))
                counts[q["p"]] = counts.get(q["p"], 0) + 1
                mk = _move_key(rec, x)
                if mk is not None:
                    seen[mk] = seen.get(mk, 0) + 1
                x["books"] = _book_rows(m.get("mk"), q.get("l"))
                index[(q["p"], x["rid"])] = x
                props.setdefault((x["e"], x["pkey"], x["st"], x["scope"]), []).append((q["p"], x["rid"]))
                platforms.setdefault(q["p"], {"label": book_label(q["p"]), "short": book_short(q["p"]), "slip": q["p"] in PICKEM, "kind": _kind(q["p"]),
                                              **({"types": [["standard", "Standard"], ["flex", "Flex"]] if q["p"] == "underdog" else [["power", "Power"], ["flex", "Flex"]]} if q["p"] in PICKEM else {})})
    for rec, x in built:                            # site audit N7: after the whole board is read, so a doubled line is known
        _note_move(lg, rec, x, now_iso, seen)
        x["rec"] = json.dumps(rec, default=str, separators=(",", ":")).encode()
    _prune_moves(lg, now)
    if _keep_last(s, counts):
        s["at"] = time.time()                       # the etags stay the last good build's: the next build reads every part again
        return 200, s["body"]
    as_of = head.get("as_of") or head.get("built_at")
    feeds = _feeds(as_of, [p for p in PICKEM if p in platforms])
    body = json.dumps({"sport": lg, "version": head.get("version"), "board_at": as_of, "feeds": feeds, "platforms": platforms, "records": records}, default=str, separators=(",", ":")).encode()
    if s["body"] == body:
        body = s["body"]
    _SNAP[lg] = dict(s, at=time.time(), body=body, index=index, props=props, feeds=feeds, as_of=as_of, gen=s["gen"] + 1, injuries=head.get("injuries") or {},
                     etags={"primary": tag, "view": v_tag, "more": m_tag}, counts=counts,
                     parts={k: _pack(s, k, d, at) for k, d, at in (("view", v_doc, v_at), ("more", more, m_at)) if d is not None})
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
    if x.get("variant") == "one_sided":          # owner 2026-11-06: shown, never on a slip or tracked
        problems.append(_pr(1, "not_gradable", f"{one_sided_label({'v': 'one_sided', 'o': x['o']})}: Scout grades lines that offer both sides."))
    elif not x["g"]:
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
            "displayed_p": p_shown, "grade": grade, "units": 0 if grade else None, "stake_reason": "Stakes remain 0." if grade else None,
            "research": True, "hold": (rec["scout"].get("hold") or {}).get("label"), "projection": x["mu"], "forecast_p": p_shown,
            "forecast_at": x["at"], "forecast_grade": grade, "forecast_units": 0 if grade else None, "l10": rec["history"]["l10"], "feed_at": rec["offer"]["seen_at"], "rules_version": e.get("rules_version"), "entry_type": e.get("entry_type")}


def receipt_fields(x, leg, lg, res, e):
    side, oid = leg["side"], offer_id(x["r"], x["p"])
    mult = (x["m"] or [None, None])[0 if side == "over" else 1]
    return {"sport": lg, "row_id": x["rid"], "offer_rev": x["rev"], "side": side, "line": x["l"], "line_type": x.get("line_type") or "standard", "pick_id": None, "forecast_id": x["fid"], "match_slug": x["e"], "match_id": x["e"],
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


# ---------------------------------------------------------------------------------------------------------------- line moves
# SCOUT_LINE_MOVE_NOTES (workspace_api._receipt_moves, wording only): a settled NBA / NFL receipt's opening and closing line, from
# the sports API's stored offer revisions (GET /api/sports/offers/moves - sports/line_moves.py: opening = the offer's first stored
# revision, closing = the last one before the game's real start). Never the in-memory first sightings above (_MOVES): those reset
# on a restart and leave 6 h after the start. At most MOVE_CALLS reads of MOVE_KEYS offers per My picks build, behind a bounded
# cache: an answer for a game that started more than MOVES_FINAL_S ago is kept, anything else is asked again after MOVES_TTL.
MOVE_KEYS, MOVE_CALLS, MOVE_TIMEOUT = 60, 2, 2.5
MOVES_TTL, MOVES_FINAL_S, MOVES_CACHE_MAX = 300, 24 * 3600, 20000
_SETTLED_MOVES = {}
_SETTLED_LOCK = threading.Lock()


def settle_key_offer(settle_key):
    """(league, platform, offer_id) from a receipt's settle_key "<league>|<platform>|<offer_id>|<side>|<line>", or None."""
    parts = str(settle_key or "").split("|")
    if len(parts) < 5 or parts[0] not in LEAGUES or not parts[1] or not parts[2]:
        return None
    return parts[0], parts[1], "|".join(parts[2:-2])


def line_moves(srv, keys, now=None):
    """{(platform, offer_id): (opening, closing)} for the offers the sports API knows both lines of. keys: (platform, offer_id,
    start). The sports API down (or slow) = nothing new to say; what is cached still answers."""
    now = time.time() if now is None else now
    out, need, seen = {}, [], set()
    for platform, oid, start in keys:
        k = (platform, oid)
        if k in seen:
            continue
        seen.add(k)
        with _SETTLED_LOCK:
            e = _SETTLED_MOVES.get(k)
        if e is not None and (e[2] or now - e[1] <= MOVES_TTL):
            if e[0] is not None:
                out[k] = e[0]
        else:
            need.append((k, start))
    for i in range(0, min(len(need), MOVE_KEYS * MOVE_CALLS), MOVE_KEYS):
        part = need[i:i + MOVE_KEYS]
        try:
            doc, _ = call(srv, "/api/sports/offers/moves", {"keys": json.dumps([list(k) for k, _ in part], separators=(",", ":"))},
                          timeout=MOVE_TIMEOUT)
        except Down:
            break
        got = {}
        for m in (doc or {}).get("moves") or []:
            if isinstance(m, list) and len(m) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in m[2:]):
                got[(m[0], m[1])] = (float(m[2]), float(m[3]))
        with _SETTLED_LOCK:
            for k, start in part:
                st = _ts(start)
                _SETTLED_MOVES.pop(k, None)
                _SETTLED_MOVES[k] = (got.get(k), now, st is not None and now - st.timestamp() > MOVES_FINAL_S)
                while len(_SETTLED_MOVES) > MOVES_CACHE_MAX:
                    _SETTLED_MOVES.pop(next(iter(_SETTLED_MOVES)))
                if got.get(k) is not None:
                    out[k] = got[k]
    return out


# ---------------------------------------------------------------------------------------------------------------- research

def _player(srv, lg, pid, path):
    """One player's file, shared across members per player. No slot within a second: the page shows what it already holds."""
    def compute():
        if not _PLAYER.acquire(timeout=1.0):
            return 200, None, 1
        try:
            doc, _ = call(srv, path, {"league": lg, "id": pid})
            return 200, trim(doc, lg)
        except Down:
            return 200, None, 5
        finally:
            _PLAYER.release()
    return srv.shared(("ws_sp_file", path, lg, pid), compute, ttl=HISTORY_TTL if path == "/api/sports/history" else PLAYER_TTL)[1]


SPLIT_GAMES = 82        # the classic player page's splits read the last 82 games held (web/sports/app.js historyPanel)
OPP_PARTS = ("min", "targets", "rush_att", "pass_att")


def _q(q):
    """One book quote of the player file, as the classic line strip reads it (sides with price / payout / bid-ask)."""
    sides = {}
    for x in q.get("sides") or []:
        if x.get("side") in ("over", "under"):
            sides[x["side"]] = {k: x.get(k) for k in ("offered", "price", "price_format", "multiplier", "bid", "ask") if x.get(k) is not None}
    return {"platform": q.get("platform"), "line": q.get("line"), "variant": q.get("variant"), "seen_at": q.get("last_seen_at"), "over": sides.get("over") or {}, "under": sides.get("under") or {}}


def _fc(f):
    d = f.get("detail") or {}
    return {"line": f.get("line"), "mean": f.get("projection_mean"), "at": f.get("forecast_at"), "model": f.get("model_version"), "reasons": list(f.get("reason_codes") or [])[:12],
            **{k: f[s] for k, s in (("artifact", "artifact_version"), ("calibration", "calibration_version"), ("grade_policy", "grade_policy_version")) if f.get(s)},
            "contributions": [{"label": c.get("label") or c.get("feature"), "value": c.get("value")} for c in f.get("contributions") or []][:20],
            "interval": d.get("interval"), "median": d.get("median"),
            "inputs": [{**({"id": x["id"]} if x.get("id") else {}), "label": x.get("label"), "value": x.get("value"), "definition": x.get("definition")} for x in d.get("factors") or []][:30]}


def _det(d):
    p = (d or {}).get("pmf") or {}
    return {"bins": [b for b in p.get("bins") or [] if isinstance(b, list) and len(b) == 3][:80] if not d.get("ladder_withheld") else [],
            "ladder": [{k: r.get(k) for k in ("line", "p_over", "p_under", "p_push", "stored")} for r in d.get("ladder") or []][:20]}


def trim(doc, league=None):
    """Only what research reads, so a cached player costs little. The classic player page's panels (owner 2026-11-09: the hub card
    shows them too): home / away and season type for the splits, the opportunity parts for the trend, every book quote and Kalshi rung
    of each market, and each of the player's forecasts with its distribution ladder - values as served, nothing recomputed."""
    if "games" in doc:
        lg = {"basketball": "nba", "football": "nfl"}.get(doc.get("family"))      # B8: one team code
        return {"games": [{"start_utc": g.get("start_utc"), "opponent": _team(g.get("opponent"), lg), "home": g.get("home"), "played": g.get("participation") == "played", "values": g.get("values") or {},
                           "season": g.get("season"), "season_type": g.get("season_type"),
                           "parts": {k: v for k, v in (g.get("parts") or {}).items() if k in OPP_PARTS and v is not None}} for g in (doc.get("games") or [])[:SPLIT_GAMES]]}
    f = doc.get("factors") or {}
    fcs = doc.get("forecasts") or {}
    dets = doc.get("forecast_detail") or {}
    ne = doc.get("next_event") or {}
    lg = league
    return {"position": doc.get("position"), "status": doc.get("status"), "injury": doc.get("injury"), "next_event": doc.get("next_event"), "context": f.get("context") or [], "stats": f.get("stats") or [],
            "engine": f.get("engine"), "next_opp": _team(ne.get("opponent"), lg) if ne.get("opponent") else None, "next_home": ne.get("home"),
            "markets": [{"stat": m.get("stat"), "scope": m.get("scope") or "full_game", "quotes": [_q(q) for q in (m.get("quotes") or []) + (m.get("alternates") or [])][:40],
                         "kalshi": [{"line": k.get("line"), "bid": ((k.get("sides") or [{}])[0]).get("bid"), "ask": ((k.get("sides") or [{}])[0]).get("ask"),
                                     "volume": (k.get("liquidity") or {}).get("volume"), "oi": (k.get("liquidity") or {}).get("open_interest"), "seen_at": k.get("last_seen_at")}
                                    for k in m.get("kalshi") or []][:30]} for m in doc.get("markets") or []],
            "forecasts": {k: _fc(v) for k, v in fcs.items() if isinstance(v, dict)},
            "details": {k: _det(v) for k, v in dets.items() if isinstance(v, dict)}}


def series_of(hist, stat):
    """Played games with a value for this stat, newest first: the same games the board's hit rates count."""
    out = []
    for g in ((hist or {}).get("games") or [])[:RESEARCH_MAX_GAMES]:      # the research window (the splits read further back)
        v = (g.get("values") or {}).get(stat)
        if g.get("played") and v is not None:
            out.append({"date": str(g.get("start_utc") or "")[:10], "opponent": g.get("opponent") or "", "value": v})
    return out


ENGINE_GROUP = "Matchup numbers (not part of Scout's forecast)"


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
            items = c.get("items") or []
            # card-and-chips 2026-11-06: the definite outs over the WHOLE report (the rows below keep only the first six), so the
            # card's "teammates out" names and count are never cut short; day-to-day / questionable stay off it (no-hedge rule)
            outs = [str(i.get("player")) for i in items if i.get("player") and str(i.get("status") or "").lower() in ("out", "injured_reserve", "suspended")]
            if outs:
                rows.append({"label": "Teammates out", "value": len(outs), "group": "Matchup", "names": outs[:12]})
            for i in items[:6]:
                add(i.get("player"), i.get("status"))
        elif isinstance(c.get("value"), bool):
            add(c.get("label"), "Yes" if c["value"] else "No")
        else:
            add(c.get("label"), c.get("value"))
    for s in (player or {}).get("stats") or []:
        for w in s.get("windows") or []:
            if w.get("window") and w.get("mean") is not None:
                add(f"{s.get('label')}, {w['window']}", w["mean"], "Averages")
    # the factor engine's rows are context (sports/player_context.py: weight 0, the Scout model does not use them): their heading
    # says so, never "Model inputs" (grading-factor rule, card-and-chips 2026-11-06)
    for r in ((player or {}).get("engine") or {}).get("rows") or []:
        if r.get("label") and r.get("value") is not None:
            add(r.get("label"), r.get("value"), ENGINE_GROUP)
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
            books.append({"platform": bk, "platform_label": r["offer"]["platform_label"], "row_id": i, "key": r["key"], "line": o["l"], "line_type": o.get("line_type") or "standard", "over": r["offer"]["over"], "under": r["offer"]["under"],
                          "seen_at": r["offer"]["seen_at"], "scout_side": o["lean"], "p_side": r["scout"]["p_side"], "grade": r["scout"]["grade"]})
    same, other = x.get("books") or ([], [])
    mk = rec.get("market_chance")
    forecast = {"id": x["fid"], "logged_at": x["at"], "model_version": x["model"], "p_saved": None} if x["fid"] else None
    tally = rec["history"]["l10"]
    avg = round(sum(s["value"] for s in series[:10]) / len(series[:10]), 1) if series else None
    hist_out = {"tally": tally, "avg": avg, "series": series[:20] if series else None}
    if series is None and hist:
        # a Q1 / first-half line has no results at its own scope in the saved file: the card shows his full-game log beside it as
        # context, labelled full game and never counted against this line (card-and-chips 2026-11-06; same file, no extra read)
        fg = series_of(hist, x["st"])
        if fg:
            hist_out["full_game"] = fg[:10]
    doc = {"record": rec, "forecast": forecast, "history": hist_out, "books": books, "market_books": {"same": same, "other": other},
                 "h2h": workspace_h2h.h2h(series, rec["player"]["opponent"], x["l"]) if series else None,
                 "context": context_of(x, player, lg), "sports_card": sports_card(x, hist, player, lg),
                 "market": {"available": True, "p_over": mk["p_over"], "p_under": mk["p_under"], "source": f"{mk['sources']} market sources at this line"} if mk else {"available": False, "reason": "No market price at this exact line."}}
    try:                                            # "Behind the grade": the record's numbers + this forecast's saved inputs (display only)
        gin = card_grade_inputs.sports(x, rec, player, lg)
        if gin:
            doc["grade_inputs"] = gin
    except Exception:
        pass
    return 200, doc


def _part_of(lg, stat):
    """The opportunity measure the classic page charts for this stat (web/sports/app.js contextPanel)."""
    if lg == "nba":
        return "min", "Minutes"
    s = str(stat or "")
    return ("targets", "Targets") if re.search(r"rec|targets", s) else ("rush_att", "Carries") if "rush" in s else ("pass_att", "Pass attempts")


def sports_card(x, hist, player, lg):
    """The classic player page's panels for this line (owner 2026-11-09), from the two cached player files the card already reads:
    the games for the splits and the opportunity trend, this forecast's distribution ladder and inputs, the matchup engine's rows for
    this stat, every book quote and Kalshi rung of this market. Values as served by Sports; display only, never a grade input."""
    part, part_label = _part_of(lg, x["st"])
    games = []
    for g in (hist or {}).get("games") or []:
        v = (g.get("values") or {}).get(x["st"])
        games.append({"d": str(g.get("start_utc") or "")[:10], "o": g.get("opponent") or "", "h": g.get("home"), "s": g.get("season"), "t": g.get("season_type"),
                      "pl": bool(g.get("played")), "v": v, "p": (g.get("parts") or {}).get(part)})
    p = player or {}
    mk = next((m for m in p.get("markets") or [] if m.get("stat") == x["st"] and (m.get("scope") or "full_game") == x["scope"]), None) or {}
    eng = p.get("engine") or {}
    fid = x.get("fid")
    out = {"part": part, "part_label": part_label, "games": games, "next_opp": p.get("next_opp"), "next_home": p.get("next_home"),
           "stats": p.get("stats") or [], "forecast": (p.get("forecasts") or {}).get(fid) if fid else None, "detail": (p.get("details") or {}).get(fid) if fid else None,
           "quotes": [dict(q, label=book_label(q.get("platform"))) for q in mk.get("quotes") or []], "kalshi": [k for k in mk.get("kalshi") or [] if k.get("bid") is not None or k.get("ask") is not None],
           "engine": {"label": eng.get("label"), "as_of": eng.get("as_of"), "rows": ((eng.get("by_stat") or {}).get(x["st"]) or [])[:60]} if eng.get("by_stat") else None}
    return out if games or out["forecast"] or out["quotes"] or out["engine"] else None


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
            "population": "Saved forward Scout forecasts with a lean, each scored at its own line from the final box score." + ("" if _letters(d) else " These are research estimates, with grades held."),
            "rule": "Over when its chance of Over is above 50% and its projection is above the line, Under mirrored. Hit rate is wins over wins plus losses. Pushes stay apart, voids and unresolved props stay out.",
            "totals": {"picks": leans.get("n") or 0, "wins": leans.get("wins"), "losses": leans.get("losses"), "pushes": leans.get("pushes"), "observed": leans.get("hit_rate"), "ci": leans.get("hit_rate_ci95"),
                       "printed": stated, "matches": d.get("games"), "void": d.get("void"), "match_word": "games"},
            "groups": {"chance": by_band, "stat": by_stat}, "letters": _letters(d), "gate": (d.get("gate") or {}).get("text"),
            "market": {"pairs": hh.get("pairs"), "games": hh.get("games"), "scout_brier": hh.get("scout_brier"), "market_brier": hh.get("market_brier"), "market": hh.get("market")} if hh else None}
