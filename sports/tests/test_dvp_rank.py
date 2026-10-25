"""Defense vs position, allowed per game + rank (sports/games/factors.py dvp_table / _dvp_rank) on a hand-built league
where every number is known: point in time (a game not ended before as_of never counts), ties, minimum games, NBA and
NFL groups, and the player-context rows the Matchup panel reads (dvp_allowed_pg, dvp_rank, n)."""
import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from sports import player_context
from sports.games import factors, store

AS_OF = datetime(2026, 1, 20, 12, tzinfo=timezone.utc)
T0 = datetime(2025, 11, 1, tzinfo=timezone.utc)
# what a guard / a centre scores AGAINST each defending team, every game -> allowed per game is exactly this
NBA_G_ALLOW = {"A": 10.0, "B": 20.0, "C": 20.0, "D": 30.0, "E": 5.0}
NBA_C_PTS = 7.0                                            # centres score the same against everyone -> all tied


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


class _League:
    def __init__(self, path, league):
        self.db, self.lg, self.n = store.connect(path), league, 0

    def game(self, home, away, start, players, ended=None, season=2026):
        """players: [(athlete_id, team, position, stats dict)]."""
        self.n += 1
        ev = f"ev{self.n}"
        self.db.execute("INSERT INTO sports_game_games (league, event_id, season, season_type, start, completed, home_id, "
                        "away_id, home_score, away_score, ended_at) VALUES (?,?,?,2,?,1,?,?,100,90,?)",
                        (self.lg, ev, season, _iso(start), home, away, _iso(ended or start + timedelta(hours=3))))
        for aid, team, pos, st in players:
            opp = away if team == home else home
            self.db.execute("INSERT INTO sports_game_players (league, event_id, athlete_id, team_id, opp_id, home, "
                            "position, starter, played, stats) VALUES (?,?,?,?,?,?,?,1,1,?)",
                            (self.lg, ev, aid, team, opp, int(team == home), pos, json.dumps(st)))
        self.db.commit()
        return ev


def _nba_players(home, away):
    out = []
    for team, opp in ((home, away), (away, home)):
        out.append((f"g{team}", team, "PG", {"points": NBA_G_ALLOW[opp], "minutes": 30}))
        out.append((f"c{team}", team, "C", {"points": NBA_C_PTS, "minutes": 30}))
        out.append((f"f{team}", team, "SF", {"minutes": 30}))          # no points recorded: never adds to the stat
    return out


@pytest.fixture
def nba(tmp_path):
    L = _League(tmp_path / "games.db", "nba")
    day = 0
    for _ in range(4):                                           # A-D round robin x4: 12 games each
        for h, a in itertools.combinations("ABCD", 2):
            L.game(h, a, T0 + timedelta(days=day), _nba_players(h, a))
            day += 1
    for _ in range(3):                                           # E: 3 games only (below the 10-game minimum)
        L.game("E", "A", T0 + timedelta(days=day), _nba_players("E", "A"))
        day += 1
    # started before as_of but ENDED after it: its result is not in yet -> must not count
    L.game("A", "B", AS_OF - timedelta(hours=1), [("gB", "B", "PG", {"points": 1000.0}),
                                                  ("gA", "A", "PG", {"points": 1000.0})],
           ended=AS_OF + timedelta(hours=2))
    # after as_of entirely
    L.game("A", "D", AS_OF + timedelta(days=1), [("gD", "D", "PG", {"points": 1000.0})])
    return L


def _data(L):
    return factors.LeagueData(L.db, L.lg, AS_OF)


def test_allowed_per_game_and_rank_known(nba):
    data = _data(nba)
    tab = factors.dvp_table(data, "points")
    g = tab["groups"]["G"]
    assert tab["teams"] == 4                                     # E is below the minimum -> not in the pool
    assert {t: g[t]["allowed_pg"] for t in "ABCD"} == {t: NBA_G_ALLOW[t] for t in "ABCD"}
    assert {t: g[t]["n"] for t in "ABCDE"} == {"A": 15, "B": 12, "C": 12, "D": 12, "E": 3}
    # 1 = fewest allowed; B and C tie at 20 -> both #2, D is #4 (competition ranking)
    assert {t: g[t]["rank"] for t in "ABCD"} == {"A": 1, "B": 2, "C": 2, "D": 4}


def test_ties_share_rank_and_forwards_with_no_stat_allow_zero(nba):
    tab = factors.dvp_table(_data(nba), "points")
    assert all(tab["groups"]["C"][t]["rank"] == 1 for t in "ABCD")
    assert all(tab["groups"]["C"][t]["allowed_pg"] == NBA_C_PTS for t in "ABCD")
    assert all(tab["groups"]["F"][t]["allowed_pg"] == 0.0 for t in "ABCD")


def test_minimum_games_no_rank_but_allowed_and_n(nba):
    data = _data(nba)
    assert factors.dvp_table(data, "points")["groups"]["G"]["E"] == {"allowed_pg": 5.0, "n": 3, "rank": None}
    out, missing = {}, {}
    factors._dvp_rank(data, "gA", "E", "points", out, missing)
    assert out["dvp_allowed_pg"] == 5.0 and out["dvp_n"] == 3 and "dvp_rank" not in out
    assert "fewer than 10" in missing["dvp_rank"]


def test_point_in_time_game_after_as_of_never_counts(nba):
    data = _data(nba)
    g = factors.dvp_table(data, "points")["groups"]["G"]
    assert g["A"]["allowed_pg"] == 10.0 and g["B"]["allowed_pg"] == 20.0 and g["D"]["n"] == 12
    # one day later the game that ended after as_of is in: A allowed 1000 to gB in it, B allowed 1000 to gA
    later = factors.LeagueData(nba.db, "nba", AS_OF + timedelta(hours=12))
    g2 = factors.dvp_table(later, "points")["groups"]["G"]
    assert g2["A"]["n"] == 16 and g2["A"]["allowed_pg"] == pytest.approx((15 * 10 + 1000) / 16)
    assert g2["D"]["n"] == 12                                    # the game after as_of is still out


def test_compute_payload_keys_and_memo(nba):
    data = _data(nba)
    pay = factors.compute(nba.db, data, "gA", "A", "B", "evnext", "2026-01-21T00:00:00Z", "points", home=True)
    f = pay["factors"]
    assert (f["dvp_allowed_pg"], f["dvp_rank"], f["dvp_n"], f["dvp_teams"], f["dvp_group"]) == (20.0, 2, 12, 4, "G")
    pay_c = factors.compute(nba.db, data, "cA", "A", "D", "evnext", "2026-01-21T00:00:00Z", "points", home=True)
    assert (pay_c["factors"]["dvp_rank"], pay_c["factors"]["dvp_group"]) == (1, "C")
    assert [k for k in data.memo if k[0] == "dvp_tab"] == [("dvp_tab", "points")]   # one table per market per board
    pra = factors.compute(nba.db, data, "gA", "A", "B", "evnext", "2026-01-21T00:00:00Z", "pra", home=True)
    assert "dvp_allowed_pg" not in pra["factors"]                # no rebounds/assists recorded -> no pra stat


def test_player_context_rows_for_matchup_panel(nba, monkeypatch, tmp_path):
    monkeypatch.setattr(player_context, "_games_db", lambda: store.connect(tmp_path / "games.db"))
    w = {"nba:player:espn:gA": {"team_id": "nba:team:espn:A", "opp_id": "nba:team:espn:D", "event_id": "nba:espn:x",
                                "start_utc": "2026-01-21T00:00:00Z", "home": True, "stats": ["points"]}}
    out, _ = player_context.build("nba", "basketball", _iso(AS_OF), w)
    rows = {r["key"]: r for r in out["nba:player:espn:gA"]["by_stat"]["points"]}
    assert rows["dvp_allowed_pg"]["value"] == 30.0 and rows["dvp_allowed_pg"]["n"] == 12
    assert rows["dvp_rank"]["value"] == 4 and rows["dvp_rank"]["n"] == 12 and rows["dvp_rank"]["teams"] == 4
    assert rows["dvp_rank"]["pos"] == "G" and "of 4" in rows["dvp_rank"]["label"]


def test_nfl_groups_qb_and_wr(tmp_path):
    L = _League(tmp_path / "games.db", "nfl")
    allow = {"A": 300.0, "B": 200.0, "C": 250.0}
    day = 0
    for _ in range(3):                                           # A, B, C round robin x3: 6 games each (min 4)
        for h, a in itertools.combinations("ABC", 2):
            ps = []
            for team, opp in ((h, a), (a, h)):
                ps.append((f"q{team}", team, "QB", {"passing.passingYards": allow[opp]}))
                ps.append((f"w{team}", team, "WR", {"receiving.receivingYards": allow[opp] / 2}))
                ps.append((f"f{team}", team, "FB", {"rushing.rushingYards": 10}))
            L.game(h, a, T0 + timedelta(days=7 * day), ps, season=2025)
            day += 1
    data = factors.LeagueData(L.db, "nfl", AS_OF)
    pay = factors.compute(L.db, data, "qA", "A", "B", "evnext", "2026-01-21T00:00:00Z", "pass_yds", home=True)
    f = pay["factors"]
    assert (f["dvp_allowed_pg"], f["dvp_rank"], f["dvp_n"], f["dvp_teams"], f["dvp_group"]) == (200.0, 1, 6, 3, "QB")
    pay = factors.compute(L.db, data, "wB", "B", "A", "evnext", "2026-01-21T00:00:00Z", "rec_yds", home=True)
    assert (pay["factors"]["dvp_allowed_pg"], pay["factors"]["dvp_rank"]) == (150.0, 3)
    tab = factors.dvp_table(data, "rush_yds")
    assert tab["groups"]["RB"]["A"]["allowed_pg"] == 10.0        # FB maps to the RB group
