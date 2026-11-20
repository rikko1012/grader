"""PrizePicks player pictures for soccer / tennis cards (v3, owner 2026-11-20). Display only.

The PrizePicks answer the collector already reads carries new_player.image_url. Many are not headshots (shared team / jersey
art, placeholders, other hosts), so only a URL that is
  - https on static.prizepicks.com,
  - free of the known placeholder marks, and
  - used by exactly one player id in that answer
is kept, keyed by the board's exact PrizePicks player id ("soccer:prizepicks:<id>"). The map lives in images.json beside
board.json (never inside the offers: an offer's revision hash and the board size stay exactly as they were) and goes out with
the research=1 answer as "images", limited to the players on that board.
"""
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

FILE = "images.json"
HOST = "static.prizepicks.com"
PLACEHOLDER_MARKS = ("placeholder.png", "player_avatar.png", "default_headshot", "/images/teams/", "jersey")
KEEP = 8000
_LOCK = threading.Lock()


def from_body(body, sport):
    """{"<sport>:prizepicks:<player id>": url} for the real, player-specific pictures in one PrizePicks answer."""
    if sport not in ("soccer", "tennis") or not isinstance(body, dict):
        return {}
    pairs = []
    for x in body.get("included") or []:
        if not isinstance(x, dict) or x.get("type") != "new_player" or x.get("id") is None:
            continue
        a = x.get("attributes") or {}
        url = a.get("image_url")
        if a.get("combo") or not isinstance(url, str) or len(url) > 500:
            continue
        try:
            s = urlsplit(url.strip())
        except ValueError:
            continue
        if s.scheme != "https" or s.hostname != HOST or any(m in url.lower() for m in PLACEHOLDER_MARKS):
            continue
        pairs.append((str(x["id"]), url.strip()))
    owners = {}
    for pid, url in pairs:
        owners.setdefault(url, set()).add(pid)
    return {f"{sport}:prizepicks:{pid}": url for pid, url in pairs if len(owners[url]) == 1}


def remember(body, sport, directory):
    """Merge one answer's pictures into <directory>/images.json (atomic, bounded). Never raises: pictures are optional."""
    try:
        found = from_body(body, sport)
        if not found:
            return 0
        path = Path(directory) / FILE
        with _LOCK:
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                old = old if isinstance(old, dict) else {}
            except (OSError, ValueError):
                old = {}
            if all(old.get(k) == v for k, v in found.items()):
                return 0                               # nothing new: the file (and the research body's cache key) stays
            for k in found:
                old.pop(k, None)                       # newest last: the oldest entries are the ones dropped
            old.update(found)
            if len(old) > KEEP:
                old = dict(list(old.items())[-KEEP:])
            tmp = path.with_name(f"{FILE}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(old, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        return len(found)
    except Exception:                                  # noqa: BLE001
        return 0


def for_board(directory, offers):
    """The saved pictures of the players on this board only ({} when there is no file)."""
    try:
        saved = json.loads((Path(directory) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    ids = {o.get("player_id") for o in offers or [] if isinstance(o, dict)}
    return {k: v for k, v in saved.items() if k in ids and isinstance(v, str)}
