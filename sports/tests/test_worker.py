"""Worker: flag gates, idempotent + resumable jobs, retries with jitter, timeouts, budgets, offline ingestion."""
import pytest

from sports import adapters, flags, store, worker
from sports.tests import helpers as h
from sports.tests.conftest import fixture


@pytest.fixture
def db():
    con = store.connect()
    yield con
    con.close()


def test_jobs_are_idempotent(db):
    calls = []
    jobs = worker.Jobs(db, sleep=lambda s: None)
    assert jobs.run("k1", "t", lambda ctx: calls.append(1) or {"n": 1})["state"] == "done"
    assert jobs.run("k1", "t", lambda ctx: calls.append(1) or {"n": 1})["state"] == "skipped"
    assert len(calls) == 1


def test_jobs_retry_with_backoff_then_fail(db):
    slept = []
    jobs = worker.Jobs(db, attempts=3, sleep=slept.append)

    def bad(ctx):
        raise ValueError("source down")
    res = jobs.run("k2", "t", bad)
    assert res["state"] == "failed" and res["attempts"] == 3 and "source down" in res["error"]
    assert len(slept) == 2 and all(s > 0 for s in slept)


def test_backoff_has_jitter_and_a_cap():
    xs = {round(worker.net.backoff(1), 6) for _ in range(20)}
    assert len(xs) > 1 and all(3.0 <= x <= 9.0 for x in xs)
    assert all(worker.net.backoff(10) <= 45 for _ in range(10))


def test_jobs_resume_from_their_cursor(db):
    jobs = worker.Jobs(db, attempts=2, sleep=lambda s: None)
    seen = []

    def work(ctx):
        done = set((ctx.cursor or {}).get("done", []))
        for x in ("a", "b", "c"):
            if x in done:
                continue
            if x == "b" and not seen:
                seen.append("crash")
                raise RuntimeError("worker died mid-job")
            done.add(x)
            ctx.checkpoint({"done": sorted(done)})
        return sorted(done)
    res = jobs.run("k3", "t", work)
    assert res["state"] == "done" and res["result"] == ["a", "b", "c"] and res["attempts"] == 2


def test_jobs_time_out(db):
    import time
    jobs = worker.Jobs(db, attempts=1, timeout_s=0.2, sleep=lambda s: None)
    res = jobs.run("k4", "t", lambda ctx: time.sleep(1))
    assert res["state"] == "failed" and "timeout" in res["error"]


def test_ingest_refuses_without_the_flag(db, monkeypatch):
    monkeypatch.delenv("SPORTS_INGEST", raising=False)
    import argparse
    with pytest.raises(flags.FlagOff):
        worker.cmd_ingest(argparse.Namespace(leagues=["nba"], sources="pp", bucket_min=5), db)
    monkeypatch.delenv("SPORTS_TRAIN", raising=False)
    with pytest.raises(flags.FlagOff):
        worker.main(["train"])


def test_store_budget_stops_ingestion(monkeypatch):
    monkeypatch.setattr(worker, "STORE_MAX_MB", -1)
    with pytest.raises(worker.BudgetExceeded):
        worker._check_budget()


def test_removed_offers_only_after_a_successful_read(db):
    lg = {"key": "nba", "family": "basketball"}
    b = adapters.pp_offers(fixture("pp_nba.json"), lg, h.T0, h.resolver())
    worker._store_offers(db, "prizepicks", "nba", b, h.T0, True)
    n = len(b.records)
    smaller = adapters.Batch(records=b.records[:-3])
    out = worker._store_offers(db, "prizepicks", "nba", smaller, "2026-10-23T20:10:00Z", False)
    assert out["removed"] == 0                                         # a failed / partial read removes nothing
    out = worker._store_offers(db, "prizepicks", "nba", smaller, "2026-10-23T20:20:00Z", True)
    assert out["removed"] == 3
    gone = [o for _, o in store.offers(db, league="nba") if o.availability == "removed"]
    assert len(gone) == 3 and len(store.offers(db, league="nba")) == n
    first = store.revisions(db, "offer", "prizepicks", gone[0].offer_id)
    assert [r[1].availability for r in first] == ["open", "removed"]


def test_memory_cap_reports_on_every_os():
    assert isinstance(worker.apply_memory_cap(4096), str)
    r = worker.rss_mb()
    assert r is None or r > 0
