"""card-why (2026-11-06): 700 members at once on what this change touches - the new card script / styles, the NBA Sleeper and
Dabble boards (hit rates at another line now counted from the player's saved game log) and an NBA Sleeper line's research card
(line?sport=nba&book=sleeper, newly open). No 5xx; the sports service sees one player-file read per player (shared, never per
member) and no member request reaches a provider. The "Why Scout leans" section is built in the browser from the card's own data,
so it adds no request. Fixture upstreams only (no network)."""
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import book_providers as bp
import server
import whop_auth
import workspace_api
import workspace_books as wb
import workspace_sports
from tests.test_workspace_all_sports import env, API  # noqa: F401

STATIC = ("/upgrade/hub.js", "/upgrade/hub.css")


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def _offer(book, name, team, line, stat, sid, now):
    side = (lambda v: {"offered": True, "payout": v, "price": None})
    return {"provider": book, "book": book, "sport": "nba", "raw_stat": stat, "stat": stat, "period": "full_game", "subject_id": sid, "player": name,
            "player_norm": bp.norm(name), "team": team, "team_norm": bp.norm(team), "game_id": "g" + sid, "line": line, "over": side(1.8), "under": side(1.9),
            "source_at": now - 60, "observed_at": now}


def test_700_members_on_nba_extra_book_lines_and_their_card(big_queue, env, monkeypatch):
    for f in ("SCOUT_UPGRADE", "SPORTS_UI", "SCOUT_BOOK_SLEEPER", "SCOUT_BOARD_H2H", "SCOUT_WORKSPACE_V2"):
        monkeypatch.setenv(f, "1")
    bp.reset(); wb.reset(); workspace_api.reset(); workspace_sports.reset()
    monkeypatch.setattr(bp, "start", lambda d: False)
    calls = []
    monkeypatch.setattr(bp, "http_get", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("no member request may reach a provider")))
    now = time.time()
    bp.state("sleeper").update(at=now, offers=[_offer("sleeper", "Reed Lean", "CHI", 7.5, "assists", "1", now),
                                               _offer("sleeper", "Karl Anders", "NYK", 21.5, "points", "2", now)])
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=60)
    c.request("GET", API + "book-board?sport=nba&book=sleeper", headers={"Cookie": f"{whop_auth.COOKIE}=good-owner"})
    r = c.getresponse(); doc = json.loads(r.read()); c.close()
    assert r.status == 200 and len(doc["records"]) == 2, doc
    for rec in doc["records"]:                        # another line than the board's: L5 / L10 / L20 from the saved game log
        assert all((rec["history"].get(w) or {}).get("n") for w in ("l5", "l10", "l20")), rec["history"]
    oid = doc["records"][0]["offer"]["offer_id"]
    env.sports.calls.clear()
    workspace_api.reset(); wb.reset()                 # cold again: every shared answer and board is rebuilt under the crowd
    server._RESULTS.clear()
    paths = ["book-board?sport=nba&book=sleeper", f"line?sport=nba&book=sleeper&id={oid}"]

    def member(i):
        out = []
        h = http.client.HTTPConnection("127.0.0.1", env.port, timeout=90)
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        try:
            for p in STATIC:
                h.request("GET", p, headers=cookie)
                x = h.getresponse(); x.read(); out.append(x.status)
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
    hist = env.sports.calls.get("/api/sports/history", 0)
    assert hist <= 2, env.sports.calls                # one saved game-log read per player, shared by all 700 members
    assert env.sports.calls.get("/api/sports/player", 0) <= 2, env.sports.calls
    assert not calls and took < 150, took
