"""Optional: a multi-book consensus from The Odds API (the-odds-api.com), beside Pinnacle. Off unless ODDS_API_KEY is set.

The approach sports-market-maker, sharpEdge and oddsapi_ev take: each US book's two-sided price is de-vigged on its own
(fair.no_vig), then the books quoting the SAME line are averaged with equal weight. A consensus needs 2+ books (the
sports-market-maker rule) - one book is shown as one book, never called a consensus.

Credits: the event-odds call costs 1 credit per market per region per event, so only CORE markets are requested and
answers are cached 30 minutes. 10 NBA games x 5 markets = 50 credits per refresh (free tier: 500 a month).
"""
from collections import defaultdict

from . import cache, config, fair, markets, names, net
from .lines import utc_iso

BASE = "https://api.the-odds-api.com/v4"
REFRESH_S = 30 * 60
CORE = {"basketball": ["player_points", "player_rebounds", "player_assists", "player_threes",
                       "player_points_rebounds_assists"],
        "football": ["player_pass_yds", "player_rush_yds", "player_receptions", "player_reception_yds"]}
MIN_BOOKS = 2


def parse_event(ev, lg):
    """One /events/{id}/odds answer -> consensus quotes [{player_key, market, line, p_over, books, ...}]."""
    fam = lg["family"]
    per = defaultdict(list)                       # (player_key, market, line) -> [(book, p_over)]
    shown = {}
    for bk in ev.get("bookmakers") or []:
        for mkt in bk.get("markets") or []:
            mk = markets.ODDS_API.get(fam, {}).get(mkt.get("key"))
            if not mk:
                continue
            pairs = defaultdict(dict)
            for o in mkt.get("outcomes") or []:
                side = (o.get("name") or "").lower()
                if side in ("over", "under") and o.get("point") is not None and o.get("price"):
                    pairs[(o.get("description"), float(o["point"]))][side] = float(o["price"])
            for (player, line), sides in pairs.items():
                if set(sides) != {"over", "under"} or not player:
                    continue
                dv = fair.no_vig(sides["over"], sides["under"])
                if dv:
                    k = (names.key(player), mk, line)
                    per[k].append((bk.get("key"), dv[0]))
                    shown[k] = player
    out = []
    for (pk, mk, line), books in per.items():
        uniq = dict(books)                        # one price per book
        if len(uniq) < MIN_BOOKS:
            continue
        out.append({"source": "consensus", "league": lg["key"], "player": shown[(pk, mk, line)], "player_key": pk,
                    "market": mk, "line": line, "p_over": sum(uniq.values()) / len(uniq), "books": len(uniq),
                    "book_keys": sorted(uniq), "start": utc_iso(ev.get("commence_time")),
                    "game": f"{ev.get('away_team')} @ {ev.get('home_team')}", "limit": None,
                    "id": f"odds:{ev.get('id')}"})
    return out


def load(lg, refresh=False, max_events=16):
    key = config.odds_api_key()
    if not key:
        return [], {"ok": False, "error": "ODDS_API_KEY not set (optional)"}
    sport, mk = lg.get("odds_api"), CORE.get(lg["family"], [])
    try:
        events = cache.fetch("odds:events", lg["key"], REFRESH_S,
                             lambda: net.get_json(f"{BASE}/sports/{sport}/events", params={"apiKey": key}),
                             refresh=refresh)
        out = []
        for ev in sorted(events, key=lambda e: e.get("commence_time") or "")[:max_events]:
            body = cache.fetch("odds:event", f"{lg['key']}:{ev['id']}", REFRESH_S,
                               lambda ev=ev: net.get_json(f"{BASE}/sports/{sport}/events/{ev['id']}/odds",
                                                          params={"apiKey": key, "regions": "us",
                                                                  "markets": ",".join(mk), "oddsFormat": "decimal"}),
                               refresh=refresh)
            out += parse_event(body, lg)
    except (net.HttpError, ValueError, KeyError) as e:
        msg = str(e)
        return [], {"ok": False, "error": msg.replace(key, "***")[:200]}
    return out, {"ok": True, "quotes": len(out)}
