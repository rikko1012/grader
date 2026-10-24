"""Polymarket player props - a second exchange price beside Kalshi for NBA / NFL / WNBA (public data, no key).

Gamma API (gamma-api.polymarket.com, the public read API the CS2 side already uses): /events?series_id=<league series>
with the games ending in the next WINDOW_H hours. Each game event carries its prop markets, typed by sportsMarketType
(points, rebounds, ... / passing_yards, receptions, ...) and asked as "<player>: <stat> O/U <line>" with outcomes
["Yes", "No"] - YES is the Over. Series and types read 2026-10-23 (GET /sports, NBA 10345, NFL 12185, WNBA 10105).
College has no Polymarket player props (team markets only).

The quote is the bid/ask midpoint, only where both sides are quoted and the spread is at most MAX_SPREAD (most prop
markets show 1c / 99c - nobody quoting - and are left out). Like Kalshi, an exchange midpoint may lean toward Over when
buyers mostly back players; record.py scores each source separately. Polymarket's start times match ESPN / PrizePicks /
Underdog (2026-10-23: CHI @ MIL 20:00Z), so its quotes are matched by start time. Polymarket's international markets
cannot be traded from the US; only their public prices are read.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from . import cache, names, net
from .lines import utc_iso

API = "https://gamma-api.polymarket.com"
REFRESH_S = 5 * 60
MAX_SPREAD = 0.05
SERIES = {"nba": "10345", "nfl": "12185", "wnba": "10105"}
WINDOW_H = {"basketball": 36, "football": 4 * 24}
TYPES = {
    "basketball": {"points": "points", "rebounds": "rebounds", "assists": "assists", "threes": "threes",
                   "steals": "steals", "blocks": "blocks"},
    "football": {"passing_yards": "pass_yds", "passing_touchdowns": "pass_td", "passing_attempts": "pass_att",
                 "passing_completions": "pass_cmp", "rushing_yards": "rush_yds", "receiving_yards": "rec_yds",
                 "receptions": "rec"},
}
_Q = re.compile(r"^(?P<name>.+?): (?P<stat>.+) O/U (?P<line>\d+(?:\.\d+)?)$")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _time(s):
    """'2026-10-23 23:00:00+00' -> '2026-10-23T23:00:00Z'."""
    return utc_iso(re.sub(r"([+-]\d{2})$", r"\1:00", str(s or "").strip())) if s else None


def parse(events, lg):
    types = TYPES.get(lg["family"], {})
    out, seen = [], set()
    for ev in events or []:
        game = re.sub(r"-player-props$", "", ev.get("slug") or "")
        for m in ev.get("markets") or []:
            mk = types.get(m.get("sportsMarketType"))
            q = _Q.match((m.get("question") or "").strip())
            if not mk or not q or not m.get("active") or m.get("closed") or m.get("acceptingOrders") is False:
                continue
            line, bid, ask = _f(m.get("line")), _f(m.get("bestBid")), _f(m.get("bestAsk"))
            try:
                first = (json.loads(m.get("outcomes") or "[]") or [""])[0]
            except ValueError:
                first = ""
            if (line is None or abs(line - float(q.group("line"))) > 1e-9 or abs(line - round(line)) < 1e-9
                    or first not in ("Yes", "Over") or bid is None or ask is None or not (0 < bid < ask < 1)
                    or round(ask - bid, 4) > MAX_SPREAD or m.get("slug") in seen):
                continue                                   # (a market listed in two events is read once)
            seen.add(m.get("slug"))
            out.append({"source": "polymarket", "league": lg["key"], "player": q.group("name"),
                        "player_key": names.key(q.group("name")), "market": mk, "line": line,
                        "p_over": (bid + ask) / 2, "bid": bid, "ask": ask, "spread": round(ask - bid, 4),
                        "books": None, "start": _time(m.get("gameStartTime")) or utc_iso(ev.get("startTime")),
                        "game": game, "limit": None, "liquidity": _f(m.get("liquidityNum")),
                        "id": f"poly:{m.get('slug')}"})
    return out


_KEEP = ("question", "sportsMarketType", "line", "bestBid", "bestAsk", "outcomes", "active", "closed",
         "acceptingOrders", "gameStartTime", "liquidityNum", "slug")
ALL_TYPES = {t for ts in TYPES.values() for t in ts}


def _events(series, hours, now=None):
    """Upcoming game events with only their player-prop markets and the fields parse() reads (an NBA day is ~15 MB
    as sent; the cached copy keeps a small part of it)."""
    now = now or datetime.now(timezone.utc)
    evs = []
    for page in range(10):                                   # 100 events a page
        body = net.get_json(f"{API}/events", params={
            "series_id": series, "closed": "false", "limit": 100, "offset": page * 100,
            "end_date_min": (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")}, timeout=90) or []
        for e in body:
            props = [{k: m.get(k) for k in _KEEP} for m in e.get("markets") or []
                     if m.get("sportsMarketType") in ALL_TYPES]
            if props:
                evs.append({"slug": e.get("slug"), "startTime": e.get("startTime"), "markets": props})
        if len(body) < 100:
            break
    return evs


def load(lg, refresh=False):
    series = SERIES.get(lg["key"])
    if not series:
        return [], {"ok": False, "error": f"no Polymarket player props for {lg['name']}"}
    try:
        evs = cache.fetch("poly:events", lg["key"], REFRESH_S,
                          lambda: _events(series, WINDOW_H[lg["family"]]), refresh=refresh)
    except (net.HttpError, ValueError) as e:
        return [], {"ok": False, "error": str(e)[:200]}
    rows = parse(evs, lg)
    return rows, {"ok": True, "quotes": len(rows)}
