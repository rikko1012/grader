"""The consolidated LoL extension: one identity across both sources (link + merge), one history (the data center's
export), one forecast format (saved pre-match, append-only), slip checks, settlement of saved forecasts, and the
A1-A3 comparison harness. Every team, player and number is a TEST FIXTURE (no network)."""
import json
import random
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from lol import backtest, history, model
from lol.center import build, export, forecast, markets, match, settle, store
from lol.tests.test_center import UD, load_world, put


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


def le_copy_of_m1(con):
    """LoL Esports' copy of the Leaguepedia series M1 (same day, same teams, same players by role)."""
    teams = [{"id": "LA", "name": "Alpha Esports", "code": "ALP", "game_wins": 2}, {"id": "LB", "name": "Beta Gaming", "code": "BET", "game_wins": 0}]
    games = [{"number": 1, "id": "e1", "state": "completed", "sides": {}}, {"number": 2, "id": "e2", "state": "completed", "sides": {}},
             {"number": 3, "id": "e3", "state": "unneeded", "sides": {}}]
    ev = lambda r: "2026-10-10 09:00:00"
    store.put_rows(con, "LE:Match", [{"match_id": "X1", "league_slug": "tl", "league": "Test League", "start": "2026-10-10T09:00:00Z",
                                      "best_of": 3, "teams": teams, "games": games}], lambda r: r["match_id"], ev, None, "2026-10-11T00:00:00Z")
    roles = ["top", "jungle", "mid", "bot", "support"]
    for n, blue in ((1, "LA"), (2, "LB")):
        red = "LB" if blue == "LA" else "LA"
        store.put_rows(con, "LE:Game", [{"game_id": f"e{n}", "match_id": "X1", "number": n, "start": f"2026-10-10T0{8 + n}:05:00Z",
                                         "duration_s": 1800, "patch": "16.20.1", "blue_team_id": blue, "red_team_id": red, "blue_kills": 10,
                                         "red_kills": 4, "winner_side": "blue" if blue == "LA" else "red"}], lambda r: r["game_id"], ev, None,
                       "2026-10-11T00:00:00Z")
        # the same box scores as Leaguepedia's copy (test_center.load_world): cross-source links need them to agree
        kills = {(1, "LA"): (1, 2, 3, 4, 0), (1, "LB"): (0, 1, 1, 2, 0), (2, "LB"): (0, 1, 2, 1, 0), (2, "LA"): (3, 1, 4, 2, 1)}
        rows = [{"game_id": f"e{n}", "match_id": "X1", "number": n, "player_id": f"{t}{i}", "name": f"le{t}{i}", "team_id": t,
                 "side": "blue" if t == blue else "red", "role": rl, "kills": kills[(n, t)][i], "deaths": 1, "assists": 2, "cs": 200}
                for t in ("LA", "LB") for i, rl in enumerate(roles)]
        store.put_rows(con, "LE:PlayerGame", rows, lambda r: f"{r['game_id']}|{r['player_id']}", ev, None, "2026-10-11T00:00:00Z")
    con.commit()


def test_le_first_then_leaguepedia_merges_into_one_identity(con):
    le_copy_of_m1(con)
    build.build(con)
    le_mid = store.find_entity(con, "player", "lolesports:player", "LA2")
    assert le_mid and con.execute("SELECT COUNT(*) FROM c_player_games").fetchone()[0] == 20       # LE only so far
    load_world(con)                                                     # Leaguepedia now holds the same series
    out = build.build(con)
    assert out["cross_source_links"] > 0 and out["lolesports"].get("left_out_leaguepedia_has_it") == 1
    lp_mid = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    assert lp_mid == le_mid == store.canonical_id(con, le_mid)          # one id; the older (LE) id survives
    assert store.find_entity(con, "team", "lolesports:team", "LA") == store.find_entity(con, "team", "leaguepedia:page", "Alpha Esports")
    pids = {r[0] for r in con.execute("SELECT DISTINCT pid FROM c_player_games WHERE name_shown = 'AlphMid'")}
    assert pids == {le_mid} and con.execute("SELECT COUNT(*) FROM c_player_games").fetchone()[0] == 20   # one copy of the series


def test_export_is_the_one_history_with_canonical_ids(con, tmp_path):
    load_world(con)
    build.build(con)
    db = history.DB(dsn="", path=tmp_path / "history.db")
    c = export.export(con, db)
    assert c["games"] == 2 and c["player_rows"] == 20
    pid = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    rows = history.player_games(db, history.player_key("AlphMid"), pid=pid)
    assert len(rows) == 2 and all(r["player_id"] == pid and r["pid"] == pid for r in rows)
    assert {r["role"] for r in rows} == {"mid"} and rows[0]["team_id"].startswith("lol:team:")
    g = db.query("SELECT * FROM lol_games ORDER BY game_number")
    assert [x["game_number"] for x in g] == [1, 2] and g[0]["winner"] == "Alpha Esports" and g[0]["match_key"].startswith("lol:series:")
    export.export(con, db)                                              # a rewrite, never duplicates
    assert db.query("SELECT COUNT(*) AS n FROM lol_player_games")[0]["n"] == 20


PRI = {"kills": {r: v for r, v in zip(model.ROLES, (2.0, 3.0, 4.0, 5.0, 1.0))}, "assists": {r: 6.0 for r in model.ROLES},
       "deaths": {r: 3.0 for r in model.ROLES}, "team_kills": 14.0}
PAR = {p: {"form": "rate", "hl": 120.0, "k0": 8.0, "ko": 10.0, "kt": 10.0} for p in ("kills", "assists", "deaths")}
RR = {(p, s): 6.0 for p in ("kills", "assists", "deaths") for s in ("1", "12", "123")}


def _future_world(con, tmp_path, monkeypatch, days_ahead=2):
    load_world(con)
    start = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).replace(microsecond=0)
    put(con, "MatchSchedule", [{"MatchId": "M9", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "BestOf": "3", "DateTime_UTC": start.strftime("%Y-%m-%d %H:%M:%S")}], at="2026-10-15T00:00:00Z")
    build.build(con)
    db = history.DB(dsn="", path=tmp_path / "history.db")
    export.export(con, db)
    monkeypatch.setattr(model, "FIT", ({}, PAR, RR, {}))
    monkeypatch.setattr(model, "PARAMS", PAR)
    monkeypatch.setattr(model, "PRIORS", PRI)
    monkeypatch.setattr(model, "R", RR)
    raw = dict(UD, games=[dict(UD["games"][0], scheduled_at=start.strftime("%Y-%m-%dT%H:%M:%SZ"))])
    raw["over_under_lines"] = [dict(raw["over_under_lines"][0], over_under={"appearance_stat": {
        "appearance_id": "a1", "stat": "kills_on_maps_1_2", "display_stat": "Kills on Maps 1+2"}},
        options=[{"choice": "higher", "status": "active", "payout_multiplier": "1.1"}, {"choice": "lower", "status": "active", "payout_multiplier": "0.9"}])]
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(raw).encode()))
    match.link_open_markets(con)
    return db, start, raw


def test_forecast_saved_once_per_state_and_read_by_board(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    assert con.execute("SELECT status FROM market_links").fetchone()[0] == "matched"
    assert forecast.run(con, db)["saved"] == 1
    assert forecast.run(con, db).get("unchanged") == 1                 # same offer, inputs and fit: no new record
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    assert f["scope"] == "12" and f["best_of"] == 3 and 0 < f["p_over"] < 1 and abs(f["p_over"] + f["p_under"] + f["p_push"] - 1) < 1e-3
    assert f["forecast_at"] < f["series_start"] and f["pid"] == store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    assert "format_scope_mismatch" not in json.loads(f["flags"])
    raw["over_under_lines"][0]["stat_value"] = "7.5"                     # the line moves -> a new record, the old one kept
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(raw).encode()))
    assert forecast.run(con, db)["saved"] == 1 and con.execute("SELECT COUNT(*) FROM lol_forecasts").fetchone()[0] == 2
    b = forecast.board(con)
    row, = b["rows"]
    fc = row["forecast"]
    assert fc["line"] == 7.5 and fc["current"] and row["stake"] == 0.0
    p_side = fc["p_over"] if fc["model_side"] == "over" else fc["p_under"]
    g, u = forecast.grade_and_units(p_side)
    assert row["grade"] == (f"Lean {fc['model_side'].title()}" if g == "Lean" else g) and fc["units_policy"] == u
    assert (fc["stake_restriction"] is not None) == (u > 0)
    assert forecast.grade_and_units(0.81) == ("A+", 3.0) and forecast.grade_and_units(0.551) == ("B-", 0.5)
    assert forecast.grade_and_units(0.549) == ("Lean", 0.0)
    rec = dict(con.execute("SELECT * FROM lol_forecasts ORDER BY id DESC").fetchone())
    inp = json.loads(rec["inputs"])
    lo, hi = inp["range80"]
    assert lo <= inp["median"] <= hi and inp["drivers"] and inp["market"]["price_status"] in ("undocumented_two_sided_price", "one_side_offered")
    assert b["match_tables"] and b["released_to_members"] is False
    elig = forecast.slip_check(con, [f["id"]])
    assert not elig["eligible"] and any("line moved" in x for x in elig["reasons"])
    assert any("not yet validated" in x for x in json.loads(f["slip_reasons"]))


def test_saved_forecast_settles_when_the_series_completes(con, tmp_path, monkeypatch):
    from lol.tests.test_center import game_rows
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    day = start.strftime("%Y-%m-%d")
    for n in (1, 2):
        sg, sp = game_rows("M9", n, "Alpha Esports", "Beta Gaming", f"{day} 2{n}:00:00")
        put(con, "ScoreboardGames", [sg])
        put(con, "ScoreboardPlayers", sp)
    put(con, "MatchScheduleGame", [{"GameId": f"M9_{n}", "MatchId": "M9", "N_GameInMatch": str(n), "Match_DateTime_UTC": f"{day} 20:00:00"} for n in (1, 2)])
    put(con, "MatchSchedule", [{"MatchId": "M9", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "BestOf": "3", "Team1Score": "2", "Team2Score": "0", "Winner": "1",
                                "DateTime_UTC": start.strftime("%Y-%m-%d %H:%M:%S")}], at="2026-10-16T00:00:00Z")
    build.build(con)
    out = forecast.settle_all(con)
    s = dict(con.execute("SELECT * FROM lol_settlements").fetchone())
    assert out == {"graded": 1} and s["line"] == 6.5 and s["actual"] == 6 and s["over_hit"] == 0 and s["version"] == 1   # 3 + 3 kills < 6.5


def _synthetic_2026(seed=3):
    """Six TEST teams, steady skills, best-of-3 and best-of-5 series every 3 days through the A1 window."""
    rnd, npr = random.Random(seed), np.random.default_rng(seed)
    teams_ = [f"Squad{i}" for i in range(6)]
    skill = {(t, r): rnd.uniform(0.6, 1.5) for t in teams_ for r in model.ROLES}
    base = dict(zip(model.ROLES, (2.4, 2.8, 3.6, 4.6, 0.9)))
    out, mid = [], 0
    d = date(2026, 2, 16)
    while d < date(2026, 10, 16):
        rnd.shuffle(teams_)
        for a, b in ((teams_[0], teams_[1]), (teams_[2], teams_[3]), (teams_[4], teams_[5])):
            mid += 1
            bo = 5 if mid % 4 == 0 else 3
            for n in range(1, (3 if bo == 5 else 2) + 1):
                ka = {r: int(npr.poisson(base[r] * skill[(a, r)])) for r in model.ROLES}
                kb = {r: int(npr.poisson(base[r] * skill[(b, r)])) for r in model.ROLES}
                gk, played = f"g{mid}-{n}", f"{d.isoformat()}T08:0{n}:00Z"
                rows = []
                for side, t, ks, other in (("blue", a, ka, kb), ("red", b, kb, ka)):
                    for r in model.ROLES:
                        rows.append({"game_key": gk, "player": f"{t}_{r}", "player_key": f"{t}_{r}".lower(), "player_id": f"lol:player:{t}{r}",
                                     "team": t, "opponent": b if t == a else a, "side": side, "role": r, "kills": ks[r], "deaths": 3,
                                     "assists": 6, "team_kills": sum(ks.values()), "opp_kills": sum(other.values()), "win": None,
                                     "match_key": f"m{mid}", "game_number": n, "best_of": bo, "played_at": played, "league": "tl",
                                     "source": "lp"})
                out.append(({"game_key": gk, "source": "lp", "match_key": f"m{mid}", "game_number": n, "best_of": bo, "played_at": played,
                             "league": "tl", "blue_team": a, "red_team": b, "blue_kills": sum(ka.values()), "red_kills": sum(kb.values())}, rows))
        d += timedelta(days=3)
    return out


def test_compare_harness_scores_both_models_on_the_same_targets(tmp_path, monkeypatch):
    db = history.DB(dsn="", path=tmp_path / "h.db")
    for g, rows in _synthetic_2026():
        history.save_game(db, g, rows)
    db.commit()
    monkeypatch.setattr(backtest, "GRID", {"hl": (120.0, 480.0), "k0": (4.0, 16.0), "ko": (10.0, 80.0), "kt": (10.0,), "ew": (0.0,)})
    data = backtest.Data(history.all_player_games(db, since="2026-02-16"))
    fit, rep = backtest.compare(data, log=lambda m: None)
    k = rep["stats"]["kills"]
    for sc, bo in (("12", 3), ("123", 5)):
        s = k["scopes"][sc]
        assert s["format"] == bo and s["n"] > 50 and s["series"] > 10
        assert s["lol1"]["mae"] > 0 and s["b0"]["mae"] > 0
        g = s["logscore_gain_lol1_minus_b0"]
        assert g["lo95"] <= g["mean"] <= g["hi95"]
    assert set(fit["selection"]) == {"kills", "assists", "deaths"} and fit["selection"]["kills"] in ("lol-1", "B0")
    chosen = fit["params"]["kills"]
    assert (chosen["form"] == "baseline") == (fit["selection"]["kills"] == "B0")
    assert "kills|12" in fit["nb_size"] and fit["protocol"].startswith("lol/EVALUATION.md")
    # the live interface reproduces the harness for the chosen B0 form
    pj = model.project([(100, 4, 3, 6, 15, 12, None)] * 10, [], [], "mid", 200, "12", "kills",
                       {"kills": {"form": "baseline", "k0": 5.0}}, fit["priors"], {("kills", "12"): 6.0}, league="tl")
    prior = model.baseline_prior(fit["priors"], "kills", "mid", "tl")
    assert pj["lambda_game"] == pytest.approx((40 + 5.0 * prior) / 15)


def test_crosscheck_compares_sources_and_keeps_disagreements(con):
    le_copy_of_m1(con)
    # one disagreement: the feed gives LA0 9 kills in game 1 (Leaguepedia 1) - still corroborated (19 of 20 rows agree)
    r = [x for x in store.current_rows(con, "LE:PlayerGame") if x["game_id"] == "e1" and x["player_id"] == "LA0"][0]
    store.put_rows(con, "LE:PlayerGame", [dict(r, kills=9)], lambda x: f"{x['game_id']}|{x['player_id']}", lambda x: "2026-10-10 09:00:00",
                   None, "2026-10-12T00:00:00Z")
    load_world(con)
    out = build.build(con)
    cc = out["crosscheck"]
    assert cc["series"] == 1 and cc["games_count"]["agree"] == 1
    # disagreements are recorded and kept, never overwritten
    assert cc["player_kills"]["compared"] == 20 and cc["player_kills"]["agree"] == 19
    assert cc["team_kills_raw"]["compared"] == 4 and cc["team_kills_player_sum"]["compared"] == 4
    assert cc["player_assists"]["share"] == 1.0 and cc["player_deaths"]["share"] == 1.0
    assert cc["reconciliation"] and "does not establish completeness" in cc["note"]
    rec = con.execute("SELECT item, status, evidence FROM reconciliation WHERE item = 'team_kills'").fetchall()
    assert rec and all(r[1] in ("resolved", "unresolved") for r in rec) and json.loads(rec[0][2])["lp_player_sum"] is not None
    bad = con.execute("SELECT lp_value, le_value FROM crosscheck WHERE item = 'player_kills' AND agree = 0").fetchall()
    assert all(a != b for a, b in bad)


def test_price_fields_are_classified_never_inverted():
    from lol.center import prices
    o = markets.parse_underdog(UD)[0]
    c = prices.classify("underdog", o)
    assert c["price_status"] == "one_side_offered" and c["sides_offered"] == ["higher"] and not c["anchor_eligible"]
    assert c["undocumented_no_vig_p_over"] is None and c["payout_modifiers"] == {"higher": "1.1"}
    two = dict(o, sides={"higher": {"status": "active", "payout_multiplier": "1.48", "decimal_price": "1.55"},
                         "lower": {"status": "active", "payout_multiplier": "0.80", "decimal_price": "2.11"}})
    c2 = prices.classify("underdog", two)
    assert c2["undocumented_no_vig_p_over"] == pytest.approx(0.5765, abs=1e-3) and c2["anchor_eligible"]
    assert abs(c2["undocumented_no_vig_p_over"] - 1 / 1.48) > 0.09          # never 1 / payout modifier
    gob = prices.classify("prizepicks", {"odds_type": "goblin", "sides": {"higher": {"offered": True}}, "extra": {}})
    promo = prices.classify("prizepicks", {"odds_type": "standard", "sides": {"higher": {"offered": True}, "lower": {"offered": True}},
                                           "extra": {"is_promo": True}})
    assert gob["kind"] == "alternate" and promo["kind"] == "promotional" and not gob["anchor_eligible"] and not promo["anchor_eligible"]
    assert prices.classify("prizepicks", {"odds_type": "standard", "sides": {"higher": {"offered": True}, "lower": {"offered": True}},
                                          "extra": {}})["price_status"] == "price_unverified"


@pytest.mark.parametrize("lam,size", [(4.0, 3.0), (11.5, 8.0), (25.0, 20.0)])
def test_probabilities_sum_to_one_no_push_on_halves_monotone(lam, size):
    last = 1.0
    for line in [x / 2 for x in range(0, 80)]:
        o, u, p = model.probs(lam, size, line)
        assert o + u + p == pytest.approx(1.0, abs=1e-9)
        if line != int(line):
            assert p == 0.0
        assert o <= last + 1e-12
        last = o


def test_slips_batch_no_overlap_shortage_and_payouts(con, tmp_path, monkeypatch):
    from lol.center import slips
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    # stakes are held at 0U: nothing is recommended; the preview builds what would be built
    real = slips.build(con, "underdog", 2, 3)
    assert real["slips"] == [] and real["shortage"] and not real["recommended"]
    prev = slips.build(con, "underdog", 1, 3, preview=True)
    assert len(prev["slips"]) == 1 and prev["shortage"].startswith("1 of 3")          # one prop: never repeated
    leg = prev["slips"][0]["legs"][0]
    assert 0 < leg["p_side"] < 1 and prev["slips"][0]["chance_note"].startswith("model estimate")
    unders = slips.build(con, "underdog", 1, 1, mode="unders", preview=True)
    overs = slips.build(con, "underdog", 1, 1, mode="overs", preview=True)
    assert (len(unders["slips"]) == 1) != (len(overs["slips"]) == 1)                 # the one leg fits exactly one mode
    assert slips.build(con, "underdog", 1, 1, exclude_roles=("mid",), preview=True)["slips"] == []
    # the member summary: nothing recommended at 0U, the reasons counted over the upcoming forecasts
    ms = slips.member_summary(con)
    ud = ms["platforms"]["underdog"]
    assert ud["recommended"] == [] and ud["upcoming_forecasts"] >= 1 and ud["upcoming_series"] == 1
    assert any(r.startswith("stake 0U") for r in ud["blocked_by"]) and ms["at"]


def test_learning_dashboard_counts_settled_forecasts(con, tmp_path, monkeypatch):
    from lol.center import learning
    test_saved_forecast_settles_when_the_series_completes(con, tmp_path, monkeypatch)
    d = learning.dashboard(con)
    assert d["settled_by_status"] == {"graded": 1} and d["metrics"]["all"]["n"] == 1
    assert d["evidence"]["met"] is False and d["evidence"]["graded_player_markets"] == 1


def test_export_survives_blank_and_duplicate_names_in_one_game(con, tmp_path):
    load_world(con)
    build.build(con)
    con.execute("UPDATE c_player_games SET name_shown = '' WHERE rowid IN (SELECT rowid FROM c_player_games LIMIT 2)")
    con.execute("UPDATE c_player_games SET name_shown = 'Same' WHERE rowid IN (SELECT rowid FROM c_player_games WHERE game_n = 2 LIMIT 2)")
    con.commit()
    db = history.DB(dsn="", path=tmp_path / "h.db")
    c = export.export(con, db)
    assert c["player_rows"] == 20 and c.get("player_key_from_id", 0) == 1      # blank names fall back to the label; the duplicate gets the id
    assert db.query("SELECT COUNT(DISTINCT player_id) AS n FROM lol_player_games")[0]["n"] == 10


def test_lol2_simulation_is_coherent(tmp_path):
    """Player kills sum to team kills in every simulated game; assists come from the team's other kills; deaths are at
    least the opposing kills; the scope sums games; probabilities straight from the totals are monotone."""
    from lol import sim
    db = history.DB(dsn="", path=tmp_path / "h.db")
    for g, rows in _synthetic_2026():
        for r in rows:
            r["team_id"], r["opponent_id"], r["duration_s"], r["cs"] = r["team"], r["opponent"], 1900.0, 250
            r["win"] = int(r["team_kills"] > r["opp_kills"])
        history.save_game(db, g, rows)
    db.commit()
    rows = history.all_player_games(db)
    params = sim.fit([r for r in rows if r["played_at"] < "2026-08-01"], hl=120.0, k0=8.0, k_team=8.0, elo_fn=lambda a, b, d: 0.5)
    st = sim.state_until(rows, params["hl"], backtest._day("2026-09-01T00:00:00Z"))
    teams_ = sorted(st.lineup)
    ctx = sim.context(st, params, teams_[0], teams_[1], 0.6, backtest._day("2026-09-01T00:00:00Z"))
    assert ctx and len(ctx["lineup_a"]) == 5
    one = sim.simulate(params, ctx, 1, sims=3000, seed=1)
    k_a = sum(one[(q["pid"], "kills")] for q in ctx["lineup_a"])
    d_b = sum(one[(q["pid"], "deaths")] for q in ctx["lineup_b"])
    assert (d_b >= k_a).all()                                         # deaths >= opposing kills (+ executions)
    for q in ctx["lineup_a"]:
        assert (one[(q["pid"], "assists")] <= k_a - one[(q["pid"], "kills")]).all()
        fp = 3 * one[(q["pid"], "kills")] + 2 * one[(q["pid"], "assists")] - one[(q["pid"], "deaths")] + 0.02 * one[(q["pid"], "cs")]
        assert np.allclose(fp, one[(q["pid"], "fantasy")])
    three = sim.simulate(params, ctx, 3, sims=3000, seed=1)
    pid = ctx["lineup_a"][2]["pid"]
    assert three[(pid, "kills")].mean() == pytest.approx(3 * one[(pid, "kills")].mean(), rel=0.12)
    last = 1.0
    for line in [x / 2 for x in range(1, 60, 2)]:
        s = sim.summarize(three[(pid, "kills")], line)
        assert s["p_push"] == pytest.approx(0.0) and s["p_over"] <= last + 1e-12 and s["median"] >= s["p10"]
        last = s["p_over"]


def test_le_team_total_counting_an_execution_is_classified_not_excluded(con):
    """LoL Esports' team total = opposing deaths (one execution): canonical team kills = the player-kill sum, the raw total
    kept, the game and every player row stay in the history."""
    le_copy_of_m1(con)
    rows = store.current_rows(con, "LE:Game")
    g1 = [r for r in rows if r["number"] == 1][0]
    pg = [r for r in store.current_rows(con, "LE:PlayerGame") if r["number"] == 1]
    # blue (LA) players: 10 kills in total; make the red side die 11 times (one death credited to no champion)
    blue_k = sum(p["kills"] for p in pg if p["side"] == "blue")
    store.put_rows(con, "LE:Game", [dict(g1, blue_kills=blue_k + 1)], lambda r: r["game_id"], lambda r: "2026-10-10 09:00:00", None, "2026-10-12T00:00:00Z")
    red = [p for p in pg if p["side"] == "red"]
    extra = blue_k + 1 - sum(p["deaths"] for p in red)
    store.put_rows(con, "LE:PlayerGame", [dict(red[0], deaths=red[0]["deaths"] + extra)], lambda r: f"{r['game_id']}|{r['player_id']}",
                   lambda r: "2026-10-10 09:00:00", None, "2026-10-12T00:00:00Z")
    con.commit()
    build.build(con)
    g = con.execute("SELECT team1_kills, team1_kills_raw, team_kills_status, n_players FROM c_games WHERE source_game_id = 'e1'").fetchone()
    assert g[0] == blue_k and g[1] == blue_k + 1 and "opponent_deaths_incl_1_execution" in g[2] and g[3] == 10


def test_two_leaguepedia_players_are_never_merged(con):
    load_world(con)
    build.build(con)
    a = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    b = store.find_entity(con, "player", "leaguepedia:page", "AlphTop")
    store.link_source(con, a, "player", "lolesports:player", "LEX", "test", "2026-10-12T00:00:00Z")
    assert build._attach(con, "player", "lolesports:player", "LEX", b, "2026-10-12T00:00:00Z") == 0
    assert store.canonical_id(con, a) == a and store.canonical_id(con, b) == b
    assert con.execute("SELECT COUNT(*) FROM quarantine WHERE reason = 'cross_source_conflict'").fetchone()[0] == 1


def test_played_game_missing_from_data_keeps_settlement_pending_and_live_series_not_complete(con):
    load_world(con, best_of="3", score=(2, 1))          # 2-1: game 3 was played, but only games 1-2 are in the data
    build.build(con)
    pid = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    sid = con.execute("SELECT sid FROM c_series").fetchone()[0]
    r = settle.settle(con, "underdog", "kills", "maps_1_2_3", pid, sid, 5.5)
    assert r["status"] == "pending" and "missing from the data" in r["reasons"][0]
    put(con, "MatchSchedule", [{"MatchId": "M1", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "Team1Score": "1", "Team2Score": "0", "BestOf": "3", "DateTime_UTC": "2026-10-10 09:00:00"}],
        at="2026-10-12T00:00:00Z")                      # a best-of-3 at 1-0: in progress, not complete
    build.build(con)
    assert con.execute("SELECT status FROM c_series").fetchone()[0] == "in_progress"
    assert settle.settle(con, "underdog", "kills", "maps_1_2", pid, sid, 5.5)["status"] == "pending"


def test_make_fit_applies_the_a1_rule_to_lol2(tmp_path):
    """A5: lol-2 replaces the B0 / lol-1 choice only when its gain interval is above 0 and MAE not higher on both scopes."""
    import json as _j
    from lol import evaluate

    def res(gain_lo, mae2):
        return {"logscore_gain_lol-2_vs_B0": {"mean": 0.05, "lo95": gain_lo, "hi95": 0.1},
                "logscore_gain_lol-2_vs_lol-1": {"mean": 0.05, "lo95": gain_lo, "hi95": 0.1},
                "logscore_gain_lol-1_vs_B0": {"mean": 0.0, "lo95": -0.1, "hi95": 0.1},
                "models": {"B0": {"mae": 3.0, "logscore": -2.6}, "lol-1": {"mae": 3.0, "logscore": -2.6}, "lol-2": {"mae": mae2, "logscore": -2.5}}}
    d = {"report": {"which": "final", "periods": {"history_from": "2026-02-16", "train_from": "2026-04-01", "eval_from": "2026-08-01"},
                    "results": {"kills|12": res(0.01, 2.9), "kills|123": res(0.02, 2.9), "assists|12": res(0.01, 2.9),
                                "assists|123": res(-0.01, 2.9), "deaths|12": res(0.01, 3.1), "deaths|123": res(0.01, 2.9)}},
         "b0_lol1_fit": {"selection": {"kills": "B0", "assists": "B0", "deaths": "B0"}, "params": {}, "nb_size": {}, "priors": {}},
         "lol2_params": {"hl": 60.0}}
    p = tmp_path / "final.json"
    p.write_text(_j.dumps(d))
    fit = evaluate.make_fit(str(p), "abc")
    assert fit["selection"] == {"kills": "lol-2", "assists": "B0", "deaths": "B0"}
    assert fit["sim"] == {"hl": 60.0} and fit["history_snapshot_sha256"] == "abc" and fit["final_report_sha256"]


def test_overlap_with_a_differently_named_team_is_one_series(con):
    """2026-10-17 audit: LoL Esports names a team differently ("Beta Academy" vs Leaguepedia "Beta Gaming"). One team
    known + game 1 within 20 minutes = the same series: left out (no duplicate games) and the other team linked."""
    load_world(con)
    build.build(con)
    le_copy_of_m1(con)
    m = store.current_rows(con, "LE:Match")[0]
    m["teams"][1].update(name="Beta Academy", code="BAC")
    store.put_rows(con, "LE:Match", [m], lambda r: r["match_id"], lambda r: "2026-10-10 09:00:00", None, "2026-10-12T00:00:00Z")
    con.commit()
    build.build(con)                          # pass 1 finds the overlap by start time and links LB; pass 2 then matches exactly
    assert not con.execute("SELECT 1 FROM quarantine WHERE reason = 'cross_source_teams_not_matched'").fetchall()
    assert con.execute("SELECT COUNT(*) FROM c_player_games").fetchone()[0] == 20                  # one copy of the series
    assert store.canonical_id(con, store.find_entity(con, "team", "lolesports:team", "LB")) ==         store.find_entity(con, "team", "leaguepedia:page", "Beta Gaming")


def test_forecast_history_uses_the_evaluated_window(tmp_path):
    """Scout stores all history; live inputs use the last HISTORY_DAYS days only (the evaluated 8-month window)."""
    db = history.DB(dsn="", path=tmp_path / "h.db")
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    for key, when, k in (("old", "2025-03-01T10:00:00Z", 9), ("new", "2026-09-01T10:00:00Z", 3)):
        g = {"game_key": f"lol:game:{key}", "source": "lp", "match_key": f"lol:series:{key}", "game_number": 1, "best_of": 3,
             "played_at": when, "blue_team": "A", "red_team": "B", "blue_team_id": "lol:team:1", "red_team_id": "lol:team:2"}
        ps = [{"game_key": f"lol:game:{key}", "match_key": f"lol:series:{key}", "game_number": 1, "played_at": when,
               "player": "P", "player_key": "p", "player_id": "lol:player:1", "team": "A", "team_id": "lol:team:1",
               "opponent": "B", "opponent_id": "lol:team:2", "role": "mid", "kills": k, "deaths": 1, "assists": 2,
               "team_kills": 10, "opp_kills": 5, "win": 1}]
        history.save_game(db, g, ps)
    h = forecast.History(db, {"lol:player:1"}, {"lol:team:1"}, now=now)
    pin, tin, _ = h.inputs("lol:player:1", "lol:team:1", "lol:team:2", now.date().toordinal())
    assert [r[1] for r in pin] == [3] and len(tin) == 1                 # the 2025 game is stored but not used
    assert h.since.startswith("2026-02-14")


def test_spelling_variant_joins_its_page_only_with_a_common_team(con):
    """A scoreboard link spelled with other capitals ('ALPHMID') and no page of its own resolves to the one page with that
    name ignoring capitals ('AlphMid') when they played for a common team; otherwise it stays its own id."""
    from lol.tests.test_center import game_rows
    extra = [game_rows("M2", 1, "Alpha Esports", "Beta Gaming", "2026-10-12 09:05:00", link_override={"AlphMid": "ALPHMID"}),
             game_rows("M3", 1, "Gamma Team", "Beta Gaming", "2026-10-13 09:05:00", link_override={"GammTop": "BETAMID"})]
    load_world(con, extra_games=extra)
    build.build(con)
    alph = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    assert store.canonical_id(con, store.find_entity(con, "player", "leaguepedia:page", "ALPHMID") or alph) == alph
    assert con.execute("SELECT COUNT(DISTINCT pid) FROM c_player_games WHERE name_shown = 'AlphMid'").fetchone()[0] == 1
    assert store.get_meta(con, "page_variant:ALPHMID") == "AlphMid"
    # 'BETAMID' on Gamma Team: Beta's mid never played for Gamma - no link
    assert store.get_meta(con, "page_variant:BETAMID") is None
    beta = store.find_entity(con, "player", "leaguepedia:page", "BetaMid")
    assert beta not in {r[0] for r in con.execute("SELECT pid FROM c_player_games WHERE start_utc LIKE '2026-10-13%' AND role = 'top'")}


def test_academy_match_on_the_same_day_is_not_its_main_teams_series(con):
    """2026-10-17 audit: same codes, same day, hours apart (academy vs main roster) - not one series, no links."""
    load_world(con)
    le_copy_of_m1(con)
    m = store.current_rows(con, "LE:Match")[0]
    m["teams"][0].update(name="Alpha Esports Academy")
    m["teams"][1].update(name="Beta Gaming Academy")
    store.put_rows(con, "LE:Match", [m], lambda r: r["match_id"], lambda r: "2026-10-10 09:00:00", None, "2026-10-12T00:00:00Z")
    for g in [x for x in store.current_rows(con, "LE:Game")]:
        store.put_rows(con, "LE:Game", [dict(g, start=g["start"].replace("T09:", "T15:").replace("T10:", "T16:"))],
                       lambda r: r["game_id"], lambda r: "2026-10-10 09:00:00", None, "2026-10-12T00:00:00Z")
    con.commit()
    out = build.build(con)
    assert not out["lolesports"].get("left_out_leaguepedia_has_it")
    assert store.find_entity(con, "team", "lolesports:team", "LA") != store.find_entity(con, "team", "leaguepedia:page", "Alpha Esports")
    assert store.find_entity(con, "player", "lolesports:player", "LA2") != store.find_entity(con, "player", "leaguepedia:page", "AlphMid")


def test_wrong_existing_link_is_corrected_by_corroborated_games(con):
    """A LoL Esports player id linked to the wrong Leaguepedia player earlier moves to the page every corroborated shared
    game puts it on - recorded (history + quarantine), the wrong id keeps its own page."""
    load_world(con)
    build.build(con)
    beta_mid = store.find_entity(con, "player", "leaguepedia:page", "BetaMid")
    store.link_source(con, beta_mid, "player", "lolesports:player", "LA2", "an earlier wrong link")
    con.commit()
    le_copy_of_m1(con)
    build.build(con)
    alph = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    assert store.canonical_id(con, store.find_entity(con, "player", "lolesports:player", "LA2")) == alph
    assert store.canonical_id(con, beta_mid) != alph and store.find_entity(con, "player", "leaguepedia:page", "BetaMid") == beta_mid
    q = con.execute("SELECT detail FROM quarantine WHERE reason = 'cross_source_link_corrected'").fetchall()
    assert q and "BetaMid" in q[0][0]
