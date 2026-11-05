"""700 members at once opening CS2 cards with SCOUT_LINE_HISTORY on: no 5xx, the line-history block is built inside /line's shared
single-flight copy (at most one build per card per window, never one per member) and the move stores are tailed by one caller."""
import http.client
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import prizepicks
import whop_auth
import workspace_linehist as lh
from tests.test_workspace_api import env, API  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_open_cards_with_line_history(big_queue, env, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("SCOUT_LINE_HISTORY", "1")
    (tmp_path / ".cache").mkdir(exist_ok=True)
    monkeypatch.setattr(prizepicks, "HISTORY", tmp_path / ".cache" / "pp_lines.jsonl")
    lh.reset()
    cards = [r["id"] for r in env.rows if r["book"] == "underdog"][:4]
    builds, tails, lock = {}, [0], threading.Lock()
    real_block, real_tail = lh.block, lh._tail_pp

    def block(srv, x, *a, **k):
        with lock:
            builds[x["id"]] = builds.get(x["id"], 0) + 1
        time.sleep(0.05)
        return real_block(srv, x, *a, **k)

    def tail(now):
        with lock:
            tails[0] += 1
        return real_tail(now)
    monkeypatch.setattr(lh, "block", block)
    monkeypatch.setattr(lh, "_tail_pp", tail)
    codes, bodies = [], []

    def member(i):
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=60)
        try:
            c.request("GET", API + f"line?book=underdog&id={cards[i % len(cards)]}", headers={"Cookie": f"{whop_auth.COOKIE}=good-m{i}"})
            r = c.getresponse()
            body = r.read()
            return r.status, body
        finally:
            c.close()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        for st, body in ex.map(member, range(700)):
            codes.append(st)
            bodies.append(body)
    took = time.time() - t0
    assert codes.count(200) == 700, {c: codes.count(c) for c in set(codes)}
    assert all(b'"line_history"' in b for b in bodies)
    assert set(builds) == set(cards) and max(builds.values()) <= 2, builds
    assert tails[0] <= 2, tails
    assert took < 120, took
