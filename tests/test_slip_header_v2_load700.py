"""700 members at once rechecking slips with SCOUT_SLIP_HEADER_V2 on (review 2026-11-05: the recheck path had no load test).
Three leg sets (the same pair for many members, plus different pairs and books): no 5xx, every answer carries the header, and
gradecore.slip_header.header() runs once per leg set per window (srv.shared), not once per member. Fixture upstreams only."""
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import server
import whop_auth
import workspace_api
from gradecore import slip_header as gsh
from tests.test_workspace_api import API, calibrated_export, env, leg_of, records  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_recheck_with_the_v2_header(big_queue, env, monkeypatch):
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    monkeypatch.setattr(server, "_p1_export", calibrated_export)
    r = records(env)
    sets = [("underdog", [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "underdog")])]),
            ("prizepicks", [leg_of(r[("vargo", "prizepicks")]), leg_of(r[("orlo", "prizepicks")])]),
            ("underdog", [leg_of(r[("orlo", "underdog")]), leg_of(r[("vargo", "underdog")])])]      # same picks, other order: its own set
    builds, lock, real = [0], threading.Lock(), gsh.header

    def counted(*a, **k):
        with lock:
            builds[0] += 1
        time.sleep(0.02)
        return real(*a, **k)
    monkeypatch.setattr(gsh, "header", counted)

    def member(i):
        book, legs = sets[0] if i % 2 == 0 else sets[1 + (i // 2) % 2]       # half on one shared slip, the rest on two others
        body = json.dumps({"idem": f"idem-load-{i:05d}", "legs": legs, "entry_types": {}}).encode()
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=90)
        try:
            c.request("POST", API + "recheck", body=body, headers={"Cookie": f"{whop_auth.COOKIE}=good-h{i}",
                                                                   "Content-Type": "application/json"})
            resp = c.getresponse()
            raw = resp.read()
            doc = json.loads(raw) if resp.status == 200 else None
        finally:
            c.close()
        return resp.status, book, doc
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        got = list(ex.map(member, range(700)))
    took = time.time() - t0
    codes = [s for s, _, _ in got]
    assert codes.count(200) == 700, {c: codes.count(c) for c in set(codes)}
    for _, book, doc in got:
        e = doc["entries"][book]
        assert "slip_header" in e, e.get("ev")
        if book == "prizepicks":                                             # payouts unknown: never rated
            assert all(t["rated"] is False for t in e["slip_header"]["types"].values())
    assert builds[0] <= 2 * len(sets), builds        # one build per leg set per window (single flight), not one per member
    assert took < 120, took
    workspace_api.reset()
