"""The Odds API budget lasts the month (sports/books/ledger.py paced) and rotates through every upcoming game
(sports/books/sources.py _oddsapi_order / oddsapi_ingest). No network: requests are faked."""
from datetime import datetime, timezone

import pytest

from sports.books import ledger, sources


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


@pytest.fixture
def lim(monkeypatch):
    monkeypatch.setitem(ledger.LIMITS, "oddsapi", {"monthly": 3100, "reserve": 100, "gap_s": 0.0, "per_min": 1000})


def _spend(cost, at):
    con = ledger.connect()
    con.execute("INSERT INTO log (source, at, month, cost, outcome, detail) VALUES ('oddsapi', ?, ?, ?, 'sent', 'x')",
                (_ts(at), at[:7], cost))
    con.close()


def test_the_month_is_shared_evenly_by_day_and_hour(lim):
    # 3,000 usable credits on Oct 2 with 30 days left -> 100 today; by 05:xx UTC 6/24 of it = 25
    assert ledger.paced("oddsapi", 25, now=_ts("2026-10-02T05:10:00Z"))[0] is True
    assert ledger.paced("oddsapi", 26, now=_ts("2026-10-02T05:10:00Z"))[0] is False
    _spend(25, "2026-10-02T05:00:00Z")
    ok, why = ledger.paced("oddsapi", 4, now=_ts("2026-10-02T05:20:00Z"))
    assert not ok and "paced" in why                                             # ahead of the spread: wait
    assert ledger.paced("oddsapi", 4, now=_ts("2026-10-02T06:00:00Z"))[0] is True  # next hour adds ~4.2
    # money left over from quiet days is spread over the days that remain
    assert ledger.paced("oddsapi", 140, now=_ts("2026-10-31T23:00:00Z"))[0] is True


def test_the_providers_own_balance_caps_the_spread(lim):
    ledger.report("oddsapi", 200, remaining=340)                                 # provider says 340 left
    # (340 - 100 reserve) / 30 days = 8 today; by 23:xx the whole 8 -> 9 refused
    assert ledger.paced("oddsapi", 8, now=_ts("2026-10-02T23:30:00Z"))[0] is True
    assert ledger.paced("oddsapi", 9, now=_ts("2026-10-02T23:30:00Z"))[0] is False


def test_games_rotate_least_recently_fetched_first_within_the_horizon(lim):
    now = "2026-10-24T18:00:00Z"
    evs = [{"id": "aaa", "commence_time": "2026-10-24T23:00:00Z"}, {"id": "bbb", "commence_time": "2026-10-25T00:00:00Z"},
           {"id": "ccc", "commence_time": "2026-10-25T01:00:00Z"}, {"id": "started", "commence_time": "2026-10-24T17:00:00Z"},
           {"id": "nextweek", "commence_time": "2026-10-31T17:00:00Z"}]
    assert [e["id"] for e in sources._oddsapi_order(evs, now)] == ["aaa", "bbb", "ccc"]   # never fetched: soonest
    con = ledger.connect()
    con.execute("INSERT INTO log (source, at, month, cost, outcome, detail) VALUES "
                "('oddsapi', ?, '2026-10', 4, 'sent', '/v4/sports/basketball_nba/events/aaa/odds')", (_ts(now) - 600,))
    con.close()
    assert [e["id"] for e in sources._oddsapi_order(evs, now)] == ["bbb", "ccc", "aaa"]   # aaa was just read


def test_the_poller_stops_at_the_pace_instead_of_draining_the_month(lim, monkeypatch):
    monkeypatch.setenv("SPORTS_ODDSAPI", "1")
    monkeypatch.setenv("ODDS_API_KEY", "k" * 32)
    monkeypatch.setitem(ledger.LIMITS, "oddsapi", {"monthly": 450, "reserve": 50, "gap_s": 0.0, "per_min": 1000})
    calls = []

    class R:
        def __init__(self, body):
            self.body, self.status_code, self.headers = body, 200, {}

        def json(self):
            return self.body

    def fake_get(url, params=None, headers=None, timeout=30, tries=1):
        calls.append(url)
        if url.endswith("/events"):
            return R([{"id": f"e{i}", "commence_time": f"2026-10-25T0{i}:00:00Z"} for i in range(6)])
        return R({"id": url.split("/")[-2], "commence_time": "2026-10-25T00:00:00Z", "bookmakers": []})
    monkeypatch.setattr(sources.net, "get", fake_get)
    monkeypatch.setattr(sources.store, "put_many", lambda con, recs: {"inserted": 0, "revision": 0})
    monkeypatch.setattr(sources.ledger, "time", type("T", (), {"time": staticmethod(lambda: _ts("2026-10-24T20:30:00Z")),
                                                             "sleep": staticmethod(lambda s: None)}))
    out = sources.oddsapi_ingest(None, "nba", "2026-10-24T20:30:00Z", None, [])
    # 400 usable / 8 days left = 50 today; by 20:xx 21/24 of it = 43.75 -> 10 NBA calls of 4 would be 40; 6 events cap
    assert out["events"] == 6 and out["credits"] == 24 and "paced" not in out
    out2 = sources.oddsapi_ingest(None, "nba", "2026-10-24T20:30:00Z", None, [])
    assert out2["events"] == 4 and out2["paced"].startswith("paced")             # 24 + 16 = 40; the next 4 would pass 43.75


def test_free_default_never_calls_oddsapi_even_with_saved_key(monkeypatch):
    monkeypatch.delenv("SPORTS_ODDSAPI", raising=False)
    monkeypatch.setenv("ODDS_API_KEY", "invalid-saved-key")
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled provider must not use network or credits")
    monkeypatch.setattr(sources, "_get", forbidden)
    for league in ("nba", "nfl"):
        result = sources.oddsapi_ingest(None, league, "2026-10-28T16:00:00Z", None, [])
        assert result["ok"] and "disabled" in result["skipped"]
    monkeypatch.setenv("SPORTS_ODDSAPI", "1")
    monkeypatch.delenv("ODDS_API_KEY")
    assert sources.oddsapi_ingest(None, "nba", "2026-10-28T16:00:00Z", None, []) == {
        "ok": False, "status": "key not set here"}
