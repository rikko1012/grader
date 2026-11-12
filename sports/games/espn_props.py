"""ESPN-supplied DraftKings player-prop LINES (provenance "ESPN-supplied DraftKings").

ESPN's public core API republishes, per game, the prop board of the odds provider it shows (DraftKings, provider 100,
on NBA and NFL games read 2026-10-23):

  sports.core.api.espn.com/v2/sports/<sport>/leagues/<league>/events/<id>/competitions/<id>/odds
  .../odds/<provider id>/propBets?limit=1000[&page=2]

What it carries, verified on real NBA and NFL games: the game (competition $ref, checked against the requested event
id), the athlete or team ($ref -> id), the prop type ("Total Receiving Yards (incl. overtime)", "Points Milestones",
"1st Half Total Rushing Yards" ...), the CURRENT line and the OPENING line ("target"), and lastUpdated (minute
precision, ESPN's time). The live feed now also carries odds.american.value (verified 2026-11-12), but still no side
label. Reported prices are saved unordered, never assigned by list position. These rows are reference data: they are never de-vigged, never given a probability and never counted in a price
consensus - a line is not a 50% price. This is not a DraftKings connection: DraftKings' own site refuses this server and
is not contacted; ESPN is the publisher of these rows.

Kinds: "line" (a "Total ..." market), "milestone" (an "N+" ladder rung -> over N - 0.5). A half or quarter market keeps
its period ("1h", "1q") and is never matched to a full-game line. Types with no target (touchdown scorers, double
doubles) carry no line and are skipped.
"""
import re
import json
import math

from .. import markets, net
from .espn_game import CORE, ref_id

PROVENANCE = "ESPN-supplied {provider}"
_PERIOD = (("1st Half", "1h"), ("1st Quarter", "1q"), ("2nd Half", "2h"))


def period_of(type_name):
    for label, per in _PERIOD:
        if label in type_name:
            return per
    return "game"


def _base_type(type_name):
    """'1st Half Total Rushing Yards' -> 'Total Rushing Yards (incl. overtime)'-style lookup names, and
    'Assists Milestones - 1st Quarter' -> 'Assists Milestones'."""
    t = re.sub(r"\s*-\s*1st (Half|Quarter)$", "", type_name)
    t = re.sub(r"^1st (Half|Quarter) ", "", t)
    return t


def market_of(family, type_name):
    table = markets.ESPN_DK.get(family, {})
    if type_name in table:
        return table[type_name]
    base = _base_type(type_name)
    return table.get(base) or table.get(f"{base} (incl. overtime)")


def parse(body, league, family, event_id, provider_id, provider_name, athletes=None):
    """propBets items -> prop-line rows. Items for another game are dropped (exact event check); the two side-unidentified prices
    of a prop (same athlete / team, type, current and opening line) collapse to one row with the later update time."""
    prov = PROVENANCE.format(provider=provider_name)
    athletes = athletes or {}
    seen, rows = {}, []
    for it in body.get("items") or []:
        if ref_id(it.get("competition")) != str(event_id):
            continue
        target = ((it.get("current") or {}).get("target") or {}).get("value")
        if target is None:
            continue
        typ = it.get("type") or {}
        name = typ.get("name") or ""
        aid, tid = ref_id(it.get("athlete")), ref_id(it.get("team"))
        milestone = "Milestone" in name
        opened = ((it.get("open") or {}).get("target") or {}).get("value")
        sig = (aid, tid, typ.get("id"), target, opened)
        upd = _iso_minute(it.get("lastUpdated"))
        # ESPN supplies prices but no selection-side identity. Keep them as an
        # unordered reported set; neither item order nor price implies O/U.
        raw_price = ((it.get("odds") or {}).get("american") or {}).get("value")
        try:
            price = float(raw_price)
            price = int(price) if math.isfinite(price) and price.is_integer() and 100 <= abs(price) <= 100000 else None
        except (ValueError, TypeError):
            price = None
        if sig in seen:                                  # the other side-unidentified price: keep the later update time
            r0 = seen[sig]
            if price is not None: r0["_prices"].add(price)
            r0["provider_updated_at"] = max(x for x in (r0["provider_updated_at"], upd) if x) if (upd or
                                                                                                r0["provider_updated_at"]) else None
            continue
        line = float(target) - 0.5 if milestone else float(target)
        a = athletes.get(aid) or {}
        rows.append({"source": "espn", "book": provider_name.lower(), "provenance": prov, "league": league,
                     "event_id": str(event_id), "athlete_id": aid, "player": a.get("player"),
                     "player_key": a.get("player_key"), "team_id": tid or a.get("team_id"),
                     "market": market_of(family, name), "period": period_of(name),
                     "kind": "milestone" if milestone else "line", "raw_type_id": typ.get("id"), "raw_type": name,
                     "line": line, "line_open": (float(opened) - 0.5 if milestone else float(opened))
                     if opened is not None else None, "price_over": None, "price_under": None,
                     "provider_updated_at": upd, "status": "open", "_prices": {price} if price is not None else set(), "_who": aid or f"team{tid}"})
        seen[sig] = rows[-1]
    # prop identity: a standard line moves (same key, new history row); a milestone rung is its own prop. Two distinct
    # standard lines for one player and type at once (rare) are told apart by line.
    count = {}
    for r in rows:
        if r["kind"] == "line":
            k = (r["_who"], r["raw_type_id"])
            count[k] = count.get(k, 0) + 1
    for r in rows:
        prices = sorted(r.pop("_prices"))
        r["reported_prices"] = json.dumps(prices, separators=(",", ":")) if prices else None
        k = (r["_who"], r["raw_type_id"])
        tail = f":{r['line']}" if r["kind"] == "milestone" or count.get(k, 0) > 1 else ""
        r["prop_key"] = f"{league}:{event_id}:{provider_id}:{r.pop('_who')}:{r['raw_type_id']}{tail}"
    return rows


def _iso_minute(s):
    """'2026-10-23T22:55Z' -> '2026-10-23T22:55:00Z'."""
    if not s:
        return None
    return s[:-1] + ":00Z" if re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$", s) else s


def fetch(lg, event_id, athletes=None):
    """-> (rows, info). info = {"providers": [...], "requests": n}. Only providers whose odds entry links a propBets
    list are read."""
    base = f"{CORE}/{lg['espn'][0]}/leagues/{lg['espn'][1]}/events/{event_id}/competitions/{event_id}/odds"
    odds = net.get_json(base)
    rows, info = [], {"providers": [], "requests": 1}
    for item in odds.get("items") or []:
        p = item.get("provider") or {}
        if not item.get("propBets") or not p.get("id"):
            continue
        url = f"{base}/{p['id']}/propBets"
        body = net.get_json(url, params={"limit": 1000})
        items = list(body.get("items") or [])
        info["requests"] += 1
        for page in range(2, int(body.get("pageCount") or 1) + 1):
            items += net.get_json(url, params={"limit": 1000, "page": page}).get("items") or []
            info["requests"] += 1
        got = parse({"items": items}, lg["key"], lg["family"], event_id, p["id"], p.get("name") or str(p["id"]),
                    athletes)
        info["providers"].append({"id": p["id"], "name": p.get("name"), "items": len(items), "rows": len(got)})
        rows += got
    return rows, info
