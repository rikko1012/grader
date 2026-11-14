"""LoL match odds from Polymarket (public, no key) - series 10311 "League of Legends".

Per match Polymarket lists (checked 2026-10-17): the series moneyline, "Game N Winner" for every game, "Games Total: O/U
2.5 / 3.5 / 4.5" (how long the series runs - the chance map 3 is played decides whether a Maps 1-3 line in a BO3 is
graded at all), game handicaps and "Total Kills Over/Under X in Game N". All are read here as pre-match probabilities;
a match that has started (or ended) is skipped - an in-play price is not a pre-match chance (the CS2 rule).

Teams by identity (lol/teams.py), never substring; the price is oriented to the team asked for.
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import net, teams

GAMMA = "https://gamma-api.polymarket.com"
LOL_SERIES = 10311
MAX_SPREAD = 0.10          # wider bid / ask than this: last traded price, flagged
THIN_LIQUIDITY = 500       # USD
MATCH_WINDOW_H = 3         # a listing within this many hours of the graded match's start is the same match
EVENTS_TTL = 60
_EV = {"at": 0.0, "data": None}
_LOCK = threading.Lock()


def _jl(x):
    if isinstance(x, list):
        return x
    try:
        v = json.loads(x or "[]")
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, list) else None


def _prices(m):
    try:
        return [float(p) for p in _jl(m.get("outcomePrices")) or []]
    except (TypeError, ValueError):
        return None


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _price(m, idx):
    """Outcome idx's probability: the bid / ask midpoint when the book is tight (the book quotes outcome 0), else the
    last price, flagged."""
    prices = _prices(m) or []
    last = prices[idx] if idx < len(prices) else None
    bid, ask = m.get("bestBid"), m.get("bestAsk")
    try:
        if bid is not None and ask is not None and float(ask) - float(bid) <= MAX_SPREAD:
            mid = (float(bid) + float(ask)) / 2
            return (mid if idx == 0 else 1 - mid), "midpoint"
    except (TypeError, ValueError):
        pass
    return last, "last price (wide spread)"


def when(s):
    try:
        t = str(s).strip().replace(" ", "T", 1).replace("Z", "+00:00")
        t = t + ":00" if re.search(r"[+-]\d{2}$", t) else t
        d = datetime.fromisoformat(t)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def upcoming_events(*, with_time=False):
    """Open LoL events (cached 60 s; a failed download keeps the last list)."""
    with _LOCK:
        if _EV["data"] is not None and time.time() - _EV["at"] < EVENTS_TTL:
            return (_EV["data"], _EV["at"]) if with_time else _EV["data"]
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events, offset = [], 0
        try:
            while True:
                batch = net.get(f"{GAMMA}/events", params={"series_id": LOL_SERIES, "closed": "false",
                                                           "end_date_min": since, "limit": 100, "offset": offset}).json()
                events += batch
                if len(batch) < 100:
                    break
                offset += 100
        except Exception:                   # noqa: BLE001 - Polymarket down: keep the last list if there is one
            if _EV["data"] is not None:
                return (_EV["data"], _EV["at"]) if with_time else _EV["data"]
            raise
        _EV.update(at=time.time(), data=events)
        return (events, _EV["at"]) if with_time else events


def series_winners(events, observed):
    """Two-team pre-match markets with a tight two-sided quote on an open listing."""
    seen = datetime.fromtimestamp(observed, timezone.utc)
    games = []
    ids = set()
    for event in events:
        if any(event.get(flag) for flag in ("closed", "live", "ended", "archived")):
            continue
        for market in event.get("markets") or []:
            if market.get("sportsMarketType") != "moneyline" or market.get("closed") or market.get("archived"):
                continue
            if market.get("closed") is not False or market.get("acceptingOrders") is False or market.get("active") is False:
                continue
            start = when(market.get("gameStartTime"))
            names = _jl(market.get("outcomes")) or []
            key = market.get("id")
            if not start or start <= seen or not key or key in ids or len(names) != 2:
                continue
            if not all(isinstance(n, str) and n.strip() for n in names) or names[0] == names[1]:
                continue
            bid, ask = _num(market.get("bestBid")), _num(market.get("bestAsk"))
            if not (0 < bid <= ask < 1 and ask - bid <= MAX_SPREAD + 1e-12):
                continue
            mid = (bid + ask) / 2
            ids.add(key)
            games.append({"id": str(key), "teams": names, "start": start.isoformat(), "seen_at": seen.isoformat(),
                          "quotes": [{"source": "Polymarket", "kind": "midpoint", "prices": [mid, 1 - mid]}]})
    return {"league": "lol", "observed_at": seen.isoformat(), "games": sorted(games, key=lambda g: g["start"])}


def _oriented(m, team_name):
    """{team_prob, opp_prob, source} for a two-outcome market priced for `team_name` (an outcome label)."""
    outs = _jl(m.get("outcomes")) or []
    if team_name not in outs or _prices(m) is None:
        return None
    p, src = _price(m, outs.index(team_name))
    return None if p is None else {"team_prob": round(p, 3), "opp_prob": round(1 - p, 3), "source": src}


def _over(m):
    outs = _jl(m.get("outcomes")) or []
    if "Over" not in outs or _prices(m) is None:
        return None
    p, src = _price(m, outs.index("Over"))
    return None if p is None else {"line": m.get("line"), "over_prob": round(p, 3), "source": src,
                                   "liquidity": round(_num(m.get("liquidityNum")))}


def match_odds(team, opponent=None, start=None, events=None):
    """The next pre-match Polymarket listing for `team` (vs `opponent` when given; nearest to `start` when given)."""
    events = events if events is not None else upcoming_events()
    want = when(start) if start else None
    best = None
    for ev in events:
        markets = ev.get("markets") or []
        ml = next((m for m in markets if m.get("sportsMarketType") == "moneyline"), None)
        if not ml or ml.get("closed") or ev.get("live") or ev.get("ended"):
            continue
        st = when(ml.get("gameStartTime"))
        if st is not None and st <= datetime.now(timezone.utc):
            continue
        outs = _jl(ml.get("outcomes")) or []
        if len(outs) != 2:
            continue
        hits = [i for i in range(2) if teams.same_team(team, outs[i])]
        if len(hits) != 1 or (opponent and not teams.same_team(opponent, outs[1 - hits[0]])):
            continue
        if want is not None:
            if st is None or abs((st - want).total_seconds()) > MATCH_WINDOW_H * 3600:
                continue
            key = abs((st - want).total_seconds())
        else:
            key = st.timestamp() if st else float("inf")
        if best is None or key < best[0]:
            best = (key, ev, ml, outs[hits[0]], outs[1 - hits[0]])
    if not best:
        who = f"{team} vs {opponent}" if opponent else team
        return {"found": False, "query": who, "note": f"No open Polymarket market for {who}"}
    _, ev, ml, tname, oname = best
    side = _oriented(ml, tname) or {}
    liq = _num(ml.get("liquidityNum"))
    flags = []
    if liq < THIN_LIQUIDITY:
        flags.append(f"Thin market (${liq:,.0f} liquidity)")
    if side.get("source") and side["source"] != "midpoint":
        flags.append("Wide bid/ask spread - last traded price")
    games, totals, kills = {}, [], {}
    for m in ev.get("markets") or []:
        q, kind = str(m.get("question") or ""), m.get("sportsMarketType")
        if m.get("closed"):
            continue
        g = re.search(r"Game (\d) Winner", q)
        if kind == "child_moneyline" and g:
            o = _oriented(m, tname)
            if o:
                games[f"game{g.group(1)}"] = dict(o, liquidity=round(_num(m.get("liquidityNum"))))
        elif kind == "totals" and q.startswith("Games Total"):
            o = _over(m)
            if o:
                totals.append(o)
        elif kind == "kill_over_under_game":
            g = re.search(r"Game (\d)", q)
            o = _over(m)
            if g and o:
                kills.setdefault(f"game{g.group(1)}", []).append(o)
    bo = re.search(r"\(BO(\d)\)", str(ml.get("question") or ev.get("title") or ""))
    return {"found": True, "team": tname, "opponent": oname, "title": ev.get("title"), "start": ml.get("gameStartTime"),
            "best_of": int(bo.group(1)) if bo else None, "url": f"https://polymarket.com/event/{ev.get('slug')}",
            "liquidity": round(liq), "flags": flags, **side, "games": games,
            "games_total": sorted(totals, key=lambda o: o["line"] or 0), "total_kills": kills,
            "market_id": ml.get("id"), "retrieved_at": datetime.fromtimestamp(_EV["at"] or time.time(), timezone.utc)
            .isoformat(timespec="seconds")}


def p_game_played(odds, n):
    """P(game n is played) from 'Games Total O/U (n - 0.5)' - e.g. n=3: over 2.5 maps. None when not listed."""
    for t in odds.get("games_total") or []:
        if t.get("line") is not None and abs(float(t["line"]) - (n - 0.5)) < 1e-9:
            return t["over_prob"]
    return None
