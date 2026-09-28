#!/usr/bin/env python3
"""
bo3.py - BO3.gg data through the cs2api wrapper (tommhe14/CS2API, in ./cs2api).

The wrapper is used as-is, with three changes made here from the outside:
  - honest headers: the wrapper pretends to be bo3.gg's own website in Chrome;
    we identify as cs2_scout instead (the API answers the same either way)
  - polite pacing: at least MIN_GAP seconds between requests
  - caching: results saved in .cache (finished maps forever, the rest for TTL hours)

Everything is synchronous for the rest of the project: BO3().player_stats(...)
"""
import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from cs2api import CS2

# CS2_DATA_DIR = persistent storage on the host (e.g. a Railway volume); defaults to this folder
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
CACHE_DIR = DATA_DIR / ".cache" / "bo3"
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
        path = CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".json")
        ttl = self._ttl_now
        if ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < ttl * 3600:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass  # unreadable - fetch it again
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
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)   # atomic: readers never see a half-written file
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
                "with": "teams,tournament,ai_predictions,games,streams",
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
