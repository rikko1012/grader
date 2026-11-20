"""HLTV per-map raw totals for the v3 player card (display only - never a grade input; not in picklog's model hash).

Owner 2026-11-20: "stop using BO3.gg we have HLTV". The v3 card's map cards, hero stat grid and the 2-map combine read only
this: his HLTV maps (the same HLTV database the projections read, the same exact-id rule: hltv._player_ids - exact nick, the
namesake who played for his team, never fuzzy), as RAW TOTALS per map for the last 12 months and the last 3 months:

    maps, rounds, kills, deaths, headshots (+ the kills and maps of the maps that have a headshot row), rating x rounds,
    ADR x rounds, KAST x rounds (+ the rounds each covers), map wins / decided maps

so any set of maps combines HLTV style: totals summed, then divided (kills / maps, kills / rounds, headshots / kills, kills /
deaths, round-weighted rating) - never an average of averages. A missing value stays missing (null), never 0.

Rows: HLTV-sourced matches only (match_overview.source = 'hltv'); a map without his kills, deaths or the round score is left out
(perf_live's rule), and a headshot row of 0 with 12+ kills is no headshot row (perf_live's "hs == 0 and k >= 12" rule).

Caching (700 members at once): one read per player at a time (hltv.single_flight), the answer kept 15 minutes in hltv's bounded
cache and on the data disk (hltv_store); a failed read keeps the last good answer and is retried after hltv.FAIL_TTL - a blip
never becomes "no data" (transient-failure rule).

The current map pool (pool_for): the match's own pool from veto-1 (veto_serve - the pool of the event's real HLTV vetoes), else
the most common complete pool of the last 7 days of HLTV vetoes (veto_serve's table), else ACTIVE_DUTY below."""
import threading
from datetime import date, timedelta
from decimal import Decimal

import hltv

# The CS2 Active Duty pool, the fallback only (the live pool follows HLTV's stored vetoes - pool_for). Verified 2026-11-20 against
# HLTV: veto_serve's recent pool (complete 7-map vetoes, last 7 days) and the HLTV simulated map chances of live matches are
# exactly these 7 (Cache in, Overpass and Train out). UPDATE HERE when Valve changes the Active Duty pool.
ACTIVE_DUTY = ("ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke")
NAMES = {"ancient": "Ancient", "anubis": "Anubis", "cache": "Cache", "dust2": "Dust 2", "inferno": "Inferno", "mirage": "Mirage",
         "nuke": "Nuke", "overpass": "Overpass", "train": "Train", "vertigo": "Vertigo", "cobblestone": "Cobblestone"}
WINDOWS = (("12m", 365), ("3m", 91))
MAX_ROWS = 3000           # one player's year is ~100-250 maps; the read is bounded whatever the data
SLOTS = threading.BoundedSemaphore(3)    # at most 3 HLTV reads for this card at once (the pool has 16 connections)
SLOT_WAIT = 2.0
LOADER = None             # tests / local renders: loader(nick, team, since) -> (hltv id, rows) in place of the database read

_SQL = """
SELECT mp.name, mo.date, ps.matchid, ps.k, ps.d, h.hs, own.score, opp.score, ps.rating, ps.adr, ps.kast
FROM player_stats ps
JOIN maps mp ON mp.mapid = ps.mapid
JOIN match_overview mo ON mo.matchid = ps.matchid
JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
LEFT JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
WHERE ps.playerid = ANY(%(ids)s) AND ps.mapid <> 0 AND ps.sideid = 0 AND mo.date >= %(since)s AND mo.source = 'hltv'
  AND ps.k IS NOT NULL AND ps.d IS NOT NULL AND own.score IS NOT NULL AND opp.score IS NOT NULL
ORDER BY mo.date DESC, ps.matchid DESC
LIMIT %(lim)s
"""


def map_key(name):
    """'de_dust2' / 'Dust 2' / 'dust2' -> 'dust2' (the key the card, veto and map chances share)."""
    s = "".join(ch for ch in str(name or "").lower().removeprefix("de_") if ch.isalnum())
    return s


def display(key, fallback=None):
    """The display name of a map key: 'dust2' -> 'Dust 2'. Never a 'de_' name."""
    return NAMES.get(key) or (str(fallback).removeprefix("de_").strip().title() if fallback else key.title())


def _num(v):
    """float of a number the driver returns (int, float or Decimal for a numeric column); None for missing / not a number."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return f if f == f else None
    return None


def totals(rows):
    """Raw totals of map rows (name, date, matchid, k, d, hs, own, opp, rating, adr, kast). None for no rows."""
    if not rows:
        return None
    t = {"maps": 0, "rounds": 0, "kills": 0, "deaths": 0, "hs": 0, "hs_kills": 0, "hs_maps": 0,
         "rating_x_rounds": 0.0, "rating_rounds": 0, "adr_x_rounds": 0.0, "adr_rounds": 0, "kast_x_rounds": 0.0, "kast_rounds": 0,
         "wins": 0, "decided": 0}
    for r in rows:
        _, _, _, k, d, hs, own, opp, rating, adr, kast = r
        rounds = int(own) + int(opp)
        t["maps"] += 1
        t["rounds"] += rounds
        t["kills"] += int(k)
        t["deaths"] += int(d)
        if hs is not None and not (int(hs) == 0 and int(k) >= 12):
            t["hs"] += int(hs)
            t["hs_kills"] += int(k)
            t["hs_maps"] += 1
        for name, v in (("rating", rating), ("adr", adr), ("kast", kast)):
            v = _num(v)
            if v is not None and rounds:
                t[name + "_x_rounds"] += v * rounds
                t[name + "_rounds"] += rounds
        if own != opp:
            t["decided"] += 1
            t["wins"] += int(own > opp)
    for name in ("rating", "adr", "kast"):
        t[name + "_x_rounds"] = round(t[name + "_x_rounds"], 4) if t[name + "_rounds"] else None
    return t


def build(rows, today=None, hltv_id=None):
    """The card's document from his map rows (newest first): per window, all maps and each map."""
    today = today or date.today()
    out = {"v": 1, "source": "HLTV", "hltv_id": hltv_id, "windows": {}, "names": {},
           "newest_map": None, "as_of": today.isoformat()}
    dates = [r[1] for r in rows if r[1] is not None]
    if dates:
        out["newest_map"] = str(max(dates))[:10]
    for label, days in WINDOWS:
        since = (today - timedelta(days=days)).isoformat()
        win = [r for r in rows if r[1] is not None and str(r[1])[:10] >= since]
        by = {}
        for r in win:
            k = map_key(r[0])
            if k:
                by.setdefault(k, []).append(r)
                out["names"].setdefault(k, display(k, r[0]))
        out["windows"][label] = {"days": days, "since": since, "overall": totals(win),
                                 "maps": {k: totals(v) for k, v in sorted(by.items())}}
    return out


def _load(nick, team, since):
    """(hltv id, rows) from the HLTV database; (None, []) when the nick has no exact HLTV id. Raises on a failed read."""
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            ids = hltv._player_ids(cur, nick, team)
            if not ids:
                return None, []
            params = {"ids": list(ids), "since": since, "lim": MAX_ROWS}
            try:
                cur.execute(_SQL, params)
            except Exception as e:
                if "source" not in str(e):
                    raise
                cur.execute(_SQL.replace("AND mo.source = 'hltv'", ""), params)   # an older scraper database: no source column
            return ids[0], cur.fetchall()
    finally:
        con.close()


def _key(nick, team):
    return ("hmaps", hltv._norm(nick), hltv._norm(team))


def for_player(nick, team=None, loader=None, today=None):
    """{"state": "ok", ...build()} | {"state": "none"} (no exact HLTV id / no HLTV maps) | {"state": "unavailable"} (the read
    failed and no good answer exists anywhere). Never raises. loader(nick, team, since) -> (hltv id, rows): tests only."""
    if not nick:
        return {"state": "none"}
    loader = loader or LOADER
    if loader is None and not hltv.dsn():
        return {"state": "unavailable"}
    key = _key(nick, team)
    got = hltv._peek(key)
    if got is not hltv.MISS:
        return got

    def read():
        day = today or date.today()
        since = (day - timedelta(days=max(d for _, d in WINDOWS))).isoformat()
        try:
            if not SLOTS.acquire(timeout=SLOT_WAIT):
                raise TimeoutError("HLTV map totals: all read slots busy")
            try:
                pid, rows = (loader or _load)(nick, team, since)
            finally:
                SLOTS.release()
        except Exception as e:                                   # noqa: BLE001 - the card shows its retry state, never a grade
            return hltv._remember(key, {"state": "unavailable", "transient": hltv.is_transient(e) or isinstance(e, TimeoutError)},
                                  ok=False)
        if not pid or not rows:
            return hltv._remember(key, {"state": "none", "hltv_id": pid})
        return hltv._remember(key, dict(build(rows, day, pid), state="ok"))
    try:
        return hltv.single_flight(key, read, lambda: hltv._peek(key))
    except Exception:                                            # noqa: BLE001
        return {"state": "unavailable"}


def pool_for(match_id=None):
    """(map keys, source) of the current pool for this match: veto-1's own pool for the match (the event's real HLTV vetoes),
    else the most common complete pool of the last 7 days of HLTV vetoes, else ACTIVE_DUTY. Memory reads only."""
    try:
        import veto_serve as vs
        with vs._LOCK:
            ent = vs._STORE.get(match_id) if match_id else None
            recent = (vs.STATE.get("table_pools") or {}).get("recent")
        mine = ((ent or {}).get("vm1") or {}).get("pool")
        for p, src in ((mine, "match"), (recent, "recent")):
            if isinstance(p, (list, tuple)) and len(p) == 7 and all(isinstance(m, str) for m in p):
                return [map_key(m) for m in p], src
    except Exception:                                            # noqa: BLE001
        pass
    return list(ACTIVE_DUTY), "constant"


def card_doc(nick, team, match_id=None, loader=None):
    """The /api/upgrade/player-maps answer: the player's totals + the pool (keys in display order + names)."""
    doc = dict(for_player(nick, team, loader=loader))
    keys, src = pool_for(match_id)
    doc["pool"] = {"keys": keys, "names": {k: display(k) for k in keys}, "source": src}
    names = dict(doc.get("names") or {})
    names.update(doc["pool"]["names"])
    doc["names"] = names
    return doc
