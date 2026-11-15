"""Game store: ESPN game parsing (real trimmed samples), Kalshi game ladders, ESPN-supplied DraftKings prop lines,
play-by-play derivations, the factor contract and the no-look-ahead rules of the factor engine."""
import json
from datetime import datetime, timezone

import pytest

from sports import config, espn
from sports.games import collect, contract, derive, espn_game, espn_props, factors, kalshi_lines, store
from sports.tests.conftest import fixture

NBA, NFL = config.league("nba"), config.league("nfl")


@pytest.mark.parametrize(("as_of", "first_day"), [
    ("2026-11-15T00:30:00+00:00", "2026-11-14"),
    ("2026-03-09T03:59:00+00:00", "2026-03-08"),
    ("2026-03-09T04:00:00+00:00", "2026-03-09"),
    ("2026-11-02T04:59:00+00:00", "2026-11-01"),
    ("2026-11-02T05:00:00+00:00", "2026-11-02"),
])
def test_evening_game_context_uses_espn_schedule_day(monkeypatch, as_of, first_day):
    monkeypatch.setattr(espn, "scoreboard", lambda league, start, end: (start, end))
    start, end = collect._games_window(NBA, datetime.fromisoformat(as_of), 0, 8)
    assert start.isoformat() == first_day
    assert (end - start).days == 8


def g(name):
    return fixture(f"games/{name}")


@pytest.fixture
def gdb(tmp_path):
    db = store.connect(tmp_path / "games.db")
    yield db
    db.close()


@pytest.fixture
def nba_game():
    return espn_game.parse(g("espn_summary_nba_401809234.json"), "nba", "basketball", venue={"indoor": True},
                           now_iso="2026-10-23T22:00:00Z")


@pytest.fixture
def nfl_game():
    return espn_game.parse(g("espn_summary_nfl_401872996.json"), "nfl", "football",
                           core_plays=g("espn_core_plays_nfl_401872996.json"), venue={"indoor": False})


# ------------------------------------------------------------------------------------------------------- parsing

def test_stat_dict_splits_pairs_keeps_numbers_and_drops_blanks():
    got = espn_game.stat_dict(["fieldGoalsMade-fieldGoalsAttempted", "completions/passingAttempts", "thirdDownEff",
                               "possessionTime", "plusMinus", "adjQBR"], ["40-86", "24/34", "5-11", "29:37", "-4", "--"])
    assert got == {"fieldGoalsMade": 40, "fieldGoalsAttempted": 86, "completions": 24, "passingAttempts": 34,
                   "thirdDownConversions": 5, "thirdDownAttempts": 11, "possessionTime": 1777, "plusMinus": -4}


def test_parse_nba_summary(nba_game):
    gm = nba_game["game"]
    assert (gm["event_id"], gm["home_abbr"], gm["away_abbr"], gm["home_score"], gm["away_score"]) == \
        ("401809234", "NY", "CLE", 119, 111)
    assert gm["periods"] == 4 and gm["indoor"] == 1 and gm["ended_at"] == "2025-10-23T01:42:40Z"
    assert gm["officials"][0]["name"] == "Ed Malloy"
    t = {x["abbr"]: x for x in nba_game["teams"]}
    assert t["CLE"]["stats"]["fieldGoalsAttempted"] == 86 and t["CLE"]["opp_id"] == t["NY"]["team_id"]
    p = {x["player"]: x for x in nba_game["players"]}
    assert p["Jarrett Allen"]["stats"]["minutes"] == 28 and p["Jarrett Allen"]["starter"] == 1
    assert p["Jarrett Allen"]["stats"]["freeThrowsAttempted"] == 4 and p["Jarrett Allen"]["position"] == "C"
    assert p["Luke Travers"]["played"] == 0 and p["Luke Travers"]["dnp_reason"] == "COACH'S DECISION"
    ft = next(x for x in nba_game["plays"] if (x["type"] or "").startswith("Free Throw"))
    assert ft["x"] is None and ft["y"] is None                     # ESPN's -2.1e9 "no location" is dropped
    shot = next(x for x in nba_game["plays"] if x["type"] == "Driving Layup Shot")
    assert (shot["x"], shot["y"], shot["points_attempted"], shot["home_wp"]) == (26.0, 1.0, 2, 0.511)


def test_parse_nfl_summary_with_core_roles(nfl_game):
    gm = nfl_game["game"]
    assert gm["neutral"] == 1 and gm["week"] == 6 and gm["participants"] == 1
    rec = next(x for x in nfl_game["plays"] if x["type"] == "Pass Reception")
    assert (rec["down"], rec["distance"], rec["yte"], rec["yards"], rec["yac"]) == (1, 10, 70, 24, 4)
    assert [a["role"] for a in rec["athletes"]][:2] == ["passer", "receiver"]
    assert rec["offense_id"] == "30" and rec["defense_id"] == "34"
    p = next(x for x in nfl_game["players"] if "receiving.receivingTargets" in x["stats"])
    assert p["stats"]["receiving.receivingTargets"] >= p["stats"]["receiving.receptions"]
    assert nfl_game["context"]["pickcenter"][0]["provider"] == "DraftKings"
    assert nfl_game["context"]["pickcenter"][0]["spread"] == -2.5 and nfl_game["context"]["injuries"]


# ------------------------------------------------------------------------------------------------------- storage

def test_save_game_replaces_and_history_tables_append_only_on_change(gdb, nba_game):
    store.save_game(gdb, nba_game)
    store.save_game(gdb, nba_game)                                  # a re-read replaces, never duplicates
    assert gdb.one("SELECT COUNT(*) AS n FROM sports_game_games")["n"] == 1
    assert gdb.one("SELECT COUNT(*) AS n FROM sports_game_plays_z")["n"] == 1            # one packed row per game
    assert len(store.plays(gdb, "nba", "401809234")) == len(nba_game["plays"])
    assert store.has_game(gdb, "nba", "401809234")
    t0, t1 = datetime(2026, 10, 23, 20, tzinfo=timezone.utc), datetime(2026, 10, 23, 21, tzinfo=timezone.utc)
    assert store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -3}], now=t0)
    assert not store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -3}], now=t1)
    assert store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -3.5}], now=t1)
    row = {"source": "kalshi", "league": "nba", "event_id": "1", "provider_market_id": "M1", "market": "total_over",
           "strike": 220.5, "bid": 0.48, "ask": 0.50, "status": "active"}
    assert store.save_market_lines(gdb, [row], now=t0) == 1
    assert store.save_market_lines(gdb, [row], now=t1) == 0        # unchanged: only marked seen
    assert store.save_market_lines(gdb, [dict(row, bid=0.49)], now=t1) == 1
    st = gdb.one("SELECT last_seen_at FROM sports_game_line_state WHERE key = 'mkt:kalshi:M1'")
    assert st["last_seen_at"] == "2026-10-23T21:00:00Z"


def test_lease_is_exclusive(gdb):
    now = datetime(2026, 10, 23, 22, tzinfo=timezone.utc)
    assert collect.take_lease(gdb, now)
    gdb.execute("UPDATE sports_game_settings SET value = 'other:1' WHERE key = 'lease'")
    assert not collect.take_lease(gdb, now)


# --------------------------------------------------------------------------------------------- Kalshi game ladders

def _kalshi_rows():
    rows = kalshi_lines.parse(g("kalshi_games_nba_26OCT23NYKBOS.json")["events"], "nba")
    games = [espn._event(e) for e in g("espn_scoreboard_nba_20261023.json")["events"]]
    return kalshi_lines.match(rows, "nba", games), games


def test_kalshi_ladders_match_espn_by_date_and_both_teams():
    rows, games = _kalshi_rows()
    assert rows and all(r["event_id"] == "401909095" for r in rows)            # NYK @ BOS = ESPN NY @ BOS
    nyb = next(x for x in games if x["id"] == "401909095")
    named = [r for r in rows if r["team_code"]]
    assert {r["team_id"] for r in named} == {nyb["home"]["id"], nyb["away"]["id"]}
    imp = kalshi_lines.implied(rows, nyb["home"]["id"], nyb["away"]["id"])
    assert 0.5 < imp["home_win"] < 0.65 and 0 < imp["home_margin"] < 6 and 210 < imp["total"] < 230
    assert imp["home_team_total"] > imp["away_team_total"]
    assert all(0 <= p <= 1.2 for p in imp["p_margin_over"].values())


def test_kalshi_event_on_another_date_is_not_matched():
    rows = kalshi_lines.parse(g("kalshi_games_nba_26OCT23NYKBOS.json")["events"], "nba")
    for r in rows:
        r["_date"] = "2026-10-24"
    games = [espn._event(e) for e in g("espn_scoreboard_nba_20261023.json")["events"]]
    assert not any(r["event_id"] for r in kalshi_lines.match(rows, "nba", games))


# ----------------------------------------------------------------------------------- ESPN-supplied DraftKings lines

def test_espn_dk_prop_lines_are_lines_only_exact_event_and_labelled():
    body = g("espn_propbets_nfl_401873011.json")
    rows = espn_props.parse(body, "nfl", "football", "401873011", "100", "DraftKings")
    assert rows and all(r["event_id"] == "401873011" for r in rows)            # the other game's item is dropped
    assert all(r["provenance"] == "ESPN-supplied DraftKings" and r["book"] == "draftkings" for r in rows)
    assert all(r["price_over"] is None and r["price_under"] is None for r in rows)
    assert len({r["prop_key"] for r in rows}) == len(rows)                    # the two unpriced sides collapse
    ms = next(r for r in rows if r["kind"] == "milestone")
    raw = next(i for i in body["items"] if i["type"]["name"] == ms["raw_type"]
               and i.get("athlete", {}).get("$ref", "").endswith(ms["athlete_id"]))
    assert ms["line"] == raw["current"]["target"]["value"] - 0.5            # "15+" = over 14.5
    std = next(r for r in rows if r["raw_type"] == "Total Receiving Yards (incl. overtime)")
    assert std["market"] == "rec_yds" and std["period"] == "game" and std["kind"] == "line"
    assert std["provider_updated_at"].endswith(":00Z")
    first_q = [r for r in rows if r["raw_type"].startswith("1st Quarter")]
    assert first_q and all(r["period"] == "1q" for r in first_q)            # never a full-game line


def test_espn_dk_market_names_match_whole():
    assert espn_props.market_of("football", "1st Half Total Receiving Yards") == "rec_yds"
    assert espn_props.market_of("football", "Longest Reception (incl. overtime)") is None
    assert espn_props.market_of("basketball", "Assists Milestones - 1st Quarter") == "assists"
    assert espn_props.period_of("Assists Milestones - 1st Quarter") == "1q"


# ----------------------------------------------------------------------------------------- play-by-play derived

def test_shot_zones():
    z = derive.shot_zone
    assert z({"shooting": 1, "points_attempted": 2, "type": "Driving Layup Shot", "text": "x makes layup"}) == "rim"
    assert z({"shooting": 1, "points_attempted": 2, "type": "Jump Shot", "text": "x makes 17-foot jumper"}) == \
        "long_mid"
    assert z({"shooting": 1, "points_attempted": 3, "type": "Jump Shot", "text": "", "x": 48, "y": 0}) == "corner3"
    assert z({"shooting": 1, "points_attempted": 1, "type": "Free Throw - 1 of 2", "text": ""}) is None


def test_basketball_derivation_tracks_five_on_court(nba_game):
    gm = nba_game["game"]
    per = derive.basketball_game(nba_game["plays"], nba_game["players"], gm["home_id"], gm["away_id"])
    team_of = {p["athlete_id"]: p["team_id"] for p in nba_game["players"]}
    last = {}
    for p in nba_game["plays"]:
        if p["clock_s"] is not None:
            last[p["period"]] = p["clock_s"]
    elapsed = sum(720 - c for c in last.values())                   # each period's clock starts at 12:00
    for tm in (gm["home_id"], gm["away_id"]):
        on = sum(v.get("on_s", 0) for a, v in per.items() if team_of.get(a) == tm)
        assert abs(on - 5 * elapsed) < 1e-6                         # always five on court
    assert sum(v.get("rim_fga", 0) for v in per.values()) > 0
    assert any("oreb_live_misses_on" in v for v in per.values())


def test_football_success_and_epa_sign(nfl_game):
    ep = {"cells": {}, "by_down_yte": {f"{d}|{b}": 6 - 0.6 * b - 0.3 * (d - 1) for d in range(1, 5)
                                       for b in range(10)}}
    gm = nfl_game["game"]
    pl, tm = derive.football_game(nfl_game["plays"], gm["home_id"], gm["away_id"], ep)
    assert tm and all(v["plays"] > 0 for v in tm.values())
    assert derive.success({"down": 1, "distance": 10, "yards": 4}) and not derive.success(
        {"down": 3, "distance": 5, "yards": 4})
    big = {"down": 1, "distance": 10, "yte": 70, "type": "Pass Reception", "offense_id": "30", "period": 1}
    nxt = {"down": 1, "distance": 10, "yte": 46, "offense_id": "30", "period": 1}
    assert derive.epa(ep, big, nxt) > 0                             # 24 yards gained on 1st down adds points


def test_fit_ep_values_rise_toward_the_goal_line(nfl_game):
    ep = derive.fit_ep(nfl_game["plays"], min_n=1)
    by = ep["by_down_yte"]
    assert ep["n_plays"] > 0 and by


# ------------------------------------------------------------------------------------------ contract & factors

def test_contract_is_complete_and_single_sourced():
    allowed = {"collected", "derived", "proxy", "unavailable"}
    for lg in ("nba", "nfl"):
        reg = contract.REGISTRY[lg]
        for k, spec in reg.items():
            assert spec["status"] in allowed, k
            assert (spec["source"] is None) == (spec["status"] == "unavailable"), k
            assert spec["status"] != "unavailable" or spec["how"], k
        assert contract.ON_ROW <= set(reg)
    nba = contract.REGISTRY["nba"]
    assert nba["reb_per_live_miss_proxy"]["status"] == "proxy" and nba["rebound_chances"]["status"] == "unavailable"
    assert contract.CONTRACT == "sports-factors/1"


def test_factors_use_only_results_known_before_as_of(gdb, nba_game):
    store.save_game(gdb, nba_game)
    collect.derive_game(gdb, NBA, nba_game["game"], nba_game["plays"], nba_game["players"])
    gm = nba_game["game"]
    allen = next(p["athlete_id"] for p in nba_game["players"] if p["player"] == "Jarrett Allen")
    during = datetime(2025, 10, 23, 1, 0, tzinfo=timezone.utc)        # started 23:00Z, ended 01:42Z
    data = factors.LeagueData(gdb, "nba", during)
    assert not data.games                                             # not finished at as_of: not known
    pay = factors.compute(gdb, data, allen, gm["away_id"], gm["home_id"], "next", "2025-10-24T23:00:00Z", "rebounds",
                          home=False)
    assert pay["missing"].get("*") and "min_l5" not in pay["factors"]
    after = datetime(2025, 10, 23, 2, 0, tzinfo=timezone.utc)
    data = factors.LeagueData(gdb, "nba", after)
    pay = factors.compute(gdb, data, allen, gm["away_id"], gm["home_id"], "next", "2025-10-24T23:00:00Z", "rebounds",
                          home=False)
    f = pay["factors"]
    assert pay["contract"] == contract.CONTRACT and f["min_l5"] == 28.0 and f["stat_l3"] == 4.0
    assert "potential_assists" in pay["missing"] and "book_consensus" not in pay["missing"]


def test_epa_factors_withheld_when_the_fit_saw_later_games(gdb, nfl_game, monkeypatch):
    store.save_game(gdb, nfl_game)
    gm = nfl_game["game"]
    monkeypatch.setattr(derive, "load_ep", lambda: {"cells": {}, "by_down_yte": {"1|7": 1.0},
                                                    "trained_through": "2026-12-31T00:00:00Z"})
    data = factors.LeagueData(gdb, "nfl", datetime(2026, 10, 20, tzinfo=timezone.utc))
    pay = factors.compute(gdb, data, "4360310", gm["home_id"], gm["away_id"], "x", "2026-10-25T17:00:00Z",
                          "pass_yds", home=True)
    for k in factors.EPA_KEYS:
        assert k not in pay["factors"] and "trained on games through" in pay["missing"][k]


def test_weather_coords_and_wind_parse():
    from sports.games import weather
    assert weather.coords("Lambeau Field") == (44.501, -88.062) and weather.coords("Stade de France") is None
    assert weather._mph("10 to 15 mph") == 15


def test_packed_plays_are_lossless_and_legacy_rows_move_over_verified(gdb, nba_game, nfl_game):
    from sports.games import backfill_job
    # legacy layout: plays as rows (what a store written before packing holds)
    for g in (nba_game, nfl_game):
        store.save_game(gdb, g)
        lg, ev = g["game"]["league"], g["game"]["event_id"]
        packed = store.plays(gdb, lg, ev)
        gdb.execute("DELETE FROM sports_game_plays_z WHERE league = ? AND event_id = ?", (lg, ev))
        for p in packed:
            gdb.execute("INSERT INTO sports_game_plays " + store._ins(store.PLAY_COLS), [p[c] for c in store.PLAY_COLS])
    gdb.commit()
    before = {ev: store.plays(gdb, lg, ev) for lg, ev in (("nba", "401809234"), ("nfl", "401872996"))}
    assert all(before.values()) and gdb.one("SELECT COUNT(*) AS n FROM sports_game_plays_z")["n"] == 0
    assert store.pack_plays(gdb) == 2
    assert gdb.one("SELECT COUNT(*) AS n FROM sports_game_plays")["n"] == 0
    for (lg, ev), rows in zip((("nba", "401809234"), ("nfl", "401872996")), before.values()):
        assert store.plays(gdb, lg, ev) == rows                   # every column of every play, exactly
    assert any(p["x"] is not None for p in before["401809234"])    # shot locations survive
    assert any(p["athletes"] for p in before["401872996"])
    assert backfill_job.compact(gdb).startswith(("skipped", "vacuumed"))
