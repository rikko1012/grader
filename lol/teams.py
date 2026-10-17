"""Team identity across LoL sources (LoL Esports, Leaguepedia, Polymarket, PrizePicks, Underdog).

Exact identity only, never substring (the CS2 rule after audit C13): two names are the same team when they are equal
after normalising, equal after dropping generic words ("Team", "Esports", "Gaming"...), or listed in the same group of
lol/team_aliases.json (sponsor names, old names, a book's own abbreviation). An academy / challengers squad is another
team: "Karmine Corp Blue" never matches "Karmine Corp". A code (e.g. "BFX") matches only the team it belongs to
(matches(query, name, code))."""
import json
import re
import unicodedata
from functools import lru_cache

from . import config

# words that never tell two teams apart ("Team Liquid" = "Liquid", "FURIA Esports" = "FURIA"). Not "club" / "the" / "gg":
# "The Secret Club" is not "Team Secret" (2026-10-17: the two merged while "club" and "the" were on this list)
GENERIC = {"team", "esports", "esport", "gaming", "e", "sports"}
ALIASES = config.PKG_DIR / "team_aliases.json"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def core(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    words = re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).split()
    kept = [w for w in words if w not in GENERIC]
    return "".join(kept or words)


@lru_cache(maxsize=1)
def _groups():
    try:
        data = json.loads(ALIASES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index = {}
    for gid, names in enumerate(data.get("groups") or []):
        for n in names:
            for k in (norm(n), core(n)):
                if k:
                    index.setdefault(k, set()).add(gid)
    return index


def _gids(name):
    g = _groups()
    return g.get(norm(name), set()) | g.get(core(name), set())


def key(name):
    """One stable key per team for grouping history: its alias group when it has one (so a sponsor rename keeps one
    history), else its name without generic words."""
    g = _gids(name)
    return f"group{min(g)}" if g else (core(name) or norm(name))


def same_team(a, b):
    if not a or not b:
        return False
    if norm(a) == norm(b):
        return True
    ca, cb = core(a), core(b)
    if ca and ca == cb and len(ca) >= 2:
        return True
    return bool(_gids(a) & _gids(b))


def matches(query, name, code=None):
    """`query` (a name or abbreviation from any source) is the team called `name` with code `code`."""
    if same_team(query, name):
        return True
    return bool(code) and norm(query) == norm(code)
