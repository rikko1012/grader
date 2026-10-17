"""LoL Esports (Riot's lolesports.com data) - schedule, results, rosters and per-game player stats. No login.

Two hosts (documented by the community: vickz84259.github.io/lolesports-api-docs):
  esports-api.lolesports.com/persisted/gw/*   leagues, tournaments, schedule, completed events, event details, teams.
                                              Needs the site's public x-api-key (the key lolesports.com itself sends;
                                              LOLESPORTS_API_KEY overrides it if Riot rotates it).
  feed.lolesports.com/livestats/v1/window/*   per-game frames: every player's kills, deaths, assists, CS, gold, level,
                                              champion and role. Asked once without a time (the game's first frames) and
                                              once at a time after the game ended (its final frames).

What it gives the grader: the slate (upcoming matches, best-of), rosters, and the game history of every match lolesports
broadcasts (the backup to Leaguepedia, which covers more events but needs a bot password). It has no per-game winner:
winner() infers it from inhibitors / towers / gold and the sync keeps it only when the inferred wins add up to the
series score.

Caching (lol/cache.py): finished games and finished matches are kept (they never change); schedules 5 minutes,
rosters 12 hours, tournament lists 12 hours.
"""
import os
import re
from datetime import datetime, timedelta, timezone

from . import cache, net

API = "https://esports-api.lolesports.com/persisted/gw"
FEED = "https://feed.lolesports.com/livestats/v1"
API_KEY = os.environ.get("LOLESPORTS_API_KEY") or "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
FOREVER = None
DAY = 86400
ROLES = {"top": "top", "jungle": "jungle", "mid": "mid", "middle": "mid", "bottom": "bot", "bot": "bot", "adc": "bot",
         "support": "support", "utility": "support"}
# the leagues the grader follows by default (props are posted on these); `--leagues all` reads every league
MAJOR = ("worlds", "msi", "first_stand", "lck", "lpl", "lec", "lcs", "lcp", "cblol-brazil", "lta_n", "lta_s", "ewc_lol",
         "demacia_cup", "lck_challengers_league", "emea_masters", "lfl", "primeleague", "pcs", "vcs", "ljl-japan")


def _gw(endpoint, ttl, **params):
    params = {k: v for k, v in params.items() if v is not None}
    name = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    hit = cache.get("le", name, ttl)
    if hit is not None:
        return hit
    r = net.get(f"{API}/{endpoint}", params=dict(hl="en-US", **params), headers={"x-api-key": API_KEY})
    data = r.json().get("data") or {}
    cache.put("le", name, data)
    return data


def leagues():
    return _gw("getLeagues", DAY).get("leagues") or []


def league_ids(slugs=None):
    """{slug: id} for the given slugs (default MAJOR; 'all' = every league)."""
    allv = {lg["slug"]: lg["id"] for lg in leagues()}
    if slugs == "all":
        return allv
    want = slugs or MAJOR
    return {s: allv[s] for s in want if s in allv}


def tournaments(league_id):
    data = _gw("getTournamentsForLeague", DAY / 2, leagueId=league_id)
    out = []
    for lg in data.get("leagues") or []:
        out += lg.get("tournaments") or []
    return out


def completed_events(tournament_id, ttl=3600):
    return (_gw("getCompletedEvents", ttl, tournamentId=tournament_id).get("schedule") or {}).get("events") or []


def event_details(match_id):
    """A match: league, tournament, teams (id, name, code, series score), games (number, id, state, sides).
    Kept for good once every game is completed / unneeded; otherwise re-read after 10 minutes."""
    name = f"getEventDetails?id={match_id}"
    hit = cache.get("le_done", name)
    if hit is not None:
        return hit
    data = _gw("getEventDetails", 600, id=match_id).get("event") or {}
    games = ((data.get("match") or {}).get("games")) or []
    if games and all(g.get("state") in ("completed", "unneeded") for g in games):
        cache.put("le_done", name, data)
    return data


def schedule(league_slugs=None, page_token=None):
    ids = ",".join(league_ids(league_slugs).values()) if league_slugs != "all" else None
    return _gw("getSchedule", 300, leagueId=ids, pageToken=page_token).get("schedule") or {}


def team(slug_or_id):
    teams = _gw("getTeams", DAY / 2, id=slug_or_id).get("teams") or []
    return teams[0] if teams else None


def live():
    return (_gw("getLive", 30).get("schedule") or {}).get("events") or []


def _when(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def slate(hours=36, league_slugs=None, now=None):
    """Matches starting in the next `hours` (and any in progress), soonest first:
    [{match_id, league, block, start, state, best_of, teams: [{name, code}]}]. TBD vs TBD placeholders skipped."""
    now = now or datetime.now(timezone.utc)
    end = now + timedelta(hours=hours)
    out, token, pages = [], None, 0
    while pages < 6:
        sch = schedule(league_slugs, token)
        pages += 1
        latest = None
        for ev in sch.get("events") or []:
            st = _when(ev.get("startTime"))
            latest = st if st and (latest is None or st > latest) else latest
            if ev.get("type") != "match" or ev.get("state") not in ("unstarted", "inProgress") or not st:
                continue
            if st > end or (ev.get("state") == "unstarted" and st < now - timedelta(hours=6)):
                continue
            m = ev.get("match") or {}
            teams = [{"name": t.get("name"), "code": t.get("code")} for t in m.get("teams") or []]
            if len(teams) != 2 or any((t["code"] or "").upper() == "TBD" for t in teams):
                continue
            out.append({"match_id": m.get("id"), "league": (ev.get("league") or {}).get("name"),
                        "league_slug": (ev.get("league") or {}).get("slug"), "block": ev.get("blockName"),
                        "start": ev.get("startTime"), "state": ev.get("state"),
                        "best_of": (m.get("strategy") or {}).get("count"), "teams": teams})
        token = (sch.get("pages") or {}).get("newer")
        if not token or (latest and latest > end):
            break
    dedup = {e["match_id"]: e for e in out}
    return sorted(dedup.values(), key=lambda e: e["start"])


# ------------------------------------------------------------------ per-game stats (livestats feed)
def _iso10(t):
    """The feed's startingTime: a past time on a 10-second boundary."""
    t = t.replace(microsecond=0, second=t.second - t.second % 10)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _window(game_id, starting=None):
    r = net.get(f"{FEED}/window/{game_id}", params={"startingTime": starting} if starting else None, ok=(200, 204, 404))
    if r.status_code != 200 or not r.content:
        return None
    return r.json()


def clean_name(summoner, team_code=None):
    """'FUR Guigo' -> 'Guigo' (the feed prefixes the team code); 'C9 Loki ' -> 'Loki'."""
    s = " ".join(str(summoner or "").split())
    parts = s.split(" ", 1)
    if len(parts) == 2 and (team_code and parts[0].upper() == team_code.upper()
                            or re.fullmatch(r"[A-Z0-9]{1,5}", parts[0])):
        return parts[1]
    return s


def winner(blue, red):
    """Which side won a finished game, from its final frame: more inhibitors destroyed, then more towers, then more gold
    (a surrender can end a game before an inhibitor falls). Returns ('blue'|'red'|None, basis)."""
    for key in ("inhibitors", "towers", "totalGold"):
        b, r = blue.get(key) or 0, red.get(key) or 0
        if b != r:
            return ("blue" if b > r else "red"), f"inferred from {key}"
    return None, "not inferable"


def game_stats(game_id, now=None):
    """One finished game from the livestats feed, or None (not played / no feed / still running):
    {game_id, start, end, duration_s, patch, finished, sides: {blue: {team_id, kills, towers, inhibitors, gold},
    red: {...}}, winner_side, winner_basis, players: [{side, team_id, player_id, summoner, role, champion, kills, deaths,
    assists, cs, gold, level}]}. A finished game is cached for good."""
    hit = cache.get("le_game", str(game_id))
    if hit is not None:
        return hit
    first = _window(game_id)
    if not first or not first.get("frames"):
        return None
    t0 = _when(first["frames"][0].get("rfc460Timestamp"))
    now = now or datetime.now(timezone.utc)
    if t0 is None:
        return None
    at = min(t0 + timedelta(minutes=180), now - timedelta(seconds=60))
    last = _window(game_id, _iso10(at))
    frames = (last or {}).get("frames") or []
    fin = [f for f in frames if f.get("gameState") == "finished"]
    if not fin:
        return None                                    # still running (or the feed has no end): never cached
    end_frame = fin[-1]
    end = min(_when(f.get("rfc460Timestamp")) for f in fin)
    meta = (last or first).get("gameMetadata") or {}
    sides, players = {}, []
    for side in ("blue", "red"):
        tm = meta.get(f"{side}TeamMetadata") or {}
        tf = end_frame.get(f"{side}Team") or {}
        sides[side] = {"team_id": tm.get("esportsTeamId"), "kills": tf.get("totalKills"), "towers": tf.get("towers"),
                       "inhibitors": tf.get("inhibitors"), "barons": tf.get("barons"),
                       "dragons": len(tf.get("dragons") or []), "gold": tf.get("totalGold")}
        stats = {p.get("participantId"): p for p in tf.get("participants") or []}
        for pm in tm.get("participantMetadata") or []:
            p = stats.get(pm.get("participantId")) or {}
            players.append({"side": side, "team_id": tm.get("esportsTeamId"), "player_id": pm.get("esportsPlayerId"),
                            "summoner": pm.get("summonerName"), "role": ROLES.get(str(pm.get("role") or "").lower()),
                            "champion": pm.get("championId"), "kills": p.get("kills"), "deaths": p.get("deaths"),
                            "assists": p.get("assists"), "cs": p.get("creepScore"), "gold": p.get("totalGold"),
                            "level": p.get("level")})
    blue_tf, red_tf = end_frame.get("blueTeam") or {}, end_frame.get("redTeam") or {}
    w, basis = winner(blue_tf, red_tf)
    out = {"game_id": str(game_id), "start": t0.isoformat().replace("+00:00", "Z"),
           "end": end.isoformat().replace("+00:00", "Z") if end else None,
           "duration_s": (end - t0).total_seconds() if end else None, "patch": meta.get("patchVersion"),
           "finished": True, "sides": sides, "winner_side": w, "winner_basis": basis, "players": players}
    cache.put("le_game", str(game_id), out)
    return out


def matches_since(league_slugs=None, since=None):
    """Completed matches of the chosen leagues whose tournament ran on/after `since` (date):
    [{match_id, league, start, best_of, teams: [{name, code, game_wins}], game_ids}] (game_ids in game order)."""
    since = since or (datetime.now(timezone.utc) - timedelta(days=365)).date()
    out = []
    for slug, lid in league_ids(league_slugs).items():
        for t in tournaments(lid):
            try:
                end = datetime.strptime(t.get("endDate") or "2100-01-01", "%Y-%m-%d").date()
            except ValueError:
                continue
            if end < since:
                continue
            finished = end < (datetime.now(timezone.utc) - timedelta(days=2)).date()
            for ev in completed_events(t["id"], ttl=30 * DAY if finished else 1800):
                m = ev.get("match") or {}
                st = _when(ev.get("startTime"))
                if not st or st.date() < since:
                    continue
                out.append({"match_id": m.get("id"), "league": slug, "tournament": t.get("slug"),
                            "start": ev.get("startTime"), "best_of": (m.get("strategy") or {}).get("count"),
                            "teams": [{"name": x.get("name"), "code": x.get("code"),
                                       "game_wins": (x.get("result") or {}).get("gameWins")} for x in m.get("teams") or []],
                            "game_ids": [g.get("id") for g in ev.get("games") or []]})
    return out
