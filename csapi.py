#!/usr/bin/env python3
"""
csapi.py - CS API (api.csapi.de): free, no key. A second source BEHIND BO3.gg for top teams.

What it is used for (checked against the live responses and BO3.gg on 2026-09-29):
- roster cross-check (Gate C second opinion), VRS world ranking, rating / Round Swing / ADR /
  KAST context, CT/T split (context only, never a multiplier), and a staleness check.
What it is NOT used for:
- Maps 1-2 or H2H pairs: its map lists are not in play order (FURIA-Vitality 2026-09-04 was
  Nuke, Cache, Dust2 on BO3.gg; CS API lists Nuke, Dust2, Cache and Dust2, Nuke, Cache).
- Headshots (not in the data) and sub-ratings.
Its data runs weeks behind: every answer carries the newest match date it knows about.

Use:  python csapi.py ZywOo
"""
import json
import os
import re
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = "https://api.csapi.de"
MIN_GAP = 0.5
TTL = 6 * 3600
STALE_DAYS = 7          # CS API's newest match this many days older than BO3.gg's = stale warning
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "cs2-scout/1.0 (https://github.com/rikko1012/cs2-scout)"})
_LOCK, _LAST, _CACHE = threading.Lock(), [0.0], {}


def _get(path, **params):
    ck = (path, tuple(sorted(params.items())))
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    with _LOCK:
        wait = _LAST[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = _SESSION.get(BASE + path, params=params, timeout=20)
        finally:
            _LAST[0] = time.time()
    r.raise_for_status()
    data = r.json()
    _CACHE[ck] = (time.time(), data)
    return data


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def find_player(nick):
    return next((p for p in _get("/players/", name=nick) or [] if _norm(p.get("name")) == _norm(nick)), None)


def newest_match_date():
    rows = _get("/matches/latest", limit=5) or []
    return max((m.get("date") or "" for m in rows), default=None)


def rankings():
    return {r["id"]: r for r in (_get("/rankings/") or {}).get("rankings") or []}


def player_check(nick, bo3_team=None, bo3_last_date=None, today=None):
    """Everything CS API adds for one player, or {'found': False, ...}. Never raises.
    Windows follow the owner's rule: 3 months if 10+ maps, else 6, else 12."""
    try:
        p = find_player(nick)
        if not p:
            return {"found": False, "note": "Not in CS API (it covers top teams only) - BO3.gg only"}
        today = today or date.today()
        stats, window = None, None
        for months, days in ((3, 90), (6, 180), (12, 365)):
            since = (today - timedelta(days=days)).isoformat()
            d = _get(f"/players/{p['id']}", start_date=since, end_date=today.isoformat())
            if (d.get("stats") or {}).get("N", 0) >= 10 or months == 12:
                stats, window, team = d.get("stats") or {}, f"{months}m", d.get("team") or {}
                break
        sides = {s["name"]: s for s in _get(f"/players/{p['id']}/stats/sides", start_date=since,
                                             end_date=today.isoformat()) or []}
        newest = newest_match_date()
        roster = [x.get("name") for x in (_get(f"/teams/{team['id']}").get("roster") or [])] if team.get("id") else []
        rank = rankings().get(team.get("id"))
        out = {"found": True, "id": p["id"], "team": team.get("name"), "window": window,
               "maps": stats.get("N"), "kills_per_map": stats.get("k"), "deaths_per_map": stats.get("d"),
               "rating": stats.get("rating"), "swing": stats.get("swing"), "adr": stats.get("adr"),
               "kast": stats.get("kast"),
               "ct": {k: sides.get("ct", {}).get(k) for k in ("k", "rating", "N")},
               "t": {k: sides.get("t", {}).get(k) for k in ("k", "rating", "N")},
               "on_roster": _norm(nick) in {_norm(n) for n in roster} if roster else None,
               "roster": roster, "vrs_rank": rank.get("rank") if rank else None,
               "newest_match": newest}
        if bo3_team and team.get("name") and _norm(bo3_team) != _norm(team["name"]):
            out["team_mismatch"] = f"CS API has him on {team['name']}, BO3.gg on {bo3_team}"
        if newest and bo3_last_date and (date.fromisoformat(bo3_last_date[:10]) -
                                         date.fromisoformat(newest[:10])).days > STALE_DAYS:
            out["stale"] = (f"CS API data may be stale: its newest match is {newest}, his last "
                            f"BO3.gg map is {bo3_last_date[:10]}")
        return out
    except Exception as e:
        return {"found": False, "note": f"CS API unavailable ({type(e).__name__})"}


# ---------------------------------------------------------------- never block a grade
# The check is weight 0 context, so grades never wait for it: the first request queues it for a
# single background worker (paced, one at a time) and the card shows it from the next grade on.
# Answers are kept 6 h in memory and on disk, so a restart/deploy doesn't lose them.
_DONE = {}                       # key -> (time, result)
_PENDING, _QLOCK = [], threading.Lock()
_WAKE = threading.Event()
_FILE = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / ".cache" / "csapi_checks.json"
_SAVED = [0.0]


def _load():
    try:
        now = time.time()
        _DONE.update({k: tuple(v) for k, v in json.loads(_FILE.read_text("utf-8")).items() if now - v[0] < TTL})
    except Exception:
        pass


def _save():
    if time.time() - _SAVED[0] < 30:
        return
    _SAVED[0] = time.time()
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(dict(list(_DONE.items())), default=str), "utf-8")
        os.replace(tmp, _FILE)
    except Exception:
        pass


def _worker():
    while True:
        _WAKE.wait(30)
        _WAKE.clear()
        while True:
            with _QLOCK:
                if not _PENDING:
                    break
                key, args = _PENDING.pop(0)
            _DONE[key] = (time.time(), player_check(*args))
            if len(_DONE) > 5000:
                _DONE.clear()
            _save()


def player_check_async(nick, bo3_team=None, bo3_last_date=None):
    """Cached answer right away, or a 'loading' note while the background worker fetches it."""
    key = "|".join(str(x or "").lower() for x in (nick, bo3_team, (bo3_last_date or "")[:10]))
    hit = _DONE.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    with _QLOCK:
        if key not in {k for k, _ in _PENDING} and len(_PENDING) < 500:
            _PENDING.append((key, (nick, bo3_team, bo3_last_date)))
    _WAKE.set()
    return {"found": False, "pending": True, "note": "CS API check is loading - it shows on the next grade"}


_load()
threading.Thread(target=_worker, daemon=True, name="csapi").start()


if __name__ == "__main__":
    print(json.dumps(player_check(sys.argv[1] if len(sys.argv) > 1 else "ZywOo"), indent=2))
