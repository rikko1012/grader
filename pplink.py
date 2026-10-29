"""PrizePicks pre-filled lineup link + the Underdog pick'em home link (2026-10-28, owner-verified on the web).

PrizePicks' public web board reads ?projections=<projectionId>-<o|u>-<line_score>,... and loads those picks into the
member's own lineup builder ("Lineup copied!"); the member submits the entry themselves. The projection id decides the
pick (the line is informational: a moved line opens PrizePicks' own "lines changed" drawer). The side token MUST be
exactly "o" or "u" (anything else is read as an under), PrizePicks forces goblin / demon picks to Over and caps an entry
at 6 picks. Never an invite_code or wager_id, never an account, never a private endpoint: the link is built only from
the board's own rows.

Underdog has no pre-fill link (only server-created share ids): an Underdog slip gets its copy text + UD_HOME, and never a
claim that the picks are pre-filled.

PP_OPEN_LINK=1 turns the feature on (default OFF until the owner's app test). This module is pure: no I/O, no network.
The server's slip check (server.slip_check / studio verify) decides first; this only spells the link of a checked slip.
"""
import math
import os
import re
from urllib.parse import quote

PP_BOARD = "https://app.prizepicks.com/board"
UD_HOME = "https://app.underdogsports.com/pick-em/higher-lower/all/home"
PP_MAX = 6                       # PrizePicks' entry cap
PP_MIN = 2
_ID = re.compile(r"^[0-9]{1,15}$")


def enabled(env=None):
    """PP_OPEN_LINK=1 (exactly) turns the links on; unset / anything else = off (the pages are unchanged)."""
    return (os.environ if env is None else env).get("PP_OPEN_LINK", "").strip() == "1"


def projection_id(row_id):
    """'pp:16360820' (a board row id) or '16360820' -> '16360820'; anything else -> None."""
    s = str(row_id or "")
    if s.startswith("pp:"):
        s = s[3:]
    return s if _ID.match(s) else None


def side_token(side):
    """More / Over -> 'o', Less / Under -> 'u'; anything else -> None (never guessed)."""
    return {"More": "o", "Over": "o", "over": "o", "Less": "u", "Under": "u", "under": "u"}.get(side)


def line_token(line):
    """The feed's line_score as PrizePicks writes it: 31.5 -> '31.5', 30.0 -> '30'. None for a missing / negative /
    non-finite line (a '-' inside a token would break PrizePicks' split)."""
    try:
        x = float(line)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x < 0:
        return None
    return str(int(x)) if x.is_integer() else repr(x)


def pp_link(legs):
    """legs: [{"projection_id" (or "row_id" 'pp:<id>'), "side" More/Less, "line", "odds_type" standard/promo/goblin/demon,
    "book" (optional, must be prizepicks when given)}] -> (url, None) or (None, member-facing reason).
    Refuses: not 2-6 picks, a non-PrizePicks leg, a missing / non-numeric projection id, a side that is not exactly
    More / Less, a goblin / demon Less (PrizePicks would flip it to More), a bad line, the same projection twice."""
    if not isinstance(legs, (list, tuple)) or not PP_MIN <= len(legs) <= PP_MAX:
        return None, f"PrizePicks lineups take {PP_MIN}-{PP_MAX} picks"
    toks, seen = [], set()
    for l in legs:
        if not isinstance(l, dict):
            return None, "This slip can't be opened in PrizePicks"
        if l.get("book") not in (None, "prizepicks"):
            return None, "Only PrizePicks picks open in PrizePicks"
        pid = projection_id(l.get("projection_id") if l.get("projection_id") is not None else l.get("row_id"))
        if pid is None:
            return None, "PrizePicks' id for a pick on this slip is not known - copy the slip instead"
        if pid in seen:
            return None, "The same PrizePicks pick is on this slip twice"
        seen.add(pid)
        sd = side_token(l.get("side"))
        if sd is None:
            return None, "A pick on this slip has no side"
        if str(l.get("odds_type") or "standard").lower() in ("goblin", "demon") and sd != "o":
            return None, "Goblin and demon picks are More only on PrizePicks"
        ln = line_token(l.get("line"))
        if ln is None:
            return None, "A pick on this slip has no current line"
        toks.append(f"{pid}-{sd}-{ln}")
    return PP_BOARD + "?projections=" + quote(",".join(toks), safe="-,."), None
