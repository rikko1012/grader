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


# ---------------------------------------------------------------- Underdog: one line's own market screen (2026-10-29)
# OWNER-VERIFIED 2026-10-29 on a phone: Underdog's OneLink with af_dp=underdogfantasy://market-details/<over_under_id>
# opens the Underdog app on that exact line's market screen (XANTARES kills). On a PC the link lands on the match's
# pick'em board (af_web_dp = app.underdogsports.com ...featured?match_id=&match_type=, verified on the web); the
# market-details route has no web page. Underdog has NO pre-fill: the member still taps Higher / Lower and submits, so
# the member-facing word is "Open in Underdog" / "Open", never "pre-filled". over_under_id is the board's
# over_under_lines[].over_under_id (== over_under.id) - never the line id or an option id; match_id / match_type are
# that line's appearances[] values. Built only from Underdog's public board rows: no account, no private endpoint.
UD_ONELINK = "https://underdogfantasy.onelink.me/vgwg?af_force_deeplink=true&af_dp="
UD_MARKET = "underdogfantasy://market-details/"
UD_FEATURED = "https://app.underdogsports.com/pick-em/higher-lower/all/featured"
UD_MATCH_TYPES = ("Game",)                     # the only value seen (CS, LoL, NBA): anything else drops af_web_dp only
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MATCH = re.compile(r"^[0-9]{1,12}$")


def uuid_or_none(x):
    """A canonical (lower-case) uuid string, else None (never guessed, never trimmed into shape)."""
    s = str(x).strip().lower() if isinstance(x, str) else ""
    return s if _UUID.fullmatch(s) else None


def ud_web_link(match_id, match_type):
    """The PC landing: that match's Underdog pick'em board, or None (unknown match id / type)."""
    m = str(match_id) if isinstance(match_id, int) and not isinstance(match_id, bool) else match_id if isinstance(match_id, str) else ""
    if not _MATCH.fullmatch(m or "") or match_type not in UD_MATCH_TYPES:
        return None
    return f"{UD_FEATURED}?match_id={m}&match_type={match_type}"


def ud_link(over_under_id, match_id=None, match_type=None):
    """Underdog's OneLink to one line's market screen (app), landing on the match board on a PC when the match is known.
    None when over_under_id is not a uuid (no link is better than a wrong market)."""
    ou = uuid_or_none(over_under_id)
    if ou is None:
        return None
    url = UD_ONELINK + quote(UD_MARKET + ou, safe="")
    web = ud_web_link(match_id, match_type)
    return url + "&af_web_dp=" + quote(web, safe="") if web else url


_UD_LINK_RE = re.compile(r"^" + re.escape(UD_ONELINK) + re.escape(quote(UD_MARKET, safe="")) + r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                         r"(?:&af_web_dp=" + re.escape(quote(UD_FEATURED + "?match_id=", safe="")) + r"[0-9]{1,12}"
                         + re.escape(quote("&match_type=", safe="")) + r"(?:" + "|".join(UD_MATCH_TYPES) + r"))?$")


def ud_link_ok(url):
    """Exactly a link ud_link builds (the page re-checks the same pattern before it draws one)."""
    return isinstance(url, str) and bool(_UD_LINK_RE.fullmatch(url))


# ---------------------------------------------------------------- owner tail links (an Underdog entry share link)
_TAIL_HOSTS = ("play.underdogsports.com", "underdogfantasy.onelink.me", "app.underdogsports.com")
TAIL_MAX = 600
_TAIL_BAD = frozenset('"<>`' + chr(92))


def tail_share_id(url):
    """An owner-pasted Underdog share link -> (canonical url, share id) or (None, reason). Strict: https, one of three
    hosts, no user / port / fragment, <= TAIL_MAX characters, and the entry slip's share id is a uuid:
      underdogfantasy.onelink.me/<code>?...deep_link_value=entry_slip&deep_link_sub1=<uuid>...
      app.underdogsports.com/...?entrySlipShareId=<uuid>
      play.underdogsports.com/... with either of those, or the uuid as the last path segment."""
    from urllib.parse import urlsplit, parse_qs
    if not isinstance(url, str):
        return None, "Paste the share link"
    url = url.strip()
    if not url or len(url) > TAIL_MAX or any(c.isspace() or ord(c) < 33 or c in _TAIL_BAD for c in url):
        return None, "That is not an Underdog share link"
    try:
        p = urlsplit(url)
    except ValueError:
        return None, "That is not an Underdog share link"
    if p.scheme != "https" or p.hostname not in _TAIL_HOSTS or p.netloc != p.hostname or p.fragment:
        return None, "Only Underdog share links (play.underdogsports.com, underdogfantasy.onelink.me, app.underdogsports.com)"
    try:
        q = parse_qs(p.query, keep_blank_values=True, strict_parsing=bool(p.query), max_num_fields=20)
    except ValueError:
        return None, "That is not an Underdog share link"
    one = lambda k: (q.get(k) or [None])[0] if len(q.get(k) or []) <= 1 else None
    sid = None
    if one("deep_link_value") == "entry_slip":
        sid = uuid_or_none(one("deep_link_sub1"))
    if sid is None and p.hostname != "underdogfantasy.onelink.me":
        sid = uuid_or_none(one("entrySlipShareId"))
    if sid is None and p.hostname == "play.underdogsports.com":
        seg = [s for s in p.path.split("/") if s]
        sid = uuid_or_none(seg[-1]) if seg else None
    if sid is None:
        return None, "The link has no entry slip share id"
    if p.hostname == "underdogfantasy.onelink.me" and not re.fullmatch(r"/[A-Za-z0-9]{2,12}", p.path or ""):
        return None, "That is not an Underdog share link"
    return url, sid
