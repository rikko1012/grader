"""700 members at once on the Slip Studio routes the verified PrizePicks payout table changed (owner 2026-11-06):
PrizePicks Power / Flex builds (expected return through the settlement rules) and PrizePicks slip settlement. No 5xx,
every answer carries the verified figures, and the pool is built once (cached). Fixture world only."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from sports import store, web
from sports.slips import log
from sports.tests import slip_world as w


@pytest.fixture
def world(monkeypatch):
    con = store.connect()
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(log, "_SEEN", {})
    from sports.slips import pool as pm
    real = pm.build
    calls = []

    def build(c, lg, pf, now=None, policy="member", **kw):
        calls.append(pf)
        return real(c, lg, pf, now=w.NOW, policy=policy, **kw)
    monkeypatch.setattr(pm, "build", build)
    w.world(con, [(f"pp{i}", f"T{i}", i, "over" if i % 2 else "under", 0.58 + 0.01 * i, {"platform": "prizepicks"})
                  for i in range(1, 7)])
    settled = [w.offer(f"s{i}", f"S{i}", 10 + i, platform="prizepicks") for i in range(1, 4)]
    for o, st in zip(settled, ("win", "win", "void")):
        store.put(con, o)
        store.put(con, w.settlement(o, "over", st, 30))
    yield con, calls, settled
    con.close()


def test_700_members_build_and_settle_prizepicks_slips(world):
    con, calls, settled = world
    slip = {"platform": "prizepicks", "league": "nba", "entry_type": "flex",
            "legs": [{"platform": "prizepicks", "offer_id": o.offer_id, "side": "over", "team": o.team} for o in settled]}
    reqs = [("/api/sports/slips/build", {"league": "nba", "platform": "prizepicks",
                                         "settings": {"sides": "mixed", "sizes": {3: 2}, "entry_type": "power"}}),
            ("/api/sports/slips/build", {"league": "nba", "platform": "prizepicks",
                                         "settings": {"sides": "mixed", "sizes": {3: 2}, "entry_type": "flex"}}),
            ("/api/sports/slips/settle", {"slip": slip})]

    def member(i):
        path, body = reqs[i % len(reqs)]
        code, out, _ = web.dispatch(web.Request(path=path, method="POST", query={}, body=json.dumps(body).encode(),
                                                member=f"m_{i}", role="member"))
        return code, path, json.loads(out)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        out = list(ex.map(member, range(700)))
    took = time.time() - t0
    assert [(c, p, b) for c, p, b in out if c != 200] == []
    for code, path, b in out:
        if path.endswith("settle"):                                    # 3-pick Flex + a DNP -> 2-pick Power, 2 hits
            assert (b["status"], b["payout_multiple"], b["payout_known"]) == ("won", 3.0, True)
        else:
            assert b["ranking"]["by"] == "expected_return" and b["slips"]
            assert all(s["expected_return"]["shown"] and s["payout"]["figures_shown"] for s in b["slips"])
            assert "not published" not in json.dumps(b).lower()
    assert calls.count("prizepicks") <= 2, calls                       # the pool is cached, not rebuilt per member
    assert took < 120, took
