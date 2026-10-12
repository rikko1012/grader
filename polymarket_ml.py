#!/usr/bin/env python3
"""
polymarket_ml.py - Polymarket CS2 SERIES-WINNER (moneyline) quotes, one per team outcome.

Separate from polymarket.py on purpose: polymarket.match_odds() feeds the prop grader (team_ml)
and keeps its exact behaviour.  This module only READS the same public data for the market
baseline / snapshot history (moneyline.py).

Docs used (checked 2026-10-12, docs.polymarket.com):
  Gamma  GET https://gamma-api.polymarket.com/events?series_id=10310&closed=false&end_date_min=..
         (limit/offset paging).  Rate limit: /events 500 req / 10 s (throttled, not rejected).
  CLOB   POST https://clob.polymarket.com/books  body [{"token_id": ..}, ..]  (500 req / 10 s)
         GET  https://clob.polymarket.com/book?token_id=..                    (1500 req / 10 s)
         Book: market, asset_id, timestamp (ms epoch), bids/asks [{price, size}], tick_size,
         min_order_size, last_trade_price.  The docs say bids are sorted best-first; live they
         arrive worst -> best, so we take max(bids) / min(asks) and never trust the order.
         Requests need a User-Agent header (403 without one).
One refresh = gamma pages (1 call today) + one /books call per 100 tokens.

Market structure: each CS2 event has ONE market with sportsMarketType "moneyline" (series winner,
question "Counter-Strike: A vs B (BO3) - Event").  Its two outcomes are complementary shares of
that ONE market (clobTokenIds = one token per outcome), so no de-vig is needed or done.
"child_moneyline" markets are map winners (scope map) and are never read.

source_ts = the CLOB book's own timestamp when the book was read; when only the Gamma listing is
available (CLOB down) source_ts is None - Gamma's updatedAt is market metadata, not a quote time.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import requests

from polymarket import CS2_SERIES, GAMMA   # shared constants only - match_odds() is untouched

CLOB = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "cs2-scout/1.0", "Accept": "application/json"}
TIMEOUT = 10
PAGE = 100
MAX_PAGES = 10
BOOK_BATCH = 100
PLATFORM = "polymarket"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _jl(x):
    if isinstance(x, list):
        return x
    try:
        v = json.loads(x or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def parse_start(s):
    if not s:
        return None
    try:
        t = str(s).strip().replace(" ", "T")
        if t.endswith("+00"):
            t += ":00"
        t = t.replace("Z", "+00:00")
        d = datetime.fromisoformat(t)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def series_market(ev):
    """The event's series-winner market, or None.  Map winners (child_moneyline) never qualify."""
    for m in ev.get("markets") or []:
        if m.get("sportsMarketType") == "moneyline" and not re.search(r"\bMap \d", m.get("question") or ""):
            return m
    return None


# ---------------------------------------------------------------- fetch (network)

def fetch_events(session=None, days_back=1):
    get = (session or requests).get
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events, offset = [], 0
    for _ in range(MAX_PAGES):
        r = get(f"{GAMMA}/events", headers=HEADERS, timeout=TIMEOUT, params={
            "series_id": CS2_SERIES, "closed": "false", "end_date_min": since,
            "limit": PAGE, "offset": offset})
        r.raise_for_status()
        batch = r.json() or []
        events += batch
        if len(batch) < PAGE:
            break
        offset += PAGE
    return events


def fetch_books(token_ids, session=None):
    post = (session or requests).post
    books = {}
    ids = list(dict.fromkeys(t for t in token_ids if t))
    for i in range(0, len(ids), BOOK_BATCH):
        r = post(f"{CLOB}/books", json=[{"token_id": t} for t in ids[i:i + BOOK_BATCH]],
                 headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for b in r.json() or []:
            if b.get("asset_id"):
                books[str(b["asset_id"])] = b
    return books


def fetch(session=None, now=None):
    """One refresh.  Gamma failing fails the refresh; the CLOB failing does not (quotes then come
    from Gamma's best bid/ask with no sizes - shown, but never usable)."""
    events = fetch_events(session)
    received = now_iso()
    now = now or datetime.now(timezone.utc)
    tokens = []
    for ev in events:
        m = series_market(ev)
        if not m or m.get("closed"):
            continue
        st = parse_start(m.get("gameStartTime"))
        if st is not None and st <= now:
            continue
        tokens += [str(t) for t in _jl(m.get("clobTokenIds"))]
    books, book_error, book_ts = {}, None, None
    if tokens:
        try:
            books = fetch_books(tokens, session)
            book_ts = now_iso()
        except Exception as e:      # noqa: BLE001 - CLOB down never blocks the listing
            book_error = f"{type(e).__name__}: {str(e)[:120]}"
    return {"events": events, "received_ts": received, "books": books,
            "book_ts": book_ts, "book_error": book_error}


# ---------------------------------------------------------------- normalize

def _book_side(levels):
    out = []
    for lv in levels or []:
        p, s = _f((lv or {}).get("price")), _f((lv or {}).get("size"))
        if p is not None and s and s > 0:
            out.append((p, s))
    return out


def book_quote(book):
    bids, asks = _book_side(book.get("bids")), _book_side(book.get("asks"))
    bid, ask = max(bids, default=None), min(asks, default=None)
    ts = book.get("timestamp")
    src = None
    try:
        v = float(ts)
        src = datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return {
        "bid": bid[0] if bid else None, "ask": ask[0] if ask else None,
        "bid_size": bid[1] if bid else None, "ask_size": ask[1] if ask else None,
        "depth": {"bid_best": bid[1] if bid else None, "ask_best": ask[1] if ask else None,
                  "bid_5c": round(sum(s for p, s in bids if bid and p >= bid[0] - 0.05 - 1e-9), 2) if bid else 0.0,
                  "ask_5c": round(sum(s for p, s in asks if ask and p <= ask[0] + 0.05 + 1e-9), 2) if ask else 0.0,
                  "source": "clob book"},
        "source_ts": src,
    }


def gamma_quote(m, idx):
    """Gamma's bestBid/bestAsk quote outcome 0; outcome 1 is the complement of the SAME market.
    No sizes here, so such a quote can never pass the depth check."""
    bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
    if bid is not None and bid <= 0:
        bid = None
    if ask is not None and ask >= 1:
        ask = None
    if idx == 1:
        bid, ask = (round(1 - ask, 4) if ask is not None else None), (round(1 - bid, 4) if bid is not None else None)
    return {"bid": bid, "ask": ask, "bid_size": None, "ask_size": None,
            "depth": {"bid_best": None, "ask_best": None, "bid_5c": None, "ask_5c": None,
                      "source": "gamma listing (no order book sizes)"},
            "source_ts": None}


def _status(m):
    if m.get("closed"):
        return "closed"
    if m.get("umaResolutionStatus"):
        return f"resolving ({m.get('umaResolutionStatus')})"
    if m.get("acceptingOrders") and m.get("active", True):
        return "open"
    return "not accepting orders"


def normalize(raw):
    """Raw fetch -> list of match listings (one per CS2 event with a series moneyline):
    {platform, event_id, teams [t0, t1], start, bo, event_title, url, rules, live, quotes [q0, q1], problem}"""
    out = []
    books = raw.get("books") or {}
    for ev in raw.get("events") or []:
        m = series_market(ev)
        if not m:
            continue
        outcomes = [str(o) for o in _jl(m.get("outcomes"))]
        tokens = [str(t) for t in _jl(m.get("clobTokenIds"))]
        q_text = m.get("question") or ev.get("title") or ""
        bo = re.search(r"\(BO(\d)\)", q_text, re.I)
        evt = re.search(r"\)\s*-\s*(.+)$", q_text)
        start = parse_start(m.get("gameStartTime")) or parse_start(ev.get("startTime"))
        status = _status(m)
        rules = (m.get("description") or ev.get("description") or "").strip()[:1500]
        if m.get("resolutionSource"):
            rules += f" Resolution source: {m.get('resolutionSource')}"
        listing = {
            "platform": PLATFORM, "event_id": str(ev.get("id") or ""),
            "teams": [o.strip() for o in outcomes], "start": _iso(start),
            "bo": int(bo.group(1)) if bo else None,
            "event_title": evt.group(1).strip() if evt else (ev.get("title") or ""),
            "url": f"https://polymarket.com/event/{ev.get('slug')}" if ev.get("slug") else None,
            "rules": rules, "live": bool(ev.get("live")) or bool(ev.get("ended")),
            "quotes": [], "problem": None,
        }
        if len(outcomes) != 2 or len(tokens) != 2:
            listing["problem"] = f"{len(outcomes)} outcomes / {len(tokens)} tokens (expected 2)"
            out.append(listing)
            continue
        last0 = _f(m.get("lastTradePrice"))
        for i in range(2):
            book = books.get(tokens[i])
            q = book_quote(book) if book else gamma_quote(m, i)
            if not book and raw.get("book_error"):
                q["depth"]["error"] = raw["book_error"]
            last = None if last0 is None or last0 <= 0 else (last0 if i == 0 else round(1 - last0, 4))
            q.update({
                "platform": PLATFORM, "event_id": listing["event_id"], "market_id": str(m.get("id")),
                "outcome_id": tokens[i], "team_name": outcomes[i].strip(), "scope": "series",
                "last_trade": {"price": last, "label": "last trade (not a quote)"} if last is not None else None,
                "received_ts": (raw.get("book_ts") if book else None) or raw.get("received_ts"),
                "status": status, "market_open": status == "open",
                "rules": rules, "start": listing["start"], "bo": listing["bo"],
                "event_title": listing["event_title"],
                "raw": {"condition_id": m.get("conditionId"), "outcome_index": i, "outcome": outcomes[i],
                        **{k: m.get(k) for k in ("bestBid", "bestAsk", "spread", "lastTradePrice", "outcomePrices",
                                                 "liquidityNum", "acceptingOrders", "closed", "gameStartTime",
                                                 "updatedAt", "umaResolutionStatus", "resolutionSource")},
                        "book_hash": (book or {}).get("hash"), "book_timestamp": (book or {}).get("timestamp")},
            })
            listing["quotes"].append(q)
        out.append(listing)
    return out


def snapshot(session=None):
    return normalize(fetch(session))


if __name__ == "__main__":
    ls = snapshot()
    print(json.dumps([{k: v for k, v in x.items() if k not in ("quotes", "rules")} for x in ls], indent=1)[:4000])
    print(len(ls), "events")
