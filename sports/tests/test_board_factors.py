"""Player-page factor data: the scheduled game-store backfill (sports/games/backfill_job.py), the per-player factor
files (sports/games/board_factors.py) and their place in the player snapshot. Real trimmed ESPN samples, no network."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from sports import config, scheduler, snapshots, store as cstore
from sports.contracts import Event
from sports.games import backfill_job, board_factors, collect, contract, espn_game, store as gstore
from sports.tests import slip_world as w
from sports.tests.conftest import fixture

NBA = config.league("nba")


@pytest.fixture
def gdb(tmp_path):
    db = gstore.connect(tmp_path / "games.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------------------------------- backfill
def test_backfill_resumes_stops_at_the_budget_and_never_relists_a_finished_season(gdb):
    t = [0.0]
    stored = []

    def clock():
        t[0] += 1.0
        return t[0]

    def lister(lg, season, typ):
        return [f"{lg['key']}-{season}-{typ}-{i}" for i in range(5)] if typ == 2 else []

    def ingest(db, lg, ev):
        stored.append(ev)
        gstore.set_setting(db, f"fake:{ev}", "1")
        return "stored"
    real_has = gstore.has_game
    gstore.has_game = lambda db, league, ev, complete=True: gstore.setting(db, f"fake:{ev}") is not None
    try:
        now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        out = backfill_job.run(gdb, ("nba",), budget_s=6, now=now, clock=clock, ingest=ingest, lister=lister)
        assert out["budget"] == "spent" and 0 < len(stored) < 10                 # bounded by the time budget
        first = list(stored)
        backfill_job.run(gdb, ("nba",), budget_s=100, now=now, clock=clock, ingest=ingest, lister=lister)
        assert stored[:len(first)] == first and len(set(stored)) == len(stored) == 10   # resumed, nothing twice
        assert json.loads(gstore.setting(gdb, "backfill:nba:2026:2"))["done"] is True    # previous season finished
        listed = []
        backfill_job.run(gdb, ("nba",), budget_s=100, now=now, clock=clock, ingest=ingest,
                         lister=lambda lg, s, t2: listed.append((s, t2)) or [])
        assert listed == []                       # current season re-listed only after RELIST_H; finished never
    finally:
        gstore.has_game = real_has


# --------------------------------------------------------------------------------------------- factor files
def test_board_factors_write_the_engine_values_the_player_page_shows(gdb, monkeypatch, tmp_path):
    game = espn_game.parse(fixture("games/espn_summary_nba_401809234.json"), "nba", "basketball",
                           venue={"indoor": True}, now_iso="2026-10-23T22:00:00Z")
    gstore.save_game(gdb, game)
    collect.derive_game(gdb, NBA, game["game"], game["plays"], game["players"])
    gm = game["game"]
    allen = next(p["athlete_id"] for p in game["players"] if p["player"] == "Jarrett Allen")
    con = cstore.connect()
    start = "2025-10-24T23:00:00Z"
    cstore.put(con, Event(event_id="nba:espn:9001", league="nba", season=2026, season_type="regular",
                          home_team_id=f"nba:team:espn:{gm['home_id']}", away_team_id=f"nba:team:espn:{gm['away_id']}",
                          start_utc=start, start_source="espn", status="scheduled", observed_at=w.OBS,
                          known_at=w.OBS))
    o = w.offer(allen, "CLE", 9001, line=8.5, stat="rebounds", start=start)
    cstore.put(con, o)
    now = datetime(2025, 10, 23, 3, 0, tzinfo=timezone.utc)                # after the stored game ended
    out = board_factors.build_league("nba", now=now, con=con, db=gdb)
    assert out["players"] == 1 and out["pairs"] == 1, out
    f = board_factors.read("nba", f"nba:player:espn:{allen}")
    v = f["by_stat"]["rebounds"]
    assert f["contract"] == contract.CONTRACT and v["min_l5"] == 28.0 and v["stat_l3"] == 4.0
    assert f["labels"]["usg_l10"][1].startswith("Usage rate") and "Likely defender" in f["not_held"]
    assert "Usage rate" not in " ".join(f["not_held"])                       # computed, not "not held"
    # gone from the board -> its file is removed on the next build
    from dataclasses import replace
    cstore.put(con, replace(o, availability="removed", observed_at="2026-10-23T20:10:00Z",
                            known_at="2026-10-23T20:10:00Z"))
    assert board_factors.build_league("nba", now=now, con=con, db=gdb)["players"] == 0
    assert board_factors.read("nba", f"nba:player:espn:{allen}") is None
    con.close()


def test_nflverse_snap_share_air_yards_and_wopr():
    h = sqlite3.connect(":memory:")
    h.row_factory = sqlite3.Row
    h.execute("CREATE TABLE nfl_pg (player_id, season, week, offense_pct, air_yards_share, wopr)")
    h.executemany("INSERT INTO nfl_pg VALUES ('G1', ?, ?, ?, ?, ?)",
                  [(2026, wk, 0.8 + wk / 100, 0.30, 0.5) for wk in range(1, 8)] + [(2025, 18, 0.5, 0.1, 0.2)])
    u = board_factors.nflverse_usage(h, {"123": "G1"}, "123", 2026)
    assert u["snap_share_l3"] == round((0.87 + 0.86 + 0.85) / 3, 3) and u["snap_share_season"] == round(
        sum(0.8 + wk / 100 for wk in range(1, 8)) / 7, 3)
    assert u["air_yds_share"] == 0.3 and u["wopr"] == 0.5
    assert board_factors.nflverse_usage(h, {}, "123", 2026) == {}


def test_not_held_lists_only_what_no_held_source_has_and_jobs_are_scheduled():
    assert set(snapshots.NOT_HELD["basketball"]) == {"Rebound chances", "Potential assists", "Likely defender"}
    nfl = snapshots.NOT_HELD["football"]
    assert "Snap share" not in nfl and "Route participation" in nfl and "DVOA" in nfl
    for k in ("snap_share_l3", "snap_share_season", "air_yds_share", "wopr"):
        assert contract.REGISTRY["nfl"][k]["source"] == contract.NFLVERSE
    jobs = scheduler.default_jobs()
    assert jobs["games-backfill"]["flag"] == "SPORTS_INGEST" and jobs["games-backfill"]["cls"] == "light"
    assert jobs["board-factors"]["flag"] == "SPORTS_INGEST" and jobs["board-factors"]["cap_mb"] >= 200
