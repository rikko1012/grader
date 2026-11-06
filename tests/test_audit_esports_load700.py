"""700 members at once on the audit 2026-11-05 esports routes: the Sleeper board (hit rates at each line from the game log, the chance
at another line from the saved distribution) and a Sleeper line's research doc (line?book=sleeper). No 5xx, one shared build per
window, the HLTV game log read once per player (never per member), no provider request from any member. Fixture upstreams only."""
import http.client
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import book_providers as bp
import server
import whop_auth
import workspace_books as wb
from tests.test_audit_esports_1105 import _cs2_row, _maps
from tests.test_workspace_api import env, API, get, fresh  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_on_sleeper_lines_and_their_research(big_queue, env, monkeypatch):
    for f in ("SCOUT_BOOK_SLEEPER", "SCOUT_BOARD_H2H", "SCOUT_GENIE"):
        monkeypatch.setenv(f, "1")
    bp.reset(); wb.reset()
    monkeypatch.setattr(bp, "start", lambda d: False)
    calls = []
    monkeypatch.setattr(bp, "http_get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("no member request may reach a provider")))
    reads, lock = [], threading.Lock()

    def series(*a, **k):
        with lock:
            reads.append(a)
        time.sleep(0.02)
        return _maps()
    monkeypatch.setattr(server.hltv, "recent_series", series)
    row = _cs2_row(41.0, 6.0, 38.5)
    for bk in ("underdog", "prizepicks"):
        x = env.by("vargo", bk)
        x.update(p=row["p"], side=row["side"], model_outcomes=row["model_outcomes"])
        server.BOARD[x["id"]] = {"dist": row["dist"]}
    now = time.time()
    off = lambda line, sid, who="vargo", team="Halcyon": {
        "provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills", "period": "maps12", "subject_id": sid,
        "player": who, "player_norm": who, "team": team, "team_norm": team.lower(), "game_id": "g", "line": line,
        "over": {"offered": True, "payout": 1.8, "price": None}, "under": {"offered": True, "payout": 1.76, "price": None}, "source_at": now - 60, "observed_at": now}
    bp.state("sleeper").update(at=now, offers=[off(37.5, "7"), off(38.5, "7b"), off(33.5, "9", "brandr", "Norrland")])
    fresh()
    st, doc = get(env, "book-board?sport=cs2&book=sleeper")
    assert st == 200 and len(doc["records"]) == 3
    oid = next(r["offer"]["offer_id"] for r in doc["records"] if r["offer"]["line"] == 37.5)
    fresh()
    reads.clear()
    paths = ["book-board?sport=cs2&book=sleeper", f"line?book=sleeper&id={oid}"]
    codes = []

    def member(i):
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
    assert len(reads) <= 4, len(reads)                 # one game-log read per player (vargo, brandr), shared by every member
    assert not calls and took < 120, took
