"""Player roles for the slip builder: IGLs from Liquipedia team rosters (`igl=y` in the active squad).

Display / slip filtering only - the grader's own IGL input (igl.csv) is untouched (frozen grader).
One batched Liquipedia API request (up to 50 team pages) at most every 6 hours, cached on the volume
for 2 days per team, within Liquipedia's API terms (identifying user agent, gzip, <= 1 request / 2 s).
"""
import json
import re
import threading
import time
from pathlib import Path

import requests

from bo3 import DATA_DIR

API = "https://liquipedia.net/counterstrike/api.php"
UA = "cs2-scout-roles/1.0 (+https://cs2-scout-production.up.railway.app)"
CACHE = Path(DATA_DIR) / ".cache" / "liquipedia_roles.json"
TEAM_TTL = 2 * 86400          # re-read rosters every 2 days (roster moves)
MIN_GAP = 6 * 3600
_LOCK = threading.Lock()
_STATE = {"teams": None, "last_fetch": 0.0}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load():
    if _STATE["teams"] is None:
        try:
            _STATE["teams"] = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _STATE["teams"] = {}
    return _STATE["teams"]


def parse_igls(wikitext):
    """IGL ids (lowercased) in the page's active squad."""
    m = re.search(r"\{\{Squad\|status=active(.*?)\n\}\}", wikitext or "", re.S)
    if not m:
        return []
    out = []
    for person in re.findall(r"\{\{Person\|([^\n]*)", m.group(1)):
        if re.search(r"\|igl=y", person) and not re.search(r"\|role=(Coach|Analyst|Manager)", person, re.I):
            pid = re.search(r"\bid=([^|}]+)", person)
            if pid:
                out.append(pid.group(1).strip().lower())
    return out


def refresh(team_names):
    """Fetch teams not cached in the last week (one request, up to 50 teams). Never raises."""
    teams = _load()
    now = time.time()
    todo = [t for t in dict.fromkeys(team_names) if t and now - (teams.get(_norm(t)) or {}).get("at", 0) > TEAM_TTL][:50]
    if not todo or now - _STATE["last_fetch"] < MIN_GAP:
        return 0
    _STATE["last_fetch"] = now
    try:
        r = requests.get(API, params={"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                                      "redirects": 1, "format": "json", "formatversion": 2,
                                      "titles": "|".join(t.replace(" ", "_") for t in todo)},
                         headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=30)
        q = r.json().get("query") or {}
    except Exception:
        return 0
    # requested name -> final page title (through normalisation and redirects)
    hop = {x["from"]: x["to"] for x in (q.get("normalized") or []) + (q.get("redirects") or [])}
    pages = {p["title"]: p for p in q.get("pages") or []}
    with _LOCK:
        for t in todo:
            title = t.replace(" ", "_")
            for _ in range(3):
                title = hop.get(title, title)
            title = title.replace("_", " ")
            for _ in range(3):
                title = hop.get(title, title)
            p = pages.get(title) or {}
            text = ((((p.get("revisions") or [{}])[0].get("slots") or {}).get("main") or {}).get("content")) or ""
            teams[_norm(t)] = {"at": now, "page": title if text else None, "igl": parse_igls(text)}
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(teams), encoding="utf-8")
        except OSError:
            pass
    return len(todo)


def is_igl(nick, team_name):
    """True / False when the team's Liquipedia page was read, None when unknown."""
    t = _load().get(_norm(team_name))
    if not t or not t.get("page"):
        return None
    return _norm(nick) in {_norm(x) for x in t["igl"]}


def igl_role(nick, team_name):
    """(True / False / None, source with the page and when it was read)."""
    t = _load().get(_norm(team_name))
    if not t or not t.get("page"):
        return None, "unknown (no Liquipedia roster for this team yet)"
    when = time.strftime("%Y-%m-%d", time.gmtime(t["at"]))
    return _norm(nick) in {_norm(x) for x in t["igl"]}, f"Liquipedia {t['page']} roster, checked {when}"
