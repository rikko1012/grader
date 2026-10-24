"""Kalshi game markets (partner exchange, public data, no key): the price ladders nothing else publishes.

Used for what only these ladders give: the margin distribution (blowout chance), team totals (NBA) and the win chance.
The game's point spread and total come from one other place (ESPN pickcenter, factors.py) and are not duplicated from
here; the ladders are still stored whole, so the median line they imply can be compared later.

Series (GET /series?category=Sports, 2026-10-23), every market a YES price on one statement:
  KX<L>GAME        "<team> wins"                     -> market "winner"
  KX<L>SPREAD      "<team> wins by over N.5 points"  -> market "margin_over", team, strike N.5
  KX<L>TOTAL       "Over N.5 points"                 -> market "total_over", strike
  KX<L>TEAMTOTAL   "<team> over N.5 points"          -> market "team_total_over", team, strike
  ... 1H versions (period "1h"), KX<L>OT (overtime), and NFL game touchdowns / sacks / turnovers.
An event ticker is "KXNBASPREAD-26OCT23CHIMIL": the game's US (Eastern) date and Kalshi's team codes, away first.
Games are matched to ESPN by that date and BOTH team codes (Kalshi's codes are mapped to ESPN abbreviations below);
an event that matches no ESPN game is stored with event_id NULL and is never used.
"""
import re

from .. import net
from ..kalshi import API, event_date

SERIES = {
    "nba": {"KXNBAGAME": ("winner", "game"), "KXNBASPREAD": ("margin_over", "game"),
            "KXNBATOTAL": ("total_over", "game"), "KXNBATEAMTOTAL": ("team_total_over", "game"),
            "KXNBA1HWINNER": ("winner", "1h"), "KXNBA1HSPREAD": ("margin_over", "1h"),
            "KXNBA1HTOTAL": ("total_over", "1h"), "KXNBA1HTEAMTOTAL": ("team_total_over", "1h"),
            "KXNBAOT": ("overtime", "game")},
    "nfl": {"KXNFLGAME": ("winner", "game"), "KXNFLSPREAD": ("margin_over", "game"),
            "KXNFLTOTAL": ("total_over", "game"), "KXNFLTEAMTOTAL": ("team_total_over", "game"),
            "KXNFL1HWINNER": ("winner", "1h"), "KXNFL1HSPREAD": ("margin_over", "1h"),
            "KXNFL1HTOTAL": ("total_over", "1h"), "KXNFL1HTEAMTOTAL": ("team_total_over", "1h"),
            "KXNFLOT": ("overtime", "game"), "KXNFLTOTALTD": ("touchdowns_over", "game"),
            "KXNFLGAMESACK": ("sacks_over", "game"), "KXNFLGAMETO": ("turnovers_over", "game")},
}
# Kalshi code -> ESPN abbreviation where they differ (2026-10-23 NBA / NFL boards vs ESPN team lists)
ALIASES = {
    "nba": {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"},
    "nfl": {"WAS": "WSH", "JAC": "JAX", "LA": "LAR"},
}
_SUFFIX = re.compile(r"^(?P<team>[A-Z]+)?(?P<n>\d+)?$")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def teams_of(event_ticker):
    """'KXNBASPREAD-26OCT23CHIMIL' -> the code string after the date ('CHIMIL')."""
    m = re.search(r"-\d{2}[A-Z]{3}\d{2}([A-Z]+)$", event_ticker or "")
    return m.group(1) if m else None


def split_codes(code_str, known):
    """'CHIMIL' -> ('CHI', 'MIL') using the codes seen in this event's markets (or a 2+3 / 3+2 / 3+3 split)."""
    for i in range(2, len(code_str) - 1):
        a, b = code_str[:i], code_str[i:]
        if a in known and b in known:
            return a, b
    return None


def parse(events, league):
    """Kalshi events (with nested markets) -> market-line rows (event_id filled later by match())."""
    out = []
    for ev in events or []:
        series = (ev.get("series_ticker") or (ev.get("event_ticker") or "").split("-")[0])
        kind = SERIES.get(league, {}).get(series)
        if not kind:
            continue
        market, period = kind
        day = event_date(ev.get("event_ticker"))
        for m in ev.get("markets") or []:
            suffix = (m.get("ticker") or "").rsplit("-", 1)[-1]
            sm = _SUFFIX.match(suffix)
            team = sm.group("team") if sm and market in ("winner", "margin_over", "team_total_over") else None
            out.append({"source": "kalshi", "league": league, "event_id": None,
                        "provider_event_id": ev.get("event_ticker"), "series": series, "market": market,
                        "period": period, "team_id": None, "team_code": team, "strike": _f(m.get("floor_strike")),
                        "strike_type": m.get("strike_type"), "title": m.get("yes_sub_title") or m.get("title"),
                        "bid": _f(m.get("yes_bid_dollars")), "ask": _f(m.get("yes_ask_dollars")),
                        "last": _f(m.get("last_price_dollars")), "volume": _f(m.get("volume_fp")),
                        "open_interest": _f(m.get("open_interest_fp")), "status": m.get("status"),
                        "provider_market_id": m.get("ticker"), "close_time": m.get("close_time"),
                        "_date": day, "_codes": teams_of(ev.get("event_ticker"))})
    return out


def match(rows, league, espn_games, extra_codes=()):
    """Fill event_id / team_id from ESPN games: same Eastern date, both team codes equal (after ALIASES). Team codes
    come from the markets that name a team (winner / spread / team total); a totals-only event is split with the same
    code set, and ESPN's own abbreviations (mapped back to Kalshi's) cover a game with no team market open."""
    from zoneinfo import ZoneInfo

    from ..books.store import dt
    et = ZoneInfo("America/New_York")
    alias = ALIASES.get(league, {})
    back = {v: k for k, v in alias.items()}
    by_day, known = {}, set(extra_codes)
    for g in espn_games:
        if g.get("start") and g.get("home") and g.get("away"):
            by_day.setdefault(dt(g["start"]).astimezone(et).date().isoformat(), []).append(g)
            known |= {back.get(g["home"]["abbr"], g["home"]["abbr"]), back.get(g["away"]["abbr"], g["away"]["abbr"])}
    known |= {r["team_code"] for r in rows if r["team_code"]}
    for r in rows:
        codes = split_codes(r.pop("_codes", None) or "", known)
        day = r.pop("_date", None)
        if not codes:
            continue
        ea, eh = (alias.get(c, c) for c in codes)
        hit = [g for g in by_day.get(day, []) if g["away"]["abbr"] == ea and g["home"]["abbr"] == eh]
        if len(hit) != 1:
            continue
        g = hit[0]
        r["event_id"] = str(g["id"])
        if r["team_code"]:
            ab = alias.get(r["team_code"], r["team_code"])
            r["team_id"] = g["home"]["id"] if ab == eh else (g["away"]["id"] if ab == ea else None)
    return rows


def fetch(league):
    """-> (rows, requests). Open events of every series for the league, 200 a page."""
    rows, n = [], 0
    for series in SERIES.get(league, {}):
        cursor = None
        for _ in range(10):
            params = {"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            body = net.get_json(f"{API}/events", params=params)
            n += 1
            evs = body.get("events") or []
            for e in evs:
                e.setdefault("series_ticker", series)
            rows += parse(evs, league)
            cursor = body.get("cursor")
            if not cursor or not evs:
                break
    return rows, n


# ----------------------------------------------------------------------------------------- what the ladders imply

def _mid(r, max_spread=0.10):
    b, a = r.get("bid"), r.get("ask")
    if b is None or a is None or a < b or round(a - b, 4) > max_spread:
        return None
    return (a + b) / 2


def _cross(points):
    """[(x, P(X > x))] -> the x where P crosses 0.5 (linear between the two strikes around it), or None."""
    pts = sorted((x, p) for x, p in points if p is not None)
    for (x0, p0), (x1, p1) in zip(pts, pts[1:]):
        if p0 >= 0.5 >= p1 and p0 != p1:
            return x0 + (p0 - 0.5) * (x1 - x0) / (p0 - p1)
    return None


def implied(rows, home_id, away_id, period="game"):
    """Latest ladder rows of one game -> {"home_win", "home_margin", "total", "home_team_total", "away_team_total",
    "p_margin_over": {strike: P(either team wins by more than strike)}} (None where the ladder is too thin)."""
    out = {"home_win": None, "home_margin": None, "total": None, "home_team_total": None, "away_team_total": None,
           "p_margin_over": {}}
    rows = [r for r in rows if r.get("period") == period and r.get("status") == "active"]
    win = {r["team_id"]: _mid(r) for r in rows if r["market"] == "winner"}
    if win.get(home_id) is not None and win.get(away_id) is not None and win[home_id] + win[away_id] > 0:
        out["home_win"] = win[home_id] / (win[home_id] + win[away_id])
    home_m = {r["strike"]: _mid(r) for r in rows if r["market"] == "margin_over" and r["team_id"] == home_id}
    away_m = {r["strike"]: _mid(r) for r in rows if r["market"] == "margin_over" and r["team_id"] == away_id}
    pts = [(s, p) for s, p in home_m.items() if p is not None]
    pts += [(-s, 1 - p) for s, p in away_m.items() if p is not None]         # P(home margin > -s) = 1 - P(away by > s)
    out["home_margin"] = _cross(pts)
    for s in sorted(set(home_m) & set(away_m)):
        if home_m[s] is not None and away_m[s] is not None:
            out["p_margin_over"][s] = home_m[s] + away_m[s]
    out["total"] = _cross([(r["strike"], _mid(r)) for r in rows if r["market"] == "total_over"])
    for side, tid in (("home_team_total", home_id), ("away_team_total", away_id)):
        out[side] = _cross([(r["strike"], _mid(r)) for r in rows
                            if r["market"] == "team_total_over" and r["team_id"] == tid])
    return out

