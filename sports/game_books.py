"""DraftKings full-game prices via the existing ESPN collector. No new upstream requests."""
from __future__ import annotations
import json
import math
import os
from datetime import datetime, timezone
from . import config


def path(league):
    if league not in ("nba", "nfl"):
        raise ValueError("unsupported league")
    return config.DATA_DIR / "snapshots" / "game_books" / (league + ".json")


def number(v, price=False):
    if isinstance(v, bool): return None
    try: n = float(v)
    except (TypeError, ValueError): return None
    if not math.isfinite(n) or (price and not 100 <= abs(n) <= 100000): return None
    return n


def parse_summary(body):
    """Explicit home/away, Over/Under and current (`close`) values only. Never infer a side."""
    header = body.get("header") or {}
    league = (header.get("league") or {}).get("slug")
    if league not in ("nba", "nfl"): return None
    comps = header.get("competitions") or []
    if len(comps) != 1: return None
    comp = comps[0]
    if str(comp.get("id")) != str(header.get("id")): return None
    if (comp.get("status") or {}).get("type", {}).get("state") != "pre": return None
    teams = {t.get("homeAway"): t.get("team") or {} for t in comp.get("competitors", [])}
    if set(teams) != {"home", "away"}: return None
    if not all(t.get("displayName") and t.get("id") for t in teams.values()): return None
    if teams["home"]["id"] == teams["away"]["id"]: return None
    try:
        start = datetime.fromisoformat(comp["date"].replace("Z", "+00:00"))
        if start.tzinfo is None: return None
    except (ValueError, KeyError): return None
    books = {}
    for pc in body.get("pickcenter") or []:
        provider = pc.get("provider") or {}
        if str(provider.get("id")) != "100" or provider.get("name") != "DraftKings": continue
        # Reject crossed team identities when ESPN supplies them.
        if any(str((pc.get(side+"TeamOdds") or {}).get("teamId", teams[side]["id"])) != str(teams[side]["id"]) for side in teams): continue
        markets = {}
        for kind, field, sides in (("money-line", "moneyline", ("home", "away")), ("spread", "pointSpread", ("home", "away")), ("totals", "total", ("over", "under"))):
            val = {}
            for side in sides:
                close = ((pc.get(field) or {}).get(side) or {}).get("close") or {}
                odds = number(close.get("odds"), True)
                if odds is None: continue
                if kind != "money-line":
                    raw = str(close.get("line", ""))
                    if kind == "totals" and raw[:1].lower() in ("o", "u"):
                        if raw[0].lower() != side[0]: continue
                        raw = raw[1:]
                    line = number(raw)
                    if line is None: continue
                    if kind == "totals":
                        if "total" in val and val["total"] != line: return None
                        val["total"] = line
                    else: val[side+"Spread"] = line
                val[side+"Odds"] = odds
            if val: markets[kind] = val
        if markets: books["draftkings"] = {"id": "draftkings", "label": "DraftKings", "markets": markets}
    if not books: return None
    return {"id": "espn:"+str(header["id"]), "league": league, "home": teams["home"]["displayName"], "away": teams["away"]["displayName"], "start_utc": start.astimezone(timezone.utc).isoformat(), "books": books}


def refresh(db, league, now=None):
    """Publish only current non-late revisions; keep each quote's last-seen time, never restamp old odds."""
    from .games import store
    now = now or datetime.now(timezone.utc)
    try:
        rows = db.all("SELECT s.data, st.last_seen_at FROM sports_game_line_state st JOIN sports_game_snapshots s ON s.id=st.last_id WHERE st.tbl='sports_game_snapshots' AND s.league=? AND s.kind='book_game_prices' AND s.source='espn' AND s.late=0 ORDER BY st.last_seen_at DESC LIMIT 500", (league,))
        events = []
        for r in rows:
            e = json.loads(r["data"])
            if e.get("league") != league: continue
            if datetime.fromisoformat(e["start_utc"].replace("Z", "+00:00")) <= now: continue
            e["seen_at"] = r["last_seen_at"]
            e["league"] = league
            events.append(e)
        doc = {"league": league, "source": "ESPN", "source_time_unknown": True, "observed_at": now.isoformat(), "events": sorted(events, key=lambda e:e["start_utc"])}
        target = path(league);target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp");tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8");os.replace(tmp,target)
        return {"events": len(events)}
    except Exception as e:
        return {"error": type(e).__name__}


def view(req):
    from . import api_board
    league = req.query.get("league")
    if league not in ("nba", "nfl"): return 400, {"error": "league must be nba or nfl"}
    return api_board._serve(path(league))
