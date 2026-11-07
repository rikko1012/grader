"""700 members press Generate at once on a pool that sits near break-even (audit review 2026-11-07, T1 follow-up).

The break-even rule (owner audit 2026-11-06 T1) let a slip search run its full 20,000-node budget for every slip it could not fill,
so one Flex 5 / 6 build on an 80-leg pool at 50-56% cost 2-3 s of request-thread CPU, and members asking with different settings
each paid it. Here 700 members spread over 48 different Generate settings (Flex and Power 5 / 6 picks, 8 / 12 slips, goblin rule,
prop reuse, PrizePicks and Underdog) on such a pool: no 5xx, one shared build per setting, every build inside the batch's one node
budget, every slip handed out clears break-even, and the builds' total time stays small. Fixture server, synthetic pool, no network."""
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import product

import pytest

import slip_quality as sq
import whop_auth
import workspace_api
from tests.test_slip_quality import rec
from tests.test_turn_everything_on_load700 import _send, big_queue  # noqa: F401  (fixture)
from tests.test_workspace_api import API, env  # noqa: F401  (fixture)

SETTINGS = [f"generate?platform={pf}&type={et}&picks={n}&slips={k}&sports=cs2&lines={ln}&repeats={rp}"
            for (pf, et), n, k, ln, rp in product((("prizepicks", "flex"), ("prizepicks", "power"), ("underdog", "flex")),
                                                  (5, 6), (8, 12), ("no_goblins", "all"), ("allow", "none"))]


def _near_break_even_pool():
    rnd = random.Random(7)
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    recs = [rec(f"{book[:2]}{i}", f"e{i // 2}", round(rnd.uniform(.50, .56), 4), book=book, start=start)
            for book in ("prizepicks", "underdog") for i in range(80)]
    return sq.pool(recs)


def test_700_members_generate_near_break_even(big_queue, env, monkeypatch):
    assert len(SETTINGS) == 48
    legs, why = _near_break_even_pool()
    monkeypatch.setattr(workspace_api, "_slip_pool", lambda srv, sport, table: (legs, why) if sport == "cs2" else None)
    workspace_api.reset()
    lock = threading.Lock()
    builds, spent = {}, []
    real_build = sq.build

    def timed_build(pool_legs, opts):
        t = time.perf_counter()
        out = real_build(pool_legs, opts)
        assert all(sq.clears_break_even(opts["platform"], opts["entry_type"], x["legs"]) for x in out["slips"])
        with lock:
            spent.append((time.perf_counter() - t, out["search"]["nodes"], opts.get("break_even")))
        return out

    real_gen = workspace_api._generate

    def counted_gen(srv, o):
        k = (o["platform"], o["entry_type"], o["size"], o["count"], o["lines"], o["no_repeat"])
        with lock:
            builds[k] = builds.get(k, 0) + 1
        return real_gen(srv, o)
    monkeypatch.setattr(workspace_api.sq, "build", timed_build)
    monkeypatch.setattr(workspace_api, "_generate", counted_gen)

    def member(i):
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        st, raw = _send(env.port, "GET", API + SETTINGS[i % len(SETTINGS)], cookie)
        return st, (json.loads(raw) if st == 200 else None)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        res = list(ex.map(member, range(700)))
    took = time.time() - t0
    codes = [st for st, _ in res]
    assert codes.count(200) == 700, {c: codes.count(c) for c in set(codes)}
    assert len(builds) == len(SETTINGS) and all(n == 1 for n in builds.values()), builds   # one shared build per setting
    assert all(be for _, _, be in spent)                                                   # the member route always checks break-even
    assert max(n for _, n, _ in spent) <= sq.BUILD_NODE_LIMIT + sq.MAX_SLIPS
    total = sum(s for s, _, _ in spent)
    assert total < 15, (round(total, 2), round(max(s for s, _, _ in spent), 3))           # 48 builds; was ~2-3 s EACH for Flex 5 / 6
    for _, doc in res:                                                                     # (each build checked its slips clear)
        assert all(not s["same_match"] and s["all_hit"] is not None for s in doc["slips"])
    assert took < 150, took
    print(f"\nLOAD generate near break-even: 700 members, {len(SETTINGS)} settings, wall {took:.1f} s, builds total {total:.2f} s, "
          f"max {max(s for s, _, _ in spent):.3f} s, max nodes {max(n for _, n, _ in spent)}")
