"""ESPN game data -> storage rows: everything ESPN publishes about a game, parsed once and kept.

  site.api.espn.com/apis/site/v2/sports/<sport>/<league>/summary?event=<id>
      header (season, week, teams, score, line scores, status), boxscore (every team and player stat, starter, did not
      play + reason, ejected), plays (basketball: shot location, points attempted, participants in order) or drives
      (football: down / distance / yards to the end zone before and after, yards, yards after catch, penalty,
      turnover), winprobability (home chance after each play), gameInfo (venue, attendance, officials), pickcenter
      (the odds ESPN shows, its provider named), predictor (ESPN's matchup projection), injuries (football)
  sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/<id>/competitions/<id>/plays?limit=1000
      football only: each play's participants WITH their role (passer, receiver, rusher, tackler, sackedBy, ...),
      which the summary's play text only abbreviates ("C.Stroud pass short left to D.Schultz")
  sports.core.api.espn.com/v2/sports/<sport>/leagues/<league>/venues/<id>       indoor / grass, once per venue
  sports.core.api.espn.com/v2/sports/<sport>/leagues/<league>/seasons/<yyyy>/types/<t>/events   a season's game ids

Box-score stats are kept under ESPN's own names, numbers only; an "a-b" or "a/b" stat is split into its two names
("fieldGoalsMade-fieldGoalsAttempted" "40-86" -> fieldGoalsMade 40, fieldGoalsAttempted 86). Football player stats are
prefixed by their box-score group ("passing.passingYards", "receiving.receivingTargets").
"""
import re

from .. import names, net
from ..lines import utc_iso

SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://sports.core.api.espn.com/v2/sports"
_TEAM_PAIRS = {"thirdDownEff": ("thirdDownConversions", "thirdDownAttempts"),
               "fourthDownEff": ("fourthDownConversions", "fourthDownAttempts"),
               "redZoneAttempts": ("redZoneTouchdowns", "redZoneAttempts"),
               "totalPenaltiesYards": ("penalties", "penaltyYards"),
               "sacksYardsLost": ("sacksAllowed", "sackYardsLost"),
               "completionAttempts": ("completions", "passingAttempts")}
_PAIR_VALUE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[-/]\s*(-?\d+(?:\.\d+)?)\s*$")
_SENTINEL = -1e8                     # ESPN's "no location" coordinate is about -2.1e9 (free throws, fouls)
_ID = re.compile(r"/(?:athletes|teams|venues|events)/(\d+)")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if re.match(r"^\d+:\d{2}$", s):                                   # "29:37" possession time -> seconds
        m, sec = s.split(":")
        return int(m) * 60 + int(sec)
    try:
        return float(s.lstrip("+"))
    except ValueError:
        return None


def stat_dict(stat_names, values, prefix=""):
    """ESPN stat names + display values -> {name: number}; pairs split; '--' and blanks dropped."""
    out = {}
    for n, v in zip(stat_names or [], values or []):
        if not n:
            continue
        m = _PAIR_VALUE.match(str(v)) if v is not None else None
        pair = _TEAM_PAIRS.get(n) or (tuple(re.split(r"[-/]", n, maxsplit=1)) if re.search(r"[-/]", n) else None)
        if m and pair and len(pair) == 2:
            out[prefix + pair[0]], out[prefix + pair[1]] = float(m.group(1)), float(m.group(2))
            continue
        x = _num(v)
        if x is not None:
            out[prefix + n] = x
    return out


def clock_s(display):
    """'10:53' -> 653.0 seconds left in the period ('45.2' -> 45.2)."""
    if not display:
        return None
    s = str(display)
    if ":" in s:
        m, sec = s.split(":", 1)
        try:
            return int(m) * 60 + float(sec)
        except ValueError:
            return None
    return _num(s)


def _coord(c, k):
    v = (c or {}).get(k)
    return None if v is None or v < _SENTINEL else float(v)


def ref_id(ref):
    m = _ID.search((ref or {}).get("$ref", "") if isinstance(ref, dict) else str(ref or ""))
    return m.group(1) if m else None


def parse(body, league, family, core_plays=None, venue=None, now_iso=None):
    """One summary (+ football core plays, + venue details) -> {"game", "teams", "players", "plays", "context"}."""
    header = body.get("header") or {}
    comp = (header.get("competitions") or [{}])[0]
    st = (comp.get("status") or {}).get("type") or {}
    side = {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        side[c.get("homeAway")] = {"id": str(t.get("id")), "abbr": t.get("abbreviation"), "score": _num(c.get("score")),
                                   "lines": [_num(x.get("displayValue")) for x in c.get("linescores") or []]}
    home, away = side.get("home") or {}, side.get("away") or {}
    info = body.get("gameInfo") or {}
    v = info.get("venue") or {}
    addr = v.get("address") or {}
    event_id = str(comp.get("id") or header.get("id"))
    game = {"league": league, "event_id": event_id, "season": (header.get("season") or {}).get("year"),
            "season_type": (header.get("season") or {}).get("type"), "week": header.get("week"),
            "start": utc_iso(comp.get("date")), "state": st.get("state"), "completed": int(bool(st.get("completed"))),
            "home_id": home.get("id"), "away_id": away.get("id"), "home_abbr": home.get("abbr"),
            "away_abbr": away.get("abbr"),
            "home_score": int(home["score"]) if home.get("score") is not None else None,
            "away_score": int(away["score"]) if away.get("score") is not None else None,
            "periods": len(home.get("lines") or []) or None, "neutral": int(bool(comp.get("neutralSite"))),
            "venue_id": v.get("id"), "venue": v.get("fullName"), "city": addr.get("city"), "region": addr.get("state"),
            "country": addr.get("country"),
            "indoor": None if (venue or {}).get("indoor") is None else int(bool(venue["indoor"])),
            "grass": None if v.get("grass") is None else int(bool(v.get("grass"))),
            "attendance": info.get("attendance"),
            "officials": [{"name": o.get("displayName"), "position": (o.get("position") or {}).get("name"),
                           "order": o.get("order")} for o in info.get("officials") or []],
            "linescores": {"home": home.get("lines"), "away": away.get("lines")},
            "box_source": comp.get("boxscoreSource"), "pbp_source": comp.get("playByPlaySource"),
            "fetched_at": now_iso}
    opp = {home.get("id"): away.get("id"), away.get("id"): home.get("id")}
    teams = []
    for t in (body.get("boxscore") or {}).get("teams") or []:
        tid = str((t.get("team") or {}).get("id"))
        stats = {}
        for s in t.get("statistics") or []:
            stats.update(stat_dict([s.get("name")], [s.get("displayValue")]))
        sc = home.get("score") if tid == home.get("id") else away.get("score")
        teams.append({"league": league, "event_id": event_id, "team_id": tid, "home": int(tid == home.get("id")),
                      "opp_id": opp.get(tid), "abbr": (t.get("team") or {}).get("abbreviation"),
                      "score": int(sc) if sc is not None else None, "stats": stats})
    players = {}
    for t in (body.get("boxscore") or {}).get("players") or []:
        tid = str((t.get("team") or {}).get("id"))
        for grp in t.get("statistics") or []:
            keys = grp.get("keys") or grp.get("names") or []
            prefix = f"{grp.get('name')}." if family == "football" and grp.get("name") else ""
            for a in grp.get("athletes") or []:
                ath = a.get("athlete") or {}
                aid = str(ath.get("id"))
                row = players.get(aid)
                if row is None:
                    row = players[aid] = {
                        "league": league, "event_id": event_id, "athlete_id": aid, "player": ath.get("displayName"),
                        "player_key": names.key(ath.get("displayName")), "team_id": tid, "opp_id": opp.get(tid),
                        "home": int(tid == home.get("id")), "position": (ath.get("position") or {}).get("abbreviation"),
                        "jersey": ath.get("jersey"), "starter": None if a.get("starter") is None else int(a["starter"]),
                        "played": None, "dnp_reason": None, "ejected": int(bool(a.get("ejected"))), "stats": {}}
                row["stats"].update(stat_dict(keys, a.get("stats"), prefix))
                if a.get("didNotPlay"):
                    row["played"], row["dnp_reason"] = 0, a.get("reason")
                elif family == "basketball":
                    mins = row["stats"].get("minutes")
                    row["played"] = int(bool(a.get("stats")) and (mins or 0) > 0)
                    if not row["played"] and a.get("reason"):
                        row["dnp_reason"] = a.get("reason")
                elif a.get("stats"):
                    row["played"] = 1
    wp = {str(w.get("playId")): w.get("homeWinPercentage") for w in body.get("winprobability") or []}
    plays = _plays_basketball(body, league, event_id, wp) if family == "basketball" else \
        _plays_football(body, league, event_id, wp, core_plays)
    game["n_plays"] = len(plays)
    walls = [p["wallclock"] for p in plays if p.get("wallclock")]
    last = (body.get("meta") or {}).get("lastPlayWallClock")
    game["ended_at"] = utc_iso(last or (max(walls) if walls else None)) if game["completed"] else None
    game["participants"] = int(family == "basketball" or bool(core_plays))
    return {"game": game, "teams": teams, "players": list(players.values()), "plays": plays,
            "context": context(body)}


def _plays_basketball(body, league, event_id, wp):
    out = []
    for i, p in enumerate(body.get("plays") or []):
        c = p.get("coordinate") or {}
        out.append({"league": league, "event_id": event_id, "seq": i, "play_id": p.get("id"),
                    "period": (p.get("period") or {}).get("number"),
                    "clock_s": clock_s((p.get("clock") or {}).get("displayValue")),
                    "team_id": (p.get("team") or {}).get("id"), "type_id": (p.get("type") or {}).get("id"),
                    "type": (p.get("type") or {}).get("text"), "text": p.get("text"),
                    "scoring": int(bool(p.get("scoringPlay"))), "score_value": p.get("scoreValue"),
                    "home_score": p.get("homeScore"), "away_score": p.get("awayScore"), "wallclock": p.get("wallclock"),
                    "home_wp": wp.get(str(p.get("id"))), "x": _coord(c, "x"), "y": _coord(c, "y"),
                    "shooting": int(bool(p.get("shootingPlay"))), "points_attempted": p.get("pointsAttempted"),
                    "athletes": [{"id": str((x.get("athlete") or {}).get("id")), "role": None}
                                 for x in p.get("participants") or []]})
    return out


def _core_roles(core_plays):
    out = {}
    for it in (core_plays or {}).get("items") or []:
        out[str(it.get("id"))] = [{"id": ref_id(x.get("athlete")), "role": x.get("type")}
                                  for x in it.get("participants") or [] if ref_id(x.get("athlete"))]
    return out


def _plays_football(body, league, event_id, wp, core_plays):
    roles = _core_roles(core_plays)
    out, i = [], 0
    drives = (body.get("drives") or {})
    for d in (drives.get("previous") or []) + ([drives["current"]] if drives.get("current") else []):
        for p in d.get("plays") or []:
            s, e = p.get("start") or {}, p.get("end") or {}
            off = de = None
            for tp in p.get("teamParticipants") or []:
                if tp.get("type") == "offense":
                    off = str(tp.get("id"))
                elif tp.get("type") == "defense":
                    de = str(tp.get("id"))
            out.append({"league": league, "event_id": event_id, "seq": i, "play_id": p.get("id"),
                        "period": (p.get("period") or {}).get("number"),
                        "clock_s": clock_s((p.get("clock") or {}).get("displayValue")),
                        "team_id": off or ((s.get("team") or {}).get("id")), "type_id": (p.get("type") or {}).get("id"),
                        "type": (p.get("type") or {}).get("text"), "text": p.get("text"),
                        "scoring": int(bool(p.get("scoringPlay"))), "score_value": p.get("scoreValue"),
                        "home_score": p.get("homeScore"), "away_score": p.get("awayScore"),
                        "wallclock": p.get("wallclock"), "home_wp": wp.get(str(p.get("id"))),
                        "down": s.get("down"), "distance": s.get("distance"), "yte": s.get("yardsToEndzone"),
                        "end_down": e.get("down"), "end_distance": e.get("distance"), "end_yte": e.get("yardsToEndzone"),
                        "yards": p.get("statYardage"), "yac": p.get("yardsAfterCatch"),
                        "penalty": int(bool(p.get("isPenalty"))), "turnover": int(bool(p.get("isTurnover"))),
                        "scoring_type": (p.get("scoringType") or {}).get("name"), "drive_id": d.get("id"),
                        "offense_id": off, "defense_id": de, "athletes": roles.get(str(p.get("id")), [])})
            i += 1
    return out


def context(body):
    """Pre-game context a summary carries, as snapshot payloads: {kind: data}."""
    out = {}
    pcs = []
    for pc in body.get("pickcenter") or []:
        h, a = pc.get("homeTeamOdds") or {}, pc.get("awayTeamOdds") or {}
        pcs.append({"provider": (pc.get("provider") or {}).get("name"), "details": pc.get("details"),
                    "spread": pc.get("spread"), "total": pc.get("overUnder"), "over_odds": pc.get("overOdds"),
                    "under_odds": pc.get("underOdds"), "home_ml": h.get("moneyLine"), "away_ml": a.get("moneyLine"),
                    "home_spread_odds": h.get("spreadOdds"), "away_spread_odds": a.get("spreadOdds"),
                    "open_close": {k: pc.get(k) for k in ("pointSpread", "total", "moneyline") if pc.get(k)}})
    if pcs:
        out["pickcenter"] = pcs
    pred = body.get("predictor") or {}
    if pred:
        out["predictor"] = {side: {"team_id": (pred.get(side) or {}).get("id"),
                                   **{k: _num(v) for k, v in (pred.get(side) or {}).items()
                                      if k in ("gameProjection", "teamChanceLoss", "teamChanceTie")}}
                            for side in ("homeTeam", "awayTeam")}
    inj = []
    for t in body.get("injuries") or []:
        for i in t.get("injuries") or []:
            ath = i.get("athlete") or {}
            inj.append({"team_id": str((t.get("team") or {}).get("id")), "athlete_id": str(ath.get("id")),
                        "player": ath.get("displayName"), "position": (ath.get("position") or {}).get("abbreviation"),
                        "status": i.get("status"), "date": i.get("date"),
                        "detail": " ".join(str(x) for x in ((i.get("details") or {}).get(k)
                                                            for k in ("side", "type", "detail")) if x) or None})
    if inj:
        out["injuries"] = inj
    return out


# ---------------------------------------------------------------------------------------------------------- fetches

def summary_raw(lg, event_id):
    return net.get_json(f"{SITE}/{lg['espn'][0]}/{lg['espn'][1]}/summary", params={"event": event_id})


def core_plays_raw(lg, event_id):
    url = f"{CORE}/{lg['espn'][0]}/leagues/{lg['espn'][1]}/events/{event_id}/competitions/{event_id}/plays"
    body = net.get_json(url, params={"limit": 1000})
    items = list(body.get("items") or [])
    for page in range(2, int(body.get("pageCount") or 1) + 1):
        items += net.get_json(url, params={"limit": 1000, "page": page}).get("items") or []
    return {"items": items}


def venue_raw(lg, venue_id):
    return net.get_json(f"{CORE}/{lg['espn'][0]}/leagues/{lg['espn'][1]}/venues/{venue_id}")


def season_event_ids(lg, season, season_type):
    """Every game id ESPN lists for a season and type (2 regular season, 3 postseason; 1 preseason not used)."""
    url = f"{CORE}/{lg['espn'][0]}/leagues/{lg['espn'][1]}/seasons/{season}/types/{season_type}/events"
    body = net.get_json(url, params={"limit": 1000})
    refs = list(body.get("items") or [])
    for page in range(2, int(body.get("pageCount") or 1) + 1):
        refs += net.get_json(url, params={"limit": 1000, "page": page}).get("items") or []
    return [i for i in (ref_id(r) for r in refs) if i]
