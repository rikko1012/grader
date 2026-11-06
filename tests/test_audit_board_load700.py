"""Audit 2026-11-05, board group: 700 members at once on every route the board change touches. Each member reads the new board's
script and styles, /me, the NBA board (rows now carry the team abbreviation for the logo) and saves their sort to their own account
state. No 5xx, one NBA board build per window, every member's sort saved once. Fixture upstreams only (no network)."""
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import whop_auth
import workspace_api
import workspace_sports
from tests.test_workspace_all_sports import env, API  # noqa: F401

STATIC = ("/upgrade/hub.js", "/upgrade/hub.css", "/upgrade/upgrade.js")


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_members_read_the_board_and_save_their_sort(big_queue, env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    workspace_api.reset(); workspace_sports.reset()
    builds = [0]
    lock = threading.Lock()
    real = workspace_sports._build

    def counted(srv, lg):
        with lock:
            builds[0] += 1
        time.sleep(0.05)
        return real(srv, lg)
    monkeypatch.setattr(workspace_sports, "_build", counted)

    def member(i):
        out, docs = [], {}
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=90)
        cookie = {"Cookie": f"{whop_auth.COOKIE}=good-m{i}", "Accept-Encoding": "gzip"}
        try:
            for path in STATIC:
                c.request("GET", path, headers=cookie)
                r = c.getresponse(); r.read(); out.append(r.status)
            for p in ("me", "board?sport=nba", "state"):
                c.request("GET", API + p, headers={"Cookie": cookie["Cookie"]})
                r = c.getresponse(); raw = r.read(); out.append(r.status)
                docs[p] = json.loads(raw) if r.status == 200 else None
            st = docs["state"]
            body = json.dumps({"rev": st["rev"], "prefs": {**(st.get("prefs") or {}), "researchHub": {"sort": {"key": "l10", "dir": "desc"}}}}).encode()
            c.request("POST", API + "state", body=body, headers={"Cookie": cookie["Cookie"], "Content-Type": "application/json"})
            r = c.getresponse(); r.read(); out.append(r.status)
        finally:
            c.close()
        rows = (docs.get("board?sport=nba") or {}).get("records") or []
        return out, rows
    t0 = time.time()
    codes, abbrs = [], set()
    with ThreadPoolExecutor(max_workers=700) as ex:
        for out, rows in ex.map(member, range(700)):
            codes.extend(out)
            abbrs.update(r["player"].get("team_abbr") for r in rows)
    took = time.time() - t0
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}
    assert builds[0] <= 2, builds                     # one shared NBA board build per window, not one per member
    assert abbrs and abbrs <= {"NYK", "BOS", "MIA", "CHI", ""}, abbrs
    # every member's own sort, saved on their own account
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=30)
    for i in (0, 349, 699):
        c.request("GET", API + "state", headers={"Cookie": f"{whop_auth.COOKIE}=good-m{i}"})
        r = c.getresponse(); d = json.loads(r.read())
        assert d["prefs"]["researchHub"]["sort"] == {"key": "l10", "dir": "desc"}, d
    c.close()
    assert took < 150, took
