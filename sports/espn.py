"""ESPN's public site APIs - schedule, players, game logs, box scores and injuries for every league in config.LEAGUES.

No key and no login; it answers a cloud server (stats.nba.com, which swar/nba_api reads, times out from cloud hosts and
cdn.nba.com answers 403 - checked 2026-10-23 - so the NBA's own site cannot be the server's source). Endpoints as
documented by pseudo-r/Public-ESPN-API (MIT):

  site.api.espn.com/apis/site/v2/sports/<sport>/<league>/scoreboard?dates=YYYYMMDD[-YYYYMMDD]
  site.api.espn.com/apis/site/v2/sports/<sport>/<league>/summary?event=<id>          (box score)
  site.api.espn.com/apis/site/v2/sports/<sport>/<league>/injuries
  site.web.api.espn.com/apis/common/v3/sports/<sport>/<league>/athletes/<id>/gamelog[?season=YYYY]
  site.web.api.espn.com/apis/search/v2?query=<name>&type=player                      (name -> athlete id)
  site.web.api.espn.com/apis/common/v3/sports/<sport>/<league>/athletes/<id>/splits  (home / away / opponent ...)
  site.api.espn.com/apis/site/v2/sports/<sport>/<league>/teams[/<id>/depthcharts]    (who starts where)

Unofficial: 2 requests / second at most, and everything is cached (a final box score for good). The ESPN API hosts
publish no robots.txt (checked 2026-10-23). Basketball-Reference's robots.txt disallows */gamelog/ and */splits/ and
Pro-Football-Reference answers a Cloudflare challenge, so neither is scraped.
"""
import re
from datetime import datetime, timedelta, timezone

from . import cache, markets, names, net
from .lines import utc_iso

SITE = "https://site.api.espn.com/apis/site/v2/sports"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports"
SEARCH = "https://site.web.api.espn.com/apis/search/v2"


def _path(lg):
    return "/".join(lg["espn"])


def _event(e):
    c = (e.get("competitions") or [{}])[0]
    side = {}
    for x in c.get("competitors") or []:
        t = x.get("team") or {}
        side[x.get("homeAway")] = {"id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("displayName"),
                                   "nick": t.get("name")}        # "Celtics", "Trail Blazers" - every book prints it
    st = (c.get("status") or e.get("status") or {}).get("type") or {}
    return {"id": e.get("id"), "start": utc_iso(e.get("date")), "name": e.get("name"), "short": e.get("shortName"),
            "home": side.get("home"), "away": side.get("away"), "state": st.get("state"),
            "completed": bool(st.get("completed")), "status": st.get("description")}


def scoreboard_raw(lg, day, refresh=False):
    """ESPN's scoreboard body for ONE US calendar day (cached). 2026-10-23: a date RANGE (dates=YYYYMMDD-YYYYMMDD,
    with or without limit) answers HTTP 400 "Failed to get events endpoint." for NBA and NFL, so ranges are read one
    day at a time (integration fix, sports/INTEGRATION_MANIFEST.md)."""
    d = f"{day:%Y%m%d}"
    past = day < datetime.now(timezone.utc).date() - timedelta(days=1)
    params = {"dates": d, "limit": 500}
    if lg["key"] == "cfb":
        params["groups"] = 80                     # FBS (ESPN's default lists only ranked games)
    return cache.fetch("espn:scoreboard", f"{lg['key']}:{d}", 6 * 3600 if past else 300,
                       lambda: net.get_json(f"{SITE}/{_path(lg)}/scoreboard", params=params), refresh=refresh)


def scoreboard(lg, start, end, refresh=False):
    """Games whose dates fall in [start, end] (dates, US calendar days as ESPN files them)."""
    out, seen, day = [], set(), start
    while day <= end:
        for e in scoreboard_raw(lg, day, refresh=refresh).get("events") or []:
            if e.get("id") not in seen:
                seen.add(e.get("id"))
                out.append(_event(e))
        day += timedelta(days=1)
    return out


def slate(lg, hours=36, now=None):
    now = now or datetime.now(timezone.utc)
    evs = scoreboard(lg, (now - timedelta(hours=12)).date(), (now + timedelta(hours=hours)).date())
    lo, hi = now - timedelta(hours=4), now + timedelta(hours=hours)
    keep = []
    for e in evs:
        t = datetime.fromisoformat(e["start"].replace("Z", "+00:00")) if e["start"] else None
        if t and lo <= t <= hi and not e["completed"]:
            keep.append(e)
    return sorted(keep, key=lambda e: e["start"])


def search_athlete(lg, name):
    """-> [{"id", "name", "team"}] for this league (ESPN uid 's:40~l:<league>~a:<id>')."""
    body = cache.fetch("espn:search", f"{lg['key']}:{names.key(name)}", 7 * 86400,
                       lambda: net.get_json(SEARCH, params={"query": name, "limit": 10, "type": "player"}))
    out = []
    for r in body.get("results") or []:
        for c in r.get("contents") or []:
            m = re.search(r"l:(\d+)~a:(\d+)", c.get("uid") or "")
            if m and m.group(1) == lg["espn_uid"]:
                out.append({"id": m.group(2), "name": c.get("displayName"), "team": c.get("subtitle")})
    exact = [x for x in out if names.key(x["name"]) == names.key(name)]
    return exact or out


def parse_gamelog(body, family):
    """ESPN game log -> games, newest first: {event_id, date, opponent, home, result, parts}. Preseason left out."""
    nm = body.get("names") or []
    meta = body.get("events") or {}
    games = []
    for st in body.get("seasonTypes") or []:
        label = (st.get("displayName") or "").lower()
        if "preseason" in label or "pre season" in label:
            continue
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                m = meta.get(ev.get("eventId")) or {}
                parts = markets.defaults_for(family, markets.espn_parts(nm, ev.get("stats")), True)
                games.append({"event_id": ev.get("eventId"), "date": utc_iso(m.get("gameDate")),
                              "opponent": (m.get("opponent") or {}).get("abbreviation"),
                              "home": m.get("atVs") == "vs", "result": m.get("gameResult"),
                              "postseason": "post" in label, "parts": parts})
    seen, out = set(), []
    for g in sorted(games, key=lambda g: g["date"] or "", reverse=True):
        if g["event_id"] not in seen:
            seen.add(g["event_id"])
            out.append(g)
    return out


def _season_of(body):
    for f in body.get("filters") or []:
        if f.get("name") == "season":
            try:
                return int(f.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def gamelog(lg, athlete_id, seasons=2, refresh=False):
    """This season's and the previous seasons' games (newest first)."""
    url = f"{WEB}/{_path(lg)}/athletes/{athlete_id}/gamelog"
    body = cache.fetch("espn:gamelog", f"{lg['key']}:{athlete_id}", 6 * 3600, lambda: net.get_json(url),
                       refresh=refresh)
    games = parse_gamelog(body, lg["family"])
    season = _season_of(body)
    for back in range(1, seasons):
        if not season:
            break
        s = season - back
        try:
            old = cache.fetch("espn:gamelog", f"{lg['key']}:{athlete_id}:{s}", 30 * 86400,
                              lambda s=s: net.get_json(url, params={"season": s}))
        except net.HttpError:
            break
        games += parse_gamelog(old, lg["family"])
    return games


def parse_summary(body, family):
    """Box score -> {"completed", "start", "players": {player_key: [{"name", "id", "team", "played", "parts"}]}}.
    played: True / False (ESPN's did-not-play flag or 0 minutes, basketball) / None (football: a player missing from
    every box-score group may have played without a stat - settle.py then asks his game log)."""
    comp = ((body.get("header") or {}).get("competitions") or [{}])[0]
    st = (comp.get("status") or {}).get("type") or {}
    players = {}
    for team in (body.get("boxscore") or {}).get("players") or []:
        abbr = (team.get("team") or {}).get("abbreviation")
        for grp in team.get("statistics") or []:
            cols = grp.get("keys") or grp.get("names") or grp.get("labels") or []
            for a in grp.get("athletes") or []:
                ath = a.get("athlete") or {}
                pk = names.key(ath.get("displayName"))
                rows = players.setdefault(pk, [])
                row = next((r for r in rows if r["id"] == ath.get("id")), None)
                if row is None:
                    row = {"name": ath.get("displayName"), "id": ath.get("id"), "team": abbr, "played": None, "parts": {}}
                    rows.append(row)
                parts = markets.espn_parts(cols, a.get("stats"), grp.get("name"))
                row["parts"].update(parts)
                if family == "basketball":
                    dnp = a.get("didNotPlay") or not a.get("stats")
                    row["played"] = False if dnp else (parts.get("min", 0) > 0)
                elif a.get("stats"):
                    row["played"] = True
    for rows in players.values():
        for r in rows:
            r["parts"] = markets.defaults_for(family, r["parts"], r["played"])
    return {"completed": bool(st.get("completed")), "start": utc_iso(comp.get("date")), "players": players}


def summary(lg, event_id, refresh=False):
    key = f"{lg['key']}:{event_id}"
    hit = None if refresh else cache.get("espn:summary", key)
    if hit and hit.get("completed"):
        return hit                                  # a final box score never changes
    hit = None if refresh else cache.get("espn:summary", key, 300)
    if hit:
        return hit
    data = parse_summary(net.get_json(f"{SITE}/{_path(lg)}/summary", params={"event": event_id}), lg["family"])
    cache.put("espn:summary", key, data)
    return data


def injuries(lg, refresh=False):
    """-> {player_key: {"name", "team", "status", "detail"}} (ESPN's injury report)."""
    body = cache.fetch("espn:injuries", lg["key"], 1800,
                       lambda: net.get_json(f"{SITE}/{_path(lg)}/injuries"), refresh=refresh)
    out = {}
    for t in body.get("injuries") or []:
        for i in t.get("injuries") or []:
            if (i.get("status") or "").lower() in ("", "active"):
                continue                               # the NFL list also carries players back to Active
            ath, det = i.get("athlete") or {}, i.get("details") or {}
            out[names.key(ath.get("displayName"))] = {
                "name": ath.get("displayName"), "team": t.get("displayName"), "status": i.get("status"),
                "detail": " ".join(x for x in (det.get("side"), det.get("type"), det.get("detail")) if x) or None,
                "return": det.get("returnDate")}
    return out


DEPTH_LEAGUES = ("nba", "wnba", "nfl")         # college has 130-360 teams: no depth charts read
_NFL_OFFENSE = ("qb", "rb", "wr1", "wr2", "wr3", "te")


def teams(lg):
    """-> [{"id", "abbr", "name"}] for the league."""
    body = cache.fetch("espn:teams", lg["key"], 7 * 86400,
                       lambda: net.get_json(f"{SITE}/{_path(lg)}/teams", params={"limit": 500}))
    out = []
    for sp in body.get("sports") or []:
        for le in sp.get("leagues") or []:
            for t in le.get("teams") or []:
                t = t.get("team") or {}
                out.append({"id": t.get("id"), "abbr": t.get("abbreviation"), "name": t.get("displayName")})
    return out


def _role(family, pos, depth):
    """('wr2', 0) -> 'WR2'; ('rb', 1) -> 'RB2'; ('pg', 0) -> 'starter (PG)'; ('c', 2) -> 'bench (C #3)'."""
    p = pos.upper()
    if family == "basketball":
        return f"starter ({p})" if depth == 0 else f"bench ({p} #{depth + 1})"
    if p[-1:].isdigit():
        return p if depth == 0 else f"{p} backup #{depth}"
    return f"{p}{depth + 1}"


def parse_depth(body, family, team_abbr=None):
    """One team's depth chart -> {player_key: {"name", "id", "team", "pos", "depth", "role"}}. A player listed at two
    positions keeps the higher one (lower depth). Football keeps the offence (QB, RB, WR1-3, TE) only."""
    out = {}
    for grp in body.get("depthchart") or []:
        for key, slot in (grp.get("positions") or {}).items():
            if family == "football" and key not in _NFL_OFFENSE:
                continue
            for i, a in enumerate(slot.get("athletes") or []):
                pk = names.key(a.get("displayName"))
                if pk and (pk not in out or i < out[pk]["depth"]):
                    out[pk] = {"name": a.get("displayName"), "id": a.get("id"), "team": team_abbr, "pos": key,
                               "depth": i, "role": _role(family, key, i)}
    return out


def depth_index(lg, refresh=False):
    """{player_key: role row} over every team's depth chart (cached 6 h; one request per team)."""
    if lg["key"] not in DEPTH_LEAGUES:
        return {}

    def load():
        idx = {}
        for t in teams(lg):
            try:
                body = net.get_json(f"{SITE}/{_path(lg)}/teams/{t['id']}/depthcharts")
            except net.HttpError:
                continue                               # one team failing leaves the others
            for pk, row in parse_depth(body, lg["family"], t["abbr"]).items():
                if pk not in idx or row["depth"] < idx[pk]["depth"]:
                    idx[pk] = row
        return idx
    return cache.fetch("espn:depth", lg["key"], 6 * 3600, load, refresh=refresh)


def parse_splits(body):
    """Splits -> {"names": [...], "cats": {category: {split name: {stat name: value}}}}."""
    nm = body.get("names") or []
    cats = {}
    for c in body.get("splitCategories") or []:
        cats[c.get("name")] = {s.get("displayName"): dict(zip(nm, s.get("stats") or [])) for s in c.get("splits") or []}
    return {"names": nm, "cats": cats}


def splits(lg, athlete_id, refresh=False):
    body = cache.fetch("espn:splits", f"{lg['key']}:{athlete_id}", 6 * 3600,
                       lambda: net.get_json(f"{WEB}/{_path(lg)}/athletes/{athlete_id}/splits"), refresh=refresh)
    return parse_splits(body)
