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
    assert scheduler.cadence_every(("books", ("nba", "nfl")), t, tmp_path) == 180         # idle


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
