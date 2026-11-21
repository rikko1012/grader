"""workspace_auto review fixes (2026-11-20): the scan's cost (payout tables solved once, EV part shared across member sport sets,
paced, scheduled from build end) and age (each part its own as_of, expired slips / pairs dropped, kept parts age out).
Fixture data only."""
import json
import time

import pytest

import workspace_auto as wa_auto
import workspace_ev as we
import workspace_markets as wm
from workspace_books import _iso
from tests.test_auto_slips import _fresh_market


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    import server
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    wa_auto.reset()
    yield
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)        # stop this test's background thread before the next test
    monkeypatch.delenv("SCOUT_MARKET_TOOLS", raising=False)
    t = wa_auto._THREAD["t"]
    if t is not None:
        t.join(5)
    wa_auto.reset()


def _nba_board(monkeypatch, n_ev=8, per=6, stale_share=0):
    """A fake NBA snapshot: PrizePicks (multiplier 1) and Underdog (multipliers 0.85 / 1.04 / 1) legs at two books; every
    `stale_share`-th leg rests on stale prices (hidden)."""
    import workspace_api as wapi
    import workspace_books as wb
    from tests.book_rules_fixture import like_the_app
    from tests.test_pickem_ev import rec, book
    like_the_app(monkeypatch)
    wb.reset(); we._FAIR.clear(); we._LEGS.clear(); we._TABLES.clear(); wapi.reset()
    start = _iso(time.time() + 6 * 3600)
    items, k = [], 0
    for e in range(n_ev):
        for j in range(per):
            k += 1
            for app, mult in (("prizepicks", None), ("underdog", (None, 0.85, 1.04)[k % 3])):
                r = rec(f"p{k}", 20.5, app=app, start=start, p_over=0.6, p_under=0.4, mult=mult)
                r["event"] = dict(r["event"], id=f"e{e}", label=f"T{e} at H{e}")
                r["key"] = r["key"].replace("e1", f"e{e}")
                r["player"] = dict(r["player"], team=f"T{e}")
                r["scout"] = dict(r["scout"], side="over", p_side=0.6, grade="B", projection=25.0)
                age = 5000 if stale_share and k % stale_share == 0 else 60
                o, u = (1.45, 2.90) if k % 2 else (1.95, 1.90)
                items.append((r, [book("fanduel", 20.5, o, u, age), book("betrivers", 20.5, o + 0.02, u - 0.1, age)]))
    index = {(r["offer"]["platform"], r["offer"]["offer_id"]): {"books": (rows, []), "st": r["market"]["stat"], "rec": json.dumps(r)}
             for r, rows in items}
    snap = {"body": json.dumps({"records": [r for r, _ in items]}).encode(), "index": index,
            "feeds": {"prizepicks": {"state": "ok"}, "underdog": {"state": "ok"}}}
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r for r, _ in items], snap) if sport == "nba" else (None, None))
    monkeypatch.setattr(wapi, "_sport_snap", lambda srv, sport: snap if sport == "nba" else None)
    monkeypatch.setattr(wapi, "_log_generated", lambda *a, **k: None)
    return items


def test_payout_tables_are_solved_once_not_per_leg(monkeypatch):
    """Before: every leg of every build re-solved every Flex table (default_entry -> entries -> entry_types), 83% of a build."""
    import server
    from gradecore import payouts
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    _nba_board(monkeypatch)
    calls = [0]
    real = payouts.entry_types

    def counted(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    monkeypatch.setattr(payouts, "entry_types", counted)
    ev = wa_auto.ev_part(server, {"nba": True, "cs2": True})
    assert len(ev["entries"]) >= 10 and calls[0] <= 1, calls
    calls[0] = 0
    wa_auto.ev_part(server, {"nba": True})
    assert calls[0] == 0


def test_board_numbers_unchanged_by_the_memo(monkeypatch):
    """Every row's break-even / EV and the other app's plays count equal a direct solve (Underdog multipliers, hidden legs)."""
    import server
    from gradecore import ev_board, fair, payouts
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    _nba_board(monkeypatch, stale_share=4)
    cfg = payouts.pickem_config()
    direct_default = {}
    for app in we.APPS:
        es = [e for e in payouts.entry_types() if e["book"] == app and e["break_even"] is not None and payouts.in_app(app, e["entry"], e["n"])]
        e = min(es, key=lambda e: (round(e["break_even"], 6), e["n"]))
        direct_default[app] = (e["entry"], e["n"])
        assert we.default_entry(app) == direct_default[app]
    seen_mult = set()
    for e in we.entries():
        p = we.params({"app": e["app"], "entry": e["entry"], "n": str(e["n"])}, we.known_books(server))
        d = we.build(server, p, {"nba": True})
        assert d["hidden"]["count"] > 0
        for r in d["rows"]:
            be = ev_board.pickem_break_even(r["app"], e["entry"], e["n"], r["multiplier"], config=cfg)
            assert r["be"] == (None if be is None else round(be, 6))
            assert r["ev"] == (None if be is None else round(fair.leg_ev(r["p_market"], be), 4)) or abs(r["ev"] - fair.leg_ev(r["p_market"], be)) < 1e-3
            seen_mult.add(r["multiplier"])
        other = [a for a in we.APPS if a != e["app"]][0]
        want = 0
        _t, legs = we._legs(server)
        _l, priced = we._priced(server, p, time.time())
        for leg in legs:
            f, _why = priced.get((leg["key"], leg["side"]), (None, None))
            if leg["app"] == other and f is not None and f["p"] is not None:
                obe = ev_board.pickem_break_even(other, *direct_default[other], leg["multiplier"], config=cfg)
                want += obe is not None and fair.leg_ev(f["p"], obe) > 0
        assert d["plays"][other] == want
    assert {0.85, 1.04} <= seen_mult


def test_ev_part_is_shared_by_member_sets_with_the_same_market_sports(monkeypatch):
    import server
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.delenv("SCOUT_MARKET_TOOLS", raising=False)
    _nba_board(monkeypatch)
    keys = []
    real = wa_auto._build_part
    monkeypatch.setattr(wa_auto, "_build_part", lambda srv, key, pace=None: (keys.append(key), real(srv, key, pace))[1])
    a = json.loads(wa_auto.serve(server, {"cs2": True, "nba": True})[1])
    b = json.loads(wa_auto.serve(server, {"lol": True, "nba": True})[1])
    c = json.loads(wa_auto.serve(server, {"nba": True})[1])
    assert keys == [("ev", ("nba",))], keys
    assert a["ev"] == b["ev"] == c["ev"] and a["ev"]["as_of"] and a["ev"]["max_age_s"] == wa_auto.keep_max_s()
    made = [s for e in a["ev"]["entries"] for s in e["slips"]]
    assert made and all(s["expires_at"] for s in made)


def test_slip_expiry_follows_the_oldest_price(monkeypatch):
    """A slip expires when its oldest used price passes the quote kind's age limit (counted from the price memo's start)."""
    import server
    from gradecore import fair
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    _nba_board(monkeypatch)
    t0 = time.time()
    ev = wa_auto.ev_part(server, {"nba": True})
    s = next(s for e in ev["entries"] for s in e["slips"])
    exp = time.mktime(time.strptime(s["expires_at"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    want = t0 - we.TTL + fair.FRESHNESS_S["sportsbook"] - 60
    assert abs(exp - want) < 5, (exp, want)


def test_expired_slips_and_pairs_are_dropped_and_the_body_changes_when_they_expire(monkeypatch):
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    now = time.time()
    slip = lambda x: {"legs": [], "mean_ev": 0.1, "expires_at": _iso(x)}
    ev = {"state": "ok", "as_of": _iso(now), "entries": [{"app": "prizepicks", "entry": "power", "n": 2, "slips": [slip(now + 10), slip(now - 1)]},
                                                         {"app": "underdog", "entry": "standard", "n": 2, "slips": [slip(now - 5)]}]}
    arb = {"state": "ok", "as_of": _iso(now), "pairs": [{"leg_id": 1, "expires_at": _iso(now + 20)}, {"leg_id": 2, "expires_at": _iso(now - 1)}]}
    wa_auto._PARTS[("ev", ("nba",))] = {"t0": now, "tried": now, "next": now + 30, "cpu": 0, "part": ev}
    wa_auto._PARTS[("arb", ("nba",))] = {"t0": now, "tried": now, "next": now + 30, "cpu": 0, "part": arb}
    seen = ("nba",)
    d = json.loads(wa_auto._assemble(seen, now))
    assert [len(e["slips"]) for e in d["ev"]["entries"]] == [1, 0]
    assert [p["leg_id"] for p in d["arb"]["pairs"]] == [1]
    assert abs(wa_auto._STORE[seen]["expires"] - (now + 10)) < 1.5         # the stored body is redone when the next slip expires
    assert not wa_auto._current(seen, wa_auto._STORE[seen], now + 11)
    d = json.loads(wa_auto._assemble(seen, now + 11))
    assert [len(e["slips"]) for e in d["ev"]["entries"]] == [0, 0] and len(d["arb"]["pairs"]) == 1


def test_a_kept_part_ages_out_and_shows_its_own_as_of(monkeypatch):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    import server
    d, r = _fresh_market()
    monkeypatch.setattr(wm, "shared", lambda srv: (200, json.dumps(d).encode()))
    seen = ("cs2", "nba")
    good = json.loads(wa_auto._rebuild(server, seen)[1])
    assert len(good["arb"]["pairs"]) == 1 and good["arb"]["as_of"] == good["as_of"]
    monkeypatch.setattr(wm, "shared", lambda srv: (503, b"{}"))
    server._RESULTS.clear()
    time.sleep(1.1)
    kept = json.loads(wa_auto._rebuild(server, seen)[1])
    assert kept["arb"]["kept"] and kept["arb"]["as_of"] == good["arb"]["as_of"] and kept["arb"]["pairs"] == good["arb"]["pairs"]
    e = wa_auto._PARTS[("arb", ("cs2", "nba"))]
    late = e["t0"] + wa_auto.keep_max_s() + 1
    gone = json.loads(wa_auto._assemble(seen, late))
    assert gone["arb"]["state"] == "unavailable" and gone["arb"]["pairs"] == []          # never an old pair as current
    assert not wa_auto._current(seen, {"stamp": wa_auto._stamp(seen), "body": b"", "expires": e["t0"] + wa_auto.keep_max_s()}, late)


def test_next_build_is_timed_from_build_end_and_paced(monkeypatch):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    import server
    d, r = _fresh_market()

    def slow(srv):
        time.sleep(0.5)
        return 200, json.dumps(d).encode()
    monkeypatch.setattr(wm, "shared", slow)
    pace = wa_auto._pacer()
    e = wa_auto._build_part(server, ("arb", ("nba",)), pace)
    assert e["next"] >= e["t0"] + 0.5 + wa_auto.CYCLE_S - 0.05                      # CYCLE_S after the build ENDED
    assert e["part"]["as_of"] == _iso(e["t0"])                                      # as_of = when the build started
    monkeypatch.setattr(wa_auto, "CYCLE_CPU_S", 0.0001)
    burn = wa_auto._pacer()
    t = time.thread_time()
    while time.thread_time() - t < 0.05:
        pass
    e = wa_auto._build_part(server, ("arb", ("nba",)), burn)
    assert e["next"] > time.time() + wa_auto.CYCLE_S * 10                           # over its CPU budget: waits longer


def test_the_scan_waits_out_a_member_burst_but_never_forever(monkeypatch):
    import threading
    import types
    srv = types.SimpleNamespace(REQUESTS_SEEN=[0])
    stop = threading.Event()

    def burst():                                                                     # ~1,000 requests a second
        while not stop.is_set():
            srv.REQUESTS_SEEN[0] += 1
            time.sleep(0.001)
    t = threading.Thread(target=burst, daemon=True)
    t.start()
    monkeypatch.setattr(wa_auto, "MAX_DEFER_S", 0.6)
    try:
        pace = wa_auto._pacer(srv)
        t0 = time.time()
        pace()
        waited = time.time() - t0
        assert 0.5 <= waited < 2.0 and pace.state["deferred"] >= 0.5, waited          # waited, up to the cap
        t0 = time.time()
        pace()
        assert time.time() - t0 < 0.3                                                  # the cap is per part: no second wait
    finally:
        stop.set()
        t.join()
    quiet = wa_auto._pacer(types.SimpleNamespace(REQUESTS_SEEN=[0]))
    t0 = time.time()
    quiet()
    assert time.time() - t0 < 0.3 and quiet.state["deferred"] == 0
