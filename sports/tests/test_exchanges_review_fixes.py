"""Review fixes on the display-only exchanges step (Novig, Gemini; books/exchanges.py), 2026-11-09:
the 30 s budget is enforced inside a venue (every request, page, answer body, hold, write chunk); a 5-min cadence with one
early retry; one venue's error never stops the others; a partial / empty read withdraws nothing; a rung dropped only by
the 3-rung cap is never withdrawn; a memory guard before every request; never settled, never a market-column fallback
chance, never the lab's offered line."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from sports import market_view, net, store
from sports.books import exchanges as x
from sports.tests import helpers as h

FX = Path(__file__).resolve().parent / "fixtures" / "books"
OBS = "2026-11-09T18:00:00Z"


def _fx(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class _Resp:
    def __init__(self, body):
        self._b, self.status_code = body, 200

    def json(self):
        return self._b


class FakeTime:
    """x.time stand-in: monotonic() moves only when a fake request says so."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def time(self):
        return time.time()


@pytest.fixture
def clock(monkeypatch):
    ft = FakeTime()
    monkeypatch.setattr(x, "time", ft)
    return ft


@pytest.fixture
def no_resolver(monkeypatch):
    from sports.books import sources
    monkeypatch.setattr(sources, "resolver_from_store", lambda con, lg: (None, ()))
    for v in ("NOVIG", "GEMINI"):
        monkeypatch.delenv(v, raising=False)


# ------------------------------------------------------------------------------------- 1. the budget is really hard
def test_paging_stops_at_the_deadline_never_a_request_after_it(monkeypatch, clock, no_resolver):
    fx = _fx("novig_nba_catalog.json")
    calls = []

    def slow_get(source, url, params=None, timeout=20.0, deadline=None):
        calls.append((source, deadline))
        clock.t += 19.0                                    # a slow venue: 19 s a page, a `next` cursor every time
        body = fx["events_answer"] if url.endswith("/events") else fx["markets_answer"]
        return _Resp(dict(body, next="cursor"))
    monkeypatch.setattr(x, "_get", slow_get)
    monkeypatch.setattr(x, "store_venue", lambda *a, **k: pytest.fail("a stopped read is never written"))
    out = x.run(None, ["nba", "nfl"], budget_s=30, observed_at=OBS)
    assert len(calls) == 2                                 # 0 s and 19 s; at 38 s nothing more is sent
    assert {d for _, d in calls} == {1030.0}               # one deadline for the whole step, passed to every request
    assert out["nba"]["novig"] == {"ok": False, "skipped": "time budget", "read_s": 38.0}
    assert out["nba"]["gemini"]["skipped"] == "time budget" and out["nfl"]["novig"]["skipped"] == "time budget"


def test_each_request_waits_only_inside_the_time_left(monkeypatch):
    seen = {}
    monkeypatch.setattr(x.ledger, "reserve", lambda src, cost, detail, wait_s: seen.update(wait=wait_s) or (True, None))
    monkeypatch.setattr(x.ledger, "report", lambda *a: None)

    class R:
        status_code = 200

        def iter_content(self, n):
            yield b'{"items": []}'

        def close(self):
            seen["closed"] = True

    def fake_request(url, params, headers, timeout, tries, ok, send):
        seen.update(timeout=timeout, tries=tries)
        return R()
    monkeypatch.setattr(x.net, "_request", fake_request)
    body = x._get("novig", x.NOVIG + "/events", deadline=time.monotonic() + 4.0)
    assert body.json() == {"items": []} and seen["closed"] and seen["tries"] == 1
    assert seen["wait"] <= 3.0                             # the ledger's pacing wait: never past the deadline
    connect, read = seen["timeout"]
    assert connect <= 4.0 and read <= 4.0                  # (connect, read) timeouts cut to the time left


def test_a_trickling_answer_stops_at_the_deadline(monkeypatch, clock):
    monkeypatch.setattr(x.ledger, "reserve", lambda *a, **k: (True, None))
    monkeypatch.setattr(x.ledger, "report", lambda *a: None)

    class Trickle:
        status_code = 200
        closed = False

        def iter_content(self, n):
            while True:                                    # one byte now and then, forever
                clock.t += 4.0
                yield b" "

        def close(self):
            Trickle.closed = True
    monkeypatch.setattr(x.net, "_request", lambda *a: Trickle())
    with pytest.raises(x.Stop):
        x._get("novig", x.NOVIG + "/markets", deadline=clock.t + 30.0)
    assert Trickle.closed


def test_a_retry_after_hold_fails_the_venue_at_once_never_slept(monkeypatch):
    host = "api.novig.com"
    net.hold(host, time.time() + 60)
    try:
        monkeypatch.setattr(x.net, "_request", lambda *a: pytest.fail("no request while the host is held"))
        monkeypatch.setattr(x.ledger, "reserve", lambda *a, **k: pytest.fail("no ledger slot while held"))
        t = time.monotonic()
        with pytest.raises(net.HttpError) as e:
            x._get("novig", x.NOVIG + "/events", deadline=time.monotonic() + 30)
        assert e.value.held and time.monotonic() - t < 1.0
    finally:
        with net._LOCK:
            net._HOLD.pop(host, None)


def test_the_write_stops_at_the_deadline_and_restores_the_busy_wait(tmp_path, clock):
    con = store.connect(tmp_path / "s.db")
    store.migrate(con)
    b = x.adapters.Batch()
    b.records = [h.offer(platform="novig", oid=f"novig:{i}", line=20.5 + i) for i in range(5)]
    r = x.store_venue(con, "novig", "nba", b, OBS, deadline=clock.t + 0.1)
    assert r["stopped"].startswith("time budget") and r["inserted"] == 0
    assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    r = x.store_venue(con, "novig", "nba", b, OBS, deadline=clock.t + 30)
    assert r["inserted"] == 5 and "stopped" not in r
    assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


# ---------------------------------------------------------------------------- 2. cadence: 5 min, one early retry
def test_a_failed_pass_is_retried_once_early_then_the_normal_cadence(monkeypatch):
    results = [{"nba": {"novig": {"ok": False, "error": "503"}}}, {"nba": {"novig": {"ok": False, "error": "503"}}},
               {"nba": {"novig": {"ok": True}}}]
    ran = []
    monkeypatch.setattr(x, "run", lambda con, lgs, budget_s=None, mem_ok=None: ran.append(1) or results[len(ran) - 1])
    assert x.EVERY_S == 300 and x.RETRY_S < x.EVERY_S
    assert x.run_due(None, ["nba"], now=0.0 + 1e6)["due"] is True                      # fails
    assert x.run_due(None, ["nba"], now=1e6 + x.RETRY_S - 1)["due"] is False
    assert x.run_due(None, ["nba"], now=1e6 + x.RETRY_S)["due"] is True                # the one early retry (fails)
    assert x.run_due(None, ["nba"], now=1e6 + 2 * x.RETRY_S)["due"] is False           # never chained
    assert x.run_due(None, ["nba"], now=1e6 + x.RETRY_S + x.EVERY_S)["due"] is True
    assert len(ran) == 3


def test_kill_switched_venues_are_not_a_failure():
    assert not x._failed({"nba": {"novig": {"ok": True, "skipped": "NOVIG=0"}}, "budget_s": 30})
    assert x._failed({"nba": {"gemini": {"ok": False, "skipped": "time budget"}}})


# ---------------------------------------------------------------------- 3. one venue's error is that venue's alone
def test_an_odd_answer_shape_fails_only_that_venue(monkeypatch, no_resolver):
    fx_g = _fx("gemini_nfl_props.json")
    wrote = []

    def get(source, url, params=None, timeout=20.0, deadline=None):
        if source == "novig":
            return _Resp([{"not": "an object"}])           # a JSON list where an object was expected
        return _Resp({"data": fx_g["data"], "pagination": {"total": 3}})
    monkeypatch.setattr(x, "_get", get)
    monkeypatch.setattr(x, "store_venue", lambda con, v, lg, b, obs, **k: wrote.append((lg, v)) or {})
    out = x.run(None, ["nba", "nfl"], observed_at=OBS)
    assert out["nba"]["novig"]["ok"] is False and out["nfl"]["novig"]["ok"] is False
    assert out["nfl"]["gemini"]["ok"] is True and ("nfl", "gemini") in wrote


def test_odd_fields_inside_an_answer_are_skipped_not_fatal(monkeypatch):
    fx = _fx("novig_nba_catalog.json")
    mk = json.loads(json.dumps(fx["markets_answer"]))
    for o in mk["items"][1]["outcomes"]:
        o["bid"] = "0.46"                                   # a string where {price, qty} was expected
    mk["items"].append(None)
    monkeypatch.setattr(x, "_get", lambda s, url, params=None, timeout=20.0, deadline=None: _Resp(
        fx["events_answer"] if url.endswith("/events") else mk))
    raw = x.novig_raw("nba")
    x.novig_offers(raw, "nba", OBS)                          # no AttributeError
    evs = _fx("gemini_nfl_props.json")["data"]
    evs = json.loads(json.dumps(evs))
    evs[0]["sportsMarket"]["scope"] = "first_half"
    assert all(r.market_id != evs[0]["ticker"] for r in x.gemini_offers(evs, "nfl", OBS).records)


def test_a_locked_database_fails_only_that_venue(monkeypatch, no_resolver):
    fx_n, fx_g = _fx("novig_nba_catalog.json"), _fx("gemini_nfl_props.json")

    def get(source, url, params=None, timeout=20.0, deadline=None):
        if source == "gemini":
            return _Resp({"data": fx_g["data"], "pagination": {"total": 3}})
        return _Resp(fx_n["events_answer"] if url.endswith("/events") else fx_n["markets_answer"])
    wrote = []

    def sv(con, v, lg, b, obs, **k):
        if (lg, v) == ("nba", "novig"):
            raise sqlite3.OperationalError("database is locked")
        wrote.append((lg, v))
        return {}
    monkeypatch.setattr(x, "_get", get)
    monkeypatch.setattr(x, "store_venue", sv)
    out = x.run(None, ["nba", "nfl"], observed_at=OBS)
    assert out["nba"]["novig"]["error"].startswith("write: OperationalError")
    assert ("nfl", "novig") in wrote and ("nfl", "gemini") in wrote


# ---------------------------------------------------- 4. partial / empty read: offers written, nothing withdrawn
def test_a_page_limit_or_a_moving_total_marks_the_read_partial(monkeypatch):
    fx = _fx("gemini_nfl_props.json")
    monkeypatch.setattr(x, "_get", lambda s, url, params=None, timeout=20.0, deadline=None: _Resp(
        {"data": fx["data"], "pagination": {"total": 5000}}))
    evs = x.gemini_raw("nfl")
    assert evs.complete is False and len(evs) == 3 * x.GM_MAX_PAGES
    tot = iter([300, 299, 299])
    monkeypatch.setattr(x, "_get", lambda s, url, params=None, timeout=20.0, deadline=None: _Resp(
        {"data": fx["data"], "pagination": {"total": next(tot)}}))
    assert x.gemini_raw("nfl").complete is False
    fn = _fx("novig_nba_catalog.json")
    monkeypatch.setattr(x, "_get", lambda s, url, params=None, timeout=20.0, deadline=None: _Resp(
        dict(fn["events_answer"] if url.endswith("/events") else fn["markets_answer"], next="more")))
    assert x.novig_raw("nba")["complete"] is False


def test_run_passes_partial_and_empty_reads_on_as_incomplete(monkeypatch, no_resolver):
    monkeypatch.setenv("GEMINI", "0")
    got = []
    monkeypatch.setattr(x, "store_venue", lambda con, v, lg, b, obs, complete=True, deadline=None:
                        got.append(complete) or {})
    monkeypatch.setattr(x, "_get", lambda s, url, params=None, timeout=20.0, deadline=None: _Resp({"items": []}))
    x.run(None, ["nba"], observed_at=OBS)
    fn = _fx("novig_nba_catalog.json")
    monkeypatch.setattr(x, "_get", lambda s, url, params=None, timeout=20.0, deadline=None: _Resp(
        fn["events_answer"] if url.endswith("/events") else fn["markets_answer"]))
    x.run(None, ["nba"], observed_at=OBS)
    assert got == [False, True]                              # an empty answer never withdraws the stored lines


def _stored(tmp_path, n=4):
    con = store.connect(tmp_path / "w.db")
    store.migrate(con)
    b = x.adapters.Batch()
    b.records = [h.offer(platform="novig", oid=f"novig:{i}", line=20.5 + i, listed_start_utc="2026-11-10T00:00:00Z",
                         observed_at=OBS, known_at=OBS) for i in range(n)]
    x.store_venue(con, "novig", "nba", b, OBS)
    return con, b


def _current(con):
    return {o.offer_id: o.availability for _, o in store.offers(con, platform="novig")}


def test_an_incomplete_read_withdraws_nothing(tmp_path):
    con, b = _stored(tmp_path)
    part = x.adapters.Batch()
    part.records = b.records[:1]
    r = x.store_venue(con, "novig", "nba", part, "2026-11-09T18:05:00Z", complete=False)
    assert r["removed"] == 0 and set(_current(con).values()) == {"open"}
    r = x.store_venue(con, "novig", "nba", part, "2026-11-09T18:10:00Z", complete=True)
    assert r["removed"] == 3


def test_a_rung_left_out_only_by_the_cap_is_never_withdrawn(tmp_path):
    con, b = _stored(tmp_path)
    kept = x.adapters.Batch()
    kept.records = b.records[:3]
    kept.capped = {b.records[3].offer_id}                    # still listed and priced, 4th nearest even
    r = x.store_venue(con, "novig", "nba", kept, "2026-11-09T18:05:00Z")
    assert r["removed"] == 0 and _current(con)["novig:3"] == "open"


def test_nearest_even_reports_the_rungs_it_dropped():
    b = x.adapters.Batch()
    for i, mid in enumerate((0.70, 0.56, 0.44, 0.30)):
        b.records.append(h.offer(platform="novig", oid=f"novig:{i}", line=18.5 + 2 * i, market_id="m1",
                                 sides=(h.SideQuote("over", True, "exchange_yes_no", bid=mid - 0.01, ask=mid + 0.01),
                                        h.SideQuote("under", True, "exchange_yes_no", bid=0.99 - mid,
                                                    ask=1.01 - mid))))
    x._nearest_even(b)
    assert len(b.records) == 3 and len(b.capped) == 1


# ------------------------------------------------------------------------------- 5. memory guard before every request
def test_the_memory_guard_stops_a_venue_before_its_request(monkeypatch, no_resolver):
    monkeypatch.setattr(x, "_get", lambda *a, **k: pytest.fail("no request over the memory guard"))
    monkeypatch.setattr(x, "store_venue", lambda *a, **k: pytest.fail("nothing written"))
    out = x.run(None, ["nba"], observed_at=OBS, mem_ok=lambda: False)
    assert out["nba"]["novig"]["skipped"] == "memory" and out["nba"]["gemini"]["skipped"] == "memory"


def test_the_books_step_hands_the_guard_down(monkeypatch):
    from sports import worker
    monkeypatch.setattr(worker, "rss_mb", lambda: 40.0)
    monkeypatch.setenv("SPORTS_JOB_BOOKS_CAP_MB", "108")
    monkeypatch.setattr(worker, "_check_budget", lambda: None)
    seen = {}
    monkeypatch.setattr(x, "run_due", lambda con, lgs, mem_ok=None: seen.update(g=mem_ok) or {"due": True})
    worker._exchanges_step(None, ["nba"])
    assert seen["g"]() is True
    monkeypatch.setattr(worker, "rss_mb", lambda: 80.0)       # 80 + 30 > 108: the next page could reach the cap
    assert seen["g"]() is False


# -------------------------------------------------------------------- 6. never a market chance / settlement / lab line
def test_the_market_column_fallback_never_uses_a_display_only_price():
    nv = {"p": "novig", "l": 24.5, "kind": "mid", "c": 0.42, "in": False, "age": 30}
    assert market_view.fallback_at([nv], 24.5) is None
    fd = {"p": "fanduel", "l": 24.5, "kind": "devig", "c": 0.55, "in": False, "age": 1200}
    assert market_view.fallback_at([nv, fd], 24.5)["books"] == ["fanduel"]


def test_display_only_offers_are_never_settled(monkeypatch):
    from sports import adapters, espn, settlement, worker
    monkeypatch.setattr(worker, "_game_store_final", lambda *a: None)
    monkeypatch.setattr(espn, "summary_and_raw", lambda lg, eid, refresh=False: ({"completed": True}, None))

    def box(summ, lg, eid, observed_at):
        b = adapters.Batch()
        b.records = [h.stat()]
        return b
    monkeypatch.setattr(adapters, "espn_box", box)
    settled = []
    monkeypatch.setattr(settlement, "settle_offer", lambda o, obs, e, at: settled.append(o.platform) or [])
    with store.opened() as con:
        store.put(con, h.event(status="final"))
        store.put_many(con, [h.offer(), h.offer(platform="novig", oid="novig:1", variant="main"),
                             h.offer(platform="gemini", oid="gemini:1", variant="main")])
        worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
    assert settled == ["prizepicks"]


def test_the_lab_offered_line_is_never_a_display_only_rung(monkeypatch):
    from sports.lab import dataset
    with store.opened() as con:
        store.put_many(con, [h.offer(), h.offer(platform="novig", oid="novig:1", variant="main", line=22.5,
                                                observed_at="2026-10-23T21:00:00Z", known_at="2026-10-23T21:00:00Z")])
    lines = dataset._offer_lines("nba", "points", "2026-10-23T22:00:00Z")
    assert [ln for _, ln, _ in lines[("nba:espn:1", "nba:player:espn:3134907")]] == [20.5]
