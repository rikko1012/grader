#!/usr/bin/env python3
"""
polymarket.py - team win odds for CS2 matches from Polymarket (public data, no key).

Finds the upcoming match between two teams and returns the series moneyline
(plus Map 1 / Map 2 winner prices) as probabilities AND as American odds, so
the result drops straight into grader.grade_leg() as team_ml / opp_ml.

Use:  python polymarket.py Vitality Spirit
      from polymarket import match_odds
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

GAMMA = "https://gamma-api.polymarket.com"
CS2_SERIES = 10310          # Polymarket's CS2 series id
MAX_SPREAD = 0.10           # wider bid/ask than this = use last price, flag it
THIN_LIQUIDITY = 500        # USD; below this the price is easy to push around
GENERIC = {"team", "esports", "esport", "gaming", "club", "academy", "gg", "the"}


def norm(name):
    words = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower()).split()
    core = [w for w in words if w not in GENERIC]
    return " ".join(core or words)


def name_match(query, outcome):
    """3 = exact, 2 = exact after dropping 'Team'/'Esports'/etc, 1 = contains, 0 = no.
    'Academy' only matches if the query says academy (so Vitality != Vitality Academy)."""
    q, o = str(query).lower().strip(), str(outcome).lower().strip()
    if q == o:
        return 3
    if ("academy" in o) != ("academy" in q) or (" junior" in o) != (" junior" in q):
        return 0
    nq, no = norm(q), norm(o)
    if nq == no:
        return 2
    if len(nq) >= 3 and (re.search(rf"\b{re.escape(nq)}\b", no) or re.search(rf"\b{re.escape(no)}\b", nq)):
        return 1
    return 0


def same_team(a, b):
    """The fallback odds' team match (2026-10-13 audit C13: 'FURIA' picked 'FURIA fe vs Imperial fe'): one team by
    matchid's team identity (team_key - exact names after normalisation and org affixes, maintained + verified aliases), never
    substring - Academy / fe / NXT / Young / Force squads are other teams. name_match (loose) stays for its other callers."""
    try:
        import matchid
        return matchid.same_team(a, b)
    except Exception:                    # noqa: BLE001 - identity unreadable: no match (never a guess)
        return False


def prob_to_american(p):
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


_EVENTS = {"at": 0.0, "data": None, "retry_at": 0.0, "error": None}
import threading as _threading
_EVENTS_LOCK = _threading.Lock()
EVENTS_TTL = 60   # seconds - the market list is shared by every grade in that minute
FAIL_RETRY_S = 30  # a failed download is retried no sooner than this (the last good list meanwhile)


class Unavailable(RuntimeError):
    """No event list in memory and the last download failed less than FAIL_RETRY_S ago (no new request)."""


def upcoming_events(days_back=1):
    """Open CS2 events whose end date hasn't passed (skips stale never-closed ones).
    Cached in memory for 60 seconds. 2026-10-13 audit (700 members): one download at a time - while it runs, callers
    with a list in memory get that list at once (never queued behind a 20 s request); a failed download keeps the last
    good list and is retried no sooner than FAIL_RETRY_S (a retry delay, not a cached answer); with no list yet, callers
    inside that delay fail at once (Unavailable) instead of each sending a request."""
    import time as _t

    def ready(now):
        if _EVENTS["data"] is not None and now - _EVENTS["at"] < EVENTS_TTL:
            return True
        if now < _EVENTS["retry_at"]:
            if _EVENTS["data"] is None:
                raise Unavailable(f"Polymarket event list unavailable ({_EVENTS['error']}); retried within {FAIL_RETRY_S} s")
            return True
        return False
    if ready(_t.time()):
        return _EVENTS["data"]
    if not _EVENTS_LOCK.acquire(blocking=_EVENTS["data"] is None):   # one download for everyone (no stampede)
        return _EVENTS["data"]                                       # another caller is downloading: the last list
    try:
        if ready(_t.time()):
            return _EVENTS["data"]
        try:
            data = _fetch_events(days_back)
        except Exception as e:
            _EVENTS.update(retry_at=_t.time() + FAIL_RETRY_S, error=type(e).__name__)
            if _EVENTS["data"] is not None:          # Polymarket hiccup: keep the last list
                return _EVENTS["data"]
            raise
        _EVENTS.update(at=_t.time(), data=data, retry_at=0.0, error=None)
        return data
    finally:
        _EVENTS_LOCK.release()


def _fetch_events(days_back):
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events, offset = [], 0
    while True:
        r = requests.get(f"{GAMMA}/events", timeout=20, params={
            "series_id": CS2_SERIES, "closed": "false", "end_date_min": since,
            "limit": 100, "offset": offset})
        r.raise_for_status()
        batch = r.json()
        events += batch
        if len(batch) < 100:
            return events
        offset += 100


def _jl(x):
    """A JSON-list field as Gamma sends it (text; a list as is) - None when malformed (polymarket_ml._jl)."""
    if isinstance(x, list):
        return x
    try:
        v = json.loads(x or "[]")
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, list) else None


def _prices(m):
    try:
        return [float(x) for x in _jl(m.get("outcomePrices"))]
    except (TypeError, ValueError):
        return None


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _price(m, idx):
    """Probability for outcome idx: bid/ask midpoint when the book is tight, else last price."""
    prices = _prices(m) or []
    last = prices[idx] if idx < len(prices) else None
    bid, ask = m.get("bestBid"), m.get("bestAsk")
    try:
        if bid is not None and ask is not None and float(ask) - float(bid) <= MAX_SPREAD:
            mid = (float(bid) + float(ask)) / 2      # book quotes outcome 0
            return (mid if idx == 0 else 1 - mid), "midpoint"
    except (TypeError, ValueError):
        pass
    return last, "last price (wide spread)"


def _side(m, team_idx):
    p, src = _price(m, team_idx)
    return None if p is None else {"team_prob": round(p, 3), "opp_prob": round(1 - p, 3), "source": src}


def _start(ml):
    try:
        return datetime.fromisoformat(ml["gameStartTime"].replace(" ", "T").replace("+00", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return None


def _started(ml):
    """Only PRE-MATCH prices are valid for grading. Once a match starts, the price is an in-game
    price (a team down a map can sit at 9%), and after it ends it is the result - both would
    wreck the win chance, blowout factor and stomp rounds. Props lock at the start anyway."""
    start = _start(ml)
    return start is not None and start <= datetime.now(timezone.utc)


def cached_events():
    """(event list, retrieval time) already in memory - (None, None) before the first download. Never a request
    (market_odds.revalidate re-checks a grade's market input on this list)."""
    return _EVENTS["data"], (_EVENTS["at"] or None)


MATCH_WINDOW_H = 3          # the same match: listed within this many hours of the graded match's start (matchid.WINDOW_H)


def when(s):
    """An ISO / Gamma start ('2026-10-12 01:00:00+00', '...Z') -> aware UTC datetime, None when unreadable."""
    try:
        t = str(s).strip().replace(" ", "T", 1).replace("Z", "+00:00")
        t = t + ":00" if re.search(r"[+-]\d{2}$", t) else t
        d = datetime.fromisoformat(t)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def match_odds(team, opponent=None, events=None, events_at=None, start=None):
    """Next Polymarket match for `team` (optionally vs `opponent`) that has NOT started yet.
    Live and finished matches are skipped - their price isn't a pre-match win chance (2026-10-13 audit: the event's own
    live / ended flags too - a match that starts early is in play before its listed start).
    Teams by exact identity (same_team: matchid's team identity with maintained + verified aliases - never substring);
    the price is oriented by the team, whatever the listing order. start (the graded match's start, 2026-10-13 audit):
    only a listing within MATCH_WINDOW_H of it, the nearest one (a rematch of the same teams is another match); no start:
    the earliest. An event Gamma lists malformed is skipped (it never breaks the other lookups).
    events_at: when a caller-supplied list was retrieved (epoch s) - recorded as retrieved_at."""
    if events is None:
        events = upcoming_events()
        events_at = _EVENTS["at"] or None
    want = when(start) if start else None
    best = None
    for ev in events:
        try:
            ml = next((m for m in ev.get("markets", []) if m.get("sportsMarketType") == "moneyline"), None)
            if not ml or ml.get("closed") or _started(ml) or ev.get("live") or ev.get("ended"):
                continue
        except (AttributeError, TypeError):
            continue
        outcomes = _jl(ml.get("outcomes"))
        if not outcomes or len(outcomes) != 2 or not all(isinstance(o, str) for o in outcomes) or _prices(ml) is None:
            continue
        if team:
            hits = [i for i in range(2) if same_team(team, outcomes[i])]
            if len(hits) != 1:
                continue
            ti = hits[0]
            oi = 1 - ti
            if opponent and not same_team(opponent, outcomes[oi]):
                continue
        else:   # player's team unknown (not on BO3.gg): find the match by the opponent alone
            hits = [i for i in range(2) if same_team(opponent, outcomes[i])]
            if len(hits) != 1:
                continue
            oi = hits[0]
            ti = 1 - oi
        if want is not None:                         # the graded match: within the window, the nearest listing
            st = when(ml.get("gameStartTime"))
            gap = abs((st - want).total_seconds()) if st is not None else None
            if gap is None or gap > MATCH_WINDOW_H * 3600:
                continue
            key = (gap, str(ml.get("gameStartTime") or ""))
        else:
            key = (0, str(ml.get("gameStartTime") or ""))   # the earliest start
        if best is None or key < best[0]:
            best = (key, ev, ml, ti, oi, outcomes)
    if not best:
        who = f"{team} vs {opponent}" if opponent else team
        return {"found": False, "query": who,
                "note": f"No open Polymarket market for {who} - win chance treated as 50/50"}

    _, ev, ml, ti, oi, outcomes = best
    side = _side(ml, ti)
    flags = []
    liq = _num(ml.get("liquidityNum"))
    if liq < THIN_LIQUIDITY:
        flags.append(f"Thin market (${liq:,.0f} liquidity) - price is less reliable")
    if side and side["source"] != "midpoint":
        flags.append("Wide bid/ask spread - using last traded price")
    start = ml.get("gameStartTime")

    maps = {}
    for m in ev.get("markets", []):
        mm = re.search(r"Map (\d) Winner", str(m.get("question") or ""))
        if m.get("sportsMarketType") == "child_moneyline" and mm:
            mo = _jl(m.get("outcomes")) or []
            idx = next((i for i, o in enumerate(mo) if o == outcomes[ti]), None)
            if idx is not None and _prices(m) is not None:
                maps[f"map{mm.group(1)}"] = _side(m, idx)

    out = {
        "found": True, "team": outcomes[ti], "opponent": outcomes[oi],
        "title": str(ml.get("question") or "").replace("Counter-Strike: ", ""),
        "start": start, "url": f"https://polymarket.com/event/{ev.get('slug')}",
        "liquidity": round(liq), "volume": round(_num(ml.get("volumeNum"))),
        "flags": flags, "maps": maps,
        # forecast record (2026-10-13): the market's own ids and raw prices as listed (outcome order = "outcomes"; the
        # bid / ask quote outcome 0) and OUR retrieval time of the event list - None when not listed, never invented
        "event_slug": ev.get("slug"), "market_id": ml.get("id"), "condition_id": ml.get("conditionId"),
        "outcomes": outcomes, "outcome_prices": _jl(ml.get("outcomePrices")),
        "best_bid": ml.get("bestBid"), "best_ask": ml.get("bestAsk"),
        "retrieved_at": (datetime.fromtimestamp(events_at, timezone.utc).isoformat(timespec="seconds")
                         if events_at else None),
    }
    if side:
        out.update(side,
                   team_ml=prob_to_american(side["team_prob"]),   # -> grade_leg team_ml
                   opp_ml=prob_to_american(side["opp_prob"]))     # -> grade_leg opp_ml
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python polymarket.py TEAM [OPPONENT]")
    print(json.dumps(match_odds(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), indent=2))
