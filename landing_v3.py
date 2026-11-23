"""The v3 signed-out landing page (direction C, "the line terminal"): web/v3/landing.html filled server-side with real data.

Served instead of web/landing.html + the v3 overlay when v3 is on (server.py, the two landing send sites). web/landing.html is
untouched: flag-off visitors keep its exact bytes (tests/test_ui_v3_gate.py).

Every number on the page comes from a body the server already keeps for its own public routes - never typed into the template:
- the live line terminal: the CS2 board snapshot members read (workspace_api.snapshot, the shared 5 s board), rows chosen by a
  stated rule (soonest graded props where two apps list different lines or a line moved, then the soonest graded props); each row
  opens in place (chance at exactly the line, projection vs line, last 10, break-even, a link to that grade's tier on /record)
  and the gate counts the board's other letter-graded markets. No live row: the saved board (FALLBACK), always with its as-of time;
- "Move the line": seeded from a real CS2 kills Maps 1-2 row, drawn only when its formula prints that row's own chance;
- the record ledger: the same shared answer /api/record serves (key and compute identical), per sport;
- the review wall: the same shared answer /api/reviews serves (newest first page), member text escaped here.
- the CS2 map module: one live-board player picked by a fixed rule (pool_subject), his HLTV per-map kills from hltv_maps.for_player
  (the member player card's read and cache) and the current pool from hltv_maps.pool_for; no subject or any error drops the module.

Capacity (700 visitors at once): the request path only returns the last built bytes object (send_shared keys its ETag + gzip on
that object's identity). One builder at a time rebuilds it at most every REBUILD_S seconds in a background thread, from cached
bodies only; a missing input is asked for in that thread (single flight through srv.shared), never on a visitor's request. A
missing or failed input drops its block (the terminal falls back to a dated screenshot); the page itself never fails over one.
Display only: nothing here is read by the grader, stakes, forecasts or settlement."""
import html
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "web" / "v3" / "landing.html"
# A saved CS2 board (real rows from the Nov 22, 17:53 UTC member board, trimmed to the fields the terminal reads). Shown only when the
# live snapshot has no row to show, always labelled with its own time (the browser says it relative to now: "Saved board from ...").
FALLBACK = ROOT / "web" / "v3" / "landing-fallback.json"
# A real Slip Studio slip (the latest 3-pick slip in slip_studio.db when saved, picked by that rule, never by result), as built: legs,
# lines, sides, chances, grades. Rendered server-side in the page's own v3 markup (no screenshot). No member id, no result.
SLIP = ROOT / "web" / "v3" / "landing-slip.json"
ASSETS = {"TOKENS_CSS": ROOT / "web" / "v3" / "tokens.css", "PAGE_CSS": ROOT / "web" / "v3" / "landing-page.css",
          "PAGE_JS": ROOT / "web" / "v3" / "landing-page.js"}
STALE_S = 900                      # a page not rebuilt for this long (no visitors) is rebuilt from cached bodies before serving
REBUILD_S = 120                    # the page changes at most this often (hero rows, ledger, reviews)
BOARD_READ = True                  # tests switch this off (no feed read from the background builder)
FRESH_MIN = 30                     # a board older than this is labelled "as of <time>", never "checked N min ago"
SPORTS = ("cs2", "lol", "nba", "nfl")
LABEL = {"cs2": "CS2", "lol": "LoL", "nba": "NBA", "nfl": "NFL", "soccer": "Soccer", "tennis": "Tennis"}
LONG = {"cs2": "CS2", "lol": "League of Legends", "nba": "NBA", "nfl": "NFL", "soccer": "Soccer", "tennis": "Tennis"}
# The apps and books named on the page are read live, from the member boards' own snapshots (apps_seen): a source is named only
# while its rows are on a board read in the last APPS_MAX_AGE_S. Two kinds, kept apart on the page (the product's own split,
# workspace_sports.PICKEM vs "shown for comparison"): the pick'em apps Slip Studio builds slips for, and the sportsbooks / markets whose
# lines the NBA / NFL boards show beside them for comparison. Only names on these two lists can appear (owner-approved names; Bovada rows
# are on the NBA / NFL boards and are deliberately never named - the landing's banned-word test keeps it off). Text only, no logos.
PICK_APPS = (("prizepicks", "PrizePicks"), ("underdog", "Underdog"))
COMPARE_BOOKS = (("fanduel", "FanDuel"), ("betrivers", "BetRivers"), ("unibet_au", "Unibet"), ("polymarket", "Polymarket"),
                 ("draftkings", "DraftKings"), ("kalshi", "Kalshi"))
APPS_MAX_AGE_S = 24 * 3600
SHORT = {"underdog": "UD", "prizepicks": "PP"}
# The "Move the line" widget: Scout's CS2 kills chance (grader.p_more with the live kills spread, chances capped either side) and the
# letter cut points (grader.grade_from_p; tests/test_landing_v3.py keeps the two in step). The widget is only drawn when this formula,
# at the seed row's own line, prints the same chance the board row shows (otherwise the block is dropped, never approximated).
KILLS_SPREAD = 1.4756515691194962
KILLS_CAP = 0.78
CUTS = ((0.80, "A+"), (0.74, "A"), (0.68, "A-"), (0.63, "B+"), (0.57, "B"), (0.55, "B-"))

# Review wall featuring rule (said on the page): the reviews that talk about using the product (stats, reasons, breakdowns, design,
# navigation...). A review with photos (they are slips / calendars) or shouting stays on /reviews only; a sentence with money, result,
# betting or guarantee talk is never quoted (the quote shows an ellipsis where a sentence was left out and links to the reviews page).
_MONEY = re.compile(r"profit|money|\$|cash|paid|payout|\bwon\b|\bwin|hit big|\bbank|rarely miss|every (single )?day|\bmf\b|"
                    r"\bf+u+c+k|\bshit|\bbitch|\bdamn|guarantee|\blocks?\b|\bbets?\b|\bbetter\b|\bslips?\b|\bparlay|10 ?/ ?10|outta|no cap|\btwin\b", re.I)
_PRODUCT = re.compile(r"stat|breakdown|reason|navigat|design|analy[sz]|matchup|information|\bcards?\b|\bmaps?\b|projection|grader|"
                      r"\bboard\b|explain|\bdata\b", re.I)
FEATURE_N = 3
# The landing's brand is Prop Grader. A review that names the site's old name (CS2 Scout, 1time, 1T) is featured only when fewer than
# FEATURE_N other reviews qualify, after them, and its card says it was written before the rename. Its text is the member's own and is
# never edited.
_OLD_BRAND = re.compile(r"\bscout\b|1time|\b1T\b", re.I)

# Pricing and the trial (owner blueprint, Nov 22 2026). Read from the public Whop checkout pages on Nov 23: the free-trial plan
# (server.LANDING_TRIAL_URL) says "1 day free, then $24.99 per month"; Monthly plan_8d2GVuU7671mD = $24.99/month; Weekly
# plan_4zOzRl7roBDFf = $9.99/week. All three are on the Plays product, whose live memberships (trialing included) unlock the site
# (whop_auth._row_paid). These are the only prices on the page; the template itself carries no "$" (tests/test_landing_v3.py).
PRICE_M = "$24.99"
PRICE_W = "$9.99"
# The only numbers on the page that do not come from the site's own data: two posts the owner shared, word for word, always shown
# with their attribution (owner blueprint: "Grader 4 mans: roughly +46U ..." from the Prop Grader X account, Nov 20 2026, and the
# member 6-man story). Nothing else may be typed in (tests/test_landing_v3.py checks the page's other numbers come from data).
OWNER_POST = "Grader 4 mans: roughly <b>+46U</b> in the past week, and Grader has only been live 2 weeks"
OWNER_POST_BY = "Prop Grader on X, Nov 20"
OWNER_STORY = "A member ran his first ever 6 man with Grader and cashed."
OWNER_STORY_BY = "One member, one night: a result, not a promise."

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
    return (TEMPLATE.stat().st_mtime,) + tuple(f.stat().st_mtime for f in ASSETS.values()) + (m(FALLBACK), m(SLIP))


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

def _rows(board, n=6):
    """Terminal rows by the stated rule: graded standard CS2 props with a lock after the board's own time, one row per
    player/stat/maps market (every app's line on it). Line gaps and moved lines first, then the soonest. Returns (rows, board time,
    how many letter-graded markets the board holds in all)."""
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
    seen, pick = set(), []
    for pri, lock, rs, gap, moved in out:
        who = (rs[0].get("player") or {}).get("key")
        if who in seen:
            continue
        seen.add(who)
        pick.append((rs, gap, moved))
        if len(pick) == n:
            break
    return pick, at, len(out)


def _l10(r):
    h = ((r.get("history") or {}).get("l10")) or {}
    side = (r.get("scout") or {}).get("side")
    n, over, under = h.get("n"), h.get("over"), h.get("under")
    if not n or over is None:
        return None
    return (over if side == "over" else under), n


def _gcls(g):
    g = str(g or "")
    return "ga" if g[:1] == "A" else "gb" if g[:1] == "B" else ""


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
    l10 = _l10(r)
    be = _pct((r.get("parity") or {}).get("be"))
    l10s = f"{l10[0]} of {l10[1]}" if l10 else ""
    seen, graded = _ts(of.get("seen_at")), _ts(sc.get("forecast_at"))
    data = (f' data-p="{esc(p)}" data-side="{side}" data-g="{esc(sc.get("grade") or "")}" data-line="{esc(_num(of.get("line")))}"'
            f' data-app="{esc(of.get("platform_label") or "")}" data-l10="{esc(l10s)}" data-h="{l10[0] if l10 else ""}" data-n="{l10[1] if l10 else ""}"'
            f' data-be="{esc(be or "")}" data-entry="{esc((r.get("parity") or {}).get("entry") or "")}"'
            f' data-seen="{seen.strftime("%H:%M") if seen else ""}" data-graded="{esc(_when(graded))}"')
    return (f'<button type="button" class="bk{" on" if lead else ""}"{data}'
            f' aria-pressed="{"true" if lead else "false"}"><b>{tag}</b><span>{esc(_num(of.get("line")))}</span>{mv}</button>')


def _span_cells(rs):
    out = []
    for i, r in enumerate(rs):
        cell = _offer_cell(r, i == 0)
        cell = re.sub(r'^<button type="button"', "<span", cell)
        cell = re.sub(r' (data-[a-z0-9]+|aria-pressed)="[^"]*"', "", cell)
        out.append(cell.replace("</button>", "</span>"))
    return "".join(out)


def minis(board):
    """The tool tiles' small product fragments, from real terminal rows (a line gap, a moved line, a break-even). {} = none."""
    pick = _rows(board)[0]
    out = {}
    for rs, gap, moved in pick:
        if gap and "GAP" not in out:
            out["GAP"] = _span_cells(rs) + '<em class="chip gap">Line gap</em>' + _who(rs[0])
        if moved and "MOVE" not in out:
            out["MOVE"] = _span_cells(rs) + _who(rs[0])
        be = _pct((rs[0].get("parity") or {}).get("be"))
        p = _pct((rs[0].get("scout") or {}).get("p_side"))
        if be and p and "BE" not in out:
            out["BE"] = (f'<span class="mt big" style="--p:{esc(p)}"><i></i><u style="--b:{esc(be)}"></u></span>'
                         f'<span class="lbl"><b>Model estimate {esc(p)}%</b><b>Break-even {esc(be)}%</b></span>'
                         f'<span class="num">{esc((rs[0].get("parity") or {}).get("entry") or "")}</span>' + _who(rs[0]))
    return out


def hand_minis(board, pair=None):
    """The by-hand table's real UI chips: (pills, meter, row text). Pills: the first terminal row with two apps' lines; when the CS2
    sample has none, `pair` (sports_pair: a real NBA / NFL row both pick'em apps list, labelled with its sport); else the first row's
    single line with wording that says so. Meter: the first terminal row's chance against its break-even."""
    pick = _rows(board, n=HERO_ROWS)[0] if board else []
    bar = ""
    if pick:
        rs0 = pick[0][0]
        p, be = _pct((rs0[0].get("scout") or {}).get("p_side")), _pct((rs0[0].get("parity") or {}).get("be"))
        bar = (f'<span class="mt big" style="--p:{esc(p)}" aria-hidden="true"><i></i><u style="--b:{esc(be)}"></u></span>'
               f'<span class="lbl"><b>Model estimate {esc(p)}%</b><b>Break-even {esc(be)}%</b></span>') if p and be else ""
    two = next((rs for rs, _g, _m in pick if len({(r.get("offer") or {}).get("platform") for r in rs}) > 1), None)
    if two:
        return _span_cells(two) + _who(two[0], "CS2"), bar, "Each app's line on one row, each graded on its own"
    if pair:
        lg, rs = pair
        return _span_cells(rs) + _who(rs[0], LABEL.get(lg, lg.upper())), bar, "Each app's line on one row, each graded on its own"
    if pick:
        rs = pick[0][0]
        return (_span_cells(rs) + _who(rs[0], "CS2"), bar,
                "The app's line on the row; when a second app lists the prop, its line sits beside it, graded on its own")
    return "", bar, "Each app's line on one row, each graded on its own"


def _who(r, sport=None):
    pl, mk = r.get("player") or {}, r.get("market") or {}
    lead = f"{esc(sport)} · " if sport else ""
    return f'<span class="num">{lead}{esc(pl.get("name"))} · {esc(mk.get("stat_label"))} · {esc(mk.get("period_label"))}</span>'


def _detail(i, lead, opened):
    """The open card under a row (the member card's first lines): the side and line, the chance at exactly that line, why (projection
    against the line), the last 10 at this line, the break-even for the entry, when it was graded, and a link to that grade's tier on
    the public record. No hit rate is printed here: a tier's record sits on /record with its own stated chance, never beside a
    break-even it could be read against."""
    of, mk, sc, par = lead.get("offer") or {}, lead.get("market") or {}, lead.get("scout") or {}, lead.get("parity") or {}
    side = "Over" if sc.get("side") == "over" else "Under"
    line = esc(_num(of.get("line")))
    p, be, l10 = _pct(sc.get("p_side")), _pct(par.get("be")), _l10(lead)
    proj = sc.get("projection")
    graded, seen = _ts(sc.get("forecast_at")), _ts(of.get("seen_at"))
    app = esc(of.get("platform_label") or "")
    hid = lambda v: "" if v else " hidden"
    cells = [f'<div><dt>Grade at this line</dt><dd><b class="gt {_gcls(sc.get("grade"))}" data-k="g">{esc(sc.get("grade") or "–")}</b></dd></div>',
             f'<div><dt>Model estimate at exactly <span data-k="line">{line}</span></dt><dd class="big"><b data-k="p">{esc(p)}</b>%</dd></div>']
    if isinstance(proj, (int, float)):
        cells.append(f'<div><dt>Why</dt><dd>Projection <b>{float(proj):.1f}</b> vs line <b data-k="line">{line}</b></dd></div>')
    l10s = f"{l10[0]} of {l10[1]}" if l10 else ""
    cells.append(f'<div data-c="l10"{hid(l10)}><dt>Last 10 at this line</dt><dd><b data-k="l10">{l10s}</b>'
                 f' went <span data-k="side">{side}</span></dd></div>')
    cells.append(f'<div data-c="be"{hid(be)}><dt>Break-even, <span data-k="entry">{esc(par.get("entry") or "")}</span></dt>'
                 f'<dd><b data-k="be">{esc(be or "")}</b>%</dd></div>')
    gtxt, stxt = _when(graded), (seen.strftime("%H:%M") if seen else "")
    foot = (f'<span data-c="graded"{hid(gtxt)}>Graded <span data-k="graded">{esc(gtxt)}</span></span>'
            f'<span data-c="seen"{hid(stxt)}> · <span data-k="app">{app}</span> line seen <span data-k="seen">{stxt}</span> UTC</span>')
    return (f'<div class="dt" id="td{i}"{"" if opened else " hidden"}>'
            f'<p class="dt-h"><b data-k="side">{side}</b> <b data-k="line">{line}</b> {esc(mk.get("stat_label"))} · {esc(mk.get("period_label"))}'
            f' <span class="dt-app">on <span data-k="app">{app}</span></span></p>'
            f'<dl class="dt-g">{"".join(cells)}</dl>'
            f'<p class="dt-rec"><a href="/record#cs2">See the <b data-k="g">{esc(sc.get("grade") or "")}</b> tier on the public record, losses included'
            f' <span aria-hidden="true">→</span></a></p>'
            f'<p class="dt-f">{foot}</p>'
            f'</div>')


HERO_ROWS = 4                      # the hero card shows 4 rows (3 on a phone); the gate counts the rest


def feeds_label(board):
    """'Underdog + PrizePicks': the apps whose rows are on this board, in the board's feed order (never a feed that listed nothing)."""
    have = {(r.get("offer") or {}).get("platform"): (r.get("offer") or {}).get("platform_label") for r in (board or {}).get("records") or []}
    order = list(((board or {}).get("feeds") or {}).keys()) + sorted(k for k in have if k)
    out = []
    for k in order:
        if k in have and have[k] and have[k] not in out:
            out.append(have[k])
    return " + ".join(out)


def terminal(board):
    """(html rows, meta line, row count, letter-graded markets on the board). Empty board -> ("", "", 0, 0). Rows start closed (tap a
    player for the read), so the hero card shows its rows next to the headline."""
    pick, at, total = _rows(board, n=HERO_ROWS)
    if not pick:
        return "", "", 0, 0
    meta = "as of " + _when(at) if at else ""   # the page is cached: the HTML never claims freshness; the browser reads data-at
    rows = []
    for i, (rs, gap, moved) in enumerate(pick):
        lead = rs[0]
        ev, pl, mk, sc = lead.get("event") or {}, lead.get("player") or {}, lead.get("market") or {}, lead.get("scout") or {}
        p = _pct(sc.get("p_side"))
        side = "Over" if sc.get("side") == "over" else "Under"
        grade = sc.get("grade") or ""
        l10 = _l10(lead)
        be = _pct((lead.get("parity") or {}).get("be"))
        chips = ('<em class="chip gap">Line gap</em>' if gap else "") + ('<em class="chip mvd">Moved</em>' if moved else "")
        dots = f'<span class="l10" title="Last 10 at this line"{"" if l10 else " hidden"}><b>{l10[0] if l10 else ""}</b>/<i>{l10[1] if l10 else ""}</i></span>'
        start = _ts(ev.get("start_utc"))
        opened = False
        when = (" · " + esc(start.strftime("%a %H:%M")) + " UTC") if start else ""
        bee = f'<u style="--b:{esc(be or 0)}"{"" if be else " hidden"}></u>'
        bes = f'<small{"" if be else " hidden"}>break-even <b>{esc(be or "")}</b>%</small>'
        rows.append(
            f'<li class="tr{" open" if opened else ""}" data-sport="cs2">'
            f'<button type="button" class="who" aria-expanded="{"true" if opened else "false"}" aria-controls="td{i}">'
            f'<b>{esc(pl.get("name"))}<i class="cv" aria-hidden="true"></i></b><small>{esc(ev.get("label"))}{when}</small></button>'
            f'<div class="mkt"><span>{esc(mk.get("stat_label"))}</span><small>{esc(mk.get("period_label"))}</small>{chips}</div>'
            f'<div class="bks" role="group" aria-label="Lines by app">{"".join(_offer_cell(r, j == 0) for j, r in enumerate(rs))}</div>'
            f'<div class="ch"><span class="sd">{side}</span><b class="pv">{esc(p)}<i>%</i></b>'
            f'<span class="mt" style="--p:{esc(p)}" aria-hidden="true"><i></i>{bee}</span></div>'
            f'<div class="gr"><b class="{_gcls(grade)}">{esc(grade) or "–"}</b></div>'
            f'<div class="rc">{dots}{bes}</div>'
            f'{_detail(i, lead, opened)}'
            f'</li>')
    return "".join(rows), meta, len(rows), total


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


# ----------------------------------------------------------------------------------------------------------- move the line

def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def kills_chance(line, lam):
    """Scout's CS2 kills P(Over), push removed, capped at KILLS_CAP either side: the widget's formula, in Python, to check it."""
    sd = KILLS_SPREAD * math.sqrt(max(lam, 1.0))
    whole = float(line) == int(line)
    up = line + 0.5 if whole else math.floor(line) + 0.5
    dn = line - 0.5 if whole else up
    po, pu = 1 - _phi((up - lam) / sd), _phi((dn - lam) / sd)
    raw = po / (po + pu) if po + pu > 0 else 0.5
    return min(KILLS_CAP, max(1 - KILLS_CAP, raw))


def grade_seed(board):
    """The widget's seed: the first row (in the terminal's order, over every graded market on the board) that is a CS2 kills Maps 1-2 line whose chance the formula reproduces within 0.3
    points (the board's own number, not an approximation). None = no widget."""
    for rs, _g, _m in _rows(board, n=10 ** 6)[0]:
        r = rs[0]
        mk, sc, of = r.get("market") or {}, r.get("scout") or {}, r.get("offer") or {}
        lam, line, ps = sc.get("projection"), of.get("line"), sc.get("p_side")
        if mk.get("stat") != "kills" or mk.get("period") != "maps12" or not isinstance(lam, (int, float)) or line is None or ps is None:
            continue
        po = kills_chance(float(line), float(lam))
        mine = po if sc.get("side") == "over" else 1 - po
        if abs(mine - float(ps)) <= 0.003:
            return r
    return None


# ------------------------------------------------------------------------------------------------------------- the map module

# "Pick two maps. See the pace." (the CS2 band): one real board player's HLTV kills per map, picked by a fixed rule (pool_subject):
#   candidates = the LIVE board's letter-graded standard CS2 Kills Maps 1-2 props that lock after the board's time and after now (never the saved
#   board: its line is not current), one per player per match, the lettered Underdog line first (the terminal's order);
#   order = soonest lock, then the player's name (case-insensitive), then the event id;
#   the subject = the first candidate whose HLTV per-map totals (hltv_maps.for_player: exact HLTV id via hltv._player_ids, the
#   same read and 15-min cache the member player card uses) hold >= POOL_MIN_MAPS current-pool maps with >= POOL_MIN_N maps
#   each in the 12-month window; the module shows the top POOL_SHOW of those by maps played (ties: higher kills/map, then the key).
# HLTV only (owner 2026-11-20), never BO3.gg; a map missing a value is left out, never shown as 0. The current pool is
# hltv_maps.pool_for (the match's HLTV veto pool, else the last 7 days of HLTV vetoes, else Active Duty), as on the player card.
# Reads: the background builder (fetch=True) may ask hltv_maps.for_player for at most POOL_TRIES uncached players per build (its own
# single flight, bounded read slots, failures kept FAIL_TTL), and stops reading at the first failed read; a visitor's request (fetch=False) only peeks at cached answers. No
# subject, a read older than POOL_MAX_AGE_H, or any error: the whole module is left out (the page still serves).
POOL_MIN_MAPS = 4
POOL_MIN_N = 8
POOL_SHOW = 4
POOL_TRIES = 8
POOL_MAX_AGE_H = 24
WORDS = {2: "two", 3: "three", 4: "four"}
MAP_COLOR = {"mirage": "#E5A653", "dust2": "#D8BD72", "inferno": "#E86A4E", "ancient": "#5DB884", "anubis": "#4FB3C8",
             "nuke": "#7FA8E8", "cache": "#B89BE0", "overpass": "#8FC27A", "train": "#C9A27E", "vertigo": "#E58FB0"}


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


def pace(maps2, line):
    """(pooled kills per map, line pace, delta) exactly as the page script draws it: summed kills / summed maps, pace = line / 2."""
    k, n = sum(m[1] for m in maps2), sum(m[2] for m in maps2)
    v = k / n
    pc = float(line) / 2
    return v, pc, v - pc


def pool_module(board, fetch):
    """The fills for the map module ({} = no module)."""
    try:
        got = pool_subject(board, fetch)
        if not got:
            return {}
        import hltv_maps
        r, doc, maps, pool, read = got
        pl, of = r.get("player") or {}, r.get("offer") or {}
        newest = _day(str(doc.get("newest_map") or "")[:10])
        app = of.get("platform_label") or ""
        if not newest or not app:
            return {}
        line = float(of.get("line"))
        btns = []
        for i, (k, kills, n) in enumerate(maps):
            kpm = kills / n
            btns.append(f'<li><button type="button" class="map" aria-pressed="{"true" if i < 2 else "false"}" data-k="{kpm:.6f}" data-n="{n}"'
                        f' style="--mc:{MAP_COLOR.get(k, "#9AA4B8")}"><span class="mn">{esc(hltv_maps.display(k))}</span>'
                        f'<span class="kv"><span>Kills/map</span><b>{kpm:.2f}</b></span><span class="kv"><span>Maps</span><b>{n}</b></span></button></li>')
        v, pc, d = pace(maps[:2], line)
        names = sorted(hltv_maps.display(k) for k in pool)
        who = str(pl.get("name"))
        cap = (f"{who}'s HLTV numbers for the last 12 months (newest map {newest}, read {_when(read)}) on the "
               f"{WORDS.get(len(maps), len(maps))} maps of the current pool {who} has played most. The pool today: {_list(names)}.")
        return {
            "POOL_LINE": esc(_num(line)), "POOL_WHO": esc(who), "POOL_APP": esc(app), "POOL_MAPS": "".join(btns),
            "POOL_NAMES": esc(f"{hltv_maps.display(maps[0][0])} + {hltv_maps.display(maps[1][0])}"),
            "POOL_V": f"{v:.2f}", "POOL_PACE": f"{pc:.2f}", "POOL_D": ("+" if d >= 0 else "\u2212") + f"{abs(d):.2f}",
            "POOL_DCLS": "up" if d >= 0 else "dn", "POOL_CAP": esc(cap),
        }
    except Exception:
        return {}


# ------------------------------------------------------------------------------------------------------------------ the ledger

def _first_sentence(s):
    s = str(s or "").strip()
    m = re.match(r"(.+?\.)(\s|$)", s)
    return m.group(1) if m else s


def ledger(record, sports=None):
    """(cards html, decided total, oldest as-of, recorded sport labels). Decided = wins + losses, the count the cards print (pushes
    apart, as /record counts them), so the headline number always ties to the cards. Framed as a log, never a performance claim: no hit
    rate and no break-even here. A graded sport with no public record yet (soccer, tennis) gets a card that says exactly that."""
    if not record:
        return "", 0, "", []
    cells, total, asof, names = [], 0, None, []
    for sp in SPORTS:
        d = record.get(sp)
        if not d:
            continue
        t = d["totals"]
        w, l, p = int(t.get("wins") or 0), int(t.get("losses") or 0), int(t.get("pushes") or 0)
        total += w + l
        names.append(LABEL[sp])
        days = ((d.get("calendar") or {}).get("days")) or []
        a = _ts(d.get("as_of"))
        asof = a if asof is None or (a and a < asof) else asof
        hi = max([x.get("wins", 0) + x.get("losses", 0) for x in days] or [1]) or 1
        bars = "".join(
            f'<i style="--w:{round(100 * x.get("wins", 0) / hi, 1)}%;--l:{round(100 * x.get("losses", 0) / hi, 1)}%" title="{esc(_day(x.get("date")))}: {x.get("wins", 0)}-{x.get("losses", 0)}"></i>'
            for x in days[-30:])
        cells.append(
            f'<article class="lg" data-sport="{sp}"><header><b>{LONG[sp]}</b><small>{esc(t.get("matches"))} {esc(t.get("match_word") or "matches")}</small></header>'
            f'<p class="wlp"><b>{w:,}</b><span>-</span><b>{l:,}</b><span>-</span><b>{p:,}</b></p><p class="wlk">Won · Lost · Push</p>'
            f'<div class="days" aria-label="The last 30 days, wins above the line and losses below">{bars}</div>'
            f'<p class="rule"><span>Rule:</span> {esc(_first_sentence(d.get("rule") or d.get("population")))}</p></article>')
    if not cells:
        return "", 0, "", []
    for sp in sports or []:
        if sp not in SPORTS and sp in LONG:
            cells.append(f'<article class="lg lg-new" data-sport="{sp}"><header><b>{LONG[sp]}</b></header>'
                         f'<p class="lg-n">Graded on the board</p><p class="rule">Not on the public record yet. Its card shows here once it is.</p></article>')
    return "".join(cells), total, (_when(asof) if asof else ""), names


# ----------------------------------------------------------------------------------------------------------------- the reviews

def _sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(s or "").strip()) if x.strip()]


def featured(doc):
    """[(review, quote, cut, old)] by the stated rule: reviews that talk about using the product, most specific first. A review with
    photos (slips) or shouting is not featured; a sentence with money / result / betting / guarantee talk is never quoted (cut = a sentence
    was left out, shown as an ellipsis with a link to the reviews page). old = the review names the site's old brand (_OLD_BRAND): those
    come after every other qualifying review, so they only fill a slot nothing else can."""
    out = []
    for r in (doc or {}).get("reviews") or []:
        text = f'{r.get("title") or ""} {r.get("body") or ""}'
        letters = [c for c in text if c.isalpha()]
        shouting = len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > .6
        if r.get("photos") or shouting:
            continue
        sents = _sentences(r.get("body"))
        kept = [x for x in sents if not _MONEY.search(x)]
        quote = " ".join(kept)
        hits = len(_PRODUCT.findall(quote))
        if not hits or len(quote.split()) < 5:
            continue
        old = bool(_OLD_BRAND.search(f'{text} {r.get("name") or ""}'))
        out.append((old, hits, len(quote), r, quote, len(kept) < len(sents)))
    out.sort(key=lambda x: (x[0], -x[1], -x[2]))
    return [(r, q, cut, old) for old, _h, _n, r, q, cut in out[:FEATURE_N]]


def wall(doc):
    """(summary html, cards html, review count, note). No star histogram (one number and the count); the note says where the reviews
    were written, by whom and when, from the reviews themselves."""
    s = (doc or {}).get("summary") or {}
    n = int(s.get("count") or 0)
    if not n:
        return "", "", 0, ""
    cards = []
    for r, quote, cut, old in featured(doc):
        d = _ts(r.get("created"))
        title = r.get("title") if r.get("title") and not _MONEY.search(str(r.get("title"))) else ""
        stars = int(r.get("stars") or 0)
        more = "Read the full review" if cut else "On the reviews page"
        cards.append(
            f'<figure class="rv"><div class="st" aria-label="{stars} of 5 stars">{"★" * stars}<span>{"★" * (5 - stars)}</span></div>'
            f'{"<h3>" + esc(title) + "</h3>" if title else ""}'
            f'<blockquote>{esc(quote)}{" …" if cut else ""}</blockquote>'
            f'{"<p class=rv-old>Written before the site was renamed Prop Grader. The member’s words are unchanged.</p>" if old else ""}'
            f'<figcaption><b>{esc(r.get("name"))}</b><small>Paying member · {esc(_day(r.get("created")) + (", " + str(d.year) if d else ""))}{" · edited" if r.get("edited") else ""}</small>'
            f'<a href="/reviews">{more} <span aria-hidden="true">→</span></a></figcaption></figure>')
    avg = s.get("average")
    head = (f'<p class="rating"><b>★ {float(avg):.1f}</b><span>· {n} member reviews</span></p>' if n >= 10 and avg is not None
            else f'<p class="rating"><span>{n} member reviews</span></p>')
    rv = (doc or {}).get("reviews") or []
    days = sorted({f"{_day(x.get('created'))}, {_ts(x.get('created')).year}" for x in rv if _ts(x.get("created"))})
    when = f" All {n} were posted on {days[0]}." if len(rv) == n and len(days) == 1 else ""
    note = ("Written on the site's reviews page by paying members (sign-in with Whop checks the membership), one review each. "
            "Every review is published, whatever its rating." + when)
    return head, "".join(cards), n, note


def rating_tile(doc):
    """The hero proof tile for the reviews: the live average and count (the average only from 10 reviews, as on the wall)."""
    s = (doc or {}).get("summary") or {}
    n, avg = int(s.get("count") or 0), s.get("average")
    if not n:
        return ""
    if n >= 10 and avg is not None:
        return f'<b class="star">★ {float(avg):.1f}</b><span>from {n} member reviews</span>'
    return f'<b>{n}</b><span>member reviews, all published</span>'


# --------------------------------------------------------------------------------------------------------------------- the slip

_PROP = {"hs": "Headshots", "kills": "Kills", "assists": "Assists", "deaths": "Deaths"}
_SCOPE = {"1": "Map 1", "12": "Maps 1-2", "123": "Maps 1-3"}
_BOOK = {"prizepicks": "PrizePicks", "underdog": "Underdog"}


def slip_card():
    """(slip html, caption) for the Slip Studio tile, in the page's v3 markup, from SLIP (a real slip as built); ("", "") when the file
    is missing or odd. All-hit chance = the product of the legs' chances (Slip Studio's own figure: the model's chances, assumed
    independent)."""
    try:
        doc = json.loads(SLIP.read_text(encoding="utf-8"))
        legs = doc["legs"]
        p_all = 1.0
        rows = []
        for l in legs:
            p = float(l["p"])
            p_all *= p
            side = "Over" if l.get("side") == "over" else "Under"
            g = str(l.get("grade") or "")
            prop = _PROP.get(l.get("prop"), str(l.get("prop")).title())
            rows.append(f'<li><div class="sl-w"><b>{esc(l["player"])}</b><small>{esc(l.get("team") or "")}</small></div>'
                        f'<div class="sl-m"><span>{side} {esc(_num(l["line"]))}</span><small>{esc(prop)} · {esc(_SCOPE.get(str(l.get("scope")), ""))}</small></div>'
                        f'<div class="sl-p"><b>{p * 100:.1f}<i>%</i></b></div><b class="gt {_gcls(g)}">{esc(g)}</b></li>')
        built = _ts(doc.get("built_at"))
    except Exception:
        return "", ""
    if len(legs) < 2 or not built:
        return "", ""
    book = _BOOK.get(doc.get("book"), str(doc.get("book") or "").title())
    n = len(legs)
    head = (f'<div class="sl-h"><span class="sl-k">{n}-pick slip · {esc(book)}</span>'
            f'<p class="sl-all"><b>{p_all * 100:.1f}<i>%</i></b><span>model estimate, all {n} hit</span></p>'
            f'<span class="mt big" style="--p:{p_all * 100:.1f}" aria-hidden="true"><i></i></span></div>')
    cap = f"The latest {n}-pick slip built in Slip Studio when this sample was saved, shown as built (legs, lines, chances, grades)."
    return head + f'<ol class="sl-legs">{"".join(rows)}</ol>', cap


# ------------------------------------------------------------------------------------------------------------ apps, live

def _snap_platforms(snap, now):
    """{platform: rows} from one member board snapshot (module memory, never a feed read), or {} when it is missing or not read for
    APPS_MAX_AGE_S. NBA / NFL keep a per-platform count; LoL and soccer / tennis keep an index keyed (platform, id)."""
    if not isinstance(snap, dict) or snap.get("body") is None or now - float(snap.get("at") or 0) > APPS_MAX_AGE_S:
        return {}
    if isinstance(snap.get("counts"), dict):
        return {p: n for p, n in snap["counts"].items() if n}
    out = {}
    for k in list((snap.get("index") or {}).keys()):
        if isinstance(k, tuple) and k:
            out[k[0]] = out.get(k[0], 0) + 1
    return out


def _warm_sports(srv, sp):
    """Background builder only, once per process: read an NBA / NFL board that no member has opened since the start (single flight with
    the members' own reads), so the app list is not short after a deploy. Never on a visitor's request."""
    try:
        import workspace_sports
    except Exception:
        return
    for lg in workspace_sports.LEAGUES:
        if lg in sp and (workspace_sports._SNAP.get(lg) or {}).get("body") is None and ("warm", lg) not in _STATE["last"]:
            _STATE["last"][("warm", lg)] = True
            try:
                workspace_sports.snapshot(srv, lg)
            except Exception:
                pass


def apps_seen(board, sp, now=None):
    """(pick apps, comparison books, leagues with comparison books): the names on PICK_APPS / COMPARE_BOOKS whose rows are on a member
    board read in the last APPS_MAX_AGE_S (the live CS2 board passed in, the LoL / NBA / NFL / soccer / tennis snapshots in memory)."""
    now = time.time() if now is None else now
    seen, book_lgs = set(), []
    at = _ts((board or {}).get("board_at"))
    if board and at and now - at.timestamp() <= APPS_MAX_AGE_S:
        seen |= {(r.get("offer") or {}).get("platform") for r in board.get("records") or []}
    snaps = []
    try:
        import workspace_lol
        if "lol" in sp:
            snaps.append(("lol", workspace_lol._SNAP))
    except Exception:
        pass
    try:
        import workspace_sports
        snaps += [(lg, workspace_sports._SNAP.get(lg)) for lg in workspace_sports.LEAGUES if lg in sp]
    except Exception:
        pass
    try:
        import workspace_expansion
        snaps += [(x, workspace_expansion._SNAP.get(x)) for x in ("soccer", "tennis") if x in sp]
    except Exception:
        pass
    books = {k for k, _ in COMPARE_BOOKS}
    for lg, snap in snaps:
        have = set(_snap_platforms(snap, now))
        seen |= have
        if have & books and lg in LABEL:
            book_lgs.append(LABEL[lg])
    return ([n for k, n in PICK_APPS if k in seen], [n for k, n in COMPARE_BOOKS if k in seen], book_lgs)


def app_fills(picks, books, book_lgs):
    """The page's app wording from apps_seen: the proof tile, the by-hand headline / chips / row, step 1, the FAQ answer. Pick'em apps
    (slips) and comparison books are never counted as one kind. Nothing seen: no count and no "and more" (the tile is left out)."""
    n = len(picks) + len(books)
    if picks and books:
        kind = "apps and books"
    elif picks:
        kind = "apps" if len(picks) > 1 else "app"
    else:
        kind = "books" if len(books) > 1 else "book"
    lgs = _list(book_lgs) if book_lgs else "sports"
    apps_word = "apps" if len(picks) > 1 else "app"
    tile = ""
    if picks:
        lead = ", ".join(picks) + " and more" if books else _list(picks)
        tile = f"<b>{n} {kind}</b><span>Lines from {esc(lead)}</span>"
    chips = "".join(f"<li>{esc(b)}</li>" for b in picks)
    if books:
        chips += f'<li class="cmp-k">For comparison on {esc(lgs)}</li>' + "".join(f'<li class="cmp">{esc(b)}</li>' for b in books)
    if picks:
        faq = (f"The boards carry lines from {_list(picks)}, the pick'em {apps_word} Slip Studio builds slips for, "
               f"and each line is graded at the number that app lists.")
        if books:
            faq += f" The {lgs} boards also show lines from {_list(books)} beside them for comparison. Slips cover {_list(picks)} only."
        hand_open = "Open " + _list(picks + ([f"{len(books)} books"] if books else [])) + " and write the numbers down"
        step1 = (f"{_list(picks)} lines on one row for every sport on the board"
                 f"{f', with books beside them for comparison on {lgs}' if books else ''}.")
    else:
        faq = "Every row names the app whose line it is and when that line was seen, and each line is graded at that app's own number."
        hand_open = "Open each app and write the numbers down"
        step1 = "Every app's line on one row, for every sport on the board."
    return {
        "APP_TILE": tile,
        "HAND_H": esc(f"One row instead of {n} {kind}." if n > 1 else "One row for every line."),
        "BOOK_CHIPS": chips,
        "HAND_OPEN": esc(hand_open),
        "STEP1": esc(step1),
        "APP_FAQ": esc(faq),
    }


def _pair_rule_key(rs, lock):
    lines = {(r.get("offer") or {}).get("line") for r in rs}
    return (0 if len(lines) > 1 else 1, lock, (rs[0].get("player") or {}).get("name") or "")


def sports_pair(sp, now=None, compute=True):
    """A real two-app row for the by-hand table when the CS2 sample has none: from the NBA / NFL snapshots in memory, the props both
    pick'em apps list where each row has a side and the lead row a letter, locking after now; line gaps first, then the soonest lock,
    then the name. Only candidates' saved row bytes are parsed (never the whole board). -> (league, rows) or None. Cached per snapshot;
    compute=False (a visitor's request) only reads that cache."""
    now = time.time() if now is None else now
    try:
        import workspace_sports
    except Exception:
        return None
    best = None
    for lg in workspace_sports.LEAGUES:
        snap = workspace_sports._SNAP.get(lg) or {}
        if lg not in sp or snap.get("body") is None or now - float(snap.get("at") or 0) > APPS_MAX_AGE_S:
            continue
        ck = ("pair", lg)
        hit = _STATE["last"].get(ck)
        if hit and hit[0] is snap.get("body") and (hit[1] is None or hit[1][0][1].timestamp() > now):
            cand = hit[1]
        elif not compute:
            cand = hit[1] if hit and hit[1] and hit[1][0][1].timestamp() > now else None
        else:
            cand = None
            index, props = snap.get("index") or {}, snap.get("props") or {}
            for key, lst in list(props.items()):
                if not {"underdog", "prizepicks"} <= {k[0] for k in lst}:
                    continue
                xs = [index.get(k) for k in lst if k[0] in ("underdog", "prizepicks")]
                if any(x is None or not x.get("lean") or x.get("started") or not x.get("rec") for x in xs):
                    continue
                lock = _ts(xs[0].get("start"))
                if lock is None or lock.timestamp() <= now:
                    continue
                try:
                    rs = [json.loads(x["rec"]) for x in xs]
                except Exception:
                    continue
                rs.sort(key=lambda r: (0 if (r.get("scout") or {}).get("grade") else 1,
                                       0 if (r.get("offer") or {}).get("platform") == "underdog" else 1))
                if not (rs[0].get("scout") or {}).get("grade") or any((r.get("scout") or {}).get("p_side") is None for r in rs):
                    continue
                rk = _pair_rule_key(rs, lock)
                if cand is None or rk < cand[0]:
                    cand = (rk, rs)
            _STATE["last"][ck] = (snap.get("body"), cand)
        if cand and cand[0][1].timestamp() > now and (best is None or cand[0] < best[1][0]):
            best = (lg, cand)
    return (best[0], best[1][1]) if best else None


# --------------------------------------------------------------------------------------------------------------------- build

def sports_on():
    try:
        import ui_v3
        return ui_v3.sports_on().split()
    except Exception:
        return list(SPORTS)


def _list(names):
    return names[0] if len(names) < 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _widget(seed, saved_at):
    """The fills for "Move the line" from its seed row ({} = no widget). No per-tier hit rate beside the chance (see _detail)."""
    if seed is None:
        return {}
    sc, of, pl = seed.get("scout") or {}, seed.get("offer") or {}, seed.get("player") or {}
    line, lam = float(of.get("line")), float(sc.get("projection"))
    lo, hi = max(0.5, math.floor(lam * 0.5) + 0.5), math.floor(lam * 1.5) + 0.5
    cuts = [[c, g] for c, g in CUTS if c <= KILLS_CAP]      # kills are capped at 78% either side: no A+ on kills (grader p_cap_kills)
    g = sc.get("grade") or ""
    data = {"proj": round(lam, 2), "line": line, "spread": KILLS_SPREAD, "cap": KILLS_CAP, "cuts": cuts}
    return {
        "GW_DATA": esc(json.dumps(data, separators=(",", ":"))),
        "GW_WHO": esc(pl.get("name")), "GW_PROJ": f"{lam:.1f}", "GW_LINE": esc(_num(line)), "GW_MIN": _num(lo), "GW_MAX": _num(hi),
        "GW_SIDE": "Over" if sc.get("side") == "over" else "Under", "GW_P": esc(_pct(sc.get("p_side"))), "GW_G": esc(g),
        "GW_GCLS": _gcls(g), "GW_ASOF": esc(saved_at),
        "GW_LADDER": "".join(f'<li data-g="{x}"{" class=on" if x == g else ""}>{x}</li>' for x in ["Lean"] + [c[1] for c in reversed(cuts)]),
    }


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
    checkout every primary button opens (server.landing_trial_url)."""
    inp = _inputs(srv, fetch)
    sp = sports_on()
    board, saved = inp["board"], False
    rows, meta, nrows, ntotal = terminal(board) if board else ("", "", 0, 0)
    if not nrows:                                     # nothing to show live: the saved board, dated, through the same code
        board = fallback_board()
        rows, meta, nrows, ntotal = terminal(board) if board else ("", "", 0, 0)
        saved = bool(nrows)
    cells, total, asof, rec_names = ledger(inp["record"], sp)
    head, cards, nrv, rv_note = wall(inp["reviews"])
    nfeat = len(featured(inp["reviews"])) if nrv else 0
    seed = grade_seed(board) if nrows else None
    gw = _widget(seed, _when(_ts((board or {}).get("board_at"))))
    pm = pool_module(board, fetch) if (nrows and not saved) else {}     # live board only: the saved board's line is not current
    more = ntotal - nrows if (nrows and not saved) else 0
    mi = minis(board) if nrows else {}
    slip, slip_cap = slip_card()
    if fetch and BOARD_READ:
        _warm_sports(srv, sp)
    hp, hb, hrow = hand_minis(board if nrows else None, sports_pair(sp, compute=fetch))
    apps = app_fills(*apps_seen(inp["board"], sp))
    plats = {(r.get("offer") or {}).get("platform") for rs, _g, _m in (_rows(board, n=HERO_ROWS)[0] if nrows else []) for r in rs}
    term_rule = ("lettered props, two-app line gaps first, then moved lines, then the soonest" if len(plats) > 1 or
                 len({(r.get("offer") or {}).get("platform") for r in (board or {}).get("records") or []}) > 1 else
                 f"lettered props, line gaps between apps first (this sample has {feeds_label(board) or 'one app'} lines only), "
                 f"then moved lines, then the soonest")
    tools = (os.environ.get("SCOUT_MARKET_TOOLS") == "1", os.environ.get("SCOUT_PICKEM_EV", "").strip() == "1")
    base = public_base()
    unrec = [LONG[s].lower() for s in sp if s not in SPORTS and s in LONG]
    fill = {
        "TRIAL_URL": esc(join_url),
        "WEEKLY_URL": esc(weekly_url(srv)),
        "PRICE_M": esc(PRICE_M), "PRICE_W": esc(PRICE_W),
        "OWNER_POST": OWNER_POST, "OWNER_POST_BY": esc(OWNER_POST_BY),
        "OWNER_STORY": esc(OWNER_STORY), "OWNER_STORY_BY": esc(OWNER_STORY_BY),
        "SPORT_SUB": esc(_sport_sub(sp)),
        "RV_TILE": rating_tile(inp["reviews"]),
        "SPORT_CHIPS": "".join(f'<li data-sport="{s}">{LABEL[s]}</li>' for s in sp),
        "SPORT_LIST": esc(_list([LONG[s] for s in sp])),
        "SPORT_SHORT": esc(_list([LABEL[s] for s in sp])),
        "SPORT_REST": esc(_list([LONG[s] if s in ("nba", "nfl") else LONG[s].lower() for s in sp if s not in ("cs2", "lol")])),
        "SPORT_N": str(len(sp)),
        "REC_SPORTS": esc(_list(rec_names)) if rec_names else "",
        "UNREC": esc(_list(unrec)) if unrec else "",
        "UNREC_SENT": esc(f"{_list(unrec)[:1].upper()}{_list(unrec)[1:]} {'are' if len(unrec) > 1 else 'is'} graded on the board but not on the public record yet.") if unrec else "",
        **apps,
        "HAND_ROW": esc(hrow),
        "TERM_RULE": esc(term_rule),
        "TERM_ROWS": rows,
        "TERM_FEEDS": esc(feeds_label(board)) if nrows else "",
        "TERM_META": esc(meta),
        "TERM_STATE": ("saved" if saved else "dated") if nrows else "empty",
        "TERM_AT": esc((board or {}).get("board_at") or "") if nrows else "",
        "FRESH_MIN": str(FRESH_MIN),
        "TERM_N": str(nrows),
        "TERM_MORE": f"{more:,}",
        "TERM_MORE_PHONE": f"{more + max(0, nrows - 3):,}",
        "LEDGER": cells,
        "LEDGER_ASOF": esc(asof),
        "LEDGER_TOTAL": f"{total:,}" if total else "",
        "RV_HEAD": head,
        "RV_CARDS": cards,
        "RV_NOTE": esc(rv_note),
        "RV_N": str(nrv),
        "RV_REST": str(max(0, nrv - nfeat)),
        "SLIP": slip, "SLIP_CAP": esc(slip_cap), "HAND_PILLS": hp, "HAND_BE": hb,
        "GAP_MINI": mi.get("GAP", ""), "MOVE_MINI": mi.get("MOVE", ""), "BE_MINI": mi.get("BE", ""),
        "TOOLS_FLAG": (" has-ev" if tools[1] else "") + (" has-arb" if tools[0] else ""),
        "BASE": esc(base),
        "CS2_CLS": "" if pm else " no-pool",
        **gw,
        **pm,
    }
    out = _template()
    # optional blocks: <!--if:NAME--> ... <!--/if:NAME--> kept only when NAME has content
    for name, on in (("TERM", nrows > 0), ("NOTERM", nrows == 0), ("SAVED", saved), ("MORE", more > 0), ("NOMORE", more <= 0), ("HANDBE", bool(hb)), ("LEDGER", bool(cells)),
                     ("REVIEWS", bool(nrv)), ("NOREVIEWS", not nrv), ("TOTAL", bool(total)), ("RVN", bool(nrv)), ("GW", bool(gw)), ("POOL", bool(pm)), ("EV", tools[1]),
                     ("ARB", tools[0]), ("GAP", "GAP" in mi), ("NOGAP", "GAP" not in mi), ("MOVE", "MOVE" in mi), ("BE", "BE" in mi), ("BASE", bool(base)),
                     ("SLIP", bool(slip)), ("UNREC", bool(unrec and rec_names)), ("RVREST", nrv - nfeat > 0),
                     ("APPS", bool(apps["APP_TILE"])), ("HANDPILLS", bool(hp))):
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
