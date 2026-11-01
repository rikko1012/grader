"""sports/scheduler.py - the Starter memory-reservation queue with REAL child processes: never two heavy children,
exclusive jobs alone, the memory budget, strict priority, over-cap kills, timeouts, on-demand triggers, child env and
low OS priority."""
import os
import sys
import threading
import time

import pytest

from sports import scheduler

PY = sys.executable


def sleeper(s):
    return [PY, "-c", f"import time; time.sleep({s})"]


def J(argv, cls, cap=50, every=0.2, prio=5, timeout=20, first=0.0, **kw):
    return dict(argv=argv, every_s=every, timeout_s=timeout, flag="SPORTS_API", cap_mb=cap, cls=cls, prio=prio,
                first_s=first, **kw)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "JOBS", {})
    yield


def run_for(sch, seconds):
    t = threading.Thread(target=sch.run, daemon=True)
    t.start()
    time.sleep(seconds)
    sch.stop()
    t.join(30)


def intervals(sch):
    """[(name, start, end)] from the scheduler's own start / exit events."""
    open_, out = {}, []
    for t, ev, name, _d in sch.history:
        if ev == "start":
            open_[name] = t
        elif ev == "exit" and name in open_:
            out.append((name, open_.pop(name), t))
    return out


def overlaps(iv, a_names, b_names):
    bad = []
    for n1, s1, e1 in iv:
        for n2, s2, e2 in iv:
            if (n1, s1) >= (n2, s2) or n1 not in a_names or n2 not in b_names:
                continue
            if s1 < e2 and s2 < e1:
                bad.append((n1, n2))
    return bad


def test_never_two_heavy_children_and_exclusive_runs_alone():
    jobs = {"h1": J(sleeper(0.5), "heavy", prio=1), "h2": J(sleeper(0.5), "heavy", prio=2),
            "h3": J(sleeper(0.4), "heavy", prio=3), "l1": J(sleeper(0.3), "light", cap=20, prio=4),
            "x1": J(sleeper(0.4), "exclusive", cap=200, prio=0, every=1.0)}
    sch = scheduler.Scheduler(jobs=jobs, budget=120, tick_s=0.05, aging_s=0.5)
    run_for(sch, 8)
    iv = intervals(sch)
    names = {n for n, _, _ in iv}
    assert {"h1", "h2", "h3", "l1", "x1"} <= names, iv                            # everything got its turn
    heavy = {"h1", "h2", "h3"}
    assert overlaps(iv, heavy, heavy) == []                                     # never two heavy at once
    assert overlaps(iv, {"x1"}, set(jobs)) == [] and overlaps(iv, set(jobs), {"x1"}) == []   # exclusive alone
    assert scheduler.STATS["peak_reserved_mb"] <= 200                            # light 20 + heavy 50 (or x1 alone)


def test_memory_budget_limits_concurrent_light_jobs():
    jobs = {"a": J(sleeper(0.5), "light", cap=100, prio=1), "b": J(sleeper(0.5), "light", cap=100, prio=2)}
    sch = scheduler.Scheduler(jobs=jobs, budget=150, tick_s=0.05)
    run_for(sch, 4)
    iv = intervals(sch)
    assert {n for n, _, _ in iv} == {"a", "b"} and overlaps(iv, {"a", "b"}, {"a", "b"}) == []
    sch2 = scheduler.Scheduler(jobs={k: dict(v) for k, v in jobs.items()}, budget=250, tick_s=0.05)
    scheduler.JOBS.clear()
    sch2.__init__(jobs={k: dict(v) for k, v in jobs.items()}, budget=250, tick_s=0.05)
    run_for(sch2, 3)
    assert overlaps(intervals(sch2), {"a", "b"}, {"a", "b"})                   # with room they do overlap


def test_a_waiting_exclusive_job_holds_back_later_starts():
    jobs = {"heavy": J(sleeper(1.0), "heavy", prio=2, every=60),                 # runs first (exclusive not due yet)
            "excl": J(sleeper(0.3), "exclusive", cap=200, prio=1, every=60, first=0.2),
            "light": J(sleeper(0.1), "light", cap=10, prio=3, every=60, first=0.4)}
    sch = scheduler.Scheduler(jobs=jobs, budget=300, tick_s=0.05)
    run_for(sch, 4)
    iv = {n: (s, e) for n, s, e in intervals(sch)}
    assert iv["heavy"][0] < iv["excl"][0]                                        # excl waited for the heavy job
    assert iv["light"][0] >= iv["excl"][1] - 0.01                                # light (lower prio) waited for excl
    assert scheduler.JOBS["excl"]["last_exit"]["code"] == 0


def test_a_child_over_its_memory_cap_is_killed_and_retried_later():
    hog = [PY, "-c", "import time; b = bytearray(160 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096]); time.sleep(20)"]
    jobs = {"hog": J(hog, "heavy", cap=60, every=60, timeout=60), "after": J(sleeper(0.1), "heavy", prio=9, every=60)}
    sch = scheduler.Scheduler(jobs=jobs, budget=300, tick_s=0.05)
    run_for(sch, 6)
    st = scheduler.JOBS["hog"]
    assert str(st["last_exit"]["code"]).startswith("killed_over_cap") and st["over_cap"] == 1
    assert st["last_exit"]["ran_s"] < 10 and st["next_start"] > time.time() + 30   # backed off, not hot-looping
    assert scheduler.JOBS["after"]["last_exit"]["code"] == 0                      # the queue moved on
    assert scheduler.STATS["over_cap_kills"] >= 1


def test_timeout_kills_a_hung_child():
    sch = scheduler.Scheduler(jobs={"hung": J(sleeper(60), "heavy", every=60, timeout=1.0)}, budget=100, tick_s=0.05)
    run_for(sch, 3)
    assert scheduler.JOBS["hung"]["last_exit"]["code"] == "timeout" and scheduler.JOBS["hung"]["last_exit"]["ran_s"] < 3


def test_on_demand_job_starts_only_when_its_trigger_says_so():
    calls = {"n": 0, "queued": False}

    def trig():
        calls["n"] += 1
        return calls["queued"]
    jobs = {"assistant": dict(argv=sleeper(0.1), every_s=None, trigger="t", timeout_s=10, flag="SPORTS_API", cap_mb=20,
                              cls="light", prio=0)}
    sch = scheduler.Scheduler(jobs=jobs, budget=100, tick_s=0.05, triggers={"t": trig})
    t = threading.Thread(target=sch.run, daemon=True)
    t.start()
    time.sleep(0.6)
    assert scheduler.JOBS["assistant"]["starts"] == 0 and calls["n"] > 3          # polled, not started
    calls["queued"] = True
    time.sleep(0.8)
    calls["queued"] = False
    time.sleep(0.4)
    sch.stop()
    t.join(10)
    assert scheduler.JOBS["assistant"]["starts"] >= 1 and scheduler.JOBS["assistant"]["last_exit"]["code"] == 0


def test_flag_off_jobs_never_start(monkeypatch):
    monkeypatch.delenv("SPORTS_INGEST", raising=False)
    jobs = {"x": dict(J(sleeper(0.1), "heavy"), flag="SPORTS_INGEST")}
    sch = scheduler.Scheduler(jobs=jobs, budget=100, tick_s=0.05)
    run_for(sch, 1)
    assert scheduler.JOBS["x"]["starts"] == 0 and not scheduler.JOBS["x"]["enabled"]


def test_children_get_single_thread_math_and_low_priority(tmp_path):
    out = tmp_path / "env.txt"
    code = ("import os, sys; open(sys.argv[1], 'w').write(','.join(os.environ.get(k, '') for k in "
            "('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MALLOC_ARENA_MAX', 'X_EXTRA'))); import time; time.sleep(1)")
    spec = J([PY, "-c", code, str(out)], "heavy", env={"X_EXTRA": "ok"})
    p = scheduler.spawn(spec)
    time.sleep(0.4)
    if os.name == "posix":
        assert os.getpriority(os.PRIO_PROCESS, p.pid) >= 10
    else:
        import psutil
        assert psutil.Process(p.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
    p.wait(10)
    assert out.read_text() == "1,1,2,ok"


def test_default_job_caps_fit_the_starter_budget():
    """Starter sizing (RUNBOOK 'Starter memory budget'): every non-exclusive job fits the child budget; an exclusive
    job runs alone; service steady RSS (<= 100 MB measured with the slim slip pool) + the largest reservation stays
    under the 512 MB container with headroom."""
    jobs = scheduler.default_jobs()
    budget = scheduler.budget_mb()
    service_steady = 100
    for name, s in jobs.items():
        if s["cls"] == "exclusive":
            assert service_steady + s["cap_mb"] <= 450, name
        else:
            assert s["cap_mb"] <= budget, name
    assert service_steady + budget <= 450
    lights = [s["cap_mb"] for s in jobs.values() if s["cls"] == "light"]
    assert all(c <= 120 for c in lights)


def test_ops_keeps_the_name_of_a_timed_out_job(monkeypatch):
    monkeypatch.setitem(scheduler.STATS, "recent_timeouts", [])
    sch = scheduler.Scheduler(jobs={"hung": J(sleeper(60), "heavy", every=60, timeout=0.2)}, budget=100, tick_s=0.05)
    t = threading.Thread(target=sch.run)
    t.start()
    time.sleep(1.0)
    sch.stop()
    t.join(10)
    assert scheduler.state()["stats"]["recent_timeouts"][0]["job"] == "hung"

