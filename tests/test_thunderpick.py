"""Thunderpick partner odds - phase 1 groundwork (2026-10-13): shared request budget, backoff, empty / stale feeds,
validation, the proportional margin-adjusted estimate, append-only history, and the key never leaking."""
import json
import sqlite3

import pytest
import requests

import thunderpick as T

KEY = "3F2A9C6D-7B14-4E58-9D02-1C6A8F4E2B77"      # the guide's published sample, not a real key


def ev(**kw):
    e = {"name": "1X2", "team1": "Team Spirit", "team2": "FaZe", "team1Odds": 1.62, "team2Odds": 2.30,
         "matchUrl": "https://thunderpick.io/match/2399001", "startTime": "2026-10-14 15:00:00",
         "competition": "CS2", "markets": [{"name": "Map Handicap", "selections": {"Team Spirit": 1.9, "FaZe": 1.85},
                                             "baseLine": "-1.5", "isMainLine": True}]}
    e.update(kw)
    return e


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def private(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THUNDERPICK_API_KEY", KEY)


def test_two_way_is_the_proportional_margin_adjusted_estimate():
    r = T.two_way(1.62, 2.30)
    qa, qb = 1 / 1.62, 1 / 2.30
    assert abs(r["p_a"] - qa / (qa + qb)) < 1e-6 and abs(r["p_a"] + r["p_b"] - 1) < 1e-9
    assert abs(r["margin"] - (qa + qb - 1)) < 1e-6 and r["method"] == "proportional margin-adjusted market estimate"
    assert T.two_way(1.0, 2.0) is None and T.two_way("x", 2.0) is None and T.two_way(-2, 2.0) is None


def test_reversed_order_gives_mirrored_probabilities():
    a, b = T.two_way(1.62, 2.30), T.two_way(2.30, 1.62)
    assert abs(a["p_a"] - b["p_b"]) < 1e-9 and abs(a["margin"] - b["margin"]) < 1e-9


def test_normalize_flags_problems_and_never_normalizes_a_draw():
    out = T.normalize([ev(), ev(team1Odds=0), ev(drawOdds=3.4), ev(team2=""), ev(startTime="14/10/2026"),
                       ev(matchUrl="https://evil.example/match/1"), ev(matchUrl="http://thunderpick.io/match/5"), "x"], "t")
    assert out[0]["problem"] is None and out[0]["two_way"]["p_a"] > 0.5 and out[0]["source_event_id"] == "2399001"
    assert out[0]["start_tz"] == "unconfirmed"
    assert "invalid" in out[1]["problem"] and out[1]["two_way"] is None
    assert out[2]["draw_present"] and out[2]["two_way"] is None                    # three-way: never a two-team probability
    assert "missing team" in out[3]["problem"] and "startTime" in out[4]["problem"]
    assert out[5]["url"] is None and "approved" in out[5]["problem"]
    assert out[6]["url"] is None                                                    # plain http is never rendered
    assert out[7]["problem"] == "event is not an object"


def test_inventory_reports_fields_markets_and_duplicates():
    inv = T.inventory([ev(), ev(), ev(team1="Vitality", drawOdds=None, extraField=1)])
    assert inv["events"] == 3 and inv["duplicate_events"] == 1 and inv["main_market_names"] == {"1X2": 3}
    assert "extraField" in inv["event_fields"] and inv["market_names"]["Map Handicap"] == 3
    assert inv["match_url_hosts"] == {"thunderpick.io": 3} and inv["events_with_draw_odds"] == 0


def test_budget_caps_requests_in_any_minute():
    b = T.RequestBudget(limit=8, window=60)
    assert all(b.take(100 + i) for i in range(8)) and not b.take(110)
    assert 0 < b.wait_s(110) <= 60 and b.take(161)


def poller(fetch_fn, clock):
    return T.Poller(fetch_fn=fetch_fn, budget=T.RequestBudget(8, 60), poll_s=15, clock=clock)


def test_empty_feed_is_current_and_failure_keeps_stale_data(tmp_path):
    c = Clock()
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        if calls["n"] == 1:
            return [ev()], 200, json.dumps([ev()]).encode()
        if calls["n"] == 2:
            return [], 200, b"[]"
        raise T.FetchError("upstream", 503)
    p = poller(f, c)
    assert p.poll_once() and p.snapshot()[1]["current"] and len(p.snapshot()[0]) == 1
    c.t += 15
    assert p.poll_once() and p.snapshot()[1]["empty_feed"] and p.snapshot()[0] == []      # empty = empty, not stale
    c.t += 15
    assert p.poll_once() is False and p.next_delay == 30                                  # 5xx: back off 30 s
    c.t += 100
    events, fresh = p.snapshot()
    assert fresh["stale"] and not fresh["current"] and "retrieval" in fresh["time_basis"]
    con = sqlite3.connect(tmp_path / ".cache" / "thunderpick.db")
    assert con.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0] == 3
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM raw_snapshots")                                            # append-only


def test_rate_limit_waits_for_the_window_and_budget_skips():
    c = Clock()

    def f():
        raise T.FetchError("rate_limited", 429)
    p = poller(f, c)
    assert p.poll_once() is False and p.next_delay == 60 and p.rate_limited == 1
    c.t += 60
    p.poll_once()
    assert p.next_delay == 120                                                              # exponential after a window
    full = T.Poller(fetch_fn=f, budget=T.RequestBudget(1, 60), clock=c)
    full.poll_once()
    assert full.poll_once() is None and full.attempts == 1                                  # retries share the budget


def test_auth_and_path_errors_retry_slowly():
    c = Clock()

    def f():
        raise T.FetchError("auth", 401)
    p = poller(f, c)
    p.poll_once()
    assert p.next_delay == T.CONFIG_RETRY_S and p.status()["error_kind"] == "auth"


def test_fetch_maps_statuses_and_never_returns_the_key():
    class R:
        def __init__(self, st, body=b"[]"):
            self.status_code, self.content = st, body

    class S:
        def __init__(self, r):
            self.r, self.seen = r, None

        def get(self, url, headers=None, timeout=None):
            self.seen = (url, headers, timeout)
            return self.r
    s = S(R(200, b"[]"))
    assert T.fetch(s)[0] == [] and s.seen[0] == T.BASE + T.PATH and s.seen[1]["api-key"] == KEY
    assert s.seen[0].startswith("https://") and s.seen[2] == T.TIMEOUT
    for st, kind in ((429, "rate_limited"), (401, "auth"), (403, "auth"), (404, "path"), (502, "upstream")):
        with pytest.raises(T.FetchError) as e:
            T.fetch(S(R(st)))
        assert e.value.kind == kind and KEY not in str(e.value)
    with pytest.raises(T.FetchError) as e:
        T.fetch(S(R(200, b'{"a":1}')))
    assert e.value.kind == "bad_shape"

    class Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError(f"failed with header api-key {KEY}")
    with pytest.raises(T.FetchError) as e:
        T.fetch(Boom())
    assert KEY not in str(e.value) and e.value.kind == "network"


def test_admin_view_and_status_never_contain_the_key():
    c = Clock()
    p = T.Poller(fetch_fn=lambda: ([ev()], 200, json.dumps([ev()]).encode()), clock=c)
    p.poll_once()
    T.POLLER, old = p, T.POLLER
    try:
        out = json.dumps(T.admin_view(), default=str)
    finally:
        T.POLLER = old
    assert KEY not in out and "inventory" in out and "main_market_confirmed" in out


def test_no_probability_reaches_members_until_the_main_market_is_confirmed():
    assert T.MAIN_MARKET_CONFIRMED == frozenset()


def test_admin_route_requires_the_admin_token():
    import server
    src = open(server.__file__, encoding="utf-8").read()
    assert 'u.path == "/admin/thunderpick.json" and self.authed(q.get("token"))' in src


def test_key_diagnostic_lists_names_only(monkeypatch):
    monkeypatch.setenv("THUNDERPICK_KEY", "secret-value-123")
    d = T.key_diagnostic()
    assert "THUNDERPICK_KEY" in d["related_names"] and "secret-value-123" not in json.dumps(d) and d["present"] is True
    monkeypatch.delenv("THUNDERPICK_API_KEY")
    assert T.key_diagnostic()["present"] is False and not T.enabled()


def test_render_key_name_is_accepted(monkeypatch):
    monkeypatch.delenv("THUNDERPICK_API_KEY")
    monkeypatch.setenv("THUNDER_PICK_API", KEY)
    assert T.enabled() and T._key() == KEY
