"""GET /api/sports/admin/sanity - the board's model guards (ADMIN; sanity agent, owner re-audit 2026-10-24 N1/N2).

Reads only the snapshot files the worker wrote (<league>/sanity.json from sports/snapshots.write_sanity): the vg-1 gate
state per stat (registered sm-1 validation; a failed gate = projection only) and every quote the sg-1 sanity guard
holds now, with its inputs (player, stat, line, projection, L10, model, forecast, reason). No store query.
Query: league=nba,nfl (default both), limit (default 200, max 3000), offset (default 0) - paged: each league answers
total / offset / next_offset (None on the last page).
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
    try:
        limit = max(1, min(3000, int(req.query.get("limit") or 200)))
    except ValueError:
        limit = 200
    try:
        offset = max(0, int(req.query.get("offset") or 0))
    except ValueError:
        offset = 0
    out = {}
    for lg in lgs:
        d = _load(snapshots.snap_root() / lg / "sanity.json")
        if d is None:
            out[lg] = {"built": False}
            continue
        out[lg] = {k: d.get(k) for k in ("built_at", "as_of", "rule", "gate_rule", "gate", "gate_held_quotes",
                                         "held", "reasons", "truncated")}
        ents = d.get("entries") or []
        out[lg]["entries"] = ents[offset:offset + limit]
        out[lg].update(total=len(ents), offset=offset, limit=limit,
                       next_offset=offset + limit if offset + limit < len(ents) else None)
    return 200, out
