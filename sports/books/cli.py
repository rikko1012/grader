"""Sportsbook commands (registered in sports/scout.py) and the board hook. Everything here READS storage, except
`books-run`, which runs the worker - the only code that calls a provider.

  books-run    [--once] [--leagues nba,nfl]      run the worker (one pass with --once)
  books-health                                   per book: direct feed, documented feed, coverage, last success, blocker
  books-plan                                     requests / credits per day from the real NBA / NFL schedule
  books LEAGUE [--player NAME] [--market KEY]    fresh matched offers side by side, no-vig where both sides exist
  books-verify [--providers theoddsapi,...]      owner check: the next NBA + NFL game per provider, fetched once
"""
from datetime import datetime, timezone

from .. import config, names
from . import compare, plan, providers, schedule, store, worker


def _table(rows, cols):
    if not rows:
        print("  (none)")
        return
    w = [max(len(c), *(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w[i]) for i, v in enumerate(r)))


def _age(s):
    if s is None:
        return "-"
    return f"{s // 60}m" if s < 3600 else (f"{s // 3600}h{(s % 3600) // 60:02d}m" if s < 86400 else f"{s // 86400}d")


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def _am(x):
    return "-" if x is None else (f"+{x}" if x > 0 else str(x))


def cmd_run(a):
    if a.once:
        db = store.connect()
        try:
            print(worker.run_once(db, tuple(x for x in a.leagues.split(",") if x)))
        finally:
            db.close()
    else:
        worker.main(["--leagues", a.leagues])


def cmd_verify(a):
    db = store.connect()
    try:
        out = worker.verify(db, [p for p in a.providers.split(",") if p] or None)
    finally:
        db.close()
    for k, v in out.items():
        print(f"  {k}: {v}")


def health(db, now=None):
    """-> one row per book: the status table's data (also used by tests)."""
    now = now or datetime.now(timezone.utc)
    from .. import config
    feed_ok = bool(config.odds_api_key())      # polled by the canonical path (books/sources.oddsapi_ingest), not here
    feed_why = "" if feed_ok else "ODDS_API_KEY is not set (The Odds API is polled by sports.worker, source oddsapi)"
    op_ok, op_why = providers.PROVIDERS["oddspapi"].configured()
    rows = []
    for book in providers.BOOKS:
        direct = next((p for p in providers.ROUTES[book] if p not in ("theoddsapi", "oddspapi")), None) \
            or f"direct:{book}"
        st = store.provider_state(db, direct)
        last_ok = db.one("SELECT MAX(finished_at) AS t FROM sports_book_fetches WHERE provider = ? AND scope = 'props' "
                         "AND ok = 1 AND n_offers > 0", (direct,))["t"]
        feed_last = db.one("SELECT MAX(q.observed_at) AS t FROM sports_book_quotes q WHERE q.provider = 'theoddsapi' "
                           "AND q.book = ?", (book,))["t"]
        cov = {}
        for lg in ("nba", "nfl"):
            r = db.one("SELECT COUNT(*) AS n, COUNT(DISTINCT q.market) AS m FROM sports_book_offer_state s JOIN "
                       "sports_book_quotes q ON q.id = s.last_quote_id WHERE q.book = ? AND s.league = ? AND "
                       "s.last_status = 'open' AND q.start > ?", (book, lg, store.iso(now)))
            cov[lg] = (r["n"], r["m"])
        if direct.startswith("direct:"):
            dstat = "blocked" if st["blocked"] else ("not probed yet" if not st["last_error_at"] and not st["last_ok_at"]
                                                     else "answers")
        else:
            dstat = ("connected" if last_ok else "not fetched yet") if not st["blocked"] else "blocked"
        blocker = None
        if direct.startswith("direct:") and st["blocked"]:
            blocker = f"own feed refuses this server ({(st['last_error'] or '')[:60]})"
        elif st["last_error"] and st["failures"]:
            blocker = st["last_error"][:80]
        if not feed_ok:
            blocker = (blocker + "; " if blocker else "") + f"documented feed: {feed_why}"
        if book == "caesars":
            blocker = (blocker + "; " if blocker else "") + "Odds API lists Caesars on paid plans only"
        op_last = db.one("SELECT MAX(q.observed_at) AS t FROM sports_book_quotes q WHERE q.provider = 'oddspapi' "
                         "AND q.book = ?", (book,))["t"]
        rows.append({"book": book, "direct": dstat, "direct_provider": direct, "feed": "on" if feed_ok else "off",
                     "oddspapi": "on" if op_ok else "off", "oddspapi_why": None if op_ok else op_why,
                     "nba": cov["nba"], "nfl": cov["nfl"], "last_ok": last_ok, "feed_last": feed_last,
                     "oddspapi_last": op_last, "blocker": blocker})
    return rows


def cmd_health(a):
    db = store.connect()
    try:
        rows = health(db)
    finally:
        db.close()
    _table([(r["book"], r["direct"], r["feed"], r["oddspapi"], f"{r['nba'][0]} offers / {r['nba'][1]} stats",
             f"{r['nfl'][0]} offers / {r['nfl'][1]} stats",
             r["last_ok"] or r["oddspapi_last"] or r["feed_last"] or "-", r["blocker"] or "-")
            for r in rows], ["book", "own feed", "Odds API", "OddsPapi", "NBA open props", "NFL open props",
                             "latest success", "blocker"])
    why = next((r["oddspapi_why"] for r in rows if r["oddspapi_why"]), None)
    if why:
        print(f"\n  OddsPapi: {why}")


def cmd_oddspapi(a):
    """Owner checks for the shared OddsPapi account. account: GET /v4/account (not metered) - entitlements and the
    quota guard's numbers; the key itself is never printed. catalog: NBA / NFL tournament ids and every player-prop
    market with the stat it maps to (spends up to 3 requests, only through the guard, and needs SPORTS_ODDSPAPI=1)."""
    from . import oddspapi as op
    prov = providers.PROVIDERS["oddspapi"]
    if not op.api_key():
        print("  ODDSPAPI_API_KEY (or odds_papi_api) is not set in this environment: run this on the service that "
              "holds the CS2 OddsPapi key")
        return
    from .base import FetchResult
    res = FetchResult()
    acc = prov.account(res)
    if not isinstance(acc, dict):
        print(f"  /v4/account failed: {res.error}")
        return
    sub = op.subscription(acc) or {}
    db = store.connect()
    try:
        now = datetime.now(timezone.utc)
        ok, why, nums = op.guard(acc, op.used_this_month(db, now), op.allocation(), op.cs2_floor())
        print(f"  subscription: active={bool(sub)} valid {sub.get('valid_from')} -> {sub.get('valid_until')} "
              f"sports {sub.get('sport_ids')}")
        print(f"  quota: {nums or '-'}")
        for b in op.BOOK_PRIORITY:
            e = (sub.get("bookmakers") or {}).get(b)
            print(f"  {b:11s} listed={e is not None} player_props={bool((e or {}).get('has_player_props'))}")
        print(f"  books Sports would read: NBA {op.books_for(acc, 'basketball')}  NFL {op.books_for(acc, 'football')}")
        print(f"  guard: {'a request would be allowed' if ok else 'no request: ' + why}")
        print(f"  provider: {'on' if prov.configured()[0] else 'off - ' + prov.configured()[1]}")
        if a.what == "catalog":
            if not prov.configured()[0]:
                print("  catalog needs the provider on (SPORTS_ODDSPAPI=1 and a monthly allocation)")
                return
            for key in ("nba", "nfl"):
                lg = config.league(key)
                r2 = FetchResult()
                cat = prov.catalog(db, lg, now, acc, r2)
                if cat is None:
                    print(f"  {key}: {r2.budget_denied or r2.error}")
                    continue
                tid, parts, mkts = cat
                print(f"  {key}: tournament {tid}, {len(parts)} participants")
                for mid, info in sorted(mkts.items()):
                    if info["sport"] == op.SPORT[lg["family"]]:
                        print(f"    {mid:>6} {info['name']!r:45} period={info['period']!r:12} -> "
                              f"{op.map_market(lg['family'], info['name']) or 'NOT MAPPED'}")
    finally:
        db.close()


def cmd_plan(a):
    now = datetime.now(timezone.utc)
    g = plan.games(("nba", "nfl"), now)
    est = plan.estimate(g, now)
    print(f"  games on ESPN's schedule, next 7 days: " + ", ".join(f"{k.upper()} {v}" for k, v in est["games"].items()))
    print(f"  direct feeds (no credits), 'full' ladder: " +
          ", ".join(f"{p} ~{n:,} requests/day" for p, n in est["direct"].items()))
    rows = []
    for lad in schedule.ORDER:
        c = est["odds_api"][lad]
        fits = [name for name, m in plan.PLANS.items() if c <= plan.daily_allowance(m)]
        rows.append((lad, est["per_game"][lad], f"{c:,}", f"{c * 30:,}", fits[0] if fits else "none listed"))
    print("  The Odds API (credits = markets x 1 region per game call; upper bound - only markets returned are billed):")
    _table(rows, ["ladder", "fetches/game", "credits/day", "credits/30 days", "smallest plan that fits"])
    m = plan.monthly_credits()
    print(f"\n  configured monthly credits: {m:,} (SPORTS_ODDS_API_MONTHLY_CREDITS) -> worker ladder: "
          f"{plan.choose_ladder(est, m) or 'none fits'}")


def cmd_view(a):
    lg = config.league(a.league)
    now = datetime.now(timezone.utc)
    db = store.connect()
    try:
        q = compare.current(db, lg["key"], now, market=a.market)
    finally:
        db.close()
    if a.player:
        q = [r for r in q if names.key(a.player) in (r["player_key"] or "")]
    pairs = {(p["book"], p["event_key"], p["player_key"], p["market"], p["line"], p["market_type"]): p
             for p in compare.two_sided(q, fresh_only=False)}
    cons = compare.consensus(compare.two_sided(q))
    grouped = {}
    for r in q:
        k = (r["event_key"], r["player"], r["market"], r["line"])
        grouped.setdefault(k, []).append(r)
    out = []
    for (ev, player, mk, line), rs in sorted(grouped.items(), key=lambda x: (x[0][1] or "", x[0][2], x[0][3])):
        c = cons.get((ev, names.key(player), mk, "game", line))
        books = {}
        for r in rs:
            books.setdefault((r["book"], r["market_type"]), {})[r["side"]] = r
        for (book, mtype), sides in sorted(books.items()):
            p = pairs.get((book, ev, names.key(player), mk, line, mtype))
            ages = [s["age_s"] for s in sides.values()]
            stale = not all(s["fresh"] for s in sides.values())
            out.append((player, mk, line, book, mtype[:3], _am((sides.get("over") or {}).get("price_american")),
                        _am((sides.get("under") or {}).get("price_american")),
                        _pct(p["p_over"]) if p else "one side" if len(sides) == 1 else "-",
                        f"{_pct(c['p_over'])} ({len(c['books'])})" if c else "-",
                        _age(max(ages)) + (" STALE" if stale else ""),
                        "unmatched" if (ev or "").startswith("unmatched:") else ""))
    print(f"  {len(q)} stored offers for {lg['name']} (latest per offer; ages are since OUR last observation)")
    _table(out[:a.limit], ["player", "stat", "line", "book", "type", "over", "under", "no-vig over",
                           "consensus", "seen", ""])
    if len(out) > a.limit:
        print(f"  ... {len(out) - a.limit} more rows (--limit)")


def attach(lg, rows, db=None, now=None):
    """Board hook: each pick'em row gains "books" - the stored market view of its EXACT line (never another line):
    consensus over the offered side (2+ books, standard, fresh) with the quote ids used, each book's no-vig, and the
    best fresh price per side. Read-only; a missing / empty store leaves "books" None."""
    own = db is None
    db = db or store.connect()
    try:
        idx = compare.board_index(db, lg["key"], now)
    finally:
        if own:
            db.close()
    for r in rows:
        e = compare.lookup(idx, r.get("player_key"), r.get("market"), r.get("line"), r.get("start")) \
            if r.get("player_key") and r.get("market") and r.get("line") is not None else None
        if not e:
            r["books"] = None
            continue
        c = e["consensus"]
        side = r.get("side") or "over"
        r["books"] = {
            "line": r["line"], "event_key": e["event_key"],
            "consensus_over": c["p_over"] if c else None,
            "consensus_side": (c["p_over"] if side == "over" else 1 - c["p_over"]) if c else None,
            "consensus_books": c["books"] if c else [], "quote_ids": c["quote_ids"] if c else [],
            "pairs": {p["book"]: round(p["p_over"], 4) for p in e["pairs"]},
            "best": {s: {"book": q["book"], "price": q["price_american"], "quote_id": q["quote_id"]}
                     for s, q in e["best"].items()}}
    return rows


def label(b, side):
    """Short board cell: '54.2% 3bk' (consensus for the offered side) / '1bk -110' / ''."""
    if not b:
        return ""
    if b["consensus_side"] is not None:
        return f"{_pct(b['consensus_side'])} {len(b['consensus_books'])}bk"
    best = b["best"].get(side)
    return f"{best['book'][:3]} {_am(best['price'])}" if best else ""


def cmd_inventory(argv=None):
    """python -m sports.books.cli inventory|inventory-show oddspapi <nba|nfl>"""
    import argparse
    from . import inventory
    ap = argparse.ArgumentParser(prog="python -m sports.books.cli")
    ap.add_argument("what", choices=("inventory", "inventory-show"))
    ap.add_argument("source", choices=("oddspapi",))
    ap.add_argument("league", choices=("nba", "nfl"))
    a = ap.parse_args(argv)
    s = inventory.capture(a.source, a.league) if a.what == "inventory" else inventory.read_summary(a.source, a.league)
    inventory.render(s)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(cmd_inventory(sys.argv[1:]))
