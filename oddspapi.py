#!/usr/bin/env python3
"""
oddspapi.py - bookmaker odds (Pinnacle first) for CS2 matches from OddsPapi's free tier.

Used when Polymarket has no pre-match market (or only a thin one): Pinnacle is the sharpest
esports book, so its de-vigged price is a real win chance. Key: ODDSPAPI_API_KEY (env only).
Requests are spaced 2 s apart and the fixture list is cached for 10 minutes.

Use:  python oddspapi.py Krytiepacani Omega
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://api.oddspapi.io/v4"
CS2_SPORT = 17               # OddsPapi sportId for Counter-Strike 2
MATCH_WINNER = "171"         # market id; outcome "171" = participant 1, "172" = participant 2
BOOKS = ("pinnacle", "betfair-ex", "polymarket", "bet365", "stake")   # first one with a price wins
MIN_GAP = 2.0
_LOCK, _LAST = threading.Lock(), [0.0]
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "cs2-scout/1.0 (https://github.com/rikko1012/cs2-scout)"})
_FIX = {"at": 0.0, "data": None}
_ODDS = {}
FIX_TTL = 600


def key():
    return os.environ.get("ODDSPAPI_API_KEY") or os.environ.get("odds_papi_api")   # Railway name


def _get(path, **params):
    """GET with the key added; errors never echo the URL (it contains the key)."""
    params["apiKey"] = key()
    with _LOCK:
        wait = _LAST[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = _SESSION.get(BASE + path, params=params, timeout=20)
        except requests.RequestException as e:
            raise RuntimeError(f"OddsPapi request failed ({type(e).__name__})") from None
        finally:
            _LAST[0] = time.time()
    if r.status_code != 200:
        raise RuntimeError(f"OddsPapi {path} -> HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def fixtures():
    """CS2 fixtures from yesterday to +4 days (cached 10 min)."""
    if _FIX["data"] is not None and time.time() - _FIX["at"] < FIX_TTL:
        return _FIX["data"]
    now = datetime.now(timezone.utc)
    data = _get("/fixtures", sportId=CS2_SPORT, **{"from": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                                                   "to": (now + timedelta(days=4)).strftime("%Y-%m-%d")})
    rows = data if isinstance(data, list) else (data.get("data") or data.get("fixtures") or [])
    _FIX.update(at=time.time(), data=rows)
    return rows


def _norm(s):
    words = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower()).split()
    core = [w for w in words if w not in {"team", "esports", "esport", "gaming", "club", "gg", "the"}]
    return " ".join(core or words)


def _same(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a) and bool(b) and (a == b or (len(a) >= 4 and len(b) >= 4 and (a in b.split() or b in a.split())))


def _started(fx):
    try:
        t = datetime.fromisoformat(str(fx.get("startTime")).replace("Z", "+00:00"))
        return t <= datetime.now(timezone.utc)
    except ValueError:
        return False


def _price(odds, book, outcome):
    try:
        o = odds["bookmakerOdds"][book]["markets"][MATCH_WINNER]["outcomes"][outcome]["players"]
        p = next(iter(o.values()))
        return float(p["price"]) if p.get("price") else None
    except (KeyError, TypeError, ValueError, StopIteration):
        return None


def prob_to_american(p):
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def match_odds(team, opponent=None):
    """Same shape as polymarket.match_odds: {'found', 'team', 'opponent', 'team_prob', 'team_ml', ...}.
    team may be None when only the opponent is known. Pre-match only. Never raises."""
    if not key():
        return {"found": False, "note": "No OddsPapi key"}
    try:
        best = None
        for fx in fixtures():
            if _started(fx):
                continue
            p1, p2 = fx.get("participant1Name"), fx.get("participant2Name")
            for ti, (mine, theirs) in enumerate(((p1, p2), (p2, p1))):
                ok_team = _same(team, mine) if team else True
                ok_opp = _same(opponent, theirs) if opponent else True
                if ok_team and ok_opp and (team or opponent):
                    if best is None or str(fx.get("startTime")) < str(best[0].get("startTime")):
                        best = (fx, ti, mine, theirs)
        if not best:
            return {"found": False, "note": "No OddsPapi fixture for this match"}
        fx, ti, mine, theirs = best
        hit = _ODDS.get(fx["fixtureId"])                 # limited free quota: 10-minute cache
        if hit and time.time() - hit[0] < FIX_TTL:
            odds = hit[1]
        else:
            odds = _get("/odds", fixtureId=fx["fixtureId"])
            _ODDS[fx["fixtureId"]] = (time.time(), odds)
        for book in BOOKS:
            d1, d2 = _price(odds, book, "171"), _price(odds, book, "172")
            if d1 and d2 and d1 > 1 and d2 > 1:
                i1, i2 = 1 / d1, 1 / d2
                p1 = i1 / (i1 + i2)                      # de-vigged
                tp = p1 if ti == 0 else 1 - p1
                return {"found": True, "team": mine, "opponent": theirs, "book": book,
                        "team_prob": round(tp, 3), "opp_prob": round(1 - tp, 3),
                        "team_ml": prob_to_american(tp), "opp_ml": prob_to_american(1 - tp),
                        "start": fx.get("startTime"), "title": f"{mine} vs {theirs} - {fx.get('tournamentName')}",
                        "source": f"{book} (OddsPapi, de-vigged)", "flags": [], "maps": {},
                        "decimal": [d1, d2] if ti == 0 else [d2, d1]}
        return {"found": False, "note": f"OddsPapi fixture found but no match-winner price ({mine} vs {theirs})"}
    except Exception as e:
        return {"found": False, "note": f"OddsPapi unavailable: {str(e)[:160]}"}


def probe():
    """Admin-only: shape of the live responses (no key in the output)."""
    out = {"key_set": bool(key())}
    try:
        fx = fixtures()
        out["fixtures"] = len(fx)
        out["sample"] = fx[:3]
        with_odds = next((f for f in fx if not _started(f) and f.get("hasOdds", True)), None)
        if with_odds:
            o = _get("/odds", fixtureId=with_odds["fixtureId"])
            bm = o.get("bookmakerOdds") or {}
            out["odds_books"] = sorted(bm)[:30]
            out["odds_sample_pinnacle"] = json.dumps(bm.get("pinnacle"))[:1500] if "pinnacle" in bm else None
            out["odds_top_keys"] = sorted(o)[:30]
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


if __name__ == "__main__":
    print(json.dumps(match_odds(sys.argv[1] if len(sys.argv) > 1 else None,
                                sys.argv[2] if len(sys.argv) > 2 else None), indent=2))
