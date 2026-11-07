"""700 members at once on SCOUT_MARKET_EDGE (/me + market-edge for NBA and CS2, gzip): no 5xx, one shared build per sport per
window (single flight), the books' fair chances priced once per board generation, and no provider or sports-service request
from any member (the build reads only the boards already in memory). Fixture data only."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import workspace_ev as we
import workspace_market_edge as wme
import workspace_sports
from gradecore import ev_board
from tests.test_market_edge import board  # noqa: F401
from tests.test_workspace_api import env, API  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def _send(port, path, cookie, tries=6):
    import http.client
    for k in range(tries):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            c.request("GET", path, headers={"Cookie": cookie, "Accept-Encoding": "gzip"})
            r = c.getresponse()
            r.read()
            return r.status
        except ConnectionRefusedError:          # the client OS refused before the server saw it (accept backlog full)
            time.sleep(0.2 * (k + 1))
        finally:
            c.close()
    return 0


def test_700_members_at_once(big_queue, env, board, monkeypatch):
    import whop_auth
    monkeypatch.setenv("SCOUT_MARKET_EDGE", "1")
    monkeypatch.setattr(workspace_sports, "on", lambda h, q, owner: True)
    builds, priced, lock = {}, [0], threading.Lock()
    real_build, real_price = wme.build, ev_board.price_leg

    def count_build(srv, sport, now=None):
        with lock:
            builds[sport] = builds.get(sport, 0) + 1
        time.sleep(0.05)
        return real_build(srv, sport, now)

    def count_price(*a, **k):
        with lock:
            priced[0] += 1
        return real_price(*a, **k)
    monkeypatch.setattr(wme, "build", count_build)
    monkeypatch.setattr(ev_board, "price_leg", count_price)
    paths = ["me", "market-edge?sport=nba", "market-edge?sport=cs2"]
    codes = []

    def member(i):
        return [_send(env.port, API + p, f"{whop_auth.COOKIE}=good-m{i}") for p in paths]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        for r in ex.map(member, range(700)):
            codes.extend(r)
    took = time.time() - t0
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}
    assert set(builds) == {"nba", "cs2"} and max(builds.values()) <= 2, builds        # one shared build per sport per window
    legs = sum(1 for x in we._LEGS.get("v", (None, None, []))[2])
    assert priced[0] <= 2 * max(legs, 1), (priced, legs)                               # each leg-side priced once per window
    assert took < 120, took
