#!/usr/bin/env python3
"""
bo3.py - BO3.gg data through the cs2api wrapper (tommhe14/CS2API, in ./cs2api).

The wrapper is used as-is, with three changes made here from the outside:
  - honest headers: the wrapper pretends to be bo3.gg's own website in Chrome;
    we identify as cs2_scout instead (the API answers the same either way)
  - polite pacing: at least MIN_GAP seconds between requests
  - caching: results saved in .cache, slimmed to the fields the grader reads and gzipped
    (finished map scoreboards kept 400 days, the rest refreshed after TTL hours and deleted
    after 7 days unused - see prune())

Everything is synchronous for the rest of the project: BO3().player_stats(...)
"""
import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from cs2api import CS2

# CS2_DATA_DIR = persistent storage on the host (e.g. a Railway volume); defaults to this folder
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
CACHE_DIR = DATA_DIR / ".cache" / "bo3"
GAMES_DIR = CACHE_DIR / "games"     # finished map scoreboards - kept long term
KEEP_DAYS = 7                       # everything else is deleted after this many days unused
KEEP_GAME_DAYS = 400                # scoreboards older than this are never in a stats window
GAME_ENDPOINT = re.compile(r"^/games/\d+/players_stats$")

# ------------------------------------------------------------ slimming
# BO3.gg sends far more than the grader reads (betting promos, AI predictions, streams,
# round-by-round ratings). Only these fields are saved - ~95% smaller on disk.
_MATCH = ("id", "slug", "status", "start_date", "end_date", "team1_id", "team2_id", "winner_team_id",
          "loser_team_id", "team1_score", "team2_score", "bo_type", "tier", "tier_rank", "parsed_status")
_TEAM = ("id", "slug", "name", "rank")
_TOURNAMENT = ("id", "slug", "name", "tier", "tier_rank")
_GAME = ("id", "match_id", "map_name", "number", "state", "status", "winner_clan_score", "loser_clan_score",
         "begin_at", "rounds_count")
_ROW = ("game_id", "clan_name", "enemy_clan_name", "win", "kills", "death", "assists", "headshots",
        "first_kills", "first_death", "trade_kills", "trade_death", "kast", "player_rating", "multikills",
        "adr", "hits", "shots", "damage", "clutches", "weapons_value")


def _pick(d, keys):
    return {k: d[k] for k in keys if isinstance(d, dict) and k in d}


def _slim_match(m):
    out = _pick(m, _MATCH)
    for side in ("team1", "team2"):
        if isinstance(m.get(side), dict):
            out[side] = _pick(m[side], _TEAM)
    if isinstance(m.get("tournament"), dict):
        out["tournament"] = _pick(m["tournament"], _TOURNAMENT)
    out["games"] = []
    for g in m.get("games") or []:
        sg = _pick(g, _GAME)
        for k in ("winner_team_clan", "loser_team_clan"):
            if isinstance(g.get(k), dict):
                sg[k] = {"team_id": g[k].get("team_id")}
        out["games"].append(sg)
    return out


def _slim_row(r):
    out = _pick(r, _ROW)
    tc = r.get("team_clan") or {}
    out["team_clan"] = {"team_id": tc.get("team_id"), "clan_name": tc.get("clan_name"),
                        "team": _pick(tc.get("team") or {}, _TEAM)}
    sp = r.get("steam_profile") or {}
    out["steam_profile"] = {"id": sp.get("id"), "steam_id_64": sp.get("steam_id_64"),
                            "nickname": sp.get("nickname"), "player_id": sp.get("player_id"),
                            "player": _pick(sp.get("player") or {}, ("id", "slug", "nickname"))}
    return out


def slim(endpoint, data):
    if endpoint == "/matches" and isinstance(data, dict) and isinstance(data.get("results"), list):
        return dict(_pick(data, ("total",)), results=[_slim_match(m) for m in data["results"]])
    if GAME_ENDPOINT.match(endpoint) and isinstance(data, list):
        return [_slim_row(r) for r in data]
    return data


# ------------------------------------------------------------ storage (gzip, atomic, never fatal)
_warned = [False]


def _read(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, EOFError):
        return None


def _write(path, data):
    """Atomic gzip write. A full or read-only disk only means this answer isn't saved."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        if not _warned[0]:
            print(f"cache write failed ({e}) - continuing without saving", file=sys.stderr, flush=True)
            _warned[0] = True


def prune():
    """Free space: delete temp files and anything unused for KEEP_DAYS (map scoreboards:
    KEEP_GAME_DAYS), and shrink files saved in the old uncompressed format."""
    freed, now = 0, time.time()
    if not CACHE_DIR.exists():
        return 0
    for p in list(CACHE_DIR.rglob("*")):
        if not p.is_file():
            continue
        try:
            size, age = p.stat().st_size, (now - p.stat().st_mtime) / 86400
            if p.suffix == ".tmp":
                p.unlink()
                freed += size
            elif p.suffix == ".json":   # old format: read, delete, re-save slimmed + compressed
                data = json.loads(p.read_text(encoding="utf-8"))
                mtime = p.stat().st_mtime
                p.unlink()
                freed += size
                game = isinstance(data, list) and data and isinstance(data[0], dict) and "steam_profile" in data[0]
                is_match = isinstance(data, dict) and isinstance(data.get("results"), list) and \
                    data["results"] and "games" in data["results"][0]
                if is_match or (not game and age > KEEP_DAYS):
                    continue   # old match lists are re-fetched slim on next use; stale extras dropped
                new = (GAMES_DIR if game else CACHE_DIR) / (p.stem + ".json.gz")
                _write(new, [_slim_row(r) for r in data] if game else data)
                if new.exists():
                    os.utime(new, (mtime, mtime))
                    freed -= new.stat().st_size
            elif age > (KEEP_GAME_DAYS if p.parent == GAMES_DIR else KEEP_DAYS):
                p.unlink()
                freed += size
        except (OSError, ValueError):
            continue
    return freed


def storage_report():
    total = files = 0
    if CACHE_DIR.exists():
        for p in CACHE_DIR.rglob("*"):
            if p.is_file():
                files += 1
                total += p.stat().st_size
    du = shutil.disk_usage(DATA_DIR)
    return {"cache_mb": round(total / 1e6, 1), "cache_files": files,
            "disk_used_pct": round(100 * du.used / du.total, 1), "disk_free_mb": round(du.free / 1e6)}
HONEST_HEADERS = {
    "accept": "application/json",
    "user-agent": "cs2_scout/0.2 (CS2 stats tool; python-aiohttp)",
}
MIN_GAP = 1.0          # seconds between requests - shared by every BO3 client in this process
FOREVER = 10 ** 6      # hours - finished map stats never change

# One request schedule for the whole process (member look-ups + background pre-loading), so
# running both never doubles the rate. Members come first: low-priority clients wait while any
# member look-up is running.
_SLOT = threading.Lock()
_next_slot = [0.0]
_members_active = [0]


def _reserve_slot():
    with _SLOT:
        now = time.time()
        at = max(now, _next_slot[0])
        _next_slot[0] = at + MIN_GAP
        return at - now


class BO3:
    def __init__(self, ttl_hours=12, background=False):
        self.ttl = ttl_hours
        self.background = background
        if not background:
            with _SLOT:
                _members_active[0] += 1
        self.loop = asyncio.new_event_loop()
        self.cs2 = CS2()
        self.cs2._api.headers = HONEST_HEADERS          # before the session is created
        self._raw = self.cs2._api._make_request
        self.cs2._api._make_request = self._request     # every wrapper method goes through here
        self._ttl_now = ttl_hours
        self.requests = 0

    # ------------------------------------------------------------ plumbing
    async def _request(self, endpoint, params=None):
        key = endpoint + "?" + json.dumps(params or {}, sort_keys=True, default=str)
        folder = GAMES_DIR if GAME_ENDPOINT.match(endpoint) else CACHE_DIR
        path = folder / (hashlib.sha1(key.encode()).hexdigest() + ".json.gz")
        ttl = self._ttl_now
        try:
            fresh = ttl > 0 and time.time() - path.stat().st_mtime < ttl * 3600
        except OSError:
            fresh = False
        if fresh:
            data = _read(path)
            if data is not None:
                return data
        while self.background and _members_active[0] > 0:
            await asyncio.sleep(0.5)                    # a member is waiting - step aside
        wait = _reserve_slot()                          # spaced out across the whole process
        if wait > 0:
            await asyncio.sleep(wait)
        for attempt in range(3):
            try:
                data = await self._raw(endpoint, params)
                break
            except Exception as e:
                if attempt == 2 or not any(c in str(e) for c in ("429", "502", "503", "504", "Timeout")):
                    raise
                await asyncio.sleep(10 * (attempt + 1))
        self.requests += 1
        data = slim(endpoint, data)
        _write(path, data)
        return data

    def _run(self, coro, ttl=None):
        self._ttl_now = self.ttl if ttl is None else (ttl if self.ttl > 0 else 0)
        try:
            return self.loop.run_until_complete(coro)
        finally:
            self._ttl_now = self.ttl

    def close(self):
        if not self.background:
            with _SLOT:
                _members_active[0] -= 1
        self.loop.run_until_complete(self.cs2.close())
        self.loop.close()

    # ------------------------------------------------------------ wrapper methods
    def search_players(self, q, limit=10):
        return self._run(self.cs2.search_players(q, limit))

    def player(self, slug):
        return self._run(self.cs2.get_player_details(slug))

    def player_stats(self, slug, days):
        return self._run(self.cs2.get_player_stats(slug, days))

    def player_matches(self, player_id, pages=3):
        """Newest first. The wrapper returns the latest 100; later pages use the same query."""
        first = self._run(self.cs2.get_player_matches(player_id))
        results = list(first.get("results") or [])
        total = (first.get("total") or {}).get("count") or 0
        for page in range(1, pages):
            if len(results) >= total:
                break
            params = {
                "scope": "widget-matches", "page[offset]": 100 * page, "page[limit]": 100,
                "sort": "-start_date", "filter[matches.status][in]": "finished,defwin,upcoming,current",
                "filter[matches.player_ids][overlap]": str(player_id),
                "filter[matches.discipline_id][eq]": 1,
                "with": "teams,tournament,games",   # no predictions/streams - never used
            }
            results += self._run(self.cs2._api._make_request("/matches", params)).get("results") or []
        return dict(first, results=results)

    def team(self, slug):
        return self._run(self.cs2.get_team_data(slug))

    def todays_matches(self):
        return self._run(self.cs2.get_todays_matches(), ttl=0.5)

    def team_upcoming(self, team_id):
        return self._run(self.cs2.get_team_upcoming_matches(team_id), ttl=0.5)

    # ------------------------------------------------------------ endpoints the wrapper doesn't cover
    def game_players(self, game_id, finished=True):
        """Per-player scoreboard for one map: kills, deaths, headshots, KAST, ADR, rating, Steam ID."""
        return self._run(self.cs2._api._make_request(f"/games/{game_id}/players_stats"),
                         ttl=FOREVER if finished else None)
