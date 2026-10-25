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


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "JOBS", {})
    for k in ("SPORTS_CHILD_BUDGET_MB", "SPORTS_MAX_HEAVY"):
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
    assert abs(scheduler.cap_scale(2048) - 7 / 3) < 1e-9 and scheduler.cap_scale(8192) == scheduler.cap_scale(2048)
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    monkeypatch.setenv("SPORTS_MEMORY_MB", "512")
    small = scheduler.default_jobs()
    assert small["ui-snapshots-nfl"]["cap_mb"] == 300 and small["ingest-nfl"]["cls"] == "exclusive"
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    big = scheduler.default_jobs()
    assert big["ui-snapshots-nba"]["cap_mb"] == big["ui-snapshots-nfl"]["cap_mb"] == 700
    assert big["ingest-nba"]["cls"] == "heavy" and big["ingest-nba"]["cap_mb"] == 700
    assert big["model-forward"]["cls"] == "exclusive" and big["lab"]["cls"] == "exclusive"
    # 25% headroom: service + every heavy pair that may co-run stays inside the plan
    assert scheduler.SERVICE_MB + scheduler.budget_mb(2048) <= 0.75 * 2048
    pair = big["ui-snapshots-nba"]["cap_mb"] + big["ingest-nfl"]["cap_mb"]
    assert pair <= scheduler.budget_mb(2048)
    monkeypatch.setenv("SPORTS_JOB_UI_SNAPSHOTS_NBA_CAP_MB", "650")             # an explicit cap still wins
    assert scheduler.default_jobs()["ui-snapshots-nba"]["cap_mb"] == 650


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
