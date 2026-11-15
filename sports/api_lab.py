"""Admin-only model lab handlers (route: "/api/sports/admin/lab/*" -> route(req), ADMIN; req.rest = sub-path).

  GET  overview                      features (definitions), algorithms + versions, data sources available, model
                                     registry (read-only adapter), recent experiments
  POST experiments                   queue an experiment spec (validated; idempotent id) -> {"id", "state"}
  GET  experiment?id=                full record: spec, selected features + definitions, dates, train / tune / test
                                     counts, algorithm / version / seed, fitted parameters, baseline comparison,
                                     calibration, inputs / outputs hashes, replay history
  GET  export?id=&format=json|csv    per-row predictions (actual, mean, sd, line + source, p_over / p_under / p_push,
                                     source record refs)
  POST replay                        {"id"} -> queue a replay from the saved inputs (the runner compares hashes)
Handlers only read / enqueue in lab.db; training runs in the lab runner process (python -m sports.lab.runner).
Page: web/sports/lab.html (served at /sports/lab.html to the owner only - server.py answers 403 to anyone else, UI 1114;
the server mints the admin-role token these routes need, the page never asks for one).
"""
from __future__ import annotations

import csv
import io

from .lab import ALGORITHMS, features, jobs, registry, sources

CSV_COLS = ("row_key", "split", "game_id", "player_id", "start_utc", "forecast_at", "actual", "pred_mean", "pred_sd",
            "line", "line_source", "line_record_id", "p_over", "p_under", "p_push")


def route(req):
    sub = (req.rest or "").strip("/")
    con = jobs.connect()
    try:
        if sub in ("", "overview") and req.method == "GET":
            return 200, {"features": features.listing(), "feature_registry": features.REGISTRY_VERSION,
                         "algorithms": ALGORITHMS, "targets": sources.TARGETS,
                         "sources": sources.availability(), "registry": registry.versions(),
                         "experiments": jobs.listing(con)}
        if sub == "experiments" and req.method == "POST":
            eid, state = jobs.queue(con, req.json(), req.member or "admin")
            return 202, {"id": eid, "state": state}
        if sub == "experiment" and req.method == "GET":
            e = jobs.get(con, str(req.query.get("id") or ""))
            return (200, e) if e else (404, {"error": "experiment not found"})
        if sub == "export" and req.method == "GET":
            eid = str(req.query.get("id") or "")
            preds = jobs.predictions(con, eid)
            if preds is None:
                return 404, {"error": "no predictions for this experiment"}
            total = len(preds)
            try:                                        # optional paging: &offset=&limit= (none = every row, the
                off = max(0, int(req.query.get("offset") or 0))     # page's one-file download)
                lim = int(req.query["limit"]) if req.query.get("limit") else None
            except ValueError:
                return 400, {"error": "offset / limit must be numbers"}
            if lim is not None:
                lim = max(1, min(50000, lim))
                preds = preds[off:off + lim]
            elif off:
                preds = preds[off:]
            if req.query.get("format") == "csv":
                buf = io.StringIO()
                w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore", lineterminator="\n")
                w.writeheader()
                w.writerows(preds)
                return 200, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8"
            nxt = off + len(preds) if off + len(preds) < total else None
            return 200, {"id": eid, "rows": len(preds), "total": total, "offset": off, "next_offset": nxt,
                         "predictions": preds}
        if sub == "replay" and req.method == "POST":
            eid = str((req.json() or {}).get("id") or "")
            try:
                rid = jobs.queue_replay(con, eid, req.member or "admin")
            except KeyError:
                return 404, {"error": "experiment not found"}
            return 202, {"id": eid, "replay": rid, "state": "queued"}
        return 404, {"error": "not found"}
    finally:
        con.close()

