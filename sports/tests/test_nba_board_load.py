"""NBA board stale, 2026-10-31: the build hit its 240 s timeout (each timeout threw the whole build away) and queued
20-40 min behind long jobs of the same age. The board must go first and its reads must not grow with the store."""
import sys

import pytest

from sports import scheduler, snapshots, store
from sports.tests import helpers as h

PY = sys.executable
NOW = 1_793_000_000.0
ASOF = "2026-10-31T12:00:00Z"
SLEEP = [PY, "-c", "import time; time.sleep(30)"]


@pytest.fixture(autouse=True)
def _flag(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "STATS", dict(scheduler.STATS))      # peaks are process-wide
    monkeypatch.setattr(scheduler, "JOBS", {})


def _sched(jobs, clock, heavy=2):
    return scheduler.Scheduler(jobs=jobs, budget=1200, now=lambda: clock[0], max_heavy_n=heavy)


def _job(cls, prio, locks=None, cap=100, urgent=None):
    d = dict(argv=SLEEP, every_s=60, timeout_s=900, flag="SPORTS_API", cap_mb=cap, cls=cls, prio=prio, first_s=0.0)
    if locks:
        d["locks"] = locks
    if urgent:
        d["urgent_after_s"] = urgent
    return d


def _stop(sch):
    for _n, (p, _t) in list(sch.running.items()):
        p.kill()
        p.wait(20)


def test_overdue_board_build_outranks_a_long_job_that_aged_further():
    clock = [NOW]
    sch = _sched({"board": _job("heavy", 2, ("b",), urgent=300), "long": _job("heavy", 5)}, clock)
    scheduler.JOBS["board"]["next_start"] = NOW - 600
    scheduler.JOBS["long"]["next_start"] = NOW - 3000
    assert sch.due()[0] == "board"
    scheduler.JOBS["board"]["next_start"] = NOW - 100                    # not urgent yet: ordinary aging
    assert sch.due()[0] == "long"


def test_urgent_board_build_is_not_passed_by_jobs_that_would_take_its_slot():
    clock = [NOW]
    jobs = {"run": _job("heavy", 5), "board": _job("heavy", 2, ("b",), urgent=300),
            "other": _job("heavy", 1, ("o",)), "excl": _job("exclusive", 3), "light": _job("light", 6)}
    sch = _sched(jobs, clock)
    for n in jobs:
        scheduler.JOBS[n]["next_start"] = NOW + 10_000
    scheduler.JOBS["run"]["next_start"] = NOW
    sch.tick()
    assert list(sch.running) == ["run"]
    for n in ("board", "other", "excl", "light"):
        scheduler.JOBS[n]["next_start"] = NOW - 600
    scheduler.JOBS["excl"]["next_start"] = NOW - 500
    scheduler.JOBS["light"]["next_start"] = NOW - 900                    # ahead of excl, which holds whatever is behind it
    sch.tick()
    try:
        assert set(sch.running) == {"run", "light"}                      # the slot stays free for the board
        sch.running["run"][0].kill()
        sch.running["run"][0].wait(20)
        scheduler.JOBS["run"]["next_start"] = NOW + 10_000
        sch.tick()
        assert "board" in sch.running
    finally:
        _stop(sch)


def test_board_build_timeout_outlasts_the_slowest_good_build(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    j = scheduler.default_jobs()
    assert j["ui-snapshots-nba"]["timeout_s"] >= 420 and j["ui-snapshots-nba"]["urgent_after_s"] == 300


def _offer_sql(con, league, as_of):
    """The league-wide statements the build used until 2026-10-31."""
    last = {r[0]: r[1] for r in con.execute(
        f"SELECT platform, MAX(COALESCE(last_seen_at, observed_at)) FROM records WHERE kind='offer' AND league = ? "
        f"AND {store.CURRENT_SQL} AND known_at <= ? GROUP BY platform", (league, as_of)) if r[0] and r[1]}
    un = con.execute(f"SELECT COUNT(*) FROM (SELECT event_id, MAX(revision) FROM records WHERE kind='offer' AND "
                     f"league = ? AND {store.CURRENT_SQL} AND known_at <= ? GROUP BY source, source_key) "
                     f"WHERE event_id IS NULL", (league, as_of)).fetchone()[0]
    return last, un


def test_last_read_and_unmatched_match_the_old_league_wide_reads():
    con = store.connect()
    store.put(con, h.offer(oid="m1"))
    store.put(con, h.offer(oid="m1", line=21.5, observed_at="2026-10-24T02:00:00Z"))    # a newer revision
    store.put(con, h.offer(oid="u1", event_id=None))                     # no game
    store.put(con, h.offer(oid="u2", event_id=None))                     # later matched: not unmatched
    store.put(con, h.offer(oid="u2", line=25.5, observed_at="2026-10-24T02:00:00Z"))
    store.put(con, h.offer(platform="underdog", oid="ud1", event_id=None))
    last, un = _offer_sql(con, "nba", ASOF)
    assert un == 2 and set(last) == {"prizepicks", "underdog"}
    assert snapshots._last_read(con, "nba", ASOF) == last
    assert snapshots._unmatched_offers(con, "nba", ASOF) == un
    assert snapshots._last_read(con, "nfl", ASOF) == {} and snapshots._unmatched_offers(con, "nfl", ASOF) == 0
    con.close()


def _history(con, n):
    store.put(con, h.event(eid="nba:espn:1", start="2026-10-01T23:00:00Z"))
    for i in range(n):
        eid = f"nba:espn:{1000 + i}"
        store.put(con, h.event(eid=eid, start=f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T23:00:00Z"))
        store.put(con, h.stat(part="pts", value=float(i), eid=eid))


def test_player_history_decodes_only_the_games_it_returns_and_each_game_once(monkeypatch):
    con = store.connect()
    n = snapshots.MAX_GAMES + 40
    _history(con, n)
    pid = "nba:player:espn:3134907"
    games = snapshots.player_games(con, "nba", pid, ASOF, {})
    assert len(games) == snapshots.MAX_GAMES
    assert [g["event_id"] for g in games] == [f"nba:espn:{1000 + i}" for i in range(n - 1, n - 1 - snapshots.MAX_GAMES, -1)]
    decoded = []
    unpack = store._unpack
    monkeypatch.setattr(store, "_unpack", lambda p: (decoded.append(1), unpack(p))[1])
    cache = {}
    assert snapshots.player_games(con, "nba", pid, ASOF, {}, cache) == games
    first = len(decoded)
    assert first <= n + snapshots.MAX_GAMES + 5                          # every game once + only the newest stats
    decoded.clear()
    assert snapshots.player_games(con, "nba", pid, ASOF, {}, cache) == games
    assert len(decoded) <= snapshots.MAX_GAMES + 5                       # a teammate's history re-decodes no game
    con.close()
