"""hltv_mapchances.py - the HLTV map chances reader (2026-11-04): HLTV's "Maps -> Simulated" tables in the hltv-scraper
database, read through hltv.py's own read path (hltv.dsn, the pooled read-only connection with its breaker and 5 s statement
timeout, hltv.single_flight, hltv.is_transient). Display (mapchances.py, weight 0) and the vm-2 shadow (vm2.py) only - never
a grade input.

It lives beside hltv.py, not inside it: hltv.py is one of the files picklog.MODEL_VERSION hashes and is frozen since 4877218
(tests/test_workspace_api.py::test_model_hash_files_are_untouched), so any edit there would move the CS2 model tag with no
grading change. Nothing in hltv.py changes.

The hltv-scraper reads HLTV's box (1,000 simulated vetoes) for every upcoming match with both teams known that starts within
48 h - every scan within 6 h of the start, every 3 h before - and REPLACES the match's rows on each read until the start (rows
stay afterwards). Tables (read only; keyed by the HLTV matchid = the hltv:<id> canonical match id):
  map_chances          (matchid, map, chance smallint %, position 1 = listed first, updated_at)   PK (matchid, map)
  simulated_vetoes     (matchid, rank 1..3, maps text[] played maps in order, chance %, updated_at) PK (matchid, rank)
  simulated_veto_steps (matchid, rank, step, team (NULL = left-over map), action removed|picked|leftover, map, chance %)
  map_chances_status   (matchid, checked_at, found, last_error)                                    PK (matchid)
map_chances_history (one row per changed chance) is for line-movement display only - never read here."""
import json
import re
import threading
import time

import hltv

MC_TTL = 180              # one match's map chances are re-read at most every 3 minutes (members share one cached answer)
MC_MAX = 1000             # cached matches + players together (bounded, oldest quarter dropped; ~1-2 MB at most)
PMK_TTL = 1800            # a player's per-map kills (maps dated BEFORE the match day) re-read at most every 30 minutes
MC_STALE_S = 1800         # the display may show the last good answer up to 30 minutes old (its own "Updated" time shows)
MC_DISPLAY_WAIT = 0.5     # a member path waits at most this long for a refresh, never for the pool / a slow database
_MC = {}                  # key -> (read_at, value, ok, last good (at, value)); a failed read is served FAIL_TTL only
_MC_LOCK = threading.Lock()
_MC_BG = threading.BoundedSemaphore(2)   # at most 2 display refreshes at once, each in its own short-lived thread

# one statement, one round trip, every subquery on its table's matchid primary key (<= 7 + 3 + 21 + 1 + 1 rows)
_MC_SQL = """
SELECT
 (SELECT json_agg(json_build_object('map', c.map, 'chance', c.chance, 'position', c.position, 'updated_at', c.updated_at)
                  ORDER BY c.position) FROM map_chances c WHERE c.matchid = %(m)s),
 (SELECT json_agg(json_build_object('rank', v.rank, 'maps', v.maps, 'chance', v.chance, 'updated_at', v.updated_at)
                  ORDER BY v.rank) FROM simulated_vetoes v WHERE v.matchid = %(m)s),
 (SELECT json_agg(json_build_object('rank', s.rank, 'step', s.step, 'team', s.team, 'action', s.action, 'map', s.map,
                  'chance', s.chance) ORDER BY s.rank, s.step) FROM simulated_veto_steps s WHERE s.matchid = %(m)s),
 (SELECT json_build_object('checked_at', st.checked_at, 'found', st.found, 'last_error', st.last_error)
    FROM map_chances_status st WHERE st.matchid = %(m)s),
 (SELECT json_build_object('time', u.time, 'format', u.format, 'team1', u.team1, 'team2', u.team2)
    FROM upcoming_matches u WHERE u.matchid = %(m)s ORDER BY u.scraped_at DESC LIMIT 1)
"""

# one player's kills per map (map_player_stats sideid 0 = total, joined to match_maps / maps by the brief's rule), on matches
# dated in [since, before) - aggregated in the database (<= ~12 rows), on the map_player_stats playerid index
_PMK_SQL = """
SELECT mp.name, count(*), sum(s.k)
FROM map_player_stats s
JOIN match_maps mm ON mm.matchid = s.matchid AND mm.mapstatsid = s.mapstatsid
JOIN maps mp ON mp.mapid = mm.mapid
JOIN match_overview mo ON mo.matchid = s.matchid
WHERE s.playerid = %(p)s AND s.sideid = 0 AND s.k IS NOT NULL AND mo.date >= %(since)s AND mo.date < %(before)s
GROUP BY mp.name
"""


def hltv_match_number(match_id):
    """'hltv:2400124' / 'hltv-2400124' / 2400124 -> 2400124; anything else (a hashed match id) -> None."""
    if isinstance(match_id, int) and not isinstance(match_id, bool):
        return match_id if match_id > 0 else None
    m = re.fullmatch(r"hltv[:\-](\d+)", str(match_id or "").strip())
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- cache (bounded; failures never stand in for a number)
def _mc_get(key, ttl):
    with _MC_LOCK:
        hit = _MC.get(key)
    if not hit:
        return hltv.MISS
    at, value, ok = hit[:3]
    return value if time.time() - at < (ttl if ok else hltv.FAIL_TTL) else hltv.MISS


def _mc_put(key, value, ok):
    """Cache one answer. A failure (ok=False) is served FAIL_TTL only; the last GOOD answer is kept beside it for the
    display (map_chances_display) - never for the shadow, which logs the failure as ineligible."""
    with _MC_LOCK:
        prev = _MC.get(key)
        good = (time.time(), value) if ok else (prev[3] if prev and len(prev) > 3 else None)
        if len(_MC) >= MC_MAX:
            for k in sorted(_MC, key=lambda k: _MC[k][0])[:MC_MAX // 4]:
                _MC.pop(k, None)
        _MC[key] = (time.time(), value, ok, good)
    return value


def _mc_good(key, max_age):
    with _MC_LOCK:
        hit = _MC.get(key)
    good = hit[3] if hit and len(hit) > 3 else None
    return good[1] if good and time.time() - good[0] < max_age else None


def _failure(e, **extra):
    t = hltv.is_transient(e)
    return dict({"ok": False, "reason": "HLTV database unreachable" if t else hltv.err_text(e), "transient": t}, **extra)


# ---------------------------------------------------------------- parsing (pure)
def _json_val(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _iso_utc(t):
    from datetime import timezone
    d = hltv._utc_dt(t)
    return d.astimezone(timezone.utc).isoformat() if d is not None else None


def _fmt_of(s):
    s = str(s or "").strip().lower().replace(" ", "")
    return {"bo1": "bo1", "1": "bo1", "bo3": "bo3", "3": "bo3", "bo5": "bo5", "5": "bo5"}.get(s)


def parse_map_chances(row, match_no, read_at=None):
    """The reader's answer from one _MC_SQL row (5 JSON values, any of them NULL). Pure - the fixture test calls it.
    {"ok": True, "hltv_match", "found", "chances": [{"map", "key", "chance", "position"}] (listed order), "sum",
     "updated_at" (the snapshot's capture time, ISO UTC), "format" (HLTV schedule, None when not listed), "start" (HLTV's
     listed start), "teams", "status", "vetoes": [{"rank", "maps", "chance", "updated_at", "steps", "map1"}]} -
    or {"ok": False, "reason"} when HLTV has no map chances for the match."""
    ch, vs, steps, st, up = (_json_val(x) for x in (tuple(row) + (None,) * 5)[:5])
    status = None
    if isinstance(st, dict):
        status = {"checked_at": _iso_utc(st.get("checked_at")), "found": st.get("found"), "last_error": st.get("last_error")}
    base = {"hltv_match": match_no, "match_id": f"hltv:{match_no}", "status": status, "read_at": read_at,
            "start": _iso_utc(up.get("time")) if isinstance(up, dict) else None,
            "format": _fmt_of(up.get("format")) if isinstance(up, dict) else None,
            "teams": [up.get("team1"), up.get("team2")] if isinstance(up, dict) else None}
    chances = []
    for c in ch if isinstance(ch, list) else []:
        try:
            chances.append({"map": str(c["map"]), "key": hltv.map_key(c["map"]), "chance": int(c["chance"]),
                            "position": int(c["position"]), "updated_at": _iso_utc(c.get("updated_at"))})
        except (KeyError, TypeError, ValueError):
            continue
    if not chances:
        why = "HLTV showed no map simulation for this match" if status and status.get("found") is False \
            else "no HLTV map chances for this match"
        return dict(base, ok=False, found=False, reason=why)
    chances.sort(key=lambda c: (c["position"], c["map"]))
    times = sorted(c["updated_at"] for c in chances if c["updated_at"])
    by_rank = {}
    for s in steps if isinstance(steps, list) else []:
        try:
            by_rank.setdefault(int(s["rank"]), []).append({"step": int(s["step"]), "team": s.get("team"), "action": str(s["action"]),
                                                          "map": str(s["map"]), "chance": None if s.get("chance") is None else int(s["chance"])})
        except (KeyError, TypeError, ValueError):
            continue
    vetoes = []
    for v in vs if isinstance(vs, list) else []:
        try:
            rk = int(v["rank"])
        except (KeyError, TypeError, ValueError):
            continue
        stp = sorted(by_rank.get(rk, []), key=lambda s: s["step"])
        # Map 1 = the first map in step order that is picked or left over (BO3 / BO5: the first pick; BO1: the left-over map)
        m1 = next((s for s in stp if s["action"] in ("picked", "leftover")), None)
        vetoes.append({"rank": rk, "maps": [str(m) for m in (v.get("maps") or [])],
                       "chance": None if v.get("chance") is None else int(v["chance"]), "updated_at": _iso_utc(v.get("updated_at")),
                       "steps": stp, "map1": {"map": m1["map"], "key": hltv.map_key(m1["map"]), "step": m1["step"],
                                              "chance": m1["chance"]} if m1 else None})
    vetoes.sort(key=lambda v: v["rank"])
    return dict(base, ok=True, found=True, chances=chances, sum=sum(c["chance"] for c in chances),
                updated_at=times[-1] if times else None, updated_first=times[0] if times else None, vetoes=vetoes)


def parse_player_map_kills(rows):
    """{"ok": True, "maps": {key: {"name", "n", "kills"}}, "n", "kills"} from (map name, maps, kills) rows. Pure."""
    maps = {}
    for name, n, k in rows or []:
        if name is None or not n or k is None:
            continue
        e = maps.setdefault(hltv.map_key(name), {"name": str(name), "n": 0, "kills": 0})
        e["n"] += int(n)
        e["kills"] += int(k)
    return {"ok": True, "maps": maps, "n": sum(e["n"] for e in maps.values()), "kills": sum(e["kills"] for e in maps.values())}


# ---------------------------------------------------------------- reads
def map_chances_snapshot(match_id):
    """HLTV's current map chances for one match (whatever the scraper last wrote). One bounded query per match per MC_TTL,
    shared by every caller (single flight: 700 callers on one match = one read). Never raises: {"ok": False, "reason",
    "transient"} when there is nothing to read or the database failed (a failure is kept FAIL_TTL seconds only and never
    stands in for a number)."""
    mno = hltv_match_number(match_id)
    if mno is None:
        return {"ok": False, "reason": "match has no HLTV id", "transient": False}
    if not hltv.dsn():
        return {"ok": False, "reason": "HLTV database not configured", "transient": False}
    key = ("map_chances", mno)

    def read():
        from datetime import datetime, timezone
        try:
            con = hltv._connect()
            try:
                with con.cursor() as cur:
                    cur.execute(_MC_SQL, {"m": mno})
                    row = cur.fetchone() or (None,) * 5
            finally:
                con.close()
        except Exception as e:  # database down / busy / timed out: ineligible now, retried after FAIL_TTL
            return _mc_put(key, _failure(e, hltv_match=mno), ok=False)
        out = parse_map_chances(row, mno, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return _mc_put(key, dict(out, transient=False), ok=True)
    hit = _mc_get(key, MC_TTL)
    return hit if hit is not hltv.MISS else hltv.single_flight(key, read, lambda: _mc_get(key, MC_TTL))


def map_chances_display(match_id, wait=MC_DISPLAY_WAIT):
    """Member paths (classic card, research hub): the fresh cached answer, else the last good one (<= MC_STALE_S old) or
    None, while a background thread refreshes it (single flight). Waits at most `wait` seconds - a busy pool, the breaker
    or a slow database never holds a member's card. A failed read shows the last good answer or nothing. Never raises."""
    mno = hltv_match_number(match_id)
    if mno is None or not hltv.dsn():
        return None
    key = ("map_chances", mno)
    hit = _mc_get(key, MC_TTL)
    if hit is not hltv.MISS:
        return hit if hit.get("ok") else _mc_good(key, MC_STALE_S)
    if _MC_BG.acquire(blocking=False):
        def run():
            try:
                map_chances_snapshot(match_id)
            except Exception:
                pass
            finally:
                _MC_BG.release()
        th = threading.Thread(target=run, daemon=True, name="hltv-map-chances")
        th.start()
        th.join(wait)
        hit = _mc_get(key, MC_TTL)
        if hit is not hltv.MISS and hit.get("ok"):
            return hit
    return _mc_good(key, MC_STALE_S)


def map_chances(match_id, start=None):
    """The latest HLTV map chances captured BEFORE the match start, with their capture time (vm-2 shadow input).
    The start is the earlier of the caller's (the forecast's match start) and HLTV's listed start. map_chances holds only
    the latest read, so a snapshot updated at or after the start leaves nothing usable ("late"). Never raises."""
    return cut_before_start(map_chances_snapshot(match_id), start)


def cut_before_start(snap, start=None):
    """map_chances()'s pre-start rule applied to a snapshot already in hand (the card's display answer, map_weighted). Pure."""
    if not isinstance(snap, dict) or not snap.get("ok"):
        return snap
    starts = [t for t in (hltv._utc_dt(start), hltv._utc_dt(snap.get("start"))) if t is not None]
    if not starts:
        return dict(snap, ok=False, reason="match start unknown")
    cut = min(starts)
    cap = hltv._utc_dt(snap.get("updated_at"))
    if cap is None:
        return dict(snap, ok=False, reason="HLTV map chances carry no capture time")
    if cap >= cut:
        return dict(snap, ok=False, late=True, start_used=_iso_utc(cut), captured_at=snap.get("updated_at"),
                    reason="HLTV map chances were updated only at or after the match start")
    vetoes = [v for v in snap.get("vetoes") or [] if hltv._utc_dt(v.get("updated_at")) is not None
              and hltv._utc_dt(v["updated_at"]) < cut]
    return dict(snap, captured_at=snap.get("updated_at"), start_used=_iso_utc(cut), vetoes=vetoes)


def player_map_kills(player_id, before, days):
    """One HLTV player's kills per map on matches dated in [before - days, before) (before = the match day, so nothing
    from the match itself or later). {"ok": True, "player_id", "since", "before", "maps": {map key: {"name", "n",
    "kills"}}, "n", "kills"} - or {"ok": False, "reason", "transient"}. One bounded aggregate query, cached PMK_TTL per
    (player, day, window), single flight; a failed read is kept FAIL_TTL and never becomes a number. Never raises."""
    from datetime import date, timedelta
    try:
        pid = int(player_id)
        b = before if isinstance(before, date) else date.fromisoformat(str(before)[:10])
        days = int(days)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "no HLTV player id" if not player_id else "bad input", "transient": False}
    if not hltv.dsn():
        return {"ok": False, "reason": "HLTV database not configured", "transient": False}
    since = b - timedelta(days=days)
    key = ("player_map_kills", pid, b.isoformat(), days)

    def read():
        try:
            con = hltv._connect()
            try:
                with con.cursor() as cur:
                    cur.execute(_PMK_SQL, {"p": pid, "since": since.isoformat(), "before": b.isoformat()})
                    rows = cur.fetchall()
            finally:
                con.close()
        except Exception as e:
            return _mc_put(key, _failure(e), ok=False)
        return _mc_put(key, dict(parse_player_map_kills(rows), player_id=pid, since=since.isoformat(), before=b.isoformat(),
                                 transient=False), ok=True)
    hit = _mc_get(key, PMK_TTL)
    return hit if hit is not hltv.MISS else hltv.single_flight(key, read, lambda: _mc_get(key, PMK_TTL))


def player_map_kills_display(player_id, before, days, wait=MC_DISPLAY_WAIT):
    """Member card path (map_weighted's shadow projection beside the live one): the cached answer, else one background read
    (single flight, at most 2 refresh threads) that the card waits for at most `wait` seconds. None when nothing good is in
    hand in time - the card then shows no shadow number. Never raises."""
    try:
        from datetime import date
        pid = int(player_id)
        b = before if isinstance(before, date) else date.fromisoformat(str(before)[:10])
        key = ("player_map_kills", pid, b.isoformat(), int(days))
    except (TypeError, ValueError):
        return None
    if not hltv.dsn():
        return None
    hit = _mc_get(key, PMK_TTL)
    if hit is not hltv.MISS:
        return hit if hit.get("ok") else None
    if _MC_BG.acquire(blocking=False):
        def run():
            try:
                player_map_kills(pid, b, days)
            except Exception:
                pass
            finally:
                _MC_BG.release()
        th = threading.Thread(target=run, daemon=True, name="hltv-player-map-kills")
        th.start()
        th.join(wait)
        hit = _mc_get(key, PMK_TTL)
        if hit is not hltv.MISS and hit.get("ok"):
            return hit
    return None
