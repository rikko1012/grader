"""Scout Sports read endpoints for the member workspace (UI agent). Registered in sports/web.py ROUTES, run in the
sports service process. Every answer is a PRECOMPUTED snapshot file written by the worker (sports/snapshots.py): no
store query, no model work per request - a handler reads bytes from disk once per file change (mtime + size) and
serves them from a bounded in-memory cache.

  GET /api/sports/manifest?league=nba          build time, versions, per-source freshness, counts
  GET /api/sports/slate?league=nba             games, injuries, coverage
  GET /api/sports/board?league=nba[&part=more|view] prop board (primary stats; more = secondary; view = bv-1 hit rates)
  GET /api/sports/forecast?league=nba&id=fc-.. one forecast record view (the same copy every surface renders)
  GET /api/sports/player?league=nba&id=nba:player:espn:123   Player Workspace
  GET /api/sports/history?league=nba&id=nba:player:espn:123  per-game history with typed record ids
  GET /api/sports/results?league=nba           results ledger (accuracy separate from recorded payouts)

Errors: 400 bad parameter, 404 unknown player / forecast, 503 {"error": "snapshot_not_ready"} before the first build.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from . import snapshots

LEAGUES = ("nba", "nfl")
CACHE_FILES = int(os.environ.get("SPORTS_BOARD_CACHE_FILES", "96"))   # bounded: board/slate/... + recent players
CACHE_BYTES = int(float(os.environ.get("SPORTS_BOARD_CACHE_MB", "48")) * 1_000_000)   # Starter template: 16
_ID = re.compile(r"^(nba|nfl):player:[a-z]+:[A-Za-z0-9._-]{1,40}$")
_FC = re.compile(r"^fc-[0-9a-f]{8,64}$")
_LOCK = threading.Lock()
_FILES = {}                         # path -> (mtime_ns, size, bytes)
_PARSED = {}                        # path -> (mtime_ns, size, obj) for forecasts.json only
_PARSE_LOCK = threading.Lock()      # one forecasts.json parse at a time (two leagues; a parse is milliseconds)


class NotReady(Exception):
    pass


def _root():
    return snapshots.snap_root()


def _read(path: Path) -> bytes:
    try:
        st = path.stat()
    except OSError:
        raise NotReady(path.name) from None
    key = str(path)
    with _LOCK:
        hit = _FILES.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    data = path.read_bytes()
    with _LOCK:
        total = sum(len(v[2]) for v in _FILES.values())
        if len(_FILES) >= CACHE_FILES or total + len(data) > CACHE_BYTES:
            for k in list(_FILES)[:max(1, len(_FILES) // 3)]:      # oldest inserted first
                _FILES.pop(k, None)
        _FILES[key] = (st.st_mtime_ns, st.st_size, data)
    return data


def _league(req):
    lg = (req.query.get("league") or "").lower()
    if lg not in LEAGUES:
        return None
    return lg


def _serve(path):
    try:
        return 200, _read(path), "application/json"
    except NotReady:
        return 503, {"error": "snapshot_not_ready"}


def _simple(name):
    def fn(req):
        lg = _league(req)
        if not lg:
            return 400, {"error": "league must be nba or nfl"}
        return _serve(_root() / lg / name)
    fn.__name__ = name.split(".")[0]
    return fn


manifest = _simple("manifest.json")
slate = _simple("slate.json")
results = _simple("results.json")


def board(req):
    lg = _league(req)
    if not lg:
        return 400, {"error": "league must be nba or nfl"}
    part = req.query.get("part") or "primary"
    files = {"primary": "board.json", "more": "board_more.json", "view": "board_view.json"}
    if part not in files:
        return 400, {"error": "part must be primary, more or view"}
    return _serve(_root() / lg / files[part])


def _player_file(req, folder):
    lg = _league(req)
    if not lg:
        return 400, {"error": "league must be nba or nfl"}
    pid = req.query.get("id") or ""
    if not _ID.match(pid) or not pid.startswith(lg + ":"):
        return 400, {"error": "id must be a player id of this league"}
    path = _root() / lg / folder / f"{snapshots.safe_name(pid)}.json"
    if not path.exists():
        if not (_root() / lg / "manifest.json").exists():
            return 503, {"error": "snapshot_not_ready"}
        return 404, {"error": "player_not_on_board"}
    return _serve(path)


def player(req):
    return _player_file(req, "players")


def history(req):
    return _player_file(req, "history")


def forecast(req):
    lg = _league(req)
    if not lg:
        return 400, {"error": "league must be nba or nfl"}
    fid = req.query.get("id") or ""
    if not _FC.match(fid):
        return 400, {"error": "id must be a forecast id"}
    path = _root() / lg / "forecasts.json"
    try:
        st = path.stat()
    except OSError:
        return 503, {"error": "snapshot_not_ready"}
    key = str(path)
    with _LOCK:
        hit = _PARSED.get(key)
    if not hit or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
        # single flight (audit 2026-10-26): when a snapshot rebuild lands, the members asking at that moment wait for ONE
        # parse of forecasts.json instead of each parsing the whole file at once (every service thread on the same work)
        with _PARSE_LOCK:
            with _LOCK:
                hit = _PARSED.get(key)
            if not hit or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
                obj = json.loads(_read(path))
                hit = (st.st_mtime_ns, st.st_size, obj)
                with _LOCK:
                    _PARSED[key] = hit
    obj = hit[2]
    f = obj["forecasts"].get(fid)
    if f is None:
        return 404, {"error": "forecast_not_on_board"}
    return 200, {"version": obj.get("version"), "built_at": obj.get("built_at"), "forecast": f}


def cache_info():
    with _LOCK:
        return {"files": len(_FILES), "bytes": sum(len(v[2]) for v in _FILES.values()), "max_files": CACHE_FILES,
                "max_bytes": CACHE_BYTES, "pid": os.getpid()}
