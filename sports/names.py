"""Player names across sources (PrizePicks, Underdog, Pinnacle, The Odds API, ESPN).

key("C.J. Stroud") == key("CJ Stroud") == "cj stroud"; key("Trey Murphy III") == "trey murphy";
key("Karl-Anthony Towns") == "karl anthony towns"; accents are dropped ("Nikola Jokić" -> "nikola jokic").
A name is only ever matched whole - never by substring - and every match also needs the same market and the same game
start (edges.py), so two players with the same name in different games never merge. A spelling two sources disagree on
(e.g. "Nic" / "Nicolas") goes in player_aliases.json as one group; nothing is guessed.
"""
import json
import re
import unicodedata
from pathlib import Path

_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}
_ALIASES = Path(__file__).resolve().parent / "player_aliases.json"
_GROUPS = None


def _base(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[.'`]", "", s)                    # C.J. -> cj, D'Angelo -> dangelo
    s = re.sub(r"[^a-z0-9]+", " ", s)               # hyphens, commas -> spaces
    parts = [p for p in s.split() if p]
    while len(parts) > 2 and parts[-1] in _SUFFIX:
        parts.pop()
    return " ".join(parts)


def _groups():
    global _GROUPS
    if _GROUPS is None:
        _GROUPS = {}
        try:
            data = json.loads(_ALIASES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for group in data.get("groups", []):
            names = [_base(n) for n in group]
            for n in names:
                _GROUPS[n] = names[0]
    return _GROUPS


def key(name):
    b = _base(name)
    return _groups().get(b, b)


# One team abbreviation per team on every member surface (owner re-audit N9, 2026-10-24: Slate "UTAH" vs slips "UTA").
# ESPN's own short codes -> the codes the pick'em boards print. web/sports/labels.js TEAM_ABBR is the same table
# (sports/tests/test_ui_uifix.py checks they match). Logos keep ESPN's code (their URL uses it).
TEAM_ABBR = {
    "nba": {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"},
    "nfl": {"WSH": "WAS"},
}


def team_abbr(league, abbr):
    """'UTAH' -> 'UTA' (nba); unknown / already canonical codes are returned upper-cased; None stays None."""
    if not abbr:
        return None
    a = str(abbr).strip().upper()
    return TEAM_ABBR.get(league, {}).get(a, a)
