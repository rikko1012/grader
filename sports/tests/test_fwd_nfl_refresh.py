"""NFL history refresh inside the inference-only forecast job (sports/model/fwd.py refresh_nfl): the current season's
nflverse files reload into the installed copy (earlier seasons untouched), at most every 12 h; every file is fetched
before any table changes, so a source failure keeps the data and the forecast still runs. No network.
Every team, player and number here is a TEST FIXTURE."""
import io

import pytest

from sports.model import datasets, fwd

G = ("season,game_id,gameday,gametime,game_type,week,home_team,away_team,home_score,away_score,spread_line,"
     "total_line,location,stadium_id,roof,temp,wind,espn\n")
S = ("game_id,player_id,player_display_name,position,position_group,team,opponent_team,week,season_type,"
     "receptions,receiving_yards,targets\n")


def _files(weeks):
    games = G + "2022,2022_01_AAA_BBB,2022-09-11,13:00,REG,1,BBB,AAA,20,17,3,44,Home,S1,outdoors,70,5,1\n"
    stats = S
    for wk in weeks:
        gid = f"2026_{wk:02d}_KC_BAL"
        games += f"2026,{gid},2026-10-{10 + wk:02d},13:00,REG,{wk},BAL,KC,24,21,2.5,47.5,Home,S2,dome,,,{900 + wk}\n"
        stats += f"{gid},P1,Test Receiver,WR,WR,BAL,KC,{wk},REG,{wk},{10 * wk},{wk + 2}\n"
    return {"games.csv": games, "players.csv": "gsis_id,espn_id,pfr_id\nP1,E1,PFR1\n",
            "stats_player_week_2026.csv": stats, "snap_counts_2026.csv": "game_id,pfr_player_id,team,position,"
            "offense_snaps,offense_pct\n", "play_by_play_2026.csv.gz": "game_id,posteam,play_type\n"}


@pytest.fixture
def hist(tmp_path, monkeypatch):
    monkeypatch.delenv("SPORTS_FORECAST_REFRESH", raising=False)
    monkeypatch.delenv("SPORTS_FORECAST_NFL_REFRESH_H", raising=False)
    p = tmp_path / "fwd_history.db"
    con = datasets.connect(p)
    con.execute("INSERT INTO nfl_pg (game_id, player_id, season, week, rec) VALUES ('2022_01_AAA_BBB', 'P0', 2022, 1, 7)")
    con.commit()
    datasets.nfl_build(con, seasons=(2026,), files=_files([7]))                  # the package: through week 7
    con.close()
    return p


def _rows(p):
    con = datasets.connect(p)
    try:
        return con.execute("SELECT season, week, rec FROM nfl_pg ORDER BY season, week").fetchall()
    finally:
        con.close()


def test_the_prefetch_list_is_exactly_what_the_build_reads(tmp_path, monkeypatch):
    seen = []

    def fake(con, name, url, gz=False):
        seen.append((name, url, gz))
        return io.StringIO(_files([7])[name])
    monkeypatch.setattr(datasets, "_download", fake)
    datasets.nfl_build(datasets.connect(tmp_path / "h.db"), seasons=(2026,))
    assert seen == [(n, u, g) for n, u, g, _ in datasets.nfl_files(2026)]


def test_refresh_adds_the_new_week_keeps_earlier_seasons_and_waits_12_hours(hist):
    out = fwd.refresh_nfl("2026-10-27T12:00:00Z", path=hist, files=_files([7, 8]))
    assert out["state"] == "ok" and out["season"] == 2026 and out["stats_2026"] == 2
    assert [tuple(r) for r in _rows(hist)] == [(2022, 1, 7.0), (2026, 7, 7.0), (2026, 8, 8.0)]
    assert fwd.refresh_nfl("2026-10-27T20:00:00Z", path=hist, files=_files([7, 8, 9]))["state"] == "fresh"
    assert fwd.refresh_nfl("2026-10-28T01:00:00Z", path=hist, files=_files([7, 8, 9]))["state"] == "ok"
    assert [tuple(r)[1] for r in _rows(hist)] == [1, 7, 8, 9]


def test_a_source_failure_changes_nothing(hist):
    before = _rows(hist)
    out = fwd.refresh_nfl("2026-10-27T12:00:00Z", path=hist)              # no cached files; the network is off
    assert out["state"] == "failed" and "ConnectionError" in out["error"]
    assert _rows(hist) == before
    con = datasets.connect(hist)
    assert con.execute("SELECT v FROM meta WHERE k = 'nfl_refresh_at'").fetchone() is None    # retried next run
    con.close()


def test_off_switch_and_season_mapping(hist, monkeypatch):
    assert fwd.nfl_season("2026-10-24T00:00:00Z") == 2026 and fwd.nfl_season("2027-01-20T00:00:00Z") == 2026
    assert fwd.nfl_season("2027-03-05T00:00:00Z") == 2027
    assert fwd.refresh_nfl("2027-09-10T00:00:00Z", path=hist)["state"] == "skipped"   # not a ds-1 season
    monkeypatch.setenv("SPORTS_FORECAST_REFRESH", "0")
    assert fwd.refresh_nfl("2026-10-27T12:00:00Z", path=hist) == {"state": "off"}


def test_the_forecast_job_refreshes_nfl_only_when_it_forecasts_nfl(monkeypatch):
    calls = []
    monkeypatch.setenv("SPORTS_FORECAST", "1")
    monkeypatch.setattr(fwd, "verify_artifacts", lambda: {"state": "ok"})
    monkeypatch.setattr(fwd, "install_history", lambda: {"state": "present"})
    monkeypatch.setattr(fwd, "refresh_nba", lambda now: calls.append("nba") or {"state": "ok"})
    monkeypatch.setattr(fwd, "refresh_nfl", lambda now: calls.append("nfl") or {"state": "ok"})
    monkeypatch.setattr(fwd, "open_history", lambda: (_ for _ in ()).throw(RuntimeError("stop after refresh")))
    for leagues, want in ((["nfl"], ["nfl"]), (["nba"], ["nba"]), (["nba", "nfl"], ["nba", "nfl"])):
        calls.clear()
        with pytest.raises(RuntimeError, match="stop after refresh"):
            fwd.run(leagues)
        assert calls == want
