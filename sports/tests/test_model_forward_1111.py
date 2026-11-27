"""model-forward 2026-11-11: the job exited 1 every run since ~Nov 9 and ran up to its timeout.
(1) A whole-number line above every outcome of the PMF (p_over == 0, p_push > 0) gave pu / (1 - pp) =
1.0000000000000002, store.put rejected the forecast (ContractError) and nothing caught it, so the whole job died.
(2) The duplicate-id check in store._put walked every stored forecast (kind only) on each insert."""
import json

import numpy as np

from sports import store
from sports.contracts import ContractError
from sports.model import datasets, forward
from sports.tests import helpers as h
from sports.tests.test_model import _hist, _store_world


def _pmf_above(pp):
    pmf = np.full(14, (1 - pp) / 13)
    pmf[13] = pp                                        # support 0..13; line 13.0: nothing above it
    return pmf


def test_rounding_above_one_is_capped_and_the_forecast_stores():
    o = h.offer(line=13.0, stat="points")
    for pp in (0.003243287004037301, 0.07, 0.3):         # each gives 1.0000000000000002 with the raw division
        pmf = _pmf_above(pp)
        po, pu, p = forward.dists.line_probs(pmf, 0, 13.0)
        assert po == 0.0 and pu / (1 - p) > 1.0
        fc, _ = forward.build_forecast(o, "rid-1", h.T0, "sm-1", "sm-1:test", pmf, 0, 3.65, ("rid-1",), [],
                                       "ds-test", "cal-test")
        assert fc.p_under_given_no_push == 1.0 and fc.p_over_given_no_push == 0.0
        assert fc.validate() == []
    con = store.connect()
    rid = store.put(con, o)["record_id"]
    fc, _ = forward.build_forecast(o, rid, h.T0, "sm-1", "sm-1:test", _pmf_above(0.07), 0, 3.65, (rid,), [],
                                   "ds-test", "cal-test")
    assert store.put(con, fc)["status"] == "inserted"


def test_ordinary_forecast_values_are_unchanged():
    o = h.offer(line=20.5)
    pmf = np.full(41, 1 / 41)
    fc, _ = forward.build_forecast(o, "rid-1", h.T0, "sm-1", "sm-1:test", pmf, 0, 20.0, ("rid-1",), [],
                                   "ds-test", "cal-test")
    po, pu, pp = forward.dists.line_probs(pmf, 0, 20.5)
    assert fc.p_over_given_no_push == po / (1 - pp) and fc.p_under_given_no_push == pu / (1 - pp)


def test_forecast_id_clash_check_is_an_exact_index_lookup():
    con = store.connect()
    plan = " ".join(str(tuple(r)) for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM records WHERE kind='forecast' AND source='forecast' AND source_key=?",
        ("x",)).fetchall())
    assert "source_key=?" in plan, plan
    src = open(store.__file__, encoding="utf-8").read()
    assert "WHERE kind='forecast' AND source='forecast' AND source_key=?" in src


def test_one_invalid_forecast_never_stops_the_league(tmp_path, monkeypatch):
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
    real_put = store.put

    def bad_put(con, rec, *a, **k):
        if getattr(rec, "KIND", "") == "forecast":
            raise ContractError("forecast: p_under_given_no_push=1.0000000000000002 is not a probability")
        return real_put(con, rec, *a, **k)

    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        monkeypatch.setattr(store, "put", bad_put)
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["contract_errors"] == {"sm-1|points": 1} and res["written"].get("sm-1", 0) == 0
        monkeypatch.setattr(store, "put", real_put)       # the next run writes it normally
        res2 = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res2["written"].get("sm-1") == 1 and "contract_errors" not in res2


def _offer_world(con):
    """Three platforms, two events, several revisions per identity (line moves back and forth)."""
    for i in range(30):
        for plat in ("prizepicks", "underdog", "kalshi"):
            ev = "nba:espn:1" if i % 2 else "nba:espn:2"
            for j, line in enumerate((10.5 + i, 11.5 + i, 10.5 + i)[: 1 + i % 3]):
                store.put(con, h.offer(platform=plat, oid=f"{plat[:2]}:{i}", line=line, event_id=ev,
                                       stat="points" if i % 3 else "rebounds",
                                       observed_at=f"2026-01-31T{10 + j:02d}:{i:02d}:00Z"))


def test_platform_filter_keeps_the_same_rows_in_the_same_order():
    con = store.connect()
    _offer_world(con)
    allr = list(store.iter_offers(con, league="nba", limit=500000))
    assert len(allr) == 90
    tg = list(store.iter_offers(con, league="nba", limit=500000, platforms=forward.TARGETS))
    mk = list(store.iter_offers(con, league="nba", limit=500000, exclude_platforms=forward.TARGETS))
    assert [r for r, _ in tg] == [r for r, o in allr if o.platform in forward.TARGETS]
    assert [r for r, _ in mk] == [r for r, o in allr if o.platform not in forward.TARGETS]
    assert [o.to_dict() for _, o in tg] == [o.to_dict() for _, o in allr if o.platform in forward.TARGETS]
    assert list(store.iter_offers(con, league="nba", platforms=())) == []


def test_forecast_slate_reads_preserve_current_revisions_and_skip_other_events(monkeypatch):
    con = store.connect()
    _offer_world(con)
    expected = {rid: o.to_dict() for rid, o in store.iter_offers(
        con, league="nba", limit=500000, platforms=forward.TARGETS)
        if o.event_id == "nba:espn:1"}
    from sports import snapshots
    real = snapshots._slate_offers
    reads = []

    def scoped(con, league, event_ids, at):
        reads.append(set(event_ids))
        assert set(event_ids) == {"nba:espn:1"}
        return real(con, league, event_ids, at)

    monkeypatch.setattr(snapshots, "_slate_offers", scoped)
    got = {rid: o.to_dict() for rid, o in forward._slate_offers(
        con, "nba", ["nba:espn:1", "nba:espn:1"], platforms=forward.TARGETS)}
    assert got == expected and len(got) > 0
    assert reads == [{"nba:espn:1"}]
    monkeypatch.setattr(snapshots, "_slate_offers", real)
    assert list(forward._slate_offers(con, "nba", [], platforms=forward.TARGETS)) == []


def test_slate_reads_do_not_resurrect_moved_offers_or_change_saved_data():
    from sports import snapshots
    con = store.connect()
    store.put(con, h.event())
    store.put(con, h.offer(oid="moving"))
    store.put(con, h.offer(oid="moving", event_id="nba:espn:old",
                           observed_at="2026-10-23T21:00:00Z"))
    store.put(con, h.offer(oid="stays", player_id="nba:player:espn:42"))
    before = list(con.execute("SELECT record_id, payload FROM records ORDER BY record_id"))
    got = list(forward._slate_offers(con, "nba", ["nba:espn:1"]))
    assert [o.offer_id for _, o in got] == ["stays"]
    assert snapshots.board_player_ids(con, "nba", "2026-10-23T22:00:00Z") == ["nba:player:espn:42"]
    after = list(con.execute("SELECT record_id, payload FROM records ORDER BY record_id"))
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def test_current_offer_ids_same_as_the_old_query():
    from sports.model import pipeline
    con = store.connect()
    _offer_world(con)
    ev = ["nba:espn:1", "nba:espn:2"]
    old = (f"SELECT r.record_id FROM records r JOIN (SELECT source, source_key, MAX(revision) rv FROM records WHERE "
           f"kind = 'offer' AND {store.CURRENT_SQL} AND league = ? AND event_id IN (?,?) "
           f"GROUP BY source, source_key) z ON r.source = z.source AND r.source_key = z.source_key AND "
           f"r.revision = z.rv WHERE r.kind = 'offer'")
    want = {x[0] for x in con.execute(old, ["nba"] + ev)}
    assert len(want) == 90 and pipeline.current_offer_ids(con, "nba", ev) == want
    assert pipeline.current_offer_ids(con, "nba", ev[:1]) == {x[0] for x in con.execute(
        old.replace("IN (?,?)", "IN (?)"), ["nba"] + ev[:1])}
    plan = " ".join(str(tuple(r)) for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT source, source_key, MAX(revision) FROM records INDEXED BY records_event WHERE "
        "kind = 'offer' AND late = 0 AND league = ? AND event_id IN (?) GROUP BY source, source_key", ["nba", "x"]))
    assert "records_event" in plan

def test_target_slate_reads_once_with_identical_market_rows(monkeypatch):
    con = store.connect()
    _offer_world(con)
    markets = ['points', 'rebounds', 'assists']
    ids = ['nba:espn:1', 'nba:espn:2']
    expected = {mk: [(rid, o.to_dict()) for rid, o in forward._slate_offers(
        con, 'nba', ids, stat=mk, platforms=forward.TARGETS)] for mk in markets}
    real = forward._slate_offers
    calls = []
    def counted(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(forward, '_slate_offers', counted)
    grouped = forward._target_slate_by_market(con, 'nba', iter(ids), markets)
    assert {mk: [(rid, o.to_dict()) for rid, o in rows] for mk, rows in grouped.items()} == expected
    assert len(calls) == 1
    assert forward._target_slate_by_market(con, 'nba', ids, []) == {}
    assert len(calls) == 1
