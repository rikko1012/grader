#!/usr/bin/env python3
"""
liquipedia.py - backup source for a player's CURRENT team when BO3.gg has none (or no match).

Uses Liquipedia's free MediaWiki API under its terms (liquipedia.net/api-terms-of-use):
identifying User-Agent, gzip, one reused connection, at most 1 request per 2 seconds, no
action=parse (that one is limited to 1 per 30 s - we read page wikitext instead), and
answers cached for 12 hours. Content is CC-BY-SA 3.0 - cards credit Liquipedia.

Use:  python liquipedia.py ct0m
"""
import json
import os
import re
import sys
import threading
import time

import requests

API = "https://liquipedia.net/counterstrike/api.php"
UA = "cs2-scout/1.0 (CS2 prop grader; https://github.com/rikko1012/cs2-scout)"
MIN_GAP = 2.1            # seconds between requests (terms: max 1 per 2 s)
TTL = 12 * 3600
_LOCK = threading.Lock()
_LAST = [0.0]
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip"})
_CACHE_FILE = os.path.join(os.environ.get("CS2_DATA_DIR") or os.path.dirname(os.path.abspath(__file__)),
                           ".cache", "liquipedia.json")
_MEM = None


def _cache():
    global _MEM
    if _MEM is None:
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                _MEM = json.load(f)
        except (OSError, ValueError):
            _MEM = {}
    return _MEM


def _save():
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_MEM, f)
        os.replace(tmp, _CACHE_FILE)
    except OSError:
        pass                       # the cache is a convenience - never fatal


def _wikitext(title):
    with _LOCK:                    # one request at a time, 2.1 s apart, across all threads
        wait = _LAST[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = _SESSION.get(API, timeout=15, params={
                "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                "format": "json", "redirects": 1, "titles": title})
        finally:
            _LAST[0] = time.time()
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    for p in pages.values():
        revs = p.get("revisions") or []
        if revs:
            return (revs[0].get("slots") or {}).get("main", {}).get("*")
    return None


def _field(text, name):
    m = re.search(rf"^\|[ \t]*{name}[ \t]*=[ \t]*(.*?)[ \t]*$", text, re.M | re.I)
    if not m:
        return None
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", m.group(1))   # [[Page|Name]] -> Name
    v = re.sub(r"<[^>]+>|\{\{[^}]*\}\}", "", v).strip()
    return v or None


def player_team(nick):
    """{'team': 'Omega', 'loan': None, 'source': 'Liquipedia'} or None. Never raises."""
    key = nick.lower()
    c = _cache()
    hit = c.get(key)
    if hit and time.time() - hit.get("at", 0) < TTL:
        return hit.get("data")
    data = None
    try:
        title = nick[:1].upper() + nick[1:]            # wiki titles start upper-case
        text = _wikitext(title)
        if text and re.search(r"\{\{\s*Infobox player", text, re.I):
            team = _field(text, "team")
            if team:
                data = {"team": team, "loan": _field(text, "team2"), "source": "Liquipedia",
                        "url": f"https://liquipedia.net/counterstrike/{title}"}
    except Exception:
        return None                                    # network trouble: don't cache, don't fail
    c[key] = {"at": time.time(), "data": data}
    _save()
    return data


if __name__ == "__main__":
    print(json.dumps(player_team(sys.argv[1] if len(sys.argv) > 1 else "ct0m"), indent=2))
