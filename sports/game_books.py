"""DraftKings full-game prices via the existing ESPN collector. No new upstream requests."""
from __future__ import annotations
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
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


def matchup_signals(body, comp, teams):
    """Signed away-minus-home comparisons from this pre-game summary only."""
    signals = {}
    pred = body.get("predictor") or {}
    sides = [pred.get(side + "Team") for side in ("away", "home")] if isinstance(pred, dict) else []
    if sides and all(isinstance(p, dict) and str(p.get("id")) == str(teams[s]["id"]) for p, s in zip(sides, ("away", "home"))):
        a, h = [number(p.get("gameProjection")) for p in sides]
        if all(v is not None and 0 <= v <= 100 for v in (a, h)) and 90 <= a + h <= 100.1:
            signals["ESPN matchup projection"] = (a - h) / 100
    records = {}
    for competitor in comp["competitors"]:
        for r in competitor.get("record") or []:
            if not isinstance(r, dict):
                continue
            match = re.fullmatch(r"(\d+)-(\d+)(?:-(\d+))?", str(r.get("summary", "")))
            if match:
                w, l, t = [int(v or 0) for v in match.groups()]
                if w + l + t:
                    records[competitor["homeAway"], r.get("type")] = (w + t/2 + 2) / (w+l+t+4)
    pairs = [("Season record", "total", "total")]
    if comp.get("neutralSite") is False:
        pairs.append(("Home/road record", "road", "home"))
    for label, ar, hr in pairs:
        if ("away", ar) in records and ("home", hr) in records:
            signals[label] = records["away", ar] - records["home", hr]
    return signals


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
    return {"id": "espn:"+str(header["id"]), "league": league, "home": teams["home"]["displayName"], "away": teams["away"]["displayName"], "start_utc": start.astimezone(timezone.utc).isoformat(), "books": books,
            "winner_team_ids": [str(teams[s]["id"]) for s in ("away","home")],
            "winner_matchup": matchup_signals(body, comp, teams)}


def recent_matchup(history, event, seen_at, league):
    """Recent results that Grader had in hand by this quote. Each final game counts once."""
    ids = event.get("winner_team_ids") or []
    if len(ids) != 2 or ids[0] == ids[1]:
        return {}
    margins = [[], []]
    meetings = []
    for row in history:
        if row["fetched_at"] > seen_at or row["start"] >= seen_at:
            continue
        home, away = row["home_id"], row["away_id"]
        gap = row["home_score"] - row["away_score"]
        for i, team in enumerate(ids):
            if team in (home,away) and len(margins[i]) < 10:
                margins[i].append(gap if team == home else -gap)
        if {home, away} == set(ids):
            meetings.append(gap if home == ids[0] else -gap)
    result = {}
    if all(margins):
        means = [sum(gaps)/(len(gaps)+4) for gaps in margins]
        result["Recent scoring margin"] = math.tanh((means[0]-means[1]) / (12 if league == "nba" else 14))
    if meetings:
        result["Recent head-to-head"] = sum((g > 0)-(g < 0) for g in meetings)/(len(meetings)+4)
    return result


def refresh(db, league, now=None):
    """Publish only current non-late revisions; keep each quote's last-seen time, never restamp old odds."""
    from .games import store
    now = now or datetime.now(timezone.utc)
    try:
        rows = db.all("SELECT s.data, st.last_seen_at FROM sports_game_line_state st JOIN sports_game_snapshots s ON s.id=st.last_id WHERE st.tbl='sports_game_snapshots' AND s.league=? AND s.kind='book_game_prices' AND s.source='espn' AND s.late=0 ORDER BY st.last_seen_at DESC LIMIT 500", (league,))
        history = db.all("SELECT start,fetched_at,home_id,away_id,home_score,away_score FROM sports_game_games "
                         "WHERE league=? AND start>=? AND start<? AND completed=1 AND fetched_at IS NOT NULL "
                         "AND home_score IS NOT NULL AND away_score IS NOT NULL ORDER BY start DESC,event_id",
                         (league, store.iso(now-timedelta(days=120)), store.iso(now)))
        events = []
        for r in rows:
            e = json.loads(r["data"])
            if e.get("league") != league: continue
            if datetime.fromisoformat(e["start_utc"].replace("Z", "+00:00")) <= now: continue
            e["seen_at"] = r["last_seen_at"]
            e.setdefault("winner_matchup", {}).update(recent_matchup(history, e, e["seen_at"], league))
            e["league"] = league
            e["reference_props"] = reference_props(db, league, e["id"].removeprefix("espn:"), now)
            events.append(e)
        doc = {"league": league, "source": "ESPN", "source_time_unknown": True, "observed_at": now.isoformat(), "events": sorted(events, key=lambda e:e["start_utc"])}
        target = path(league);target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp");tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8");os.replace(tmp,target)
        return {"events": len(events)}
    except Exception as e:
        return {"error": type(e).__name__}


def reference_props(db, league, event_id, now):
    """Latest non-late ESPN thresholds for this event, never a sportsbook price.

    Join the current pointer rather than historical MAX timestamps. Reconfirmed
    unchanged rows retain their revision and last-seen time. Old/unmapped rows
    cannot look like newly offered markets merely because a snapshot rebuilt.
    """
    rows = db.all("SELECT p.*, st.last_seen_at, a.player AS roster_player FROM sports_game_prop_lines p "
        "JOIN sports_game_line_state st ON st.key=('prop:' || p.source || ':' || p.prop_key) "
        "AND st.tbl='sports_game_prop_lines' AND st.last_id=p.id "
        "LEFT JOIN sports_game_athletes a ON a.league=p.league AND a.athlete_id=p.athlete_id "
        "WHERE p.league=? AND p.event_id=? AND p.source='espn' AND p.book='draftkings' AND p.late=0 "
        "AND p.status='open' ORDER BY p.player,p.market,p.period,p.line LIMIT 10000", (league,event_id))
    out = []
    for p in rows:
        player = p['player'] or p['roster_player']
        if not player or not p['athlete_id'] or not p['market'] or p['period'] not in ('game','1h','2h','1q'): continue
        line = number(p['line'])
        if line is None or p['kind'] not in ('line','milestone'): continue
        try: age = (now-datetime.fromisoformat(p['last_seen_at'].replace('Z','+00:00'))).total_seconds()
        except (ValueError,TypeError): continue
        if not 0 <= age <= 5400: continue
        out.append({'revision_id':p['id'], 'player_id':p['athlete_id'], 'player':player,
                    'market':p['market'], 'period':p['period'], 'kind':p['kind'], 'line':line,
                    'label':p['raw_type'], 'seen_at':p['last_seen_at'], 'source':'ESPN-supplied DraftKings',
                    'price_over':None, 'price_under':None,
                    'reported_prices':json.loads(p['reported_prices']) if p['reported_prices'] else [],
                    'price_side_known':False})
    return out


def view(req):
    from . import api_board
    league = req.query.get("league")
    if league not in ("nba", "nfl"): return 400, {"error": "league must be nba or nfl"}
    response = api_board._serve(path(league))
    if response[0] == 503:
        # An optional snapshot not built yet is not a service outage. A 503 here
        # would trip the shared Sports connection breaker on the esports site.
        return 200, {"league": league, "source": "ESPN", "pending": True,
                     "observed_at": datetime.now(timezone.utc).isoformat(), "events": []}
    return response
