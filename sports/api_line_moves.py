"""GET /api/sports/offers/moves?keys=[[platform, offer_id], ...] (route line in sports/web.py ROUTES).

The opening and closing line of up to line_moves.KEYS_MAX stored offers, for the settled-pick note on the esports workspace's
My picks (SCOUT_LINE_MOVE_NOTES, wording only - sports/line_moves.py). Read-only, bounded, cached per process. Always a 200
with what is known: an offer over the request budget is simply left out (a 503 would pause every sports read on the caller).
-> {"moves": [[platform, offer_id, opening, closing], ...]}
"""
from __future__ import annotations

import json

from . import line_moves, web
from .slips import rules


def _keys(q):
    keys = q.get("keys")
    keys = json.loads(keys) if isinstance(keys, str) else keys
    if not isinstance(keys, list) or len(keys) > line_moves.KEYS_MAX:
        raise ValueError(f"keys: a list of at most {line_moves.KEYS_MAX} [platform, offer_id] pairs")
    out = []
    for k in keys:
        if not (isinstance(k, list) and len(k) == 2 and k[0] in rules.PLATFORMS and isinstance(k[1], str) and 0 < len(k[1]) <= 120):
            raise ValueError("each key is [platform, offer_id]")
        out.append((k[0], k[1]))
    return out


def view(req):
    try:
        keys = _keys(req.query or {})
    except (ValueError, TypeError) as e:
        return 400, {"error": "bad_request", "detail": str(e)[:160]}
    if not keys:
        return 200, {"moves": []}
    got = line_moves.offer_moves(keys, web.store_ro)
    return 200, {"moves": [[p, o, oc[0], oc[1]] for (p, o), oc in got.items()]}
