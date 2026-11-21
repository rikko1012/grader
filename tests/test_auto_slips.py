"""workspace_auto (owner 2026-11-20: EV and Arbitrage automatic): ready EV slips from the existing EV board + slip builder, arbitrage
pairs with exact cent stakes from the existing market sheet, kept warm by one background thread, read by 700 members at once with
no per-member build. Fixture data only."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import workspace_auto as wa_auto
import workspace_ev as we
import workspace_markets as wm
from workspace_books import _iso
from tests.test_price_slips import market
from tests.test_price_slips_api import ev_doc
from tests.test_slip_generate_api import keys_on_board
from tests.test_workspace_api import env, get, API  # noqa: F401


@pytest.fixture(autouse=True)
def clean():
    wa_auto.reset()
    yield
    wa_auto.reset()


def _fresh_market(sport="nba", odds=(2.1, 2.1)):
    d, r = market()
    r["sport"] = sport
    for l, o in zip(r["legs"], odds):
        l["seen_at"] = _iso(time.time() - 20)
        l["odds"] = o
    d["sports"] = {sport: dict(d["sports"]["nba"], quotes_checked=40, markets_checked=12)}
    d["method"] = {"arb_max_age_s": 300, "ev_stale_s": 1800}
    return d, r


def _ev_fixture(env, monkeypatch):
    """The fixture board is CS2; the EV board is a fixed doc with every PrizePicks leg +12% at 2 books (as test_price_slips_api)."""
    monkeypatch.setattr(we, "MARKET_SPORTS", ("cs2",))
    doc = ev_doc(env)
    board = keys_on_board(env)
    for r in doc["rows"]:
        sport, book, rid = r["key"].split("|")
        r.update(sport=sport, player="P", stat_label="Kills", period_label="Maps 1-2", event=board[(book, rid)]["event"]["label"], be=0.577,
                 books_label="2 books")
    doc["as_of"] = _iso(time.time())
    doc["rows_total"] = len(doc["rows"])
    doc["options"] = {"books": [{"id": "fanduel", "label": "FanDuel"}, {"id": "betrivers", "label": "BetRivers"}], "freshness_s": {"sportsbook": 1740}}
    doc["params"] = {"min_books": 2}
    calls = []

    def build(srv, p, allowed, now=None):
        calls.append(p)
        return dict(doc, app=p[0], entry=p[1], n=p[2], rows=[dict(r, app=p[0]) for r in doc["rows"]] if p[0] == "prizepicks" else [])
    monkeypatch.setattr(we, "build", build)
    monkeypatch.setattr(we, "known_books", lambda srv: ["betrivers", "fanduel"])
    return calls


def test_flags_off_is_404_and_no_thread(env, monkeypatch):
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    monkeypatch.delenv("SCOUT_MARKET_TOOLS", raising=False)
    assert get(env, "auto-slips")[0] == 404
    assert wa_auto.start(object()) is False


def test_ready_ev_slips_every_entry_from_the_existing_builder(env, monkeypatch):
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.delenv("SCOUT_MARKET_TOOLS", raising=False)
    calls = _ev_fixture(env, monkeypatch)
    st, d = get(env, "auto-slips")
    assert st == 200 and d["version"] == "auto-1" and d["arb"] is None, d
    ev = d["ev"]
    assert ev["state"] == "ok" and ev["books"] == ["BetRivers", "FanDuel"]
    sizes = {(e["app"], e["entry"], e["n"]) for e in ev["entries"]}
    assert ("prizepicks", "power", 2) in sizes and all(2 <= n <= 6 for _, _, n in sizes)          # generator limits: no UD 7 / 8
    assert len(calls) == len(sizes)
    board = keys_on_board(env)
    made = [e for e in ev["entries"] if e["slips"]]
    assert made and all(e["app"] == "prizepicks" for e in made)                                  # Underdog had no +EV leg here
    assert ev["entries"][0]["slips"]                                                              # an entry with slips first
    for e in made:
        for s in e["slips"]:
            assert len(s["legs"]) == e["n"] and s["platform"] == "prizepicks" and s["entry_type"] == e["entry"]
            recs = [board[(l["book"], l["row_id"])] for l in s["legs"]]                         # every leg is a current board line
            assert len({r["event"]["id"] for r in recs}) == e["n"]                                # different matches
            assert all(l["ev"] > 0 and l["p_market"] == 0.65 for l in s["legs"])                  # only legs that beat the entry
    for e in ev["entries"]:
        if e["app"] == "underdog":
            assert e["positive_legs"] == 0 and e["slips"] == []
    txt = json.dumps(d).lower()
    for word in ("guarantee", "lock", "provisional", "placed", "live"):
        assert word not in txt, word


def test_auto_reads_never_use_the_generate_limit(env, monkeypatch):
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    _ev_fixture(env, monkeypatch)
    for _ in range(30):
        assert get(env, "auto-slips")[0] == 200


def test_arbitrage_pairs_carry_exact_cent_stakes(env, monkeypatch):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    monkeypatch.setattr("workspace_sports.on", lambda h, q, owner: True)
    d, r = _fresh_market()
    monkeypatch.setattr(wm, "shared", lambda srv: (200, json.dumps(d).encode()))
    st, doc = get(env, "auto-slips")
    assert st == 200 and doc["ev"] is None
    a = doc["arb"]
    assert a["state"] == "ok" and len(a["pairs"]) == 1, a
    p = a["pairs"][0]
    assert sum(b["stake"] for b in p["bets"]) == pytest.approx(100.0) and p["total_stake"] == 100.0
    assert {b["side"] for b in p["bets"]} == {"over", "under"} and len({b["book"] for b in p["bets"]}) == 2
    assert p["minimum_return"] == 105.0 and p["profit"] == pytest.approx(5.0)
    assert p["quote"] == [[l["book"], l["side"], l["odds"]] for l in r["legs"]]             # what market-slips rechecks on a tap


def test_stale_started_or_unprofitable_pairs_are_dropped_and_sports_are_cut(env, monkeypatch):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    for change in ("stale", "started", "negative"):
        wa_auto.reset()
        d, r = _fresh_market()
        if change == "stale":
            r["legs"][0]["seen_at"] = _iso(time.time() - 1000)
        if change == "started":
            r["start_utc"] = "2020-01-01T00:00:00Z"
        if change == "negative":
            r["legs"][0]["odds"] = 1.8
        monkeypatch.setattr(wm, "shared", lambda srv, d=d: (200, json.dumps(d).encode()))
        assert wa_auto.arb_part(None, {"nba": True})["pairs"] == [], change
    d, r = _fresh_market()
    monkeypatch.setattr(wm, "shared", lambda srv: (200, json.dumps(d).encode()))
    assert wa_auto.arb_part(None, {"cs2": True, "nba": False})["pairs"] == []                 # a CS2-only member never gets an NBA pair
    assert len(wa_auto.arb_part(None, {"cs2": True, "nba": True})["pairs"]) == 1


def test_a_failed_build_keeps_the_last_good_answer(monkeypatch):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    import server
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    d, r = _fresh_market()
    monkeypatch.setattr(wm, "shared", lambda srv: (200, json.dumps(d).encode()))
    seen = ("cs2", "nba")
    st, body = wa_auto._rebuild(server, seen)
    good = json.loads(body)
    assert len(good["arb"]["pairs"]) == 1
    monkeypatch.setattr(wm, "shared", lambda srv: (503, b"{}"))
    time.sleep(0.01)
    server._RESULTS.clear()
    st, body = wa_auto._rebuild(server, seen)
    kept = json.loads(body)
    assert kept["arb"]["state"] == "ok" and kept["arb"]["pairs"] == good["arb"]["pairs"] and kept["arb"]["as_of"] == good["as_of"]


def test_one_background_thread_rebuilds_what_members_asked_for(monkeypatch):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    import server
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(wa_auto, "CYCLE_S", 0.2)
    d, r = _fresh_market()
    n = [0]

    def sheet(srv):
        n[0] += 1
        return 200, json.dumps(d).encode()
    monkeypatch.setattr(wm, "shared", sheet)
    wa_auto.serve(server, {"cs2": True, "nba": True})
    assert wa_auto.start(server) and wa_auto.start(server)
    threads = [t for t in threading.enumerate() if t.name == "auto-ev-arb"]
    assert len(threads) == 1
    time.sleep(5)
    assert n[0] >= 2                                                    # rebuilt in the background, no member request
    st = wa_auto.status()
    assert st["alive"] and st["cycles"] >= 1 and st["errors"] == 0
    monkeypatch.delenv("SCOUT_MARKET_TOOLS")
    threads[0].join(5)
    assert not threads[0].is_alive()                                     # the flag off stops the thread


@pytest.fixture
def big_queue(monkeypatch):
    from http.server import ThreadingHTTPServer
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def _send(port, path, cookie, tries=6):
    import http.client
    for k in range(tries):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            c.request("GET", path, headers={"Cookie": cookie, "Accept-Encoding": "gzip"})
            r = c.getresponse()
            r.read()
            return r.status
        except ConnectionRefusedError:
            time.sleep(0.2 * (k + 1))
        finally:
            c.close()
    return 0


def test_700_members_at_once_one_build(big_queue, env, monkeypatch):
    import whop_auth
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.setattr("workspace_sports.on", lambda h, q, owner: True)
    _ev_fixture(env, monkeypatch)
    d, r = _fresh_market()
    monkeypatch.setattr(wm, "shared", lambda srv: (200, json.dumps(d).encode()))
    builds, lock = [0], threading.Lock()
    real = wa_auto.build

    def count(srv, seen, now=None):
        with lock:
            builds[0] += 1
        time.sleep(0.2)
        return real(srv, seen, now)
    monkeypatch.setattr(wa_auto, "build", count)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        codes = list(ex.map(lambda i: _send(env.port, API + "auto-slips", f"{whop_auth.COOKIE}=good-m{i}"), range(700)))
    took = time.time() - t0
    assert codes.count(200) == 700, {c: codes.count(c) for c in set(codes)}
    assert builds[0] <= 2, builds                                        # one single-flight build (the thread may add one)
    assert took < 120, took
