#!/usr/bin/env python3
"""
pandascore.py - backup schedule source (free plan: fixtures, results, rosters; 1000 req/hour).

BO3.gg misses some events entirely (e.g. CIS LAN Championship). PandaScore lists them, so when
BO3.gg has no match we look here: by the player's team name, or by the opponent the member typed.
Key: PANDASCORE_API_KEY (env only). Answers cached (teams 12 h, schedules 10 min).

Use:  python pandascore.py Krytiepacani
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

import membudget

BASE = "https://api.pandascore.co/csgo"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "cs2-scout/1.0 (https://github.com/rikko1012/cs2-scout)"})
# audit 37: was a plain dict never emptied, keyed by member-typed opponent text - now byte- and count-bounded
_CACHE = membudget.BudgetLRU("pandascore", membudget.env_mb("PANDASCORE_MEM_MB", 10), max_items=2000)   # (fetched at, data)
GENERIC = {"team", "esports", "esport", "gaming", "club", "gg", "the"}


def key():
    return os.environ.get("PANDASCORE_API_KEY") or os.environ.get("panda_score_api")   # Railway name


def _get(path, ttl, **params):
    ck = (path, tuple(sorted((k, " ".join(str(v).split()).lower()) for k, v in params.items())))   # case / spacing variants share one entry
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = _SESSION.get(BASE + path, params=params, timeout=15,
                         headers={"Authorization": f"Bearer {key()}"})
    except requests.RequestException as e:
        raise RuntimeError(f"PandaScore request failed ({type(e).__name__})") from None
    if r.status_code != 200:
        raise RuntimeError(f"PandaScore {path} -> HTTP {r.status_code}")
    data = r.json()
    _CACHE.put(ck, (time.time(), data), membudget.json_cost(data))
    return data


def _norm(s):
    words = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).split()
    core = [w for w in words if w not in GENERIC]
    return " ".join(core or words)


def find_team(name):
    """PandaScore team whose name, acronym or slug matches exactly (after dropping 'Team' etc)."""
    n = _norm(name)
    if not n:
        return None
    for t in _get("/teams", 12 * 3600, **{"search[name]": name, "per_page": 20}):
        if n in (_norm(t.get("name")), _norm(t.get("acronym")), _norm(t.get("slug"))):
            return t
    return None


def upcoming(team_name):
    """Upcoming (not started) matches for a team, earliest first:
    [{'opponent', 'team', 'start', 'event', 'bo', 'id'}]."""
    if not key():
        return []
    try:
        t = find_team(team_name)
        if not t:
            return []
        rows = _get("/matches/upcoming", 600, **{"filter[opponent_id]": t["id"], "per_page": 10})
    except Exception:
        return []
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for m in rows:
        opps = [o.get("opponent") or {} for o in m.get("opponents") or []]
        other = next((o for o in opps if o.get("id") != t["id"]), None)
        if not other or (m.get("begin_at") or "") <= now[:19]:
            continue
        out.append({"id": m.get("id"), "team": t.get("name"), "opponent": other.get("name"),
                    "start": m.get("begin_at"), "bo": m.get("number_of_games"),
                    "event": " - ".join(x for x in ((m.get("league") or {}).get("name"),
                                                    (m.get("serie") or {}).get("full_name")) if x)})
    return sorted(out, key=lambda x: x["start"] or "")


def player_team(nick):
    """PandaScore's current team for a player nickname (exact nickname match), or None."""
    if not key():
        return None
    try:
        for p in _get("/players", 12 * 3600, **{"search[name]": nick, "per_page": 10}):
            if (p.get("name") or "").lower() == nick.lower() and p.get("current_team"):
                return p["current_team"].get("name")
    except Exception:
        return None
    return None


if __name__ == "__main__":
    print(json.dumps(upcoming(sys.argv[1] if len(sys.argv) > 1 else "Krytiepacani"), indent=2))
