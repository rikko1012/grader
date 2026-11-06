"""pgn-1 sports job (sports/pregame_news.py): injury report, depth charts and pre-game summaries archived change-only
from the service's own reads; a failed read writes nothing; nothing member-facing reads the archive."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pregame_news as core
from sports import config, espn, pregame_news as job, worker
from sports.games import espn_game

FIX = Path(__file__).resolve().parent / "fixtures"
NBA = config.league("nba")
NOW = datetime(2026, 11, 5, 22, 0, tzinfo=timezone.utc)


def _fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _setup(monkeypatch, injuries=None, summary=None, start=NOW + timedelta(hours=1)):
    monkeypatch.setattr(worker, "_espn_injuries_raw", lambda lg: injuries if injuries is not None else _fx("espn_injuries_nba.json"))
    depth = espn.parse_depth(_fx("espn_depth_nba.json"), "basketball", "BOS")
    monkeypatch.setattr(espn, "depth_index", lambda lg, refresh=False: depth)
    monkeypatch.setattr(espn, "slate", lambda lg, hours=36, now=None: [{"id": "401", "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                                        "state": "pre", "completed": False}])
    calls = []

    def summ(lg, eid):
        calls.append(eid)
        return summary if summary is not None else _fx("espn_summary_nba.json")
    monkeypatch.setattr(espn_game, "summary_raw", summ)
    monkeypatch.setattr(job, "datetime", _Frozen)
    return calls


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def test_run_archives_injuries_depth_and_lineups(monkeypatch):
    calls = _setup(monkeypatch)
    out = job.run(["nba"], now=NOW)
    assert out["nba"]["injury"]["new"] > 0
    assert out["nba"]["depth"]["new"] > 0
    assert calls == ["401"] and out["nba"]["games"]["read"] == 1
    con = core.connect(job.db_path())
    kinds = core.stats(con)["kinds"]
    assert "sports:injury" in kinds and "sports:depth" in kinds and "sports:lineup" in kinds
    # no ESPN news text is ever stored
    assert not any("Comment" in r[0] or "news text" in r[0] for r in con.execute("SELECT payload FROM news"))
    # unchanged second pass appends nothing
    n = con.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    out2 = job.run(["nba"], now=NOW)
    assert out2["nba"]["injury"]["new"] == 0 and con.execute("SELECT COUNT(*) FROM news").fetchone()[0] == n
    # a player id the board uses reads back point-in-time
    pid = con.execute("SELECT subject FROM news WHERE kind = 'injury' LIMIT 1").fetchone()[0]
    assert pid.startswith("nba:player:espn:")
    assert job.forecast_news(con, "nba", pid, "2100-01-01T00:00:00Z")[0]["kind"] == "injury"
    assert job.forecast_news(con, "nba", pid, "2000-01-01T00:00:00Z") == []


def test_failed_injury_read_writes_nothing_and_no_tombstones(monkeypatch):
    _setup(monkeypatch)
    job.run(["nba"], now=NOW)

    def boom(lg):
        raise OSError("espn down")
    monkeypatch.setattr(worker, "_espn_injuries_raw", boom)
    out = job.run(["nba"], now=NOW)
    assert out["nba"]["injury"]["ok"] is False
    con = core.connect(job.db_path())
    assert con.execute("SELECT COUNT(*) FROM current WHERE kind = 'injury' AND absent = 1").fetchone()[0] == 0
    monkeypatch.setattr(worker, "_espn_injuries_raw", lambda lg: {"error": "x"})          # not a report
    assert job.run(["nba"], now=NOW)["nba"]["injury"]["ok"] is False


def test_games_outside_the_window_are_not_read(monkeypatch):
    calls = _setup(monkeypatch, start=NOW + timedelta(hours=5))
    job.run(["nba"], now=NOW)
    assert calls == []
    calls = _setup(monkeypatch, start=NOW - timedelta(minutes=5))
    job.run(["nba"], now=NOW)
    assert calls == []


def test_scheduler_has_the_light_job():
    from sports import scheduler
    j = scheduler.default_jobs()["pregame-news"]
    assert j["flag"] == "SPORTS_INGEST" and j["every_s"] == 900 and j["cls"] == scheduler.LIGHT
    assert j["argv"][1:4] == ["-m", "sports.pregame_news", "run"] and j["cap_mb"] <= 80


def test_no_member_surface_imports_the_archive():
    root = Path(__file__).resolve().parents[1]
    for f in list(root.glob("api_*.py")) + [root / "web.py", root / "board_view.py", root / "snapshots.py"]:
        assert "pregame_news" not in f.read_text(encoding="utf-8"), f.name
