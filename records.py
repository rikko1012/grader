"""Display-only records that the grader does not use (2026-10-10).

Maps 1-3 lines: his last 10 series' totals over maps 1, 2 and 3 - only series where map 3 was played (every BO5; a BO3
that went 1-1), never maps 4-5. A BO3 that ended 2-0 is not in it: Underdog voids Maps 1-3 picks then. HLTV first (maps 1-3 of each series), BO3.gg as the backup for
players / series HLTV lacks; tonight's environment (LAN / online) when he has 5+ such series there, like the Maps 1-2
record. Never a grade input: the grader reads the last-10 record on Maps 1-2 lines only. Not in the model hash."""
import threading
import time
from datetime import date, timedelta

import hltv

N_SERIES = 10
TTL = 15 * 60
_CACHE, _LOCK = {}, threading.Lock()


def _hltv_rows(nick, team, since):
    """HLTV map rows (maps 1-3) of his series with maps 1 and 2 known - [] on any problem. Cached 15 minutes."""
    if not hltv.dsn() or not nick:
        return []
    key = (hltv._norm(nick), hltv._norm(team), since)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    out, ok = [], True
    try:
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                ids = hltv._player_ids(cur, nick, team)
                out = hltv._series_rows(cur, ids, since, maxn=3, need=(1, 2)) if ids else []
        finally:
            con.close()
    except Exception:
        ok = False
    with _LOCK:
        if len(_CACHE) > 3000:
            _CACHE.clear()
        if not ok:                                     # a failed read keeps the last good rows and is retried in 30 s
            prev = _CACHE.get(key)
            out = prev[1] if prev else []
            _CACHE[key] = (time.time() - TTL + 30, out)
        else:
            _CACHE[key] = (time.time(), out)
    return out


def _series(rows, stat):
    by = {}
    for r in rows:
        if r.get("number") in (1, 2, 3):
            by.setdefault(r["match_id"], []).append(r)
    out = []
    for mid, maps in by.items():
        maps.sort(key=lambda m: m["number"])
        if [m["number"] for m in maps] != [1, 2, 3] or any(m.get(stat) is None for m in maps):
            continue                                   # Maps 1-3 = maps 1, 2 and 3 all played (a 2-0 BO3 is a void)
        won = sum(1 for m in maps if m.get("won"))
        out.append({"match_id": mid, "date": maps[0]["date"], "opponent": maps[0].get("opponent"),
                    "source": "HLTV" if maps[0].get("source") == "hltv" else None, "env": maps[0].get("env"),
                    "maps": [m.get("map") for m in maps], "values": [m[stat] for m in maps], "total": sum(m[stat] for m in maps),
                    "result": f"{'W' if won > len(maps) - won else 'L'} {won}-{len(maps) - won}"})
    return out


def maps123_record(pb, line=None, stat="kills", hltv_rows=None, today=None):
    """{"matches", "series", "avg_total", "median_total", "overs", "pushes", "decided", "hit_rate", "line", "form_from"}
    for a Maps 1-3 line; {} when no series qualifies. HLTV series replace BO3.gg's for the same match day + opponent."""
    nick = (pb.get("player") or {}).get("nick")
    team = pb.get("playing_for") or pb.get("current_team")
    since = ((today or date.today()) - timedelta(days=365)).isoformat()
    hl = _series(hltv_rows if hltv_rows is not None else _hltv_rows(nick, team, since), stat)
    b3 = _series([dict(r, source=None) for r in (pb.get("recent_maps") or [])], stat)
    def same(a, b):
        return a["date"][:10] == b["date"][:10] and hltv._norm(a.get("opponent"))[:4] == hltv._norm(b.get("opponent"))[:4]
    allv = hl + [s for s in b3 if not any(same(s, h) for h in hl)]      # BO3.gg only for series HLTV doesn't have
    menv = pb.get("_match_env") or {}
    for s in allv:
        s["env"] = s.get("env") or menv.get(s["match_id"])
    allv.sort(key=lambda s: s["date"], reverse=True)
    tonight = (pb.get("_env") or {}).get("tonight")
    pick, form_from = allv, None
    if tonight:
        env_rows = [s for s in allv if s.get("env") == tonight]
        if len(env_rows) >= 5:
            pick, form_from = env_rows, tonight
    ser = pick[:N_SERIES]
    if not ser:
        return {}
    for s in ser:
        s.pop("match_id", None)
    tot = sorted(s["total"] for s in ser)
    out = {"matches": len(ser), "series": ser, "avg_total": round(sum(tot) / len(tot), 1),
           "median_total": (tot[(len(tot) - 1) // 2] + tot[len(tot) // 2]) / 2, "form_from": form_from, "scope": "123"}
    if line is not None:
        overs = sum(1 for s in ser if s["total"] > line)
        pushes = sum(1 for s in ser if s["total"] == line)          # exactly on a whole line: a push, never an Under
        decided = len(ser) - pushes
        out.update(line=line, overs=overs, pushes=pushes, decided=decided, hit_rate=round(overs / decided, 3) if decided else None)
    return out
