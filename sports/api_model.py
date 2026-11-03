"""Read-only model endpoints (route table: sports/web.py ROUTES, one line each). Handlers answer ONLY from the
precomputed snapshot files the worker writes (sports/model/pipeline.write_snapshot) - no numpy, no fitting, no store
writes, nothing heavy per request. Pure python + json.

MEMBER  GET /api/sports/model/forecasts?league=nba|nfl[&event_id=&player_id=&stat=&model_version=&limit<=500]
            forecast views (one per offer x model version): probabilities (unconditional + named conditional),
            projection mean / median, versions, input record ids, grade-policy output (validation state, band,
            grade, stake, reasons, side availability)
        GET /api/sports/model/ladder?league=&forecast_id=[&lines=20.5,21,21.5]
            alternate lines from the forecast's ONE distribution: P(X > L), P(X < L), P(X = L) per line
        GET /api/sports/model/registry      versions, artifacts, validation states, registered study summaries
        GET /api/sports/model/features      feature registry fr-1 with observed effects
ADMIN   GET /api/sports/admin/model/learning   scorecard, forward gate F status, diagnostic queue
        GET /api/sports/admin/model/gradecheck[?part=headline]   daily grade check, containment, cal-mi log (A9a)
"""
from __future__ import annotations

import json
import os

from . import config, web

MAX_LIMIT = 500


def _dir():
    return config.DATA_DIR / "model" / "api"


def _load(name):
    p = _dir() / name
    try:
        mt = os.stat(p).st_mtime_ns
    except FileNotFoundError:
        return None
    return web.cached(("model_snapshot", name, mt), lambda: json.loads(p.read_text(encoding="utf-8")), ttl=300)


def _league(q):
    lg = q.get("league")
    return lg if lg in ("nba", "nfl") else None


def model(req):
    rest = (req.rest or "").strip("/")
    q = req.query
    if rest == "forecasts":
        lg = _league(q)
        if not lg:
            return 400, {"error": "league must be nba or nfl"}
        try:
            limit = max(1, min(MAX_LIMIT, int(q.get("limit") or 200)))
        except ValueError:
            return 400, {"error": "limit must be a number"}
        snap = _load(f"forecasts_{lg}.json")
        if snap is None:
            return 503, {"error": "model_snapshot_not_ready"}
        key = ("model_fc", lg, snap["written_at"], q.get("event_id"), q.get("player_id"), q.get("stat"),
               q.get("model_version"), limit)

        def pick():
            rows = [f for f in snap["forecasts"]
                    if all(q.get(k) in (None, "", f.get(k)) for k in ("event_id", "player_id", "stat",
                                                                      "model_version"))]
            return {"league": lg, "written_at": snap["written_at"], "count": len(rows), "forecasts": rows[:limit]}
        return 200, web.cached(key, pick)
    if rest == "ladder":
        lg = _league(q)
        fid = q.get("forecast_id") or ""
        if not lg or not fid.startswith("fc-"):
            return 400, {"error": "league and forecast_id required"}
        pm = _load(f"pmf_{lg}.json")
        if pm is None:
            return 503, {"error": "model_snapshot_not_ready"}
        d = pm["pmf"].get(fid)
        if d is None:
            return 404, {"error": "no distribution stored for this forecast (market-only versions have none)"}
        try:
            lines = [float(x) for x in (q.get("lines") or "").split(",") if x.strip()][:40]
        except ValueError:
            return 400, {"error": "lines must be numbers"}
        if not lines:
            c = _median(d)
            lines = [c + 0.5 * k for k in range(-8, 9)]
        return 200, {"forecast_id": fid, "written_at": pm["written_at"], "lines": [ladder_row(d, ln) for ln in lines],
                     "note": "unconditional probabilities from the forecast's one distribution; P(X = L) is the push "
                             "mass at a whole line"}
    if rest in ("registry", "features"):
        meta = _load("model_meta.json")
        if meta is None:
            return 503, {"error": "model_snapshot_not_ready"}
        if rest == "features":
            return 200, {"written_at": meta["written_at"], "features": meta["features"]}
        return 200, {k: meta[k] for k in ("written_at", "registry", "artifacts", "studies")}
    return 404, {"error": "not found"}


def admin_model(req):
    rest = (req.rest or "").strip("/")
    if rest == 'gradecore-export':
        league = _league(req.query or {})
        if not league:
            return 400, {'error':'league must be nba or nfl'}
        path = _dir() / ('gradecore_export_' + league + '.json')
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                return 503, {'error':'export_exceeds_download_budget'}
        except FileNotFoundError:
            return 503, {'error':'gradecore_export_not_built'}
        return 200, _load(path.name)
    if rest == "gradecheck":
        return admin_gradecheck(req)
    if rest != "learning":
        return 404, {"error": "not found"}
    d = _load("admin_learning.json")
    return (200, d) if d is not None else (503, {"error": "model_snapshot_not_ready"})


def admin_gradecheck(req):
    """GET /api/sports/admin/model/gradecheck[?part=headline] - the daily grade check (REGISTER A9a 3), read-only:
    the headline per league, the containment state + log, the cal-mi daily decisions; without part=headline also the
    full compact gradecheck JSON the job wrote (model/gradecheck.json). Files only; nothing is computed here."""
    from .model import daily
    p = daily.paths()["gradecheck"]
    try:
        mt = os.stat(p).st_mtime_ns
    except FileNotFoundError:
        mt = None
    view = web.cached(("admin_gradecheck_view", mt), daily.admin_view, ttl=60)
    if (req.query or {}).get("part") == "headline":
        return 200, view
    full = web.cached(("admin_gradecheck", mt), lambda: json.loads(p.read_text(encoding="utf-8")), ttl=300) \
        if mt is not None else None
    return 200, dict(view, gradecheck=full)


def ladder_row(d, line):
    lo, p = d["lo"], d["p"]
    over = under = push = 0.0
    for i, x in enumerate(p):
        k = lo + i
        if k > line + 1e-9:
            over += x
        elif k < line - 1e-9:
            under += x
        else:
            push += x
    t = over + under + push
    return {"line": line, "p_over": over / t, "p_under": under / t, "p_push": push / t}


def _median(d):
    c = 0.0
    for i, x in enumerate(d["p"]):
        c += x
        if c >= 0.5:
            return float(d["lo"] + i)
    return float(d["lo"])
