"""The v3 signed-out landing page (direction C, "the line terminal"): web/v3/landing.html filled server-side with real data.

Served instead of web/landing.html + the v3 overlay when v3 is on (server.py, the two landing send sites). web/landing.html is
untouched: flag-off visitors keep its exact bytes (tests/test_ui_v3_gate.py).

Every number on the page comes from a body the server already keeps for its own public routes - never typed into the template:
- the live line terminal: the CS2 board snapshot members read (workspace_api.snapshot, the shared 5 s board), rows chosen by a
  stated rule (soonest graded props where two apps list different lines or a line moved, then the soonest graded props);
- the record ledger: the same shared answer /api/record serves (key and compute identical), per sport;
- the review wall: the same shared answer /api/reviews serves (newest first page), member text escaped here.

Capacity (700 visitors at once): the request path only returns the last built bytes object (send_shared keys its ETag + gzip on
that object's identity). One builder at a time rebuilds it at most every REBUILD_S seconds in a background thread, from cached
bodies only; a missing input is asked for in that thread (single flight through srv.shared), never on a visitor's request. A
missing or failed input drops its block (the terminal falls back to a dated screenshot); the page itself never fails over one.
Display only: nothing here is read by the grader, stakes, forecasts or settlement."""
import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "web" / "v3" / "landing.html"
ASSETS = {"TOKENS_CSS": ROOT / "web" / "v3" / "tokens.css", "PAGE_CSS": ROOT / "web" / "v3" / "landing-page.css",
          "PAGE_JS": ROOT / "web" / "v3" / "landing-page.js"}
STALE_S = 900                      # a page not rebuilt for this long (no visitors) is rebuilt from cached bodies before serving
REBUILD_S = 120                    # the page changes at most this often (hero rows, ledger, reviews)
BOARD_READ = True                  # tests switch this off (no feed read from the background builder)
FRESH_MIN = 30                     # a board older than this is labelled "as of <time>", never "checked N min ago"
SPORTS = ("cs2", "lol", "nba", "nfl")
LABEL = {"cs2": "CS2", "lol": "League", "nba": "NBA", "nfl": "NFL", "soccer": "Soccer", "tennis": "Tennis"}
LONG = {"cs2": "CS2", "lol": "League of Legends", "nba": "NBA", "nfl": "NFL", "soccer": "Soccer", "tennis": "Tennis"}
# The apps and books Scout reads (the same ten the live page names). Text only, no logos.
BOOKS = ("PrizePicks", "Underdog", "Sleeper", "Dabble", "DraftKings", "FanDuel", "BetRivers", "Unibet", "Kalshi", "Polymarket")
SHORT = {"underdog": "UD", "prizepicks": "PP"}

# Review wall featuring rule (shown on the page in small print): reviews about how the product works. A review with photos (they are
# slips / calendars), money or result talk, shouting or profanity stays on /reviews only. Every review stays published there.
_MONEY = re.compile(r"profit|money|\$|cash|paid|payout|\bwon\b|\bwin|hit big|\bbank|rarely miss|every (single )?day|\bmf\b|"
                    r"\bf+u+c+k|\bshit|\bbitch|\bdamn", re.I)
FEATURE_N = 6

_STATE = {"page": None, "built": 0.0, "key": None, "board": (None, None), "last": {}}
_LOCK = threading.RLock()
_TPL = {"mtime": None, "text": ""}


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def _mt():
    return (TEMPLATE.stat().st_mtime,) + tuple(f.stat().st_mtime for f in ASSETS.values())


def _template():
    """The template with tokens.css, the page CSS and JS inlined (one request for the whole first screen). Re-read on change."""
    mt = _mt()
    if _TPL["mtime"] != mt:
        text = TEMPLATE.read_text(encoding="utf-8")
        for k, f in ASSETS.items():
            text = text.replace("{{" + k + "}}", f.read_text(encoding="utf-8"))
        _TPL.update(mtime=mt, text=text)
    return _TPL["text"]


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _when(dt):
    """'Nov 19, 16:20 UTC'."""
    return f"{dt.strftime('%b')} {dt.day}, {dt.strftime('%H:%M')} UTC" if dt else ""


def _day(s):
    dt = _ts(s + "T00:00:00+00:00") if s and len(s) == 10 else _ts(s)
    return f"{dt.strftime('%b')} {dt.day}" if dt else ""


def _pct(p):
    try:
        return f"{float(p) * 100:.1f}"
    except (TypeError, ValueError):
        return None


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return ""
    return str(int(f)) if f == int(f) else f"{f:g}"


# ------------------------------------------------------------------------------------------------------------------- inputs

def _peek(srv, key):
    """A shared answer already in the server's results store (any age within its keep), or None. Never computes."""
    try:
        with srv._RG:
            hit = srv._RESULTS.get(key)
        if hit and hit[1] == 200 and isinstance(hit[2], (bytes, str)):
            return hit[2]
    except Exception:
        pass
    return None


def record_key(sport):
    """The /api/record route's own shared key (server.py): the landing reads exactly what that route serves."""
    return ("public_record", sport, os.environ.get("SCOUT_RECORD_STATUS") == "1", os.environ.get("SCOUT_PUBLIC_RECORD_SLIPS") == "1",
            os.environ.get("SCOUT_RECORD_RELIABILITY") == "1")


def _record_compute(srv, sport):
    def compute():
        import public_record
        try:
            return 200, json.dumps(public_record.read(srv, sport), allow_nan=False).encode()
        except Exception:
            return 503, b'{"error":"record_unavailable"}'
    return compute


def _reviews_key():
    import reviews
    return ("reviews_page", reviews.GEN[0], str(reviews.db_path()), "new", 0)


def _inputs(srv, fetch):
    """{record: {sport: doc}, reviews: doc|None, board: doc|None}. fetch=False: cached bodies only (a visitor's request);
    fetch=True: the background builder, which may ask srv.shared for a missing one (single flight, shared with the routes)."""
    out = {"record": {}, "reviews": None, "board": None}
    if os.environ.get("SCOUT_PUBLIC_RECORD") == "1":
        for sp in SPORTS:
            key = record_key(sp)
            body = _peek(srv, key)
            if body is None and fetch:
                try:
                    st, body = srv.shared(key, _record_compute(srv, sp), ttl=300)
                    body = body if st == 200 else None
                except Exception:
                    body = None
            doc = _parse(("rec", sp), body)
            if doc is None:
                doc = _STATE["last"].get(("rec", sp))       # a failed read keeps the last good ledger (its as-of time shows)
            if doc and isinstance(doc.get("totals"), dict):
                out["record"][sp] = doc
    try:
        import reviews
        if reviews.page_on():
            key = _reviews_key()
            body = _peek(srv, key)
            if body is None and fetch:
                st, body = srv.shared(key, lambda: (200, json.dumps(reviews.public_page("new", 0)).encode()), ttl=300)
                body = body if st == 200 else None
            out["reviews"] = _parse(("rv",), body) or _STATE["last"].get(("rv",))
    except Exception:
        out["reviews"] = _STATE["last"].get(("rv",))
    try:
        import workspace_api
        snap = workspace_api.snapshot(srv) if fetch and BOARD_READ else (workspace_api._SNAP if workspace_api._SNAP.get("body") is not None else None)
        body = snap and snap.get("body")
        if body is not None:
            if _STATE["board"][0] is not body:              # parse once per board body
                _STATE["board"] = (body, json.loads(body))
            out["board"] = _STATE["board"][1]
    except Exception:
        out["board"] = None
    return out


def _parse(name, body):
    if body is None:
        return None
    hit = _STATE["last"].get(("raw",) + name)
    if hit is not None and hit[0] is body:
        return hit[1]
    try:
        doc = json.loads(body)
    except Exception:
        return None
    _STATE["last"][("raw",) + name] = (body, doc)
    _STATE["last"][name] = doc
    return doc


# --------------------------------------------------------------------------------------------------------------- the terminal

def _rows(board):
    """Terminal rows by the stated rule: graded standard CS2 props with a lock after the board's own time, one row per
    player/stat/maps market (every app's line on it). Line gaps and moved lines first, then the soonest."""
    recs = (board or {}).get("records") or []
    at = _ts((board or {}).get("board_at"))
    groups = {}
    for r in recs:
        sc, of, mk, ev = r.get("scout") or {}, r.get("offer") or {}, r.get("market") or {}, r.get("event") or {}
        if mk.get("line_type") != "standard" or not sc.get("side") or sc.get("p_side") is None or (r.get("state") or {}).get("kind") != "ok":
            continue
        lock = _ts(ev.get("lock_utc") or ev.get("start_utc"))
        if lock is None or (at and lock <= at):
            continue
        k = (ev.get("id"), (r.get("player") or {}).get("key"), mk.get("stat"), mk.get("period"))
        groups.setdefault(k, []).append(r)
    out = []
    for k, rs in groups.items():
        rs.sort(key=lambda r: (0 if (r.get("scout") or {}).get("grade") else 1, 0 if (r.get("offer") or {}).get("platform") == "underdog" else 1))
        if not (rs[0].get("scout") or {}).get("grade"):
            continue                                     # a lettered grade leads every terminal row
        lines = {(r.get("offer") or {}).get("line") for r in rs}
        moved = any((r.get("offer") or {}).get("moved_from") is not None for r in rs)
        gap = len(lines) > 1
        lock = _ts((rs[0].get("event") or {}).get("lock_utc") or (rs[0].get("event") or {}).get("start_utc"))
        out.append((0 if gap else 1 if moved else 2, lock, rs, gap, moved))
    out.sort(key=lambda x: (x[0], x[1]))
    seen, pick = set(), []
    for pri, lock, rs, gap, moved in out:
        who = (rs[0].get("player") or {}).get("key")
        if who in seen:
            continue
        seen.add(who)
        pick.append((rs, gap, moved))
        if len(pick) == 6:
            break
    return pick, at


def _l10(r):
    h = ((r.get("history") or {}).get("l10")) or {}
    side = (r.get("scout") or {}).get("side")
    n, over, under = h.get("n"), h.get("over"), h.get("under")
    if not n or over is None:
        return None
    return (over if side == "over" else under), n


def _offer_cell(r, lead):
    of = r.get("offer") or {}
    sc = r.get("scout") or {}
    p = _pct(sc.get("p_side"))
    side = "Over" if sc.get("side") == "over" else "Under"
    tag = SHORT.get(of.get("platform"), esc(of.get("platform_label") or "")[:2].upper())
    mv = ""
    if of.get("moved_from") is not None:
        up = float(of.get("line") or 0) > float(of.get("moved_from") or 0)
        mv = f'<i class="mv {"up" if up else "dn"}" title="Moved from {esc(_num(of.get("moved_from")))}">{"▲" if up else "▼"}{esc(_num(of.get("moved_from")))}</i>'
    return (f'<button type="button" class="bk{" on" if lead else ""}" data-p="{esc(p)}" data-side="{side}" data-g="{esc(sc.get("grade") or "")}"'
            f' aria-pressed="{"true" if lead else "false"}"><b>{tag}</b><span>{esc(_num(of.get("line")))}</span>{mv}</button>')


def minis(board):
    """The tool tiles' small product fragments, from real terminal rows (a line gap, a moved line, a break-even). {} = none."""
    pick, _at = _rows(board)
    out = {}
    for rs, gap, moved in pick:
        if gap and "GAP" not in out:
            out["GAP"] = "".join(_offer_cell(r, i == 0).replace("<button", "<span").replace("</button>", "</span>") for i, r in enumerate(rs)) +                 '<em class="chip gap">Line gap</em>' + _who(rs[0])
        if moved and "MOVE" not in out:
            out["MOVE"] = "".join(_offer_cell(r, i == 0).replace("<button", "<span").replace("</button>", "</span>") for i, r in enumerate(rs)) + _who(rs[0])
        be = _pct((rs[0].get("parity") or {}).get("be"))
        p = _pct((rs[0].get("scout") or {}).get("p_side"))
        if be and p and "BE" not in out:
            out["BE"] = (f'<span class="mt big" style="--p:{esc(p)}"><i></i><u style="--b:{esc(be)}"></u></span>'
                         f'<span class="lbl"><b>Scout {esc(p)}%</b><b>Break-even {esc(be)}%</b></span>'
                         f'<span class="num">{esc((rs[0].get("parity") or {}).get("entry") or "")}</span>' + _who(rs[0]))
    return out


def _who(r):
    pl, mk, of = r.get("player") or {}, r.get("market") or {}, r.get("offer") or {}
    return (f'<span class="num">{esc(pl.get("name"))} · {esc(mk.get("stat_label"))} · {esc(mk.get("period_label"))}</span>')


def terminal(board):
    """(html rows, meta line, row count, live). Empty board -> ("", "", 0, False)."""
    pick, at = _rows(board)
    if not pick:
        return "", "", 0, False
    meta = "as of " + _when(at) if at else ""
    live = False          # the page is cached: the HTML never claims freshness; the inline script reads data-at in the browser
    rows = []
    for rs, gap, moved in pick:
        lead = rs[0]
        ev, pl, mk, sc = lead.get("event") or {}, lead.get("player") or {}, lead.get("market") or {}, lead.get("scout") or {}
        p = _pct(sc.get("p_side"))
        side = "Over" if sc.get("side") == "over" else "Under"
        grade = sc.get("grade") or ""
        l10 = _l10(lead)
        par = lead.get("parity") or {}
        be = _pct(par.get("be"))
        chips = ""
        if gap:
            chips += '<em class="chip gap">Line gap</em>'
        if moved:
            chips += '<em class="chip mvd">Moved</em>'
        dots = ""
        if l10:
            dots = f'<span class="l10" title="Last 10 at this line"><b>{l10[0]}</b>/{l10[1]}</span>'
        start = _ts(ev.get("start_utc"))
        rows.append(
            f'<li class="tr" data-sport="cs2">'
            f'<div class="who"><b>{esc(pl.get("name"))}</b><small>{esc(ev.get("label"))}{(" · " + esc(start.strftime("%a %H:%M")) + " UTC") if start else ""}</small></div>'
            f'<div class="mkt"><span>{esc(mk.get("stat_label"))}</span><small>{esc(mk.get("period_label"))}</small>{chips}</div>'
            f'<div class="bks" role="group" aria-label="Lines by app">{"".join(_offer_cell(r, i == 0) for i, r in enumerate(rs))}</div>'
            f'<div class="ch"><span class="sd">{side}</span><b class="pv">{esc(p)}<i>%</i></b>'
            f'<span class="mt" style="--p:{esc(p)}" aria-hidden="true"><i></i>{f"<u style=--b:{esc(be)}></u>" if be else ""}</span></div>'
            f'<div class="gr"><b class="g{esc(grade[:1].lower())}">{esc(grade) or "–"}</b></div>'
            f'<div class="rc">{dots}{f"<small>break-even {esc(be)}%</small>" if be else ""}</div>'
            f'</li>')
    return "".join(rows), meta, len(rows), live


# ------------------------------------------------------------------------------------------------------------------ the ledger

def _first_sentence(s):
    s = str(s or "").strip()
    m = re.match(r"(.+?\.)(\s|$)", s)
    return m.group(1) if m else s


def ledger(record):
    if not record:
        return "", 0, "", ""
    cells, total, asof, since = [], 0, None, None
    for sp in SPORTS:
        d = record.get(sp)
        if not d:
            continue
        t = d["totals"]
        w, l, p = int(t.get("wins") or 0), int(t.get("losses") or 0), int(t.get("pushes") or 0)
        decided = t.get("picks") if isinstance(t.get("picks"), int) else w + l
        total += decided                                 # the record's own count (wins + losses; pushes apart), as /record shows it
        days = ((d.get("calendar") or {}).get("days")) or []
        a = _ts(d.get("as_of"))
        asof = a if asof is None or (a and a < asof) else asof
        if days:
            since = days[0]["date"] if since is None or days[0]["date"] < since else since
        hi = max([x.get("wins", 0) + x.get("losses", 0) for x in days] or [1]) or 1
        bars = "".join(
            f'<i style="--w:{round(100 * x.get("wins", 0) / hi, 1)}%;--l:{round(100 * x.get("losses", 0) / hi, 1)}%" title="{esc(_day(x.get("date")))}: {x.get("wins", 0)}-{x.get("losses", 0)}"></i>'
            for x in days[-30:])
        cells.append(
            f'<article class="lg" data-sport="{sp}"><header><b>{LONG[sp]}</b><small>{esc(t.get("matches"))} {esc(t.get("match_word") or "matches")}</small></header>'
            f'<p class="wlp"><b>{w:,}</b><span>-</span><b>{l:,}</b><span>-</span><b>{p:,}</b></p><p class="wlk">Won · Lost · Push</p>'
            f'<div class="days" aria-label="The last 30 days, wins above the line and losses below">{bars}</div>'
            f'<p class="rule">{esc(_first_sentence(d.get("rule") or d.get("population")))}</p></article>')
    return "".join(cells), total, (_when(asof) if asof else ""), (_day(since) if since else "")


# ----------------------------------------------------------------------------------------------------------------- the reviews

def featured(doc):
    rv = (doc or {}).get("reviews") or []
    keep = []
    for r in rv:
        text = f'{r.get("title") or ""} {r.get("body") or ""}'
        letters = [c for c in text if c.isalpha()]
        shouting = len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > .6
        if r.get("photos") or _MONEY.search(text) or shouting or len(str(r.get("body") or "").split()) < 3:
            continue
        keep.append(r)
    keep.sort(key=lambda r: -len(str(r.get("body") or "")))
    return keep[:FEATURE_N]


def wall(doc):
    s = (doc or {}).get("summary") or {}
    n = int(s.get("count") or 0)
    if not n:
        return "", "", 0
    cards = []
    for r in featured(doc):
        d = _ts(r.get("created"))
        cards.append(
            f'<figure class="rv"><div class="st" aria-label="{int(r.get("stars") or 0)} of 5 stars">{"★" * int(r.get("stars") or 0)}<span>{"★" * (5 - int(r.get("stars") or 0))}</span></div>'
            f'{"<h3>" + esc(r.get("title")) + "</h3>" if r.get("title") else ""}'
            f'<blockquote>{esc(r.get("body"))}</blockquote>'
            f'<figcaption><b>{esc(r.get("name"))}</b><small>Paying member · {esc(_day(r.get("created")) + (", " + str(d.year) if d else ""))}{" · edited" if r.get("edited") else ""}</small></figcaption></figure>')
    br = s.get("breakdown") or {}
    bars = "".join(f'<li><span>{k}★</span><i style="--v:{round(100 * int(br.get(k) or 0) / n, 1)}%"></i><b>{int(br.get(k) or 0)}</b></li>' for k in ("5", "4", "3", "2", "1"))
    rating = (f'<p class="rating"><b>★ {float(s.get("average")):.1f}</b><span>from {n} member reviews</span></p>'
              if n >= 10 and s.get("average") is not None else f'<p class="rating"><span>{n} member reviews</span></p>')
    head = f'{rating}<ul class="brk">{bars}</ul>'
    return head, "".join(cards), n


# --------------------------------------------------------------------------------------------------------------------- build

def sports_on():
    try:
        import ui_v3
        return ui_v3.sports_on().split()
    except Exception:
        return list(SPORTS)


def _list(names):
    return names[0] if len(names) < 2 else ", ".join(names[:-1]) + " and " + names[-1]


def build(srv, join_url, fetch=True):
    """The page bytes from the inputs above. Pure assembly: escaping, no computation beyond counting."""
    inp = _inputs(srv, fetch)
    sp = sports_on()
    rows, meta, nrows, live = terminal(inp["board"]) if inp["board"] else ("", "", 0, False)
    cells, total, asof, since = ledger(inp["record"])
    head, cards, nrv = wall(inp["reviews"])
    proof = []
    if total:
        proof.append(f'<span><b>{total:,}</b> decided picks on the public record</span>')
    if nrv:
        proof.append(f'<span><b>{nrv}</b> member reviews, every one published</span>')
    mi = minis(inp["board"]) if inp["board"] else {}
    tools = (os.environ.get("SCOUT_MARKET_TOOLS") == "1", os.environ.get("SCOUT_PICKEM_EV", "").strip() == "1")
    fill = {
        "JOIN_URL": esc(join_url),
        "SPORT_CHIPS": "".join(f'<li data-sport="{s}">{LABEL[s]}</li>' for s in sp),
        "SPORT_LIST": esc(_list([LONG[s] for s in sp])),
        "SPORT_N": str(len(sp)),
        "BOOK_N": str(len(BOOKS)),
        "BOOK_CHIPS": "".join(f"<li>{b}</li>" for b in BOOKS),
        "PROOF": " <i>·</i> ".join(proof),
        "TERM_ROWS": rows,
        "TERM_META": esc(meta),
        "TERM_STATE": "dated" if nrows else "empty",
        "TERM_AT": esc(inp["board"].get("board_at") or "") if nrows else "",
        "TERM_TITLE": "CS2 board" if nrows else "The member board",
        "FRESH_MIN": str(FRESH_MIN),
        "TERM_N": str(nrows),
        "LEDGER": cells,
        "LEDGER_ASOF": esc(asof),
        "LEDGER_SINCE": esc(since),
        "LEDGER_TOTAL": f"{total:,}" if total else "",
        "RV_HEAD": head,
        "RV_CARDS": cards,
        "RV_N": str(nrv),
        "GAP_MINI": mi.get("GAP", ""), "MOVE_MINI": mi.get("MOVE", ""), "BE_MINI": mi.get("BE", ""),
        "TOOLS_FLAG": (" has-ev" if tools[1] else "") + (" has-arb" if tools[0] else ""),
    }
    out = _template()
    # optional blocks: <!--if:NAME--> ... <!--/if:NAME--> kept only when NAME has content
    for name, on in (("TERM", nrows > 0), ("NOTERM", nrows == 0), ("LEDGER", bool(cells)), ("REVIEWS", bool(nrv)),
                     ("PROOF", bool(proof)), ("EV", tools[1]), ("ARB", tools[0]),
                     ("GAP", "GAP" in mi), ("MOVE", "MOVE" in mi), ("BE", "BE" in mi)):
        out = re.sub(rf"<!--if:{name}-->(.*?)<!--/if:{name}-->", (lambda m: m.group(1)) if on else "", out, flags=re.S)
    out = re.sub(r"\{\{([A-Z_0-9]+)\}\}", lambda m: fill.get(m.group(1), ""), out)
    return out.encode("utf-8")


def page(srv, join_url):
    """The current page bytes (one object per build). Request path: never computes; at most one background rebuild runs."""
    key = (join_url, " ".join(sports_on()), _mt(), os.environ.get("SCOUT_PUBLIC_RECORD"))
    pg = _STATE["page"]
    if pg is None or _STATE["key"] != key:
        with _LOCK:
            if _STATE["page"] is None or _STATE["key"] != key:
                _STATE.update(page=build(srv, join_url, fetch=False), built=time.time(), made=time.time(), key=key)
                _kick(srv, join_url)
        return _STATE["page"]
    age = time.time() - _STATE.get("made", 0.0)
    if age > STALE_S and not _BUILDER["busy"]:
        with _LOCK:
            if time.time() - _STATE.get("made", 0.0) > STALE_S:
                new = build(srv, join_url, fetch=False)
                _STATE["page"] = pg = new if new != _STATE["page"] else _STATE["page"]
                _STATE["made"] = time.time()
    if time.time() - _STATE["built"] > REBUILD_S:
        _kick(srv, join_url)
    return _STATE["page"]


_BUILDER = {"busy": False}


def _kick(srv, join_url):
    with _LOCK:
        if _BUILDER["busy"]:
            return
        _BUILDER["busy"] = True
        _STATE["built"] = time.time()                 # no second kick while this one runs

    def run():
        t0 = time.time()
        try:
            new = build(srv, join_url, fetch=True)
            with _LOCK:
                if _STATE.get("made", 0.0) > t0:
                    return                            # a newer page was made meanwhile: never put an older one back
                if new != _STATE["page"]:
                    _STATE["page"] = new              # identical bytes keep the old object (no re-gzip, same ETag)
                _STATE["built"] = _STATE["made"] = time.time()
        except Exception:
            pass
        finally:
            _BUILDER["busy"] = False
    threading.Thread(target=run, daemon=True, name="landing-v3").start()


def reset():
    """Tests: forget the built page and inputs."""
    _STATE.update(page=None, built=0.0, made=0.0, key=None, board=(None, None), last={})
    _TPL.update(mtime=None, text="")
