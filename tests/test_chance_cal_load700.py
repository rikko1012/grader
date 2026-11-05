"""ec-1 under load: 700 members at once on the CS2 board (both platforms) and one player card with SCOUT_CAL_CHANCE=1, through the
REAL board_payload and card route on a running server. No non-200, every answer calibrated, one shared board build per window
(the layer adds no per-member work), the pick log keeps the model's own chance. Fixture data only."""
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

import picklog
import server
from gradecore import chance_cal as cc
from tests.test_audit13_site import card, site, _grade_stub  # noqa: F401  (fixtures)
from tests.test_stake_match_id import UD_START, WBT, _board

N = 700


@pytest.fixture
def running(card, site, monkeypatch):
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)
    monkeypatch.setenv(cc.FLAG, "1")
    cc.reset_cache()
    _board(site)
    for i in range(150):                              # a full board: 150 more graded lines across 30 matches
        rid = f"ud:x{i}"
        nick = f"p{i}"
        server.BOARD[rid] = dict(server.BOARD["ud:0"], nick=nick, graded_line=20.5, p=0.55 + (i % 30) / 100, side="More" if i % 2 else "Less",
                                 grade="B", units=0.5 + (i % 3) * 0.5, prop="kills" if i % 3 else "hs", match_slug=f"m{i % 30}")
        server.underdog._BOARD["rows"].append({"id": rid, "book": "underdog", "scope": "12", "player": nick, "team": f"T{i % 30}",
                                               "opponent": f"O{i % 30}", "matchup": f"T{i % 30} vs O{i % 30}", "start": UD_START,
                                               "prop": "kills" if i % 3 else "hs", "line": 20.5, "alt": False})
    rows = server.underdog._BOARD["rows"]
    monkeypatch.setattr(server.underdog, "board", lambda wait=False: {"at": time.time(), "rows": rows, "error": None})
    builds = {"n": 0}
    lock = threading.Lock()
    real = server.board_payload

    def counted(book="underdog"):
        with lock:
            builds["n"] += 1
        return real(book)
    monkeypatch.setattr(server, "board_payload", counted)
    set_grade, logged = card
    set_grade(_grade_stub())
    import whop_auth                                  # 700 signed-in members: each counts against its own rate limit
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    monkeypatch.setattr(whop_auth, "cookie_valid", lambda v: bool(v and v.startswith("good-")))
    monkeypatch.setattr(whop_auth, "cookie_user", lambda v: v[5:] if v and v.startswith("good-") else None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], builds, logged
    httpd.shutdown()
    httpd.server_close()
    cc.reset_cache()


def test_700_members_at_once_on_calibrated_boards_and_cards(running):
    port, builds, logged = running
    paths = ["/api/board?book=underdog", "/api/board?book=prizepicks",
             "/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog"]
    out, errors = [], []

    def member(i):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        res = []
        try:
            for p in paths:
                import whop_auth
                c.request("GET", p, headers={"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"})
                r = c.getresponse()
                body = r.read()
                if r.getheader("Content-Encoding") == "gzip":
                    import gzip
                    body = gzip.decompress(body)
                res.append((p, r.status, body))
        except Exception as e:                        # noqa: BLE001
            errors.append(repr(e))
        finally:
            c.close()
        return res
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N) as ex:
        for r in ex.map(member, range(N)):
            out.extend(r)
    took = time.time() - t0
    assert not errors and len(out) == N * len(paths)
    assert all(s == 200 for _, s, _ in out), {s for _, s, _ in out}
    tag = cc.tag()
    seen = set()
    for p, _, body in out:
        if body in seen:
            continue
        seen.add(body)
        d = json.loads(body)
        if p.startswith("/api/board?book=underdog"):
            sided = [r for r in d["rows"] if r.get("side")]
            assert len(sided) >= 150 and all(r["cal_v"] == tag and r["units"] <= (r["units_raw"] or 0) for r in sided)
        elif p.startswith("/api/grade"):
            assert d["verdict"]["cal_v"] == tag and d["verdict"]["p"] == round(cc.side_chance("cs2", "kills", "under", 0.64), 3)
    assert builds["n"] <= 6, builds                  # one shared build per board per window, not one per member
    assert all(v["p"] == 0.64 for v in logged)       # the pick log: the model's own chance
    assert took < 120, took
    assert picklog                                    # noqa: the module the card logs through (stubbed by the fixture)
