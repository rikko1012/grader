"""700 members at once on the SCOUT_PICKEM_EV route (/me + pickem-ev, two apps): no 5xx, one priced build per
(parameters, sports seen) per window (single flight), the fair chances priced once per board generation. Fixture data only."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import workspace_books as wb
import workspace_ev as we
from gradecore import ev_board
from tests.test_workspace_api import env, API  # noqa: F401
from tests.test_pickem_ev import rec, book, snap


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_at_once(big_queue, env, monkeypatch):
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    items = []
    for i in range(150):
        app = "prizepicks" if i % 2 else "underdog"
        items.append((rec(f"p{i}", 20.5 + i % 7, app=app, mult=None if app == "prizepicks" else 1.0),
                      [book("fanduel", 20.5 + i % 7, 1.8, 2.0, 60), book("betrivers", 19.5 + i % 7, 1.85, 1.95, 60),
                       book("kalshi", 20.5 + i % 7, "0.50 / 0.52", None, 30, kind="mid")]))
    s = snap(items)
    real = wb.records
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r for r, _ in items], s) if sport == "nba" else real(srv, sport))
    builds, priced, lock = {}, [0], threading.Lock()
    real_build, real_price = we.build, ev_board.price_leg

    def count_build(srv, p, allowed, now=None):
        with lock:
            builds[(p, tuple(sorted(allowed)))] = builds.get((p, tuple(sorted(allowed))), 0) + 1
        time.sleep(0.05)
        return real_build(srv, p, allowed, now)

    def count_price(*a, **k):
        with lock:
            priced[0] += 1
        return real_price(*a, **k)
    monkeypatch.setattr(we, "build", count_build)
    monkeypatch.setattr(ev_board, "price_leg", count_price)
    paths = ["me", "pickem-ev", "pickem-ev?app=underdog"]
    codes = []

    def member(i):
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
    # PrizePicks payouts are unverified, so ?app=prizepicks (the default) and ?app=underdog normalise to the same Underdog
    # parameters: one build for all 700 members, not one per member
    assert len(builds) == 1 and max(builds.values()) <= 2, builds
    assert priced[0] <= 2 * 2 * len(items), priced                          # each leg-side priced once per window (both apps share it)
    assert took < 120, took
