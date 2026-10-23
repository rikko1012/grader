"""Pinnacle player props - the sharp price every pick'em line is compared with.

Pinnacle is the low-margin book other books follow; its guest API (guest.api.arcadia.pinnacle.com, the one pinnacle.com's
own pages call, with the site's public guest key) answers a plain server request and needs no account. Two calls per
league: /leagues/<id>/matchups (games and their "special" prop matchups) and /leagues/<id>/markets/straight (prices).
Endpoints as used by kaarme01/kaarme-bet-scraper (MIT); read 2026-10-23.

Kept: category "Player Props", description "<player> Total <stat>" with the stat in markets.PIN, the full-game (period 0)
two-sided total. Each quote carries the book's stake limit: a low limit is a less tested price. Unofficial API - polite
pacing (1 request / second) and a 5-minute cache.

LOCATION: Pinnacle does not serve the United States. Requested the way pinnacle.com requests it (its Origin / Referer),
the price call from a US server answers 403 "Access from United States is prohibited" (2026-10-23, Ohio). That refusal
is reported as the source's status and is NEVER worked around (no stripped headers, proxies or VPNs). From a US server
(Render, Ohio) the sharp prices therefore come from Kalshi and, when ODDS_API_KEY is set, the US-book consensus.
"""
import json
import re

from . import cache, fair, markets, names, net
from .lines import utc_iso

BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
HEADERS = {"X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0r", "Origin": "https://www.pinnacle.com",
           "Referer": "https://www.pinnacle.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"}
REFRESH_S = 5 * 60
REFUSED_S = 6 * 3600          # after a location refusal, Pinnacle is not asked again for 6 hours
_DESC = re.compile(r"^(?P<name>.+?) Total (?P<stat>.+)$")


def parse(matchups, prices, lg):
    fam = lg["family"]
    props = {}
    for m in matchups or []:
        sp = m.get("special") or {}
        if m.get("type") != "special" or sp.get("category") != "Player Props":
            continue
        d = _DESC.match(sp.get("description") or "")
        mk = markets.PIN.get(fam, {}).get(m.get("units"))
        if not d or not mk or d.group("stat") != m.get("units"):
            continue
        parent = m.get("parent") or {}
        teams = {p.get("alignment"): p.get("name") for p in parent.get("participants") or []}
        side = {p.get("id"): (p.get("name") or "").lower() for p in m.get("participants") or []}
        props[m.get("id")] = {"name": d.group("name"), "market": mk, "start": utc_iso(parent.get("startTime")),
                              "home": teams.get("home"), "away": teams.get("away"), "side": side,
                              "live": bool(m.get("isLive") or parent.get("isLive"))}
    out = []
    for p in prices or []:
        info = props.get(p.get("matchupId"))
        if not info or info["live"] or p.get("type") != "total" or p.get("period") != 0 or p.get("status") not in (None, "open"):
            continue
        by = {}
        for q in p.get("prices") or []:
            s = info["side"].get(q.get("participantId"))
            if s in ("over", "under") and q.get("price") is not None and q.get("points") is not None:
                by[s] = q
        if set(by) != {"over", "under"} or by["over"]["points"] != by["under"]["points"]:
            continue
        try:
            dv = fair.no_vig(fair.american_to_decimal(by["over"]["price"]), fair.american_to_decimal(by["under"]["price"]))
        except ValueError:
            dv = None
        if not dv:
            continue
        limit = next((x.get("amount") for x in p.get("limits") or [] if x.get("type") == "maxRiskStake"), None)
        out.append({"source": "pinnacle", "league": lg["key"], "player": info["name"],
                    "player_key": names.key(info["name"]), "market": info["market"],
                    "line": float(by["over"]["points"]), "over_price": by["over"]["price"],
                    "under_price": by["under"]["price"], "p_over": dv[0], "overround": round(dv[1], 4),
                    "books": 1, "start": info["start"], "game": f"{info['away']} @ {info['home']}",
                    "limit": limit, "id": f"pin:{p.get('matchupId')}"})
    return out


def _refusal(e):
    try:
        body = json.loads(e.body or "{}")
    except ValueError:
        body = {}
    if e.status == 403 and body.get("reason") == "location":
        return f"refused for this server's location ({body.get('detail') or 'BAD_LOCATION'}) - not worked around"
    return str(e)[:200]


def load(lg, refresh=False):
    """-> (quotes, status)."""
    if not lg.get("pin"):
        return [], {"ok": False, "error": "no Pinnacle league id"}
    refused = None if refresh else cache.get("pin:refused", "location", REFUSED_S)
    if refused:
        return [], {"ok": False, "error": refused}     # asked once; not asked again for 6 h
    try:
        m = cache.fetch("pin:matchups", lg["key"], REFRESH_S,
                        lambda: net.get_json(f"{BASE}/leagues/{lg['pin']}/matchups", params={"brandId": 0},
                                             headers=HEADERS), refresh=refresh)
        p = cache.fetch("pin:prices", lg["key"], REFRESH_S,
                        lambda: net.get_json(f"{BASE}/leagues/{lg['pin']}/markets/straight",
                                             params={"primaryOnly": "false", "withSpecials": "true"},
                                             headers=HEADERS), refresh=refresh)
    except net.HttpError as e:
        msg = _refusal(e)
        if "not worked around" in msg:
            cache.put("pin:refused", "location", msg)
        return [], {"ok": False, "error": msg}
    except ValueError as e:
        return [], {"ok": False, "error": str(e)[:200]}
    rows = parse(m, p, lg)
    return rows, {"ok": True, "quotes": len(rows)}
