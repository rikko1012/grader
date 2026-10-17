"""Checks that need the live sources - run where the Leaguepedia secrets live (the GitHub Actions job,
.github/workflows/lol-full-history.yml). They print counts and field names only, never a secret.

  python -m lol.checks leaguepedia   log in, read one week, check the fields / rows per game / sides, list the league
                                     short names Leaguepedia uses and how lol/leaguepedia.LEAGUE_SLUGS maps them
  python -m lol.checks crosscheck    every game both sources hold: team kills, each player's K / D / A, and the feed's
                                     winners (certain and inferred) against Leaguepedia's official winner; plus the
                                     games only one source has, by league
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import history, leaguepedia, teams

EXPECTED = {"Link", "Name", "PlayerPage", "Team", "TeamVs", "Role", "Champion", "Kills", "Deaths", "Assists", "CS", "Gold",
            "TeamKills", "PlayerWin", "Side", "GameId", "MatchId", "PlayedAt", "GameN", "Minutes", "Team1", "Team2",
            "Team1Kills", "Team2Kills", "WinTeam", "Patch", "BestOf", "Tournament", "League", "LeagueShort", "Region",
            "PlayedAt__precision"}                  # Cargo adds a datetime field's precision on its own


def _ts(s):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")[:19]).timestamp()
    except (TypeError, ValueError):
        return 0.0


def check_leaguepedia(days=7):
    lp = leaguepedia.Leaguepedia()
    lp.login()
    print("login: Success")
    end = date.today() + timedelta(days=1)
    rows = lp.player_games(end - timedelta(days=days), end)
    games = defaultdict(list)
    for r in rows:
        games[r.get("GameId")].append(r)
    keys = set().union(*(r.keys() for r in rows)) if rows else set()
    print(f"one week: {len(rows)} player rows, {len(games)} games")
    print(f"  fields missing: {sorted(EXPECTED - keys) or 'none'}; unexpected: {sorted(keys - EXPECTED) or 'none'}")
    print(f"  rows per game: {dict(Counter(len(v) for v in games.values()))}")
    side = Counter()
    for r in rows:
        want = r.get("Team1") if str(r.get("Side")) == "1" else r.get("Team2") if str(r.get("Side")) == "2" else None
        side["Side matches Team1/Team2" if want == r.get("Team") else "Side does NOT match"] += 1
    print(f"  {dict(side)}")
    print(f"  sample row keys/values (no player identifiers): "
          f"{ {k: rows[0].get(k) for k in ('PlayedAt', 'GameN', 'Minutes', 'BestOf', 'League', 'LeagueShort', 'Side')} if rows else {} }")
    short = lp.cargo_all("Tournaments=T, Leagues=L", ("L.League_Short=Short", "L.League=League", "COUNT(*)=N"),
                         where="T.DateStart >= '2025-01-01'", join_on="T.League=L.League",
                         group_by="L.League_Short, L.League")
    print("league short names since 2025 (tournaments) -> slug used by lol/:")
    for s in sorted(short, key=lambda x: -int(x.get("N") or 0))[:60]:
        print(f"  {str(s.get('Short')):14s} {str(s.get('N')):>4s}  {str(s.get('League'))[:45]:45s} -> "
              f"{leaguepedia.LEAGUE_SLUGS.get(s.get('Short') or '', '(unmapped)')}")


def crosscheck(db):
    games = db.query("SELECT * FROM lol_games")
    lp = defaultdict(list)
    for g in games:
        if g["source"] == "lp":
            lp[(g["game_number"], frozenset((teams.key(g["blue_team"]), teams.key(g["red_team"]))))].append(g)
    players = defaultdict(list)
    for r in db.query("SELECT game_key, player_key, champion, team, kills, deaths, assists FROM lol_player_games"):
        players[r["game_key"]].append(r)
    c, bad = Counter(), []
    le_only = Counter()
    for g in (x for x in games if x["source"] == "le"):
        key = (g["game_number"], frozenset((teams.key(g["blue_team"]), teams.key(g["red_team"]))))
        near = [x for x in lp.get(key, ()) if history._near(x["played_at"], g["played_at"])]
        twin = min(near, key=lambda x: abs(_ts(x["played_at"]) - _ts(g["played_at"])), default=None)  # a rematch the
        # next day is another series: the nearest start is the same game
        if twin is None:
            le_only[g["league"]] += 1
            continue
        c["games held by both"] += 1
        kills_le = {teams.key(g["blue_team"]): g["blue_kills"], teams.key(g["red_team"]): g["red_kills"]}
        kills_lp = {teams.key(twin["blue_team"]): twin["blue_kills"], teams.key(twin["red_team"]): twin["red_kills"]}
        c["team kills agree" if kills_le == kills_lp else "team kills differ"] += 1
        if g["winner"] and twin["winner"]:
            same = teams.key(g["winner"]) == teams.key(twin["winner"])
            kind = "certain" if str(g["winner_basis"]).startswith(("series sweep", "last game")) else "inferred"
            c[f"{kind} winner {'agrees' if same else 'DIFFERS'}"] += 1
            if not same and len(bad) < 15:
                bad.append(("winner", g["league"], g["played_at"], g["blue_team"], g["red_team"], g["game_number"],
                            g["winner_basis"]))
        elif twin["winner"]:
            c["feed winner unknown, Leaguepedia knows it"] += 1
        lp_rows = {(r["player_key"], history.champion(r["champion"])): r for r in players.get(twin["game_key"], ())}
        for r in players.get(g["game_key"], ()):
            t = lp_rows.get((r["player_key"], history.champion(r["champion"])))
            if t is None:
                by_champ = [x for x in players.get(twin["game_key"], ()) if history.champion(x["champion"]) ==
                            history.champion(r["champion"])]
                c["player name differs (champion matched)" if by_champ else "player row unmatched"] += 1
                t = by_champ[0] if by_champ else None
            if t is not None:
                ok = (r["kills"], r["deaths"], r["assists"]) == (t["kills"], t["deaths"], t["assists"])
                c["player K/D/A agree" if ok else "player K/D/A differ"] += 1
                if not ok and len(bad) < 15:
                    bad.append(("kda", g["league"], g["played_at"], r["player_key"], (r["kills"], r["deaths"], r["assists"]),
                                (t["kills"], t["deaths"], t["assists"])))
    le_by = defaultdict(list)
    for g in games:
        if g["source"] == "le":
            le_by[(g["game_number"], frozenset((teams.key(g["blue_team"]), teams.key(g["red_team"]))))].append(g["played_at"])
    lp_only = Counter(g["league"] for g in games if g["source"] == "lp" and not any(       # the same +-1 day window
        history._near(t, g["played_at"]) for t in
        le_by.get((g["game_number"], frozenset((teams.key(g["blue_team"]), teams.key(g["red_team"])))), ())))
    print(json.dumps({"agreement": dict(c), "examples": bad, "feed_only_games_by_league": dict(le_only.most_common()),
                      "leaguepedia_only_games_by_league_top": dict(lp_only.most_common(25))}, indent=1, default=str))


def main(argv=None):
    cmd = (argv or sys.argv[1:] or [""])[0]
    if cmd == "leaguepedia":
        check_leaguepedia()
    elif cmd == "crosscheck":
        db = history.DB()
        try:
            crosscheck(db)
        finally:
            db.close()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
