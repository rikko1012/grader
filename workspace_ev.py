"""SCOUT_PICKEM_EV (default off): the pick'em +EV board (competitor spec 4.1), route #/ev under Tools.

For every open PrizePicks / Underdog standard line on the NBA / NFL boards: the books the sports service already holds for
that market (FanDuel, BetRivers, Bovada, Kalshi, Polymarket - the snapshot's own book rows, no provider request is made
here) -> the market's fair chance AT THE APP'S EXACT LINE (gradecore.fair with exact=True) -> the selected entry's per-leg
break-even from the verified payout tables -> EV = p / be - 1 (gradecore.ev_board).

Owner 2026-11-12 ("use 2 independent books by default, matching the exact line, period and settlement rules. Two sites
using the same underlying feed shouldn't count twice"):
  - a book counts only on the app's exact line (nothing is shifted from another line), on the same stat and period (the
    snapshot attaches book rows per event / player / stat / scope; a row of another scope is not attached), and only when
    it settles the prop like the app does - gradecore.book_rules.for_pickem; a rule that is not confirmed = not used;
  - "books" counts pricing FAMILIES (gradecore.book_rules.FAMILY), so two sites on one feed are one; the default minimum is
    ev_board.MIN_BOOKS_DEFAULT (2). A member may lower it to 1; such rows carry one_book / the "1 book" label;
  - every shared build's legs that beat the entry are queued for the record (workspace_ev_log), off the request path. The chance the board prints (Scout's model) is shown beside the market fair, never blended: no blend
weight is fitted and the calibration layers are off. CS2 / LoL lines (and NBA / NFL lines no book prices) are "model only"
and ranked in their own list. A leg whose quotes are all stale / one-sided / wide gets no EV: it is hidden and counted.

Member filters that change the fair chance (weights preset or per-book weights, devig method, min books, required books, min
liquidity, max width) and the entry type are server parameters, each normalised to a short fixed list so the number of
distinct builds stays small. Display filters (sport, search, odds-to-hit range, quick filters, EV only) run in the page.

For 700 members: one priced build per (parameters, sports the member sees) every TTL s through srv.shared; the fair chances
behind it are memoised per (board generation, fair parameters), so changing only the entry type re-prices nothing.
This module imports no lol or sports package."""
import json
import os
import threading
import time

import workspace_books as wb

TTL = 30
FLAG = "SCOUT_PICKEM_EV"
APPS = ("prizepicks", "underdog")
APP_LABEL = {"prizepicks": "PrizePicks", "underdog": "Underdog"}
MARKET_SPORTS = ("nba", "nfl")
MODEL_EV_HIDDEN = ("nba", "nfl")                # no return figure on a leg with no book price (owner audit 2026-11-06, T5)
MODEL_SPORTS = ("cs2", "lol")
PREDICTION = ("kalshi", "polymarket")
MAX_REQUIRED = 2
_LOCK = threading.Lock()
_FAIR = {}                          # (board token, fair params) -> {(sport, app, offer id, side): leg}


def enabled():
    return os.environ.get(FLAG, "").strip() == "1"


def _label(p):
    import workspace_sports
    return workspace_sports.book_label(p)


def base_book(p):
    """A feed's book name ('oddsapi:fanduel' and 'fanduel' are one book, counted once)."""
    return str(p or "").split(":")[-1].lower()


def default_entry(app):
    """The entry the board opens on: the in-app entry that needs the LOWEST chance per leg (PrizePicks: 6-pick Flex, 54.2%;
    Underdog: Standard 2-pick, 53.5%). The 2026-11-12 check found PrizePicks opened on its hardest entry (Power 2-pick, 57.7%)
    and so read "0 plays"; the needed chance is on every entry in the picker and in the list heading."""
    t = _tables()
    if app not in t["default"]:
        es = [e for e in t["entries"] if e["app"] == app]
        if not es:
            t["default"][app] = (("power" if app == "prizepicks" else "standard"), 2)
        else:
            e = min(es, key=lambda e: (round(e["break_even"], 6), e["n"]))
            t["default"][app] = (e["entry"], e["n"])
    return t["default"][app]


# ------------------------------------------------------------------------------------------------------------- params

_TABLES = {}


def _tables():
    """The verified entry list and each app's default entry, worked out once per payout table (both payout reads are cached by
    gradecore.payouts, so this is once per process). Before 2026-11-20 every leg of every build re-solved every Flex table
    (default_entry -> entries -> payouts.entry_types): 83% of a build. Same numbers, computed once."""
    from gradecore import payouts
    cfg, raw = payouts.pickem_config(), payouts._pickem_raw()
    hit = _TABLES.get("v")
    if hit is not None and hit[0] is cfg and hit[1] is raw:      # held references: an identity check, never a reused id
        return hit[2]
    es = [{"app": e["book"], "entry": e["entry"], "n": e["n"], "break_even": e["break_even"], "american": e["american"]}
          for e in payouts.entry_types() if e["book"] in APPS and e["break_even"] is not None
          and payouts.in_app(e["book"], e["entry"], e["n"])]
    t = {"entries": es, "default": {}}
    _TABLES["v"] = (cfg, raw, t)
    return t


def entries():
    """Every verified (app, entry, n) with its break-even, for the entry picker."""
    return [dict(e) for e in _tables()["entries"]]


def _choice(v, choices, default):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(choices, key=lambda c: abs(c - x))


def params(q, books=()):
    """The query -> a normalised, hashable parameter tuple (unknown values fall back to the defaults)."""
    from gradecore import ev_board, fair
    g = lambda k, d=None: (q.get(k) or d)
    ok = {(e["app"], e["entry"], e["n"]) for e in entries()}
    app = g("app") if g("app") in {a for a, _, _ in ok} else default_app()
    entry = g("entry", "")
    try:
        n = int(g("n", 0))
    except (TypeError, ValueError):
        n = 0
    if (app, entry, n) not in ok:
        entry, n = default_entry(app)
    preset = g("preset", "equal") if g("preset", "equal") in (*fair.PRESETS, "custom") else "equal"
    method = g("method", "worst") if g("method", "worst") in fair.METHODS else "worst"
    try:
        min_books = max(1, min(ev_board.MIN_BOOKS_MAX, int(g("min_books", ev_board.MIN_BOOKS_DEFAULT))))
    except (TypeError, ValueError):
        min_books = ev_board.MIN_BOOKS_DEFAULT
    known = set(books)
    req = tuple(sorted({b for b in str(g("req", "")).lower().split(",") if b in known}))[:MAX_REQUIRED]
    weights = ()
    if preset == "custom":
        w = {}
        for part in str(g("w", "")).lower().split(","):
            b, _, v = part.partition(":")
            if b in known:
                w[b] = _choice(v, ev_board.WEIGHT_CHOICES, 1.0)
        weights = tuple(sorted(w.items()))
    min_liq = _choice(g("min_liq", 0), ev_board.MIN_LIQUIDITY_CHOICES, 0.0)
    max_width = _choice(g("max_width", fair.MAX_WIDTH), ev_board.MAX_WIDTH_CHOICES, fair.MAX_WIDTH)
    return (app, entry, n, preset, method, min_books, req, weights, min_liq, max_width)


def price_limits():
    """Each quote kind's freshness limit less both cache windows (this module's _FAIR memo and srv.shared, TTL each), so a
    price a member sees is never older than gradecore.fair.FRESHNESS_S, however late in both windows it is served."""
    from gradecore import fair
    return {k: max(0.0, v - 2 * TTL) for k, v in fair.FRESHNESS_S.items()}


def default_app():
    """The first app with a verified payout table (PrizePicks: verified by owner sign-off 2026-11-05)."""
    apps = [e["app"] for e in entries()]
    return next((a for a in APPS if a in apps), "underdog")


def _fair_opts(p):
    _app, _e, _n, preset, method, min_books, req, weights, min_liq, max_width = p
    return {"preset": "equal" if preset == "custom" else preset, "weights": dict(weights) or None, "method": method,
            "min_books": min_books, "required_books": req, "min_liquidity": min_liq, "max_width": max_width}


# ------------------------------------------------------------------------------------------------------------- inputs

def _exchange(v):
    try:
        bid, ask = ([float(x) for x in v.split("/")] if isinstance(v, str) else [float(x) for x in v])[:2]
    except (TypeError, ValueError, AttributeError):
        return None, None
    return bid, ask


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 1 else None


def quotes_of(rows, sport=None):
    """The snapshot's book rows of one market -> gradecore.fair.Quote list (no price is changed here). Each quote's cluster is
    its pricing family. A book whose settlement is known to differ from the app's is marked excluded (listed as left out with
    that fact, never priced). A book whose rule is not confirmed, or that is never a price source, is not a price for this
    board at all and is not listed (owner 2026-10-16: nothing unconfirmed on a member page) - the admin table
    (/admin/ev_log.json) shows which books those are. Polymarket carries the measured sit-out share (book_rules.for_pickem)."""
    from gradecore import book_rules
    from gradecore.fair import Quote
    out = []
    for b in rows or ():
        plat, kind = b.get("platform"), b.get("kind")
        if kind in (None, "none") or b.get("line") is None:
            continue
        seen = wb._ts(b.get("seen_at"))
        seen = float("nan") if seen is None else seen
        book = base_book(plat)
        ok, share, why = book_rules.for_pickem(book, sport)
        if not ok and why != book_rules.REASON_DIFFERENT:
            continue
        fam = book_rules.family_of(book)
        if kind == "mid" or plat in PREDICTION:
            bid, ask = _exchange(b.get("over"))
            out.append(Quote(book=book, line=float(b["line"]), observed_at=seen, kind="exchange", bid=bid, ask=ask, cluster=fam,
                             sit_out=share or 0.0, excluded=None if ok else why))
            continue
        out.append(Quote(book=book, line=float(b["line"]), observed_at=seen, over=_num(b.get("over")), under=_num(b.get("under")),
                         cluster=fam, excluded=None if ok else why))
    return out


_LEGS = {}


def _legs(srv):
    """Every open standard pick'em leg the boards hold -> (token, [leg]). leg = dict with the record's display fields, its
    quotes (NBA / NFL) and the printed chances. Memoised per board generation (the token = each board body's identity) and
    re-cut at most every TTL s, so a start time passing drops its legs."""
    boards, token = [], []
    for sport in MARKET_SPORTS + MODEL_SPORTS:
        try:
            recs, s = wb.records(srv, sport)
        except Exception:                          # noqa: BLE001 - one sport failing never empties the others
            recs, s = None, None
        token.append((sport, None if recs is None else id(s["body"])))
        if recs is not None:
            boards.append((sport, recs, s))
    token = tuple(token)
    now = time.time()
    with _LOCK:
        hit = _LEGS.get("v")
        if hit and hit[0] == token and now - hit[1] < TTL:
            return token, hit[2]
    legs = []
    for sport, recs, s in boards:
        books = {}
        if sport in MARKET_SPORTS:
            for (p, rid), x in (s.get("index") or {}).items():
                if p in APPS:
                    same, other = x.get("books") or ([], [])
                    books[(p, rid)] = (list(same) + list(other), x.get("st"), x.get("scope"))
        for r in recs:
            o, m, sc = r.get("offer") or {}, r.get("market") or {}, r.get("scout") or {}
            app = o.get("platform")
            if app not in APPS or (m.get("line_type") or "standard") != "standard" or o.get("line") is None:
                continue
            if ((sc.get("hold") or {}).get("label") in ("Over only", "Under only")):
                continue                           # LoL os-1 / sports cap-2: a one-sided line is never priced (no EV or Edge)
            ev = r.get("event") or {}
            start = wb._ts(ev.get("lock_utc") or ev.get("start_utc"))
            if ev.get("status") == "started" or (start is not None and start <= now):
                continue
            bk, stat, scope = books.get((app, o.get("offer_id")), ((), m.get("stat"), None))
            if scope is not None and wb._per(scope) != wb._per(m.get("period")):
                bk = ()                            # book rows of another period never price this line
            for side in ("over", "under"):
                so = o.get(side) or {}
                if so.get("offered") is False:
                    continue
                other = "under" if side == "over" else "over"
                legs.append({"sport": sport, "app": app, "offer_id": o.get("offer_id"), "side": side, "line": float(o["line"]),
                             "key": "|".join(str(x) for x in (sport, app, o.get("offer_id"))), "revision": o.get("revision"),
                             "player": (r.get("player") or {}).get("name"), "team": (r.get("player") or {}).get("team"),
                             "event": ev.get("label"), "start_utc": ev.get("start_utc"), "stat": m.get("stat"), "shape_market": stat,
                             "stat_label": m.get("stat_label"), "period_label": m.get("period_label"),
                             "multiplier": so.get("payout"), "p_model": sc.get("p_" + side), "p_model_other": sc.get("p_" + other),
                             "book_rows": bk})
    with _LOCK:
        _LEGS["v"] = (token, now, legs)
    return token, legs


def _priced(srv, p, now):
    """The fair chance of every leg under these fair parameters, memoised per board generation (the entry type and the
    sports a member sees never re-price)."""
    from gradecore import ev_board
    token, legs = _legs(srv)
    fkey = (token, p[3:])
    with _LOCK:
        hit = _FAIR.get(fkey)
        if hit is not None and now - hit[0] < TTL:
            return legs, hit[1]
    opts = dict(_fair_opts(p), freshness=price_limits(), exact=True)
    out = {}
    for leg in legs:
        if leg["sport"] not in MARKET_SPORTS or not leg["book_rows"]:
            continue
        res = ev_board.price_leg(quotes_of(leg["book_rows"], leg["sport"]), leg["side"], leg["line"], now, be=None, p_model=None,
                                 p_model_other=None, reader=None, **opts)
        out[(leg["key"], leg["side"])] = (res["fair"], res["hidden"])
    with _LOCK:
        for k in [k for k, v in _FAIR.items() if now - v[0] >= TTL]:
            _FAIR.pop(k, None)
        _FAIR[fkey] = (now, out)
    return legs, out


# ------------------------------------------------------------------------------------------------------------- build

def _source(s, sources_label):
    return {"book": s["book"], "label": sources_label.get(s["book"], s["book"].title()), "family": s["group"], "kind": s["kind"], "line": s["line"],
            "over": s.get("over"), "under": s.get("under"), "bid": s.get("bid"), "ask": s.get("ask"), "sit_out": s.get("sit_out") or None,
            "age_s": round(s["age_s"]) if s.get("age_s") is not None else None, "liquidity": s.get("liquidity"),
            "width": None if s.get("width") is None else round(s["width"], 4), "p": round(s["p"], 4), "shifted": s["shifted"],
            "weight": s["weight"]}


def build(srv, p, allowed, now=None):
    from gradecore import ev_board, fair, payouts
    now = time.time() if now is None else now
    legs, priced = _priced(srv, p, now)
    app, entry, n = p[0], p[1], p[2]
    from gradecore import book_rules
    labels, usable = {}, set()
    for leg in legs:
        for b in leg["book_rows"] or ():
            bb = base_book(b.get("platform"))
            labels.setdefault(bb, _label(bb))
            if bb not in usable and book_rules.for_pickem(bb, leg["sport"])[0]:
                usable.add(bb)                     # only books this board can price are offered as filters
    cfg = payouts.pickem_config()
    entry_be = payouts.entry_break_even(app, entry, n, cfg)
    memo = {}

    def pbe(a, e, k, m):                            # one solve per (app, entry, size, multiplier) per build, not per leg
        key = (a, e, k, m)
        if key not in memo:
            memo[key] = ev_board.pickem_break_even(a, e, k, m, config=cfg)
        return memo[key]
    rows, model_rows, hidden, plays = [], [], {}, {a: 0 for a in APPS}
    for leg in legs:
        if not allowed.get(leg["sport"]):
            continue
        sel = p if leg["app"] == app else None
        be = pbe(leg["app"], entry, n, leg["multiplier"]) if sel else None
        f, why = priced.get((leg["key"], leg["side"]), (None, None))
        if leg["sport"] in MARKET_SPORTS and f is not None and f["p"] is not None and leg["app"] != app:
            # the other app's +EV count at its default entry (the "plays" chips)
            dflt = default_entry(leg["app"])
            obe = pbe(leg["app"], *dflt, leg["multiplier"])
            if obe is not None and fair.leg_ev(f["p"], obe) > 0:
                plays[leg["app"]] += 1
        if leg["app"] != app:
            continue
        p_np = ev_board.no_push(leg["p_model"], leg["p_model_other"])
        base = {k: leg[k] for k in ("key", "sport", "app", "side", "line", "player", "team", "event", "start_utc", "stat_label",
                                   "period_label", "revision")}
        base.update(app_label=APP_LABEL[app], multiplier=leg["multiplier"], be=None if be is None else round(be, 6),
                    american=None if be is None else round(payouts.american_of(be)),
                    p_model=None if leg["p_model"] is None else round(float(leg["p_model"]), 4),
                    ev_model=None if p_np is None or be is None else round(fair.leg_ev(p_np, be), 4))
        if f is None:
            if base["p_model"] is not None:
                # owner audit 2026-11-06 (T5): an NBA / NFL chance is a research estimate that has not held up on settled
                # results (NFL 65%+ won 55.9%), so with no book price there is no return figure to show (hidden, not labelled)
                model_rows.append((p_np if p_np is not None else base["p_model"],
                                   dict(base, basis="model only", **({"ev_model": None} if leg["sport"] in MODEL_EV_HIDDEN else {}))))
            continue
        if f["p"] is None:
            hidden[why] = hidden.get(why, 0) + 1
            continue
        ev = None if be is None else fair.leg_ev(f["p"], be)
        if ev is not None and ev > 0:
            plays[app] += 1
        rows.append(dict(base, basis="market", p_market=round(f["p"], 4), ev=None if ev is None else round(ev, 4),
                         books=len(f["books"]), independent_books=len(f["books"]), families=list(f["books"]), one_book=len(f["books"]) == 1,
                         books_label=ev_board.ONE_BOOK_LABEL if len(f["books"]) == 1 else f"{len(f['books'])} books", sources=[_source(s, labels) for s in f["sources"]],
                         left_out=[{"label": labels.get(d["book"], d["book"].title()), "line": d["line"], "reason": d["reason"],
                                    "age_s": None if d.get("age_s") is None or d["age_s"] != d["age_s"] else round(d["age_s"])}
                                   for d in f["dropped"]]))
    key = lambda r: (r.get("ev") is None, -(r.get("ev") or 0.0))
    rows.sort(key=key)
    # audit review 2026-11-07: the list is headed "ranked by Scout's chance", so every sport ranks by its chance (pushes off),
    # whether or not it shows a return figure (T5 hid it on NBA / NFL, which had sorted them below every CS2 / LoL leg)
    model_rows = [r for _, r in sorted(model_rows, key=lambda t: (-t[0], t[1]["key"], t[1]["side"]))]
    used = sorted({s["book"] for r in rows for s in r["sources"]} | usable)
    src = cfg.get(app) or {}
    ents = entries()
    verified = {e["app"] for e in ents}
    return {"as_of": wb._iso(now), "app": app, "app_label": APP_LABEL[app], "entry": entry, "n": n,
            "apps": [a for a in APPS if a in verified],
            "break_even": None if entry_be is None else round(entry_be, 6),
            "american": None if entry_be is None else round(payouts.american_of(entry_be)),
            "payout_source": {"source": src.get("source"), "read_at": src.get("read_at"), "version": src.get("version")},
            "payout": _payout(app, entry, n, cfg), "default_entry": dict(zip(("entry", "n"), default_entry(app))),
            "params": {"preset": p[3], "method": p[4], "min_books": p[5], "req": list(p[6]), "weights": dict(p[7]),
                       "min_liq": p[8], "max_width": p[9]},
            "options": {"entries": ents, "presets": sorted(fair.PRESETS) + ["custom"], "methods": list(fair.METHODS),
                        "books": [{"id": b, "label": labels.get(b, b.title())} for b in used],
                        "weight_choices": list(ev_board.WEIGHT_CHOICES), "min_liq_choices": list(ev_board.MIN_LIQUIDITY_CHOICES),
                        "max_width_choices": list(ev_board.MAX_WIDTH_CHOICES), "min_books_max": ev_board.MIN_BOOKS_MAX,
                        "min_books_default": ev_board.MIN_BOOKS_DEFAULT,
                        "freshness_s": price_limits(), "max_spread": fair.MAX_SPREAD},
            "plays": plays, "rows": rows, "rows_total": len(rows), "model_only": model_rows,
            "model_only_total": len(model_rows), "hidden": {"count": sum(hidden.values()), "by": hidden,
                                                             "text": ev_board.hidden_banner(hidden)},
            "sports": sorted(s for s, v in allowed.items() if v)}


ENTRY_WORD = {"power": "Power", "flex": "Flex", "standard": "Standard"}


def _payout(app, entry, n, cfg):
    """The payout this entry is priced on, with the day it was read - shown on the board as is (a fact with its date)."""
    src = cfg.get(app) or {}
    tbl = ((src.get("entries") or {}).get(entry) or {})
    v = tbl.get(str(n), tbl.get(n))
    day = str(src.get("read_at") or "")[:10]
    if isinstance(v, dict):
        tiers = ", ".join(f"{float(m):g}x all correct" if str(k) == "0" else f"{float(m):g}x with {k} missed"
                          for k, m in sorted(v.items(), key=lambda kv: int(kv[0])))
        text = f"{APP_LABEL[app]} {ENTRY_WORD.get(entry, entry)} {n}-pick pays {tiers}"
    elif v is not None:
        text = f"{APP_LABEL[app]} {ENTRY_WORD.get(entry, entry)} {n}-pick pays {float(v):g}x"
    else:
        return None
    return {"multiple": v, "read_at": src.get("read_at"), "version": src.get("version"),
            "text": text + (f", read from its help center on {day}." if day else ".")}


def _log_rows(doc):
    """The legs of one shared build that beat the entry, as record rows (workspace_ev_log)."""
    pay = doc.get("payout") or {}
    rows = []
    for r in doc["rows"]:
        if not (isinstance(r.get("ev"), (int, float)) and r["ev"] > 0):
            continue
        rows.append({"id": f"{r['key']}|{r['side']}", "leg": r["key"], "revision": r.get("revision"), "sport": r["sport"], "app": r["app"],
                     "player": r.get("player"), "stat": r.get("stat_label"), "period": r.get("period_label"), "start_utc": r.get("start_utc"),
                     "line": r["line"], "side": r["side"], "needed": r.get("be"), "fair": r.get("p_market"), "ev_pct": round(r["ev"] * 100, 2),
                     "families": r.get("families"), "one_book": r.get("one_book"), "multiplier": r.get("multiplier"),
                     "quotes": [{k: s.get(k) for k in ("book", "family", "kind", "line", "p", "age_s", "over", "under", "bid", "ask", "sit_out")}
                                for s in r.get("sources") or ()],
                     "entry": f"{doc['entry']}-{doc['n']}", "payout": pay.get("multiple"), "payout_version": pay.get("version"),
                     "payout_read_at": pay.get("read_at")})
    by = {}
    for r in rows:
        by[r["sport"]] = by.get(r["sport"], 0) + 1
    summary = {"app": doc["app"], "entry": f"{doc['entry']}-{doc['n']}", "needed": doc.get("break_even"), "priced": doc["rows_total"],
               "beat_entry": len(rows), "beat_entry_by_sport": by, "hidden": (doc.get("hidden") or {}).get("by"),
               "no_book_price": doc.get("model_only_total"), "sports": doc.get("sports")}
    return rows, summary


_KNOWN = {}


def rules_table():
    """The book family map and the settlement table, as rows (the admin record view)."""
    from gradecore import book_rules
    return book_rules.table()


def known_books(srv):
    """Books the NBA / NFL snapshots hold right now (for the required-books and per-book-weight parameters)."""
    from gradecore import book_rules
    _token, legs = _legs(srv)
    hit = _KNOWN.get("v")
    if hit is not None and hit[0] is legs:             # every member request asks: answered once per leg list, not per request
        return hit[1]
    pairs = {(base_book(b.get("platform")), leg["sport"]) for leg in legs for b in leg["book_rows"] or ()}
    out = sorted({b for b, sport in pairs if book_rules.for_pickem(b, sport)[0]})
    _KNOWN["v"] = (legs, out)
    return out


def shared(srv, q, allowed):
    """(status, body bytes): one build per (parameters, sports seen) every TTL s, for everyone."""
    p = params(q, known_books(srv))
    seen = tuple(sorted(s for s, v in allowed.items() if v))

    def compute():
        doc = build(srv, p, dict.fromkeys(seen, True))
        try:                                           # the record: queued here (once per shared build), written by its own thread
            import workspace_ev_log
            rows, summary = _log_rows(doc)
            workspace_ev_log.record("ev", rows, summary, params="|".join(str(x) for x in (p[0], p[1], p[2], p[3], p[4], p[5], ",".join(p[6]),
                                                                                              p[8], p[9], ",".join(seen))))
        except Exception:                              # noqa: BLE001 - the record never breaks the board
            pass
        return 200, json.dumps(doc, default=str, separators=(",", ":")).encode()
    return srv.shared(("ws_pickem_ev", p, seen), compute, ttl=TTL)
