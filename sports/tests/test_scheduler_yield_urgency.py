"""A starved dependent must not lose its slot merely because it yields to a due prerequisite."""
from sports import scheduler
from sports.tests.test_sports_stall_1108 import _j, _sched


def test_ordinary_report_does_not_promote_unrelated_prerequisites(monkeypatch):
    jobs = {
        "history": _j("heavy", 600, 6, locks=("history",)),
        "report": _j("heavy", 600, 7, locks=("report",), yields_to=("history",)),
        "maintenance": _j("heavy", 600, 8, locks=("other",)),
    }
    sch, clock = _sched(monkeypatch, jobs, budget=600)
    scheduler.JOBS["report"]["next_start"] = clock[0] - 4000
    scheduler.JOBS["maintenance"]["next_start"] = clock[0] - 3600
    sch.tick()
    assert set(sch.running) == {"maintenance"}


def test_starved_post_promotes_due_settlement_before_other_maintenance(monkeypatch):
    jobs = {
        "settle": _j("heavy", 600, 4, locks=("model",)),
        "post": _j("heavy", 600, 5, locks=("model",), yields_to=("settle",), inherit_yield_priority=True),
        "maintenance": _j("heavy", 600, 8, locks=("other",)),
    }
    sch, clock = _sched(monkeypatch, jobs, budget=600)
    scheduler.JOBS["post"]["next_start"] = clock[0] - 4000
    scheduler.JOBS["maintenance"]["next_start"] = clock[0] - 3600
    sch.tick()
    assert set(sch.running) == {"settle"}


def test_inherited_urgency_reserves_the_blocked_prerequisite_slot(monkeypatch):
    jobs = {
        "block": _j("heavy", 600, 1, locks=("canonical",)),
        "settle": _j("heavy", 600, 4, locks=("canonical", "model")),
        "post": _j("heavy", 600, 5, locks=("model",), yields_to=("settle",), inherit_yield_priority=True),
        "maintenance": _j("heavy", 600, 8, locks=("other",)),
    }
    sch, clock = _sched(monkeypatch, jobs)
    sch.start("block")
    scheduler.JOBS["post"]["next_start"] = clock[0] - 4000
    sch.tick()
    assert set(sch.running) == {"block"}
    sch.running["block"][0].rc = 0
    clock[0] += 1
    sch.tick()
    assert "settle" in sch.running
