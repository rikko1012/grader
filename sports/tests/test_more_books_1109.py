"""more-books-1109: extra The Odds API regions (SPORTS_ODDSAPI_EXTRA_REGIONS, default off). No network: requests are
faked. The added books are priced (cap-3 applies to their own lines), never pick'em, never a consensus source."""
from datetime import datetime, timezone

import pytest

from sports import board_view, market_view
from sports.books import ODDSAPI_DISPLAY_ONLY, ODDSAPI_EXTRA_BOOKS, ODDSAPI_PICKEM_KEYS, ledger, oddsapi_display_only, \
    sources
from sports.model import market

NOW = "2026-11-09T18:00:00Z"
TEAMS = [{"name": "Boston Celtics", "abbr": "BOS"}, {"name": "New York Knicks", "abbr": "NYK"}]


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _book(key, line=20.5, over=1.91, under=1.91):
    return {"key": key, "title": key, "last_update": "2026-11-09T17:59:00Z", "markets": [
        {"key": "player_points", "last_update": "2026-11-09T17:59:00Z", "outcomes": [
            {"name": "Over", "description": "Jalen Brunson", "price": over, "point": line},
            {"name": "Under", "description": "Jalen Brunson", "price": under, "point": line}]}]}


class R:
    def __init__(self, body):
        self.body, self.status_code, self.headers = body, 200, {}

    def json(self):
        return self.body


class Resolver:
    def event(self, day, away, home):
        return None, "test: no store"

    def player(self, *a):
        return None, "test"


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setenv("SPORTS_ODDSAPI", "1")
    monkeypatch.setenv("ODDS_API_KEY", "k" * 32)
    monkeypatch.setitem(ledger.LIMITS, "oddsapi", {"monthly": 100000, "reserve": 0, "gap_s": 0.0, "per_min": 10000})
    monkeypatch.setattr(sources.ledger, "time", type("T", (), {"time": staticmethod(lambda: _ts(NOW)),
                                                             "sleep": staticmethod(lambda s: None)}))
    calls, stored = [], []

    def fake_get(url, params=None, headers=None, timeout=30, tries=1):
        calls.append((url, dict(params or {})))
        if url.endswith("/events"):
            return R([{"id": "g1", "commence_time": "2026-11-10T00:30:00Z"}])
        regions = (params or {}).get("regions")
        books = ([_book("draftkings"), _book("prizepicks")] if regions == "us" else
                 [_book("hardrockbet"), _book("novig", over=1.25, under=4.2), _book("kalshi"), _book("prizepicks"),
                  _book("courtside"), _book("limitless")])
        return R({"id": "g1", "commence_time": "2026-11-10T00:30:00Z", "home_team": "Boston Celtics",
                  "away_team": "New York Knicks", "bookmakers": books})

    monkeypatch.setattr(sources.net, "get", fake_get)
    monkeypatch.setattr(sources.store, "put_many", lambda con, recs: stored.extend(recs) or {"inserted": len(recs),
                                                                                             "revision": 0})

    def go():
        calls.clear(), stored.clear()
        return sources.oddsapi_ingest(None, "nba", NOW, Resolver(), TEAMS), calls, stored
    return go


def test_flag_off_reads_exactly_as_before(run, monkeypatch):
    monkeypatch.delenv("SPORTS_ODDSAPI_EXTRA_REGIONS", raising=False)
    out, calls, stored = run()
    odds = [c for c in calls if c[0].endswith("/odds")]
    assert len(odds) == 1 and odds[0][1]["regions"] == "us"
    assert out["credits"] == 4 and "extra_regions" not in out
    # the 'us' answer's pick'em app is never stored as a priced oddsapi line
    assert {r.platform for r in stored} == {"oddsapi:draftkings"}


def test_extra_regions_one_more_call_only_listed_priced_books(run, monkeypatch):
    monkeypatch.setenv("SPORTS_ODDSAPI_EXTRA_REGIONS", "us2, us_ex,us_dfs,eu,us2")
    out, calls, stored = run()
    odds = [c for c in calls if c[0].endswith("/odds")]
    assert [c[1]["regions"] for c in odds] == ["us", "us2,us_ex"]
    assert out["extra_regions"] == ["us2", "us_ex"] and out["extra_regions_ignored"] == ["us_dfs", "eu", "us2"]
    assert out["credits"] == 4 + 4 * 2                         # markets x regions, the documented cost rule
    assert {r.platform for r in stored} == {"oddsapi:draftkings", "oddsapi:hardrockbet", "oddsapi:novig"}
    assert out["extra_books_dropped"] == {"kalshi": 1, "prizepicks": 1, "courtside": 1, "limitless": 1}
    assert all("apiKey" in c[1] for c in odds)                 # the key goes in params only, never in an output
    assert "k" * 32 not in repr(out)


def test_us_dfs_alone_requests_nothing_extra(run, monkeypatch):
    monkeypatch.setenv("SPORTS_ODDSAPI_EXTRA_REGIONS", "us_dfs")
    out, calls, _ = run()
    assert [c[1]["regions"] for c in calls if c[0].endswith("/odds")] == ["us"]
    assert out["credits"] == 4 and out["extra_regions"] == []


def test_paced_for_the_whole_game(run, monkeypatch):
    monkeypatch.setenv("SPORTS_ODDSAPI_EXTRA_REGIONS", "us2,us_ex")
    asked = []
    real = sources.ledger.paced
    monkeypatch.setattr(sources.ledger, "paced", lambda s, c, **k: asked.append(c) or real(s, c, **k))
    run()
    assert asked == [12]


def test_sets_are_disjoint_and_never_pickem():
    assert not (ODDSAPI_DISPLAY_ONLY & ODDSAPI_PICKEM_KEYS)
    assert "us_dfs" not in ODDSAPI_EXTRA_BOOKS
    assert not ({"kalshi", "polymarket", "polymarket_us", "fanduel", "draftkings", "betmgm"} & ODDSAPI_DISPLAY_ONLY)


@pytest.mark.parametrize("bk", sorted(ODDSAPI_DISPLAY_ONLY))
def test_added_books_are_priced_and_display_only(bk):
    p = f"oddsapi:{bk}"
    assert oddsapi_display_only(p) and not board_view.is_pickem(p)
    assert not market.in_consensus(p)                          # mc-1 / mi-1 source set unchanged
    ok, why = market_view.source_status(p)
    assert not ok and why.startswith("display only")
    # cap-3 applies to the book's own line like any sportsbook: a -1000 side is held, a near-even one is not
    assert board_view.price_cap(p, "over", own_po=0.9) is not None
    assert board_view.price_cap(p, "over", own_po=0.52) is None


def test_registered_books_unchanged():
    for p in ("oddsapi:draftkings", "oddsapi:fanduel", "oddsapi:betmgm", "oddsapi:bovada", "kalshi", "polymarket"):
        assert market.in_consensus(p) and not oddsapi_display_only(p)
    assert market_view.source_status("oddsapi:draftkings") == (True, None)
