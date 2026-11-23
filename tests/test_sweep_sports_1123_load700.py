"""sweep 2026-11-23 (sports lane): 700 members at once on what this change touches on the esports process - the NBA board, an
NBA line's card (its H2H block now counts the whole saved game log by opponent team id: nbanfl-1, side-hold short text:
nbanfl-2) and the mixed-sports Auto-build preview pool (SR-1 runs in the generate route; not called here because it appends to
the autobuild log - the pool search is pure and bounded, see tests/test_slip_quality.py). No 5xx, one shared player-file read
per player (never per member). Fixture upstreams only (no network)."""
import http.client
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import server
import whop_auth
import workspace_api
import workspace_sports
from tests.test_workspace_all_sports import env, API  # noqa: F401


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_on_the_nba_board_and_line_cards(big_queue, env, monkeypatch):
    for f in ("SCOUT_UPGRADE", "SPORTS_UI", "SCOUT_BOARD_H2H", "SCOUT_WORKSPACE_V2"):
        monkeypatch.setenv(f, "1")
    workspace_api.reset(); workspace_sports.reset()
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=60)
    c.request("GET", API + "board?sport=nba", headers={"Cookie": f"{whop_auth.COOKIE}=good-owner"})
    r = c.getresponse(); doc = json.loads(r.read()); c.close()
    assert r.status == 200 and doc["records"], doc
    ids = [(x["offer"]["platform"], x["offer"]["offer_id"]) for x in doc["records"]][:3]
    paths = ["board?sport=nba"] + [f"line?sport=nba&book={b}&id={i}" for b, i in ids]
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=60)
    c.request("GET", API + paths[1], headers={"Cookie": f"{whop_auth.COOKIE}=good-owner"})
    r = c.getresponse(); card = json.loads(r.read()); c.close()
    assert r.status == 200 and "h2h" in card, card
    env.sports.calls.clear()
    workspace_api.reset()
    server._RESULTS.clear()

    def member(i):
        out = []
        h = http.client.HTTPConnection("127.0.0.1", env.port, timeout=90)
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        try:
            for p in paths:
                h.request("GET", API + p, headers=cookie)
                x = h.getresponse(); x.read(); out.append(x.status)
        finally:
            h.close()
        return out
    t0 = time.time()
    codes = []
    with ThreadPoolExecutor(max_workers=700) as ex:
        for out in ex.map(member, range(700)):
            codes.extend(out)
    took = time.time() - t0
    assert codes.count(200) == len(codes), {k: codes.count(k) for k in set(codes)}
    players = len({x for x in ids})
    assert env.sports.calls.get("/api/sports/history", 0) <= 2 * players, env.sports.calls     # shared, never per member
    assert env.sports.calls.get("/api/sports/player", 0) <= 2 * players, env.sports.calls
    assert took < 150, took
