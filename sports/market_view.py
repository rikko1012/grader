"""Board market view (mv-1): every book that prices the identical market, each with its own chance, plus the market
consensus by the mc-1 RULE - built in the WORKER (sports/snapshots.py), never in a member request.

Owner decision 2026-10-23: every book we hold is a PRIMARY market source. The board's headline "Market (N books)" is
therefore the mc-1 combination rule (sports/model/market.py, frozen 2026-10-23) applied to every VERIFIED source on the
row: Kalshi, Polymarket, each Odds API book (oddsapi:<book>) and the books' own feeds (fanduel, betrivers).
  - DISPLAY FRESHNESS = OBSERVED AGE: a quote counts when Scout re-read it and found it unchanged within 15 min,
    whatever the book's own last-change time (BetRivers' changedDate on a steady price can be hours old). This is the
    only difference from the registered mc-1 forecasts, which keep the source-time age; source time still orders
    revisions and detects late answers (LINE_CORRECTNESS.md).
  - per source, the frozen eligibility filters of market.quote_prob (fresh <= 15 min, before the start, open, two-sided
    priced pair de-vigged multiplicatively per book; Kalshi = eligible rung YES midpoint; Polymarket = two-sided YES
    midpoint, spread <= 0.05, liquidity > 0, divided by 1 - DNP share);
  - per source the quote nearest the target line, moved to the target line with the dispersion.json shape;
  - equal-weight mean of log-odds across sources (one vote per source); never best-price normalisation across books;
  - the target's own platform is never a source for it (a PrizePicks / Underdog board is never a source at all).
The REGISTERED mc-1 forecasts (sports/model/forward.py) keep their registered source set (REGISTER.md 10, amendment A4:
the direct book feeds are single-source candidates there). This view is a display of the market, not a model record;
its version string is DISPLAY_VERSION and the manifest names the rule and the source set.

Capture-only sources (sports/books SOURCES verified=False: thunderpick, oddspapi) are listed with "format not
verified" and never enter a chance.

Per-book chance shown (its own line, P(Over | no push) - a two-way book refunds a push):
  devig      both sides priced: (1/o) / (1/o + 1/u) for that book's exhaustive pair only
  mid        exchange midpoint (Kalshi YES, Polymarket YES) when bid and ask both exist
  one_sided  only one side priced: the price is shown, no chance (a one-sided price is not a probability)
A book that is shown but not in the consensus carries the frozen filter's reason (e.g. "older than 15 min").
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from . import capabilities
from .contracts import parse_utc

DISPLAY_VERSION = "mv-1"
TARGET_BOARDS = ("prizepicks", "underdog")          # pick'em boards graded against; never market sources
DIRECT_BOOKS = ("fanduel", "betrivers")
_ART = Path(__file__).resolve().parent / "model" / "artifacts" / "market_mc1.json"


def dnp_share(league):
    try:
        return float(json.loads(_ART.read_text(encoding="utf-8")).get("dnp_share", {}).get(league) or 0.0)
    except (OSError, ValueError):
        return 0.0


def source_status(platform):
    """-> (in_display_consensus, reason_if_not). Verified flags from sports/books SOURCES."""
    from .books import SOURCES
    if platform in TARGET_BOARDS:
        return False, "pick'em board (graded against, never a market source)"
    base = platform.split(":", 1)[0]
    meta = SOURCES.get(base)
    if meta is None:
        return False, "unknown source"
    if not meta["verified"]:
        return False, "capture-only: prop format not verified"
    return True, None


def is_book(platform):
    return platform not in TARGET_BOARDS


def reading(q):
    """A book's own chance at its own line. -> dict(kind, c, over, under) - c = P(Over | no push) or None."""
    o, u = q.side("over"), q.side("under")
    out = {"kind": "none", "c": None, "over": None, "under": None}
    if q.platform in ("kalshi", "polymarket"):
        if o is not None and o.bid is not None and o.ask is not None:
            out.update(kind="mid", c=(o.bid + o.ask) / 2, over=[o.bid, o.ask])
        elif o is not None and (o.bid is not None or o.ask is not None):
            out.update(kind="one_sided", over=[o.bid, o.ask])
        return out
    op = o.price if (o and o.offered and o.price and o.price > 1) else None
    up = u.price if (u and u.offered and u.price and u.price > 1) else None
    out["over"], out["under"] = op, up
    if op and up:
        io, iu = 1 / op, 1 / up
        out.update(kind="devig", c=io / (io + iu))
    elif op or up:
        out["kind"] = "one_sided"
    return out


def _age(now, t):
    try:
        return max(0, int((parse_utc(now) - parse_utc(t)).total_seconds()))
    except Exception:                                   # noqa: BLE001 - unknown time = unknown age
        return None


CLAMP_S = 300


def _clamp(meta, now):
    """A re-confirmation stamped during this build (ingest runs beside it) counts as seen at `now`; anything later than
    CLAMP_S after `now` is left as is (and the frozen filter then excludes it)."""
    ls = meta.get("last_seen_at")
    if ls and ls > now:
        try:
            if (parse_utc(ls) - parse_utc(now)).total_seconds() <= CLAMP_S:
                return {**meta, "last_seen_at": now}
        except Exception:                               # noqa: BLE001
            pass
    return meta


def book_list(target, items, event, now, dnp):
    """Every book quote of the identical market (capabilities.compatible with the target), nearest line first per book.
    items: [(rid, OfferSnapshot, meta)] with meta last_seen_at / last_source_updated_at.
    -> [dict(p, l, kind, c, over, under, t, su, age, in, why, rid)] one entry per source (its quote nearest the
    target line; ties: the lower line), and per source the eligibility verdict of the frozen filters."""
    from .model import market
    best = {}
    for rid, q, meta in items:
        meta = _clamp(meta, now)
        if not is_book(q.platform):
            continue
        ok, why = capabilities.compatible(target, q)
        if not ok:
            continue                                     # different scope / variant / promo: not the same market
        if reading(q)["kind"] == "none":
            continue                                     # owner rule: a book without odds is never shown (no price,
            #                                              no bid / ask: nothing to show or count) - its priced lines stay
        cand = (abs(q.line - target.line), q.line, rid, q, meta)
        cur = best.get(q.platform)
        if cur is None or cand[:2] < cur[:2]:
            best[q.platform] = cand
    out = []
    for plat, (_, line, rid, q, meta) in sorted(best.items()):
        r = reading(q)
        seen = max(x for x in (q.observed_at, meta.get("last_seen_at")) if x)
        ok_src, why_src = source_status(plat)
        p, why = (None, why_src)
        if ok_src:
            # DISPLAY freshness = OUR observation age (re-read and confirmed within 15 min); the source's own change
            # time is not used here, so a steady price re-read minutes ago is current. The registered mc-1 / mi-1
            # forecasts keep market.quote_prob's source-time age unchanged (REGISTER.md 10); ordering and late
            # detection keep using source time (LINE_CORRECTNESS.md).
            qd = dataclasses.replace(q, source_updated_at=None) if q.source_updated_at else q
            p, why = market.quote_prob(qd, event, now, meta.get("last_seen_at"),
                                       dnp if plat == "polymarket" else 0.0, None, display_books=("bovada",))
        out.append({"p": plat, "l": line, "rid": rid, "kind": r["kind"], "c": r["c"], "over": r["over"],
                    "under": r["under"], "t": seen, "su": meta.get("last_source_updated_at") or q.source_updated_at,
                    "age": _age(now, seen), "in": p is not None, "pc": p, "why": None if p is not None else why})
    return out


def consensus_at(target, books, league, exclude=None):
    """mc-1 rule over the eligible books of book_list(), at target.line, excluding platform `exclude`.
    -> dict(po, pu, pp, n, used) or None."""
    from .model import market
    per = {b["p"]: (b["rid"], b["l"], b["pc"]) for b in books if b["in"] and b["p"] != exclude}
    if not per:
        return None
    res = market.consensus(target, per)
    if res is None:
        return None
    po, pu, pp, used = res
    return {"po": round(po, 4), "pu": round(pu, 4), "pp": round(pp, 4), "n": len(used), "used": used}


FALLBACK_FLAG = "SPORTS_MARKET_FALLBACK"


def fallback_on(env=None):
    """SPORTS_MARKET_FALLBACK (default off, owner on): write the display-only books fallback into the snapshot."""
    import os
    return ((os.environ if env is None else env).get(FALLBACK_FLAG) or "").strip().lower() in ("1", "true", "yes", "on")


def fallback_at(books, line):
    """Display-only Market-column fallback for a row whose consensus is None (every book failed the frozen 15-minute
    rule): the listed books' OWN readings (devig / mid) at exactly `line`, equal-weight mean of log-odds, with the
    oldest observation age. -> {po, pu, n, l, books, age, why} or None. It is a label for a stale cell - never a lean,
    edge, gap, confidence or sort input (board_view.market_at / table.js marketAt read `c` and `at` only)."""
    import math
    use = [b for b in books if b.get("kind") in ("devig", "mid") and b.get("c") is not None and b.get("l") == line
           and 0.0 < float(b["c"]) < 1.0]
    if not use:
        return None
    logits = [math.log(float(b["c"]) / (1.0 - float(b["c"]))) for b in use]
    p = 1.0 / (1.0 + math.exp(-sum(logits) / len(logits)))
    ages = [b["age"] for b in use if isinstance(b.get("age"), (int, float))]
    return {"po": round(p, 4), "pu": round(1.0 - p, 4), "n": len(use), "l": line, "books": sorted(b["p"] for b in use),
            "age": max(ages) if ages else None, "why": "no book passed the 15-minute rule; books' own prices, display only"}


def row_view(targets, items, event, now, league, dnp, fallback=False):
    """One board row. targets: [(rid, OfferSnapshot)] ordinary quotes on the row (pick'em + books, the lines a member
    can act on); items: every offer of the row's market incl. Kalshi rungs. -> compact dict:
      l   headline line (first pick'em line, else the first book line)
      c   headline consensus {po, pu, pp, n, used} at l, or None
      bk  per-book list (book_list)
      at  {record_id: consensus excluding that quote's own platform, at its line} for every target quote whose value
          differs from `c` (absent = same as the headline)
      fb  only with fallback=True (SPORTS_MARKET_FALLBACK) and c None: fallback_at(books, l) when it exists"""
    if not targets:
        return None
    pick = [t for t in targets if t[1].platform in TARGET_BOARDS] or targets
    head = pick[0][1]
    books = book_list(head, items, event, now, dnp)
    at = {}
    cache = {}
    lists = {head.line: books}
    for rid, t in targets:
        # the exclusion is decided on the book list actually used at t.line (bug sweep 2026-11-06 #8): the head's list
        # can hold a different (stale) quote of t's platform, which let t's own book into its own consensus
        if t.line not in lists:
            lists[t.line] = book_list(t, items, event, now, dnp)
        tb = lists[t.line]
        key = (t.line, t.platform if any(b["p"] == t.platform and b["in"] for b in tb) else None)
        if key not in cache:
            cache[key] = consensus_at(t, tb, league, exclude=key[1])
        at[rid] = cache[key]
    head_c = at[pick[0][0]]
    slim = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items() if k not in ("rid", "pc")}
            for b in books]
    # `at` keeps only quotes whose market chance differs from the headline (the UI falls back to `c`): size
    out = {"l": head.line, "hp": head.platform, "c": head_c, "bk": slim,
           "at": {rid: v for rid, v in at.items() if v != head_c}}
    if fallback and head_c is None:
        fb = fallback_at(slim, head.line)
        if fb:
            out["fb"] = fb
    return out
