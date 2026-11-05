"""End to end through the ONE canonical ingestion path: a book quote collected by the books job (sports.worker source
"books") lands as an sc-1 offer, is used by the model's market comparison (mkt-fanduel) and appears in the UI
snapshot - with exactly one provider request, none from the model or the snapshot builder. Also: mc-1 / mi-1 keep their
registered source set, settlement feeds the game store from its own fetch, and duplicate pollers are gone."""
import json

from sports import adapters, net, snapshots, store
from sports import worker as canonical
from sports.books import base, bridge, providers, sources
from sports.books import worker as book_worker
from sports.model import datasets, forward, market
from sports.tests import helpers as h
from sports.contracts import Membership, Player, Team
from sports import identity
from sports.tests.test_model import TEAMS, _hist

COLLECT_AT = "2026-01-31T20:55:00Z"
FORECAST_AT = "2026-01-31T21:00:00Z"
START = "2026-02-01T00:00:00Z"


class OneBookFeed(base.Provider):
    """A FanDuel-shaped direct feed answering from memory; every events() / props() call stands for ONE request."""
    name, books, kind = "fanduel", ("fanduel",), "direct"

    def __init__(self):
        self.requests = []

    def events(self, lg):
        self.requests.append("events")
        return base.FetchResult(events=[base.Event("fd-1", "nba", "NY Knicks @ BOS Celtics", "BOS Celtics", "NY Knicks",
                                                   START)], requests=1)

    def props(self, lg, ev):
        self.requests.append(f"props:{ev.provider_event_id}")
        common = dict(provider="fanduel", book="fanduel", jurisdiction="oh", league="nba", provider_event_id="fd-1",
                      event_name=ev.name, home=ev.home, away=ev.away, start=START, player="P00", market="points",
                      stat_label="Points", period="game", line=17.5, market_type="standard", status="open",
                      provider_market_id="m-1")
        return base.FetchResult(offers=[base.make_offer(**common, side="over", price_decimal=1.87),
                                        base.make_offer(**common, side="under", price_decimal=1.95)], requests=1)


KNOWN = "2026-01-31T19:00:00Z"                     # everything in this world was known before collection


def _world(scon):
    """test_model's two-team world, recorded at KNOWN (so a snapshot as of the forecast day can see it)."""
    for abbr, eid in TEAMS:
        store.put(scon, Team(team_id=identity.team_id("nba", eid), league="nba", abbr=abbr, name=abbr,
                             observed_at=KNOWN, known_at=KNOWN))
    for side, (abbr, eid) in enumerate(TEAMS):
        for j in range(8):
            pid = f"nba:player:espn:{side * 100 + j}"
            store.put(scon, Player(player_id=pid, league="nba", name=f"P{side}{j}", observed_at=KNOWN, known_at=KNOWN))
            store.put(scon, Membership(player_id=pid, team_id=identity.team_id("nba", eid),
                                       valid_from="2025-10-01T00:00:00Z", observed_at=KNOWN, known_at=KNOWN))
    store.put(scon, h.event(start=START, observed_at=KNOWN, known_at=KNOWN))
    o = h.offer(line=17.5, player_id="nba:player:espn:0", player_name="P00", player_key="p00",
                observed_at="2026-01-31T20:00:00Z", listed_start_utc=START)
    return store.put(scon, o)["record_id"]


def _fit(tmp_path, monkeypatch):
    hcon = _hist(tmp_path)
    monkeypatch.setattr(datasets, "db_path", lambda: tmp_path / "history.db")
    art = tmp_path / "artifacts"
    art.mkdir()
    monkeypatch.setattr(forward, "ART_DIR", art)
    cfg = {"markets": {mk: {"market": mk, "half_lives": [5, 20], "features": ["nba.home"], "family": fam,
                            "cal": [1.0, 1.05]} for mk, fam in (("points", "negbin"), ("rebounds", "mixture"),
                                                                ("assists", "poisson"), ("threes", "ratio"))}}
    (art / "model_config_nba.json").write_text(json.dumps(cfg))
    forward.fit("nba", hcon, as_of="2026-01-31T20:00:00Z")
    return hcon


def test_book_quote_reaches_offer_model_and_ui_with_one_provider_request(tmp_path, monkeypatch):
    hcon = _fit(tmp_path, monkeypatch)
    feed = OneBookFeed()
    monkeypatch.setattr(providers, "PROVIDERS", {"fanduel": feed})
    monkeypatch.setattr(providers, "BLOCKED", {})                       # no health probes in this test
    monkeypatch.setattr(book_worker.canon, "keys_for", lambda lg, prov, evs, now=None:
                        {e.provider_event_id: "espn:1" for e in evs})   # the books' ESPN mapping (tested elsewhere)
    from datetime import datetime, timezone
    monkeypatch.setattr(book_worker, "_now", lambda: datetime(2026, 1, 31, 20, 55, tzinfo=timezone.utc))
    calls = []

    def no_network(*a, **k):                                          # every other request would show up here
        calls.append(a[0] if a else k.get("url"))
        raise ConnectionError("network disabled in this test")
    monkeypatch.setattr(net, "get", no_network)

    with store.opened(tmp_path / "canonical.db") as scon:
        target_rid = _world(scon)                                      # PrizePicks P00 points 17.5 on nba:espn:1
        resolver, _ = sources.resolver_from_store(scon, "nba")
        res = canonical._ingest_books(scon, {"key": "nba", "family": "basketball", "name": "NBA"}, resolver)
        assert feed.requests == ["events", "props:fd-1"]               # ONE game read
        assert res["sc1_offers"]["fanduel"]["offers"] == 1 and res["sc1_offers"]["fanduel"]["unresolved_player"] == 0

        # 1. the stored sc-1 offer: both sides at one line, the book's prices, our observation time
        (rid, o), = store.offers(scon, platform="fanduel")
        assert (o.event_id, o.player_id, o.stat, o.line, o.scope, o.variant) == \
            ("nba:espn:1", "nba:player:espn:0", "points", 17.5, "full_game", "main")
        assert [(s.side, s.offered, s.price) for s in o.sides] == [("over", True, 1.87), ("under", True, 1.95)]
        assert o.observed_at == COLLECT_AT and o.source_updated_at is None and o.source_time_unknown
        assert dict(o.extras)["feed"].startswith("fanduel sbapi")

        # 2. the model's market comparison uses it - as its own candidate, never inside mc-1 / mi-1
        out = forward.forecast_league(scon, hcon, "nba", FORECAST_AT, log=lambda *x: None)
        assert out["written"].get("sm-1") == 1 and out["written"].get("mkt-fanduel") == 1
        assert not out["written"].get("mc-1") and not out["written"].get("mi-1")
        fcs = {f.model_version: f for _, f in store.forecasts(scon)}
        mk = fcs["mkt-fanduel"]
        assert mk.offer_snapshot_ids == (target_rid,) and rid in mk.input_snapshot_ids   # the book quote is its input
        assert dict(mk.distribution)["sources"] == "fanduel"
        io, iu = 1 / 1.87, 1 / 1.95
        assert abs(mk.p_over - io / (io + iu)) < 1e-9                  # same line: the de-vigged pair, unchanged

        # 3. the UI snapshot shows the quote and the forecast built on it
        snap = tmp_path / "snap"
        snapshots.build(scon, "nba", now="2026-01-31T21:05:00Z", base=snap, history=False)
        player = json.loads((snap / "nba" / "players" / f"{snapshots.safe_name('nba:player:espn:0')}.json").read_text())
        rows = [r for r in player["markets"] if r["stat"] == "points"]
        quotes = [q for r in rows for q in r["quotes"]]
        assert any(q["platform"] == "fanduel" and q["record_id"] == rid for q in quotes)
        allfc = json.loads((snap / "nba" / "forecasts.json").read_text())["forecasts"]
        assert any(f["model_version"] == "mkt-fanduel" for f in allfc.values())

    assert calls == []                                                  # the model and the UI made no request


def test_registered_consensus_sources_are_unchanged_by_a_direct_book_quote():
    ev = {"nba:espn:1": h.event()}
    tgt = h.offer(line=21.5)
    fat = "2026-10-23T22:55:00Z"
    k = ("k1", h.kalshi_rung(21.5, 0.55, 0.57, oid="kalshi:a", observed_at="2026-10-23T22:50:00Z"), None)
    fd = ("f1", h.offer(platform="fanduel", oid="fd:1", line=21.5, variant="main", observed_at="2026-10-23T22:50:00Z",
                        sides=(h.SideQuote("over", True, "decimal", price=1.80),
                               h.SideQuote("under", True, "decimal", price=2.05))), None)
    per_k, _ = market.eligible_quotes(tgt, [k], ev, fat, 0.0)
    per_both, _ = market.eligible_quotes(tgt, [k, fd], ev, fat, 0.0)
    assert set(per_both) == {"kalshi", "fanduel"}                       # the book is an eligible single source
    assert market.consensus_sources(per_both) == per_k                  # but mc-1 / mi-1 see exactly what they saw
    assert market.consensus(tgt, market.consensus_sources(per_both)) == market.consensus(tgt, per_k)


def test_settlement_hands_its_own_espn_body_to_the_game_store(monkeypatch):
    seen = []
    monkeypatch.setattr(canonical, "_game_store_final", lambda lg, eid, raw: seen.append((eid, raw["id"])))
    from sports import espn
    monkeypatch.setattr(espn, "summary_and_raw", lambda lg, eid, refresh=False: (
        {"completed": True, "start": "2026-10-23T23:00:00Z", "players": {}}, {"id": eid}))
    with store.opened() as con:
        store.put(con, h.event(status="final", start="2026-10-23T23:00:00Z"))
        canonical.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
    assert seen == [("1", "1")]                                          # one ESPN read fed both stores


def test_one_scheduled_collector_and_no_duplicate_pollers():
    assert set(canonical.SOURCES) >= {"oddsapi", "polymarket", "books", "games"}
    assert not hasattr(canonical, "_ingest_oddsapi")                    # the unresolved second Odds API path is gone
    assert "theoddsapi" not in providers.PROVIDERS                      # and the books worker no longer polls it
    import inspect

    from sports.games import collect
    src = inspect.getsource(collect)
    assert "net.get_json(f\"{espn.SITE}" not in src                      # rosters / injuries via the canonical cache
    assert "_finals" not in src                                          # finals come from settlement's own fetch
    assert "while True" not in inspect.getsource(book_worker.main)       # no second forever-loop scheduler


def test_parallel_league_threads_never_run_the_books_job_or_game_tick_at_once(monkeypatch):
    """cmd_ingest runs NBA and NFL on parallel threads; both books passes share one lease holder and the blocked-book
    probes are league-independent, so two at once could send the same probe twice. They run one at a time."""
    import threading
    import time

    from sports.books import store as book_store
    from sports.games import collect, store as game_store
    active, peak, lock = [0], [0], threading.Lock()

    def busy(*a, **k):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return {}

    class _Db:
        def close(self):
            pass
    monkeypatch.setattr(book_store, "connect", lambda: _Db())
    monkeypatch.setattr(game_store, "connect", lambda: _Db())
    monkeypatch.setattr(book_worker, "run_once", busy)
    monkeypatch.setattr(collect, "tick", busy)
    jobs = [threading.Thread(target=canonical._ingest_books, args=(None, {"key": k}, None)) for k in ("nba", "nfl")]
    jobs += [threading.Thread(target=canonical._ingest_games, args=({"key": k},)) for k in ("nba", "nfl")]
    for j in jobs:
        j.start()
    for j in jobs:
        j.join()
    assert peak[0] == 1


def test_the_store_cap_counts_every_sports_file_not_only_canonical(tmp_path, monkeypatch):
    """books.db / games.db are written by the same loop now: the cap measures the disk that fills."""
    from sports import config
    import pytest
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for name, mb in (("canonical.db", 1), ("books.db", 2), ("games.db", 1), ("snapshots/nba/board.json", 0.5)):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x" * int(mb * 1e6))
    assert round(canonical.data_mb(), 1) == 4.5
    import shutil, types; monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(total=1e6, used=0, free=1e12))
    monkeypatch.setattr(canonical, "STORE_MAX_MB", 2.5)                # canonical alone (1 MB) is under it
    with pytest.raises(canonical.BudgetExceeded, match="sports data 4"):
        canonical._check_budget()
    monkeypatch.setattr(canonical, "STORE_MAX_MB", 5)
    canonical._check_budget()


def _old_withdrawals(con, prefix, league, seen_ids, observed_at):
    """The withdrawal candidates of the former league-wide scan (before the bounded scan), for comparison."""
    out = set()
    for _, o in store.offers(con, league=league, as_of=None, limit=200000):
        if not o.platform.startswith(prefix) or o.offer_id in seen_ids or o.availability == "removed":
            continue
        if o.listed_start_utc and o.listed_time_kind == "start" and o.listed_start_utc <= observed_at:
            continue
        out.add(o.offer_id)
    return out


def _edge_world(con):
    """Offers covering every withdrawal edge: stored / unstored / unresolved / started events, no listed start,
    another platform, an identity last seen just inside and just outside the 72 h window, a price that came back."""
    T = "2026-10-24T02:00:00Z"
    store.put_many(con, [h.event("nba:espn:new", start="2026-10-24T23:00:00Z", observed_at="2026-10-20T00:00:00Z",
                                 known_at="2026-10-20T00:00:00Z"),
                         h.event("nba:espn:started", start="2026-10-24T00:30:00Z", observed_at="2026-10-20T00:00:00Z",
                                 known_at="2026-10-20T00:00:00Z")])
    F = "2026-10-25T00:00:00Z"                                # listed start still ahead
    recs = [h.offer(oid="pp:new", event_id="nba:espn:new", listed_start_utc=F, observed_at="2026-10-24T01:50:00Z"),
            h.offer(oid="pp:unstored", event_id="nba:espn:unstored", listed_start_utc=F,
                    observed_at="2026-10-24T01:50:00Z"),
            h.offer(oid="pp:unresolved", event_id=None, player_id=None, listed_start_utc=F,
                    observed_at="2026-10-24T01:50:00Z"),
            h.offer(oid="pp:started", event_id="nba:espn:started", listed_start_utc="2026-10-24T00:30:00Z",
                    observed_at="2026-10-24T00:20:00Z"),
            h.offer(oid="pp:started-nostart", event_id="nba:espn:started", listed_start_utc=None,
                    observed_at="2026-10-24T00:20:00Z"),
            h.offer(oid="pp:edge-in", event_id="nba:espn:unstored", listed_start_utc=F,
                    observed_at="2026-10-21T02:00:00Z"),
            h.offer(oid="pp:edge-out", event_id="nba:espn:unstored", listed_start_utc=F,
                    observed_at="2026-10-21T01:59:59Z"),
            h.offer(oid="pp:moved", event_id="nba:espn:new", line=20.5, listed_start_utc=F,
                    observed_at="2026-10-23T20:00:00Z"),
            h.offer(platform="kalshi", oid="k:new", event_id="nba:espn:new", observed_at="2026-10-24T01:50:00Z"),
            h.offer(platform="fanduel", oid="fd:new", event_id="nba:espn:new", observed_at="2026-10-24T01:50:00Z")]
    store.put_many(con, recs)
    store.put(con, h.offer(oid="pp:moved", event_id="nba:espn:new", line=21.5, listed_start_utc=F,
                           observed_at="2026-10-24T01:00:00Z"))
    return T


def test_bounded_removal_scan_returns_the_old_rows_for_every_offer_seen_in_the_window(tmp_path):
    with store.opened(tmp_path / "c.db") as con:
        T = _edge_world(con)
        got = {o.offer_id: o for _, o in canonical._board_offers(con, "nba", "prizepicks", T)}
        assert set(got) == {"pp:new", "pp:unstored", "pp:unresolved", "pp:started", "pp:started-nostart", "pp:edge-in",
                            "pp:moved"}                       # other platforms and the identity unseen > 72 h are out
        assert got["pp:moved"].line == 21.5                   # the identity's LATEST revision
        old = {o.offer_id: o for _, o in store.offers(con, league="nba", limit=200000) if o.platform == "prizepicks"}
        assert all(got[k] == old[k] for k in got)             # same rows as the former league-wide scan


def test_bounded_removal_scan_never_returns_an_older_revision_as_current(tmp_path, monkeypatch):
    """Data written before c0cc754 can have an OLDER revision re-seen after the newer one (a price that came back
    bumped the old row). The window is per identity, so the identity keeps its current revision."""
    with store.opened(tmp_path / "c.db") as con:
        store.put(con, h.offer(oid="pp:x", line=20.5, listed_start_utc="2026-10-25T00:00:00Z",
                               observed_at="2026-10-24T00:00:00Z"))
        store.put(con, h.offer(oid="pp:x", line=21.5, listed_start_utc="2026-10-25T00:00:00Z",
                               observed_at="2026-10-24T00:10:00Z"))
        n = con.execute("UPDATE records SET last_seen_at = '2026-10-24T01:55:00Z' WHERE kind = 'offer' AND revision = 1"
                        ).rowcount
        con.commit()
        assert n == 1
        monkeypatch.setattr(canonical, "REMOVAL_WINDOW_H", 1.0)       # rev 2 (00:10) outside, rev 1 (01:55) inside
        got = canonical._board_offers(con, "nba", "prizepicks", "2026-10-24T02:00:00Z")
        assert [(o.offer_id, o.line) for _, o in got] == [("pp:x", 21.5)]


def test_bounded_removal_scan_withdraws_exactly_what_the_old_scan_withdrew(tmp_path):
    """Missing-event, unresolved-event and started-event semantics: identical withdrawals to the former scan for every
    offer seen within the window; only the identity unseen for > 72 h (cold) is no longer withdrawn."""
    with store.opened(tmp_path / "c.db") as con:
        T = _edge_world(con)
        old = _old_withdrawals(con, "prizepicks", "nba", {"pp:new"}, T)
        res = canonical._store_offers(con, "prizepicks", "nba", adapters.Batch(records=[
            h.offer(oid="pp:new", event_id="nba:espn:new", listed_start_utc="2026-10-25T00:00:00Z",
                    observed_at=T)]), T, True)
        gone = {o.offer_id for _, o in store.offers(con, league="nba", limit=1000) if o.availability == "removed"}
        assert old - gone == {"pp:edge-out"} and gone == old - {"pp:edge-out"}
        assert gone == {"pp:unstored", "pp:unresolved", "pp:started-nostart", "pp:edge-in", "pp:moved"}
        assert res["removed"] == 5
        assert canonical._board_offers(con, "nba", "kalshi", T)[0][1].offer_id == "k:new"
        partial = canonical._store_offers(con, "prizepicks", "nba", adapters.Batch(records=[]), T, False)
        assert partial["removed"] == 0                        # a failed read still withdraws nothing


def test_polymarket_trimmed_events_give_exactly_the_untrimmed_offers():
    """Events trimmed to the adapter's fields (one 100-event page was ~110 MB of Python objects): the same offers come
    out as from the untrimmed answer (live NBA / NFL comparison: research/results/storage_2026-10-24)."""
    ev = {"id": "1", "title": "Knicks vs. Celtics", "slug": "nba-nyk-bos-2026-10-23", "startTime": "x", "tags": ["big"],
          "markets": [{"id": "6008962", "question": "Jayson Tatum: Points O/U 26.5", "outcomes": "[\"Yes\", \"No\"]",
                       "bestBid": 0.48, "bestAsk": 0.51, "line": 26.5, "gameStartTime": "2026-10-23 23:00:00+00",
                       "liquidityNum": 1240.4, "volumeNum": 9.0, "lastTradePrice": 0.5, "active": True,
                       "closed": False, "acceptingOrders": True, "sportsMarketType": "points",
                       "updatedAt": "2026-10-23T22:34:37Z", "description": "long text " * 50, "clobTokenIds": "[1,2]"},
                      {"id": "2", "question": "Knicks vs. Celtics: O/U 223.5", "sportsMarketType": "totals"}]}
    full = sources.polymarket_offers([ev], "nba", "2026-10-23T22:40:00Z", h.resolver())
    trimmed = sources._pm_trim(ev, sources.PM_STAT["nba"])
    assert [m["id"] for m in trimmed["markets"]] == ["6008962"] and "description" not in trimmed["markets"][0]
    assert sources.polymarket_offers([trimmed], "nba", "2026-10-23T22:40:00Z", h.resolver()).records == full.records


class _Gamma:
    """A provider serving an id-ordered event list by offset / limit (like Gamma with order=id&ascending=true);
    `close_after` closes events between page requests to shift the list mid-read."""
    def __init__(self, n, close_after=None):
        self.events = [{"id": str(1000 + i), "slug": f"nba-nyk-bos-2026-10-{1 + i % 28:02d}", "markets": []}
                       for i in range(n)]
        self.close_after, self.calls = close_after or {}, []

    def get(self, src, url, params=None, **k):
        assert params["order"] == "id" and params["ascending"] == "true" and params["limit"] == sources.PM_PAGE
        self.calls.append(params["offset"])
        body = self.events[params["offset"]:params["offset"] + params["limit"]]
        for eid in self.close_after.get(len(self.calls), []):          # closes AFTER this page was served
            self.events = [e for e in self.events if e["id"] != eid]

        class R:
            def json(self_):
                return body
        return R()


def test_polymarket_pagination_boundaries_never_drop_or_repeat_an_event(monkeypatch):
    for n in (0, 1, 17, 18, 19, 20, 21, 36, 38, 40, 99, 570):
        g = _Gamma(n)
        monkeypatch.setattr(sources, "_get", g.get)
        info = {}
        got = [e["id"] for e in sources.polymarket_raw("nba", info)]
        assert sorted(got) == sorted(e["id"] for e in g.events) and len(got) == len(set(got)), n
        assert info["truncated"] is False and info["events_seen"] == n


def test_polymarket_pagination_survives_events_closing_mid_read(monkeypatch):
    """Two events close between page requests (the list shifts left by 2): the overlap keeps every remaining event."""
    g = _Gamma(60, close_after={1: ["1001", "1002"], 2: ["1020", "1021"]})
    keep = {e["id"] for e in g.events} - {"1001", "1002", "1020", "1021"}
    monkeypatch.setattr(sources, "_get", g.get)
    got = {e["id"] for e in sources.polymarket_raw("nba")}
    assert keep <= got


def test_polymarket_pagination_reports_a_capped_read(monkeypatch):
    step = sources.PM_PAGE - sources.PM_OVERLAP
    g = _Gamma(sources.PM_MAX_PAGES * step + 50)
    monkeypatch.setattr(sources, "_get", g.get)
    info = {}
    sources.polymarket_raw("nba", info)
    assert info["truncated"] is True and info["pages"] == sources.PM_MAX_PAGES
    assert sources.PM_MAX_PAGES * step >= 1200                         # >= the former 12 pages x 100 events


def test_oddspapi_capture_spends_nothing_billable(monkeypatch):
    monkeypatch.setenv("ODDSPAPI_API_KEY", "k-not-real")
    paths = []

    class R:
        status_code = 200

        def json(self):
            return {"current_subscription_id": "s", "subscriptions": [
                {"subscription_id": "s", "is_active": True, "sport_ids": [11], "request_limit": 250,
                 "request_count": 1, "bookmakers": {"draftkings": {"has_player_props": True}}}]}
    monkeypatch.setattr(net, "get", lambda url, **k: paths.append(url.split("/v4", 1)[1]) or R())
    inv = sources.capture("oddspapi", "nba")
    assert paths == ["/account"] and inv["answers"][0]["books_nba"] == ["draftkings"]


def test_bridge_never_invents_a_side():
    o = base.make_offer(provider="betrivers", book="betrivers", jurisdiction="oh", league="nba", provider_event_id="e",
                        event_name="x", start=START, player="P00", market="points", period="game", line=19.5,
                        side="over", price_american=-120, market_type="alternate", status="open")
    b = bridge.to_offers([o], {"key": "nba"}, "espn:1", COLLECT_AT, None)
    (rec,) = b.records
    assert rec.platform == "betrivers" and rec.variant == "alternate"
    assert [(s.side, s.offered, s.price) for s in rec.sides] == [("over", True, round(1 + 100 / 120, 6)),
                                                                ("under", False, None)]


def test_a_complete_game_read_removes_only_that_games_missing_lines(tmp_path):
    def off(line, eid):
        return base.make_offer(provider="fanduel", book="fanduel", jurisdiction="oh", league="nba",
                               provider_event_id=eid, event_name="x", start=START, player="P00", market="points",
                               period="game", line=line, side="over", price_decimal=1.9, market_type="standard")
    lg = {"key": "nba"}
    with store.opened(tmp_path / "c.db") as con:
        for eid, key in (("g1", "espn:1"), ("g2", "espn:2")):
            b = bridge.to_offers([off(17.5, eid), off(18.5, eid)], lg, key, COLLECT_AT, None)
            bridge.store_game(con, "nba", ["fanduel"], f"nba:espn:{key[5:]}", b, COLLECT_AT, True)
        later = "2026-01-31T21:10:00Z"
        b = bridge.to_offers([off(17.5, "g1")], lg, "espn:1", later, None)            # 18.5 left game 1's board
        r = bridge.store_game(con, "nba", ["fanduel"], "nba:espn:1", b, later, True)
        assert r["removed"] == 1
        state = {(o.event_id, o.line): o.availability for _, o in store.offers(con, platform="fanduel")}
        assert state == {("nba:espn:1", 17.5): "open", ("nba:espn:1", 18.5): "removed",
                         ("nba:espn:2", 17.5): "open", ("nba:espn:2", 18.5): "open"}      # game 2 untouched
        partial = bridge.store_game(con, "nba", ["fanduel"], "nba:espn:2", bridge.to_offers([], lg, "espn:2", later,
                                                                                            None), later, False)
        assert partial["removed"] == 0                                                   # a failed read removes nothing
