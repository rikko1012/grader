"""Owner site audit 2026-11-06 N1 (board, data side): the NFL tab showed no Market edge row because FanDuel / BetRivers NFL
quotes were ~56 min old - Sunday games more than 24 h out sat on the books ladder's 24-72 h rung, read every 180 min. Every rung
up to 72 h now reads at most every 10 min (8), so a quote read on schedule is inside the freshness limit Market edge, Best prices
and Price check use. The rollback (SPORTS_BOOKS_JOB=0) still puts the books source back inside the ingest cycles."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import scheduler
from sports.books import base, schedule, store
from sports.books import worker as book_worker
from sports.books.base import Event, FetchResult

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)            # Friday afternoon CST


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ("SPORTS_BOOKS_LADDER", "SPORTS_BOOKS_JOB", "SPORTS_BOOKS_HOT_S", "SPORTS_BOOKS_GAMEDAY_S", "SPORTS_BOOKS_IDLE_S",
              "SPORTS_BOOKS_GAMEDAY_H", "SPORTS_INGEST_GAMEDAY_H", "SPORTS_INGEST_HOT_H"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(book_worker.canon, "keys_for", lambda lg, prov, evs, now=None: {e.provider_event_id: "espn:401" for e in evs})


@pytest.fixture
def db(tmp_path):
    d = store.connect(tmp_path / "books.db", dsn="")
    yield d
    d.close()


class Feed(base.Provider):
    kind = "direct"

    def __init__(self, games):
        self.name, self.books, self.games, self.calls = "fd", ("fd",), games, []

    def events(self, lg):
        self.calls.append("events")
        return FetchResult(events=[g for g in self.games if g.league == lg["key"]], requests=1, http_status=200)

    def props(self, lg, ev):
        self.calls.append(ev.provider_event_id)
        common = dict(provider="fd", book="fd", jurisdiction="oh", league="nfl", provider_event_id=ev.provider_event_id,
                      event_name=ev.name, home=ev.home, away=ev.away, start=ev.start, player="P", market="receiving_yards",
                      stat_label="Receiving yards", period="game", line=55.5, market_type="standard", status="open",
                      provider_market_id="m1")
        return FetchResult(offers=[base.make_offer(**common, side="over", price_decimal=1.91),
                                   base.make_offer(**common, side="under", price_decimal=1.91)], requests=1, http_status=200)


def _sunday(hours):
    return Event("sun", "nfl", "A @ H", "H", "A", _iso(NOW + timedelta(hours=hours)))


def _limits():
    """workspace_ev.price_limits() (Market edge / Best prices), read without importing it (sports never imports a CS2 module):
    each kind's gradecore.fair.FRESHNESS_S less both cache windows (2 x workspace_ev.TTL). tests/test_audit_board_data_1106.py
    checks the same numbers against the module itself."""
    from gradecore import fair
    ttl = int(re.search(r"^TTL = (\d+)", (ROOT / "workspace_ev.py").read_text(encoding="utf-8"), re.M).group(1))
    return {k: max(0.0, v - 2 * ttl) for k, v in fair.FRESHNESS_S.items()}


def test_every_rung_within_72h_reads_inside_the_market_edge_freshness_limit():
    from gradecore import market_tools as mt
    from sports.model import market
    lad = schedule.LADDERS[schedule.direct_ladder()]
    check = max(scheduler.cadence_settings()[k] for k in ("books_hot_s", "books_gameday_s"))
    consensus = int(re.search(r"CONSENSUS_MAX_AGE_S = (\d+) \* 60", (ROOT / "workspace_markets.py").read_text(
        encoding="utf-8")).group(1)) * 60
    edge_limit = _limits()["sportsbook"]                  # Market edge / Best prices (workspace_ev.price_limits, 29 min)
    for max_h, every in lad:
        if max_h > 72:
            continue
        assert every <= 10, (max_h, every)                # was 180 on the 24-72 h rung
        worst = every * 60 + check
        assert worst <= edge_limit and worst <= market.MAX_AGE_S and worst <= consensus, (max_h, every)
    assert edge_limit == mt.EV_STALE_S - 60 and market.MAX_AGE_S == 15 * 60   # the freshness rules are not loosened
    for hours in (25, 40, 48, 66, 71.5):                  # Sunday's NFL games seen on Thursday / Friday
        assert schedule.interval_min("direct", _iso(NOW + timedelta(hours=hours)), NOW) <= 10, hours


def test_the_books_job_checks_every_minute_while_a_game_is_within_72h(tmp_path):
    """With no NBA game that day, Sunday's NFL games 40 h out used to leave the books job on its 3-minute idle check, so a quote
    read at the 8-min rung could be 11 min old and miss Price check's 10-min rule. A game inside 72 h now keeps the 1-min check."""
    for lg, hours in (("nba", 100), ("nfl", 40)):
        (tmp_path / lg).mkdir()
        (tmp_path / lg / "slate.json").write_text(json.dumps({"events": [{"start_utc": _iso(NOW + timedelta(hours=hours))}]}),
                                                  encoding="utf-8")
    c = scheduler.cadence_settings()
    every = scheduler.cadence_every(("books", ("nba", "nfl")), NOW.timestamp(), tmp_path)
    assert every == c["books_gameday_s"] == 60
    consensus = int(re.search(r"CONSENSUS_MAX_AGE_S = (\d+) \* 60", (ROOT / "workspace_markets.py").read_text(
        encoding="utf-8")).group(1)) * 60
    assert schedule.interval_min("direct", _iso(NOW + timedelta(hours=40)), NOW) * 60 + every <= consensus
    # the ingest cycles keep their own 18-h game-day window
    assert scheduler.league_phase("nfl", NOW.timestamp(), tmp_path) == "idle"
    (tmp_path / "nfl" / "slate.json").write_text(json.dumps({"events": [{"start_utc": _iso(NOW + timedelta(hours=80))}]}),
                                                 encoding="utf-8")
    assert scheduler.cadence_every(("books", ("nba", "nfl")), NOW.timestamp(), tmp_path) == c["books_idle_s"]


def test_a_sunday_game_two_days_out_is_read_every_few_minutes(db):
    feed = Feed([_sunday(44)])
    provs = {"fd": feed}
    book_worker.run_once(db, ("nfl",), NOW, provs, probe=False)
    assert feed.calls == ["events", "sun"]
    assert not book_worker.pending(db, ("nfl",), NOW + timedelta(minutes=5), provs, probe=False)
    assert book_worker.pending(db, ("nfl",), NOW + timedelta(minutes=8), provs, probe=False)
    book_worker.run_once(db, ("nfl",), NOW + timedelta(minutes=8), provs, probe=False)
    assert feed.calls == ["events", "sun", "sun"]                           # was not read again for 3 h


def test_the_board_freshness_rule_follows_the_new_ladder():
    """book_providers.json carries the books worker's ladder (the board's quote_stale_after reads it; test_books_schedule_sync
    keeps the two equal; tests/test_audit_board_data_1106.py runs workspace_books on it): every game within 72 h is read inside
    the 20-min stale floor and the Market edge limit."""
    cfg = json.loads((ROOT / "book_providers.json").read_text(encoding="utf-8"))
    check = max(scheduler.cadence_settings()[k] for k in ("books_hot_s", "books_gameday_s"))
    for pid in ("fanduel", "betrivers"):
        feed = cfg["providers"][pid]["feed"]
        assert [tuple(x) for x in feed["ladder"]] == schedule.LADDERS["direct"]
        for hours in (3, 30, 48, 70):
            start = _iso(NOW + timedelta(hours=hours))
            worst = schedule.interval_min("direct", start, NOW) * 60 + check     # read on schedule, seen one job check later
            assert worst <= schedule.stale_after_s("direct", start, NOW) and worst <= _limits()["sportsbook"], (pid, hours)


def test_market_edge_prices_a_book_quote_read_on_the_new_cadence():
    """A two-day-out NFL line with two books read 9 min ago (8-min rung + one check) gets a fair chance, not 'stale'."""
    from gradecore import ev_board, fair
    from gradecore.fair import Quote
    now = NOW.timestamp()
    qs = [Quote(book="fanduel", line=55.5, observed_at=now - 9 * 60, over=1.87, under=1.95, cluster="fanduel"),
          Quote(book="betrivers", line=55.5, observed_at=now - 9 * 60, over=1.85, under=1.97, cluster="betrivers")]
    res = ev_board.price_leg(qs, "over", 55.5, now, be=None, p_model=None, p_model_other=None, reader=None,
                             preset="equal", weights=None, method="worst", min_books=2, required_books=(),
                             min_liquidity=0.0, max_width=fair.MAX_WIDTH, freshness=_limits())
    assert res["fair"]["p"] is not None
    old = [Quote(book=q.book, line=q.line, observed_at=now - 56 * 60, over=q.over, under=q.under, cluster=q.cluster) for q in qs]
    res = ev_board.price_leg(old, "over", 55.5, now, be=None, p_model=None, p_model_other=None, reader=None,
                             preset="equal", weights=None, method="worst", min_books=2, required_books=(),
                             min_liquidity=0.0, max_width=fair.MAX_WIDTH, freshness=_limits())
    assert res["fair"]["p"] is None                                        # the audit's 56-min quotes: still never priced


def test_rollback_still_puts_the_books_source_inside_the_ingest_cycles(monkeypatch):
    j = scheduler.default_jobs()
    assert "books" in j
    monkeypatch.setenv("SPORTS_BOOKS_JOB", "0")
    j = scheduler.default_jobs()
    assert "books" not in j
    for lg in ("nba", "nfl"):
        argv = j[f"ingest-{lg}"]["argv"]
        assert "books" in argv[argv.index("--sources") + 1].split(",")
