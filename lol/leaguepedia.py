"""Leaguepedia (lol.fandom.com) Cargo tables - the most complete LoL game history (every league, incl. the LPL that the
LoL Esports feed doesn't carry), with the official winner and exact game length.

Access: Cargo queries go through the MediaWiki API (lol.fandom.com/api.php, action=cargoquery). Fandom rate-limits
anonymous queries to nothing ("ratelimited" on the first request, checked 2026-10-17), so this logs in with a BOT
PASSWORD: on lol.fandom.com (logged in, email confirmed) open Special:BotPasswords, create one (grants: basic rights is
enough), then set
    LEAGUEPEDIA_USER          = YourWikiName@botname      (the login name the page shows)
    LEAGUEPEDIA_BOT_PASSWORD  = the generated password
as environment variables (never in a file). This is what mwrogue does after its first-run prompt; doing it here keeps
the server non-interactive and adds no dependency. Not set = Leaguepedia is skipped (the LoL Esports feed still works).

Limits: 500 rows per query (5,000 for wiki admins: LEAGUEPEDIA_LIMIT), paged with offset; "ratelimited" / "maxlag"
answers wait and retry. Field names below were checked against the live schema (api.php?action=cargofields, 2026-10-17).
Docs' gotchas followed: never filter on _pageName; never store Cargo row ids (they change on rebuilds); player names
go through PlayerRedirects (players rename).
"""
import os
import sys
import time

from . import cache, config, net

API = "https://lol.fandom.com/api.php"
LIMIT = int(os.environ.get("LEAGUEPEDIA_LIMIT") or 500)
ROLES = {"top": "top", "jungle": "jungle", "mid": "mid", "bot": "bot", "adc": "bot", "support": "support"}
net.set_gap("lol.fandom.com", 1.0)        # one request a second: a courtesy API with no stability guarantee

PLAYER_GAME_FIELDS = (
    "SP.Link=Link", "SP.Name=Name", "PR.OverviewPage=PlayerPage", "SP.Team=Team", "SP.TeamVs=TeamVs", "SP.Role=Role",
    "SP.Champion=Champion", "SP.Kills=Kills", "SP.Deaths=Deaths", "SP.Assists=Assists", "SP.CS=CS", "SP.Gold=Gold",
    "SP.TeamKills=TeamKills", "SP.PlayerWin=PlayerWin", "SP.Side=Side", "SP.GameId=GameId", "SP.MatchId=MatchId",
    "SG.DateTime_UTC=PlayedAt", "SG.N_GameInMatch=GameN", "SG.Gamelength_Number=Minutes", "SG.Team1=Team1",
    "SG.Team2=Team2", "SG.Team1Kills=Team1Kills", "SG.Team2Kills=Team2Kills", "SG.WinTeam=WinTeam", "SG.Patch=Patch",
    "MS.BestOf=BestOf", "T.Name=Tournament", "T.League=League", "L.League_Short=LeagueShort", "T.Region=Region")
PLAYER_GAME_TABLES = ("ScoreboardPlayers=SP, ScoreboardGames=SG, MatchSchedule=MS, Tournaments=T, Leagues=L, "
                      "PlayerRedirects=PR")
PLAYER_GAME_JOIN = ("SP.GameId=SG.GameId, SG.MatchId=MS.MatchId, SG.OverviewPage=T.OverviewPage, T.League=L.League, "
                    "SP.Link=PR.AllName")
# Leaguepedia's short league names -> the LoL Esports slugs the rest of lol/ uses (others stay as Leaguepedia writes them)
LEAGUE_SLUGS = {"LCK": "lck", "LPL": "lpl", "LEC": "lec", "LCS": "lcs", "LCP": "lcp", "CBLOL": "cblol-brazil",
                "WCS": "worlds", "Worlds": "worlds", "MSI": "msi", "FST": "first_stand", "First Stand": "first_stand",
                "EWC": "ewc_lol", "LCK CL": "lck_challengers_league", "EM": "emea_masters", "LFL": "lfl",
                "PRM": "primeleague", "PCS": "pcs", "VCS": "vcs", "LJL": "ljl-japan", "LTA N": "lta_n",
                "LTA S": "lta_s", "Demacia Cup": "demacia_cup"}


class LeaguepediaError(RuntimeError):
    pass


class NotConfigured(LeaguepediaError):
    pass


def _q(s):
    """A value inside a Cargo where clause (double quotes, escaped)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Leaguepedia:
    def __init__(self, credentials=None):
        self.creds = credentials or config.leaguepedia_credentials()
        if not self.creds:
            raise NotConfigured("Leaguepedia needs LEAGUEPEDIA_USER and LEAGUEPEDIA_BOT_PASSWORD (Special:BotPasswords)")
        self.sess = None

    def login(self):
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": config.USER_AGENT})
        tok = net.get(API, params={"action": "query", "meta": "tokens", "type": "login", "format": "json"}, sess=s)
        token = ((tok.json().get("query") or {}).get("tokens") or {}).get("logintoken")
        r = net.post(API, data={"action": "login", "lgname": self.creds[0], "lgpassword": self.creds[1],
                                "lgtoken": token, "format": "json"}, sess=s)
        res = (r.json().get("login") or {})
        if res.get("result") != "Success":
            raise LeaguepediaError(f"Leaguepedia login failed: {res.get('result')} {res.get('reason') or ''}".strip())
        self.sess = s

    def cargo(self, tables, fields, where=None, join_on=None, order_by=None, group_by=None, limit=LIMIT, offset=0):
        """One page of a Cargo query -> list of dicts (aliases as keys)."""
        if self.sess is None:
            self.login()
        params = {"action": "cargoquery", "format": "json", "tables": tables, "fields": ",".join(fields),
                  "limit": limit, "offset": offset, "maxlag": 5}
        for k, v in (("where", where), ("join_on", join_on), ("order_by", order_by), ("group_by", group_by)):
            if v:
                params[k] = v
        for attempt in range(5):
            body = net.get(API, params=params, sess=self.sess, timeout=60).json()
            err = body.get("error")
            if not err:
                return [row.get("title") or {} for row in body.get("cargoquery") or []]
            if err.get("code") in ("ratelimited", "maxlag"):
                wait = 30 * (attempt + 1)
                print(f"  Leaguepedia {err.get('code')}: waiting {wait} s", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            if err.get("code") in ("notloggedin", "badtoken"):
                self.login()
                continue
            raise LeaguepediaError(f"Cargo error {err.get('code')}: {err.get('info')}")
        raise LeaguepediaError("Cargo still rate-limited after 5 tries")

    def cargo_all(self, tables, fields, where=None, join_on=None, order_by=None, group_by=None, max_rows=200000):
        """Every page. The next page starts after the rows actually received, and only a page shorter than the smallest
        cap (500, an ordinary account) ends it - a LEAGUEPEDIA_LIMIT above the account's cap never stops it early."""
        out, offset = [], 0
        while offset < max_rows:
            page = self.cargo(tables, fields, where, join_on, order_by, group_by, LIMIT, offset)
            out += page
            if not page or len(page) < min(LIMIT, 500):
                break
            offset += len(page)
        return out

    # ------------------------------------------------------------------ the grader's queries
    def player_games(self, start, end, where=None):
        """Every scoreboard row (one per player per game) with a game start in [start, end) - dates."""
        w = f"SG.DateTime_UTC >= '{start} 00:00:00' AND SG.DateTime_UTC < '{end} 00:00:00'"
        if where:
            w += f" AND ({where})"
        return self.cargo_all(PLAYER_GAME_TABLES, PLAYER_GAME_FIELDS, where=w, join_on=PLAYER_GAME_JOIN,
                              order_by="SG.DateTime_UTC, SP.GameId, SP.Side, SP.Role_Number")

    def player_history(self, link, since):
        """One player's games since a date, by any of his names (PlayerRedirects)."""
        return self.player_games(since, "2100-01-01", where=f"PR.OverviewPage = {_q(link)}")

    def upcoming(self, start, end):
        """Scheduled matches in [start, end) - for events the LoL Esports schedule doesn't list."""
        name = f"upcoming:{start}:{end}"
        hit = cache.get("lp", name, 600)
        if hit is not None:
            return hit
        rows = self.cargo_all(
            "MatchSchedule=MS, Tournaments=T", ("MS.Team1=Team1", "MS.Team2=Team2", "MS.DateTime_UTC=Start",
                                                "MS.BestOf=BestOf", "MS.MatchId=MatchId", "T.Name=Tournament",
                                                "T.League=League"),
            where=f"MS.DateTime_UTC >= '{start}' AND MS.DateTime_UTC < '{end}'", join_on="MS.OverviewPage=T.OverviewPage",
            order_by="MS.DateTime_UTC")
        cache.put("lp", name, rows)
        return rows

    def roster(self, team):
        """The team's players in its most recent tournament (TournamentPlayers): [{Player, Role, Tournament, Date}]."""
        name = f"roster:{team}"
        hit = cache.get("lp", name, 12 * 3600)
        if hit is not None:
            return hit
        rows = self.cargo("TournamentPlayers=TP, Tournaments=T",
                          ("TP.Link=Player", "TP.Role=Role", "T.Name=Tournament", "T.DateStart=Date"),
                          where=f"TP.Team = {_q(team)}", join_on="TP.OverviewPage=T.OverviewPage",
                          order_by="T.DateStart DESC", limit=15)
        latest = rows[0].get("Tournament") if rows else None
        rows = [r for r in rows if r.get("Tournament") == latest]
        cache.put("lp", name, rows)
        return rows


# ------------------------------------------------------------------ Cargo rows -> history rows
def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _iso(dt):
    """Cargo '2026-09-06 08:00:00' (UTC) -> '2026-09-06T08:00:00Z'."""
    return (str(dt).strip().replace(" ", "T") + "Z") if dt else None


def to_history(rows):
    """Cargo player_games rows -> [(game_row, player_rows)] for history.save_game (source 'lp')."""
    from . import history
    games = {}
    for r in rows:
        if r.get("GameId"):
            games.setdefault(r["GameId"], []).append(r)
    out = []
    for gid, rs in games.items():
        g0 = rs[0]
        t1, t2 = g0.get("Team1"), g0.get("Team2")
        k1, k2 = _int(g0.get("Team1Kills")), _int(g0.get("Team2Kills"))
        minutes = g0.get("Minutes")
        try:
            dur = float(minutes) * 60 if minutes not in (None, "") else None
        except ValueError:
            dur = None
        winner = t1 if str(g0.get("WinTeam") or "") == str(t1) else t2 if str(g0.get("WinTeam") or "") == str(t2) else None
        game = {"game_key": f"lp:{gid}", "source": "lp", "match_key": f"lp:{g0.get('MatchId')}",
                "game_number": _int(g0.get("GameN")), "best_of": _int(g0.get("BestOf")), "played_at": _iso(g0.get("PlayedAt")),
                "league": LEAGUE_SLUGS.get(g0.get("LeagueShort") or "", g0.get("LeagueShort") or g0.get("League")),
                "tournament": g0.get("Tournament"), "blue_team": t1, "red_team": t2,
                "blue_team_id": None, "red_team_id": None, "blue_kills": k1, "red_kills": k2,
                "winner": winner, "winner_basis": "Leaguepedia" if winner else "no winner listed",
                "duration_s": dur, "patch": g0.get("Patch")}
        players = []
        for r in rs:
            team = r.get("Team")
            mine, theirs = (k1, k2) if team == t1 else (k2, k1) if team == t2 else (_int(r.get("TeamKills")), None)
            name = r.get("Name") or r.get("Link")
            players.append({
                "game_key": game["game_key"], "player": name, "player_key": history.player_key(name),
                "player_id": r.get("PlayerPage") or r.get("Link"), "team": team, "team_id": None,
                "opponent": r.get("TeamVs"), "opponent_id": None,
                "side": {"1": "blue", "2": "red"}.get(str(r.get("Side"))),
                "role": ROLES.get(str(r.get("Role") or "").lower()), "champion": r.get("Champion"),
                "kills": _int(r.get("Kills")), "deaths": _int(r.get("Deaths")), "assists": _int(r.get("Assists")),
                "cs": _int(r.get("CS")), "gold": _int(r.get("Gold")), "team_kills": mine, "opp_kills": theirs,
                "win": {"Yes": 1, "No": 0}.get(str(r.get("PlayerWin"))), "duration_s": dur,
                "match_key": game["match_key"], "game_number": game["game_number"], "best_of": game["best_of"],
                "played_at": game["played_at"], "league": game["league"], "source": "lp"})
        out.append((game, players))
    return out
