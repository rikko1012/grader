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
FAIL_RETRY_S = 30            # a failed request is not repeated sooner than this (2026-10-13 audit: never cached as an answer)
_FAIL = {}                   # request key -> (retry not before, error) - fixtures / odds per fixture
_FLIGHT, _FLIGHT_LOCK = {}, threading.Lock()


def key():
    return os.environ.get("ODDSPAPI_API_KEY") or os.environ.get("odds_papi_api")   # Railway name


def _get(path, **params):
    """GET with the key added; errors never echo the URL (it contains the key) nor the answer body.
    2026-10-13 audit: only the MIN_GAP slot is taken under the lock - the request itself runs outside it (a member's
    lookup never waits behind every other member's 20 s request)."""
    params["apiKey"] = key()
    with _LOCK:
        slot = max(time.time(), _LAST[0] + MIN_GAP)
        _LAST[0] = slot
    wait = slot - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        r = _SESSION.get(BASE + path, params=params, timeout=20)
    except requests.RequestException as e:
        raise RuntimeError(f"OddsPapi request failed ({type(e).__name__})") from None
    if r.status_code != 200:
        raise RuntimeError(f"OddsPapi {path} -> HTTP {r.status_code}")
    return r.json()


_BUSY = object()


def _once(k, fn, wait=True):
    """fn() for request key k with ONE request in flight per key: the others wait for its answer (wait=False: _BUSY at
    once - the caller has an earlier copy) and never send their own; a failure is remembered for FAIL_RETRY_S (callers in
    that window fail at once - no request; the failure is never kept as an answer)."""
    with _FLIGHT_LOCK:
        if len(_FLIGHT) > 2000:
            _FLIGHT.clear()
        lk = _FLIGHT.setdefault(k, threading.Lock())
    if not lk.acquire(blocking=wait):
        return _BUSY
    try:
        f = _FAIL.get(k)
        if f and time.time() < f[0]:
            raise RuntimeError(f"{f[1]} (retried within {FAIL_RETRY_S} s)")
        try:
            out = fn()
        except Exception as e:
            if len(_FAIL) > 2000:
                _FAIL.clear()
            _FAIL[k] = (time.time() + FAIL_RETRY_S, str(e)[:120] or type(e).__name__)
            raise
        _FAIL.pop(k, None)
        return out
    finally:
        lk.release()


def fixtures():
    """CS2 fixtures from yesterday to +4 days (cached 10 min). One request at a time - while it runs, a caller with the
    last list gets that list at once; a failed read keeps the last list (its retrieval time unchanged) and is retried no
    sooner than FAIL_RETRY_S."""
    def fresh():
        return _FIX["data"] is not None and time.time() - _FIX["at"] < FIX_TTL

    def load():
        if fresh():                     # read by the request we waited for
            return _FIX["data"]
        now = datetime.now(timezone.utc)
        data = _get("/fixtures", sportId=CS2_SPORT, **{"from": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                                                       "to": (now + timedelta(days=4)).strftime("%Y-%m-%d")})
        rows = data if isinstance(data, list) else (data.get("data") or data.get("fixtures") or [])
        _FIX.update(at=time.time(), data=rows)
        return rows
    if fresh():
        return _FIX["data"]
    have = _FIX["data"] is not None
    try:
        got = _once("fixtures", load, wait=not have)
    except Exception:
        if _FIX["data"] is not None:    # OddsPapi hiccup: the last list (fixtures, not prices)
            return _FIX["data"]
        raise
    return _FIX["data"] if got is _BUSY else got


def _same(a, b):
    """One team (2026-10-13 audit C13): matchid's exact identity - never one-word containment ('Vitality' is not
    'Vitality Academy')."""
    try:
        import matchid
        return matchid.same_team(a, b)
    except Exception:                   # noqa: BLE001
        return False


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


def match_odds(team, opponent=None, cache_only=False, max_age=None, start=None):
    """Same shape as polymarket.match_odds: {'found', 'team', 'opponent', 'team_prob', 'team_ml', ...}.
    team may be None when only the opponent is known. Pre-match only. Never raises.
    cache_only=True (market_odds.select for the board / background grades and every re-check, 2026-10-13): never a
    request - only the fixture list and prices a member's grade already fetched, at most max_age s old (default FIX_TTL).
    Teams by matchid's identity (never containment), the price oriented by the team; start (the graded match's start):
    only a fixture within 3 h of it, the nearest (a rematch is another match); no start: the earliest.
    The answer carries fixture_id and retrieved_at (our fetch time of the prices)."""
    if not key():
        return {"found": False, "note": "No OddsPapi key"}
    age_max = FIX_TTL if max_age is None else max_age
    from polymarket import MATCH_WINDOW_H, when
    want = when(start) if start else None
    try:
        best = None
        if cache_only:
            if _FIX["data"] is None or time.time() - _FIX["at"] > age_max:
                return {"found": False, "note": "OddsPapi: no recent fixture list in memory (cache-only)"}
            fx_list = _FIX["data"]
        else:
            fx_list = fixtures()
        for fx in fx_list:
            if _started(fx):
                continue
            p1, p2 = fx.get("participant1Name"), fx.get("participant2Name")
            for ti, (mine, theirs) in enumerate(((p1, p2), (p2, p1))):
                ok_team = _same(team, mine) if team else True
                ok_opp = _same(opponent, theirs) if opponent else True
                if ok_team and ok_opp and (team or opponent):
                    if want is not None:
                        st = when(fx.get("startTime"))
                        gap = abs((st - want).total_seconds()) if st is not None else None
                        if gap is None or gap > MATCH_WINDOW_H * 3600:
                            continue
                        k = (gap, str(fx.get("startTime")))
                    else:
                        k = (0, str(fx.get("startTime")))
                    if best is None or k < best[0]:
                        best = (k, fx, ti, mine, theirs)
        if not best:
            return {"found": False, "note": "No OddsPapi fixture for this match"}
        _, fx, ti, mine, theirs = best
        hit = _ODDS.get(fx["fixtureId"])                 # limited free quota: 10-minute cache
        if hit and time.time() - hit[0] < (age_max if cache_only else FIX_TTL):
            odds, got_at = hit[1], hit[0]
        elif cache_only:
            return {"found": False, "note": f"OddsPapi: no recent prices in memory for {mine} vs {theirs} (cache-only)"}
        else:
            def load(fid=fx["fixtureId"]):
                h = _ODDS.get(fid)
                if h and time.time() - h[0] < FIX_TTL:   # fetched by the request we waited for
                    return h
                h = _ODDS[fid] = (time.time(), _get("/odds", fixtureId=fid))
                return h
            got_at, odds = _once(("odds", fx["fixtureId"]), load)
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
                        "decimal": [d1, d2] if ti == 0 else [d2, d1], "fixture_id": fx.get("fixtureId"),
                        "retrieved_at": datetime.fromtimestamp(got_at, timezone.utc).isoformat(timespec="seconds"),
                        "cache_only": bool(cache_only)}
        return {"found": False, "note": f"OddsPapi fixture found but no match-winner price ({mine} vs {theirs})"}
    except Exception as e:
        return {"found": False, "note": f"OddsPapi unavailable: {str(e)[:160]}"}


def markets_inventory(n=3):
    """Admin-only, read-only (2026-10-09 source inventory): which markets each book carries for upcoming CS2 fixtures -
    market ids, names (from /markets when available), outcome counts and whether outcomes are per-player (props)."""
    out = {"key_set": bool(key())}
    try:
        names = {}
        try:
            mk = _get("/markets", sportId=CS2_SPORT)
            for m in (mk if isinstance(mk, list) else (mk.get("data") or mk.get("markets") or [])):
                names[str(m.get("marketId") or m.get("id"))] = {k: m.get(k) for k in ("marketName", "marketType", "period", "handicap", "playerProp")
                                                              if m.get(k) is not None}
        except Exception as e:
            out["markets_error"] = str(e)[:200]
        out["market_defs"] = len(names)
        up = [f for f in fixtures() if not _started(f) and f.get("hasOdds")][:n]
        out["fixtures"] = []
        for fx in up:
            hit = _ODDS.get(fx["fixtureId"])
            odds = hit[1] if hit and time.time() - hit[0] < FIX_TTL else _get("/odds", fixtureId=fx["fixtureId"])
            _ODDS[fx["fixtureId"]] = (time.time(), odds)
            books = {}
            for book, bd in (odds.get("bookmakerOdds") or {}).items():
                ms = {}
                for mid, m in (bd.get("markets") or {}).items():
                    outs = m.get("outcomes") or {}
                    players = sum(len((o or {}).get("players") or {}) for o in outs.values())
                    ms[mid] = {"name": names.get(str(mid)), "outcomes": len(outs), "player_entries": players}
                books[book] = ms
            out["fixtures"].append({"match": f"{fx.get('participant1Name')} vs {fx.get('participant2Name')}", "start": fx.get("startTime"),
                                    "books": books})
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


def probe():
    """Admin-only: shape of the live responses (no key in the output)."""
    out = {"key_set": bool(key())}
    try:
        fx = fixtures()
        out["fixtures"] = len(fx)
        out["sample"] = fx[:3]
        up = [f for f in fx if not _started(f) and f.get("hasOdds")]
        out["upcoming_with_odds"] = len(up)
        out["lookups"] = []
        for f in up[:3]:          # the real lookup members' grades use
            r = match_odds(f.get("participant1Name"), f.get("participant2Name"))
            out["lookups"].append({k: r.get(k) for k in ("found", "team", "opponent", "book", "team_prob", "note")})
        if up:
            bm = (_ODDS.get(up[0]["fixtureId"]) or (0, {}))[1].get("bookmakerOdds") or {}
            out["has_pinnacle"] = "pinnacle" in bm
            out["books_count"] = len(bm)
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


if __name__ == "__main__":
    print(json.dumps(match_odds(sys.argv[1] if len(sys.argv) > 1 else None,
                                sys.argv[2] if len(sys.argv) > 2 else None), indent=2))
