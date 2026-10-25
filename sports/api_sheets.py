"""Sheets agent read endpoints (sports service process; one ROUTES line each in sports/web.py). Every answer is a file the
worker wrote (sports/sheets.py, sports/without.py, sports/results_public.py): bytes read once per file change and served
from api_board's bounded file cache - no store query, no model work per request.

  GET /api/sports/sheets?league=nba&sheet=prizepicks|underdog|consistent
  GET /api/sports/without?league=nba&team=nba:team:espn:18      one team's games-without index (player page module)
  GET /api/sports/results/public                                 public Results (members; everyone when
                                                                 SPORTS_RESULTS_PUBLIC=1 - web.PUBLIC_WHEN)
Errors: 400 bad parameter, 404 unknown team, 503 {"error": "snapshot_not_ready"} before the first build.
"""
from __future__ import annotations

import json
import re
import threading

from . import api_board, results_public, sheets, without

_TEAM = re.compile(r"^(nba|nfl):team:[a-z]+:[A-Za-z0-9._-]{1,40}$")
_LOCK = threading.Lock()
_PARSED = {}                    # without.json path -> (mtime_ns, size, obj)


def sheet(req):
    lg = api_board._league(req)
    if not lg:
        return 400, {"error": "league must be nba or nfl"}
    name = req.query.get("sheet") or "prizepicks"
    if name not in sheets.SHEETS:
        return 400, {"error": "sheet must be one of " + ", ".join(sheets.SHEETS)}
    return api_board._serve(sheets.sheet_dir(lg) / f"{name}.json")


def _without_doc(lg):
    p = without.path(lg)
    try:
        st = p.stat()
    except OSError:
        return None
    key = str(p)
    with _LOCK:
        hit = _PARSED.get(key)
    if not hit or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
        hit = (st.st_mtime_ns, st.st_size, json.loads(api_board._read(p)))
        with _LOCK:
            _PARSED[key] = hit
    return hit[2]


def without_view(req):
    lg = api_board._league(req)
    if not lg:
        return 400, {"error": "league must be nba or nfl"}
    team = req.query.get("team") or ""
    if not _TEAM.match(team) or not team.startswith(lg + ":"):
        return 400, {"error": "team must be a team id of this league"}
    try:
        doc = _without_doc(lg)
    except api_board.NotReady:
        doc = None
    if doc is None:
        return 503, {"error": "snapshot_not_ready"}
    out = without.team_slice(doc, team)
    if out is None:
        return 404, {"error": "team_not_in_index"}
    return 200, out


def results_view(req):
    return api_board._serve(results_public.path())

