"""mapchances.py - HLTV's map chances (the hltv-scraper's map_chances / simulated_vetoes tables, read by hltv_mapchances on hltv.py's read path).

1. Display: the "HLTV map chances" block on the classic CS2 card and the research hub card - each map's chance to be played
   and the likeliest simulated veto, at weight 0 (the grade does not use them). A match without a projection shows no block.
2. Prospective check (admin diagnostics; independent of the vm-2 shadow): for every match with map chances, the last
   snapshot captured BEFORE HLTV's listed start is recorded here; after the match, the maps actually played (match_maps)
   are compared with it - the share of the played maps that were in HLTV's top k (k = maps in the format), the n likeliest
   vs the n played, and whether the likeliest veto's Map 1 was Map 1. Hit rate per match. Background only, bounded reads.

Never a grade input: nothing here is read by grader / cs2_scout / pick_verdict, and no grade, chance, stake or projection
changes. A failed read changes nothing (display: no block; check: retried next pass)."""
import datetime as dt
import json
import os
import random
import sqlite3
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or HERE)
CHECK_DB = DATA_DIR / "map_chance_check.db"
CHECK_EVERY_S = 900          # one record + settle pass per 15 minutes (background thread)
RECORD_DAYS = 3              # snapshots updated in the last 3 days are looked at (bounded: ~7 rows per match)
RECORD_LIMIT = 7000
SETTLE_AFTER_H = 3           # a match is looked up in match_maps 3 h after its start ...
GIVE_UP_DAYS = 10            # ... until 10 days after it (then 'no result')
SETTLE_BATCH = 200
MAX_ROWS = 20000             # check rows kept (oldest dropped)
FORMAT_MAPS = {"bo1": 1, "bo3": 3, "bo5": 5}
NOTE = "The grade does not use HLTV map chances yet."
_STATE = {"at": 0.0, "last": None, "busy": False, "report_at": 0.0, "report": None}
_LOCK = threading.Lock()


# ====================================================================== 1. display
def member_payload(snap):
    """The member block from hltv_mapchances.map_chances_snapshot(), or None (no projection / no simulation / a failed read: hidden)."""
    if not isinstance(snap, dict) or not snap.get("ok") or not snap.get("chances"):
        return None
    if (snap.get("status") or {}).get("found") is False:
        return None
    maps = [{"name": c["map"], "pct": int(c["chance"])} for c in snap["chances"]]
    maps.sort(key=lambda m: -m["pct"])
    out = {"at": snap.get("updated_at"), "format": snap.get("format"), "maps": maps, "weight": 0, "note": NOTE, "veto": None}
    v1 = next((v for v in snap.get("vetoes") or [] if v.get("rank") == 1 and v.get("maps")), None)
    if v1:
        fmt = snap.get("format") or {1: "bo1", 3: "bo3", 5: "bo5"}.get(len(v1["maps"]))
        labels = {"bo1": ["Map"], "bo3": ["Map 1", "Map 2", "Decider"],
                  "bo5": ["Map 1", "Map 2", "Map 3", "Map 4", "Decider"]}.get(fmt) or [f"Map {i + 1}" for i in range(len(v1["maps"]))]
        out["veto"] = {"pct": v1.get("chance"), "maps": [{"slot": labels[i] if i < len(labels) else f"Map {i + 1}", "name": m}
                                                          for i, m in enumerate(v1["maps"])],
                       "steps": [{"step": s["step"], "team": s.get("team"), "action": s["action"], "map": s["map"]}
                                 for s in v1.get("steps") or []]}
    return out


def for_match(match_id):
    """The block for a canonical match id (hltv:<id>): hltv_mapchances.map_chances_display - the cached answer, refreshed by one
    background read (single flight); a member waits at most 0.5 s and never for the pool or a slow database. Never raises."""
    try:
        import hltv_mapchances
        return member_payload(hltv_mapchances.map_chances_display(match_id))
    except Exception:
        return None


def for_card(res):
    """The block for a CS2 card result (same canonical match id rule as the veto panel). Never raises."""
    try:
        import veto_serve
        mid = veto_serve.match_id_of(res)
    except Exception:
        return None
    return for_match(mid) if mid else None


# ====================================================================== 2. prospective check
_REC_SQL = """
SELECT c.matchid, c.map, c.chance, c.position, c.updated_at, u.time, u.format, v.maps, v.chance
FROM map_chances c
LEFT JOIN upcoming_matches u ON u.matchid = c.matchid
LEFT JOIN simulated_vetoes v ON v.matchid = c.matchid AND v.rank = 1
WHERE c.updated_at >= now() - make_interval(days => %s)
ORDER BY c.matchid, c.position
LIMIT %s
"""
_PLAYED_SQL = """
SELECT mm.matchid, mm.number, mp.name FROM match_maps mm JOIN maps mp ON mp.mapid = mm.mapid
WHERE mm.matchid = ANY(%s) AND mm.mapstatsid IS NOT NULL ORDER BY mm.matchid, mm.number
"""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _utc(t):
    if t in (None, ""):
        return None
    if isinstance(t, dt.datetime):
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    try:
        x = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def _iso(t):
    t = _utc(t)
    return t.isoformat() if t else None


_DB_INIT = [None]


def _db(path=None):
    path = Path(path or CHECK_DB)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    if _DB_INIT[0] != str(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS mc_checks (matchid INTEGER PRIMARY KEY, start TEXT, captured_at TEXT,
                       format TEXT, chances TEXT, veto1 TEXT, recorded_at TEXT, status TEXT DEFAULT 'pending',
                       settled_at TEXT, played TEXT, n_played INTEGER, k INTEGER, top_k_hits INTEGER, top_n_hits INTEGER,
                       hit_rate REAL, map1_hit INTEGER)""")
        con.execute("CREATE INDEX IF NOT EXISTS mc_checks_status ON mc_checks (status, start)")
        con.commit()
        _DB_INIT[0] = str(path)
    return con


def snapshots_from_rows(rows):
    """{matchid: {"start", "format", "captured_at", "chances": [[map, chance, position]], "veto1": {"maps", "chance"}}}
    from _REC_SQL rows. Pure. The HLTV listed start may be missing (match gone from the schedule)."""
    out = {}
    for mid, mp, ch, pos, upd, start, fmt, vmaps, vch in rows:
        e = out.setdefault(int(mid), {"start": _iso(start), "format": (str(fmt).lower() if fmt else None), "chances": [],
                                      "captured_at": None, "veto1": {"maps": list(vmaps), "chance": vch} if vmaps else None})
        e["chances"].append([str(mp), int(ch), int(pos)])
        u = _iso(upd)
        if u and (e["captured_at"] is None or u > e["captured_at"]):
            e["captured_at"] = u
    return out


def record(rows, now=None, con=None):
    """Keep each match's latest snapshot captured BEFORE its start (HLTV's listed start, or the start recorded while it was
    listed). A snapshot at or after the start, or of a match whose start was never seen, is not recorded. Returns n kept."""
    now = now or _now()
    own = con is None
    con = con or _db()
    n = 0
    try:
        for mid, s in snapshots_from_rows(rows).items():
            cur = con.execute("SELECT start, captured_at, status FROM mc_checks WHERE matchid = ?", (mid,)).fetchone()
            start = s["start"] or (cur["start"] if cur else None)
            cap = s["captured_at"]
            if not start or not cap or _utc(cap) >= _utc(start):
                continue
            if cur and (cur["status"] != "pending" or (cur["captured_at"] and cur["captured_at"] >= cap)):
                continue
            con.execute("""INSERT INTO mc_checks (matchid, start, captured_at, format, chances, veto1, recorded_at, status)
                           VALUES (?,?,?,?,?,?,?, 'pending') ON CONFLICT(matchid) DO UPDATE SET start = excluded.start,
                           captured_at = excluded.captured_at, format = COALESCE(excluded.format, mc_checks.format),
                           chances = excluded.chances, veto1 = excluded.veto1, recorded_at = excluded.recorded_at""",
                        (mid, start, cap, s["format"], json.dumps(s["chances"]), json.dumps(s["veto1"]), _iso(now)))
            n += 1
        con.commit()
    finally:
        if own:
            con.close()
    return n


def score_match(chances, veto1, played, fmt=None):
    """Pure. chances = [[map, chance %, position]]; veto1 = {"maps": [...]} or None; played = maps in played order.
    k = maps in the format (HLTV schedule format, else the chance sum / 100): top_k_hits = played maps among HLTV's k
    likeliest; hit_rate = top_k_hits / maps played; top_n_hits = the n likeliest vs the n played; map1_hit = the likeliest
    veto's Map 1 was Map 1 (None without a veto)."""
    ranked = [m for m, _, _ in sorted(chances, key=lambda c: (-c[1], c[2], c[0]))]
    k = FORMAT_MAPS.get(fmt) or max(1, int(round(sum(c[1] for c in chances) / 100.0)))
    n = len(played)
    if not n:
        return None
    key = lambda m: "".join(ch for ch in str(m).lower() if ch.isalnum())   # noqa: E731 - 'Dust 2' == 'Dust2'
    pl = {key(m) for m in played}
    top_k = {key(m) for m in ranked[:k]}
    top_n = {key(m) for m in ranked[:n]}
    m1 = (veto1 or {}).get("maps") or []
    return {"n_played": n, "k": k, "top_k_hits": len(pl & top_k), "top_n_hits": len(pl & top_n),
            "hit_rate": round(len(pl & top_k) / n, 6), "map1_hit": (key(m1[0]) == key(played[0])) if m1 else None}


def settle(played_by_match, now=None, con=None):
    """Score pending matches whose start is SETTLE_AFTER_H past: played_by_match = {matchid: [maps in order]} (from
    match_maps). No result after GIVE_UP_DAYS = 'no result'. Returns n settled."""
    now = now or _now()
    own = con is None
    con = con or _db()
    n = 0
    try:
        for r in con.execute("SELECT * FROM mc_checks WHERE status = 'pending' AND start <= ?",
                             (_iso(now - dt.timedelta(hours=SETTLE_AFTER_H)),)).fetchall():
            played = played_by_match.get(r["matchid"]) or []
            sc = score_match(json.loads(r["chances"]), json.loads(r["veto1"] or "null"), played, r["format"]) if played else None
            if sc:
                con.execute("""UPDATE mc_checks SET status = 'settled', settled_at = ?, played = ?, n_played = ?, k = ?,
                               top_k_hits = ?, top_n_hits = ?, hit_rate = ?, map1_hit = ? WHERE matchid = ?""",
                            (_iso(now), json.dumps(played), sc["n_played"], sc["k"], sc["top_k_hits"], sc["top_n_hits"],
                             sc["hit_rate"], None if sc["map1_hit"] is None else int(sc["map1_hit"]), r["matchid"]))
                n += 1
            elif _utc(r["start"]) < now - dt.timedelta(days=GIVE_UP_DAYS):
                con.execute("UPDATE mc_checks SET status = 'no result', settled_at = ? WHERE matchid = ?", (_iso(now), r["matchid"]))
        cnt = con.execute("SELECT count(*) FROM mc_checks").fetchone()[0]
        if cnt > MAX_ROWS:
            con.execute("DELETE FROM mc_checks WHERE matchid IN (SELECT matchid FROM mc_checks ORDER BY start LIMIT ?)", (cnt - MAX_ROWS,))
        con.commit()
    finally:
        if own:
            con.close()
    return n


def pending_due(now=None, con=None):
    now = now or _now()
    own = con is None
    con = con or _db()
    try:
        return [r[0] for r in con.execute("SELECT matchid FROM mc_checks WHERE status = 'pending' AND start <= ? ORDER BY start LIMIT ?",
                                          (_iso(now - dt.timedelta(hours=SETTLE_AFTER_H)), SETTLE_BATCH * 5)).fetchall()]
    finally:
        if own:
            con.close()


def run_once(now=None):
    """One background pass: record snapshots, then settle due matches. Two kinds of bounded reads through hltv.py's pool.
    A failed read leaves everything as it was (retried next pass)."""
    import hltv
    now = now or _now()
    if not hltv.dsn():
        return {"skipped": "HLTV database not configured"}
    out = {"at": _iso(now)}
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            cur.execute(_REC_SQL, (RECORD_DAYS, RECORD_LIMIT))
            rows = cur.fetchall()
        out["recorded"] = record(rows, now)
        ids = pending_due(now)
        played = {}
        with con.cursor() as cur:
            for i in range(0, len(ids), SETTLE_BATCH):
                cur.execute(_PLAYED_SQL, (ids[i:i + SETTLE_BATCH],))
                for mid, _, name in cur.fetchall():
                    played.setdefault(int(mid), []).append(str(name))
        out["settled"] = settle(played, now)
    finally:
        con.close()
    return out


def maybe_run(now=None):
    """Called from the veto-serve background loop: at most one pass per CHECK_EVERY_S, never two at once. Never raises."""
    with _LOCK:
        if _STATE["busy"] or time.time() - _STATE["at"] < CHECK_EVERY_S:
            return None
        _STATE["busy"], _STATE["at"] = True, time.time()
    try:
        _STATE["last"] = run_once(now)
    except Exception as e:  # noqa: BLE001 - transient: nothing changed, the next pass retries
        _STATE["last"] = {"error": f"{type(e).__name__}", "at": _iso(_now())}
    finally:
        _STATE["busy"] = False
    return _STATE["last"]


def _boot(vals, B=2000, seed=20261104):
    if not vals:
        return None
    rnd = random.Random(seed)
    n = len(vals)
    ms = sorted(sum(vals[rnd.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return [round(ms[int(0.025 * B)], 4), round(ms[int(0.975 * B) - 1], 4)]


def report(limit=50, con=None):
    """Admin diagnostics: settled matches, mean per-match hit rate (bootstrap 95% CI over matches), the n-likeliest check,
    Map 1 of the likeliest veto, by format, and the latest rows."""
    own = con is None
    con = con or _db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM mc_checks WHERE status = 'settled' ORDER BY start DESC LIMIT 5000")]
        counts = {r[0]: r[1] for r in con.execute("SELECT status, count(*) FROM mc_checks GROUP BY status")}
    finally:
        if own:
            con.close()
    hr = [r["hit_rate"] for r in rows]
    m1 = [r["map1_hit"] for r in rows if r["map1_hit"] is not None]
    by = {}
    for r in rows:
        by.setdefault(r["format"] or "?", []).append(r["hit_rate"])
    mean = lambda v: round(sum(v) / len(v), 4) if v else None   # noqa: E731
    return {"rule": "last HLTV map-chances snapshot captured before HLTV's listed start vs the maps played (match_maps); "
                    "hit rate per match = played maps among HLTV's k likeliest (k = maps in the format) / maps played",
            "matches": counts, "settled": len(rows), "hit_rate_mean": mean(hr), "hit_rate_ci95": _boot(hr),
            "top_n_hit_share": mean([r["top_n_hits"] / r["n_played"] for r in rows if r["n_played"]]),
            "map1_hit_rate": mean([float(x) for x in m1]), "map1_n": len(m1),
            "by_format": {f: {"n": len(v), "hit_rate_mean": mean(v)} for f, v in sorted(by.items())},
            "last": [{k: r[k] for k in ("matchid", "start", "captured_at", "format", "played", "top_k_hits", "n_played",
                                        "hit_rate", "map1_hit")} for r in rows[:limit]],
            "last_pass": _STATE["last"]}


def report_cached(ttl=300):
    """report() cached 5 minutes (admin page). Never raises."""
    if _STATE["report"] is not None and time.time() - _STATE["report_at"] < ttl:
        return _STATE["report"]
    try:
        rep = report()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}"}
    _STATE.update(report=rep, report_at=time.time())
    return rep
