"""sports/web.py: flags, member / admin gates, read-only store answers."""
import json

import pytest

from sports import store, web
from sports.tests import helpers as h


def _call(path, q=None, allowed=True, admin=False):
    code, body, ctype = web.handle(path, q or {}, allowed=lambda: allowed, admin=lambda: admin)
    return code, (json.loads(body) if ctype == "application/json" else body)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(web, "_CACHE", {})


def test_everything_is_404_with_flags_off(monkeypatch):
    for f in ("SPORTS_UI", "SPORTS_API"):
        monkeypatch.delenv(f, raising=False)
    for p in ("/sports", "/api/sports/status", "/api/sports/offers", "/api/sports/admin/coverage"):
        assert _call(p)[0] == 404


def test_members_only_and_admin_only(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    assert _call("/api/sports/status", allowed=False)[0] == 401
    assert _call("/api/sports/admin/coverage", admin=False)[0] == 403


def test_offers_from_the_store_with_capability(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    assert _call("/api/sports/offers", {"league": "nba"})[0] == 503      # no store yet
    con = store.connect()
    store.put(con, h.offer())
    store.put(con, h.offer(oid="pp:2", variant="goblin"))
    con.close()
    code, body = _call("/api/sports/offers", {"league": "nba"})
    assert code == 200 and body["count"] == 2
    by = {o["offer_id"]: o for o in body["offers"]}
    assert by["pp:1"]["gradable"] and not by["pp:2"]["gradable"]
    assert by["pp:1"]["sides"][0]["side"] == "over" and by["pp:1"]["record_id"].startswith("offer:")
    assert _call("/api/sports/offers", {"league": "mlb"})[0] == 400
    assert _call("/api/sports/offers", {"league": "nba", "limit": "x"})[0] == 400
    code, cov = _call("/api/sports/admin/coverage", admin=True)
    assert code == 200 and "prizepicks:nba" in cov["offers"] and "espn" in cov["sources"]


def test_page_needs_the_ui_flag(monkeypatch):
    monkeypatch.delenv("SPORTS_UI", raising=False)
    assert _call("/sports")[0] == 404
    monkeypatch.setenv("SPORTS_UI", "1")
    code, body = _call("/sports")
    assert code == 200 and b"Scout Sports" in body


def test_single_flight_one_load_for_simultaneous_misses():
    import threading
    import time
    calls = []
    gate = threading.Event()

    def slow():
        calls.append(1)
        gate.wait(2)
        return {"v": 1}
    out = []
    ts = [threading.Thread(target=lambda: out.append(web._cached(("k",), slow))) for _ in range(40)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join(5)
    assert len(calls) == 1 and len(out) == 40 and all(x == {"v": 1} for x in out)


def test_single_flight_shares_a_failure_and_recovers():
    def bad():
        raise RuntimeError("store locked")
    with pytest.raises(RuntimeError):
        web._cached(("k2",), bad)
    assert web._cached(("k2",), lambda: {"ok": True}) == {"ok": True}       # nothing poisoned
