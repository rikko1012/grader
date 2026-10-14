#!/usr/bin/env python3
"""
kalshi.py - Kalshi CS2 SERIES-WINNER markets, normalized to one quote per team (public data, no key).

Docs used (checked 2026-10-12, docs.kalshi.com, trade-api v2):
  GET /events?series_ticker=KXCS2GAME&status=open&with_nested_markets=true   (cursor paging, limit <= 200)
  GET /markets/{ticker}/orderbook   -> {"orderbook_fp": {"yes_dollars": [[price, size]...], "no_dollars": [...]}}
      The book holds BIDS only.  A NO bid at X is a YES ask at 1 - X (same market, same size).
      Live: levels arrive worst -> best, so we take max() rather than trusting the order.
  Rate limits: Basic tier read budget = 200 tokens/s, default 10 tokens per request (~20 req/s).
      One refresh = 1 events call + 1 orderbook call per open, not-started market (~30-60 calls/min),
      4 at a time, 40 s budget (a book not read in time falls back to the market's own best levels).
Host: api.elections.kalshi.com/trade-api/v2 (also documented: external-api.kalshi.com/trade-api/v2).

Market structure (verified live): one EVENT per match (KXCS2GAME-26OCT121300MEGUNK) with TWO markets,
one per team ("<team> wins", yes_sub_title = team).  Each market is its own YES/NO contract:
NO of team A is the complement of team A's YES - it is NOT team B's YES.  KXCS2MAP (map winner) is a
different scope and is never read here.

Kalshi supplies no quote timestamp (updated_time is "last non-trading metadata update"), so
source_ts is always None; received_ts is our fetch time.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXCS2GAME"                 # series winner.  KXCS2MAP = map winner (never used)
HEADERS = {"User-Agent": "cs2-scout/1.0", "Accept": "application/json"}
TIMEOUT = 10
PAGE = 200
MAX_PAGES = 10
PLATFORM = "kalshi"
BOOK_WORKERS = 4          # parallel orderbook reads (~6 req/s, well under the 20 req/s basic budget)
BOOK_BUDGET = 40          # seconds; books not started by then fall back to the market's best levels

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- fetch (network)

def fetch_events(session=None, status="open"):
    """All open KXCS2GAME events with their markets nested.  Raises on HTTP / timeout errors."""
    get = (session or requests).get
    events, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"series_ticker": SERIES, "status": status, "with_nested_markets": "true", "limit": PAGE}
        if cursor:
            params["cursor"] = cursor
        r = get(f"{API}/events", params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        events += j.get("events") or []
        cursor = j.get("cursor")
        if not cursor:
            break
    return events


def fetch_orderbook(ticker, session=None):
    get = (session or requests).get
    r = get(f"{API}/markets/{ticker}/orderbook", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch(session=None, now=None):
    """One refresh: events + one orderbook per open, not-started team market.
    An orderbook failure never fails the refresh - that quote falls back to the market's own
    best bid/ask and sizes (depth within 5c then unknown).  Returns raw data + receive times."""
    events = fetch_events(session)
    received_list = now_iso()
    now = now or datetime.now(timezone.utc)
    books, book_ts, book_errors = {}, {}, {}
    wanted = []
    for ev in events:
        start = event_start(ev)
        for m in ev.get("markets") or []:
            if m.get("status") == "active" and not (start is not None and start <= now) and m.get("ticker"):
                wanted.append(m["ticker"])
    t0 = time.time()

    def one(t):
        if time.time() - t0 > BOOK_BUDGET:
            raise TimeoutError(f"orderbook budget {BOOK_BUDGET}s spent")
        b = fetch_orderbook(t, session)
        return b, now_iso()
    with ThreadPoolExecutor(max_workers=BOOK_WORKERS) as pool:
        futs = {t: pool.submit(one, t) for t in wanted}
        for t, f in futs.items():
            try:
                books[t], book_ts[t] = f.result()
            except Exception as e:     # noqa: BLE001 - one book failing never blocks the rest
                book_errors[t] = f"{type(e).__name__}: {str(e)[:120]}"
    return {"events": events, "received_ts": received_list, "books": books,
            "book_ts": book_ts, "book_errors": book_errors}


# ---------------------------------------------------------------- parsing

_SCHED = re.compile(r"scheduled for ([A-Z][a-z]{2})[a-z]* (\d{1,2}), (\d{4}) at (\d{1,2}):(\d{2}) ([AP]M) (EDT|EST|ET)")
_TICK = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})")


def _eastern(y, mo, d, hh, mm, zone=None):
    naive = datetime(y, mo, d, hh, mm)
    if zone == "EDT":
        return naive.replace(tzinfo=timezone(timedelta(hours=-4)))
    if zone == "EST":
        return naive.replace(tzinfo=timezone(timedelta(hours=-5)))
    try:
        from zoneinfo import ZoneInfo
        return naive.replace(tzinfo=ZoneInfo("America/New_York"))
    except Exception:          # noqa: BLE001 - no tz database: EDT is right most of the season
        return naive.replace(tzinfo=timezone(timedelta(hours=-4)))


def event_start(ev):
    """Scheduled start (UTC datetime).  rules_primary states it ('originally scheduled for Oct 12,
    2026 at 9:00 PM EDT'); the event ticker (26OCT122100 = Eastern time) is the fallback.
    close_time / expected_expiration_time are NOT the start (they are start + hours/days)."""
    for m in ev.get("markets") or []:
        mt = _SCHED.search(m.get("rules_primary") or "")
        if mt:
            mon = _MONTHS.get(mt.group(1).upper()[:3])
            hh = int(mt.group(4)) % 12 + (12 if mt.group(6) == "PM" else 0)
            if mon:
                return _eastern(int(mt.group(3)), mon, int(mt.group(2)), hh, int(mt.group(5)),
                                mt.group(7)).astimezone(timezone.utc)
    mt = _TICK.search(ev.get("event_ticker") or "")
    if mt and _MONTHS.get(mt.group(2)):
        return _eastern(2000 + int(mt.group(1)), _MONTHS[mt.group(2)], int(mt.group(3)),
                        int(mt.group(4)), int(mt.group(5))).astimezone(timezone.utc)
    return None


def event_name(ev):
    """Tournament name from the rules text ('... wins the <EVENT>: A vs. B CS2 match ...')."""
    for m in ev.get("markets") or []:
        mt = re.search(r"wins the (.+?): .+? vs\. .+? CS2 match", m.get("rules_primary") or "")
        if mt:
            return mt.group(1).strip()
    return None


def _levels(arr):
    out = []
    for lv in arr or []:
        try:
            p, s = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def book_quote(book):
    """Best YES bid/ask (+ sizes, + total size within 5c of each best) from a Kalshi orderbook.
    YES asks come from the SAME market's NO bids: ask = 1 - no_bid."""
    ob = (book or {}).get("orderbook_fp") or (book or {}).get("orderbook") or {}
    yes = _levels(ob.get("yes_dollars"))
    no = _levels(ob.get("no_dollars"))
    asks = [(round(1 - p, 4), s) for p, s in no]
    bid = max(yes, default=None)
    ask = min(asks, default=None)
    q = {"bid": bid[0] if bid else None, "bid_size": bid[1] if bid else None,
         "ask": ask[0] if ask else None, "ask_size": ask[1] if ask else None}
    q["depth"] = {
        "bid_best": q["bid_size"], "ask_best": q["ask_size"],
        "bid_5c": round(sum(s for p, s in yes if bid and p >= bid[0] - 0.05 - 1e-9), 2) if bid else 0.0,
        "ask_5c": round(sum(s for p, s in asks if ask and p <= ask[0] + 0.05 + 1e-9), 2) if ask else 0.0,
        "source": "orderbook",
    }
    return q


def market_quote(m):
    """Fallback from the market object itself: best levels only.  A 0 bid or a 1.00 ask = no order."""
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    bs, as_ = _f(m.get("yes_bid_size_fp")), _f(m.get("yes_ask_size_fp"))
    if bid is not None and bid <= 0:
        bid, bs = None, None
    if ask is not None and ask >= 1:
        ask, as_ = None, None
    return {"bid": bid, "ask": ask, "bid_size": bs if bid is not None else None,
            "ask_size": as_ if ask is not None else None,
            "depth": {"bid_best": bs if bid is not None else None, "ask_best": as_ if ask is not None else None,
                      "bid_5c": None, "ask_5c": None, "source": "market best level only"}}


def _is_map_market(ev, m):
    text = " ".join(str(x or "") for x in (ev.get("series_ticker"), ev.get("event_ticker"), m.get("ticker"),
                                            m.get("title"), ev.get("title")))
    return "KXCS2MAP" in text.upper() or re.search(r"\bmap\s*\d", text, re.I) is not None


def normalize(raw):
    """Raw fetch -> list of match listings, one per Kalshi event:
    {platform, event_id, teams [t0, t1], start, bo, event_title, url, rules, quotes [q0, q1], problem}
    quotes[i] = team i's OWN YES contract.  Map-winner and non-two-team events are skipped."""
    out = []
    books, book_ts, errs = raw.get("books") or {}, raw.get("book_ts") or {}, raw.get("book_errors") or {}
    for ev in raw.get("events") or []:
        if (ev.get("series_ticker") or SERIES) != SERIES:
            continue
        markets = [m for m in ev.get("markets") or [] if not _is_map_market(ev, m)]
        if not markets or len(markets) != len(ev.get("markets") or []):
            continue                                  # map / mixed scope: never series
        start = event_start(ev)
        listing = {
            "platform": PLATFORM, "event_id": ev.get("event_ticker"),
            "teams": [str(m.get("yes_sub_title") or "").strip() for m in markets],
            "start": _iso(start), "bo": None,           # Kalshi does not state the series length
            "event_title": event_name(ev) or ev.get("title"),
            "url": f"https://kalshi.com/markets/{SERIES.lower()}/{(ev.get('event_ticker') or '').lower()}",
            "rules": None, "quotes": [], "problem": None,
        }
        if len(markets) != 2:
            listing["problem"] = f"{len(markets)} markets in event (expected one per team)"
        for m in markets:
            t = m.get("ticker")
            if t in books:
                q = book_quote(books[t])
                received = book_ts.get(t) or raw.get("received_ts")
            else:
                q = market_quote(m)
                received = raw.get("received_ts")
                if t in errs:               # member-visible (the platform block): the cause only (2026-10-13 audit A4)
                    from moneyline import member_error
                    q["depth"]["error"] = member_error(errs[t])
            last = _f(m.get("last_price_dollars"))
            vol = _f(m.get("volume_fp")) or 0
            rules = (m.get("rules_primary") or "").strip()
            srcs = ", ".join(s.get("url") or s.get("name") or "" for s in ev.get("settlement_sources") or [])
            listing["rules"] = listing["rules"] or (rules + (f" Settlement sources: {srcs}" if srcs else ""))
            q.update({
                "platform": PLATFORM, "event_id": ev.get("event_ticker"), "market_id": t,
                "outcome_id": "yes", "team_name": str(m.get("yes_sub_title") or "").strip(),
                "scope": "series",
                "last_trade": ({"price": last, "label": "last trade (not a quote)"}
                               if last is not None and last > 0 and vol > 0 else None),
                "source_ts": None, "received_ts": received,
                "status": m.get("status"), "market_open": m.get("status") == "active",
                "rules": listing["rules"], "start": listing["start"], "bo": None,
                "event_title": listing["event_title"],
                "raw": {k: m.get(k) for k in ("ticker", "event_ticker", "status", "yes_bid_dollars", "yes_ask_dollars",
                                              "yes_bid_size_fp", "yes_ask_size_fp", "no_bid_dollars", "no_ask_dollars",
                                              "last_price_dollars", "volume_fp", "close_time",
                                              "expected_expiration_time", "updated_time", "yes_sub_title")},
            })
            listing["quotes"].append(q)
        out.append(listing)
    return out


def snapshot(session=None):
    """fetch + normalize (one call, for scripts)."""
    return normalize(fetch(session))


if __name__ == "__main__":
    import json
    t0 = time.time()
    ls = snapshot()
    print(json.dumps([{k: v for k, v in x.items() if k not in ("quotes", "rules")} for x in ls], indent=1)[:4000])
    print(len(ls), "events in", round(time.time() - t0, 1), "s")
