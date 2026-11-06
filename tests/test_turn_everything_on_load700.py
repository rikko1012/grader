"""700 members at once with the running server's DEFAULT_ON applied (owner 2026-11-05 "Turn everything on": parity board fixes,
Kelly % prefs, line history, +EV board, slip header v2) and letter containment left OFF: /me, the CS2 board, the +EV board and a
PrizePicks slip recheck each. No 5xx, one shared board build per window, PrizePicks payouts on the verified table, CS2 letters
exactly as the grader wrote them. Fixture upstreams only."""
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import server
import whop_auth
import workspace_api
import workspace_ev as we
import workspace_books as wb
from tests.test_workspace_api import env, API, records, pair, idem  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


@pytest.fixture
def default_on(monkeypatch):
    env = {}
    server.apply_default_flags(env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SCOUT_LETTER_CONTAINMENT", raising=False)
    workspace_api.reset(); wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    yield


def test_700_members_at_once_with_everything_on(big_queue, env, default_on, monkeypatch):
    recs = records(env)
    v = recs[("vargo", "underdog")]
    assert "parity" in v and v["scout"]["grade"] == env.by("vargo")["grade"]          # letters unchanged (containment off)
    assert not any(x["scout"].get("letter_held") for x in recs.values())
    pp_pair = pair(env, "prizepicks")
    builds = [0]
    lock = threading.Lock()
    real = workspace_api._build

    def counted(srv):
        with lock:
            builds[0] += 1
        time.sleep(0.05)
        return real(srv)
    monkeypatch.setattr(workspace_api, "_build", counted)

    def member(i):
        out, ev = [], None
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=90)
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        try:
            for p in ("me", "board", "pickem-ev"):
                c.request("GET", API + p, headers=cookie)
                r = c.getresponse(); r.read(); out.append(r.status)
            body = json.dumps({"idem": f"load-{i}-{idem()}", "legs": pp_pair, "entry_types": {}}).encode()
            c.request("POST", API + "recheck", body=body, headers=dict(cookie, **{"Content-Type": "application/json"}))
            r = c.getresponse(); raw = r.read(); out.append(r.status)
            if r.status == 200 and i % 50 == 0:
                import gzip
                txt = gzip.decompress(raw) if r.getheader("Content-Encoding") == "gzip" else raw
                ev = json.loads(txt)["entries"]["prizepicks"]["ev"]
        finally:
            c.close()
        return out, ev
    t0 = time.time()
    codes, evs = [], []
    with ThreadPoolExecutor(max_workers=700) as ex:
        for r, ev in ex.map(member, range(700)):
            codes.extend(r)
            if ev is not None:
                evs.append(ev)
    took = time.time() - t0
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}
    assert builds[0] <= 2, builds
    assert evs and all((e.get("payout") or {}).get("multiple") == 3.0 for e in evs), evs[:2]     # verified PrizePicks table
    assert took < 150, took
