"""Soccer / tennis slip legs: each tracked leg's result from its own saved forecast, and the settled chance-band record that
Auto-build ranks soccer / tennis legs by (owner 2026-11-26: "add soccer & tennis to the auto builder").

Read only. Every route reads forecasts.db (expansion_forecasts.connect's store) and writes nothing. No correlated subqueries:
each read is a keyed lookup or one scan joined here (review 2026-11-27: a grouped EXISTS plan took minutes on 8,000 rows).
- /api/sports/expansion/settle?sport=&ids=<forecast ids>: the latest review of each forecast (expansion_forecasts.settle
  appends a correction as a new review, so the last one wins). An actual -> the leg settles at its own line. No actual: a review
  that says every source reported the player did not play -> "void" (the apps void a DNP pick); anything else (no review yet,
  retired, conflicting sources) has no answer and the leg stays pending, never a miss.
- /api/sports/expansion/record?sport=: settled lettered forecasts by stated chance band, one per prop (event, player, stat,
  period: its last forecast), so a moved line or a second platform counts once.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import closing

from . import expansion_board as boards

MAX_IDS = 120                               # workspace_sports.SETTLE_PAGE: one settler page per read
_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
BANDS = ((0.50, 0.55, "50-55%"), (0.55, 0.60, "55-60%"), (0.60, 0.65, "60-65%"), (0.65, 1.0001, "65%+"))   # slip_quality.SPORT_BANDS
NO_LETTER = (None, "Lean")
RECORD_TTL = 600
_RECORD = {}
_RECORD_BUSY = set()
_RECORD_LOCK = threading.Lock()


def _reader(sport):
    """forecasts.db read-only, or None while the store does not exist yet."""
    path = boards.root(sport) / "forecasts.db"
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    return c


def _sport(req):
    sport = req.query.get("sport")
    return sport if sport in boards.SPORTS and boards.enabled(sport) else None


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _latest_reviews(c, ids):
    """{forecast_id: (review id, payload dict)} - the last-inserted review of each of these forecasts."""
    best = {}
    for r in c.execute(f"SELECT rowid AS rid, id, forecast_id, payload FROM reviews WHERE forecast_id IN ({','.join('?' * len(ids))})", ids):
        b = best.get(r["forecast_id"])
        if b is None or r["rid"] > b[0]:
            best[r["forecast_id"]] = (r["rid"], r["id"], r["payload"])
    return {k: (v[1], json.loads(v[2])) for k, v in best.items()}


def results(sport, ids):
    """{forecast_id: {"actual", "void", "line", "side", "review_id", "known_at"}} for the ids with an answer (see the module note)."""
    out = {}
    c = _reader(sport)
    if c is None or not ids:
        return out
    with closing(c):
        lines = {r["id"]: json.loads(r["payload"]) for r in c.execute(f"SELECT id, payload FROM forecasts WHERE id IN ({','.join('?' * len(ids))})", ids)}
        reviews = _latest_reviews(c, ids)
    for fid, (rid, rv) in reviews.items():
        f = lines.get(fid)
        if f is None:
            continue
        actual = rv.get("actual")
        dnp = actual is None and rv.get("participation") == ["did_not_play"]
        if not _num(actual) and not dnp:
            continue
        out[fid] = {"actual": actual if _num(actual) else None, "void": dnp, "line": f.get("line"), "side": f.get("side"),
                    "review_id": rid, "known_at": rv.get("known_at")}
    return out


def record(sport):
    """{"bands": [{name, wins, losses, pushes, printed}], "forecasts": n} - see the module note. Two plain scans."""
    c = _reader(sport)
    if c is None:
        return {"bands": [], "forecasts": 0}
    with closing(c):
        outcome = {r["forecast_id"]: r["o"] for r in c.execute(
            "SELECT forecast_id, json_extract(payload, '$.outcome') AS o FROM reviews ORDER BY rowid")}     # the last review wins
        last = {}
        for f in c.execute("SELECT id, json_extract(payload, '$.grade') AS grade, json_extract(payload, '$.p_side') AS p, "
                           "json_extract(payload, '$.side') AS side, json_extract(payload, '$.made_at') AS made, "
                           "json_extract(payload, '$.identity.event_id') AS ev, json_extract(payload, '$.identity.player_id') AS pl, "
                           "json_extract(payload, '$.stat') AS stat, json_extract(payload, '$.scope') AS scope FROM forecasts"):
            if f["id"] not in outcome:
                continue
            k = (f["ev"], f["pl"], f["stat"], f["scope"])
            if k not in last or (f["made"] or "") >= (last[k]["made"] or ""):
                last[k] = dict(f)
    tally = {name: {"name": name, "wins": 0, "losses": 0, "pushes": 0, "_p": 0.0} for _, _, name in BANDS}
    n = 0
    for f in last.values():
        p, o = f["p"], outcome[f["id"]]
        if f["grade"] in NO_LETTER or not _num(p) or o not in ("over", "under", "push"):
            continue
        band = next((name for lo, hi, name in BANDS if lo <= p < hi), None)
        if band is None:
            continue
        t, n = tally[band], n + 1
        if o == "push":
            t["pushes"] += 1
            continue
        t["wins" if o == f["side"] else "losses"] += 1
        t["_p"] += p
    bands = []
    for t in tally.values():
        k = t["wins"] + t["losses"]
        bands.append({"name": t["name"], "wins": t["wins"], "losses": t["losses"], "pushes": t["pushes"],
                      "printed": round(t["_p"] / k, 4) if k else None})
    return {"bands": bands, "forecasts": n}


def http_settle(req):
    sport = _sport(req)
    if sport is None:
        return 404, {"error": "not found"}
    ids = [x for x in (req.query.get("ids") or "").split(",") if x]
    if not ids or len(ids) > MAX_IDS or any(not _ID.match(x) for x in ids):
        return 400, {"error": "bad ids"}
    return 200, {"sport": sport, "results": results(sport, list(dict.fromkeys(ids)))}


def http_record(req):
    """One read per sport per RECORD_TTL, computed outside the lock: while it runs, other callers get the last answer."""
    sport = _sport(req)
    if sport is None:
        return 404, {"error": "not found"}
    with _RECORD_LOCK:
        hit = _RECORD.get(sport)
        if hit is not None and (time.monotonic() - hit[0] <= RECORD_TTL or sport in _RECORD_BUSY):
            return 200, {"sport": sport, **hit[1]}
        _RECORD_BUSY.add(sport)
    try:
        hit = (time.monotonic(), record(sport))
        with _RECORD_LOCK:
            _RECORD[sport] = hit
    finally:
        with _RECORD_LOCK:
            _RECORD_BUSY.discard(sport)
    return 200, {"sport": sport, **hit[1]}


# ------------------------------------------------------------------------------------------------ admin export (read only)
# /api/sports/admin/expansion/results?sport=&after=<rowid>&limit=: every published soccer / tennis forecast with its latest review,
# oldest first, one page at a time (owner 2026-11-26 "hows the hit rates been"; audit_1121 SCHEMA.md: no GET export of published
# reviews existed). ADMIN only. Nothing is aggregated here: the reader decides the population and dedupe.
EXPORT_MAX = 2000
EXPORT_FIELDS = ("platform", "offer_id", "stat", "scope", "line", "side", "p_side", "p_push", "grade", "units", "model_version",
                 "letter_basis", "made_at", "start", "sample")


def export(sport, after=0, limit=EXPORT_MAX):
    c = _reader(sport)
    if c is None:
        return {"sport": sport, "rows": [], "next": None}
    with closing(c):
        got = c.execute("SELECT rowid AS rid, id, offer_key, competition, event_id, payload FROM forecasts WHERE rowid > ? ORDER BY rowid LIMIT ?",
                        (after, limit)).fetchall()
        reviews = {}
        ids = [g["id"] for g in got]
        for i in range(0, len(ids), 500):
            reviews.update(_latest_reviews(c, ids[i:i + 500]))
    rows = []
    for g in got:
        f = json.loads(g["payload"])
        rv = reviews.get(g["id"], (None, {}))[1]
        row = {k: f.get(k) for k in EXPORT_FIELDS}
        row.update(id=g["id"], offer_key=g["offer_key"], competition=g["competition"], event_id=g["event_id"],
                   thin=bool(f.get("thin_model")), lean_cap=bool(f.get("lean_cap")),
                   actual=rv.get("actual"), outcome=rv.get("outcome"), known_at=rv.get("known_at"), reviewed=bool(rv),
                   participation=rv.get("participation"), settlement_scope=f.get("settlement_scope"))
        rows.append(row)
    return {"sport": sport, "rows": rows, "next": got[-1]["rid"] if len(got) == limit else None}


def http_admin_results(req):
    sport = req.query.get("sport")
    if sport not in boards.SPORTS:
        return 404, {"error": "not found"}
    try:
        after, limit = int(req.query.get("after") or 0), int(req.query.get("limit") or EXPORT_MAX)
    except ValueError:
        return 400, {"error": "bad page"}
    if after < 0 or not 1 <= limit <= EXPORT_MAX:
        return 400, {"error": "bad page"}
    return 200, export(sport, after, limit)
