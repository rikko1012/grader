"""Player-page context from the factor engine (sports/player_context.py): one engine payload per (player, stat), values
with source + as-of, no look-ahead, NFL snap share from nflverse, only truly unavailable items listed as not held."""
import sqlite3

import pytest

from sports import config, player_context
from sports.games import collect, espn_game, store
from sports.model import datasets
from sports.tests.conftest import fixture
from sports.tests.test_ui_snapshots import built  # noqa: F401 - shared fixture

NBA = config.league("nba")


@pytest.fixture
def nba_store(tmp_path, monkeypatch):
    db = store.connect(tmp_path / "games.db")
    g = espn_game.parse(fixture("games/espn_summary_nba_401809234.json"), "nba", "basketball", venue={"indoor": True},
                        now_iso="2026-10-23T22:00:00Z")
    store.save_game(db, g)
    collect.derive_game(db, NBA, g["game"], g["plays"], g["players"])
    db.close()
    monkeypatch.setattr(player_context, "_games_db", lambda: store.connect(tmp_path / "games.db"))
    allen = next(p["athlete_id"] for p in g["players"] if p["player"] == "Jarrett Allen")
    return g["game"], allen


def _wanted(gm, allen):
    return {f"nba:player:espn:{allen}": {"team_id": f"nba:team:espn:{gm['away_id']}",
                                         "opp_id": f"nba:team:espn:{gm['home_id']}", "event_id": "nba:espn:next",
                                         "start_utc": "2025-10-24T23:00:00Z", "home": False,
                                         "stats": ["rebounds", "points"]}}


def test_engine_values_per_stat_with_source_and_label(nba_store):
    gm, allen = nba_store
    out, summ = player_context.build("nba", "basketball", "2025-10-23T02:00:00Z", _wanted(gm, allen))
    sec = out[f"nba:player:espn:{allen}"]
    assert summ["computed"] == 2 and sec["label"] == player_context.CONTEXT_LABEL
    assert sec["contract"] == "sports-factors/1" and sec["as_of"].startswith("2025-10-23T02:00")
    rows = {r["key"]: r for r in sec["by_stat"]["rebounds"]}
    assert rows["min_l5"]["value"] == 28.0 and rows["min_l5"]["source"] == "ESPN box score"
    assert all(set(r) >= {"key", "label", "group", "value", "source", "status"} for r in rows.values())
    assert "usg_l10" in rows and "pace_team" in rows                     # usage + pace come from the engine
    assert not any("weight" in r for r in rows.values())
    on = rows["on_off_net"]                                              # engine's on/off, with its sample
    assert on["label"] == player_context.ON_OFF_LABEL and on["n"] == 1 and on["on_court_min"] > 0
    assert not any("On/off" in x["factor"] for x in sec["not_held"])
    # licensed / unavailable items are in not_held with a one-word reason, never in missing
    assert "potential_assists" not in sec["missing"]["rebounds"]
    assert {x["factor"] for x in sec["not_held"]} == {f for f, _ in player_context.NOT_HELD["basketball"]}
    assert all(" " not in x["reason"] for x in sec["not_held"])


def test_no_look_ahead_game_not_finished(nba_store):
    gm, allen = nba_store
    out, _ = player_context.build("nba", "basketball", "2025-10-23T01:00:00Z", _wanted(gm, allen))  # game in progress
    rows = {r["key"] for r in out[f"nba:player:espn:{allen}"]["by_stat"]["rebounds"]}
    assert "min_l5" not in rows and "stat_l3" not in rows


def test_missing_store_is_explained_not_raised(monkeypatch):
    monkeypatch.setattr(player_context, "_games_db", lambda: None)
    out, summ = player_context.build("nba", "basketball", "2026-10-23T20:00:00Z",
                                     {"nba:player:espn:1": {"team_id": "nba:team:espn:1", "opp_id": "nba:team:espn:2",
                                                            "event_id": "nba:espn:9", "start_utc": "2026-10-24T00:00:00Z",
                                                            "home": True, "stats": ["points"]}})
    assert out["nba:player:espn:1"]["by_stat"] == {} and out["nba:player:espn:1"]["why_none"]


def test_nfl_snap_share_from_nflverse_point_in_time(tmp_path, monkeypatch):
    monkeypatch.setattr(player_context, "_games_db", lambda: None)
    hp = tmp_path / "history.db"
    con = datasets.connect(hp)
    con.execute("INSERT INTO nfl_ids VALUES ('G1', '777', 'P1')")
    for i, (start, pct) in enumerate([("2026-09-14T17:00:00Z", 0.5), ("2026-09-21T17:00:00Z", 0.7),
                                      ("2026-09-28T17:00:00Z", 0.9), ("2026-10-05T17:00:00Z", 1.0)]):
        con.execute("INSERT INTO games (league, game_id, season, start_utc, status) VALUES ('nfl', ?, 2026, ?, "
                    "'final')", (f"g{i}", start))
        con.execute("INSERT INTO nfl_pg (game_id, player_id, offense_pct) VALUES (?, 'G1', ?)", (f"g{i}", pct))
    con.commit()
    con.close()
    w = {"nfl:player:espn:777": {"team_id": "nfl:team:espn:1", "opp_id": "nfl:team:espn:2", "event_id": "nfl:espn:9",
                                 "start_utc": "2026-10-12T17:00:00Z", "home": True, "stats": ["rec_yds"]}}
    # 2026-10-05 game started 17:00Z: not known until +6 h -> at 20:00Z only the first three count
    out, _ = player_context.build("nfl", "football", "2026-10-05T20:00:00Z", w, hist_path=hp)
    rows = {r["key"]: r for r in out["nfl:player:espn:777"]["by_stat"]["rec_yds"]}
    assert rows["snap_share_l3"]["value"] == pytest.approx(0.7) and rows["snap_share_season"]["n"] == 3
    assert "nflverse" in rows["snap_share_l3"]["source"]
    assert not any("Snap" in x["factor"] for x in out["nfl:player:espn:777"]["not_held"])
    out, _ = player_context.build("nfl", "football", "2026-10-06T00:00:00Z", w, hist_path=hp)
    rows = {r["key"]: r for r in out["nfl:player:espn:777"]["by_stat"]["rec_yds"]}
    assert rows["snap_share_l3"]["value"] == pytest.approx((0.7 + 0.9 + 1.0) / 3, abs=1e-4)


def test_snapshot_player_file_carries_engine_section(built):
    import json
    p = json.loads((built["root"] / "players" / "nba_player_espn_3134907.json").read_text(encoding="utf-8"))
    eng = p["factors"]["engine"]
    assert eng["label"] == player_context.CONTEXT_LABEL and eng["why_none"]          # no games store in the test
    assert p["factors"]["not_held"] == [f for f, _ in player_context.NOT_HELD["basketball"]]
    assert not {"Usage rate", "Team / opponent pace", "Game total / spread"} & set(p["factors"]["not_held"])
    man = json.loads((built["root"] / "manifest.json").read_text(encoding="utf-8"))
    assert man["player_context"]["players"] >= 1
