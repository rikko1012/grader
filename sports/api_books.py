"""GET /api/sports/admin/books - books coverage + data-quality ops view (ADMIN). Reads only snapshot files the worker
wrote (manifest.json `sources` / `market_view` / `forecast_run`, mapping_audit.json): no store query, no provider call.

Per league: every market source with offers on the board now, last successful read, verified flag, rows where it is
counted in the market estimate, and for capture-only sources (thunderpick, oddspapi) the stored inventory summary
(sports/books/inventory.py); the market-view counts; the last forward run; the player-team-game mapping audit.
"""
from __future__ import annotations

import json

from . import snapshots

LEAGUES = ("nba", "nfl")


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def admin(req):
    lgs = [x for x in (req.query.get("league") or "nba,nfl").split(",") if x in LEAGUES] or list(LEAGUES)
    out = {}
    for lg in lgs:
        root = snapshots.snap_root() / lg
        man = _load(root / "manifest.json") or {}
        audit = _load(root / "mapping_audit.json")
        out[lg] = {"built_at": man.get("built_at"), "sources": man.get("sources"),
                   "market_view": man.get("market_view"), "forecast_run": man.get("forecast_run"),
                   "models": man.get("models"),
                   "mapping_audit": None if audit is None else {
                       "as_of": audit.get("as_of"), "offers_checked": audit.get("offers_checked"),
                       "total_mismatches": audit.get("total_mismatches"),
                       # a budget stop: the counts cover only part of the offers (the next run resumes after it)
                       "truncated": bool(audit.get("truncated")),
                       "checks": {k: {"count": v["count"], "by_platform": v["by_platform"],
                                      "examples": v["examples"][:5]} for k, v in (audit.get("checks") or {}).items()}}}
    return 200, out
