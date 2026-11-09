"""Book cadence (owner 2026-11-06, claude/book-cadence): FanDuel / BetRivers NBA / NFL prices were read every 36-60 min
inside the ingest cycle while the board's market chance needs them within 15 min (sports/market_view.py, market.py
MAX_AGE_S) and the Tools +EV sheet within 30 min (gradecore/market_tools.py EV_STALE_S). The rules stay; the fetch is
faster: the "direct" ladder (3 / 5 / 8 min inside 2 / 8 / 24 h) run by the scheduler's own light "books" job, which
checks books.db every minute on a game day and sends nothing unless a game is due. Backoff, storage and memory rules
are unchanged."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import scheduler
from sports import worker as canonical
from sports.books import base, plan, providers, schedule, store
from sports.books import worker as book_worker
from sports.books.base import Event, FetchResult

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db(tmp_path):
    d = store.connect(tmp_path / "books.db", dsn="")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("SPORTS_BOOKS_LADDER", "SPORTS_BOOKS_JOB", "SPORTS_BOOKS_HOT_S", "SPORTS_BOOKS_GAMEDAY_S",
              "SPORTS_BOOKS_IDLE_S", "SPORTS_JOB_BOOKS_EVERY_S", "SPORTS_JOB_BOOKS_CAP_MB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(book_worker.canon, "keys_for", lambda lg, prov, evs, now=None: {
        e.provider_event_id: "espn:401" for e in evs})


class Feed(base.Provider):
    """A direct feed answering from memory; every events() / props() call is one request."""
    kind = "direct"

    def __init__(self, name, games, error=None, blocked=False, status=None):
        self.name, self.books, self.games = name, (name,), games
        self.error, self.blocked, self.status, self.calls = error, blocked, status, []

    def events(self, lg):
        self.calls.append("events")
        return FetchResult(events=[g for g in self.games if g.league == lg["key"]], requests=1, http_status=200)

    def props(self, lg, ev):
        self.calls.append(ev.provider_event_id)
        if self.error:
            return FetchResult(requests=1, error=self.error, blocked=self.blocked, http_status=self.status)
        common = dict(provider=self.name, book=self.name, jurisdiction="oh", league="nba",
                      provider_event_id=ev.provider_event_id, event_name=ev.name, home=ev.home, away=ev.away,
                      start=ev.start, player="P", market="points", stat_label="Points", period="game", line=15.5,
                      market_type="standard", status="open", provider_market_id="m1")
        return FetchResult(offers=[base.make_offer(**common, side="over", price_decimal=1.91),
                                   base.make_offer(**common, side="under", price_decimal=1.91)], requests=1,
                           http_status=200)


def _game(pid, minutes):
    return Event(pid, "nba", "A @ H", "H", "A", _iso(NOW + timedelta(minutes=minutes)))


# ------------------------------------------------------------------------------------------------ the ladder

def test_direct_ladder_keeps_every_game_within_24h_inside_each_consumer_rule():
    from gradecore import market_tools as mt
    from sports.model import market
    lad = schedule.LADDERS[schedule.direct_ladder()]
    assert schedule.direct_ladder() == "direct"
    check = max(scheduler.cadence_settings()[k] for k in ("books_hot_s", "books_gameday_s"))
    consensus = int(re.search(r"CONSENSUS_MAX_AGE_S = (\d+) \* 60", (ROOT / "workspace_markets.py").read_text(
        encoding="utf-8")).group(1)) * 60
    for max_h, every in lad:
        if max_h > 24:
            continue
        worst = every * 60 + check                      # read at the rung, seen by the job at most one check later
        assert worst <= market.MAX_AGE_S <= mt.EV_STALE_S, (max_h, every)
        assert worst <= consensus, (max_h, every)       # Tools +EV consensus of other books (10 min)
    assert 2 <= lad[0][1] <= 5 and lad[0][0] == 2       # 2-5 min in the last 2 h
    assert all(every <= 10 for h, every in lad if h <= 24)
    assert market.MAX_AGE_S == 15 * 60 and mt.EV_STALE_S == 30 * 60   # the rules themselves are not loosened
    assert schedule.ORDER == ("full", "standard", "lean", "minimal")    # the Odds API plan never picks "direct"


def test_the_ladder_copies_match():
    import json
    cfg = json.loads((ROOT / "book_providers.json").read_text(encoding="utf-8"))
    for pid in ("fanduel", "betrivers"):
        feed = cfg["providers"][pid]["feed"]
        assert [tuple(x) for x in feed["ladder"]] == schedule.LADDERS["direct"]
        assert "LADDERS direct" in feed["ladder_note"]


def test_plan_counts_direct_requests_on_the_direct_ladder(monkeypatch):
    starts = {"nba": [_iso(NOW + timedelta(hours=3))]}
    fast = plan.estimate(starts, NOW)
    monkeypatch.setenv("SPORTS_BOOKS_LADDER", "full")
    slow = plan.estimate(starts, NOW)
    assert fast["direct_ladder"] == "direct" and slow["direct_ladder"] == "full"
    assert fast["direct"]["fanduel"] > slow["direct"]["fanduel"]


# ------------------------------------------------------------------------------------------------ pending()

def test_pending_is_a_cheap_due_check_that_matches_run_once(db):
    feed = Feed("fd", [_game("g1", 3 * 60)])                    # 3 h out: the 5-min rung
    provs = {"fd": feed}
    assert book_worker.pending(db, ("nba",), NOW, provs, probe=False)          # no game list yet
    book_worker.run_once(db, ("nba",), NOW, provs, probe=False)
    assert feed.calls == ["events", "g1"]
    assert not book_worker.pending(db, ("nba",), NOW + timedelta(minutes=4), provs, probe=False)
    assert book_worker.pending(db, ("nba",), NOW + timedelta(minutes=5), provs, probe=False)
    book_worker.run_once(db, ("nba",), NOW + timedelta(minutes=5), provs, probe=False)
    assert feed.calls == ["events", "g1", "g1"]


def test_a_due_time_set_under_the_old_ladder_does_not_hold_the_game_back(db, monkeypatch):
    """Live 2026-11-06 22:13-22:20 UTC: after the deploy the books job ran every minute and exited (2.3 s, 36 MB) while
    FanDuel / BetRivers stayed 21 min old - the last read (21:59, inside the ingest cycle, old "full" ladder) had set
    each NBA game's next due time 30 min ahead (22:29), and pending() / run_once honoured it although the direct
    ladder says 3-5 min for games 1-4 h out."""
    feed = Feed("fd", [_game("g1", 2 * 60 + 1), _game("g2", 4 * 60)])          # 2 h 01 m and 4 h out at the read
    monkeypatch.setenv("SPORTS_BOOKS_LADDER", "full")
    book_worker.run_once(db, ("nba",), NOW, {"fd": feed}, probe=False)
    assert store.due(db, "fd", "nba", "g1") == _iso(NOW + timedelta(minutes=30))
    monkeypatch.delenv("SPORTS_BOOKS_LADDER")                                   # the deploy: direct ladder
    later = NOW + timedelta(minutes=14)
    assert book_worker.pending(db, ("nba",), later, {"fd": feed}, probe=False)
    book_worker.run_once(db, ("nba",), later, {"fd": feed}, probe=False)
    assert feed.calls == ["events", "g1", "g2", "g1", "g2"]
    assert store.due(db, "fd", "nba", "g1") == _iso(later + timedelta(minutes=3))   # now on the 3-min rung
    assert not book_worker.pending(db, ("nba",), later + timedelta(minutes=2), {"fd": feed}, probe=False)
    assert book_worker.pending(db, ("nba",), later + timedelta(minutes=3), {"fd": feed}, probe=False)


def test_a_game_crossing_into_a_shorter_rung_is_read_on_the_shorter_rung(db):
    feed = Feed("fd", [_game("g1", 8 * 60 + 2)])                                # 8 h 02 m: the 8-min rung
    book_worker.run_once(db, ("nba",), NOW, {"fd": feed}, probe=False)
    assert store.due(db, "fd", "nba", "g1") == _iso(NOW + timedelta(minutes=8))
    # 3 min later the game is inside 8 h (5-min rung): due time 5 min ahead is allowed, not earlier
    assert not book_worker.pending(db, ("nba",), NOW + timedelta(minutes=3), {"fd": feed}, probe=False)
    assert book_worker.is_due(_iso(NOW + timedelta(minutes=30)), "direct", feed.games[0].start,
                              NOW + timedelta(minutes=3))                       # a 30-min due time would not be


def test_pending_ignores_games_beyond_the_ladder_and_started_games(db):
    feed = Feed("fd", [_game("far", 9 * 24 * 60), _game("live", -10)])
    book_worker.run_once(db, ("nba",), NOW, {"fd": feed}, probe=False)
    assert feed.calls == ["events"]
    assert not book_worker.pending(db, ("nba",), NOW + timedelta(minutes=1), {"fd": feed}, probe=False)


def test_pending_counts_a_due_probe(db, monkeypatch):
    assert book_worker.pending(db, ("nba",), NOW, {}, probe=True)
    monkeypatch.setattr(providers, "probe", lambda b: FetchResult(requests=1, error="refused", blocked=True,
                                                                  http_status=403))
    book_worker.run_once(db, ("nba",), NOW, {}, probe=True)
    assert not book_worker.pending(db, ("nba",), NOW + timedelta(hours=1), {}, probe=True)


# ------------------------------------------------------------------------------------------------ politeness

@pytest.mark.parametrize("status,blocked,wait_s", [(429, False, 60), (503, False, 60), (403, True, 6 * 3600)])
def test_errors_back_off_and_the_minute_job_sends_nothing_while_cooling_down(db, status, blocked, wait_s):
    feed = Feed("fd", [_game("g1", 60)], error=f"HTTP {status}", blocked=blocked, status=status)
    s = book_worker.run_once(db, ("nba",), NOW, {"fd": feed}, probe=False)
    assert feed.calls == ["events", "g1"]                                       # one request, never retried
    assert s["providers"]["fd"]["cooling_down_until"] == _iso(NOW + timedelta(seconds=wait_s))
    for m in range(1, 6):                                                       # the job checks every minute
        t = NOW + timedelta(minutes=m)
        if t < NOW + timedelta(seconds=wait_s):
            assert not book_worker.pending(db, ("nba",), t, {"fd": feed}, probe=False)
    assert feed.calls == ["events", "g1"]


def test_a_403_is_not_retried_inside_the_call(monkeypatch):
    from sports import net
    sent = []

    class R:
        status_code, text, headers = 403, "refused", {}

    monkeypatch.setattr(net, "_wait", lambda host: None)
    with pytest.raises(net.HttpError) as e:                     # even with tries=3 (the book feeds pass tries=1)
        net._request("https://sbapi.oh.sportsbook.fanduel.com/api/event-page", None, None, 30, 3, (200,),
                     lambda: sent.append(1) or R())
    assert e.value.status == 403 and sent == [1]


# ------------------------------------------------------------------------------------------------ the books job

def test_books_command_exits_without_a_request_or_espn_read_when_nothing_is_due(monkeypatch, tmp_path):
    monkeypatch.setattr(canonical, "build_resolver", lambda *a, **k: pytest.fail("no ESPN read when nothing is due"))
    monkeypatch.setattr(canonical, "_ingest_books", lambda *a, **k: pytest.fail("no books pass when nothing is due"))
    monkeypatch.setattr(book_worker, "pending", lambda db, leagues, now=None: False)
    monkeypatch.setattr(canonical, "_check_budget", lambda: None)
    assert canonical.books_league(None, "nba") == {"league": "nba", "due": False}


def test_books_command_runs_one_pass_with_the_resolver_when_due(monkeypatch):
    seen = []
    monkeypatch.setattr(book_worker, "pending", lambda db, leagues, now=None: True)
    monkeypatch.setattr(canonical, "_check_budget", lambda: seen.append("budget"))
    monkeypatch.setattr(canonical, "build_resolver", lambda con, lg, at, out: seen.append("resolver") or "R")
    monkeypatch.setattr(canonical, "_ingest_books", lambda con, lg, r: seen.append(("books", lg["key"], r)) or {"ok": 1})
    out = canonical.books_league("CON", "nba")
    assert seen == ["budget", "resolver", ("books", "nba", "R")] and out["books"] == {"ok": 1}


def test_books_command_keeps_the_store_cap_and_disk_floor(monkeypatch, capsys):
    import argparse
    monkeypatch.setenv("SPORTS_INGEST", "1")
    monkeypatch.setattr(book_worker, "pending", lambda db, leagues, now=None: True)

    def full():
        raise canonical.BudgetExceeded("disk free 10 MB < floor 5000 MB: ingestion paused")
    monkeypatch.setattr(canonical, "_check_budget", full)
    monkeypatch.setattr(canonical, "build_resolver", lambda *a: pytest.fail("paused: nothing is read"))
    with pytest.raises(SystemExit) as e:
        canonical.cmd_books(argparse.Namespace(leagues=["nba", "nfl"]), None)
    assert e.value.code == 3                                    # the scheduler sees a failure, not a silent success


# ------------------------------------------------------------------------------------------------ scheduler

@pytest.fixture
def sched_env(monkeypatch):
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    monkeypatch.setattr(scheduler, "JOBS", {})
    import copy
    monkeypatch.setattr(scheduler, "STATS", copy.deepcopy(scheduler.STATS))   # own counters: peak_reserved_mb must not leak into test_scheduler
    for k in ("SPORTS_CHILD_BUDGET_MB", "SPORTS_MAX_HEAVY", "SPORTS_HEAVY_SLOTS"):
        monkeypatch.delenv(k, raising=False)


def test_books_job_is_light_on_its_own_cadence_and_the_ingest_cycles_leave_books_out(monkeypatch, sched_env):
    j = scheduler.default_jobs()
    b = j["books"]
    assert b["argv"][1:] == ["-m", "sports.worker", "books", "nba", "nfl"]
    assert b["cls"] == scheduler.LIGHT and b["cadence"] == ("books", ("nba", "nfl")) and b["flag"] == "SPORTS_INGEST"
    for lg in ("nba", "nfl"):
        srcs = j[f"ingest-{lg}"]["argv"][j[f"ingest-{lg}"]["argv"].index("--sources") + 1].split(",")
        assert "books" not in srcs and {"espn", "pp", "ud", "polymarket", "games"} <= set(srcs)
    monkeypatch.setenv("SPORTS_BOOKS_JOB", "0")                 # rollback: books back inside the ingest cycles
    j = scheduler.default_jobs()
    assert "books" not in j
    assert "books" in j["ingest-nba"]["argv"][j["ingest-nba"]["argv"].index("--sources") + 1].split(",")


def test_books_cadence_follows_the_slate(tmp_path):
    import json
    t = NOW.timestamp()

    def slate(league, starts):
        p = tmp_path / league / "slate.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"events": [{"start_utc": _iso(datetime.fromtimestamp(s, timezone.utc)),
                                              "status": "scheduled"} for s in starts]}))
    slate("nba", [t + 90 * 60])
    slate("nfl", [t + 2 * 86400])
    assert scheduler.cadence_every(("books", ("nba", "nfl")), t, tmp_path) == 60          # hot
    slate("nba", [t + 6 * 3600])
    assert scheduler.cadence_every(("books", ("nba", "nfl")), t, tmp_path) == 60          # game day
    slate("nba", [t + 2 * 86400])
    # site audit 2026-11-06 N1: a game within 72 h keeps the 1-min check (the ladder reads it every 8 min)
    assert scheduler.cadence_every(("books", ("nba", "nfl")), t, tmp_path) == 60
    slate("nba", [t + 4 * 86400])
    slate("nfl", [t + 4 * 86400])
    assert scheduler.cadence_every(("books", ("nba", "nfl")), t, tmp_path) == 180         # idle


class _Proc:
    _n = 0

    def __init__(self):
        _Proc._n += 1
        self.pid, self.rc = 90000 + _Proc._n, None

    def poll(self):
        return self.rc


def test_a_waiting_or_running_exclusive_job_never_holds_the_books_job_back(monkeypatch, sched_env):
    """Live 2026-11-06 22:19-22:25 UTC: model-forward (exclusive) waited behind settle (heavy, ~20 min), aged ahead of
    every job, and the tick's "hold everything behind a waiting exclusive job" stopped the books job from starting
    (last start 22:19:12, FanDuel / BetRivers 26 min old); a RUNNING exclusive job blocked it for up to 15 min more."""
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    monkeypatch.setenv("SPORTS_FORECAST", "1")
    monkeypatch.setenv("SPORTS_INGEST", "1")
    monkeypatch.setattr(scheduler, "rss_mb", lambda pid: 10.0)
    procs = {}
    monkeypatch.setattr(scheduler, "spawn", lambda spec: procs.setdefault(spec["argv"][3], _Proc()))
    all_jobs = scheduler.default_jobs()
    jobs = {k: all_jobs[k] for k in ("books", "settle", "model-forward")}
    clock = [1_794_000_000.0]
    sch = scheduler.Scheduler(jobs=jobs, budget=scheduler.budget_mb(), now=lambda: clock[0], max_heavy_n=2)
    for n in jobs:
        scheduler.JOBS[n]["next_start"] = clock[0] + 10 ** 6
    scheduler.JOBS["settle"]["next_start"] = clock[0]
    sch.tick()
    assert set(sch.running) == {"settle"}
    scheduler.JOBS["model-forward"]["next_start"] = clock[0] - 720           # waiting 12 min: aged ahead of books
    scheduler.JOBS["books"]["next_start"] = clock[0]
    assert sch.due()[0] == "model-forward"
    sch.tick()
    assert "books" in sch.running and "model-forward" not in sch.running     # books starts behind the waiting job
    procs["books"].rc = 0
    clock[0] += 2
    sch.tick()                                                               # books done; settle still running
    procs["settle"].rc = 0
    procs.pop("books")
    scheduler.JOBS["books"]["next_start"] = clock[0]
    clock[0] += 1
    sch.tick()                                                               # settle ends -> the exclusive starts
    assert "model-forward" in sch.running
    clock[0] += 60
    scheduler.JOBS["books"]["next_start"] = clock[0]
    sch.tick()
    assert "books" in sch.running                                            # ...and books runs beside it
    assert sch.reserved() <= scheduler.budget_mb()


def test_an_exclusive_job_does_not_wait_for_the_books_job_but_others_still_wait_for_it(sched_env):
    jobs = {"books": dict(argv=["x"], every_s=60, cls="light", cap_mb=100, prio=2, flag="SPORTS_API",
                          beside_exclusive=True),
            "excl": dict(argv=["x"], every_s=60, cls="exclusive", cap_mb=300, prio=5, flag="SPORTS_API"),
            "heavy": dict(argv=["x"], every_s=60, cls="heavy", cap_mb=100, prio=3, flag="SPORTS_API"),
            "light": dict(argv=["x"], every_s=60, cls="light", cap_mb=50, prio=4, flag="SPORTS_API")}
    sch = scheduler.Scheduler(jobs=jobs, budget=500, max_heavy_n=2)
    sch.running = {"books": (None, 0)}
    assert sch._fits("excl")                                                 # never waits for the books job
    sch.running = {"excl": (None, 0)}
    # stall 2026-11-09: every light job runs beside an exclusive one when the budget holds both, like books
    assert sch._fits("books") and not sch._fits("heavy") and sch._fits("light")
    sch.running = {"light": (None, 0)}
    assert sch._fits("excl")                                                 # ...and is never waited for
    sch.running = {"heavy": (None, 0)}
    assert not sch._fits("excl")                                             # everything else still runs alone
    small = scheduler.Scheduler(jobs=jobs, budget=350, max_heavy_n=1)       # Starter-sized budget: memory first
    small.running = {"excl": (None, 0)}
    assert not small._fits("books")


def test_books_job_fits_beside_two_heavy_jobs_on_standard(monkeypatch, sched_env):
    """2 GB / 1,200 MB children: board build + other league's collector (2 x 540) + the books job stay in budget, so
    the heavy slots never hold the books job back; on Starter it fits beside the exclusive collector's absence."""
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    j = scheduler.default_jobs()
    pair = j["ui-snapshots-nba"]["cap_mb"] + j["ingest-nfl"]["cap_mb"]
    assert pair + j["books"]["cap_mb"] <= scheduler.budget_mb()
    sch = scheduler.Scheduler(jobs=j, budget=scheduler.budget_mb(), max_heavy_n=2)
    sch.running = {"ui-snapshots-nba": (None, 0), "ingest-nfl": (None, 0)}
    assert sch._fits("books")
    sch.running["settle"] = (None, 0)                            # settle (~20 min) never holds the books job back
    sch.running.pop("ingest-nfl")
    assert sch._fits("books")
    monkeypatch.setenv("SPORTS_MEMORY_MB", "512")
    monkeypatch.delenv("SPORTS_CHILD_BUDGET_MB")
    assert scheduler.default_jobs()["books"]["cap_mb"] == scheduler.BOOKS_CAP_MB == 60
