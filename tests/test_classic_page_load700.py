"""Audit follow-up 2026-11-06: 700 members at once on the CS2 page '/' (the upgraded page holding the ~488 KB classic page in a
<template>, audit 2026-11-05). Every answer is 200 and byte-identical, gzip on request, and the page is built and compressed ONCE
(memo hit for everyone else) - with SCOUT_CLASSIC_LAZY unset (template) and with it set to 0 (classic built at once, as before).
Fixture upstreams only (no network)."""
import gzip
import hashlib
import http.client
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import server
import whop_auth
from tests.test_workspace_all_sports import env  # noqa: F401

N = 700


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


class CountingMemo(dict):
    def __init__(self):
        super().__init__()
        self.builds = 0
        self.lock = threading.Lock()

    def __setitem__(self, k, v):
        with self.lock:
            self.builds += 1
        super().__setitem__(k, v)


@pytest.mark.parametrize("lazy", [None, "0"], ids=["template", "built-at-once"])
def test_700_members_get_the_cs2_page_built_and_compressed_once(big_queue, env, monkeypatch, lazy):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setattr(whop_auth, "cookie_valid", lambda v: bool(v and v.startswith("good-")))   # a signed-in paid member
    if lazy is None:
        monkeypatch.delenv("SCOUT_CLASSIC_LAZY", raising=False)
    else:
        monkeypatch.setenv("SCOUT_CLASSIC_LAZY", lazy)
    memo = CountingMemo()
    monkeypatch.setattr(server, "_UPGRADE_MEMO", memo)
    pages = [0]
    lock = threading.Lock()
    real_gz = gzip.compress

    def counted_gz(data, *a, **k):
        if b'data-upgrade="cs2"' in data[:4096]:
            with lock:
                pages[0] += 1
        return real_gz(data, *a, **k)
    monkeypatch.setattr(server.gzip, "compress", counted_gz)

    def member(i):
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=120)
        try:
            c.request("GET", "/", headers={"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"})
            r = c.getresponse()
            raw = r.read()
            return r.status, r.getheader("Content-Encoding"), hashlib.sha256(raw).hexdigest(), raw if i == 0 else None
        finally:
            c.close()

    with ThreadPoolExecutor(N) as ex:
        res = list(ex.map(member, range(N)))
    assert all(s == 200 for s, _, _, _ in res), sorted({s for s, _, _, _ in res})
    assert {e for _, e, _, _ in res} == {"gzip"}
    assert len({h for _, _, h, _ in res}) == 1                                  # every member got the same bytes
    html = gzip.decompress(res[0][3])
    assert b'data-upgrade="cs2"' in html
    assert (b'<template id="scout-classic">' in html) is (lazy is None)
    assert memo.builds == 1, memo.builds                                         # built once
    assert pages[0] == 1, pages[0]                                               # compressed once
