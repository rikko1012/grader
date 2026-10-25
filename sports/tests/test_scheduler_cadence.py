"""Game-day collector cadence (owner audit 2026-10-24 bug 1: the board was 65 min old 30 min before tip).

Per league: every 5 min in the 2 h before its earliest upcoming tip, 10 min on a game day, 15 min otherwise; the board
build starts right after each successful collector cycle."""
import json
import sys
import threading
import time

import pytest

from sports import scheduler

PY = sys.executable
NOW = 1_793_000_000.0                       # fixed clock


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slate(root, league, starts, status="scheduled"):
    p = root / league / "slate.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"events": [{"start_utc": _iso(t), "status": status} for t in starts]}))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "JOBS", {})
    for k in ("SPORTS_INGEST_HOT_S", "SPORTS_INGEST_GAMEDAY_S", "SPORTS_INGEST_IDLE_S", "SPORTS_INGEST_HOT_H"):
        monkeypatch.delenv(k, raising=False)


def test_phase_and_interval_follow_the_earliest_upcoming_tip(tmp_path):
    _slate(tmp_path, "nba", [NOW + 90 * 60, NOW + 5 * 3600])            # tip in 90 min
    _slate(tmp_path, "nfl", [NOW + 3 * 86400])                           # Sunday
    assert scheduler.league_phase("nba", NOW, tmp_path) == "hot"
    assert scheduler.league_phase("nfl", NOW, tmp_path) == "idle"
    assert scheduler.cadence_every(("gameday", ("nba",)), NOW, tmp_path) == 300
    assert scheduler.cadence_every(("gameday", ("nfl",)), NOW, tmp_path) == 900
    _slate(tmp_path, "nba", [NOW + 6 * 3600])                            # later today
    assert scheduler.cadence_every(("gameday", ("nba",)), NOW, tmp_path) == 600
    _slate(tmp_path, "nba", [NOW - 3600], status="in_progress")          # under way, nothing upcoming
    assert scheduler.league_phase("nba", NOW, tmp_path) == "gameday"
    _slate(tmp_path, "nba", [NOW - 3600], status="final")
    assert scheduler.league_phase("nba", NOW, tmp_path) == "idle"
    assert scheduler.league_phase("wnba", NOW, tmp_path) == "unknown"
    assert scheduler.cadence_every(("gameday", ("wnba",)), NOW, tmp_path) == 600   # no slate yet: game-day pace
    _slate(tmp_path, "nba", [NOW + 30 * 60])
    assert scheduler.cadence_every(("kalshi", ("nba", "nfl")), NOW, tmp_path) == 1200


def test_default_jobs_have_one_collector_per_league_with_the_cadence(monkeypatch):
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    j = scheduler.default_jobs()
    for lg in ("nba", "nfl"):
        s = j[f"ingest-{lg}"]
        assert s["cadence"] == ("gameday", (lg,)) and s["then"] == ("ui-snapshots",)
        assert s["argv"][3:5] == ["ingest", lg] and "--bucket-min" in s["argv"] and s["cls"] == "exclusive"
    assert "ingest" not in j
    assert "--no-results" in j["ui-snapshots"]["argv"]
    monkeypatch.setenv("SPORTS_JOB_INGEST_NBA_EVERY_S", "420")          # an explicit override pins the interval
    j = scheduler.default_jobs()
    assert j["ingest-nba"]["every_s"] == 420 and "cadence" not in j["ingest-nba"]


def test_phase_change_pulls_the_next_cycle_in_and_snapshots_follow_each_cycle(tmp_path):
    clock = [NOW]
    _slate(tmp_path, "nba", [NOW + 3 * 3600])                            # game day, tip in 3 h -> 600 s
    jobs = {"ingest-nba": dict(argv=[PY, "-c", "pass"], every_s=900, cadence=("gameday", ("nba",)),
                               then=("snap",), timeout_s=20, flag="SPORTS_API", cap_mb=50, cls="exclusive", prio=3,
                               first_s=0.0),
            "snap": dict(argv=[PY, "-c", "pass"], every_s=10_000, timeout_s=20, flag="SPORTS_API", cap_mb=50,
                         cls="heavy", prio=2, first_s=10_000)}
    sch = scheduler.Scheduler(jobs=jobs, budget=300, now=lambda: clock[0], snap_root=tmp_path)
    sch.tick()
    assert "ingest-nba" in sch.running
    sch.running["ingest-nba"][0].wait(20)
    sch.tick()                                                           # exits -> next in ~600 s, snap due now
    st = scheduler.JOBS["ingest-nba"]
    assert 560 <= st["next_start"] - NOW <= 600
    assert scheduler.JOBS["snap"]["next_start"] <= clock[0]
    assert any(ev == "then" and n == "snap" for _t, ev, n, _d in sch.history)
    clock[0] = NOW + 61 * 60                                             # 1 h 59 min before tip: hot
    sch._cad.clear()
    sch.due()
    assert st["every_s"] == 300 and st["next_start"] <= NOW + 300 + 1
    for _n, (p, _t) in list(sch.running.items()):
        p.wait(20)


def test_alert_watches_every_league_collector():
    from sports import service
    now = time.time()
    o = {"flags": {"SPORTS_INGEST": True}, "scheduler_alive": True, "feeds": {}, "rss_mb": 50, "rss_max_mb": 160,
         "service": {"requests": 1000, "rejected_busy": 0, "errors_5xx": 0}, "token_configured": True,
         "db_pool": {"waits_timed_out": 0, "queries_interrupted": 0},
         "scheduler": {"uptime_s": 100000, "jobs": {
             "ingest-nba": {"enabled": True, "every_s": 300, "last_ok_at": now - 60},
             "ingest-nfl": {"enabled": True, "every_s": 900, "last_ok_at": now - 4 * 3600},
             "ingest-kalshi": {"enabled": True, "every_s": 3600, "last_ok_at": now - 60}}}}
    down = [a for a in service.alerts(o) if a["code"] == "worker_down"]
    assert [a["detail"]["job"] for a in down] == ["ingest-nfl"]
