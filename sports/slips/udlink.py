"""Underdog line links for Sports (PP_OPEN_LINK, 2026-10-29) - the same rule as the esports pplink.ud_link (sports does
not import CS2 modules; tests pin both to the same strings).

Underdog's OneLink with af_dp=underdogfantasy://market-details/<over_under_id> opens ONE line's market screen in the
Underdog app (owner-verified on a phone); af_web_dp lands a PC on that match's Underdog board. Underdog has no pre-fill:
the member taps Higher / Lower there. over_under_id = over_under_lines[].over_under_id (== over_under.id), never the line
id (our offer id is "ud:<line id>") or an option id; match_id / match_type = that line's appearance.

The ids come from the Underdog board body the worker already caches (sports.cache "lines:underdog" / league): no extra
request, no account, nothing new stored, no offer revision touched (an unchanged offer keeps its stored payload - the
current body still names its market). Parsed once per cached body (memo keyed by the body's saved time).
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

from .. import cache

UD_ONELINK = "https://underdogfantasy.onelink.me/vgwg?af_force_deeplink=true&af_dp="
UD_MARKET = "underdogfantasy://market-details/"
UD_FEATURED = "https://app.underdogsports.com/pick-em/higher-lower/all/featured"
UD_MATCH_TYPES = ("Game",)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MATCH = re.compile(r"^[0-9]{1,12}$")
BUCKET = "lines:underdog"


def ud_link(over_under_id, match_id=None, match_type=None):
    ou = over_under_id.strip().lower() if isinstance(over_under_id, str) else ""
    if not _UUID.fullmatch(ou):
        return None
    url = UD_ONELINK + quote(UD_MARKET + ou, safe="")
    m = str(match_id) if isinstance(match_id, int) and not isinstance(match_id, bool) else match_id if isinstance(match_id, str) else ""
    if _MATCH.fullmatch(m or "") and match_type in UD_MATCH_TYPES:
        url += "&af_web_dp=" + quote(f"{UD_FEATURED}?match_id={m}&match_type={match_type}", safe="")
    return url


def ids_of(body):
    """{"ud:<line id>": (over_under_id, match_id, match_type)} from one Underdog board body."""
    apps = {a.get("id"): a for a in (body or {}).get("appearances") or [] if isinstance(a, dict)}
    out = {}
    for ln in (body or {}).get("over_under_lines") or []:
        if not isinstance(ln, dict) or not ln.get("id"):
            continue
        ou = ln.get("over_under") or {}
        app = apps.get((ou.get("appearance_stat") or {}).get("appearance_id")) or {}
        out["ud:" + str(ln["id"])] = (ln.get("over_under_id") or ou.get("id"), app.get("match_id"), app.get("match_type"))
    return out


_MEMO: dict = {}                                 # league -> (saved at, {offer id: ids})
_LOCK = threading.Lock()


RECHECK_S = 2.0                                  # the saved body's time is read at most this often (700 checks: ~1 read)
_SEEN: dict = {}                                 # league -> monotonic time the saved time was last read


def ids_for(league):
    m = _MEMO.get(league)
    if m and time.monotonic() - _SEEN.get(league, -1e9) < RECHECK_S:
        return m[1]
    try:
        row = cache._con().execute("SELECT at FROM kv WHERE bucket = ? AND name = ?", (BUCKET, league)).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    m = _MEMO.get(league)
    if m and m[0] == row[0]:
        _SEEN[league] = time.monotonic()
        return m[1]
    with _LOCK:                                  # one parse per new body, however many members check at once
        m = _MEMO.get(league)
        if m and m[0] == row[0]:
            return m[1]
        try:
            ids = ids_of(cache.get(BUCKET, league) or {})
        except Exception:
            ids = {}
        _MEMO[league] = (row[0], ids)
        _SEEN[league] = time.monotonic()
        return ids


def link_for(league, offer_id):
    """The current Underdog board's link for one Sports Underdog offer ("ud:<line id>"), or None."""
    if not league or not isinstance(offer_id, str) or not offer_id.startswith("ud:"):
        return None
    t = ids_for(league).get(offer_id)
    return ud_link(*t) if t else None
