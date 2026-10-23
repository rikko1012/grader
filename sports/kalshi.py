"""Kalshi player-prop markets - a free, US-regulated exchange price for NBA / WNBA / NFL props (public data, no key).

Each market is "Will <player> record N+ <stat>?" (strike_type greater, floor_strike N - 0.5), so its YES price is a
chance of Over N - 0.5. The quote used is the bid/ask MIDPOINT, and only where both sides are quoted and the spread is
at most MAX_SPREAD - a wide market says little. sports-market-maker (2025-26 NBA props) found Kalshi's prices well
calibrated but its order flow one-sided (fans buy YES), so a midpoint may lean slightly toward Over; record.py measures
it by source.

A player's ladder (10+, 15+, 20+ ...) gives several quotes; edges.py takes the one nearest the pick'em line.
Kalshi's occurrence / expiration times are NOT game starts (2026-10-23: CHI @ MIL listed 23:00Z, tip-off 20:00Z per
ESPN, PrizePicks and Underdog), so a quote carries the game DATE from its event ticker (KXNBAPTS-26OCT23CHIMIL) and
edges.py matches it by date, never by time. Series read 2026-10-23 (GET /series?category=Sports); a series not listed
here (e.g. the two steals+blocks series, whose rules were not compared) is not used.
"""
import re
from datetime import date

from . import cache, names, net

API = "https://api.elections.kalshi.com/trade-api/v2"
REFRESH_S = 5 * 60
MAX_SPREAD = 0.05
SERIES = {
    "nba": {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists", "KXNBA3PT": "threes",
            "KXNBAPRA": "pra", "KXNBAPR": "pr", "KXNBAPA": "pa", "KXNBARA": "ra", "KXNBASTL": "steals",
            "KXNBABLK": "blocks"},
    "wnba": {"KXWNBAPTS": "points", "KXWNBAREB": "rebounds", "KXWNBAAST": "assists", "KXWNBA3PT": "threes"},
    "nfl": {"KXNFLPASSYDS": "pass_yds", "KXNFLPASSATT": "pass_att", "KXNFLPASSCOMP": "pass_cmp",
            "KXNFLPASSTDS": "pass_td", "KXNFLPASSINT": "pass_int", "KXNFLRSHYDS": "rush_yds",
            "KXNFLRSHATT": "rush_att", "KXNFLREC": "rec", "KXNFLRECYDS": "rec_yds", "KXNFLRRYDS": "rush_rec_yds"},
}
_TITLE = re.compile(r"^(?P<name>.+): (?P<n>\d+)\+$")
_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                        "DEC"), 1)}


def event_date(ticker):
    m = _DATE.search(ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))).isoformat()
    except ValueError:
        return None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(events, league, market):
    out = []
    for ev in events or []:
        day = event_date(ev.get("event_ticker"))
        for m in ev.get("markets") or []:
            t = _TITLE.match((m.get("yes_sub_title") or "").strip())
            bid, ask, strike = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("floor_strike"))
            if (not t or not day or m.get("status") != "active" or m.get("strike_type") != "greater" or strike is None
                    or abs(strike - (int(t.group("n")) - 0.5)) > 1e-9 or bid is None or ask is None
                    or not (0 < bid < ask < 1) or round(ask - bid, 4) > MAX_SPREAD):
                continue
            out.append({"source": "kalshi", "league": league, "player": t.group("name"),
                        "player_key": names.key(t.group("name")), "market": market, "line": strike,
                        "p_over": (bid + ask) / 2, "bid": bid, "ask": ask, "spread": round(ask - bid, 4),
                        "books": None, "start": None, "date": day, "game": ev.get("sub_title"),
                        "limit": None, "id": f"kalshi:{m.get('ticker')}"})
    return out


def _events(series):
    evs, cursor = [], None
    for _ in range(10):                                   # cursor paging, 200 events a page
        params = {"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        body = net.get_json(f"{API}/events", params=params)
        evs += body.get("events") or []
        cursor = body.get("cursor")
        if not cursor:
            break
    return evs


def load(lg, refresh=False):
    series = SERIES.get(lg["key"])
    if not series:
        return [], {"ok": False, "error": f"no Kalshi player-prop series for {lg['name']}"}
    out, failed = [], []
    for s, market in series.items():
        try:
            evs = cache.fetch("kalshi:events", s, REFRESH_S, lambda s=s: _events(s), refresh=refresh)
        except (net.HttpError, ValueError) as e:
            failed.append(f"{s}: {e}")
            continue
        out += parse(evs, lg["key"], market)
    st = {"ok": bool(out) or not failed, "quotes": len(out)}
    if failed:
        st["error"] = "; ".join(failed)[:200]
    return out, st
