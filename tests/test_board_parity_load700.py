"""700 members at once with every SCOUT_PARITY_* flag on: /me, the CS2 board (parity numbers ride on the one shared build) and one
Report a problem each. No 5xx, one board build per window (single flight), every report one data_issues row. Fixture upstreams only."""
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import board_parity as bpar
import whop_auth
import workspace_api
from tests.test_workspace_api import env, API, records  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_at_once_with_parity_on(big_queue, env, monkeypatch):
    for f in bpar.FLAGS.values():
        monkeypatch.setenv(f, "1")
    workspace_api.reset()
    v = records(env)[("vargo", "underdog")]
    assert "parity" in v
    builds = [0]
    lock = threading.Lock()
    real = workspace_api._build

    def counted(srv):
        with lock:
            builds[0] += 1
        time.sleep(0.05)
        return real(srv)
    monkeypatch.setattr(workspace_api, "_build", counted)
    body = json.dumps({"sport": "cs2", "book": "underdog", "id": v["offer"]["offer_id"], "revision": v["offer"]["revision"], "reason": "line"}).encode()

    def member(i):
        out = []
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=60)
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        try:
            for p in ("me", "board"):
                c.request("GET", API + p, headers=cookie)
                r = c.getresponse(); r.read(); out.append(r.status)
            c.request("POST", API + "report-problem", body=body, headers=dict(cookie, **{"Content-Type": "application/json"}))
            r = c.getresponse(); r.read(); out.append(r.status)
        finally:
            c.close()
        return out
    t0 = time.time()
    codes = []
    with ThreadPoolExecutor(max_workers=700) as ex:
        for r in ex.map(member, range(700)):
            codes.extend(r)
    took = time.time() - t0
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}
    assert builds[0] <= 2, builds                 # the board (with parity) is one shared build per window, not one per member
    with workspace_api.store().reader() as con:
        assert con.execute("SELECT COUNT(*) FROM data_issues").fetchone()[0] == 700
    assert took < 120, took
