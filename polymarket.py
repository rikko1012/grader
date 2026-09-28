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
LIVE_HOURS = 4              # a BO3 is over by then - older markets are finished, awaiting settlement
SETTLED = 0.03              # a started match priced at <=3% / >=97% is decided - never a pre-match price
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


def prob_to_american(p):
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def upcoming_events(days_back=1):
    """Open CS2 events whose end date hasn't passed (skips stale never-closed ones)."""
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


def _price(m, idx):
    """Probability for outcome idx: bid/ask midpoint when the book is tight, else last price."""
    prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    last = prices[idx] if idx < len(prices) else None
    bid, ask = m.get("bestBid"), m.get("bestAsk")
    if bid is not None and ask is not None and float(ask) - float(bid) <= MAX_SPREAD:
        mid = (float(bid) + float(ask)) / 2      # book quotes outcome 0
        return (mid if idx == 0 else 1 - mid), "midpoint"
    return last, "last price (wide spread)"


def _side(m, team_idx):
    p, src = _price(m, team_idx)
    return None if p is None else {"team_prob": round(p, 3), "opp_prob": round(1 - p, 3), "source": src}


def _start(ml):
    try:
        return datetime.fromisoformat(ml["gameStartTime"].replace(" ", "T").replace("+00", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return None


def _finished(ml):
    """Played already: started over LIVE_HOURS ago, or started and priced as decided."""
    start = _start(ml)
    now = datetime.now(timezone.utc)
    if not start or start > now:
        return False
    if now - start > timedelta(hours=LIVE_HOURS):
        return True
    try:
        p = float(json.loads(ml.get("outcomePrices") or "[]")[0])
    except (ValueError, IndexError, TypeError):
        return False
    return p <= SETTLED or p >= 1 - SETTLED


def match_odds(team, opponent=None, events=None):
    """Best upcoming (or live) Polymarket match for `team` (optionally vs `opponent`).
    Finished matches still waiting to settle are skipped - their price is the result, not odds."""
    events = upcoming_events() if events is None else events
    best = None
    for ev in events:
        ml = next((m for m in ev.get("markets", []) if m.get("sportsMarketType") == "moneyline"), None)
        if not ml or ml.get("closed") or _finished(ml):
            continue
        outcomes = json.loads(ml.get("outcomes") or "[]")
        if len(outcomes) != 2:
            continue
        scores = [name_match(team, o) for o in outcomes]
        ti = max(range(2), key=lambda i: scores[i])
        if scores[ti] == 0:
            continue
        oi = 1 - ti
        oscore = name_match(opponent, outcomes[oi]) if opponent else 1
        if opponent and oscore == 0:
            continue
        key = (scores[ti] + oscore, ml.get("gameStartTime") or "")
        # highest name score wins; on a tie, the earliest start
        if best is None or key[0] > best[0][0] or (key[0] == best[0][0] and key[1] < best[0][1]):
            best = (key, ev, ml, ti, oi, outcomes)
    if not best:
        who = f"{team} vs {opponent}" if opponent else team
        return {"found": False, "query": who,
                "note": f"No open Polymarket market for {who} - win chance treated as 50/50"}

    _, ev, ml, ti, oi, outcomes = best
    side = _side(ml, ti)
    flags = []
    liq = float(ml.get("liquidityNum") or 0)
    if liq < THIN_LIQUIDITY:
        flags.append(f"Thin market (${liq:,.0f} liquidity) - price is less reliable")
    if side and side["source"] != "midpoint":
        flags.append("Wide bid/ask spread - using last traded price")
    start = ml.get("gameStartTime")
    try:
        started = datetime.fromisoformat(start.replace(" ", "T").replace("+00", "+00:00")) <= datetime.now(timezone.utc)
    except (AttributeError, ValueError):
        started = False
    if started:
        flags.append("Match already started - this is a live in-play price")

    maps = {}
    for m in ev.get("markets", []):
        mm = re.search(r"Map (\d) Winner", m.get("question", ""))
        if m.get("sportsMarketType") == "child_moneyline" and mm:
            mo = json.loads(m.get("outcomes") or "[]")
            idx = next((i for i, o in enumerate(mo) if o == outcomes[ti]), None)
            if idx is not None:
                maps[f"map{mm.group(1)}"] = _side(m, idx)

    out = {
        "found": True, "team": outcomes[ti], "opponent": outcomes[oi],
        "title": ml.get("question", "").replace("Counter-Strike: ", ""),
        "start": start, "url": f"https://polymarket.com/event/{ev.get('slug')}",
        "liquidity": round(liq), "volume": round(float(ml.get("volumeNum") or 0)),
        "flags": flags, "maps": maps,
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
