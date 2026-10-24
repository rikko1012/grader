"""Scheduled game-store backfill (sports/games/backfill_job.py): time-bounded, resumable, a finished season never
re-listed. No network: the season lister and the per-game read are injected."""
import json
from datetime import datetime, timezone

import pytest

from sports import scheduler
from sports.games import backfill_job, store as gstore


@pytest.fixture
def gdb(tmp_path):
    db = gstore.connect(tmp_path / "games.db")
    yield db
    db.close()


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


def test_backfill_job_is_scheduled_light_under_the_ingest_flag():
    j = scheduler.default_jobs()["games-backfill"]
    assert j["flag"] == "SPORTS_INGEST" and j["cls"] == "light" and j["timeout_s"] >= 2 * 120
