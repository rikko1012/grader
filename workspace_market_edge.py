"""SCOUT_MARKET_EDGE (owner site audit 2026-11-06): the board's "Market edge" column and the best line and price per side across
books, for CS2, LoL, NBA and NFL. Display only: no grade, chance, letter, stake, slip rule or calibration reads anything here.

Market edge (NBA / NFL pick'em lines, standard lines only):
  the books' fair chance for a side at the app's exact line - margin removed, the +EV board's own devig and rules
  (workspace_ev._priced -> gradecore.ev_board.price_leg -> gradecore.fair.fair_chance: equal weights, worst-case devig, the
  same freshness limits workspace_ev.price_limits(); owner 2026-11-12: exact line only, books that settle like the app only,
  gradecore.book_rules) with at least MIN_BOOKS fresh independent books (two sites on one feed count once) - minus what one pick needs to break even on that app's 2-pick
  entry (gradecore.payouts on gradecore/pickem_payouts.json: Underdog standard 2-pick 53.5%, PrizePicks Power 2-pick 57.7%;
  an Underdog payout modifier through ev_board.pickem_break_even). In percentage points. It is shown whether or not Scout has
  a side: for the side the books favour (the higher fair chance), and for Scout's side too when Scout's side is the other one.
  Never Scout's grade, never a sort or input of any grade. CS2 / LoL props have no sportsbook price (books there price only
  the match winner), so their rows carry the plain reason instead.

Best line per side (every sport): every book's current line for the same event / player / stat / period - PrizePicks and
Underdog board lines, the sportsbook and exchange rows the NBA / NFL snapshots hold (same freshness limits as above), and the
direct extra books (Sleeper, Dabble) from their shared book-board builds. Best Over = the lowest line, then the payout that
needs the lowest chance to break even; best Under = the highest line, then the same. Goblin / demon / promo lines pay
differently and are left out. `ls` lists each book's standard line so the page can show the discrepancy chip (books differ by
GAP_MIN or more; owner term "discrepancy = books differ", extended from the CS2 chip of ca84528).

For 700 members: one build per sport every TTL s through srv.shared, from the boards and book builds already held in memory;
no member request ever reaches a provider. The fair chances are workspace_ev's memo (one pricing pass per board generation).
This module imports no lol or sports package."""
import json
import os
import time

import price_freshness as pf
import workspace_books as wb
import workspace_ev as we

FLAG = "SCOUT_MARKET_EDGE"
TTL = 30
MIN_BOOKS = 2
GAP_MIN = 1.0
SPORTS = ("cs2", "lol", "nba", "nfl")
MARKET_SPORTS = we.MARKET_SPORTS
APPS = we.APPS
ENTRY = {"underdog": ("standard", 2, "Underdog standard 2-pick"), "prizepicks": ("power", 2, "PrizePicks Power 2-pick")}
SHORT = {"prizepicks": "PP", "underdog": "UD", "fanduel": "FD", "betrivers": "BR", "kalshi": "Kalshi", "polymarket": "Poly",
         "draftkings": "DK", "betmgm": "MGM", "caesars": "CZR", "pinnacle": "PIN", "fanatics": "FAN", "espnbet": "ESPN",
         "bovada": "BOV", "hardrockbet": "HR", "bet365": "365", "sleeper": "SL", "dabble": "DB"}
ESPORTS_WHY = "Sportsbooks price only the match winner for this game, not player props, so there is no books' chance to compare."
WHY = {
    "type": "No fixed per-pick payout is published for this line type, so there is nothing to measure it against.",
    "one": "One side only: the books' chance needs both sides of the line.",
    "none": "No sportsbook prices this line right now.",
    "app": "Market edge is measured on PrizePicks and Underdog lines.",
    "be": "No published break-even for this pick's payout.",
    "books": f"Fewer than {MIN_BOOKS} independent books have a current price on this exact line.",
    "rules": "The books on this line settle a sit-out differently from the app.",
    "stale": "The books' prices on this line are older than the freshness limit.",
    "one-sided": "The books price only one side of this line.",
    "wide": "The books' margin on this line is too wide to read a fair chance.",
    "spread": "The exchange spread on this line is too wide.",
    "line": "No book prices this exact line.",
    "other": "The books' prices on this line could not be read.",
}


def enabled():
    return os.environ.get(FLAG, "").strip() == "1"


def features():
    """The /me features entry (flag on only)."""
    from gradecore import payouts
    cfg = payouts.pickem_config()
    ent = {}
    for app, (entry, n, label) in ENTRY.items():
        be = payouts.entry_break_even(app, entry, n, cfg)
        if be is not None:
            ent[app] = {"label": label, "be": round(be, 6)}
    return {"market_edge": {"min_books": MIN_BOOKS, "gap_min": GAP_MIN, "entries": ent, "esports_why": ESPORTS_WHY}}


def _params():
    from gradecore import fair
    return ("prizepicks", "power", 2, "equal", "worst", MIN_BOOKS, (), (), 0.0, fair.MAX_WIDTH)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def prop_key(r):
    """web/upgrade.js propKey: event id | player key | stat | period."""
    e, p, m = r.get("event") or {}, r.get("player") or {}, r.get("market") or {}
    return "|".join(str(x) for x in (e.get("id"), p.get("key"), m.get("stat"), m.get("period")))


def _started(r, now):
    e = r.get("event") or {}
    if e.get("status") == "started":
        return True
    t = wb._ts(e.get("lock_utc") or e.get("start_utc"))
    return t is not None and t <= now


def _short(book, label=None):
    return SHORT.get(book) or (label or book or "")[:4]


# ------------------------------------------------------------------------------------------------------------- market edge

def _side_edge(app, side, leg, priced, cfg):
    from gradecore import ev_board
    f, why = priced.get((leg["key"], side), (None, None))
    if f is None:
        return None, "none"
    if f.get("p") is None:
        return None, why or "other"
    entry, n, label = ENTRY[app]
    be = ev_board.pickem_break_even(app, entry, n, leg.get("multiplier"), config=cfg)
    books = sorted({we._label(s["book"]) for s in f.get("sources") or ()})
    out = {"side": side, "p": round(f["p"], 6), "n": len(f.get("books") or ()), "books": books, "entry": label}
    if be is None:
        return dict(out, be=None, edge=None), "be"
    return dict(out, be=round(be, 6), edge=round(f["p"] - be, 6)), None


def edges(srv, sport, recs, now):
    """{platform|offer_id: {"fav": side edge, "scout": side edge (Scout's side when it is not the favoured one)} or {"why"}}."""
    if sport not in MARKET_SPORTS:
        return {}
    from gradecore import payouts
    cfg = payouts.pickem_config()
    legs, priced = we._priced(srv, _params(), now)
    by = {}
    for leg in legs:
        if leg["sport"] == sport:
            by.setdefault((leg["app"], leg["offer_id"]), {})[leg["side"]] = leg
    out = {}
    for r in recs:
        o, m, s = r.get("offer") or {}, r.get("market") or {}, r.get("scout") or {}
        app = o.get("platform")
        k = f"{app}|{o.get('offer_id')}"
        if app not in APPS:
            continue                                     # a sportsbook's own row: the books' chance is not measured on itself
        lt = m.get("line_type") or "standard"
        if lt != "standard":
            out[k] = {"why": WHY["one" if lt.endswith(" only") else "type"]}
            continue
        sides = by.get((app, o.get("offer_id")))
        if not sides:
            continue                                     # started / not on the +EV legs: the board hides it anyway
        got, whys = {}, {}
        for side, leg in sides.items():
            v, why = _side_edge(app, side, leg, priced, cfg)
            if v is not None:
                got[side] = v
            else:
                whys[side] = why
        if not got:
            w = next(iter(whys.values()), "none")
            out[k] = {"why": WHY.get(w, WHY["other"])}
            continue
        fav = max(got.values(), key=lambda v: (v["p"], v["side"] == "over"))
        row = {"fav": fav}
        sd = s.get("side")
        if sd in ("over", "under") and sd != fav["side"]:
            if sd in got:
                row["scout"] = got[sd]
            else:
                row["scout_why"] = WHY.get(whys.get(sd), WHY["other"])
        out[k] = row
    return out


# ------------------------------------------------------------------------------------------------------------- best lines

def _app_needs(app, mult, cfg):
    from gradecore import ev_board
    entry, n, _label = ENTRY[app]
    m = None if not _num(mult) or abs(float(mult) - 1.0) < 1e-9 else float(mult)
    return ev_board.pickem_break_even(app, entry, n, m, config=cfg)


def _offers_of_record(r, cfg, limits, now):
    """[(side, line, book, label, price text, needs, standard, seen epoch)] of one board record's offered sides. A sportsbook's
    own row is left out once its price is older than the sportsbook limit; a pick'em app line keeps its feed's rule."""
    o, m = r.get("offer") or {}, r.get("market") or {}
    book, line = o.get("platform"), o.get("line")
    if not _num(line) or book in we.PREDICTION:
        return []                                        # an exchange's ladder rungs: read from its book row (nearest rung, bid / ask)
    lt = m.get("line_type") or "standard"
    if lt not in ("standard", "over only", "under only"):
        return []                                        # goblin / demon / promo lines pay differently: not compared
    kind = None if book in APPS else "decimal"           # a sportsbook's own board row (FanDuel...) carries decimal prices
    t = wb._ts(o.get("seen_at"))
    if kind == "decimal":
        if t is None or t > now + pf.FUTURE_SLACK_S or now - t > limits["sportsbook"]:
            return []
    label = o.get("platform_label") or book
    out = []
    for side in ("over", "under"):
        v = o.get(side) or {}
        if not v.get("offered"):
            continue
        if book in APPS:
            mult = v.get("payout")
            needs = _app_needs(book, mult, cfg)
            # the price is the one the card's Books and lines table prints for this side (upgrade.js priceText): the app's own
            # decimal price when it sends one (Underdog's price_higher / price_lower), else the pick's payout when it is not the
            # entry's standard 1.00x (site audit 2026-11-06 N8: Best line read "1.00x" beside Books and lines' 1.90 / 1.90).
            # PrizePicks standard lines pay the entry table, so they carry no per-pick price; `needs` is the entry's either way
            if _num(v.get("price")) and v["price"] > 1:
                px = f"{float(v['price']):.2f}"
            elif _num(mult) and abs(float(mult) - 1.0) > 1e-9:
                px = f"{float(mult):.2f}x"
            else:
                px = None
        elif kind == "decimal" and _num(v.get("price")) and v["price"] > 1:
            needs, px = 1.0 / v["price"], f"{v['price']:.2f}"
        else:
            needs, px = None, None
        out.append((side, float(line), book, label, px, needs, lt == "standard", t))
    return out


def _offers_of_bookrow(b, limits, now):
    """A sportsbook / exchange row of an NBA / NFL snapshot (the same rows the +EV board reads), fresh quotes only."""
    plat, line = b.get("platform"), b.get("line")
    if not _num(line) or b.get("kind") in (None, "none"):
        return None, []
    book = we.base_book(plat)
    exch = b.get("kind") == "mid" or book in we.PREDICTION
    t = wb._ts(b.get("seen_at"))
    if t is None or t > now + 60 or now - t > limits["exchange" if exch else "sportsbook"]:
        return book, []
    label = we._label(book)
    out = []
    if exch:
        bid, ask = we._exchange(b.get("over"))
        if _num(ask) and 0 < ask < 1:
            out.append(("over", float(line), book, label, f"{ask:.2f}", ask, False, t))
        if _num(bid) and 0 < bid < 1:
            out.append(("under", float(line), book, label, f"{1 - bid:.2f}", 1 - bid, False, t))
        return book, out
    for side in ("over", "under"):
        d = we._num(b.get(side))
        if d is not None:
            out.append((side, float(line), book, label, f"{d:.2f}", 1.0 / d, True, t))
    return book, out


def _extra_records(srv, sport):
    """The direct extra books' (Sleeper, Dabble) current records on this sport, from their shared book-board builds."""
    import book_providers as bp
    out = []
    for pid in bp.enabled_ids():
        p = bp.providers().get(pid) or {}
        if p.get("source") != "direct" or sport not in (p.get("sports") or ()):
            continue
        try:
            st, body = wb.board(srv, sport, pid)
            doc = json.loads(body) if st == 200 else {}
        except Exception:                                 # noqa: BLE001 - one book failing never hides the others
            continue
        if (doc.get("feed") or {}).get("state") != "ok":
            continue
        comparable = p.get("price_comparable", True)
        for r in doc.get("records") or ():
            out.append((r, comparable, p.get("kind"), p.get("price_label")))
    return out


def _better(a, b, side):
    """True when offer a beats b for this side: the easier line first (Over lower, Under higher), then the lower break-even."""
    if a[1] != b[1]:
        return a[1] < b[1] if side == "over" else a[1] > b[1]
    na, nb = a[5], b[5]
    if na is None or nb is None:
        return na is not None and nb is None
    return na < nb - 1e-12


def best_lines(srv, sport, recs, s, now):
    """{prop key: {"over": best, "under": best, "ls": [[label, line, book], ...] standard lines, "n": books,
    "bk": [[label, line, book, Over price, Under price, seen ISO or None], ...] every book and line counted in n (site audit N8: the card listed
    two of "4 books"; Sleeper and Dabble lines live in their own book-board builds, so the card lists them from here)}}."""
    from gradecore import payouts
    cfg = payouts.pickem_config()
    limits = we.price_limits()
    feeds = (s or {}).get("feeds") or {}
    per = {}

    def add(pk, offers):
        d = per.setdefault(pk, {})
        for x in offers:
            d.setdefault((x[2], x[1]), []).append(x)
    rows_of = {}
    for r in recs:
        if _started(r, now) or (r.get("state") or {}).get("kind") not in (None, "ok", "fresh", "ready", "live"):
            continue
        o = r.get("offer") or {}
        if (feeds.get(o.get("platform")) or {}).get("state", "ok") != "ok":
            continue                                     # the board hides a feed that is not current; so does this
        pk = prop_key(r)
        add(pk, _offers_of_record(r, cfg, limits, now))
        rows_of.setdefault(pk, []).append(r)
    if sport in MARKET_SPORTS:
        index = (s or {}).get("index") or {}
        for pk, rs in rows_of.items():
            seen = {}
            for r in rs:
                o = r.get("offer") or {}
                x = index.get((o.get("platform"), o.get("offer_id"))) or {}
                same, other = x.get("books") or ([], [])
                for b in list(same) + list(other):
                    key = (we.base_book(b.get("platform")), b.get("line"))
                    t = wb._ts(b.get("seen_at")) or 0.0
                    if key not in seen or t > seen[key][0]:
                        seen[key] = (t, b)
            for _t, b in seen.values():
                book, offers = _offers_of_bookrow(b, limits, now)
                if (book, float(b["line"]) if _num(b.get("line")) else None) in per.get(pk, {}):
                    continue                             # the same book and line as a board row (a FanDuel row): counted once
                add(pk, offers)
    for r, comparable, kind, price_label in _extra_records(srv, sport):
        if _started(r, now):
            continue
        o = r.get("offer") or {}
        line = o.get("line")
        if not _num(line):
            continue
        t = wb._ts(o.get("seen_at"))
        if kind == "sportsbook" and not pf.fresh(t, now, limits["sportsbook"]):
            continue                                     # a direct sportsbook's quote past the sportsbook limit: never a best price
            #                                              or best line (its line is as old as its price, comparable or not)
        offers = []
        for side in ("over", "under"):
            v = o.get(side) or {}
            if not v.get("offered"):
                continue
            price = v.get("price") if _num(v.get("price")) else v.get("payout")
            if comparable and kind == "sportsbook" and _num(price) and price > 1:
                px, needs = f"{price:.2f}", 1.0 / price
            elif comparable and _num(v.get("payout")) and abs(float(v["payout"]) - 1.0) > 1e-9:
                px, needs = f"{float(v['payout']):.2f}x", None
            elif not comparable and _num(v.get("price")):
                # Dabble: its own per-side number, never odds - named as the card's Books and lines names it (book_providers
                # price_label), never a break-even
                px, needs = f"{price_label or 'App price'} {float(v['price']):.2f}", None
            else:
                px, needs = None, None
            offers.append((side, float(line), o.get("platform"), o.get("platform_label") or o.get("platform"), px, needs, True, t))
        add(prop_key(r), offers)
    out = {}
    for pk, d in per.items():
        best = {}
        std = {}
        for (_book, _line), offers in d.items():
            for x in offers:
                cur = best.get(x[0])
                if cur is None or _better(x, cur, x[0]):
                    best[x[0]] = x
                if x[6] and x[2] not in we.PREDICTION:
                    std[(x[2], x[1])] = x[3]
        if not best:
            continue
        # "at": when that price was seen (ISO, None when the feed gave no time), so the page can write "FanDuel · checked 4 min ago"
        ent = {side: {"book": x[2], "label": x[3], "short": _short(x[2], x[3]), "line": x[1], "price": x[4],
                      "needs": None if x[5] is None else round(x[5], 6), "at": wb._iso(x[7]) if x[7] is not None else None}
               for side, x in best.items()}
        ent["ls"] = sorted(([lab, ln, bk] for (bk, ln), lab in std.items()), key=lambda v: (v[1], v[0]))
        ent["n"] = len({bk for bk, _ln in d})
        rows, seen = {}, {}
        for (bk, ln), offers in d.items():
            for x in offers:
                row = rows.setdefault((bk, ln), [x[3], ln, bk, None, None])
                row[3 if x[0] == "over" else 4] = x[4] or row[3 if x[0] == "over" else 4] or ""
                if x[7] is not None and x[7] > seen.get((bk, ln), float("-inf")):
                    seen[(bk, ln)] = x[7]
        for k, row in rows.items():                      # 6th item: when the book's line was seen (ISO or None), never a made-up time
            row.append(wb._iso(seen[k]) if k in seen else None)
        ent["bk"] = sorted(rows.values(), key=lambda v: (v[1], v[0]))
        out[pk] = ent
    return out


# ------------------------------------------------------------------------------------------------------------- build

def build(srv, sport, now=None):
    now = time.time() if now is None else now
    recs, s = wb.records(srv, sport)
    if recs is None:
        return 503, {"error": "board_unavailable"}
    doc = {"sport": sport, "as_of": wb._iso(now), "min_books": MIN_BOOKS, "gap_min": GAP_MIN,
           "why": None if sport in MARKET_SPORTS else ESPORTS_WHY,
           "rows": edges(srv, sport, recs, now), "props": best_lines(srv, sport, recs, s, now)}
    return 200, doc


def shared(srv, sport):
    """(status, body bytes): one build per sport every TTL s for every member."""
    def compute():
        st, doc = build(srv, sport)
        return st, json.dumps(doc, default=str, separators=(",", ":")).encode()
    return srv.shared(("ws_market_edge", sport), compute, ttl=TTL)
