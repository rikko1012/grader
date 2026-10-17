"""The LoL history store, the LoL Esports -> history mapping, the projection, the backtest and the scout - offline."""
import argparse
import math
import random
from datetime import date, timedelta

import numpy as np
import pytest

from lol import backtest, history, model, scout, sync


def _game(source, gid, day, n, blue, red, bk, rk, players, winner=None, match="M1", bo=3):
    """A game and its player rows. players: [(name, side, role, kills, deaths, assists, champion)]."""
    played = f"{day}T08:00:0{n}Z"
    g = {"game_key": f"{source}:{gid}", "source": source, "match_key": f"{source}:{match}", "game_number": n,
         "best_of": bo, "played_at": played, "league": "lck", "tournament": "t", "blue_team": blue, "red_team": red,
         "blue_kills": bk, "red_kills": rk, "winner": winner, "winner_basis": "test", "duration_s": 1800.0, "patch": "16.1"}
    rows = []
    for name, side, role, k, d, a, champ in players:
        mine, theirs = (bk, rk) if side == "blue" else (rk, bk)
        rows.append({"game_key": g["game_key"], "player": name, "player_id": f"{name}-{source}", "team": blue if side == "blue" else red,
                     "opponent": red if side == "blue" else blue, "side": side, "role": role, "champion": champ,
                     "kills": k, "deaths": d, "assists": a, "team_kills": mine, "opp_kills": theirs,
                     "win": None if winner is None else int(winner == (blue if side == "blue" else red)),
                     "match_key": g["match_key"], "game_number": n, "best_of": bo, "played_at": played, "league": "lck",
                     "source": source})
    return g, rows


# ------------------------------------------------------------------ history
def test_save_is_idempotent_and_reads_back(db):
    g, rows = _game("le", "1", "2026-07-29", 1, "T1", "Gen.G", 18, 9, [("Faker", "blue", "mid", 5, 1, 7, "Azir")])
    history.save_game(db, g, rows)
    history.save_game(db, g, rows)
    db.commit()
    assert len(history.player_games(db, "faker")) == 1
    assert history.known_matches(db, "le") == {"le:M1": 1}


def test_both_sources_one_game_and_one_identity(db):
    for src in ("le", "lp"):
        g, rows = _game(src, "1", "2026-07-29", 1, "T1", "Gen.G", 18, 9, [("Faker", "blue", "mid", 5, 1, 7, "Azir")])
        history.save_game(db, g, rows)
    g, rows = _game("le", "2", "2026-08-02", 1, "T1", "KT", 12, 3, [("Faker", "blue", "mid", 4, 0, 6, "Ahri")], match="M2")
    history.save_game(db, g, rows)
    db.commit()
    games = history.player_games(db, "faker")
    assert len(games) == 2 and games[0]["source"] == "lp"              # the shared game kept once, Leaguepedia's row
    assert {r["pid"] for r in games} == {"lp:Faker-lp"}                 # the feed id linked to the wiki page
    assert len(history.find_players(db, "Faker")) == 1


def test_namesakes_stay_apart(db):
    g, rows = _game("le", "1", "2026-07-29", 1, "T1", "Gen.G", 18, 9, [("Zeus", "blue", "top", 3, 1, 4, "Gnar")])
    history.save_game(db, g, rows)
    g, rows = _game("le", "9", "2026-07-30", 1, "Team A", "Team B", 10, 9, [("Zeus", "blue", "top", 1, 5, 1, "Jax")], match="M9")
    rows[0]["player_id"] = "another-zeus"
    history.save_game(db, g, rows)
    db.commit()
    cands = history.find_players(db, "Zeus")
    assert len(cands) == 2 and cands[0]["team"] == "Team A"
    assert len(history.player_games(db, "zeus", pid=cands[1]["pid"])) == 1


def test_team_games_by_identity(db):
    g, rows = _game("le", "1", "2026-07-29", 1, "Cloud9 Kia", "FURIA", 20, 4, [("Loki", "blue", "mid", 9, 0, 5, "Syndra"),
                                                                                ("Tutsz", "red", "mid", 1, 4, 2, "Ahri")])
    history.save_game(db, g, rows)
    db.commit()
    tg = history.team_games(db, "Cloud9")
    assert len(tg) == 1 and tg[0]["team_kills"] == 20 and tg[0]["opp_kills"] == 4
    assert history.team_games(db, "FURIA Esports")[0]["team_kills"] == 4


def test_postgres_history(pg_db):
    g, rows = _game("le", "1", "2026-07-29", 1, "T1", "Gen.G", 18, 9, [("Faker", "blue", "mid", 5, 1, 7, "Azir")])
    history.save_game(pg_db, g, rows)
    history.save_game(pg_db, g, rows)
    pg_db.commit()
    assert len(history.player_games(pg_db, "faker")) == 1
    assert history.team_games(pg_db, "T1")[0]["opp_kills"] == 9
    history.set_state(pg_db, "le:last_sync", "x")
    assert history.get_state(pg_db, "le:last_sync")["value"] == "x"


# ------------------------------------------------------------------ LoL Esports -> history
def _details(score_blue, score_red, games):
    return {"match": {"teams": [{"id": "B", "name": "Blue Team", "code": "BLU", "result": {"gameWins": score_blue}},
                                {"id": "R", "name": "Red Team", "code": "RED", "result": {"gameWins": score_red}}],
                      "games": [{"number": i + 1, "id": f"g{i + 1}", "state": "completed"} for i in range(games)]}}


def _stats(winner_side):
    return {"start": "2026-09-06T08:00:00Z", "duration_s": 1900.0, "patch": "16.1", "winner_side": winner_side,
            "winner_basis": "inferred from inhibitors",
            "sides": {"blue": {"team_id": "B", "kills": 15}, "red": {"team_id": "R", "kills": 6}},
            "players": [{"side": "blue", "team_id": "B", "player_id": "p1", "summoner": "BLU Ace", "role": "mid",
                         "champion": "Ahri", "kills": 6, "deaths": 1, "assists": 4, "cs": 300, "gold": 1}]}


def _winners(score_blue, score_red, inferred, in_feed=None):
    m = {"match_id": "X", "best_of": 3, "start": "2026-09-06T08:00:00Z", "league": "lck"}
    n = in_feed if in_feed is not None else len(inferred)
    det = _details(score_blue, score_red, n)
    return sync.lolesports_rows(m, det, {f"g{i + 1}": _stats(w) for i, w in enumerate(inferred[:n])})


def test_rows_from_the_feed():
    (g, players), _ = _winners(2, 0, ["blue", "blue"])
    p = players[0]
    assert (p["player"], p["team"], p["opponent"], p["team_kills"], p["opp_kills"], p["win"]) == \
        ("Ace", "Blue Team", "Red Team", 15, 6, 1)


@pytest.mark.parametrize("score,inferred,want,basis", [
    ((2, 0), ["blue", "red"], ["Blue Team", "Blue Team"], "series sweep"),               # a sweep is certain
    ((2, 1), ["blue", "red", "blue"], ["Blue Team", "Red Team", "Blue Team"], "matches the series score"),
    ((2, 1), ["blue", "blue", "blue"], [None, None, "Blue Team"], "disagree"),           # only the last game is certain
    ((1, 1), ["blue", "red"], ["Blue Team", "Red Team"], "matches the series score"),    # a drawn best-of-2
    ((1, 1), ["blue", "blue"], [None, None], "disagree"),
])
def test_game_winners(score, inferred, want, basis):
    rows = _winners(*score, inferred)
    assert [g["winner"] for g, _ in rows] == want
    assert basis in rows[0][0]["winner_basis"]
    for (g, players) in rows:
        assert players[0]["win"] == (None if g["winner"] is None else int(g["winner"] == "Blue Team"))


def test_incomplete_series_keeps_no_winner():
    rows = _winners(2, 0, ["blue", "blue"], in_feed=1)
    assert [g["winner"] for g, _ in rows] == [None] and "incomplete" in rows[0][0]["winner_basis"]


# ------------------------------------------------------------------ the model
PRI = {"kills": {r: v for r, v in zip(model.ROLES, (2.0, 3.0, 4.0, 5.0, 1.0))},
       "assists": {r: 6.0 for r in model.ROLES}, "deaths": {r: 3.0 for r in model.ROLES}, "team_kills": 14.0}
PAR = {p: {"form": "rate", "hl": 120.0, "k0": 8.0, "ko": 10.0, "kt": 10.0} for p in ("kills", "assists", "deaths")}
RR = {(p, s): 6.0 for p in ("kills", "assists", "deaths") for s in ("1", "12", "123")}


def test_no_history_is_the_role_prior():
    pj = model.project([], [], [], "mid", 1000, "12", "kills", PAR, PRI, RR)
    assert pj["lambda"] == pytest.approx(8.0) and pj["opp_factor"] == pytest.approx(1.0)
    assert pj["data_quality"].startswith("no history")


def test_history_moves_toward_the_player_and_only_earlier_days_count():
    hist = [(990 - i, 9, 2, 5, 18, 10) for i in range(40)]
    pj = model.project(hist, [], [], "mid", 1000, "1", "kills", PAR, PRI, RR)
    assert 7.5 < pj["lambda"] < 9.0                                   # (31.6 x 9 + 8 x 4) / (31.6 + 8) weighted games
    later = hist + [(1000, 0, 0, 0, 0, 0)] * 50                         # same-day / later games are ignored
    assert model.project(later, [], [], "mid", 1000, "1", "kills", PAR, PRI, RR)["lambda"] == pytest.approx(pj["lambda"])


def test_opponent_that_concedes_more_raises_kills_and_scoring_raises_deaths():
    leaky = [(990, 10, 25)] * 30                                        # concedes 25 a game (avg 14)
    sharp = [(990, 25, 8)] * 30                                         # scores 25 a game
    base = model.project([], [], [], "bot", 1000, "12", "kills", PAR, PRI, RR)["lambda"]
    assert model.project([], [], leaky, "bot", 1000, "12", "kills", PAR, PRI, RR)["lambda"] > base
    d0 = model.project([], [], [], "bot", 1000, "12", "deaths", PAR, PRI, RR)["lambda"]
    assert model.project([], [], sharp, "bot", 1000, "12", "deaths", PAR, PRI, RR)["lambda"] > d0


def test_share_form_uses_team_strength():
    par = {p: dict(PAR[p], form="share") for p in PAR}
    me = [(990, 4, 2, 6, 16, 12)] * 20
    strong = [(990, 22, 8)] * 30
    weak = [(990, 8, 20)] * 30
    a = model.project(me, strong, [], "mid", 1000, "1", "kills", par, PRI, RR)["lambda"]
    b = model.project(me, weak, [], "mid", 1000, "1", "kills", par, PRI, RR)["lambda"]
    assert a > b


def test_probabilities():
    o, u, p = model.probs(7.3, 6.0, 7.5)
    assert p == 0 and o + u == pytest.approx(1)
    o, u, p = model.probs(7.0, 6.0, 7.0)
    assert p > 0 and o + u + p == pytest.approx(1)
    assert model.probs(7.0, 1e6, 6.5)[0] == pytest.approx(1 - sum(math.exp(-7) * 7 ** k / math.factorial(k) for k in range(7)), abs=1e-4)
    lo, hi = model.interval(8.0, 6.0, 0.8)
    mass = sum(model.nb_pmf(k, 8.0, 6.0) for k in range(lo, hi + 1))
    assert mass >= 0.8 and lo < 8 < hi


def test_no_fit_refuses_to_project(monkeypatch):
    monkeypatch.setattr(model, "PARAMS", None)
    with pytest.raises(RuntimeError):
        model.project([], [], [], "mid", 1000)


# ------------------------------------------------------------------ backtest on a synthetic league
def _league(seed=7, days=420):
    """Six teams, five players each, steady skill: a model that learns players must beat the role average."""
    rnd = random.Random(seed)
    np_r = np.random.default_rng(seed)
    teams_ = [f"Team{i}" for i in range(6)]
    skill = {(t, r): rnd.uniform(0.6, 1.5) for t in teams_ for r in model.ROLES}
    base = dict(zip(model.ROLES, (2.4, 2.8, 3.6, 4.6, 0.9)))
    games, start = [], date(2025, 1, 1)
    gid = 0
    for d in range(0, days, 3):
        rnd.shuffle(teams_)
        for a, b in ((teams_[0], teams_[1]), (teams_[2], teams_[3]), (teams_[4], teams_[5])):
            gid += 1
            for n in (1, 2):
                ka = {r: int(np_r.poisson(base[r] * skill[(a, r)])) for r in model.ROLES}
                kb = {r: int(np_r.poisson(base[r] * skill[(b, r)])) for r in model.ROLES}
                players = [(f"{a}_{r}", "blue", r, ka[r], 3, 6, f"c{n}{r}") for r in model.ROLES] + \
                          [(f"{b}_{r}", "red", r, kb[r], 3, 6, f"c{n}{r}") for r in model.ROLES]
                games.append(_game("le", f"{gid}-{n}", (start + timedelta(days=d)).isoformat(), n, a, b,
                                   sum(ka.values()), sum(kb.values()), players, match=f"m{gid}"))
    return games


def test_backtest_learns_players_on_a_synthetic_league(db, monkeypatch):
    for g, rows in _league():
        history.save_game(db, g, rows)
    db.commit()
    monkeypatch.setattr(backtest, "GRID", {"hl": (120.0, 480.0), "k0": (2.0, 8.0), "ko": (10.0, 80.0), "kt": (10.0,),
                                         "ew": (0.0, 1.0)})
    data = backtest.Data(history.all_player_games(db))
    fit, rep = backtest.run(data, date(2025, 3, 1), date(2025, 12, 1), log=lambda m: None)
    r = rep[("kills", "12")]
    assert r["n"] > 100 and r["mae"] < r["mae_role_average"]
    assert abs(r["bias"]) < 0.5
    assert set(fit["params"]) == {"kills", "assists", "deaths"} and "kills|12" in fit["nb_size"]
    priors = fit["priors"]["kills"]
    assert priors["bot"] > priors["support"]


# ------------------------------------------------------------------ scout
def test_series_totals_need_every_game():
    rows = [{"match_key": "a", "game_number": 1, "kills": 3, "opponent": "X", "played_at": "2026-01-02", "win": 1},
            {"match_key": "a", "game_number": 2, "kills": 4, "opponent": "X", "played_at": "2026-01-02", "win": 0},
            {"match_key": "b", "game_number": 1, "kills": 9, "opponent": "Y", "played_at": "2026-01-05", "win": 1}]
    s12 = scout.series_totals(rows, "kills", "12")
    assert [x["total"] for x in s12] == [7]
    assert [x["total"] for x in scout.series_totals(rows, "kills", "1")] == [9, 3]


def test_grade_lines_respects_offered_sides():
    pj = {("kills", "12"): {"lambda": 4.0, "nb_size": 6.0}}
    out = scout.grade_lines(pj, [{"book": "prizepicks", "prop": "kills", "scope": "12", "line": 7.5, "odds_type": "demon",
                                  "under_available": False},
                                 {"book": "underdog", "prop": "assists", "scope": "123", "line": 20.5}])
    assert out[0]["p_under"] > 0.5 and out[0]["model_side"] == "under - not offered on this line (no play)"
    over = scout.grade_lines(pj, [{"book": "underdog", "prop": "kills", "scope": "12", "line": 2.5, "odds_type": "balanced",
                                   "under_available": False}])
    assert over[0]["model_side"] == "over"                                    # preferred side offered: shown as is
    assert out[1]["graded"] is False


def test_full_offline(db, monkeypatch):
    for g, rows in _league(days=60):
        history.save_game(db, g, rows)
    db.commit()
    monkeypatch.setattr(model, "PARAMS", PAR)
    monkeypatch.setattr(model, "PRIORS", PRI)
    monkeypatch.setattr(model, "R", RR)
    monkeypatch.setattr(scout.lolesports, "slate", lambda **k: [
        {"match_id": "x", "league": "LCK", "start": "2026-10-20T08:00:00Z", "best_of": 3, "state": "unstarted",
         "teams": [{"name": "Team1", "code": "T1X"}, {"name": "Team2", "code": "T2X"}]}])
    monkeypatch.setattr(scout.odds, "match_odds", lambda *a, **k: {"found": False})
    monkeypatch.setattr(scout.lines, "lines_for", lambda *a, **k: [
        {"book": "prizepicks", "player": "Team1_mid", "prop": "kills", "scope": "12", "line": 6.5, "odds_type": "standard",
         "under_available": True}])
    args = argparse.Namespace(name="Team1_mid", team=None, opponent=None, line=None, prop="kills", scope=None,
                              line_type="standard", hours=36)
    res = scout.run_full(db, args)
    assert res["match"]["opponent"]["name"] == "Team2" and res["inputs"]["opponent_found"]
    ln, = res["lines"]
    assert ln["graded"] and 0 < ln["p_over"] < 1 and ln["projection"] == pytest.approx(res["projection"]["kills|12"]["lambda"], abs=0.01)


# ------------------------------------------------------------------ Elo
def test_elo_uses_only_earlier_days_and_rewards_winners():
    from lol import elo
    games = [(d, 0, "Winners", "Losers", 1) for d in range(100, 120)]
    e = elo.Elo(games)
    assert e.p_win("Winners", "Losers", 100) == pytest.approx(0.5)          # nothing before day 100
    assert e.p_win("Winners", "Losers", 121) > 0.75
    assert e.p_win("Losers", "Winners", 121) == pytest.approx(1 - e.p_win("Winners", "Losers", 121))
    assert e.p_win("Cloud9", "Losers", 121) == e.p_win("Cloud9 Kia", "Losers", 121)   # a sponsor rename is one team


def test_strength_factor():
    assert model.strength(None, 0.5, 2.5, 1.0) == 1.0 and model.strength(0.7, 0.5, 2.5, 0.0) == 1.0
    assert model.strength(0.7, 0.5, 2.5, 1.0) > 1.0 > model.strength(0.3, 0.5, 2.5, 1.0)
    assert model.strength(0.7, 0.5, 0.4, 1.0) < 1.0                          # deaths: fewer when more likely to win
