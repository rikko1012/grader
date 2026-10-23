"""Reading stored sportsbook quotes: current offers with their age, compatible two-sided markets, and the market
consensus - the read contract the board, comparison views and models use (no provider is ever called from here).

current()      latest quote per offer (open or suspended), with seen_at (our last observation), age_s and fresh
               (seen within schedule.stale_after_s of its provider's ladder). Removed offers are not current.
two_sided()    a book's Over and Under ONLY when both are open, fresh, from the same provider and book, game, player,
               stat, period, market type and the SAME line. p_over = proportional no-vig. Nothing else is paired: a
               one-sided ladder rung or an Over-only line stays one-sided and gets no probability.
consensus()    per game / player / stat / period / line: the mean of the books' no-vig p_over over STANDARD two-sided
               markets from 2+ distinct books (a book counted once: its most recently seen pair). Every result lists
               the quote ids it used. This is the market's view; it is kept apart from the model's chance.
"""
from collections import defaultdict
from datetime import datetime, timezone

from .. import fair
from . import schedule, store
from .store import dt

MIN_BOOKS = 2


def _ladder(db, provider):
    if provider == "theoddsapi":
        v = store.setting(db, "odds_api_ladder", "minimal")
        return v if v in schedule.LADDERS else "minimal"
    return schedule.direct_ladder()


def current(db, league, now=None, event_key=None, player_key=None, market=None, period="game"):
    now = now or datetime.now(timezone.utc)
    sql = ("SELECT q.*, s.last_seen_at AS seen_at, s.first_seen_at, q.id AS quote_id FROM sports_book_offer_state s "
           "JOIN sports_book_quotes q ON q.id = s.last_quote_id WHERE s.league = ? AND s.last_status IN "
           "('open', 'suspended') AND q.start > ?")
    args = [league, store.iso(now)]
    for col, val in (("s.event_key", event_key), ("s.player_key", player_key), ("s.market", market),
                     ("q.period", period)):
        if val is not None:
            sql += f" AND {col} = ?"
            args.append(val)
    rows = db.all(sql, args)
    ladders = {}
    for r in rows:
        lad = ladders.setdefault(r["provider"], _ladder(db, r["provider"]))
        r["age_s"] = max(0, int((now - dt(r["seen_at"])).total_seconds()))   # never negative under clock skew
        r["fresh"] = r["age_s"] <= schedule.stale_after_s(lad, r["start"], now)
    return rows


def two_sided(quotes, fresh_only=True):
    groups = defaultdict(dict)
    for q in quotes:
        if q["status"] != "open" or (fresh_only and not q["fresh"]) or not q["event_key"] or \
                q["event_key"].startswith("unmatched:"):
            continue
        k = (q["provider"], q["book"], q["jurisdiction"], q["event_key"], q["player_key"], q["market"], q["period"],
             q["market_type"], q["line"])
        groups[k][q["side"]] = q
    out = []
    for k, sides in groups.items():
        if set(sides) != {"over", "under"}:
            continue
        o, u = sides["over"], sides["under"]
        nv = fair.no_vig(o["price_decimal"], u["price_decimal"])
        if not nv:
            continue
        out.append({"provider": k[0], "book": k[1], "jurisdiction": k[2], "event_key": k[3], "player_key": k[4],
                    "market": k[5], "period": k[6], "market_type": k[7], "line": k[8], "p_over": nv[0],
                    "overround": nv[1], "over": o, "under": u, "quote_ids": [o["quote_id"], u["quote_id"]],
                    "seen_at": min(o["seen_at"], u["seen_at"])})
    return out


def consensus(pairs, min_books=MIN_BOOKS):
    by = defaultdict(dict)
    for p in pairs:
        if p["market_type"] != "standard":
            continue
        k = (p["event_key"], p["player_key"], p["market"], p["period"], p["line"])
        held = by[k].get(p["book"])
        if held is None or p["seen_at"] > held["seen_at"]:
            by[k][p["book"]] = p                         # one book counted once: its newest pair
    out = {}
    for k, books in by.items():
        if len(books) < min_books:
            continue
        ps = list(books.values())
        out[k] = {"event_key": k[0], "player_key": k[1], "market": k[2], "period": k[3], "line": k[4],
                  "p_over": sum(p["p_over"] for p in ps) / len(ps), "books": sorted(books),
                  "quote_ids": sorted(q for p in ps for q in p["quote_ids"]),
                  "by_book": {b: {"p_over": p["p_over"], "over": p["over"]["price_american"],
                                  "under": p["under"]["price_american"], "provider": p["provider"]}
                              for b, p in books.items()}}
    return out


def board_index(db, league, now=None):
    """Everything the board needs in one read: {(event_key, player_key, market, line): entry} over fresh quotes of
    games mapped to ESPN ids. entry = {"start", "consensus" (2+ books) or None, "pairs" (each book's two-sided no-vig),
    "best" ({side: the best fresh standard / alternate price})}."""
    q = current(db, league, now)
    pairs = two_sided(q)
    idx = {}

    def entry(r):
        k = (r["event_key"], r["player_key"], r["market"], r["line"])
        e = idx.setdefault(k, {"start": r["start"], "consensus": None, "pairs": [], "best": {}})
        e["start"] = min(e["start"], r["start"]) if e["start"] and r["start"] else (e["start"] or r["start"])
        return e
    for p in pairs:
        entry(p["over"])["pairs"].append(p)
    for (ev, pk, mk, per, line), c in consensus(pairs).items():
        idx[(ev, pk, mk, line)]["consensus"] = c
    for r in q:
        if (not r["fresh"] or r["status"] != "open" or r["market_type"] == "promo" or not r["event_key"]
                or r["event_key"].startswith("unmatched:")):
            continue
        e = entry(r)
        cur = e["best"].get(r["side"])
        if cur is None or r["price_decimal"] > cur["price_decimal"]:
            e["best"][r["side"]] = r
    by_prop = defaultdict(list)
    for (ev, pk, mk, ln), e in idx.items():
        by_prop[(pk, mk, ln)].append(dict(e, event_key=ev))
    return dict(by_prop)


def lookup(index, player_key, market, line, start, tolerance_h=4):
    """The board index entry for one pick'em line: same player, stat and exact line, game starting within tolerance_h of
    the line's start. None when no book quotes that exact line (other lines are never substituted)."""
    t = dt(start)
    hits = [e for e in index.get((player_key, market, float(line)), ())
            if t and e["start"] and abs((dt(e["start"]) - t).total_seconds()) <= tolerance_h * 3600]
    return hits[0] if len(hits) == 1 else None
