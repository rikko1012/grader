"""Series-winner market baseline (moneyline.py + kalshi.py + polymarket_ml.py).

All data comes from recorded fixtures (tests/fixtures/ml_polymarket.json, ml_kalshi.json - real
responses recorded 2026-10-12).  No network: fetchers are replaced with fakes."""
import copy
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

import kalshi
import moneyline
import polymarket_ml

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 23, 0, tzinfo=timezone.utc)      # before every fixture match starts



@pytest.fixture(autouse=True)
def recorded_clock(monkeypatch):
    """The fixtures were recorded on 2026-10-11/12: run as if it were still then (see conftest.pin_clock)."""
    import sys
    from datetime import datetime as _d, timezone as _tz
    from tests.conftest import pin_clock
    mods = [sys.modules[n] for n in ("moneyline", "kalshi", "polymarket_ml", "matchid", "server", "slip_builder") if n in sys.modules]
    pin_clock(monkeypatch, _d(2026, 10, 12, 0, 45, tzinfo=_tz.utc), mods)

@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(moneyline, "_FC_LAST", {})
    monkeypatch.setattr(moneyline, "_MATCH_CANON", {})
    monkeypatch.setattr(moneyline, "_GROUP", {})                                 # cross-platform started / BO2 signal
    monkeypatch.setattr(moneyline, "_LAST", {"path": None, "map": {}})           # change-only snapshot memory
    monkeypatch.setattr(moneyline, "canonical", moneyline._fallback_canonical)   # no HLTV in unit tests
    return tmp_path


def poly_raw():
    P = json.load(open(FIX / "ml_polymarket.json", encoding="utf-8"))
    return {"events": P["events"], "books": {b["asset_id"]: b for b in P["books"]},
            "received_ts": "2026-10-12T00:05:00.000000Z", "book_ts": "2026-10-12T00:05:01.000000Z",
            "book_error": None}


def kalshi_raw(with_map=True):
    K = json.load(open(FIX / "ml_kalshi.json", encoding="utf-8"))
    return {"events": K["events"] + (K["map_events"] if with_map else []), "books": K["orderbooks"],
            "received_ts": "2026-10-12T00:05:00.000000Z", "book_ts": {}, "book_errors": {}}


def make_sources(poly=None, kal=None, canon=None):
    """Sources fed by fixtures; poly / kal = callables returning raw (or raising)."""
    canon = canon or moneyline._fallback_canonical
    return {
        "polymarket": moneyline.Source("polymarket", poly or poly_raw, polymarket_ml.normalize, canonical_fn=canon),
        "kalshi": moneyline.Source("kalshi", kal or kalshi_raw, kalshi.normalize, canonical_fn=canon),
    }


def refreshed(**kw):
    s = make_sources(**kw)
    for src in s.values():
        src.refresh_once()
    return s


def find(board, *names):
    names = {n.lower() for n in names}
    for m in board:
        if {(m["teams"]["a"]["name"] or "").lower(), (m["teams"]["b"]["name"] or "").lower()} == names:
            return m
    raise AssertionError(f"no match {names}")


def down(*_a, **_k):
    raise requests.Timeout("read timed out (10 s)")


# ------------------------------------------------------------------ orientation / linking

def test_reversed_team_order_gets_the_right_probability():
    b = moneyline.board(refreshed(), now=NOW)
    m = find(b, "Chicken Coop Esports", "Zomblers")
    # Polymarket lists [Chicken Coop, Zomblers]; Kalshi lists [Zomblers, Chicken Coop]
    assert m["match_id"] and m["teams"]["a"]["key"] == "chicken coop"
    p, k = m["platforms"]["polymarket"], m["platforms"]["kalshi"]
    assert p["team_a_name"] == "Chicken Coop Esports" and k["team_a_name"] == "Chicken Coop Esports"
    assert p["prob_a"] == pytest.approx((0.62 + 0.70) / 2)
    assert k["prob_a"] == pytest.approx((0.62 + 0.70) / 2)          # Chicken Coop's own YES, not Zomblers'
    assert k["prob_b_yes"] == pytest.approx((0.26 + 0.41) / 2)

    m = find(b, "Liquid", "BBL")                                    # canonical A = "bbl"
    assert m["teams"]["a"]["name"] == "BBL"
    assert m["platforms"]["polymarket"]["prob_a"] == pytest.approx(0.28)
    assert m["platforms"]["kalshi"]["prob_a"] == pytest.approx(0.27)
    assert m["platforms"]["polymarket"]["outcome_id"] != m["platforms"]["polymarket"]["quotes"][1]["outcome_id"]


def test_five_matches_link_across_platforms_and_shapes():
    b = moneyline.board(refreshed(), now=NOW)
    linked = [m for m in b if m["platforms"]["polymarket"] and m["platforms"]["kalshi"]]
    assert len(b) == 5 and len(linked) == 5
    for m in b:
        assert m["scope"] == "series"
        assert set(m["baseline"]) >= {"platform", "prob_a", "rule", "reason", "label", "baseline"}
        assert "frozen" not in m["baseline"]["rule"] and m["baseline"]["rule"].startswith("ml-baseline-1")
        for blk in m["platforms"].values():
            assert set(blk) >= {"prob_a", "bid", "ask", "spread", "depth", "status", "current", "usable",
                                "reason", "source_ts", "received_ts", "url"}


def test_unlinked_or_ambiguous_shown_but_never_baseline():
    def canon(a, b, start, **kw):
        c = moneyline._fallback_canonical(a, b, start, **kw)
        if "Liquid" in (a, b):
            return dict(c, match_id=None, status="ambiguous", reason="two HLTV matches fit")
        return c
    b = moneyline.board(refreshed(canon=canon), now=NOW)
    solo = [m for m in b if "Liquid" in (m["teams"]["a"]["name"], m["teams"]["b"]["name"])]
    assert len(solo) == 2                                   # one per platform, not merged
    for m in solo:
        assert m["match_id"] is None and "two HLTV matches fit" in m["link_reason"]
        assert m["baseline"]["prob_a"] is None and m["baseline"]["label"] == moneyline.UNAVAILABLE
        blk = m["platforms"]["polymarket"] or m["platforms"]["kalshi"]
        assert blk["prob_a"] is not None                    # still shown


# ------------------------------------------------------------------ Kalshi YES / NO

def test_kalshi_no_is_complement_of_own_yes_not_other_team():
    raw = kalshi_raw()
    ev = next(e for e in raw["events"] if e["event_ticker"].endswith("TUSTA"))
    a, b = ev["markets"]                                  # THE UNIT, STATE
    raw["books"][a["ticker"]] = {"orderbook_fp": {"yes_dollars": [["0.30", "50"], ["0.40", "200"]],
                                                  "no_dollars": [["0.50", "10"], ["0.55", "300"]]}}
    raw["books"][b["ticker"]] = {"orderbook_fp": {"yes_dollars": [["0.60", "300"]],
                                                  "no_dollars": [["0.34", "300"]]}}
    L = next(x for x in kalshi.normalize(raw) if x["event_id"] == ev["event_ticker"])
    qa = L["quotes"][0]
    assert qa["team_name"] == a["yes_sub_title"]
    assert (qa["bid"], qa["ask"]) == (0.40, 0.45)           # ask = 1 - best NO bid of the SAME market
    assert (qa["bid_size"], qa["ask_size"]) == (200.0, 300.0)

    srcs = refreshed(kal=lambda: raw)
    m = find(moneyline.board(srcs, now=NOW), "THE UNIT", "STATE")
    k = m["platforms"]["kalshi"]
    assert m["teams"]["a"]["key"] == "state"                # sorted: state < the unit -> A = STATE
    assert k["prob_a"] == pytest.approx(0.63)               # STATE's own YES midpoint
    assert k["prob_b_yes"] == pytest.approx(0.425)          # THE UNIT YES - recorded, not averaged in
    assert k["coherence_gap"] == pytest.approx(0.055) and k["coherence_flag"] is True


def test_kalshi_start_comes_from_rules_not_close_time():
    L = {x["event_id"]: x for x in kalshi.normalize(kalshi_raw())}
    assert L["KXCS2GAME-26OCT122100ZOMBCHI"]["start"] == "2026-10-13T01:00:00Z"     # 9:00 PM EDT
    assert kalshi.event_start({"event_ticker": "KXCS2GAME-26DEC121300ABCD", "markets": []}) \
        == datetime(2026, 12, 12, 18, 0, tzinfo=timezone.utc)                            # EST fallback


# ------------------------------------------------------------------ quotes vs last trade, usability

def test_old_last_trade_is_never_a_quote():
    raw = poly_raw()
    L = next(x for x in polymarket_ml.normalize(raw) if x["teams"][0] == "STATE")
    q = L["quotes"][0]
    assert q["last_trade"] == {"price": 0.21, "label": "last trade (not a quote)"}
    srcs = refreshed()
    m = find(moneyline.board(srcs, now=NOW), "STATE", "THE UNIT")
    assert m["platforms"]["polymarket"]["prob_a"] == pytest.approx(0.455)      # midpoint, not 0.21

    # empty book with a last trade -> no probability at all
    tok = q["outcome_id"]
    for b in raw["books"].values():
        if b["market"] == q["raw"]["condition_id"]:
            b["bids"], b["asks"] = [], []
    srcs = refreshed(poly=lambda: raw)
    m = find(moneyline.board(srcs, now=NOW), "STATE", "THE UNIT")
    p = m["platforms"]["polymarket"]
    assert p["outcome_id"] == tok and p["prob_a"] is None and p["usable"] is False
    assert "one-sided" in p["reason"] and p["last_trade"]["price"] == 0.21
    assert m["baseline"]["platform"] == "kalshi"


def test_wide_spread_and_thin_depth_unusable_and_none_is_not_fifty():
    srcs = refreshed()
    m = find(moneyline.board(srcs, now=NOW), "GenOne", "ACIDHOUZE")
    for blk in m["platforms"].values():
        assert blk["usable"] is False and "spread" in blk["reason"]
    assert m["baseline"]["prob_a"] is None and m["baseline"]["baseline"] is None
    assert m["baseline"]["label"] == "Market probability unavailable"

    q = {"bid": 0.5, "ask": 0.52, "bid_size": 3, "ask_size": 500, "market_open": True,
         "depth": {"bid_5c": 400, "ask_5c": 900}, "start": "2026-10-12T10:00:00Z"}
    assert "thin book" in moneyline.evaluate(q, NOW, True)[2]
    q.update(bid_size=50, depth={"bid_5c": 60, "ask_5c": 900})
    assert "within 5c" in moneyline.evaluate(q, NOW, True)[2]
    q.update(depth={"bid_5c": 600, "ask_5c": 900})
    assert moneyline.evaluate(q, NOW, True)[1] is True


def test_clob_down_gamma_quote_shown_but_unusable():
    raw = poly_raw()
    raw["books"], raw["book_error"] = {}, "Timeout: clob"
    srcs = refreshed(poly=lambda: raw)
    m = find(moneyline.board(srcs, now=NOW), "Liquid", "BBL")
    p = m["platforms"]["polymarket"]
    assert p["prob_a"] == pytest.approx(1 - (0.70 + 0.74) / 2) and p["source_ts"] is None
    assert p["usable"] is False and "no depth data" in p["reason"]
    assert m["baseline"]["platform"] == "kalshi"


def test_started_match_unusable():
    srcs = refreshed()
    later = datetime(2026, 10, 12, 8, 30, tzinfo=timezone.utc)
    m = find(moneyline.board(srcs, now=later), "STATE", "THE UNIT")
    for blk in m["platforms"].values():
        assert blk["usable"] is False and "started" in blk["reason"]
    assert m["baseline"]["prob_a"] is None
    # Polymarket's own live flag counts too
    raw = poly_raw()
    for ev in raw["events"]:
        ev["live"] = True
    m = find(moneyline.board(refreshed(poly=lambda: raw), now=NOW), "Liquid", "BBL")
    assert "started" in m["platforms"]["polymarket"]["reason"]


def test_map_markets_ignored():
    raw = kalshi_raw(with_map=True)
    ls = kalshi.normalize(raw)
    assert all("KXCS2MAP" not in x["event_id"] for x in ls) and len(ls) == 5
    pl = polymarket_ml.normalize(poly_raw())
    P = json.load(open(FIX / "ml_polymarket.json", encoding="utf-8"))
    ml_ids = {m["id"] for e in P["events"] for m in e["markets"] if m["sportsMarketType"] == "moneyline"}
    assert len(pl) == 5 and {q["market_id"] for x in pl for q in x["quotes"]} == ml_ids
    # an event that only has map-winner markets produces nothing
    only_maps = copy.deepcopy(P["events"][0])
    only_maps["markets"] = [m for m in only_maps["markets"] if m["sportsMarketType"] == "child_moneyline"]
    assert only_maps["markets"] and polymarket_ml.normalize({"events": [only_maps], "books": {}}) == []


# ------------------------------------------------------------------ outages

def test_one_platform_down_other_still_served():
    srcs = refreshed(kal=down)
    b = moneyline.board(srcs, now=NOW)
    assert len(b) == 5
    m = find(b, "Liquid", "BBL")
    assert m["platforms"]["kalshi"] is None
    assert m["baseline"]["platform"] == "polymarket" and m["baseline"]["prob_a"] == pytest.approx(0.28)
    st = moneyline.status(srcs)
    assert st["kalshi"]["ok"] is False and "Timeout" in st["kalshi"]["last_error"]
    assert st["polymarket"]["ok"] is True


def test_both_down_unavailable_not_fifty_and_last_good_kept_stale():
    calls = {"n": 0}

    def flaky_k():
        calls["n"] += 1
        if calls["n"] > 1:
            down()
        return kalshi_raw()
    pcalls = {"n": 0}

    def flaky_p():
        pcalls["n"] += 1
        if pcalls["n"] > 1:
            down()
        return poly_raw()
    srcs = refreshed(poly=flaky_p, kal=flaky_k)
    for s in srcs.values():
        assert s.refresh_once() is False                    # second refresh fails
    b = moneyline.board(srcs, now=NOW)
    assert len(b) == 5                                      # last good data still shown...
    for m in b:
        assert m["baseline"]["prob_a"] is None and m["baseline"]["label"] == moneyline.UNAVAILABLE
        for blk in m["platforms"].values():
            assert blk["current"] is False and blk["reason"].startswith("stale")
    st = moneyline.status(srcs)
    assert st["kalshi"]["stale"] and st["kalshi"]["age_s"] is not None and st["kalshi"]["listings"] == 5

    nothing = make_sources(poly=down, kal=down)
    for s in nothing.values():
        s.refresh_once()
    assert moneyline.board(nothing, now=NOW) == []
    r = moneyline.match("Liquid", "BBL", "2026-10-13T10:00:00Z", sources=nothing, now=NOW)
    assert r["baseline"]["baseline"] is None and r["baseline"]["label"] == moneyline.UNAVAILABLE


def test_backoff_doubles_to_ten_minutes_and_resets():
    s = moneyline.Source("x", down, lambda raw: [], snapshot=False)
    delays = []
    for _ in range(6):
        s.refresh_once()
        delays.append(s.next_delay)
    assert delays == [120, 240, 480, 600, 600, 600]
    s.fetch_fn = lambda: {}
    assert s.refresh_once() and s.next_delay == 60 and s.failures == 0


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _Session:
    """Records every call; answers from the fixtures; can time out chosen URLs."""
    def __init__(self, timeout_on=()):
        self.calls, self.timeout_on = [], timeout_on
        self.P = json.load(open(FIX / "ml_polymarket.json", encoding="utf-8"))
        self.K = json.load(open(FIX / "ml_kalshi.json", encoding="utf-8"))

    def _hit(self, url, kw):
        self.calls.append((url, kw))
        if any(t in url for t in self.timeout_on):
            raise requests.Timeout(url)

    def get(self, url, **kw):
        self._hit(url, kw)
        if url.endswith("/events") and "kalshi" in url:
            return _Resp({"events": self.K["events"], "cursor": ""})
        if "/orderbook" in url:
            return _Resp(self.K["orderbooks"][url.split("/markets/")[1].split("/")[0]])
        if url.endswith("/events"):
            return _Resp(self.P["events"])
        raise AssertionError(url)

    def post(self, url, **kw):
        self._hit(url, kw)
        return _Resp(self.P["books"])


def test_adapters_send_user_agent_and_bounded_timeouts():
    s = _Session()
    kraw = kalshi.fetch(s, now=NOW)
    praw = polymarket_ml.fetch(s, now=NOW)
    assert len(kalshi.normalize(kraw)) == 5 and len(polymarket_ml.normalize(praw)) == 5
    assert s.calls
    for url, kw in s.calls:
        assert kw["timeout"] == 10 and kw["headers"]["User-Agent"] == "cs2-scout/1.0"
    assert sum("/orderbook" in u for u, _ in s.calls) == 10


def test_book_timeouts_degrade_but_listing_timeout_fails_refresh():
    s = _Session(timeout_on=("/orderbook", "clob.polymarket.com"))
    kraw = kalshi.fetch(s, now=NOW)
    assert len(kraw["book_errors"]) == 10
    q = kalshi.normalize(kraw)[0]["quotes"][0]
    assert q["depth"]["source"] == "market best level only" and "Timeout" in q["depth"]["error"]
    praw = polymarket_ml.fetch(s, now=NOW)
    assert praw["book_error"].startswith("Timeout") and polymarket_ml.normalize(praw)[0]["quotes"][0]["bid_size"] is None
    with pytest.raises(requests.Timeout):
        kalshi.fetch(_Session(timeout_on=("kalshi.com/trade-api/v2/events",)), now=NOW)
    with pytest.raises(requests.Timeout):
        polymarket_ml.fetch(_Session(timeout_on=("gamma-api",)), now=NOW)


# ------------------------------------------------------------------ baseline rule

def _blk(spread, depth, prob, usable=True):
    return {"usable": usable, "spread": spread, "best_depth": depth, "prob_a": prob, "reason": None if usable else "x"}


def test_frozen_baseline_rule():
    ch = moneyline.choose_baseline
    assert ch(_blk(0.02, 10, 0.6), _blk(0.01, 5, 0.62))["platform"] == "kalshi"          # smaller spread
    assert ch(_blk(0.02, 10, 0.6), _blk(0.02, 50, 0.62))["platform"] == "kalshi"         # tie -> depth
    r = ch(_blk(0.02, 50, 0.6), _blk(0.02, 50, 0.62))
    assert r["platform"] == "polymarket" and "tie -> Polymarket" in r["reason"]
    assert ch(_blk(0.02, 50, 0.6), _blk(0.5, 50, 0.62, usable=False))["platform"] == "polymarket"
    assert ch(None, _blk(0.03, 50, 0.4))["prob_a"] == 0.4
    r = ch(None, None)
    assert r["baseline"] is None and r["prob_a"] is None and r["label"] == "Market probability unavailable"
    assert "never 50%" in r["rule"]


def test_disagreement_flag():
    raw = kalshi_raw()
    ev = next(e for e in raw["events"] if e["event_ticker"].endswith("BBLTL"))
    bbl = next(m for m in ev["markets"] if m["yes_sub_title"] == "BBL")
    raw["books"][bbl["ticker"]] = {"orderbook_fp": {"yes_dollars": [["0.34", "500"]], "no_dollars": [["0.64", "500"]]}}
    m = find(moneyline.board(refreshed(kal=lambda: raw), now=NOW), "Liquid", "BBL")
    assert m["platforms"]["kalshi"]["prob_a"] == pytest.approx(0.35)
    assert m["disagreement"] is True and m["disagreement_gap"] == pytest.approx(0.07)
    m = find(moneyline.board(refreshed(), now=NOW), "Liquid", "BBL")
    assert m["disagreement"] is False


# ------------------------------------------------------------------ snapshots + shadow log

def test_snapshots_append_all_quotes_and_insert_is_idempotent(data_dir):
    srcs = refreshed()
    db = data_dir / ".cache" / "ml_snapshots.db"
    con = sqlite3.connect(db)
    n = con.execute("select count(*) from ml_quotes").fetchone()[0]
    assert n == 20                                          # 10 polymarket outcomes + 10 kalshi YES
    usable = con.execute("select count(*) from ml_quotes where usable=1").fetchone()[0]
    assert 0 < usable < n                                   # unusable rows stored too
    assert con.execute("select count(*) from ml_quotes where match_id is null").fetchone()[0] == 0
    rows = moneyline._snapshot_rows(srcs["kalshi"].listings, NOW)
    assert moneyline.write_snapshots(rows) == 0            # same key -> ignored
    assert con.execute("select count(*) from ml_quotes").fetchone()[0] == n
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("update ml_quotes set bid=0.5")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("delete from ml_quotes")
    con.close()
    # change-only: the same quotes a minute later are NOT written again (190 MB/day otherwise) ...
    srcs["kalshi"].fetch_fn = lambda: dict(kalshi_raw(), received_ts="2026-10-12T00:06:00.000000Z")
    srcs["kalshi"].refresh_once()
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from ml_quotes").fetchone()[0] == n
    con.close()
    # ... one changed book is (only that quote) ...
    raw = kalshi_raw()
    raw["received_ts"] = "2026-10-12T00:07:00.000000Z"
    ev = next(e for e in raw["events"] if e["event_ticker"].endswith("BBLTL"))
    bbl = next(m for m in ev["markets"] if m["yes_sub_title"] == "BBL")
    raw["books"][bbl["ticker"]] = {"orderbook_fp": {"yes_dollars": [["0.30", "500"]], "no_dollars": [["0.66", "500"]]}}
    srcs["kalshi"].fetch_fn = lambda: raw
    srcs["kalshi"].refresh_once()
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from ml_quotes").fetchone()[0] == n + 1
    con.close()
    # ... and every quote again after the 10-minute heartbeat
    srcs["kalshi"].fetch_fn = lambda: dict(raw, received_ts="2026-10-12T00:17:30.000000Z")
    srcs["kalshi"].refresh_once()
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from ml_quotes").fetchone()[0] == n + 1 + 10
    con.close()
    # a restart (memory lost) rebuilds the last-written state from the db: still nothing duplicated
    moneyline._LAST.update(path=None, map={})
    srcs["kalshi"].fetch_fn = lambda: dict(raw, received_ts="2026-10-12T00:18:30.000000Z")
    srcs["kalshi"].refresh_once()
    con = sqlite3.connect(db)
    assert con.execute("select count(*) from ml_quotes").fetchone()[0] == n + 1 + 10
    con.close()


def test_snapshot_store_size_cap_pauses_and_reports(data_dir, monkeypatch):
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 0.0)
    srcs = refreshed()
    st = moneyline.status(srcs)
    assert "cap" in st["kalshi"]["snapshot_error"] and st["kalshi"]["snapshot_rows_written"] == 0
    assert moneyline.board(srcs, now=NOW)                    # the quotes are still served


# ------------------------------------------------------------------ one started / BO2 decision per match

def _liquid_live(raw):
    for ev in raw["events"]:
        if "Liquid" in ev["title"]:
            ev["live"] = True
    return raw


def test_polymarket_live_makes_kalshi_block_of_same_match_unusable(data_dir):
    """Kalshi has no live flag and keeps the originally scheduled start: a match that starts early is live on Polymarket
    while Kalshi's start is still in the future. Kalshi's in-play price must never become the baseline."""
    srcs = make_sources(poly=lambda: _liquid_live(poly_raw()))
    srcs["polymarket"].refresh_once()
    srcs["kalshi"].refresh_once()
    m = find(moneyline.board(srcs, now=NOW), "Liquid", "BBL")
    k, p = m["platforms"]["kalshi"], m["platforms"]["polymarket"]
    assert p["usable"] is False and "started" in p["reason"]
    assert k["usable"] is False and "another platform marks it live" in k["reason"]
    assert m["baseline"]["prob_a"] is None and m["baseline"]["platform"] is None
    assert m["started"] is True
    assert moneyline.log_forecasts(srcs, now=NOW) >= 1
    con = sqlite3.connect(data_dir / ".cache" / "ml_snapshots.db")
    rows = con.execute("select platform, usable, prob_a, unusable_reason from ml_quotes where team_a='BBL'").fetchall()
    fc = con.execute("select available, prob_a from ml_forecasts where team_a='BBL'").fetchall()
    con.close()
    assert {r[0] for r in rows} == {"polymarket", "kalshi"}
    for plat, usable, prob_a, why in rows:
        assert usable == 0 and prob_a is None, (plat, why)
    assert any(r[0] == "kalshi" and "another platform marks it live" in r[3] for r in rows)
    assert fc == [(0, None)]
    # the earliest stated start counts too: Kalshi's original start passed while Polymarket's moved later
    later = datetime(2026, 10, 13, 10, 5, tzinfo=timezone.utc)
    raw = poly_raw()
    for ev in raw["events"]:
        if "Liquid" in ev["title"]:
            for mk in ev["markets"]:
                mk["gameStartTime"] = "2026-10-13 12:00:00+00"
    m = find(moneyline.board(refreshed(poly=lambda: raw), now=later), "Liquid", "BBL")
    assert m["started"] is True and m["start"] == "2026-10-13T10:00:00.000000Z"
    assert not m["platforms"]["polymarket"]["usable"] and m["baseline"]["prob_a"] is None


def test_polymarket_outage_never_blocks_kalshi(data_dir):
    srcs = refreshed(poly=down)
    m = find(moneyline.board(srcs, now=NOW), "Liquid", "BBL")
    assert m["platforms"]["kalshi"]["usable"] is True and m["baseline"]["platform"] == "kalshi"


def _bo2(raw, team="Liquid"):
    for ev in raw["events"]:
        if team in ev["title"]:
            for mk in ev["markets"]:
                if mk.get("question"):
                    mk["question"] = mk["question"].replace("(BO3)", "(BO2)")
    return raw


def test_best_of_two_is_a_price_not_a_win_probability(data_dir):
    srcs = make_sources(poly=lambda: _bo2(poly_raw()))
    srcs["polymarket"].refresh_once()
    srcs["kalshi"].refresh_once()
    m = find(moneyline.board(srcs, now=NOW), "Liquid", "BBL")
    assert m["tie_possible"] is True and m["bo"] == 2
    assert m["baseline"]["prob_a"] is None and "best-of-2" in m["baseline"]["reason"]
    for blk in m["platforms"].values():               # Kalshi states no BO: the match decision covers its block too
        assert blk["usable"] is False and blk["price_only"] is True and "draw is possible" in blk["reason"]
        assert blk["prob_a"] is not None              # the price stays on display, labelled as a price
    other = find(moneyline.board(srcs, now=NOW), "STATE", "THE UNIT")
    assert other["tie_possible"] is False and other["baseline"]["prob_a"] is not None
    moneyline.log_forecasts(srcs, now=NOW)
    con = sqlite3.connect(data_dir / ".cache" / "ml_snapshots.db")
    assert con.execute("select count(*) from ml_forecasts where team_a='BBL'").fetchone()[0] == 0
    assert con.execute("select count(*) from ml_quotes where team_a='BBL' and prob_a is not null").fetchone()[0] == 0
    assert con.execute("select count(*) from ml_forecasts where team_a='STATE'").fetchone()[0] == 1
    con.close()


def test_kalshi_rules_with_a_draw_outcome_are_never_a_baseline(data_dir):
    raw = kalshi_raw()
    ev = next(e for e in raw["events"] if e["event_ticker"].endswith("TUSTA"))
    for mk in ev["markets"]:
        mk["rules_primary"] += " If the match ends in a draw (1-1), the market resolves to No."
    srcs = refreshed(poly=down, kal=lambda: raw)
    m = find(moneyline.board(srcs, now=NOW), "STATE", "THE UNIT")
    assert m["tie_possible"] is True and m["baseline"]["prob_a"] is None
    assert m["platforms"]["kalshi"]["price_only"] is True
    # the Polymarket 50-50 tie boilerplate on a BO3 is NOT a draw signal
    m = find(moneyline.board(refreshed(), now=NOW), "Liquid", "BBL")
    assert "tie" in m["platforms"]["polymarket"]["rules"] and m["tie_possible"] is False


# ------------------------------------------------------------------ freshness vs market open

def test_fresh_but_closed_market_is_not_called_stale():
    raw = kalshi_raw()
    ev = next(e for e in raw["events"] if e["event_ticker"].endswith("BBLTL"))
    for mk in ev["markets"]:
        mk["status"] = "closed"
    srcs = refreshed(kal=lambda: raw)
    k = find(moneyline.board(srcs, now=NOW), "Liquid", "BBL")["platforms"]["kalshi"]
    assert k["fresh"] is True and k["market_open"] is False
    assert k["usable"] is False and k["current"] is False and "market not open" in k["reason"]
    p = find(moneyline.board(srcs, now=NOW), "Liquid", "BBL")["platforms"]["polymarket"]
    assert p["fresh"] is True and p["market_open"] is True


def test_shadow_forecast_log_on_change_or_every_30_min(data_dir):
    srcs = refreshed()
    t0 = datetime(2026, 10, 11, 23, 0, tzinfo=timezone.utc)
    assert moneyline.log_forecasts(srcs, now=t0) == 5
    assert moneyline.log_forecasts(srcs, now=t0.replace(minute=10)) == 0          # unchanged, < 30 min
    assert moneyline.log_forecasts(srcs, now=t0.replace(minute=31)) == 5          # 30 min re-log
    raw = kalshi_raw()
    ev = next(e for e in raw["events"] if e["event_ticker"].endswith("BBLTL"))
    for mk in ev["markets"]:
        raw["books"][mk["ticker"]] = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
    raw2 = poly_raw()
    for ev2 in raw2["events"]:
        if "Liquid" in ev2["title"]:
            ev2["live"] = True
    srcs["kalshi"].fetch_fn = lambda: raw
    srcs["polymarket"].fetch_fn = lambda: raw2
    for s in srcs.values():
        s.refresh_once()
    assert moneyline.log_forecasts(srcs, now=t0.replace(minute=35)) == 1          # BBL/Liquid lost its baseline
    con = sqlite3.connect(data_dir / ".cache" / "ml_snapshots.db")
    rows = con.execute("select model_version, available, prob_a from ml_forecasts where team_a='BBL' order by logged_ts").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["ml-baseline-1"] * 3 and rows[-1][1:] == (0, None)


def test_start_is_idempotent_one_thread_per_source():
    srcs = make_sources()
    for s in srcs.values():
        s.interval = 3600
    try:
        assert sorted(moneyline.start(srcs)) == ["kalshi", "polymarket"]
        assert moneyline.start(srcs) == []
        deadline = time.time() + 10
        while time.time() < deadline and not all(s.refreshes for s in srcs.values()):
            time.sleep(0.05)
        assert all(s.refreshes == 1 for s in srcs.values())
        assert all(st["running"] for st in moneyline.status(srcs).values())
    finally:
        moneyline.stop(srcs, join=15)
    assert not any(st["running"] for st in moneyline.status(srcs).values())


def test_match_lookup_oriented_and_cached():
    srcs = refreshed()
    r = moneyline.match("Zomblers", "Chicken Coop Esports", "2026-10-13T01:00:00Z", sources=srcs, now=NOW)
    assert r["query_team_is_a"] is False and r["teams"]["a"]["name"] == "Chicken Coop Esports"
    assert r["baseline"]["prob_a"] == pytest.approx(0.66)


def test_matchid_style_ids_orient_by_team_keys():
    ids = {"liquid": "team:5973", "bbl": "team:7020"}

    def canon(a, b, start, **kw):
        ka, kb = ids.get(a.lower()), ids.get(b.lower())
        if not ka or not kb:
            return {"match_id": None, "team_keys": None, "flipped": False, "status": "unlinked",
                    "reason": "not on HLTV"}
        keys = sorted([ka, kb])
        return {"match_id": "hltv:2390001", "team_keys": keys, "flipped": [ka, kb] != keys, "status": "linked",
                "reason": "HLTV match on the schedule"}
    b = moneyline.board(refreshed(canon=canon), now=NOW)
    linked = [m for m in b if m["match_id"]]
    assert len(linked) == 1 and len(b) == 9                 # 4 unlinked matches x 2 platforms shown separately
    m = linked[0]
    assert m["teams"]["a"] == {"key": "team:5973", "name": "Liquid"}
    assert m["platforms"]["polymarket"]["prob_a"] == pytest.approx(0.72)
    assert m["platforms"]["kalshi"]["prob_a"] == pytest.approx(0.73)
    assert m["baseline"]["platform"] in ("polymarket", "kalshi") and m["baseline"]["prob_a"] is not None
    assert all(x["baseline"]["prob_a"] is None for x in b if not x["match_id"])


def test_forward_rows_readable_by_ml_eval(data_dir):
    ml_eval = pytest.importorskip("ml_eval")
    if not hasattr(ml_eval, "forward_rows"):
        pytest.skip("ml_eval has no forward_rows")
    import collections
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(moneyline, "_utcnow", lambda: NOW)            # snapshots taken before every fixture start
    try:
        late = "2026-10-13T00:00:00.000000Z"                          # 1 h before Chicken Coop - Zomblers (01:00)
        refreshed(poly=lambda: dict(poly_raw(), received_ts=late, book_ts=late),
                  kal=lambda: dict(kalshi_raw(), received_ts=late))
    finally:
        monkeypatch.undo()
    excl = collections.Counter()
    rows, why = ml_eval.forward_rows(str(data_dir / ".cache" / "ml_snapshots.db"), excl)
    assert why is None
    srcs = {r["source"] for r in rows}
    assert srcs <= {"polymarket", "kalshi"} and rows
    cc = [r for r in rows if {r["team_a"], r["team_b"]} == {"Chicken Coop Esports", "Zomblers"}]
    assert {r["team_a"] for r in cc} == {"Chicken Coop Esports"} and {r["source"] for r in cc} == {"polymarket", "kalshi"}
    # Liquid - BBL (10:00): its only snapshot is 9.5 h before the cutoff -> a counted stale exclusion, not a score
    assert excl["polymarket: stale snapshot (> 6 h before cutoff)"] >= 1
