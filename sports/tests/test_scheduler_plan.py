"""Plan-aware memory budget (2026-10-25: scout-sports-api moved from Starter 512 MB / 0.5 CPU to Standard 2 GB / 1 CPU).
Budget and caps follow SPORTS_MEMORY_MB (else the cgroup limit, else 512); on 2 GB two heavy jobs may run together when
they share no store write lock - one league's board build beside the other league's collector - and exclusive model
jobs still run alone."""
import sys
import threading
import time

import pytest

from sports import scheduler

PY = sys.executable


def test_growing_standard_store_jobs_have_headroom_within_existing_budget(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    jobs = scheduler.default_jobs()
    assert 223 < jobs["settle"]["cap_mb"] < scheduler.budget_mb()
    assert jobs["prebuilt"]["timeout_s"] == 600
    assert 612 < jobs["model-forward"]["cap_mb"] < scheduler.budget_mb()
    monkeypatch.setenv("SPORTS_WORKER_MAX_VM_MB", "320")
    assert scheduler._child_env(jobs["settle"])["SPORTS_WORKER_MAX_VM_MB"] == "1280"
    assert scheduler._child_env(jobs["ingest-nba"])["SPORTS_WORKER_MAX_VM_MB"] == "320"
    monkeypatch.setenv("SPORTS_JOB_SETTLE_VM_MB", "1024")
    assert scheduler._child_env(scheduler.default_jobs()["settle"])["SPORTS_WORKER_MAX_VM_MB"] == "1024"
    monkeypatch.setenv("SPORTS_MEMORY_MB", "512")
    monkeypatch.delenv("SPORTS_CHILD_BUDGET_MB")
    jobs = scheduler.default_jobs()
    assert jobs["settle"]["cap_mb"] == 120
    assert jobs["prebuilt"]["timeout_s"] == 120
    assert scheduler._child_env(jobs["settle"])["SPORTS_WORKER_MAX_VM_MB"] == "320"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "JOBS", {})
    for k in ("SPORTS_CHILD_BUDGET_MB", "SPORTS_MAX_HEAVY", "SPORTS_HEAVY_SLOTS"):
        monkeypatch.delenv(k, raising=False)


def test_memory_source_order(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    assert scheduler.memory_mb() == 2048 and scheduler.memory_source() == "SPORTS_MEMORY_MB"
    monkeypatch.delenv("SPORTS_MEMORY_MB")
    monkeypatch.setattr(scheduler, "_cgroup_limit_mb", lambda: 2048)
    assert scheduler.memory_mb() == 2048 and scheduler.memory_source() == "cgroup"
    monkeypatch.setattr(scheduler, "_cgroup_limit_mb", lambda: None)
    assert scheduler.memory_mb() == 512 and scheduler.memory_source() == "default 512"


def test_budget_caps_and_heavy_slots_scale_with_the_plan(monkeypatch):
    assert (scheduler.budget_mb(512), scheduler.max_heavy(512), scheduler.cap_scale(512)) == (314, 1, 1.0)
    assert (scheduler.budget_mb(2048), scheduler.max_heavy(2048)) == (1466, 2)
    assert abs(scheduler.cap_scale(2048) - (1466 - 120) / 600) < 1e-9                  # the pair fits the budget
    assert abs(scheduler.cap_scale(8192, budget=6000) - 7 / 3) < 1e-9                   # never above x7/3
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    monkeypatch.setenv("SPORTS_MEMORY_MB", "512")
    small = scheduler.default_jobs()
    assert small["ui-snapshots-nfl"]["cap_mb"] == 300 and small["ingest-nfl"]["cls"] == "exclusive"
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    big = scheduler.default_jobs()
    assert big["ui-snapshots-nba"]["cap_mb"] == big["ui-snapshots-nfl"]["cap_mb"] == 673
    assert big["ingest-nba"]["cls"] == "heavy" and big["ingest-nba"]["cap_mb"] == 673
    assert big["model-forward"]["cls"] == "exclusive" and big["lab"]["cls"] == "exclusive"
    assert scheduler.SERVICE_MB + scheduler.budget_mb(2048) <= 0.75 * 2048
    pair = big["ui-snapshots-nba"]["cap_mb"] + big["ingest-nfl"]["cap_mb"]
    assert pair + scheduler.LIGHT_RESERVE_MB <= scheduler.budget_mb(2048)
    monkeypatch.setenv("SPORTS_JOB_UI_SNAPSHOTS_NBA_CAP_MB", "650")             # an explicit cap still wins
    assert scheduler.default_jobs()["ui-snapshots-nba"]["cap_mb"] == 650


def test_the_owners_saved_standard_env_is_coherent(monkeypatch):
    """Render env saved from the cloud runbook (2026-10-25): SPORTS_CHILD_BUDGET_MB=1200, SPORTS_HEAVY_SLOTS=2. The
    reservation total is honoured and the caps shrink so the board-build + collector pair still fits it."""
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    monkeypatch.setenv("SPORTS_HEAVY_SLOTS", "2")
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    assert scheduler.budget_mb() == 1200 and scheduler.max_heavy() == 2
    j = scheduler.default_jobs()
    assert j["ui-snapshots-nba"]["cap_mb"] == j["ingest-nfl"]["cap_mb"] == 540
    assert j["ui-snapshots-nba"]["cap_mb"] + j["ingest-nfl"]["cap_mb"] + scheduler.LIGHT_RESERVE_MB <= 1200
    monkeypatch.setenv("SPORTS_HEAVY_SLOTS", "1")
    assert scheduler.max_heavy() == 1 and scheduler.default_jobs()["ingest-nba"]["cls"] == "exclusive"


def test_standard_plan_cadences(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    j = scheduler.default_jobs()
    assert j["prebuilt"]["every_s"] == 45 and j["ui-snapshots-nba"]["every_s"] == 60
    assert j["settle"]["every_s"] == j["picks-settle"]["every_s"] == j["slips-settle"]["every_s"] == 900
    assert j["model-forward"]["every_s"] == 1800                                 # exclusive: not tightened on 1 CPU
    c = scheduler.cadence_settings()
    assert (c["hot_s"], c["gameday_s"], c["idle_s"], c["kalshi_hot_s"], c["kalshi_idle_s"]) == (180, 300, 600, 600, 1800)
    monkeypatch.setenv("SPORTS_MEMORY_MB", "512")
    c = scheduler.cadence_settings()
    assert (c["hot_s"], c["gameday_s"], c["idle_s"], c["kalshi_hot_s"], c["kalshi_idle_s"]) == (300, 600, 900, 1200, 3600)
    j = scheduler.default_jobs()
    assert j["prebuilt"]["every_s"] == 90 and j["ui-snapshots-nba"]["every_s"] == 180 and j["settle"]["every_s"] == 3600


def _spec(cls, locks=None, league=None, s=1.0, prio=5):
    d = dict(argv=[PY, "-c", f"import time; time.sleep({s})"], every_s=0.2, timeout_s=20, flag="SPORTS_API",
             cap_mb=100, cls=cls, prio=prio, first_s=0.0)
    if locks:
        d["locks"] = locks
    if league:
        d["league"] = league
    return d


def _run(sch, seconds):
    t = threading.Thread(target=sch.run, daemon=True)
    t.start()
    time.sleep(seconds)
    sch.stop()
    t.join(30)


def _pairs(sch):
    open_, iv = {}, []
    for t, ev, name, _d in sch.history:
        if ev == "start":
            open_[name] = t
        elif ev == "exit" and name in open_:
            iv.append((name, open_.pop(name), t))
    out = set()
    for a, s1, e1 in iv:
        for b, s2, e2 in iv:
            if a < b and s1 < e2 and s2 < e1:
                out.add((a, b))
    return out


def test_two_heavy_only_without_a_shared_write_lock():
    coll = ("canonical", "books", "games", "cache")
    jobs = {"build-nba": _spec("heavy", ("snapshots:nba",), "nba"),
            "ingest-nfl": _spec("heavy", coll, "nfl"),
            "ingest-nba": _spec("heavy", coll, "nba"),
            "audit": _spec("heavy")}                                             # no locks declared: always alone
    sch = scheduler.Scheduler(jobs=jobs, budget=1466, tick_s=0.05, max_heavy_n=2)
    _run(sch, 6)
    pairs = _pairs(sch)
    assert ("build-nba", "ingest-nfl") in pairs                                 # the intended overlap happens
    assert ("ingest-nba", "ingest-nfl") not in pairs                            # both write the canonical store
    assert ("build-nba", "ingest-nba") not in pairs                             # same league: the build follows
    assert not any("audit" in p for p in pairs)


def test_exclusive_still_runs_alone_on_the_big_plan():
    jobs = {"build-nba": _spec("heavy", ("snapshots:nba",), "nba", s=0.6),
            "ingest-nfl": _spec("heavy", ("canonical",), "nfl", s=0.6),
            "model": _spec("exclusive", s=0.6, prio=1)}
    sch = scheduler.Scheduler(jobs=jobs, budget=1466, tick_s=0.05, max_heavy_n=2)
    _run(sch, 5)
    assert not any("model" in p for p in _pairs(sch))
    assert any(ev == "start" and n == "model" for _t, ev, n, _d in sch.history)


def test_starter_plan_keeps_one_heavy_at_a_time():
    jobs = {"build-nba": _spec("heavy", ("snapshots:nba",), "nba"),
            "ingest-nfl": _spec("heavy", ("canonical",), "nfl")}
    sch = scheduler.Scheduler(jobs=jobs, budget=314, tick_s=0.05, max_heavy_n=1)
    _run(sch, 4)
    assert _pairs(sch) == set()


def test_ops_state_reports_the_plan(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    st = scheduler.state()
    assert st["memory_mb"] == 2048 and st["budget_mb"] == 1466 and st["max_heavy"] == 2
    assert st["memory_source"] == "SPORTS_MEMORY_MB"


def test_long_jobs_can_share_the_second_heavy_slot_but_never_a_collector_write(monkeypatch):
    """2026-10-31: without locks settle / sheets / history waited ~50 min for an EMPTY heavy slot."""
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    jobs = scheduler.default_jobs()
    pair = scheduler.Scheduler._pairable
    for name in ("settle", "sheets", "history", "player-context"):
        assert pair(jobs[name], jobs["ui-snapshots-nba"]), name
        assert jobs[name]["cap_mb"] + jobs["ui-snapshots-nba"]["cap_mb"] <= 1200, name
    for name in ("settle", "history"):
        for writer in ("ingest-nba", "ingest-nfl", "ingest-kalshi"):
            assert not pair(jobs[name], jobs[writer]), (name, writer)
    assert not pair(jobs["settle"], jobs["history"])
    assert not pair(jobs["settle"], jobs["grades-daily"])
    assert jobs["sheets"]["timeout_s"] >= 420
    assert jobs["prebuilt"]["urgent_after_s"] == 300
    assert jobs["ingest-nba"]["urgent_after_s"] == jobs["ingest-nfl"]["urgent_after_s"] == 900

