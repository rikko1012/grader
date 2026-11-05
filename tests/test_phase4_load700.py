"""700 members at once on the Phase 4 routes with every Phase 4 flag on: /me, the extra-book board and the EV / arbitrage sheet.
No 5xx, one shared build per window (single flight), no provider request from any member. Fixture upstreams only."""
import threading

import pytest
import time
from concurrent.futures import ThreadPoolExecutor

import book_providers as bp
import workspace_books as wb
import workspace_markets as wm
from tests.test_workspace_api import env, API  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_at_once(big_queue, env, monkeypatch):
    for f in ("SCOUT_BOOK_SLEEPER", "SCOUT_MARKET_TOOLS", "SCOUT_GENIE", "SCOUT_GENIE_PERSONA"):
        monkeypatch.setenv(f, "1")
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, "start", lambda d: False)
    calls = []
    monkeypatch.setattr(bp, "http_get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("no member request may reach a provider")))
    now = time.time()
    bp.state("sleeper").update(at=now, offers=[{"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills",
                                                "period": "maps12", "subject_id": "7", "player": "vargo", "player_norm": "vargo", "team": "Halcyon",
                                                "team_norm": "halcyon", "game_id": "g", "line": 38.5,
                                                "over": {"offered": True, "payout": 1.8, "price": None}, "under": {"offered": True, "payout": 1.76, "price": None},
                                                "source_at": now - 60, "observed_at": now}])
    builds = {"books": 0, "sheet": 0}
    lock = threading.Lock()
    real_b, real_s = wb.build, wm.build

    def count(name, fn):
        def run(*a, **k):
            with lock:
                builds[name] += 1
            time.sleep(0.05)
            return fn(*a, **k)
        return run
    monkeypatch.setattr(wb, "build", count("books", real_b))
    monkeypatch.setattr(wm, "build", count("sheet", real_s))
    paths = ["me", "book-board?sport=cs2&book=sleeper", "market-tools"]
    codes = []

    def member(i):
        """One kept-alive connection per member, like a browser tab (and no ephemeral-port exhaustion on the test machine)."""
        import http.client
        import whop_auth
        out = []
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=60)
        try:
            for p in paths:
                c.request("GET", API + p, headers={"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"})
                r = c.getresponse()
                r.read()
                out.append(r.status)
        finally:
            c.close()
        return out
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        for r in ex.map(member, range(700)):
            codes.extend(r)
    took = time.time() - t0
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}
    assert builds["books"] <= 2 and builds["sheet"] <= 2, builds          # one shared build per TTL window, not one per member
    assert not calls
    assert took < 120, took
