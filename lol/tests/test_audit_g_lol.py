"""Regression tests for the 2026-10-21 audit fixes in the LoL data center (audit 12, 13, 15, 42, 43, 44, 45).
Every team, player and number is a TEST FIXTURE (no network)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from lol import elo, history, model, sim
from lol.center import build, export, forecast, markets, match, settle, store
from lol.tests.test_center import UD, load_world, put
from lol.tests.test_consolidation import PAR, PRI, RR, _future_world


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


def _poll(con, raw):
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(raw).encode()))


def _world(con, tmp_path, monkeypatch, page="Test League 2026", stat="kills_on_maps_1_2", best_of="3"):
    """_future_world with the upcoming series M9 on its own tournament page / stat / format."""
    load_world(con)
    put(con, "Tournaments", [{"OverviewPage": "Worlds 2026", "Name": "Worlds 2026 Main Event", "League": "World Championship",
                              "Region": "International"}])
    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    put(con, "MatchSchedule", [{"MatchId": "M9", "OverviewPage": page, "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "BestOf": best_of, "DateTime_UTC": start.strftime("%Y-%m-%d %H:%M:%S")}], at="2026-10-15T00:00:00Z")
    build.build(con)
    db = history.DB(dsn="", path=tmp_path / "history.db")
    export.export(con, db)
    monkeypatch.setattr(model, "FIT", ({}, PAR, RR, {}))
    monkeypatch.setattr(model, "PARAMS", PAR)
    monkeypatch.setattr(model, "PRIORS", PRI)
    monkeypatch.setattr(model, "R", RR)
    raw = dict(UD, games=[dict(UD["games"][0], scheduled_at=start.strftime("%Y-%m-%dT%H:%M:%SZ"))])
    raw["over_under_lines"] = [dict(raw["over_under_lines"][0], over_under={"appearance_stat": {
        "appearance_id": "a1", "stat": stat, "display_stat": stat}},
        options=[{"choice": "higher", "status": "active", "payout_multiplier": "1.1"},
                 {"choice": "lower", "status": "active", "payout_multiplier": "0.9"}])]
    _poll(con, raw)
    match.link_open_markets(con)
    return db, start, raw


# ---------------------------------------------------------------- audit 12: one bad offer / stage never stops the cycle
def test_offer_without_a_line_is_skipped_and_the_cycle_goes_on(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    raw["over_under_lines"][0]["stat_value"] = None                    # a feed row without a usable line
    _poll(con, raw)
    out = forecast.run(con, db)
    assert out.get("skip: no numeric line") == 1 and "saved" not in out
    raw["over_under_lines"][0]["stat_value"] = "6.5"
    _poll(con, raw)
    assert forecast.run(con, db)["saved"] == 1


def test_an_exception_in_one_offer_is_counted_not_raised(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)

    def boom(*a, **k):
        raise TypeError("bad inputs")
    monkeypatch.setattr(forecast, "forecast_one", boom)
    out = forecast.run(con, db)
    assert out["error: TypeError"] == 1 and out["_errors"][0]["error"].startswith("TypeError")


def test_build_loop_keeps_settling_and_writing_the_board_when_forecasts_fail(con, tmp_path, monkeypatch):
    from lol.center import learning, slips, worker
    store.set_meta(con, "built_at", "2026-10-20T00:00:00Z")
    store.set_meta(con, "last_export", {"at": "2026-10-20T00:00:00Z"})
    con.commit()
    calls = []
    monkeypatch.setattr(worker, "OUT", tmp_path)
    monkeypatch.setattr(match, "link_open_markets", lambda c: calls.append("links") or {})
    monkeypatch.setattr(forecast, "run", lambda c, **k: (_ for _ in ()).throw(TypeError("bad offer")))
    monkeypatch.setattr(forecast, "settle_all", lambda c: calls.append("settle") or {})
    # This loop test stops at sleep; subprocess.wait also uses time.sleep on Linux.
    # Keep the independently tested daily fitter out of this forecast-failure test,
    # regardless of UTC hour or calibration files from another test.
    monkeypatch.setattr(worker, "lc1_autofit", lambda c: None)
    monkeypatch.setattr(forecast, "board", lambda c: calls.append("board") or {"rows": []})
    monkeypatch.setattr(slips, "member_summary", lambda c: {})
    monkeypatch.setattr(slips, "build", lambda *a, **k: {})
    monkeypatch.setattr(learning, "dashboard", lambda c: calls.append("learning") or {})

    class Stop(BaseException):
        pass
    monkeypatch.setattr(worker.time, "sleep", lambda s: (_ for _ in ()).throw(Stop()))
    monkeypatch.setattr(worker, "STATE", dict(worker.STATE, errors=[]))
    with pytest.raises(Stop):
        worker.build_loop()
    assert calls == ["links", "settle", "board", "learning"]
    assert (tmp_path / "board.json").exists() and (tmp_path / "learning.json").exists() and (tmp_path / "slips.json").exists()
    assert [e["where"] for e in worker.STATE["errors"]] == ["forecasts"]


# ---------------------------------------------------------------- audit 13: closed markets are not relinked; settle once per build
def test_closed_markets_are_not_relinked(con, tmp_path, monkeypatch):
    _future_world(con, tmp_path, monkeypatch)
    con.execute("UPDATE market_state SET open = 0")
    con.commit()
    seen = []
    real = match.link
    monkeypatch.setattr(match, "link", lambda c, o: seen.append(o) or real(c, o))
    assert match.link_open_markets(con) == {} and seen == []
    assert con.execute("SELECT status FROM market_links").fetchone()[0] == "matched"     # the stored link is kept


def test_settle_all_reruns_recorded_units_only_after_a_build(con, tmp_path, monkeypatch):
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
    assert forecast.settle_all(con) == {"graded": 1}
    n = []
    real = settle.record
    monkeypatch.setattr(settle, "record", lambda *a, **k: n.append(1) or real(*a, **k))
    assert forecast.settle_all(con) == {"unchanged": 1} and not n          # no build since: not re-settled
    store.set_meta(con, "built_at", "2099-01-01T00:00:00Z")                  # a new build (same-second builds share a stamp)
    assert forecast.settle_all(con) == {"graded": 1} and len(n) == 1        # a new build: the full pass again


# ---------------------------------------------------------------- audit 15: the league is the forecast series' league
def test_forecast_league_is_the_series_league_as_in_the_study(con, tmp_path, monkeypatch):
    db, start, raw = _world(con, tmp_path, monkeypatch, page="Worlds 2026")
    seen = {}
    real = model.project
    monkeypatch.setattr(model, "project", lambda *a, **k: seen.update(league=k.get("league")) or real(*a, **k))
    assert forecast.run(con, db)["saved"] == 1
    inp = json.loads(con.execute("SELECT inputs FROM lol_forecasts").fetchone()[0])
    h = forecast.History(db, (), ())
    pid = con.execute("SELECT pid FROM lol_forecasts").fetchone()[0]
    s = con.execute("SELECT * FROM c_series WHERE sid = (SELECT sid FROM lol_forecasts)").fetchone()
    study = export._league(con)(s)[0]                                  # what the export / study gives this series' games
    assert study != h.league(pid, start.date().toordinal())            # the player's last league differs (the bug case)
    assert inp["league"] == seen["league"] == study


# ---------------------------------------------------------------- audit 44: Elo keyed by team id, as evaluated
def _hist(tmp_path, rows):
    db = history.DB(dsn="", path=tmp_path / "h.db")
    for i, (tid, tname, oid, oname, win, when) in enumerate(rows):
        g = {"game_key": f"lol:game:{i}", "source": "lp", "match_key": f"lol:series:{i}", "game_number": 1, "best_of": 3,
             "played_at": when, "blue_team": tname, "red_team": oname, "blue_team_id": tid, "red_team_id": oid}
        ps = [{"game_key": f"lol:game:{i}", "match_key": f"lol:series:{i}", "game_number": 1, "played_at": when, "player": "P",
               "player_key": f"p{i}", "player_id": "lol:player:1", "team": tname, "team_id": tid, "opponent": oname,
               "opponent_id": oid, "role": "mid", "kills": 3, "deaths": 1, "assists": 2, "team_kills": 10, "opp_kills": 5, "win": win}]
        history.save_game(db, g, ps)
    return db


def test_live_elo_is_the_evaluated_elo_by_team_id(tmp_path):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    rows = [("lol:team:1", "Same Name", f"lol:team:{9 + i}", f"Opp {i}", 1, f"2026-09-{1 + i:02d}T10:00:00Z") for i in range(10)]
    db = _hist(tmp_path, rows)
    h = forecast.History(db, (), ("lol:team:1",), now=now)
    day = now.date().toordinal()
    ev = elo.from_rows([dict(r, team=r["team_id"], opponent=r["opponent_id"]) for r in
                        db.query("SELECT * FROM lol_player_games")], lambda r: h.md[r["match_key"]])
    p_new = h.p_win("lol:team:1", "lol:team:77", day)                  # an opponent with no stored games: BASE, not None
    assert p_new is not None and p_new == pytest.approx(ev.p_win("lol:team:1", "lol:team:77", day)) and p_new > 0.6
    # another canonical team with the same display name keeps its own (BASE) rating
    assert h.p_win("lol:team:2", "lol:team:77", day) == pytest.approx(0.5)


# ---------------------------------------------------------------- audit 42: shown projection = the mixture's
def test_maps_1_3_on_a_best_of_3_shows_the_mixture_projection(con, tmp_path, monkeypatch):
    db, start, raw = _world(con, tmp_path, monkeypatch, stat="kills_on_maps_1_2_3")
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    inp = json.loads(f["inputs"])
    assert "format_scope_mismatch" in json.loads(f["flags"])
    p3, lg = inp["p_3_games"], inp["lambda_game"]
    assert f["lambda"] == pytest.approx(lg * (2 + p3), abs=1e-3) and inp["mean"] == pytest.approx(f["lambda"], abs=1e-3)
    assert inp["lambda_3_games"] == pytest.approx(3 * lg, abs=1e-3)
    mix = [(p3, 3 * lg, RR[("kills", "123")]), (1 - p3, 2 * lg, RR[("kills", "12")])]
    cdf = lambda k: sum(w * model.nb_pmf(j, lam, sz) for w, lam, sz in mix for j in range(k + 1))
    lo, hi = inp["range80"]
    assert cdf(inp["median"]) >= 0.5 > cdf(inp["median"] - 1) and cdf(lo) >= 0.1 > cdf(lo - 1) and cdf(hi) >= 0.9 > cdf(hi - 1)
    o3, _, _ = model.probs(3 * lg, RR[("kills", "123")], f["line"])
    o2, _, _ = model.probs(2 * lg, RR[("kills", "12")], f["line"])
    assert f["p_over"] == pytest.approx(p3 * o3 + (1 - p3) * o2, abs=1e-4)        # the chance is unchanged


# ---------------------------------------------------------------- audit 43: incomplete scoreboards never mean "absent"
def test_missing_player_rows_keep_settlement_pending(con):
    load_world(con)
    build.build(con)
    pid = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    sid = con.execute("SELECT sid FROM c_series").fetchone()[0]
    assert settle.settle(con, "underdog", "kills", "maps_1_2", pid, sid, 5.5)["status"] == "graded"
    g2 = con.execute("SELECT gid FROM c_games WHERE sid = ? AND game_n = 2", (sid,)).fetchone()[0]
    con.execute("DELETE FROM c_player_games WHERE gid = ?", (g2,))
    con.execute("UPDATE c_games SET n_players = 0, flags = '[\"players_incomplete\"]' WHERE gid = ?", (g2,))
    for plat in ("underdog", "prizepicks"):
        r = settle.settle(con, plat, "kills", "maps_1_2", pid, sid, 5.5)
        assert r["status"] == "pending" and "incomplete" in r["reasons"][0]
    con.execute("DELETE FROM c_player_games WHERE sid = ?", (sid,))
    con.execute("UPDATE c_games SET n_players = 0 WHERE sid = ?", (sid,))
    assert settle.settle(con, "underdog", "kills", "maps_1_2", pid, sid, 5.5)["status"] == "pending"   # not dnp
    # a complete scoreboard without the player is still a real absence
    con.execute("UPDATE c_games SET n_players = 10, flags = '[]' WHERE sid = ?", (sid,))
    assert settle.settle(con, "underdog", "kills", "maps_1_2", pid, sid, 5.5)["status"] == "dnp"


# ---------------------------------------------------------------- audit 45: lol-2 lineup never holds a player twice
def test_posted_player_in_a_new_role_swaps_slots():
    class St:
        lineup = {"T": {"top": "p1", "jungle": "p2", "mid": "p3", "bot": "p4", "support": "p5"},
                  "O": {"top": "o1", "jungle": "o2", "mid": "o3", "bot": "o4", "support": "o5"}}
    seen = []
    orig_tf, orig_pf = sim.team_feats, sim.player_feats
    sim.team_feats = lambda *a: {}
    sim.player_feats = lambda st, pid, role, *a: seen.append(pid) or {}
    try:
        ctx = sim.context(St(), {"priors": {}, "k_team": 1, "k0": 1}, "T", "O", 0.5, 100,
                          posted={"pid": "p1", "team": "T", "role": "mid"})
    finally:
        sim.team_feats, sim.player_feats = orig_tf, orig_pf
    lu = {q["role"]: q["pid"] for q in ctx["lineup_a"]}
    assert lu == {"top": "p3", "jungle": "p2", "mid": "p1", "bot": "p4", "support": "p5"}
    assert sorted(q["pid"] for q in ctx["lineup_a"]) == ["p1", "p2", "p3", "p4", "p5"]


def test_the_fixed_live_path_still_never_imports_scipy():
    """Production has no scipy (2026-10-19 outage): the forecast now maps the series league through export._league."""
    import subprocess
    import sys
    code = ("import builtins; real = builtins.__import__\n"
            "def block(name, *a, **k):\n"
            "    if name == 'scipy' or name.startswith('scipy.'): raise ModuleNotFoundError('scipy blocked')\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = block\n"
            "from lol.center import export, forecast, match, settle, worker\nfrom lol import elo, sim, teams\n"
            "print('ok')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr
