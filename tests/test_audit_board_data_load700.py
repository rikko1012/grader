"""Owner site audit 2026-11-06, board data: 700 members at once on the routes the change touches. Each member reads the CS2 board,
its hit rates (N3: filled by one background thread, never on a member's request thread) and the NBA board, then asks the
Ask tab "better line elsewhere?" (N9: an extra book's board parsed by no member). Then the sports
service answers a refresh with no lines (N2) and 700 members read the NBA board again: every one gets the last good lines. No
5xx, one history read per row for everyone together, bounded copies. Fixture upstreams only (no network)."""
import http.client
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

import server
import whop_auth
import workspace_api
import workspace_sports
from tests.test_workspace_all_sports import env, API  # noqa: F401
from tests.workspace_fixture import iso, row
from tests.workspace_other_fixture import big_nba_board

N = 700


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def _get(port, path, cookie, tries=12):
    method, body = "GET", None
    if isinstance(path, tuple):                 # ("POST", route, JSON body)
        method, path, body = path[0], path[1], json.dumps(path[2]).encode()
    for k in range(tries):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            c.request(method, API + path, body=body, headers={"Cookie": cookie, **({"Content-Type": "application/json"} if body else {})})
            r = c.getresponse()
            raw = r.read()
            return r.status, json.loads(raw) if r.status == 200 else None
        except ConnectionRefusedError:          # the client OS refused before the server saw it (Windows accept backlog full; a
            time.sleep(min(2.0, 0.25 * (k + 1)))  # browser's TCP stack retries the same way)
        finally:
            c.close()
    return 0, None


def _crowd(env, paths):
    def member(i):
        return {(p[1] if isinstance(p, tuple) else p): _get(env.port, p, f"{whop_auth.COOKIE}=good-m{i}") for p in paths}
    with ThreadPoolExecutor(max_workers=N) as ex:
        return list(ex.map(member, range(N)))


def test_700_members_get_hit_rates_with_the_board_and_keep_lines_through_a_failed_refresh(big_queue, env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    workspace_api.reset(); workspace_sports.reset()
    env.sports.boards["nba"] = big_nba_board(400)
    start = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    env.rows[:] = [row(f"11111111-0000-4000-8000-{i:012d}", "underdog", f"ud{i}", "Halcyon", "Norrland", 30.5 + i % 9, "More", 0.58, 900 + i // 10, start)
                   for i in range(300)] + [row(f"pp:{8000 + i}", "prizepicks", f"pp{i}", "Vanta", "Kestrel Five", 31.5 + i % 7, "Less", 0.57,
                                               2000 + i // 10, start) for i in range(300)]     # a full CS2 board: 600 rows
    reads, lock = [], threading.Lock()

    def history(srv, x, full=False, peek=False):
        with lock:
            reads.append(threading.current_thread().name)
        time.sleep(0.002)
        return {"ser": [{"date": "2026-10-%02d" % (d + 1), "opponent": "Other", "value": v} for d, v in enumerate((12, 15, 18, 21, 24, 27))]}
    monkeypatch.setattr(workspace_api, "_history", history)

    t0 = time.time()
    res = _crowd(env, ("board", "hits", "board?sport=nba"))
    took = time.time() - t0
    codes = [st for out in res for st, _ in out.values()]
    assert codes.count(200) == len(codes), {c: codes.count(c) for c in set(codes)}
    cs2_rows = {f"{r['offer']['platform']}|{r['offer']['offer_id']}" for r in res[0]["board"][1]["records"]}
    nba_n = len(res[0]["board?sport=nba"][1]["records"])
    res0_line = res[0]["board"][1]["records"][0]
    assert len(cs2_rows) == 600 and nba_n >= 400
    # N3: the hit rates arrive with the board - every row filled by the background thread, no member waited on a history read
    deadline = time.time() + 30
    while time.time() < deadline:
        doc = json.loads(workspace_api._HITS_DOC["body"] or b"{}")
        if doc.get("complete") and set(doc.get("rows") or {}) >= cs2_rows:
            break
        time.sleep(0.1)
    assert doc.get("complete") and set(doc["rows"]) >= cs2_rows, (len(doc.get("rows") or {}), len(cs2_rows))
    print(f"crowd 1: {took:.1f} s for {N} members x 3 reads; hit rates complete {time.time() - t0:.1f} s after the start "
          f"({len(cs2_rows)} CS2 rows, {len(reads)} history reads, NBA {nba_n} lines)")
    assert set(reads) == {"ws-hits-fill"}, set(reads)               # never on a member's request thread
    assert len(reads) == len(cs2_rows), (len(reads), len(cs2_rows))  # one history read per row for 700 members together
    assert len(workspace_api._HITS_DOC["rows"]) <= len(workspace_api._SNAP["index"])    # bounded by the board itself
    assert sum(1 for t in threading.enumerate() if t.name == "ws-hits-fill") <= 1
    # a second crowd reads the filled copy: still no history read on any request thread, every row present
    res = _crowd(env, ("hits",))
    assert all(out["hits"][0] == 200 and set(out["hits"][1]["rows"]) >= cs2_rows for out in res)
    assert set(reads) == {"ws-hits-fill"} and len(reads) == len(cs2_rows)

    # N9 (review): 700 members ask "better line elsewhere?" at once; a direct extra book (Dabble) lists this prop on a big book
    # board. The book board is built once (single flight) and its prop index made inside that build: no member parses it.
    import types
    import book_providers as bp
    import workspace_books
    import workspace_genie
    monkeypatch.setenv("SCOUT_GENIE", "1")
    workspace_genie.reset()
    line = res0_line
    dab = json.loads(json.dumps(line))
    dab["offer"].update(platform="dabble", platform_label="Dabble", offer_id="dab-1", line=line["offer"]["line"] + 1)
    fill = [dict(dab, player=dict(dab["player"], key=f"other{i}"), offer=dict(dab["offer"], offer_id=f"dab-x{i}")) for i in range(8000)]
    book_doc = {"feed": {"state": "ok"}, "records": [dab] + fill}
    builds, loads = [], []
    ask = {"book": line["offer"]["platform"], "id": str(line["offer"]["offer_id"]), "side": "over", "sport": "cs2",
           "question": "Is there a better line elsewhere?"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bp, "enabled_ids", lambda: ["dabble"])
        mp.setattr(bp, "providers", lambda: {"dabble": {"source": "direct", "sports": ["cs2"]}})
        mp.setattr(workspace_books, "build", lambda srv, sport, pid: builds.append(1) or (200, book_doc))
        mp.setattr(workspace_books, "json", types.SimpleNamespace(dumps=json.dumps, loads=lambda b, *a, **k: loads.append(1) or json.loads(b, *a, **k)))
        t1 = time.time()
        res = _crowd(env, (("POST", "genie", ask),))
    codes = [out["genie"][0] for out in res]
    assert codes.count(200) == N, {c: codes.count(c) for c in set(codes)}
    assert all(any("Dabble lists" in s for s in out["genie"][1]["sentences"]) for out in res)
    assert loads == [] and len(builds) <= 2, (len(loads), len(builds))   # was one parse of the book board per question
    print(f"crowd ask: {N} of {N} members answered with the Dabble line in {time.time() - t1:.1f} s; book board built {len(builds)}x, parsed 0x")
    workspace_books.reset()

    # N2: the next refresh comes back with no lines; 700 members all keep the last good NBA board
    doc, view = env.sports.boards["nba"]
    env.sports.boards["nba"] = (dict(doc, markets=[]), view)
    workspace_sports._SNAP["nba"]["etags"] = {}
    server._RESULTS.clear()
    res = _crowd(env, ("board?sport=nba",))
    codes = [out["board?sport=nba"][0] for out in res]
    assert codes.count(200) == N, {c: codes.count(c) for c in set(codes)}
    assert all(len(out["board?sport=nba"][1]["records"]) == nba_n for out in res)
    print(f"crowd 3 (empty refresh): {N} of {N} members got the last good {nba_n} NBA lines")
    assert took < 150, took
