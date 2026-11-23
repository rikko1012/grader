"""The v3 signed-out landing page (Prop Grader, owner brief + DESIGN_RULES.md, Nov 23 2026): web/v3/landing.html filled server-side
with real data. Hero = a browser frame (the member board) + a phone frame (one prop card), then proof strip, how it works, Inside rows,
why table, Slip Studio stage, public record, members, pricing, FAQ, final call to action.

Served instead of web/landing.html + the v3 overlay when v3 is on (server.py, the two landing send sites). web/landing.html is
untouched: flag-off visitors keep its exact bytes (tests/test_ui_v3_gate.py).

The page is VIEW-ONLY (owner access rule: visitors get no usable product for free). Everything product-shaped is a static picture of
the member app, tagged EXAMPLE: no row that opens, no app toggle, no slider, no buildable slip, no calculator. Every number on it comes
from a body the server already keeps for its own public routes - never typed into the template:
- the browser frame: up to HERO_ROWS rows of the CS2 board snapshot members read (workspace_api.snapshot, the shared 5 s board),
  chosen by a stated rule (frame_pick: one lettered market per player in the neutral rule order - markets both apps list first,
  then line gaps, moved lines, the soonest - with ANY letter, so the frame shows the grader saying no as well as yes and is never a
  list of the board's qualifying plays), each app's line, the pick, the model estimate and the grade. One row is highlighted and
  shown first, chosen by the same neutral order only (critic round 3, access rule): the first two-app market, any letter, never
  because it clears its break-even, and the board row prints no break-even. A live board with fewer than FRAME_MIN rows: the saved
  board (FALLBACK, dated), through the same code;
- the phone frame: the highlighted row's own card lines (match, player, side + line, the chance at the line on its app, the grade,
  the projection, the last 10 at this line, the break-even for the entry, the other app's line); a value the row lacks is left out,
  never shown as 0;
- the line-gap panel: the first eligible row of the LIVE board in rule order (another player than the phone's first) whose two apps list
  different lines (opening lines when known), else no panel;
- the CS2 map bars: one live-board player picked by a fixed rule (pool_subject), his HLTV kills per map (hltv_maps.for_player, the
  member player card's read and cache); no subject or any error drops the panel;
- the Slip Studio stage's one real card: the first frame row in rule order other than the phone's (and the gap panel's when it
  can); the other sports' cards are examples whose values are a lock (no number in the source);
- the record table and count: the same shared answer /api/record serves (key and compute identical), per sport;
- the reviews: the same shared answer /api/reviews serves (newest first page), member text escaped here, handles left off;
- the frame's "Showing N of M" line: letter-graded CS2 markets on the live member board, printed only from MORE_MIN (a small
  number on a quiet slate would read as an empty product; the final call to action repeats the H1 and prints no count).

Capacity (700 visitors at once): the request path only returns the last built bytes object (send_shared keys its ETag + gzip on
that object's identity). One builder at a time rebuilds it at most every REBUILD_S seconds in a background thread, from cached
bodies only; a missing input is asked for in that thread (single flight through srv.shared), never on a visitor's request. A
missing or failed input drops its block; the page itself never fails over one.
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
# A saved CS2 board (real rows from the Nov 22, 17:53 UTC member board, trimmed to the fields the frames read). Shown only when the
# live snapshot has no row to show (the frames then read it exactly as saved).
FALLBACK = ROOT / "web" / "v3" / "landing-fallback.json"
# The page CSS restates the live default-theme values itself (tokens.css is the member pages' file and is not inlined here).
ASSETS = {"PAGE_CSS": ROOT / "web" / "v3" / "landing-page.css", "PAGE_JS": ROOT / "web" / "v3" / "landing-page.js"}
STALE_S = 900                      # a page not rebuilt for this long (no visitors) is rebuilt from cached bodies before serving
REBUILD_S = 120                    # the page changes at most this often (frame rows, ledger, reviews)
BOARD_READ = True                  # tests switch this off (no feed read from the background builder)
SPORTS = ("cs2", "lol", "nba", "nfl")
LABEL = {"cs2": "CS2", "lol": "LoL", "nba": "NBA", "nfl": "NFL", "soccer": "Soccer", "tennis": "Tennis"}
LONG = {"cs2": "CS2", "lol": "League of Legends", "nba": "NBA", "nfl": "NFL", "soccer": "Soccer", "tennis": "Tennis"}
SHORT = {"underdog": "UD", "prizepicks": "PP"}
APP_ORDER = {"prizepicks": 0, "underdog": 1}     # the frame's "Line by app" cell reads PP first, then UD (the owner mockup)
HERO_ROWS = 6                      # the browser frame shows up to 6 rows (critic round 6: the board reaches the phone's bottom;
                                   # a thinner board is held to a 48 px phone overhang by CSS, .pcol stretch)

# Review featuring rule: the reviews that talk about using the product (stats, reasons, breakdowns, design, navigation...). A review
# with photos (they are slips / calendars) or shouting stays on /reviews only; a sentence with money, result, betting or guarantee talk
# is never quoted (the quote shows an ellipsis where a sentence was left out).
_MONEY = re.compile(r"profit|money|\$|cash|paid|payout|\bwon\b|\bwin|hit big|\bbank|rarely miss|every (single )?day|\bmf\b|"
                    r"\bf+u+c+k|\bshit|\bbitch|\bdamn|guarantee|\blocks?\b|\bbets?\b|\bbetter\b|\bslips?\b|\bparlay|10 ?/ ?10|outta|no cap|\btwin\b", re.I)
_PRODUCT = re.compile(r"stat|breakdown|reason|navigat|design|analy[sz]|matchup|information|\bcards?\b|\bmaps?\b|projection|grader|"
                      r"\bboard\b|explain|\bdata\b|\btools?\b", re.I)
FEATURE_N = 3
# The landing's brand is Prop Grader. A review that names the site's old name (CS2 Scout, 1time, 1T) is never featured on the landing;
# it stays on /reviews word for word. A member's text is never edited.
_OLD_BRAND = re.compile(r"\bscout\b|1time|\b1T\b", re.I)

# Pricing and the trial (owner blueprint, Nov 22 2026; read from the public Whop checkout pages on Nov 23): the free-trial plan
# (server.LANDING_TRIAL_URL) says "1 day free, then $24.99 per month"; Weekly plan_4zOzRl7roBDFf = $9.99/week. These are the only
# prices on the page; the template itself carries no "$" (tests/test_landing_v3.py).
PRICE_M = "$24.99"
PRICE_W = "$9.99"
# The only numbers on the page that do not come from the site's own data: the owner's two posts, word for word, always with their
# attribution (owner brief Nov 23: "+46U on Grader 4 mans in the past week ..." - Trey on X, Nov 20, 2026 - and the member 6-man
# story). Nothing else may be typed in (tests/test_landing_v3.py checks the page's other numbers come from data).
OWNER_UNITS = 46
# The line under the big +46U is the rest of the post's first clause, so the number is never printed twice (critic round 2); the
# "only been live 2 weeks" clause is left off (critic round 1: it read as a contradiction beside the multi-month record). Full post:
# "+46U on Grader 4 mans in the past week, and Grader has only been live 2 weeks."
OWNER_POST = "on Grader 4 mans in the past week, and Grader has only been live 2 weeks."
OWNER_POST_BY = "Trey on X, Nov 20, 2026"
OWNER_STORY = "A member ran his first ever 6 man with Grader and cashed."
OWNER_STORY_BY = "Shared by Prop Grader on X. One member, one night: a result, not a promise."
# The Slip Studio stage's other sports: no public sample rows exist for them, so they are labelled examples (owner brief 6b said
# "placeholder"; critic round 2: that word read as an unfinished site) and their value slot is a lock + "Members only" - never
# digits, never a made-up name or stat.
PLACEHOLDERS = (("nba", "NBA guard", "Over 24.5 Points", ""), ("lol", "LoL mid", "Over 7.5 Kills", "Maps 1-2"),
                ("tennis", "Tennis", "Over 21.5 Total games", ""))

_STATE = {"page": None, "built": 0.0, "key": None, "board": (None, None), "last": {}}
_LOCK = threading.RLock()
_TPL = {"mtime": None, "text": ""}


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def _mt():
    def m(f):
        try:
            return f.stat().st_mtime
        except OSError:
            return None
    return (TEMPLATE.stat().st_mtime,) + tuple(f.stat().st_mtime for f in ASSETS.values()) + (m(FALLBACK),)


def _template():
    """The template with the page CSS and JS inlined (one request for the whole first screen). Re-read on change."""
    mt = _mt()
    if _TPL["mtime"] != mt:
        text = TEMPLATE.read_text(encoding="utf-8")
        for k, f in ASSETS.items():
            body = f.read_text(encoding="utf-8")
            if f.suffix == ".css":              # source comments are for the repo, not view-source (tokens.css names the old brand)
                body = re.sub(r"(?s)/\*.*?\*/", "", body)
            text = text.replace("{{" + k + "}}", body)
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


def _date(dt):
    """'Nov 23, 2026'."""
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}" if dt else ""


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


# ---------------------------------------------------------------------------------------------------------------- the frames

def open_only(board, now=None):
    """The live board with only the rows whose match has not started (lock after now): a shallow copy, records filtered. The hero
    rows, the tool tiles, the by-hand example and the "Move the line" seed all read this, so a board that is hours old never shows a
    line nobody can play any more. The saved board (FALLBACK) is never passed through here: it is shown dated, as a saved sample."""
    if not board:
        return board
    now = now or datetime.now(timezone.utc)
    keep = []
    for r in board.get("records") or []:
        ev = r.get("event") or {}
        t = _ts(ev.get("lock_utc") or ev.get("start_utc"))
        if t is not None and (t if t.tzinfo else t.replace(tzinfo=timezone.utc)) > now:
            keep.append(r)
    return dict(board, records=keep)


def _groups(board):
    """Every eligible market by the stated rule: graded standard CS2 props with a lock after the board's own time, one group per
    player/stat/maps market (every app's line on it), the lettered line first. [(priority, lock, rows, gap, moved)] in rule order:
    line gaps, then moved lines, then the soonest."""
    recs = (board or {}).get("records") or []
    at = _ts((board or {}).get("board_at"))
    groups = {}
    for r in recs:
        sc, mk, ev = r.get("scout") or {}, r.get("market") or {}, r.get("event") or {}
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
    return out


def _rows(board, n=6):
    """Terminal rows by the stated rule (_groups), one per player. Returns (rows, board time, how many letter-graded markets the
    board holds in all)."""
    out = _groups(board)
    seen, pick = set(), []
    for pri, lock, rs, gap, moved in out:
        who = (rs[0].get("player") or {}).get("key")
        if who in seen:
            continue
        seen.add(who)
        pick.append((rs, gap, moved))
        if len(pick) == n:
            break
    return pick, _ts((board or {}).get("board_at")), len(out)


# The frame's rows (critic round 2 replaces round 1's filter): one lettered market per player, any letter, picked in the neutral rule
# order with markets both apps list first (the hero promises PrizePicks AND Underdog), at most HERO_ROWS. Round 1 kept only rows that
# clear their own break-even, which turned the free page into a list of the board's qualifying plays (owner access rule). Round 3:
# the highlight (the phone's row, shown first, tinted) is no longer chosen for clearing its break-even either - it is simply the
# first two-app market in that order (else the first row), any letter, and the board row prints no break-even (the phone's Why list
# keeps it, beside the projection and the last 10). A live board with fewer than FRAME_MIN rows shows the saved board (dated).
TIER = {"A+": 0, "A": 1, "A-": 2, "B+": 3, "B": 4}
FRAME_MIN = 2
MORE_MIN = 25                      # "Showing 4 of N graded props" only from this many (a quiet slate never reads as an empty product)


def _clears(rs):
    lead = rs[0]
    sc, par = lead.get("scout") or {}, lead.get("parity") or {}
    try:
        return sc.get("grade") in TIER and par.get("be") is not None and float(sc.get("p_side")) > float(par.get("be"))
    except (TypeError, ValueError):
        return False


def _apps(rs):
    return {(r.get("offer") or {}).get("platform") for r in rs if (r.get("offer") or {}).get("line") is not None}


def frame_pick(board, n=HERO_ROWS):
    """([(rows, gap, moved)], highlighted) for the browser frame by the rule above; the highlighted row (if any) first."""
    gs = sorted(_groups(board), key=lambda g: 0 if len(_apps(g[2])) > 1 else 1)        # stable: rule order inside each half
    seen, pick = set(), []                           # the highlight = gs[0]: the first two-app market (else the first row)
    for g in gs:
        _pri, _lock, rs, gap, moved = g
        who = (rs[0].get("player") or {}).get("key")
        if who in seen:
            continue
        seen.add(who)
        pick.append((rs, gap, moved))
        if len(pick) == n:
            break
    return pick, bool(pick)


def _l10(r):
    h = ((r.get("history") or {}).get("l10")) or {}
    side = (r.get("scout") or {}).get("side")
    n, over, under = h.get("n"), h.get("over"), h.get("under")
    if not n or over is None:
        return None
    return (over if side == "over" else under), n


def gcls(g):
    """The badge tier: A and B tiers (A+ .. B) cyan outline; B- and below (and a Lean line) gray. Never green."""
    g = str(g or "")
    return "gc" if g[:1] == "A" or g in ("B+", "B") else "gg"


def _grade_text(grade, side):
    """The grade cell's words: the letter, else 'Lean Over' / 'Lean Under' (a line under the B- cut keeps Lean wording, never a dash)."""
    return grade or (f"Lean {side}" if side else "")


def _by_app(rs):
    return sorted(rs, key=lambda r: (APP_ORDER.get((r.get("offer") or {}).get("platform"), 9), str((r.get("offer") or {}).get("platform"))))


def _side(r):
    return "Over" if (r.get("scout") or {}).get("side") == "over" else "Under"


def _tag(r):
    of = r.get("offer") or {}
    return SHORT.get(of.get("platform"), esc(of.get("platform_label") or "")[:2].upper())


def _meter(p):
    """A 0-100% meter (aria-hidden; the number beside it carries the value): transform scaleX, never layout."""
    try:
        v = max(0.0, min(1.0, float(p)))
    except (TypeError, ValueError):
        v = 0.0
    return f'<span class="mt"><i style="transform:scaleX({v:.3f})"></i></span>'


def frame_rows(shown, hl=False):
    """The browser frame's rows (a picture inside an aria-hidden frame): player + prop | each app's line | pick | model estimate +
    meter | grade. Lines print exactly as the member board prints them. hl: the first row is the highlighted one (tinted; the
    board row prints no break-even)."""
    out = []
    for i, (rs, _gap, _moved) in enumerate(shown):
        lead = rs[0]
        pl, mk, sc = lead.get("player") or {}, lead.get("market") or {}, lead.get("scout") or {}
        p = _pct(sc.get("p_side"))
        lines = "".join(f'<span>{_tag(r)} {esc(_num((r.get("offer") or {}).get("line")))}</span>' for r in _by_app(rs))
        g = sc.get("grade") or ""
        on = bool(hl and i == 0)
        est = f'<span class="b-p"><b>{esc(p)}%</b>{_meter(sc.get("p_side"))}</span>'
        out.append(f'<div class="br{" hl" if on else ""}"><span class="b-who"><b>{esc(pl.get("name"))}</b>'
                   f'<small>{esc(mk.get("stat_label"))} · {esc(mk.get("period_label"))}</small></span>'
                   f'<span class="b-ln">{lines}</span><span class="b-pk">{_side(lead)}</span>'
                   f'{est}<span class="b-g"><i class="gb {gcls(g)}">{esc(_grade_text(g, _side(lead)))}</i></span></div>')
    return "".join(out)


def frame_sr(shown):
    """The frame's rows in words, for screen readers (the frame itself is aria-hidden)."""
    parts = []
    for rs, _gap, _moved in shown:
        lead = rs[0]
        pl, mk, sc = lead.get("player") or {}, lead.get("market") or {}, lead.get("scout") or {}
        lines = ", ".join(f'{(r.get("offer") or {}).get("platform_label")} {_num((r.get("offer") or {}).get("line"))}' for r in _by_app(rs))
        parts.append(f'{pl.get("name")}, {mk.get("stat_label")} {mk.get("period_label")}, {lines}, {_side(lead)}, '
                     f'model estimate {_pct(sc.get("p_side"))}%, grade {_grade_text(sc.get("grade") or "", _side(lead))}')
    return esc("Example rows from the member board: " + "; ".join(parts) + ".") if parts else ""


def _entry_short(entry, app):
    """'PrizePicks Power 2-pick' -> 'Power 2-pick' (the row already names its app)."""
    e = str(entry or "")
    return e[len(app):].strip() if app and e.lower().startswith(app.lower() + " ") else e


def phone(shown):
    """The phone frame's fills from the first displayed row ({} = no rows). Each 'Why' line only when the row has its value."""
    if not shown:
        return {}
    rs = shown[0][0]
    lead = rs[0]
    ev, pl, mk, sc, of, par = (lead.get(k) or {} for k in ("event", "player", "market", "scout", "offer", "parity"))
    app, line, side = of.get("platform_label") or "", _num(of.get("line")), _side(lead)
    p, be, l10, proj = _pct(sc.get("p_side")), _pct(par.get("be")), _l10(lead), sc.get("projection")
    why = []
    if isinstance(proj, (int, float)) and not isinstance(proj, bool) and proj > 0:
        why.append(("Projection", f"{float(proj):.1f} vs {line}"))
    if l10 and l10[1]:
        why.append(("Last 10 at this line", f"{l10[0]} / {l10[1]} {side}"))
    if be:
        e = _entry_short(par.get("entry"), app)
        why.append((f"Break-even{', ' + e if e else ''}", f"{be}%"))
    other = next((r for r in rs[1:] if (r.get("offer") or {}).get("platform") != of.get("platform")
                  and (r.get("offer") or {}).get("line") is not None), None)
    if other:
        why.append((f'{(other.get("offer") or {}).get("platform_label") or ""} line', _num((other.get("offer") or {}).get("line"))))
    # The brief's Why list is exactly these four (critic round 6 removed round 5's Stake / Verdict row: a members-only stake on a live
    # row is product given away, access rule 0.4). The other app's line carries class "oa" (hidden in the >=1024 phone, rulebook D21).
    g = sc.get("grade") or ""
    prop = f"{side} {line} {mk.get('stat_label') or ''} · {mk.get('period_label') or ''}"
    sr = (f"Example prop card: {pl.get('name')}, {prop.replace(' · ', ', ')} on {app}, model estimate {p}% at the {line} line, grade "
          f"{_grade_text(g, side)}." + "".join(f" {k}: {v}." for k, v in why))
    return {
        "PH_MATCH": esc(ev.get("label") or ""), "PH_WHO": esc(pl.get("name")),
        "PH_PROP": esc(prop), "PH_APP": esc(app),
        "PH_LINE": esc(line), "PH_P": esc(p), "PH_G": esc(_grade_text(g, side)), "PH_GCLS": gcls(g),
        "PH_METER": _meter(sc.get("p_side")),
        "PH_WHY": "".join(f'<li{" class=oa" if other and i == len(why) - 1 else ""}><span>{esc(k)}</span><b>{esc(v)}</b></li>'
                          for i, (k, v) in enumerate(why)),
        "PH_SR": esc(sr),
    }


def gap_panel(pick, skip=None):
    """The 'apps disagree' panel from the first row in rule order whose two apps list different lines ({} = no panel; never
    chosen for clearing its break-even, critic round 3). skip = the phone's player key: another player's gap leads when there is
    one, so the page never repeats one play in every section (critic round 2)."""
    for rs, gap, _m in sorted(pick, key=lambda x: 1 if skip and (x[0][0].get("player") or {}).get("key") == skip else 0):
        plats = {(r.get("offer") or {}).get("platform") for r in rs}
        if not gap or len(plats) < 2:
            continue
        lead = rs[0]
        mk, pl, of, sc = (lead.get(k) or {} for k in ("market", "player", "offer", "scout"))
        two = _by_app(rs)[:2]
        tiles = []
        for r in two:
            o = r.get("offer") or {}
            mv = o.get("moved_from")
            tiles.append(f'<div class="lt"><span>{esc(o.get("platform_label"))}</span><b>{esc(_num(o.get("line")))}</b>'
                         + (f'<small>Opened {esc(_num(mv))}</small>' if mv is not None else "") + '</div>')
        ls = [float((r.get("offer") or {}).get("line")) for r in two]
        d = f"{abs(ls[0] - ls[1]):.1f}"
        pick_t = f"{_side(lead)} {_num(of.get('line'))} on {of.get('platform_label')}"
        sr = (f"Example: {pl.get('name')}, {mk.get('stat_label')} {mk.get('period_label')}. "
              + ", ".join(f'{(r.get("offer") or {}).get("platform_label")} line {_num((r.get("offer") or {}).get("line"))}' for r in two)
              + f". Line gap {d}. {pick_t}: {_pct(sc.get('p_side'))}% model estimate.")
        return {"GAP_KEY": pl.get("key"), "GAP_WHO": esc(f"{pl.get('name')} · {mk.get('stat_label')} · {mk.get('period_label')}"), "GAP_TILES": "".join(tiles),
                "GAP_D": d, "GAP_PICK": esc(pick_t), "GAP_P": esc(_pct(sc.get("p_side"))), "GAP_METER": _meter(sc.get("p_side")),
                "GAP_SR": esc(sr)}
    return {}


def slip_real(pick, skip=None):
    """The Slip Studio stage's real card: the first lettered frame row in rule order other than the phone's (the first row) and,
    when another exists, other than skip (the gap panel's player) - so the page never repeats one play everywhere. Never chosen by
    grade or estimate (critic round 3: no merit-picked play on the free page)."""
    cands = [rs[0] for rs, _g, _m in (pick[1:] or pick)
             if (rs[0].get("scout") or {}).get("grade") and (rs[0].get("scout") or {}).get("p_side") is not None]
    if not cands:
        return {}
    best = next((r for r in cands if skip is None or (r.get("player") or {}).get("key") != skip), cands[0])
    pl, mk, sc, of = (best.get(k) or {} for k in ("player", "market", "scout", "offer"))
    return {"SS_WHO": esc(pl.get("name")), "SS_PROP": esc(f"{_side(best)} {_num(of.get('line'))} {mk.get('stat_label')}"),
            "SS_PER": esc(mk.get("period_label") or ""), "SS_P": esc(_pct(sc.get("p_side"))), "SS_G": esc(sc.get("grade")),
            "SS_GCLS": gcls(sc.get("grade"))}


ICONS = {"cs2": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.25"/><path d="M8 1.5v4M8 10.5v4M1.5 8h4M10.5 8h4"/></svg>',
         "lol": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.75l5.4 3.12v6.26L8 14.25l-5.4-3.12V4.87z"/></svg>',
         "nba": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25"/><path d="M1.75 8h12.5M8 1.75v12.5M3.6 3.6c2.3 2.3 2.3 6.5 0 8.8M12.4 3.6c-2.3 2.3-2.3 6.5 0 8.8"/></svg>',
         "tennis": '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25"/><path d="M3.3 3.7c2.5 2.3 2.5 6.3 0 8.6M12.7 3.7c-2.5 2.3-2.5 6.3 0 8.6"/></svg>'}


LOCK = ('<svg class="lk" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="1.5"/>'
        '<path d="M5.5 7V5a2.5 2.5 0 015 0v2"/></svg>')


def slip_cards(real):
    """(board cards, slip slots): the real CS2 card first (its value labelled 'model estimate'), then the examples (the role on the
    name line: 'NBA guard', 'LoL mid', 'Tennis'; the stage's EXAMPLE tag and caption say what they are, critic round 3 cut the
    per-card chips). An example's value slot is a lock only (never a value in the page source) and it carries no meter. Each board
    card sits in a box whose dashed 'Added to slip' outline shows once the card has flown, so the end frame never repeats the four
    cards side by side; each slot holds a copy of its card (the still page shows the final frame: every slot filled)."""
    cards = [f'<span class="si">{ICONS["cs2"]}</span><span class="sw"><b>{real["SS_WHO"]}</b>'
             f'<small>{real["SS_PROP"]}{" · " + real["SS_PER"] if real["SS_PER"] else ""}</small></span>'
             f'<span class="sv"><span class="sp"><span><b>{real["SS_P"]}%</b><i class="gb {real["SS_GCLS"]}">{real["SS_G"]}</i></span>'
             f'<small>model estimate</small></span></span>']
    for sp, who, prop, per in PLACEHOLDERS:
        cards.append(f'<span class="si">{ICONS[sp]}</span><span class="sw"><b>{who}</b>'
                     f'<small>{prop}{" · " + per if per else ""}</small></span>'
                     f'<span class="sv mo">{LOCK}</span>')
    cls = lambda i: "sc" if i == 0 else "sc ph"          # noqa: E731
    board = "".join(f'<div class="sbx" data-i="{i}"><div class="{cls(i)}">{c}</div></div>' for i, c in enumerate(cards))
    slots = "".join(f'<div class="slot" data-i="{i}"><div class="{cls(i)}">{c}</div></div>' for i, c in enumerate(cards))
    return board, slots


def feeds_label(shown):
    """(board subtitle, line column header) from the apps the SHOWN rows list (critic round 2: never "each app's own line" over one
    app): two or more apps -> ('PrizePicks + Underdog, graded at each app's own line', 'Line by app'); one -> ("Graded at
    Underdog's line", 'Line'); none -> ('', 'Line')."""
    have = {}
    for rs, _g, _m in shown:
        for r in rs:
            of = r.get("offer") or {}
            if of.get("platform") and of.get("line") is not None:
                have.setdefault(of.get("platform"), of.get("platform_label") or of.get("platform"))
    names = [have[k] for k in sorted(have, key=lambda k: (APP_ORDER.get(k, 9), k))]
    if len(names) > 1:
        return " + ".join(names) + ", graded at each app’s own line", "Line by app"
    if names:
        return f"Graded at {names[0]}’s line", "Line"
    return "", "Line"


def fallback_board():
    """The saved board (FALLBACK), parsed once per file change; None when missing or unreadable."""
    try:
        mt = FALLBACK.stat().st_mtime
    except OSError:
        return None
    hit = _STATE["last"].get(("fallback",))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        doc = json.loads(FALLBACK.read_text(encoding="utf-8"))
    except Exception:
        return None
    _STATE["last"][("fallback",)] = (mt, doc)
    return doc


# ------------------------------------------------------------------------------------------------------------- the map bars

# The CS2 panel ("Built for CS2, not bolted on."): one real board player's HLTV kills per map, picked by a fixed rule (pool_subject):
#   candidates = the LIVE board's letter-graded standard CS2 Kills Maps 1-2 props that lock after the board's time and after now (never the saved
#   board: its line is not current), one per player per match, the lettered Underdog line first;
#   order = soonest lock, then the player's name (case-insensitive), then the event id;
#   the subject = the first candidate whose HLTV per-map totals (hltv_maps.for_player: exact HLTV id via hltv._player_ids, the
#   same read and 15-min cache the member player card uses) hold >= POOL_MIN_MAPS current-pool maps with >= POOL_MIN_N maps
#   each in the 12-month window; the panel shows the top POOL_SHOW of those by maps played, drawn as thin bars by kills per map
#   (highest first, the top bar cyan, the rest muted).
# HLTV only (owner 2026-11-20), never BO3.gg; a map missing a value is left out, never shown as 0. The current pool is
# hltv_maps.pool_for (the match's HLTV veto pool, else the last 7 days of HLTV vetoes, else Active Duty), as on the player card.
# Reads: the background builder (fetch=True) may ask hltv_maps.for_player for at most POOL_TRIES uncached players per build (its own
# single flight, bounded read slots, failures kept FAIL_TTL), and stops reading at the first failed read; a visitor's request (fetch=False) only peeks at cached answers. No
# subject, a read older than POOL_MAX_AGE_H, or any error: the panel is left out (the page still serves).
POOL_MIN_MAPS = 4
POOL_MIN_N = 8
POOL_SHOW = 7                      # the played pool, up to 7 maps (critic round 6)
POOL_TRIES = 8
POOL_MAX_AGE_H = 24


def _pool_candidates(board):
    """[(lock, record)] by the rule above (lettered Kills Maps 1-2, standard, ok, lock after the board's time), one per player per match."""
    recs = (board or {}).get("records") or []
    at = _ts((board or {}).get("board_at"))
    now = datetime.now(timezone.utc)
    best = {}
    for r in recs:
        sc, mk, ev, pl, of = r.get("scout") or {}, r.get("market") or {}, r.get("event") or {}, r.get("player") or {}, r.get("offer") or {}
        line = of.get("line")
        if (mk.get("stat") != "kills" or mk.get("period") != "maps12" or mk.get("line_type") != "standard" or not sc.get("grade")
                or (r.get("state") or {}).get("kind") != "ok" or not pl.get("name") or isinstance(line, bool)
                or not isinstance(line, (int, float)) or line <= 0):
            continue
        lock = _ts(ev.get("lock_utc") or ev.get("start_utc"))
        if lock is None or (at and lock <= at) or lock <= now:      # a kept (older) board never shows a match that has locked
            continue
        k = (ev.get("id"), pl.get("key") or pl.get("name"))
        rank = 0 if of.get("platform") == "underdog" else 1
        if k not in best or rank < best[k][0]:
            best[k] = (rank, lock, r)
    out = [(lock, r) for _rk, lock, r in best.values()]
    out.sort(key=lambda x: (x[0], str((x[1].get("player") or {}).get("name")).lower(), str((x[1].get("event") or {}).get("id"))))
    return out


def _pool_maps(doc, pool):
    """The current-pool maps of an HLTV doc with >= POOL_MIN_N maps and real totals: [(key, kills, maps)], most maps first."""
    win = (((doc or {}).get("windows") or {}).get("12m") or {}).get("maps") or {}
    out = []
    for k in pool:
        t = win.get(k) or {}
        n, kills = t.get("maps"), t.get("kills")
        if (isinstance(n, int) and not isinstance(n, bool) and isinstance(kills, int) and not isinstance(kills, bool)
                and n >= POOL_MIN_N and kills > 0):
            out.append((k, kills, n))
    out.sort(key=lambda x: (-x[2], -x[1] / x[2], x[0]))
    return out


def _match_id(r):
    """The board row's own match id (workspace_api's index: the row the member card reads), or None."""
    try:
        import workspace_api
        of = r.get("offer") or {}
        x = (workspace_api._SNAP.get("index") or {}).get((of.get("platform"), str(of.get("offer_id"))))
        return (x or {}).get("match_id")
    except Exception:
        return None


def _read_at(key):
    """When the cached HLTV answer under key was read (aware datetime), or None."""
    try:
        import hltv
        st = hltv.read_status(key) or {}
        return _ts(st.get("as_of")) if st.get("state") != "failed" else None
    except Exception:
        return None


def pool_subject(board, fetch):
    """(record, doc, maps, pool keys, read time) for the module by the rule above, or None. fetch=False: cached answers only."""
    import hltv
    import hltv_maps
    tries = 0
    now = datetime.now(timezone.utc)
    for _lock, r in _pool_candidates(board):
        pl = r.get("player") or {}
        nick, team = pl.get("name"), pl.get("team") or None
        key = hltv_maps._key(nick, team)
        doc = hltv._peek(key)
        memo = _STATE["last"].get(("pool",))
        if doc is hltv.MISS and not fetch and memo and memo[0] == key:
            doc = memo[1]                                # the last subject's answer (hltv's 15-min cache may have lapsed while idle)
        if doc is hltv.MISS:
            if not fetch or tries >= POOL_TRIES:
                continue
            tries += 1
            doc = hltv_maps.for_player(nick, team)
            if not isinstance(doc, dict) or doc.get("state") == "unavailable":
                tries = POOL_TRIES                       # HLTV is failing: no more reads this build (the rest of the page is not held up)
        if not isinstance(doc, dict) or doc.get("state") != "ok" or not doc.get("hltv_id") or doc.get("source") != "HLTV":
            continue
        read = _read_at(key)
        if read is None or (now - read).total_seconds() > POOL_MAX_AGE_H * 3600:
            continue
        pool, _src = hltv_maps.pool_for(_match_id(r))
        maps = _pool_maps(doc, pool)
        if len(maps) >= POOL_MIN_MAPS:
            _STATE["last"][("pool",)] = (key, doc)
            return r, doc, maps[:POOL_SHOW], pool, read
    return None


def pool_module(board, fetch):
    """The fills for the map bars ({} = no panel): kills per map, highest first; bar length = kills per map / the top map's."""
    try:
        got = pool_subject(board, fetch)
        if not got:
            return {}
        import hltv_maps
        r, doc, maps, _pool, _read = got
        pl = r.get("player") or {}
        rows = sorted(((k, kills / n) for k, kills, n in maps), key=lambda x: (-x[1], x[0]))
        top = rows[0][1]
        bars = "".join(f'<li{" class=top" if i == 0 else ""}><span>{esc(hltv_maps.display(k))}</span>'
                       f'<span class="kb"><span><i style="transform:scaleX({v / top:.3f})"></i></span></span><b>{v:.1f}</b></li>'
                       for i, (k, v) in enumerate(rows))
        sr = (f"Example: {pl.get('name')}, HLTV kills per map over the past year: "
              + ", ".join(f"{hltv_maps.display(k)} {v:.1f}" for k, v in rows) + ".")
        return {"POOL_WHO": esc(pl.get("name")), "POOL_BARS": bars, "POOL_SR": esc(sr)}
    except Exception:
        return {}


# ------------------------------------------------------------------------------------------------------------------ the ledger

def ledger(record):
    """(table rows html, decided total, as-of date). Decided = wins + losses (pushes apart, as /record counts them), so the proof
    number always ties to the table. Won / Lost / Push only: no hit rate, no break-even, no sparkline (the record page has the rest)."""
    if not record:
        return "", 0, ""
    rows, total, asof = [], 0, None
    for sp in SPORTS:
        d = record.get(sp)
        if not d:
            continue
        t = d["totals"]
        w, l, p = int(t.get("wins") or 0), int(t.get("losses") or 0), int(t.get("pushes") or 0)
        if not (w + l):
            continue                                     # nothing decided yet: the row is left out, never shown as 0 / 0
        total += w + l
        a = _ts(d.get("as_of"))
        asof = a if asof is None or (a and a < asof) else asof
        n = t.get("matches")
        rows.append(f'<tr><th scope="row"><span>{LONG[sp]}</span>{f"<small>{int(n):,} {esc(t.get("match_word") or "matches")}</small>" if n else ""}</th>'
                    f'<td class="w">{w:,}</td><td class="l">{l:,}</td><td class="p">{p:,}</td></tr>')
    if not rows:
        return "", 0, ""
    return "".join(rows), total, _date(asof)


def ledger_gap(record, sp):
    """The one line under the record table for the board's sports the table does not show (critic round 6: six sports are promised,
    the table proves four, and the gap must not read as a hidden record). Two cases, each only when it is true:
    - a sport the public record does not cover at all (not in SPORTS: soccer, tennis) -> "... graded on the board, not on the public
      record yet";
    - a record sport whose ledger LOADED with nothing decided -> "... no decided picks yet".
    A record sport whose read failed (no doc) is never named: that is an outage, not an empty record. '' when nothing is missing."""
    if not record:
        return ""
    off = [s for s in sp if s not in SPORTS]
    empty = []
    for s in sp:
        d = (record or {}).get(s)
        t = (d or {}).get("totals") if isinstance(d, dict) else None
        if isinstance(t, dict) and not (int(t.get("wins") or 0) + int(t.get("losses") or 0)):
            empty.append(s)
    out = []
    low = lambda xs: _list([LONG[x] if x in SPORTS else LONG[x].lower() for x in xs])      # noqa: E731 (mid-sentence: soccer, tennis)
    up = lambda t: t[:1].upper() + t[1:]                                                  # noqa: E731
    if off:
        names = up(low(off))
        out.append(f"{names} {'are' if len(off) > 1 else 'is'} graded on the board, not on the public record yet.")
    if empty:
        names = up(low(empty))
        out.append(f"{names} {'have' if len(empty) > 1 else 'has'} no decided picks yet.")
    return " ".join(out)


# ----------------------------------------------------------------------------------------------------------------- the reviews

def _sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(s or "").strip()) if x.strip()]


# critic round 4 (reverses D38): complete reviews first; when fewer than FEATURE_N are complete, an excerpt fills the row. An excerpt
# quotes whole sentences only (5+ words); critic round 6: it carries an ellipsis inside the quote marks on each side where sentences were
# left out (wall), and its caption links the full review on /reviews.


def curly(s):
    """Typographic apostrophes (rulebook 3): a straight ' between two letters becomes a right single quote. Run BEFORE esc() (esc
    turns ' into &#x27;)."""
    return re.sub(r"(?<=\w)'(?=\w)", "’", str(s or ""))


def featured(doc):
    """[(review, quote, cut, old)] by the stated rule: reviews that talk about using the product. A review with photos (slips) or
    shouting is not featured; a sentence with money / result / betting / guarantee talk is never quoted (cut = a sentence was left
    out: the quote is one run of consecutive whole sentences, and the card links the full review). Order: complete reviews first
    (the most product talk, then the longest); excerpts only fill the row up to FEATURE_N; fewer candidates = fewer cards. Different dates are preferred when the reviews have them. A review
    that names the site's old brand (_OLD_BRAND) is never featured (it stays on /reviews). old is always False (kept for callers)."""
    out = []
    for r in (doc or {}).get("reviews") or []:
        text = f'{r.get("title") or ""} {r.get("body") or ""}'
        letters = [c for c in text if c.isalpha()]
        shouting = len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > .6
        if r.get("photos") or shouting:
            continue
        sents = _sentences(r.get("body"))
        kept, runs = [], []                              # an excerpt is ONE run of consecutive sentences (never two joined over a gap)
        for x in sents:
            if _MONEY.search(x):
                kept = []
            else:
                if not kept:
                    runs.append(kept)
                kept.append(x)
        run = max(runs, key=lambda k: (len(_PRODUCT.findall(" ".join(k))), len(" ".join(k))), default=[])
        quote = " ".join(run)
        cut = len(run) < len(sents)
        hits = len(_PRODUCT.findall(quote))
        if not hits or len(quote.split()) < 5 or _OLD_BRAND.search(f'{text} {r.get("name") or ""}'):
            continue
        out.append((cut, -hits, -len(quote), r, quote))
    out.sort(key=lambda x: x[:3])

    def by_date(xs):                                     # one review per date first (when the dates differ), then the rest in order
        first, rest, days = [], [], set()
        for x in xs:
            d = str(x[3].get("created") or "")[:10]
            (rest if d in days else first).append(x)
            days.add(d)
        return sorted(first + rest, key=lambda x: x[:3]) if len(first) > 1 else xs
    whole = by_date([x for x in out if not x[0]])
    pick = (whole + by_date([x for x in out if x[0]]))[:FEATURE_N]   # complete reviews first; an excerpt only fills the row
    return [(r, q, cut, False) for cut, _h, _n, r, q in pick]


def wall(doc):
    """(head html, cards html, review count, average or None). Handles are left off (owner brief: no member names on the sales page);
    each card says 'Paying member, <date>'. The average shows only from 10 reviews. The head links to /reviews."""
    s = (doc or {}).get("summary") or {}
    n = int(s.get("count") or 0)
    if not n:
        return "", "", 0, None
    avg = s.get("average")
    avg = float(avg) if n >= 10 and avg is not None else None
    cards = []
    for r, quote, cut, _old in featured(doc):
        d = _ts(r.get("created"))
        more = ' <a class="tl" href="/reviews">Full review</a>' if cut else ""
        if cut:                                          # an excerpt says so inside the quote marks (critic round 6): an ellipsis on
            full = " ".join(_sentences(r.get("body")))   # each side where sentences were left out, so it never reads as the whole review
            quote = ("…\u00a0" if not full.startswith(quote) else "") + quote + ("\u00a0…" if not full.endswith(quote) else "")   # nbsp: never a lone …
        cards.append(f'<li><figure class="rq"><blockquote><p>&ldquo;{esc(curly(quote))}&rdquo;</p></blockquote>'
                     f'<figcaption>Paying member, {esc(_date(d))}{more}</figcaption></figure></li>')
    head = (f"from {n} reviews, all published" if avg is not None else "reviews, all published")
    return head, "".join(cards), n, avg


# --------------------------------------------------------------------------------------------------------------------- build

def sports_on():
    try:
        import ui_v3
        return ui_v3.sports_on().split()
    except Exception:
        return list(SPORTS)


def _list(names):
    return names[0] if len(names) < 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _origin(v):
    v = (v or "").strip().rstrip("/")
    return v if re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", v) else ""


def public_base():
    """The origin the landing's canonical / og:url / og:image / twitter:image name, never the request's Host header (the page is one
    cached body for everyone): LANDING_PUBLIC_URL (the brand's own domain, e.g. https://propsgrader.com - a landing-only setting, so
    Whop sign-in keeps PUBLIC_URL and its registered callback), else PUBLIC_URL. '' when neither is a valid https origin (the
    absolute-URL tags are then left out)."""
    return _origin(os.environ.get("LANDING_PUBLIC_URL")) or _origin(os.environ.get("PUBLIC_URL"))


def weekly_url(srv):
    try:
        return srv.landing_weekly_url()
    except Exception:
        return WEEKLY_URL


WEEKLY_URL = "https://whop.com/checkout/plan_4zOzRl7roBDFf"


def _sport_sub(sp):
    """'CS2, LoL, NBA, NFL, soccer and tennis' (mid-sentence: soccer / tennis lower case)."""
    return _list([LABEL[s] if s in ("cs2", "lol", "nba", "nfl") else LABEL[s].lower() for s in sp])


def build(srv, join_url, fetch=True):
    """The page bytes from the inputs above. Pure assembly: escaping, no computation beyond counting. join_url = the free-trial
    checkout every 'Try' button opens (server.landing_trial_url)."""
    inp = _inputs(srv, fetch)
    sp = sports_on()
    live = open_only(inp["board"])                    # live rows only while their match has not started (never a stale line)
    live_rows, _at, live_n = _rows(live, n=10 ** 6) if live else ([], None, 0)
    board, saved = live, False
    pick, hl = frame_pick(live) if live else ([], False)
    if len(pick) < FRAME_MIN:                         # too few live rows: the saved board (dated), through the same code
        fb = fallback_board()
        fpick, fhl = frame_pick(fb) if fb else ([], False)
        if len(fpick) >= FRAME_MIN or (fpick and not pick):
            board, pick, hl, saved = fb, fpick, fhl, True
    rows, total, asof = ledger(inp["record"])
    head, cards, nrv, avg = wall(inp["reviews"])
    shown = pick                                      # frame_pick puts the highlighted (phone) row first
    gap_src = live_rows or (_rows(board, n=10 ** 6)[0] if pick else [])     # the gap panel and map bars read the live board first
    skip = (shown[0][0][0].get("player") or {}).get("key") if shown else None
    ph, gp = phone(shown), gap_panel(gap_src, skip)
    real = slip_real(pick, gp.pop("GAP_KEY", None))
    cards_ss = slip_cards(real) if real else ("", "")
    pm = pool_module(live, fetch) if live_rows else {}  # live board only: the saved board's line is not current
    base = public_base()
    bdate = _date(_ts((board or {}).get("board_at"))) if pick else ""
    tot_s = f"{total:,}" if total else ""
    more_n = live_n - len(shown) if (pick and not saved and live_n >= MORE_MIN) else 0   # the saved file is not a board: no count
    units_s = f"+{OWNER_UNITS}U"
    fsub, flh = feeds_label(shown)
    ncols = 1 + bool(total) + bool(nrv)               # proof columns: +46U, the record count, the review score
    fill = {
        "TRIAL_URL": esc(join_url),
        "WEEKLY_URL": esc(weekly_url(srv)),
        "PRICE_M": esc(PRICE_M), "PRICE_W": esc(PRICE_W),
        "OWNER_UNITS": str(OWNER_UNITS), "OWNER_UNITS_S": esc(units_s), "OWNER_UNITS_W": str(len(units_s)),
        "OWNER_POST": esc(OWNER_POST), "OWNER_POST_BY": esc(OWNER_POST_BY),
        "PF_CLS": " pf-solo" if ncols == 1 else " pf-2" if ncols == 2 else "",
        "OWNER_STORY": esc(OWNER_STORY).replace("6 man", "6&nbsp;man"), "OWNER_STORY_BY": esc(OWNER_STORY_BY),
        "SPORT_SUB": esc(_sport_sub(sp)),
        "SPORT_PILL": esc(" · ".join(LABEL[s] for s in sp)),       # the hero pill (brief 2): "CS2 · LoL · NBA · NFL · Soccer · Tennis"
        "SPORT_LONG": esc(_list([LONG[s] if s in ("cs2", "lol", "nba", "nfl") else LONG[s].lower() for s in sp])),
        # the frame's sidebar: the long name, and the short one the 1024-1279 layout shows (a nav label never truncates)
        "SPORT_NAV": "".join(f'<li{" class=on" if s == "cs2" else ""}><span class="lg">{LONG[s]}</span><span class="sh">{LABEL[s]}</span></li>'
                             for s in sp),
        "FRAME_ROWS": frame_rows(shown, hl), "FRAME_SR": frame_sr(shown),
        "FRAME_SUB": esc(fsub), "FRAME_LN_H": esc(flh),
        "FRAME_MORE": esc(f"Showing {len(shown)} of {live_n:,} graded props on the board.") if more_n > 0 else "",
        "FRAME_CAP": esc(f"Example rows from the public sample board{', ' + bdate if bdate else ''}. "
                         "A model estimate is a chance, not a promise."),
        # phones show the phone frame only (one prop, no rows): the caption says so (critic round 6); CSS picks one per width
        "FRAME_CAP_PH": esc(f"Example prop from the public sample board{', ' + bdate if bdate else ''}. "
                            "A model estimate is a chance, not a promise."),
        **ph, **gp, **pm,
        "POOL_ROW": "" if pm else " solo",
        "SS_CARDS": cards_ss[0], "SS_SLOTS": cards_ss[1], "SS_WHO": real.get("SS_WHO", ""),
        "LEDGER_ROWS": rows, "LEDGER_ASOF": esc(asof),
        "LEDGER_TOTAL": tot_s, "LEDGER_GAP": esc(ledger_gap(inp["record"], sp)), "LEDGER_N": str(total), "LEDGER_W": str(len(tot_s)),
        "REC_SRC": esc(f"{re.sub(r'^https://', '', base)}/record") if base else "the public record",
        "RV_HEAD": head, "RV_CARDS": cards, "RV_N": str(nrv),
        # the one rating line beside the Members H2 (brief 8); the story card carries the 6-man post only (critic round 5)
        "RV_LINE": (f'<span aria-hidden="true"><span class="star">★</span>&nbsp;{avg:.1f} ·&nbsp;</span><span class="sr-only">Rated {avg:.1f} out of 5 from </span>'
                    f'{nrv} reviews, all published' if avg is not None else f"{nrv} reviews, all published"),
        "RQS_CLS": f" n{cards.count('<li>')}",
        # "in their words" only over members' own words: with no review card the band holds Prop Grader's post alone (critic round 6)
        "RV_H2": "Members, in their words." if cards else "Members.",
        "RV_BIG": (f'<span class="star" aria-hidden="true">★</span>&nbsp;{avg:.1f}' if avg is not None else str(nrv)),
        "RV_BIG_SR": esc(f"{avg:.1f} out of 5" if avg is not None else str(nrv)),
        "RV_PROOF": esc(f"from {nrv} member reviews. Every review is published, whatever its rating." if avg is not None
                        else "member reviews. Every review is published, whatever its rating."),
        "BASE": esc(base),
    }
    out = _template()
    # optional blocks: <!--if:NAME--> ... <!--/if:NAME--> kept only when NAME has content
    for name, on in (("FRAME", bool(pick)), ("PHONE", bool(ph)), ("GAP", bool(gp)), ("POOL", bool(pm)), ("SLIP", bool(real)),
                     ("LEDGER", bool(rows)), ("TOTAL", bool(total)), ("LGAP", bool(rows and ledger_gap(inp["record"], sp))), ("REVIEWS", bool(nrv)), ("RVCARDS", bool(cards)),
                     ("BASE", bool(base)), ("INSIDE", bool(gp or pm)), ("FMORE", bool(more_n > 0))):
        out = re.sub(rf"<!--if:{name}-->(.*?)<!--/if:{name}-->", (lambda m: m.group(1)) if on else "", out, flags=re.S)
    out = re.sub(r"\{\{([A-Z_0-9]+)\}\}", lambda m: fill.get(m.group(1), ""), out)
    return out.encode("utf-8")


def page(srv, join_url):
    """The current page bytes (one object per build). Request path: never computes; at most one background rebuild runs."""
    key = (join_url, weekly_url(srv), " ".join(sports_on()), _mt(), os.environ.get("SCOUT_PUBLIC_RECORD"), os.environ.get("PUBLIC_URL"),
           os.environ.get("LANDING_PUBLIC_URL"))
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
